"""Pure, branch-agnostic D&D combat planning and resolution.

This module deliberately does not read or write a database.  The MCP layer owns
authorization, branch selection, optimistic revisions, idempotency, and the
atomic commit.  The functions here receive validated actor-card snapshots and
return new values plus an auditable result.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable
from uuid import uuid4

from sagasmith_core.access import LOCAL_SYSTEM_PRINCIPAL_ID

from sagasmith_dnd.activity_identity import is_multiattack_activity
from sagasmith_dnd.character_schema import (
    SKILL_ABILITIES,
    active_effect_roll_bonus,
    effective_ability_scores,
    effective_hit_point_maximum,
    effective_size,
    validate_character_sheet,
)
from sagasmith_dnd.conditions import (
    DEATH_SAVE_SETTLED_CONDITIONS,
    INCAPACITATING_STATE_IDS,
    apply_condition_change,
    apply_effect_conditions,
    condition_ids,
    effect_condition_additions,
    reconcile_condition_projection,
    reconcile_ended_effect_conditions,
)
from sagasmith_dnd.content_solution import (
    ContentSolutionError,
    normalize_content_solution,
)
from sagasmith_dnd.editions import DEFAULT_CHARACTER_EDITION, normalize_dnd_edition
from sagasmith_dnd.engine import (
    ability_modifier,
    proficiency_bonus,
    resolve_attack,
    resolve_check,
    resolve_death_save,
    roll,
    roll_d20,
)
from sagasmith_dnd.hit_points import apply_basic_healing_to_sheet
from sagasmith_dnd.resolution_plan import (
    ResolutionPlanCompilationError,
    compile_resolution_plan,
)
from sagasmith_dnd.resources import mutate_bounded_resource
from sagasmith_dnd.rule_engine import (
    ResolutionContext,
    apply_rule_event,
    context_with_facts,
    core_receipts,
    rule_event_ruling_kind,
)
from sagasmith_dnd.spell_resolution import (
    SPELL_RESOLUTION_MECHANIC_ID,
    scaled_roll_expression,
)
from sagasmith_dnd.standard_feature_ids import (
    CORE_RELENTLESS_ENDURANCE_MECHANIC_ID,
)
from sagasmith_dnd.standard_spell_ids import (
    CORE_BLADE_WARD_MECHANIC_ID,
    CORE_HYPNOTIC_PATTERN_SPELL_IDS,
    CORE_WITCH_BOLT_MECHANIC_ID,
)
from sagasmith_dnd.vocabulary import WEAPON_HAND_SLOTS

ABILITY_CHECK_KINDS = frozenset({"ability", "check"})
SAVING_THROW_KINDS = frozenset({"save", "death_save"})
ACTOR_CHECK_KINDS = ABILITY_CHECK_KINDS | SAVING_THROW_KINDS
DAMAGE_REDUCTION_OUTCOMES = frozenset({"full", "half", "none"})
WEAPON_MASTERY_IDS = frozenset(
    {"cleave", "graze", "nick", "push", "sap", "slow", "topple", "vex"}
)


def damage_amount_after_reduction(amount: int, outcome: str) -> int:
    """Apply the canonical full/half/none damage result, rounding half down."""

    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
        raise CombatEngineError("damage amount must be a non-negative integer")
    normalized = str(outcome).strip().casefold()
    if normalized not in DAMAGE_REDUCTION_OUTCOMES:
        raise CombatEngineError("damage outcome must be full, half, or none")
    if normalized == "none":
        return 0
    if normalized == "half":
        return amount // 2
    return amount


def standard_save_damage_reduction(
    actor: dict[str, Any],
    *,
    ability: str,
    success: bool,
    ordinary_successful_save: str,
    rules: ResolutionContext | None = None,
) -> dict[str, Any]:
    """Apply source-bound standard traits to one save-for-damage outcome."""

    normalized_ability = _long_ability_name(ability)
    normalized_success = str(ordinary_successful_save).strip().casefold()
    if normalized_success not in DAMAGE_REDUCTION_OUTCOMES:
        raise CombatEngineError(
            "ordinary successful-save damage must be full, half, or none"
        )
    reduction = normalized_success if success else "full"
    boundary_ids: list[str] = []
    sheet = actor_sheet(actor)
    evasion = _validated_standard_source_trait(sheet, "evasion")
    if (
        evasion is not None
        and normalized_ability == "dexterity"
        and normalized_success == "half"
        and not (
            _condition_set(sheet.get("conditions"))
            & set(evasion.get("unavailable_conditions") or [])
        )
    ):
        reduction = (
            str(evasion["successful_save"])
            if success
            else str(evasion["failed_save"])
        )
        boundary_ids.append("dnd5e.core.save.evasion")
    return {
        "damage_reduction": reduction,
        "rule_receipts": core_receipts(
            rules,
            boundary_ids,
            "save.damage_reduction",
        ),
    }


def d20_exhaustion_adjustment(
    *,
    ruleset: str,
    exhaustion: int,
    kind: str,
    bonus: int = 0,
    disadvantage: bool = False,
) -> dict[str, Any]:
    """Apply the edition-specific exhaustion rule to one d20 roll."""

    normalized_ruleset = _normalize_ruleset(ruleset)
    if isinstance(exhaustion, bool) or not isinstance(exhaustion, int) or exhaustion < 0:
        raise CombatEngineError("exhaustion must be a non-negative integer")
    if kind not in {"ability", "attack", "check", "death_save", "initiative", "save"}:
        raise CombatEngineError("unsupported exhaustion roll kind")
    adjusted_bonus = int(bonus)
    adjusted_disadvantage = bool(disadvantage)
    exhaustion_disadvantage = False
    if normalized_ruleset == "2024":
        adjusted_bonus -= 2 * exhaustion
    elif (
        kind in ABILITY_CHECK_KINDS | {"initiative"}
        and exhaustion >= 1
        or kind in {"attack", "death_save", "save"}
        and exhaustion >= 3
    ):
        adjusted_disadvantage = True
        exhaustion_disadvantage = True
    return {
        "bonus": adjusted_bonus,
        "disadvantage": adjusted_disadvantage,
        "exhaustion_disadvantage": exhaustion_disadvantage,
        "applied": adjusted_bonus != int(bonus)
        or adjusted_disadvantage != bool(disadvantage),
    }


class CombatEngineError(ValueError):
    """Base error for a rejected or incomplete combat operation."""


def clear_ended_invisibility_spell_condition(
    sheet: dict[str, Any], *, ended_effect_ids: Iterable[str]
) -> bool:
    """Compatibility wrapper around the shared effect-condition projection."""

    ids = {str(item) for item in ended_effect_ids if str(item)}
    if not ids:
        return False
    before = list(sheet.get("conditions", []))
    reconcile_ended_effect_conditions(
        sheet,
        ended_effects=[
            effect for effect in sheet.get("effects", []) if str(effect.get("id") or "") in ids
        ],
    )
    return sheet["conditions"] != before


def end_concentration_for_incapacitating_conditions(
    sheet: dict[str, Any], *, ended_reason: str = "incapacitated"
) -> list[str]:
    """End concentration when the actor is Incapacitated, directly or indirectly."""
    conditions = condition_ids(sheet.get("conditions"))
    if not conditions & INCAPACITATING_STATE_IDS:
        return []
    ended: list[str] = []
    for effect in sheet.get("effects", []):
        if not effect.get("active") or not bool(effect.get("concentration")):
            continue
        effect["active"] = False
        effect["ended_reason"] = ended_reason
        effect_id = str(effect.get("id") or "")
        if effect_id:
            ended.append(effect_id)
    clear_ended_invisibility_spell_condition(sheet, ended_effect_ids=ended)
    return ended


class NeedsRulingError(CombatEngineError):
    """Raised when the engine cannot safely infer a narrative prerequisite."""

    def __init__(
        self,
        message: str,
        *,
        missing: Iterable[str] = (),
        ruling_kind: str = "agent_dm_adjudication",
    ) -> None:
        super().__init__(message)
        self.missing = tuple(missing)
        self.ruling_kind = str(ruling_kind or "agent_dm_adjudication")




@dataclass(frozen=True)
class ActionIntent:
    """A fully identified action declaration before mechanical resolution."""

    campaign_id: str
    actor_id: str
    action_type: str
    target_ids: tuple[str, ...] = ()
    item_id: str | None = None
    activity_id: str | None = None
    payment: str | None = None
    branch_id: str | None = None
    principal_id: str = LOCAL_SYSTEM_PRINCIPAL_ID
    expected_revisions: dict[str, int] = field(default_factory=dict)
    idempotency_key: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    rulings: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ActionIntent":
        targets = value.get("target_ids", value.get("targets", ()))
        if isinstance(targets, str):
            targets = (targets,)
        if not isinstance(targets, (list, tuple)):
            raise CombatEngineError("target_ids must be a list")
        rulings = value.get("rulings", ())
        if isinstance(rulings, dict):
            rulings = (rulings,)
        return cls(
            campaign_id=str(value.get("campaign_id") or ""),
            actor_id=str(value.get("actor_id") or ""),
            action_type=str(value.get("action_type") or value.get("type") or ""),
            target_ids=tuple(str(item) for item in targets),
            item_id=value.get("item_id"),
            activity_id=value.get("activity_id"),
            payment=value.get("payment"),
            branch_id=value.get("branch_id"),
            principal_id=str(value.get("principal_id") or LOCAL_SYSTEM_PRINCIPAL_ID),
            expected_revisions={
                str(key): int(item)
                for key, item in dict(value.get("expected_revisions") or {}).items()
            },
            idempotency_key=value.get("idempotency_key"),
            payload=deepcopy(dict(value.get("payload") or {})),
            rulings=tuple(deepcopy(item) for item in rulings if isinstance(item, dict)),
        )

    def validate(self) -> None:
        if not self.campaign_id:
            raise CombatEngineError("campaign_id is required")
        if not self.actor_id:
            raise CombatEngineError("actor_id is required")
        if not self.action_type:
            raise CombatEngineError("action_type is required")
        for ruling in self.rulings:
            if not ruling.get("kind") or "value" not in ruling:
                raise CombatEngineError("every ruling needs kind and value")
            if ruling.get("source") not in {"rule", "module", "scene", "dm_ruling"}:
                raise CombatEngineError("ruling source is invalid")


@dataclass(frozen=True)
class ChoiceWindow:
    id: str
    kind: str
    actor_id: str
    event: str
    candidates: tuple[dict[str, Any], ...] = ()
    deadline: str = "before_commit"
    status: str = "pending"


@dataclass(frozen=True)
class ResolutionReceipt:
    id: str
    operation: str
    status: str
    campaign_id: str
    branch_id: str | None
    actor_id: str | None
    result: dict[str, Any]
    rolls: tuple[dict[str, Any], ...] = ()
    changes: tuple[dict[str, Any], ...] = ()
    pending: tuple[dict[str, Any], ...] = ()
    rulings: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def actor_id(actor: dict[str, Any]) -> str:
    value = actor.get("id") or actor.get("character_id") or actor.get("actor_id")
    if not value:
        raise CombatEngineError("actor snapshot has no id")
    return str(value)


def actor_sheet(actor: dict[str, Any]) -> dict[str, Any]:
    sheet = actor.get("sheet")
    if not isinstance(sheet, dict):
        raise CombatEngineError(f"actor {actor_id(actor)} has no validated sheet")
    return deepcopy(sheet)


def actor_derived(actor: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(dict(actor.get("derived") or {}))


def active_condition_source_effects(sheet: dict[str, Any], condition: str) -> list[dict[str, Any]]:
    """Return active timed effects that explicitly own one condition."""
    normalized_values = condition_ids([condition])
    if not normalized_values:
        return []
    normalized = normalized_values.pop()
    matches: list[dict[str, Any]] = []
    for effect in sheet.get("effects", []):
        if not effect.get("active"):
            continue
        if normalized in effect_condition_additions(effect):
            matches.append(deepcopy(effect))
    return matches


def timed_condition_sources(sheet: dict[str, Any]) -> dict[str, list[str]]:
    """Index active condition-owning effects by condition and source actor."""
    result: dict[str, list[str]] = {}
    for normalized in condition_ids(sheet.get("conditions")):
        sources = sorted(
            {
                str(effect.get("source") or "")
                for effect in active_condition_source_effects(sheet, normalized)
                if str(effect.get("source") or "")
            }
        )
        if sources:
            result[normalized] = sources
    return result


def source_speed_multiplier(sheet: dict[str, Any]) -> float:
    """Return generic active-effect multipliers applied to every movement speed."""

    multiplier = 1.0
    for effect in sheet.get("effects", []):
        if not effect.get("active"):
            continue
        if _is_hypnotic_pattern_target_effect(effect):
            multiplier = 0.0
            continue
        for change in effect.get("changes", []):
            if (
                isinstance(change, dict)
                and change.get("path") == "combat.speed.multiplier"
                and change.get("mode") == "multiply"
                and not isinstance(change.get("value"), bool)
                and isinstance(change.get("value"), (int, float))
            ):
                multiplier *= float(change["value"])
    return multiplier


def _active_attack_roll_effect_flags(sheet: dict[str, Any]) -> tuple[bool, bool, list[str]]:
    """Read generic attack-roll advantage/disadvantage flags from active effects."""

    advantage = False
    disadvantage = False
    sources: list[str] = []
    for effect in sheet.get("effects", []):
        if not isinstance(effect, dict) or not effect.get("active"):
            continue
        effect_applied = False
        for change in effect.get("changes", []):
            if (
                not isinstance(change, dict)
                or change.get("mode") != "set"
                or change.get("value") is not True
            ):
                continue
            if change.get("path") == "rolls.attack.advantage":
                advantage = True
                effect_applied = True
            elif change.get("path") == "rolls.attack.disadvantage":
                disadvantage = True
                effect_applied = True
        if effect_applied:
            sources.append(str(effect.get("id") or "source_effect"))
    return advantage, disadvantage, sources


def _is_hypnotic_pattern_target_effect(effect: dict[str, Any]) -> bool:
    """Recognize only target effects created by the Core Hypnotic Pattern path."""

    return (
        effect.get("kind") == "timed_conditions"
        and str(effect.get("name") or "").strip().casefold() == "hypnotic pattern"
        and str(effect.get("source_spell_id") or "")
        in CORE_HYPNOTIC_PATTERN_SPELL_IDS
    )


def active_hypnotic_pattern_effect_ids(sheet: dict[str, Any]) -> list[str]:
    """Return every active Hypnotic Pattern target effect on one actor."""

    return [
        str(effect.get("id") or "")
        for effect in sheet.get("effects", [])
        if effect.get("active") and _is_hypnotic_pattern_target_effect(effect)
    ]


def end_hypnotic_pattern_effects(
    sheet: dict[str, Any],
    *,
    ended_reason: str,
) -> dict[str, Any]:
    """End all active Hypnotic Pattern effects without removing shared conditions."""

    reason = str(ended_reason).strip()
    if not reason:
        raise CombatEngineError("Hypnotic Pattern ended_reason is required")
    value = deepcopy(sheet)
    ended_effects: list[dict[str, Any]] = []
    for effect in value.get("effects", []):
        if effect.get("active") and _is_hypnotic_pattern_target_effect(effect):
            effect["active"] = False
            effect["ended_reason"] = reason
            ended_effects.append(effect)
    if ended_effects:
        reconcile_ended_effect_conditions(value, ended_effects=ended_effects)
    return {
        "sheet": validate_character_sheet(value),
        "ended_effect_ids": [str(effect.get("id") or "") for effect in ended_effects],
        "ended_reason": reason,
    }


def resolve_hypnotic_pattern_target(
    target: dict[str, Any],
    *,
    caster_id: str,
    spell_id: str,
    save_dc: int,
    rules: ResolutionContext | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    """Resolve one creature seeing the source-bound 2014/2024 Hypnotic Pattern."""

    target_id = actor_id(target)
    source_actor_id = str(caster_id).strip()
    if not source_actor_id:
        raise CombatEngineError("Hypnotic Pattern caster_id is required")
    if str(spell_id) not in CORE_HYPNOTIC_PATTERN_SPELL_IDS:
        raise CombatEngineError("Hypnotic Pattern requires its source-bound SRD spell id")
    if isinstance(save_dc, bool) or not isinstance(save_dc, int) or not 1 <= save_dc <= 40:
        raise CombatEngineError("Hypnotic Pattern save DC must be an integer from 1 to 40")
    sheet = actor_sheet(target)
    conditions = condition_ids(sheet.get("conditions"))
    if "blinded" in conditions:
        return {
            "sheet": sheet,
            "result": {
                "target_id": target_id,
                "saw_pattern": False,
                "outcome": "did_not_see_pattern",
                "save": None,
                "effect_id": "",
                "ended_concentration_effect_ids": [],
            },
        }
    immunities = condition_ids(dict(sheet.get("traits") or {}).get("condition_immunities"))
    if "charmed" in immunities:
        return {
            "sheet": sheet,
            "result": {
                "target_id": target_id,
                "saw_pattern": True,
                "outcome": "immune_to_charmed",
                "save": None,
                "effect_id": "",
                "ended_concentration_effect_ids": [],
            },
        }
    save = resolve_actor_check(
        target,
        kind="save",
        ability="wisdom",
        dc=save_dc,
        save_source_kind="spell",
        save_effect_conditions=["charmed", "incapacitated"],
        ruleset=str(sheet.get("edition") or DEFAULT_CHARACTER_EDITION),
        rules=context_with_facts(
            rules,
            save_source_kind="spell",
            save_effect_conditions=["charmed", "incapacitated"],
        ),
        rng=rng,
    )
    if save["success"]:
        return {
            "sheet": sheet,
            "result": {
                "target_id": target_id,
                "saw_pattern": True,
                "outcome": "saved",
                "save": save,
                "effect_id": "",
                "ended_concentration_effect_ids": [],
            },
        }
    value = deepcopy(sheet)
    effect_id = f"hypnotic-pattern-{uuid4().hex}"
    effect = {
        "id": effect_id,
        "name": "Hypnotic Pattern",
        "kind": "timed_conditions",
        "source": source_actor_id,
        "source_spell_id": str(spell_id),
        "active": True,
        "concentration": False,
        "duration": {"period": "round", "remaining": 10},
        "changes": [
            {"path": "conditions", "mode": "add", "value": "charmed"},
            {"path": "conditions", "mode": "add", "value": "incapacitated"},
        ],
        "description": "",
    }
    value.setdefault("effects", []).append(effect)
    apply_effect_conditions(value, effect)
    ended_concentration_effect_ids = end_concentration_for_incapacitating_conditions(
        value,
        ended_reason="incapacitated",
    )
    return {
        "sheet": validate_character_sheet(value),
        "result": {
            "target_id": target_id,
            "saw_pattern": True,
            "outcome": "affected",
            "save": save,
            "effect_id": effect_id,
            "ended_concentration_effect_ids": ended_concentration_effect_ids,
        },
    }


def reconcile_effect_dependencies(
    encounter: dict[str, Any],
    sheets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """End target effects when their exact recorded dependency is no longer true."""

    value = deepcopy(encounter)
    updated_sheets = {actor_id_value: deepcopy(sheet) for actor_id_value, sheet in sheets.items()}
    changed_actor_ids: set[str] = set()
    ended_links: list[dict[str, Any]] = []
    links = value.get("dependent_effects", [])
    if not isinstance(links, list):
        raise CombatEngineError("encounter dependent_effects must be a list")
    for link in links:
        if not isinstance(link, dict) or not link.get("active", True):
            continue
        dependency = str(link.get("dependency") or "")
        if dependency not in {
            "source_effect_active",
            "source_actor_capable",
        }:
            raise CombatEngineError("unsupported encounter effect dependency")
        source_actor_id = str(link.get("source_actor_id") or "")
        source_effect_id = str(link.get("source_effect_id") or "")
        target_actor_id = str(link.get("target_actor_id") or "")
        target_effect_id = str(link.get("target_effect_id") or "")
        if not all((source_actor_id, target_actor_id, target_effect_id)) or (
            dependency == "source_effect_active" and not source_effect_id
        ):
            raise CombatEngineError("encounter effect dependency is malformed")
        if source_actor_id not in updated_sheets or target_actor_id not in updated_sheets:
            raise CombatEngineError("encounter effect dependency actor sheet is missing")
        if dependency == "source_effect_active":
            source_active = any(
                effect.get("active")
                and str(effect.get("id") or "") == source_effect_id
                for effect in updated_sheets[source_actor_id].get("effects", [])
            )
            ended_reason = "source_effect_ended"
        else:
            source_active = not bool(
                condition_ids(updated_sheets[source_actor_id].get("conditions"))
                & INCAPACITATING_STATE_IDS
            )
            ended_reason = "source_incapacitated_or_dead"
        target_active = any(
            effect.get("active") and str(effect.get("id") or "") == target_effect_id
            for effect in updated_sheets[target_actor_id].get("effects", [])
        )
        if target_active and source_active:
            continue
        link["active"] = False
        if not target_active:
            link["ended_reason"] = "target_effect_ended"
        else:
            target_effect = next(
                effect
                for effect in updated_sheets[target_actor_id].get("effects", [])
                if str(effect.get("id") or "") == target_effect_id
            )
            target_effect["active"] = False
            target_effect["ended_reason"] = ended_reason
            reconcile_ended_effect_conditions(
                updated_sheets[target_actor_id],
                ended_effects=[target_effect],
            )
            updated_sheets[target_actor_id] = validate_character_sheet(
                updated_sheets[target_actor_id]
            )
            changed_actor_ids.add(target_actor_id)
            link["ended_reason"] = ended_reason
        ended_links.append(deepcopy(link))
    return {
        "encounter": value,
        "sheets": updated_sheets,
        "changed_actor_ids": sorted(changed_actor_ids),
        "ended_links": ended_links,
    }


_SRD2014_JACK_OF_ALL_TRADES_ID = "dnd5e.content.srd2014.feature.bard-jack-of-all-trades"
_JACK_OF_ALL_TRADES_BOUNDARY_ID = "dnd5e.core.check.jack_of_all_trades"


def _has_content_mechanic(sheet: dict[str, Any], mechanic_id: str) -> bool:
    """Recognize a standard mechanic by its stable contract, not an edition card id."""

    return any(
        isinstance(entry, dict)
        and mechanic_id in {str(item) for item in entry.get("mechanic_refs", [])}
        for section in ("features", "activities")
        for entry in dict(sheet.get("content") or {}).get(section, [])
    )


def _jack_of_all_trades_bonus(sheet: dict[str, Any]) -> int:
    edition = str(sheet.get("edition") or "")
    has_legacy_feature = edition == "2014" and any(
        isinstance(feature, dict) and str(feature.get("id") or "") == _SRD2014_JACK_OF_ALL_TRADES_ID
        for feature in dict(sheet.get("content") or {}).get("features", [])
    )
    has_feature = has_legacy_feature or _has_content_mechanic(
        sheet, _JACK_OF_ALL_TRADES_BOUNDARY_ID
    )
    if not has_feature:
        return 0
    level = int(dict(sheet.get("progression") or {}).get("level", 1) or 1)
    return proficiency_bonus(level) // 2


def start_encounter(
    participants: list[dict[str, Any]],
    *,
    ruleset: str = DEFAULT_CHARACTER_EDITION,
    scene_id: str | None = None,
    name: str = "Combat",
    battle_map: dict[str, Any] | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    """Create encounter state from actor references and derived values.

    The caller must supply any narrative decisions such as surprise and hidden
    participation.  This function only rolls initiative and creates budgets.
    """
    if not participants:
        raise CombatEngineError("combat requires at least one participant")
    normalized_ruleset = _normalize_ruleset(ruleset)
    validated_participants: list[
        tuple[int, dict[str, Any], str, dict[str, Any], dict[str, Any], set[str], int]
    ] = []
    for index, actor in enumerate(participants):
        identifier = actor_id(actor)
        derived = actor_derived(actor)
        sheet = actor_sheet(actor)
        conditions = _condition_set(sheet.get("conditions"))
        exhaustion = int(sheet.get("combat", {}).get("exhaustion", 0) or 0)
        if exhaustion >= 6 and "dead" not in conditions:
            raise CombatEngineError(
                f"actor {identifier} has exhaustion level 6 and must be marked dead"
            )
        validated_participants.append(
            (index, actor, identifier, derived, sheet, conditions, exhaustion)
        )

    combatants: list[dict[str, Any]] = []
    rule_boundary_ids: set[str] = set()
    for index, actor, identifier, derived, sheet, conditions, exhaustion in validated_participants:
        initiative_bonus = int(derived.get("initiative", 0))
        participant_boundary_ids: list[str] = []
        jack_of_all_trades_bonus = (
            _jack_of_all_trades_bonus(sheet)
            if normalized_ruleset == "2014"
            else 0
        )
        if jack_of_all_trades_bonus:
            initiative_bonus += jack_of_all_trades_bonus
            rule_boundary_ids.add(_JACK_OF_ALL_TRADES_BOUNDARY_ID)
            participant_boundary_ids.append(_JACK_OF_ALL_TRADES_BOUNDARY_ID)
        surprised = bool(actor.get("surprised", False))
        initiative_disadvantage = bool(actor.get("initiative_disadvantage", False)) or (
            surprised and normalized_ruleset == "2024"
        )
        exhaustion_adjustment = d20_exhaustion_adjustment(
            ruleset=normalized_ruleset,
            exhaustion=exhaustion,
            kind="initiative",
            bonus=initiative_bonus,
            disadvantage=initiative_disadvantage,
        )
        initiative_bonus = int(exhaustion_adjustment["bonus"])
        initiative_disadvantage = bool(exhaustion_adjustment["disadvantage"])
        speed = int(derived.get("speed", {}).get("walk", 30) or 30)
        if normalized_ruleset == "2024":
            speed = max(0, speed - 5 * exhaustion)
        elif exhaustion >= 5:
            speed = 0
        elif exhaustion >= 2:
            speed //= 2
        speed_multiplier = source_speed_multiplier(sheet)
        current_movement = int(speed * speed_multiplier)
        supplied = actor.get("initiative")
        die = None
        if supplied is None:
            die = roll_d20(
                advantage=bool(actor.get("initiative_advantage", False))
                or ("invisible" in conditions and normalized_ruleset == "2024"),
                disadvantage=initiative_disadvantage,
                reroll_ones=_has_halfling_lucky(sheet),
                rng=rng,
            )
            initiative = die["natural"] + initiative_bonus
        else:
            initiative = int(supplied)
        combatants.append(
            {
                "actor_id": identifier,
                "token_id": actor.get("token_id"),
                "name": actor.get("name", identifier),
                "character_type": str(actor.get("character_type") or ""),
                "initiative": initiative,
                "initiative_roll": die,
                "initiative_bonus": initiative_bonus,
                "_initiative_supplied": supplied is not None,
                "tie_breaker": int(actor.get("tie_breaker", index)),
                "_tie_breaker_supplied": "tie_breaker" in actor,
                "turn_budget": {
                    "main_action": 1,
                    "bonus_action": 1,
                    "reaction": 1,
                    "movement": current_movement,
                    "speed": speed,
                    "object_interaction": 1,
                    "attack_budget": 0,
                },
                "conditions": list(sheet.get("conditions") or []),
                "condition_sources": timed_condition_sources(sheet),
                "speed_multiplier": speed_multiplier,
                "base_speed": speed,
                "position": deepcopy(actor.get("position")),
                "hidden": bool(actor.get("hidden", False)),
                "visible_to_actor_ids": deepcopy(actor.get("visible_to_actor_ids")),
                "disposition": _normalize_disposition(actor.get("disposition")),
                "reach_ft": _nonnegative_int(actor.get("reach_ft"), default=5),
                "can_share_space": bool(actor.get("can_share_space", False)),
                "surprised": bool(actor.get("surprised", False)),
                "turns_completed": 0,
                "death_saves": bool(actor.get("death_saves", actor.get("character_type") == "pc")),
                "zero_hp_recovery": bool(actor.get("zero_hp_recovery", False)),
                "exhaustion": exhaustion,
                "rule_boundary_ids": participant_boundary_ids,
            }
        )
        if combatants[-1]["surprised"] and normalized_ruleset == "2014":
            combatants[-1]["turn_budget"].update(
                main_action=0,
                bonus_action=0,
                movement=0,
                reaction=0,
                object_interaction=0,
            )
    ties: dict[int, list[dict[str, Any]]] = {}
    for combatant in combatants:
        ties.setdefault(int(combatant["initiative"]), []).append(combatant)
    unresolved_ties = [
        items
        for items in ties.values()
        if (
            len(items) > 1
            and all(item["_initiative_supplied"] for item in items)
            and not all(item["_tie_breaker_supplied"] for item in items)
        )
    ]
    if unresolved_ties:
        ruling_kind = (
            "player_owned_choice"
            if any(
                all(item.get("character_type") == "pc" for item in items)
                for items in unresolved_ties
            )
            else "agent_dm_adjudication"
        )
        raise NeedsRulingError(
            "initiative ties need explicit tie_breaker choices",
            missing=("tie_breaker",),
            ruling_kind=ruling_kind,
        )
    for combatant in combatants:
        combatant.pop("_tie_breaker_supplied", None)
        combatant.pop("_initiative_supplied", None)
    combatants.sort(
        key=lambda value: (-value["initiative"], value["tie_breaker"], value["actor_id"])
    )
    return {
        "id": f"encounter-{uuid4().hex}",
        "active": True,
        "name": name or "Combat",
        "scene_id": scene_id,
        "battle_map": deepcopy(battle_map) if battle_map is not None else None,
        "ruleset": normalized_ruleset,
        "round": 1,
        "turn_index": 0,
        "combatants": combatants,
        "reinforcements": [],
        "pending": [],
        "readied": [],
        "effects": [],
        "log": [],
        "rule_boundary_ids": sorted(rule_boundary_ids),
    }


def queue_combatant(
    encounter: dict[str, Any],
    actor: dict[str, Any],
    *,
    joins_round: int | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    """Queue one canonical actor to enter at the start of a future round."""
    value = deepcopy(encounter)
    if not value.get("active", True):
        raise CombatEngineError("cannot join an inactive encounter")
    identifier = actor_id(actor)
    occupied_ids = {
        str(item.get("actor_id") or "")
        for item in [
            *list(value.get("combatants") or []),
            *list(value.get("reinforcements") or []),
        ]
    }
    if identifier in occupied_ids:
        raise CombatEngineError("actor is already present or queued in this encounter")
    current_round = int(value.get("round", 1) or 1)
    due_round = current_round + 1 if joins_round is None else int(joins_round)
    if due_round <= current_round:
        raise CombatEngineError("a queued combatant must join in a future round")

    generated_encounter = start_encounter(
        [actor],
        ruleset=value.get("ruleset"),
        rng=rng,
    )
    generated = generated_encounter["combatants"][0]
    value["rule_boundary_ids"] = sorted(
        {
            *list(value.get("rule_boundary_ids") or []),
            *list(generated_encounter.get("rule_boundary_ids") or []),
        }
    )
    same_initiative = [
        item
        for item in [
            *list(value.get("combatants") or []),
            *list(value.get("reinforcements") or []),
        ]
        if int(item.get("initiative", 0) or 0) == int(generated["initiative"])
    ]
    if same_initiative and "tie_breaker" not in actor:
        ruling_kind = (
            "player_owned_choice"
            if actor.get("character_type") == "pc"
            and all(item.get("character_type") == "pc" for item in same_initiative)
            else "agent_dm_adjudication"
        )
        raise NeedsRulingError(
            "joining initiative ties need an explicit tie_breaker choice",
            missing=("tie_breaker",),
            ruling_kind=ruling_kind,
        )
    if "tie_breaker" not in actor:
        generated["tie_breaker"] = (
            max(
                (int(item.get("tie_breaker", 0) or 0) for item in value["combatants"]),
                default=-1,
            )
            + 1
        )
    generated["join_round"] = due_round
    value["reinforcements"] = [
        *list(value.get("reinforcements") or []),
        generated,
    ]
    value["log"] = [
        *list(value.get("log") or []),
        {
            "type": "reinforcement_queued",
            "actor_id": identifier,
            "initiative": generated["initiative"],
            "join_round": due_round,
        },
    ][-100:]
    return value


def current_combatant(encounter: dict[str, Any]) -> dict[str, Any] | None:
    combatants = list(encounter.get("combatants") or [])
    if not combatants:
        return None
    return combatants[int(encounter.get("turn_index", 0)) % len(combatants)]


def _combat_turn_token(encounter: dict[str, Any]) -> str:
    current = current_combatant(encounter)
    return (
        f"{int(encounter.get('round', 1) or 1)}:"
        f"{int(encounter.get('turn_index', 0) or 0)}:"
        f"{str((current or {}).get('actor_id') or '')}"
    )


def _record_action_payment(
    encounter: dict[str, Any],
    combatant: dict[str, Any],
    *,
    action: str,
    payment: str,
) -> None:
    """Record an Action payment and immediately break incompatible tethers."""

    if payment not in {"main_action", "extra_action"}:
        return
    flags = dict(combatant.get("turn_flags") or {})
    payments = list(flags.get("action_payments") or [])
    payments.append(
        {
            "action": str(action),
            "payment": payment,
            "turn_token": _combat_turn_token(encounter),
        }
    )
    flags["action_payments"] = payments[-8:]
    combatant["turn_flags"] = flags
    if action == "sustain_witch_bolt":
        return
    for effect in encounter.get("ongoing_effects", []):
        if (
            isinstance(effect, dict)
            and effect.get("active", True)
            and effect.get("mechanic_id") == CORE_WITCH_BOLT_MECHANIC_ID
            and str(effect.get("source_actor_id") or "")
            == str(combatant.get("actor_id") or "")
        ):
            effect["active"] = False
            effect["ended_reason"] = "caster_used_action_for_another_purpose"
            effect["ended_turn_token"] = _combat_turn_token(encounter)


def start_witch_bolt_tether(
    encounter: dict[str, Any],
    *,
    caster_id: str,
    target_id: str,
    spell_id: str,
    concentration_effect_id: str,
) -> dict[str, Any]:
    """Create the hard 2014 Witch Bolt tether after its initial attack hits."""

    value = deepcopy(encounter)
    if not value.get("active", True):
        raise CombatEngineError("Witch Bolt requires an active encounter")
    actor_ids = {
        str(item.get("actor_id") or "") for item in value.get("combatants", [])
    }
    if caster_id not in actor_ids or target_id not in actor_ids:
        raise CombatEngineError("Witch Bolt caster and target must be combatants")
    if caster_id == target_id:
        raise CombatEngineError("Witch Bolt cannot target its caster")
    effect_id = str(concentration_effect_id).strip()
    if not effect_id:
        raise CombatEngineError("Witch Bolt requires its exact concentration effect")
    for effect in value.get("ongoing_effects", []):
        if (
            isinstance(effect, dict)
            and effect.get("active", True)
            and effect.get("mechanic_id") == CORE_WITCH_BOLT_MECHANIC_ID
            and str(effect.get("source_actor_id") or "") == caster_id
        ):
            effect["active"] = False
            effect["ended_reason"] = "replaced_by_witch_bolt"
    tether = {
        "id": f"witch-bolt-{uuid4().hex}",
        "kind": "witch_bolt_tether",
        "mechanic_id": CORE_WITCH_BOLT_MECHANIC_ID,
        "active": True,
        "source_actor_id": caster_id,
        "target_id": target_id,
        "source_spell_id": str(spell_id),
        "concentration_effect_id": effect_id,
        "range_ft": 30,
        "repeat_damage": "1d12",
        "damage_type": "lightning",
        "started_turn_token": _combat_turn_token(value),
    }
    value["ongoing_effects"] = [
        *list(value.get("ongoing_effects") or []),
        tether,
    ]
    value["log"] = [
        *list(value.get("log") or []),
        {
            "type": "witch_bolt_tether_started",
            "effect_id": tether["id"],
            "caster_id": caster_id,
            "target_id": target_id,
            "spell_id": str(spell_id),
        },
    ][-100:]
    return {"encounter": value, "effect": deepcopy(tether)}


def reconcile_witch_bolt_range(encounter: dict[str, Any]) -> dict[str, Any]:
    """End active Witch Bolt tethers whose participants are more than 30 feet apart."""

    value = deepcopy(encounter)
    combatants = {
        str(item.get("actor_id") or ""): item for item in value.get("combatants", [])
    }
    ended: list[dict[str, Any]] = []
    for effect in value.get("ongoing_effects", []):
        if (
            not isinstance(effect, dict)
            or not effect.get("active", True)
            or effect.get("mechanic_id") != CORE_WITCH_BOLT_MECHANIC_ID
        ):
            continue
        caster = combatants.get(str(effect.get("source_actor_id") or ""))
        target = combatants.get(str(effect.get("target_id") or ""))
        caster_position = _position((caster or {}).get("position"))
        target_position = _position((target or {}).get("position"))
        if caster_position is None or target_position is None:
            continue
        distance = _grid_distance(caster_position, target_position)
        if distance <= int(effect.get("range_ft", 30) or 30):
            continue
        effect["active"] = False
        effect["ended_reason"] = "target_outside_spell_range"
        effect["ended_distance_ft"] = distance
        ended.append(deepcopy(effect))
    return {"encounter": value, "ended": ended}


def reconcile_witch_bolt_concentration(
    encounter: dict[str, Any],
    *,
    actor_id_value: str,
    active_concentration_effect_ids: set[str],
) -> dict[str, Any]:
    """End a caster's tethers when their exact concentration effect is inactive."""

    value = deepcopy(encounter)
    active_ids = {
        str(effect_id).strip()
        for effect_id in active_concentration_effect_ids
        if str(effect_id).strip()
    }
    ended: list[dict[str, Any]] = []
    for effect in value.get("ongoing_effects", []):
        if (
            not isinstance(effect, dict)
            or not effect.get("active", True)
            or effect.get("mechanic_id") != CORE_WITCH_BOLT_MECHANIC_ID
            or str(effect.get("source_actor_id") or "") != actor_id_value
            or str(effect.get("concentration_effect_id") or "") in active_ids
        ):
            continue
        effect["active"] = False
        effect["ended_reason"] = "concentration_ended"
        effect["ended_turn_token"] = _combat_turn_token(value)
        ended.append(deepcopy(effect))
    return {"encounter": value, "ended": ended}


def pay_witch_bolt_sustain_action(
    encounter: dict[str, Any],
    *,
    actor_id_value: str,
    effect_id: str,
    target_total_cover: bool,
) -> dict[str, Any]:
    """Pay one Action for the fixed 1d12 continuation, or end an invalid tether."""

    if not isinstance(target_total_cover, bool):
        raise CombatEngineError("Witch Bolt total-cover fact must be boolean")
    ranged = reconcile_witch_bolt_range(encounter)
    value = ranged["encounter"]
    effect = next(
        (
            item
            for item in value.get("ongoing_effects", [])
            if isinstance(item, dict)
            and str(item.get("id") or "") == str(effect_id)
            and item.get("mechanic_id") == CORE_WITCH_BOLT_MECHANIC_ID
        ),
        None,
    )
    if effect is None:
        raise CombatEngineError("Witch Bolt tether is not recorded")
    if not effect.get("active", True):
        return {
            "encounter": value,
            "effect": deepcopy(effect),
            "status": "spell_ended",
            "payment": None,
        }
    if str(effect.get("source_actor_id") or "") != actor_id_value:
        raise CombatEngineError("only the Witch Bolt caster can sustain this tether")
    if target_total_cover:
        effect["active"] = False
        effect["ended_reason"] = "target_has_total_cover"
        return {
            "encounter": value,
            "effect": deepcopy(effect),
            "status": "spell_ended",
            "payment": None,
        }
    current = current_combatant(value)
    if current is None or str(current.get("actor_id") or "") != actor_id_value:
        raise CombatEngineError("Witch Bolt can be sustained only on the caster's turn")
    budget = dict(current.get("turn_budget") or {})
    payment = (
        "main_action"
        if int(budget.get("main_action", 0) or 0) > 0
        else "extra_action"
        if int(budget.get("extra_action", 0) or 0) > 0
        else ""
    )
    if not payment:
        raise CombatEngineError("caster has no Action available to sustain Witch Bolt")
    budget[payment] = int(budget[payment]) - 1
    current["turn_budget"] = budget
    _record_action_payment(
        value,
        current,
        action="sustain_witch_bolt",
        payment=payment,
    )
    effect["last_sustained_turn_token"] = _combat_turn_token(value)
    value["log"] = [
        *list(value.get("log") or []),
        {
            "type": "witch_bolt_sustained",
            "effect_id": str(effect["id"]),
            "caster_id": actor_id_value,
            "target_id": str(effect.get("target_id") or ""),
            "payment": payment,
            "turn_token": _combat_turn_token(value),
        },
    ][-100:]
    return {
        "encounter": value,
        "effect": deepcopy(effect),
        "status": "ready",
        "payment": payment,
    }


def available_actions(encounter: dict[str, Any], actor_id_value: str) -> list[str]:
    if not encounter.get("active", True):
        return []
    combatant = next(
        (
            item
            for item in encounter.get("combatants", [])
            if item.get("actor_id") == actor_id_value
        ),
        None,
    )
    if combatant is None:
        raise CombatEngineError(f"combatant not found: {actor_id_value}")
    conditions = _condition_set(combatant.get("conditions"))
    budget = dict(combatant.get("turn_budget") or {})
    current = current_combatant(encounter)
    if current is None or current.get("actor_id") != actor_id_value:
        return []
    if combatant.get("surprised") and _normalize_ruleset(encounter.get("ruleset")) == "2014":
        return []
    if conditions & {"dead", "unconscious", "stunned", "paralyzed", "petrified"}:
        return []
    if "incapacitated" in conditions:
        return ["move"] if "grappled" not in conditions and "restrained" not in conditions else []
    if "turned" in conditions:
        actions = (
            ["move"]
            if budget.get("movement", 0) > 0 and not conditions & {"grappled", "restrained"}
            else []
        )
        if budget.get("main_action", 0) > 0 or budget.get("extra_action", 0) > 0:
            actions.extend(["dash", "dodge"])
            if conditions & {"grappled", "restrained"}:
                actions.append("escape")
        return actions
    actions = (
        ["move"]
        if budget.get("movement", 0) > 0 and not conditions & {"grappled", "restrained"}
        else []
    )
    if budget.get("main_action", 0) > 0 or budget.get("extra_action", 0) > 0:
        actions.extend(
            [
                "attack",
                "cast",
                "dash",
                "disengage",
                "dodge",
                "help",
                "hide",
                "ready",
                "search",
                "shake_hypnotic_pattern",
                "stabilize",
            ]
        )
        if _normalize_ruleset(encounter.get("ruleset")) == "2024":
            actions.extend(["influence", "study", "utilize"])
        else:
            actions.extend(["improvise", "use_object"])
        if conditions & {"grappled", "restrained"}:
            actions.append("escape")
        if (
            int(budget.get("main_action", 0) or 0) <= 0
            and int(budget.get("extra_action", 0) or 0) > 0
            and "cast" in set(
                dict(combatant.get("turn_flags") or {}).get(
                    "extra_action_forbidden_actions", []
                )
            )
        ):
            actions = [item for item in actions if item != "cast"]
    if budget.get("bonus_action", 0) > 0:
        actions.append("bonus_action")
    if budget.get("object_interaction", 0) > 0:
        actions.append("interact_object")
    if budget.get("attack_budget", 0) > 0:
        actions.append("attack")
    return list(dict.fromkeys(actions))


def pay_attack_action(
    encounter: dict[str, Any],
    attacker: dict[str, Any],
    *,
    weapon_id: str,
    attack_mode: str,
    multiattack_option_id: str | None = None,
    target_id: str | None = None,
    light_extra_attack: str | None = None,
    weapon_mastery_followup: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pay one attack while preserving a recorded monster Multiattack composition."""
    value = deepcopy(encounter)
    current = current_combatant(value)
    attacker_id = actor_id(attacker)
    combatant = next(
        item for item in value.get("combatants", []) if item.get("actor_id") == attacker_id
    )
    budget = dict(combatant.get("turn_budget") or {})
    flags = dict(combatant.get("turn_flags") or {})
    legendary_attack = dict(flags.get("legendary_weapon_attack") or {})
    if current is None:
        raise CombatEngineError("combat has no current actor")
    if current.get("actor_id") != attacker_id:
        if (
            legendary_attack.get("turn_token") != _combat_turn_token(value)
            or legendary_attack.get("weapon_id") != weapon_id
            or legendary_attack.get("attack_mode") != attack_mode
        ):
            raise CombatEngineError("it is not this actor's turn")
        flags.pop("legendary_weapon_attack", None)
        if flags:
            combatant["turn_flags"] = flags
        else:
            combatant.pop("turn_flags", None)
        return value, {
            "kind": "legendary_action_attack",
            "activity_id": str(legendary_attack.get("activity_id") or ""),
            "weapon_id": weapon_id,
            "attack_mode": attack_mode,
            "turn_token": str(legendary_attack["turn_token"]),
        }
    active_multiattack = flags.get("multiattack")
    action_payment_key: str | None = None
    normalized_mastery_followup = str(weapon_mastery_followup or "").strip().casefold()
    if normalized_mastery_followup not in {"", "cleave"}:
        raise CombatEngineError("weapon_mastery_followup must be cleave or omitted")
    mastery_followup = (
        dict(flags.get("weapon_mastery_followup") or {})
        if normalized_mastery_followup == "cleave"
        else {}
    )
    normalized_light_payment = str(light_extra_attack or "").strip().casefold()
    if normalized_light_payment not in {"", "bonus_action", "nick"}:
        raise CombatEngineError(
            "light_extra_attack must be bonus_action, nick, or omitted"
        )
    if mastery_followup and normalized_light_payment:
        raise CombatEngineError(
            "a Cleave follow-up cannot also be a Light extra attack"
        )

    attacks = list(actor_derived(attacker).get("inventory", {}).get("weapon_attacks", []))
    selected_weapon = next(
        (item for item in attacks if str(item.get("item_id") or "") == weapon_id),
        None,
    )
    selected_properties = {
        str(item).strip().casefold()
        for item in dict(selected_weapon or {}).get("properties", [])
    }

    if mastery_followup:
        if active_multiattack or multiattack_option_id:
            raise CombatEngineError("Cleave cannot be folded into Multiattack")
        if int(budget.get("attack_budget", 0) or 0) < 1:
            raise CombatEngineError("Cleave follow-up attack is no longer available")
        if str(mastery_followup.get("weapon_id") or "") != weapon_id:
            raise CombatEngineError("Cleave follow-up requires its recorded weapon")
        restricted_target = str(mastery_followup.get("target_id") or "")
        if not target_id or restricted_target != target_id:
            raise CombatEngineError("Cleave follow-up requires its recorded second target")
        budget["attack_budget"] = int(budget["attack_budget"]) - 1
        flags.pop("weapon_mastery_followup", None)
        flags["pending_weapon_attack_modifier"] = deepcopy(mastery_followup)
        payment = {
            "kind": "weapon_mastery_followup",
            "mastery": "cleave",
            "weapon_id": weapon_id,
            "target_id": target_id,
        }
    elif normalized_light_payment:
        if active_multiattack or multiattack_option_id:
            raise CombatEngineError("the Light extra attack cannot be folded into Multiattack")
        if selected_weapon is None or "light" not in selected_properties:
            raise CombatEngineError("the Light extra attack requires a Light weapon")
        if flags.get("light_extra_attack_used"):
            raise CombatEngineError("the Light extra attack is available only once per turn")
        prior_light_attacks = [
            item
            for item in list(flags.get("weapon_attacks_this_turn") or [])
            if item.get("attack_action")
            and item.get("light")
            and str(item.get("weapon_id") or "") != weapon_id
        ]
        if not prior_light_attacks:
            raise CombatEngineError(
                "the Light extra attack requires an earlier Attack-action attack "
                "with a different Light weapon"
            )
        if normalized_light_payment == "nick":
            if str(selected_weapon.get("mastery") or "").strip().casefold() != "nick":
                raise CombatEngineError("Nick requires a weapon with the Nick Mastery property")
            _require_selected_weapon_mastery(
                actor_sheet(attacker),
                weapon_id=weapon_id,
                mastery="nick",
            )
            payment = {
                "kind": "weapon_mastery_followup",
                "mastery": "nick",
                "weapon_id": weapon_id,
                "payment": "attack_action",
            }
            flags["weapon_mastery_nick_used"] = True
        else:
            if int(budget.get("bonus_action", 0) or 0) < 1:
                raise CombatEngineError("the Light extra attack requires a Bonus Action")
            budget["bonus_action"] = int(budget["bonus_action"]) - 1
            payment = {
                "kind": "light_extra_attack",
                "weapon_id": weapon_id,
                "payment": "bonus_action",
            }
            _record_action_payment(
                value,
                combatant,
                action="light_extra_attack",
                payment="bonus_action",
            )
        content = dict(actor_sheet(attacker).get("content") or {})
        features = [
            *list(content.get("features") or []),
            *list(content.get("feats") or []),
        ]
        has_two_weapon_fighting = any(
            str(feature.get("name") or "").strip().casefold() == "two-weapon fighting"
            for feature in features
            if isinstance(feature, dict)
        )
        flags["light_extra_attack_used"] = True
        flags["pending_weapon_attack_modifier"] = {
            "kind": normalized_light_payment,
            "weapon_id": weapon_id,
            "target_id": "",
            "include_attack_ability_modifier": has_two_weapon_fighting,
        }

    elif int(budget.get("attack_budget", 0) or 0) > 0:
        if active_multiattack:
            if multiattack_option_id and multiattack_option_id != active_multiattack.get(
                "option_id"
            ):
                raise CombatEngineError("multiattack option cannot change during the action")
            remaining = _consume_multiattack_attack_entry(
                active_multiattack.get("remaining"), weapon_id, attack_mode
            )
            if remaining:
                flags["multiattack"] = {
                    **dict(active_multiattack),
                    "remaining": remaining,
                }
            else:
                flags.pop("multiattack", None)
            payment = {
                "kind": "multiattack_followup",
                "option_id": active_multiattack.get("option_id"),
            }
        else:
            if multiattack_option_id:
                raise CombatEngineError(
                    "multiattack option can be selected only on its first attack"
                )
            payment = {"kind": "extra_attack"}
        budget["attack_budget"] -= 1
    elif not mastery_followup:
        payment_key = (
            "main_action"
            if int(budget.get("main_action", 0) or 0) > 0
            else "extra_action"
            if int(budget.get("extra_action", 0) or 0) > 0
            else ""
        )
        if not payment_key:
            raise CombatEngineError("actor has no attack payment available")
        action_payment_key = payment_key
        if multiattack_option_id:
            multiattack_options = _validated_multiattack_options(attacker)
            option = _select_multiattack_option(multiattack_options, multiattack_option_id)
            remaining = _consume_multiattack_attack_entry(option["entries"], weapon_id, attack_mode)
            total = sum(int(item["count"]) for item in option["entries"])
            budget["attack_budget"] = total - 1
            if remaining:
                flags["multiattack"] = {
                    "activity_id": option["activity_id"],
                    "option_id": option["id"],
                    "remaining": remaining,
                }
            payment = {
                "kind": "multiattack",
                "payment": payment_key,
                "activity_id": option["activity_id"],
                "option_id": option["id"],
                "attack_count": total,
            }
        else:
            count = int(actor_derived(attacker).get("attacks_per_action", 1) or 1)
            budget["attack_budget"] = max(0, count - 1)
            payment = {
                "kind": "attack_action",
                "payment": payment_key,
                "attack_count": count,
            }
        budget[payment_key] -= 1

    weapon_attacks = list(flags.get("weapon_attacks_this_turn") or [])
    weapon_attacks.append(
        {
            "weapon_id": weapon_id,
            "light": "light" in selected_properties,
            "attack_action": str(payment.get("kind") or "") == "attack_action",
            "payment_kind": str(payment.get("kind") or ""),
        }
    )
    flags["weapon_attacks_this_turn"] = weapon_attacks[-20:]

    combatant["turn_budget"] = budget
    if flags:
        combatant["turn_flags"] = flags
    else:
        combatant.pop("turn_flags", None)
    if action_payment_key is not None:
        _record_action_payment(
            value,
            combatant,
            action="attack",
            payment=action_payment_key,
        )
    return value, payment


def pay_multiattack_activity(
    encounter: dict[str, Any],
    actor_id_value: str,
    *,
    activity_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pay a structured activity that remains inside an active Multiattack."""

    value = deepcopy(encounter)
    current = current_combatant(value)
    if current is None or current.get("actor_id") != actor_id_value:
        raise CombatEngineError("it is not this actor's turn")
    combatant = next(
        item for item in value.get("combatants", []) if item.get("actor_id") == actor_id_value
    )
    budget = dict(combatant.get("turn_budget") or {})
    flags = dict(combatant.get("turn_flags") or {})
    active_multiattack = dict(flags.get("multiattack") or {})
    if not active_multiattack or int(budget.get("attack_budget", 0) or 0) < 1:
        raise CombatEngineError("source activity is not available as a Multiattack follow-up")
    remaining = _consume_multiattack_activity_entry(
        active_multiattack.get("remaining"),
        activity_id,
    )
    budget["attack_budget"] = int(budget["attack_budget"]) - 1
    if remaining:
        flags["multiattack"] = {
            **active_multiattack,
            "remaining": remaining,
        }
    else:
        flags.pop("multiattack", None)
    combatant["turn_budget"] = budget
    if flags:
        combatant["turn_flags"] = flags
    else:
        combatant.pop("turn_flags", None)
    return value, {
        "kind": "multiattack_activity_followup",
        "activity_id": activity_id,
        "option_id": active_multiattack.get("option_id"),
    }


def _require_selected_weapon_mastery(
    sheet: dict[str, Any],
    *,
    weapon_id: str,
    mastery: str,
) -> None:
    """Prove a 2024 actor selected this exact weapon for its printed mastery."""

    if _normalize_ruleset(sheet.get("edition")) != "2024":
        raise CombatEngineError("Weapon Mastery is available only under the 2024 rules")
    features = list(dict(sheet.get("content") or {}).get("features") or [])
    mastery_features = [
        feature
        for feature in features
        if "dnd5e.core.weapon.mastery" in set(feature.get("mechanic_refs") or [])
        or (
            str(feature.get("name") or "").strip().casefold() == "weapon mastery"
            and str(feature.get("class_name") or feature.get("source_key") or "")
            .strip()
            .casefold()
            in {"barbarian", "fighter", "paladin", "ranger", "rogue"}
        )
    ]
    if not mastery_features:
        raise CombatEngineError("Weapon Mastery is not recorded on this actor card")
    feature_ids = {str(feature.get("id") or "") for feature in mastery_features}
    selections = list(dict(sheet.get("content") or {}).get("selections") or [])
    selected_ids: set[str] = set()
    selected_properties: dict[str, str] = {}
    for selection in selections:
        if str(selection.get("artifact_id") or "") not in feature_ids:
            continue
        choice = dict(selection.get("selection") or {})
        raw_ids = choice.get("weapon_ids", [])
        if not isinstance(raw_ids, list) or any(not isinstance(item, str) for item in raw_ids):
            raise CombatEngineError("Weapon Mastery selection.weapon_ids must be a list")
        selected_ids.update(str(item) for item in raw_ids)
        raw_properties = choice.get("mastery_by_weapon_id", {})
        if not isinstance(raw_properties, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in raw_properties.items()
        ):
            raise CombatEngineError(
                "Weapon Mastery selection.mastery_by_weapon_id must be an object"
            )
        selected_properties.update(
            {str(key): str(value).strip().casefold() for key, value in raw_properties.items()}
        )
    if weapon_id not in selected_ids:
        raise CombatEngineError("this weapon was not selected for Weapon Mastery")
    recorded = selected_properties.get(weapon_id)
    if recorded is not None and recorded != mastery:
        raise CombatEngineError("selected Weapon Mastery conflicts with the weapon card")


def preflight_attack(
    attacker: dict[str, Any],
    target: dict[str, Any],
    *,
    action: dict[str, Any],
    encounter: dict[str, Any] | None = None,
    allow_out_of_turn: bool = False,
    require_attack_action: bool = True,
    rules: ResolutionContext | None = None,
) -> dict[str, Any]:
    """Validate an attack declaration without changing any state or rolling."""
    actor_sheet(attacker)
    actor_sheet(target)
    if encounter is not None:
        current = current_combatant(encounter)
        if not allow_out_of_turn and current and current.get("actor_id") != actor_id(attacker):
            raise CombatEngineError("it is not this actor's turn")
        if (
            require_attack_action
            and not allow_out_of_turn
            and "attack" not in available_actions(encounter, actor_id(attacker))
            and not dict(current.get("turn_flags") or {}).get(
                "pending_weapon_attack_modifier"
            )
            and not str(action.get("light_extra_attack") or "").strip()
        ):
            raise CombatEngineError("actor has no legal attack action")
        attacker = deepcopy(attacker)
        target = deepcopy(target)
        for combatant in encounter.get("combatants", []):
            if combatant.get("actor_id") == actor_id(attacker):
                attacker["position"] = deepcopy(combatant.get("position"))
                attacker["turn_flags"] = deepcopy(combatant.get("turn_flags") or {})
                attacker["conditions"] = deepcopy(combatant.get("conditions") or [])
                attacker["hidden"] = bool(combatant.get("hidden", False))
                attacker["death_saves"] = bool(combatant.get("death_saves", True))
                attacker["zero_hp_recovery"] = bool(combatant.get("zero_hp_recovery", False))
                attacker["visible_to_actor_ids"] = deepcopy(combatant.get("visible_to_actor_ids"))
            elif combatant.get("actor_id") == actor_id(target):
                target["position"] = deepcopy(combatant.get("position"))
                target["turn_flags"] = deepcopy(combatant.get("turn_flags") or {})
                target["conditions"] = deepcopy(combatant.get("conditions") or [])
                target["hidden"] = bool(combatant.get("hidden", False))
                target["death_saves"] = bool(combatant.get("death_saves", True))
                target["zero_hp_recovery"] = bool(combatant.get("zero_hp_recovery", False))
                target["visible_to_actor_ids"] = deepcopy(combatant.get("visible_to_actor_ids"))
    attacker_unresolved = actor_derived(attacker).get("unresolved_rules") or []
    if attacker_unresolved:
        raise NeedsRulingError("attacker has unresolved rules", missing=attacker_unresolved)
    target_unresolved = actor_derived(target).get("unresolved_rules") or []
    if target_unresolved:
        raise NeedsRulingError("target has unresolved rules", missing=target_unresolved)
    target_ac = int(actor_derived(target).get("armor_class", 10))
    attacks = list(actor_derived(attacker).get("inventory", {}).get("weapon_attacks", []))
    weapon_id = action.get("weapon_id") or action.get("item_id")
    weapon = next((item for item in attacks if item.get("item_id") == weapon_id), None)
    if weapon_id == "unarmed-strike":
        strength = int(
            (actor_sheet(attacker).get("abilities", {}).get("strength") or {}).get("score", 10)
        )
        modifier = ability_modifier(strength)
        weapon = {
            "item_id": "unarmed-strike",
            "name": "Unarmed Strike",
            "attack_type": "melee",
            "reach_ft": 5,
            "properties": [],
            "attack_bonus": modifier + int(actor_derived(attacker).get("proficiency_bonus", 2)),
            "damage_expression": f"1 {'+' if modifier >= 0 else '-'} {abs(modifier)}",
            "damage_type": "bludgeoning",
        }
    elif weapon is None:
        if weapon_id:
            raise CombatEngineError("weapon is not present in the actor's derived attacks")
        if len(attacks) == 1:
            weapon = attacks[0]
        elif not attacks:
            strength = int(
                (actor_sheet(attacker).get("abilities", {}).get("strength") or {}).get("score", 10)
            )
            modifier = ability_modifier(strength)
            weapon = {
                "item_id": "unarmed-strike",
                "attack_bonus": modifier + int(actor_derived(attacker).get("proficiency_bonus", 2)),
                "damage_expression": f"1 {'+' if modifier >= 0 else '-'} {abs(modifier)}",
                "damage_type": "bludgeoning",
            }
        else:
            raise CombatEngineError("weapon_id is required when actor has multiple attacks")
    if dict(weapon.get("recharge") or {}):
        uses = dict(weapon.get("uses") or {})
        if int(uses.get("value", 0) or 0) < 1:
            raise CombatEngineError(
                "weapon activity is waiting for its Recharge roll"
            )
    attack_flags = dict(attacker.get("turn_flags") or {})
    mastery_followup = dict(attack_flags.get("pending_weapon_attack_modifier") or {})
    declared_mastery_followup = str(
        action.get("weapon_mastery_followup") or ""
    ).strip().casefold()
    if declared_mastery_followup not in {"", "cleave"}:
        raise CombatEngineError("weapon_mastery_followup must be cleave or omitted")
    if declared_mastery_followup:
        if mastery_followup:
            raise CombatEngineError("a paid weapon attack modifier is already pending")
        mastery_followup = dict(attack_flags.get("weapon_mastery_followup") or {})
        if str(mastery_followup.get("kind") or "") != "cleave":
            raise CombatEngineError("no Cleave follow-up attack is pending")
    if mastery_followup:
        if str(mastery_followup.get("weapon_id") or "") != str(
            weapon.get("item_id") or ""
        ):
            raise CombatEngineError("Weapon Mastery follow-up requires its recorded weapon")
        restricted_target = str(mastery_followup.get("target_id") or "")
        if restricted_target and restricted_target != actor_id(target):
            raise CombatEngineError("Cleave follow-up requires its recorded second target")
    ammunition_item_id = action.get("ammunition_item_id") or weapon.get("ammunition_item_id")
    ammunition_slaying: dict[str, Any] | None = None
    if ammunition_item_id:
        ammunition = next(
            (
                item
                for item in actor_sheet(attacker).get("inventory", {}).get("items", [])
                if item.get("id") == ammunition_item_id
            ),
            None,
        )
        if (
            not isinstance(ammunition, dict)
            or ammunition.get("kind") != "ammunition"
            or int(ammunition.get("quantity", 0) or 0) < 1
        ):
            raise CombatEngineError("weapon has no linked ammunition remaining")
        if action.get("ammunition_item_id") and "ammunition" not in {
            str(item).strip().casefold() for item in weapon.get("properties", [])
        }:
            raise CombatEngineError("weapon cannot use selected ammunition")
        slaying = dict(dict(ammunition.get("mechanics") or {}).get("slaying") or {})
        target_species = str(
            dict(actor_sheet(target).get("progression") or {}).get("species") or ""
        ).casefold()
        target_tokens = set(re.findall(r"[a-z0-9_]+", target_species))
        matched_groups = [
            group
            for group in slaying.get("target_groups") or []
            if str(group).casefold() in target_tokens
        ]
        if matched_groups:
            ammunition_slaying = {
                **deepcopy(slaying),
                "matched_groups": matched_groups,
                "ammunition_item_id": str(ammunition_item_id),
            }
    effect_roll_bonus = active_effect_roll_bonus(actor_sheet(attacker), "attack")
    attack_bonus = int(weapon.get("attack_bonus", 0)) + effect_roll_bonus
    context = dict(action.get("context") or {})
    raw_cover = context.get("cover")
    if raw_cover is not None and not isinstance(raw_cover, dict):
        raise CombatEngineError("cover context must be an object")
    cover = dict(raw_cover or {})
    unknown_cover_fields = set(cover) - {"degree"}
    if unknown_cover_fields:
        raise CombatEngineError(
            "cover context accepts only the rules-defined degree"
        )
    cover_degree = str(cover.get("degree") or "none").strip().casefold().replace("-", "_")
    if cover_degree not in {"none", "half", "three_quarters", "total"}:
        raise CombatEngineError(
            "cover degree must be none, half, three_quarters, or total"
        )
    if cover_degree == "total" or context.get("targetable") is False:
        raise CombatEngineError("target has total cover")
    cover_bonus = {"half": 2, "three_quarters": 5}.get(cover_degree, 0)
    target_ac += cover_bonus
    expression = weapon.get("damage_expression") or weapon.get("damage") or ""
    attack_mode = str(action.get("attack_mode") or weapon.get("attack_type") or "melee").lower()
    if attack_mode not in {"melee", "ranged"}:
        raise CombatEngineError("attack_mode must be melee or ranged")
    weapon_attack_type = str(weapon.get("attack_type") or "melee").lower()
    if weapon_attack_type == "ranged" and attack_mode != "ranged":
        raise CombatEngineError("a ranged weapon cannot make a melee weapon attack")
    if attack_mode == "ranged" and weapon_attack_type != "ranged":
        thrown_range = weapon.get("thrown_range_ft")
        if not isinstance(thrown_range, dict) or not thrown_range.get("normal"):
            raise CombatEngineError("weapon has no recorded ranged attack mode")
    properties = {
        str(item).strip().casefold() for item in weapon.get("properties", [])
    }
    light_extra_attack = str(action.get("light_extra_attack") or "").strip().casefold()
    if light_extra_attack not in {"", "bonus_action", "nick"}:
        raise CombatEngineError(
            "light_extra_attack must be bonus_action, nick, or omitted"
        )
    if light_extra_attack:
        if _normalize_ruleset(actor_sheet(attacker).get("edition")) != "2024":
            raise CombatEngineError("this Light extra-attack contract requires 2024 rules")
        if mastery_followup:
            raise CombatEngineError(
                "a Light extra attack cannot also be a Cleave follow-up"
            )
        if "light" not in properties:
            raise CombatEngineError("the Light extra attack requires a Light weapon")
        if attack_flags.get("light_extra_attack_used"):
            raise CombatEngineError("the Light extra attack is available only once per turn")
        if not any(
            item.get("attack_action")
            and item.get("light")
            and str(item.get("weapon_id") or "") != str(weapon.get("item_id") or "")
            for item in list(attack_flags.get("weapon_attacks_this_turn") or [])
        ):
            raise CombatEngineError(
                "the Light extra attack requires an earlier Attack-action attack "
                "with a different Light weapon"
            )
        if light_extra_attack == "nick":
            if str(weapon.get("mastery") or "").strip().casefold() != "nick":
                raise CombatEngineError("Nick requires a weapon with the Nick Mastery property")
            _require_selected_weapon_mastery(
                actor_sheet(attacker),
                weapon_id=str(weapon.get("item_id") or ""),
                mastery="nick",
            )
        elif encounter is not None:
            current_attacker = next(
                (
                    item
                    for item in encounter.get("combatants", [])
                    if str(item.get("actor_id") or "") == actor_id(attacker)
                ),
                None,
            )
            bonus_action = int(
                dict(current_attacker or {})
                .get("turn_budget", {})
                .get("bonus_action", 0)
                or 0
            )
            if bonus_action < 1:
                raise CombatEngineError("the Light extra attack requires a Bonus Action")
        content = dict(actor_sheet(attacker).get("content") or {})
        has_two_weapon_fighting = any(
            str(feature.get("name") or "").strip().casefold() == "two-weapon fighting"
            for feature in [
                *list(content.get("features") or []),
                *list(content.get("feats") or []),
            ]
            if isinstance(feature, dict)
        )
        mastery_followup = {
            "kind": light_extra_attack,
            "weapon_id": str(weapon.get("item_id") or ""),
            "target_id": "",
            "include_attack_ability_modifier": has_two_weapon_fighting,
        }
    supplied_grip = str(action.get("weapon_grip") or "").strip().casefold()
    if supplied_grip and supplied_grip not in {"one_handed", "two_handed"}:
        raise CombatEngineError("weapon_grip must be one_handed or two_handed")
    weapon_grip = supplied_grip or (
        "two_handed" if "two_handed" in properties else "one_handed"
    )
    if weapon_grip == "one_handed" and "two_handed" in properties:
        raise CombatEngineError("this weapon requires two hands")
    if weapon_grip == "two_handed":
        if not properties & {"two_handed", "versatile"}:
            raise CombatEngineError("this weapon has no two-handed attack mode")
        equipped_shield_id = str(
            dict(actor_sheet(attacker).get("inventory") or {})
            .get("equipment_slots", {})
            .get("shield")
            or ""
        )
        if equipped_shield_id:
            raise CombatEngineError(
                "a two-handed weapon attack cannot be made while wielding a shield"
            )
    additional_damage = deepcopy(list(weapon.get("additional_damage") or []))
    if weapon_grip == "two_handed" and "versatile" in properties:
        versatile_formula = str(weapon.get("versatile_damage_formula") or "")
        if not versatile_formula:
            raise CombatEngineError(
                "versatile weapon is missing its two-handed damage formula"
            )
        damage_bonus = int(weapon.get("damage_bonus", 0) or 0)
        expression = (
            f"{versatile_formula} "
            f"{'+' if damage_bonus >= 0 else '-'} {abs(damage_bonus)}"
            if damage_bonus
            else versatile_formula
        )
        # The alternate formula replaces dice already folded into that
        # parenthetical. Damage printed after the alternate clause applies to
        # both grips and is retained separately so its damage type is honored.
        additional_damage = deepcopy(
            list(weapon.get("versatile_additional_damage") or [])
        )
    use_weapon_mastery = action.get("use_weapon_mastery", False)
    if not isinstance(use_weapon_mastery, bool):
        raise CombatEngineError("use_weapon_mastery must be boolean")
    weapon_mastery: dict[str, Any] | None = None
    if use_weapon_mastery:
        mastery = str(weapon.get("mastery") or "").strip().casefold()
        if mastery not in WEAPON_MASTERY_IDS:
            raise CombatEngineError("weapon has no supported Mastery property")
        _require_selected_weapon_mastery(
            actor_sheet(attacker),
            weapon_id=str(weapon.get("item_id") or ""),
            mastery=mastery,
        )
        target_size = effective_size(actor_sheet(target))
        if mastery == "push" and target_size not in {"tiny", "small", "medium", "large"}:
            raise CombatEngineError("Push mastery requires a Large or smaller target")
        if mastery == "cleave" and attack_mode != "melee":
            raise CombatEngineError("Cleave mastery requires a melee attack")
        if mastery == "cleave" and dict(attacker.get("turn_flags") or {}).get(
            "weapon_mastery_cleave_used"
        ):
            raise CombatEngineError("Cleave can grant an extra attack only once per turn")
        if mastery == "nick" and "light" not in properties:
            raise CombatEngineError("Nick mastery requires the Light property")
        if mastery == "nick":
            raise CombatEngineError(
                "Nick mastery is declared through the Light extra-attack entitlement"
            )
        attacker_modifier = int(weapon.get("attack_ability_modifier", 0) or 0)
        weapon_mastery = {
            "id": mastery,
            "weapon_id": str(weapon.get("item_id") or ""),
            "attack_ability_modifier": attacker_modifier,
            "save_dc": (
                8
                + attacker_modifier
                + int(actor_derived(attacker).get("proficiency_bonus", 2) or 2)
                if mastery == "topple"
                else None
            ),
            "target_size": target_size,
        }
        if mastery == "cleave":
            secondary_target_id = str(action.get("mastery_secondary_target_id") or "")
            if encounter is None or not secondary_target_id:
                raise CombatEngineError(
                    "Cleave mastery requires mastery_secondary_target_id in an encounter"
                )
            secondary = next(
                (
                    item
                    for item in encounter.get("combatants", [])
                    if str(item.get("actor_id") or "") == secondary_target_id
                ),
                None,
            )
            primary_position = _position(target.get("position"))
            secondary_position = _position((secondary or {}).get("position"))
            attacker_position = _position(attacker.get("position"))
            reach = int(weapon.get("reach_ft", 5) or 5)
            if (
                secondary is None
                or secondary_target_id in {actor_id(attacker), actor_id(target)}
                or primary_position is None
                or secondary_position is None
                or attacker_position is None
                or _grid_distance(primary_position, secondary_position) > 5
                or _grid_distance(attacker_position, secondary_position) > reach
            ):
                raise CombatEngineError(
                    "Cleave second target must be another creature within 5 feet of "
                    "the first target and within weapon reach"
                )
            weapon_mastery["secondary_target_id"] = secondary_target_id
            weapon_mastery["weapon_reach_ft"] = reach
    if mastery_followup:
        damage_bonus = int(weapon.get("damage_bonus", 0) or 0)
        ability_bonus = int(weapon.get("attack_ability_modifier", 0) or 0)
        if not bool(mastery_followup.get("include_attack_ability_modifier", False)):
            adjusted_bonus = damage_bonus - max(0, ability_bonus)
            damage_formula = str(weapon.get("damage_formula") or "")
            expression = (
                f"{damage_formula} {'+' if adjusted_bonus > 0 else '-'} "
                f"{abs(adjusted_bonus)}"
                if adjusted_bonus
                else damage_formula
            )
    dueling_bonus = _dueling_damage_bonus(
        attacker,
        weapon,
        attack_mode=attack_mode,
        weapon_grip=weapon_grip,
    )
    if dueling_bonus and expression:
        expression = f"{expression} + {dueling_bonus}"
    damage_type = str(weapon.get("damage_type") or "")
    on_hit_effect = str(weapon.get("on_hit_effect") or "").strip()
    if ammunition_slaying is not None:
        if on_hit_effect:
            raise NeedsRulingError(
                "weapon and selected ammunition both define on-hit effects",
                missing=("multiple_on_hit_effects",),
            )
        on_hit_effect = str(ammunition_slaying["source_excerpt"])
    range_result = _attack_range(attacker, target, weapon, attack_mode=attack_mode)
    if range_result["disadvantage"]:
        context["disadvantage"] = True
        context.setdefault("disadvantage_sources", []).append("weapon_long_range")
    close_combat_threat_ids: list[str] = []
    attacker_position = _position(attacker.get("position"))
    if attack_mode == "ranged" and encounter is not None and attacker_position is not None:
        for candidate in encounter.get("combatants", []):
            candidate_id = str(candidate.get("actor_id") or "")
            candidate_position = _position(candidate.get("position"))
            if (
                not candidate_id
                or candidate_id == actor_id(attacker)
                or candidate_position is None
                or _grid_distance(attacker_position, candidate_position) > 5
                or not _are_hostile(candidate, attacker)
                or not _can_see(candidate, attacker)
                or _condition_set(candidate.get("conditions"))
                & INCAPACITATING_STATE_IDS
            ):
                continue
            close_combat_threat_ids.append(candidate_id)
        if close_combat_threat_ids:
            context["disadvantage"] = True
            context.setdefault("disadvantage_sources", []).append("hostile_creature_within_5_ft")
    attacker_conditions = _condition_set(
        attacker.get("conditions") or actor_sheet(attacker).get("conditions")
    )
    attacker_exhaustion = int(actor_sheet(attacker).get("combat", {}).get("exhaustion", 0) or 0)
    ruleset = (
        _normalize_ruleset(encounter.get("ruleset"))
        if encounter is not None
        else _normalize_ruleset(actor_sheet(attacker).get("edition"))
    )
    exhaustion_adjustment = d20_exhaustion_adjustment(
        ruleset=ruleset,
        exhaustion=attacker_exhaustion,
        kind="attack",
        bonus=attack_bonus,
        disadvantage=bool(context.get("disadvantage", False)),
    )
    attack_bonus = int(exhaustion_adjustment["bonus"])
    if exhaustion_adjustment["exhaustion_disadvantage"]:
        context["disadvantage"] = True
        sources = context.setdefault("disadvantage_sources", [])
        if "exhaustion" not in sources:
            sources.append("exhaustion")
    target_conditions = _condition_set(
        target.get("conditions") or actor_sheet(target).get("conditions")
    )
    attacker_can_see_target = bool(
        context.get("attacker_can_see_target", _can_see(attacker, target))
    )
    target_can_see_attacker = bool(
        context.get("target_can_see_attacker", _can_see(target, attacker))
    )
    if not target_can_see_attacker:
        context["advantage"] = True
        context.setdefault("advantage_sources", []).append("attacker_unseen")
    if not attacker_can_see_target:
        context["disadvantage"] = True
        context.setdefault("disadvantage_sources", []).append("target_unseen")
    if attacker_conditions & {"blinded", "poisoned", "prone", "restrained"}:
        context["disadvantage"] = True
        context.setdefault("disadvantage_sources", []).extend(
            sorted(attacker_conditions & {"blinded", "poisoned", "prone", "restrained"})
        )
    unresolved_condition_sources: list[str] = []
    if "charmed" in attacker_conditions:
        charmed_effects = active_condition_source_effects(actor_sheet(attacker), "charmed")
        if not charmed_effects:
            unresolved_condition_sources.append("charmed")
        else:
            charm_sources = {
                str(effect.get("source") or "")
                for effect in charmed_effects
                if str(effect.get("source") or "")
            }
            if not charm_sources or any(
                not str(effect.get("source") or "") for effect in charmed_effects
            ):
                unresolved_condition_sources.append("charmed")
            elif actor_id(target) in charm_sources:
                raise CombatEngineError("a charmed creature cannot attack its charmer")
    if "frightened" in attacker_conditions:
        frightened_effects = active_condition_source_effects(actor_sheet(attacker), "frightened")
        fear_sources = {
            str(effect.get("source") or "")
            for effect in frightened_effects
            if str(effect.get("source") or "")
        }
        if not fear_sources:
            unresolved_condition_sources.append("frightened")
        elif encounter is not None:
            encounter_actor_ids = {
                str(combatant.get("actor_id") or "")
                for combatant in encounter.get("combatants", [])
            }
            if fear_sources - encounter_actor_ids:
                unresolved_condition_sources.append("frightened")
            visible_sources = [
                combatant
                for combatant in encounter.get("combatants", [])
                if str(combatant.get("actor_id") or "") in fear_sources
                and _can_see(attacker, combatant)
            ]
            if visible_sources:
                context["disadvantage"] = True
                context.setdefault("disadvantage_sources", []).append("frightened")
        else:
            unresolved_condition_sources.append("frightened")
    effect_advantage, effect_disadvantage, effect_sources = (
        _active_attack_roll_effect_flags(actor_sheet(attacker))
    )
    if effect_advantage:
        context["advantage"] = True
        context.setdefault("advantage_sources", []).extend(effect_sources)
    if effect_disadvantage:
        context["disadvantage"] = True
        context.setdefault("disadvantage_sources", []).extend(effect_sources)
    if unresolved_condition_sources:
        raise NeedsRulingError(
            "condition source is required to determine this attack's legality",
            missing=sorted(set(unresolved_condition_sources)),
            ruling_kind="missing_or_conflicting_source_review",
        )
    if target_conditions & {
        "blinded",
        "paralyzed",
        "petrified",
        "restrained",
        "stunned",
        "unconscious",
    }:
        context["advantage"] = True
        context.setdefault("advantage_sources", []).extend(
            sorted(
                target_conditions
                & {"blinded", "paralyzed", "petrified", "restrained", "stunned", "unconscious"}
            )
        )
    distance = range_result.get("distance_ft")
    if target_conditions & {"prone", "unconscious"} and distance is not None:
        if int(distance) <= 5:
            context["advantage"] = True
            context.setdefault("advantage_sources", []).append("target_prone_within_5_ft")
        else:
            context["disadvantage"] = True
            context.setdefault("disadvantage_sources", []).append("target_prone_beyond_5_ft")
    target_flags = dict(target.get("turn_flags") or {})
    target_speed = int(actor_derived(target).get("speed", {}).get("walk", 0) or 0)
    if target_conditions & {
        "grappled",
        "paralyzed",
        "petrified",
        "restrained",
        "stunned",
        "unconscious",
    }:
        target_speed = 0
    if (
        target_flags.get("dodging")
        and "incapacitated" not in target_conditions
        and target_speed > 0
        and target_can_see_attacker
    ):
        context["disadvantage"] = True
        context.setdefault("disadvantage_sources", []).append("target_dodging")
    helped_by = None
    next_attack_advantage_effect_id = None
    next_attack_disadvantage_effect_id = None
    if encounter is not None:
        target_position = _position(target.get("position"))
        for helper in encounter.get("combatants", []):
            helping = dict(helper.get("turn_flags") or {}).get("helping")
            helper_position = _position(helper.get("position"))
            if (
                isinstance(helping, dict)
                and helping.get("target_id") == actor_id(attacker)
                and target_position is not None
                and helper_position is not None
                and _grid_distance(helper_position, target_position) <= 5
                and not _condition_set(helper.get("conditions"))
                & INCAPACITATING_STATE_IDS
            ):
                context["advantage"] = True
                context.setdefault("advantage_sources", []).append("help")
                helped_by = str(helper.get("actor_id"))
                break
        for effect in encounter.get("ongoing_effects", []):
            if (
                isinstance(effect, dict)
                and effect.get("active", True)
                and effect.get("kind") == "next_attack_advantage"
                and str(effect.get("target_id") or "") == actor_id(target)
                and (
                    not str(effect.get("eligible_actor_id") or "")
                    or str(effect.get("eligible_actor_id") or "") == actor_id(attacker)
                )
            ):
                next_attack_advantage_effect_id = str(effect.get("id") or "")
                context["advantage"] = True
                context.setdefault("advantage_sources", []).append(next_attack_advantage_effect_id)
                break
        for effect in encounter.get("ongoing_effects", []):
            if (
                isinstance(effect, dict)
                and effect.get("active", True)
                and effect.get("kind") == "next_attack_disadvantage"
                and str(effect.get("target_id") or "") == actor_id(attacker)
            ):
                next_attack_disadvantage_effect_id = str(effect.get("id") or "")
                context["disadvantage"] = True
                context.setdefault("disadvantage_sources", []).append(
                    next_attack_disadvantage_effect_id
                )
                break
        for effect in encounter.get("ongoing_effects", []):
            if (
                isinstance(effect, dict)
                and effect.get("active", True)
                and effect.get("kind") == "attack_disadvantage_against_source"
                and str(effect.get("target_id") or "") == actor_id(attacker)
                and str(effect.get("protected_actor_id") or "") == actor_id(target)
            ):
                effect_id = str(effect.get("id") or "")
                context["disadvantage"] = True
                context.setdefault("disadvantage_sources", []).append(effect_id)
                break
    automatic_critical = bool(
        distance is not None
        and int(distance) <= 5
        and target_conditions & {"paralyzed", "unconscious"}
    )
    extension = apply_rule_event(actor_sheet(attacker), "attack.preflight", rules)
    if extension.status != "committed":
        raise NeedsRulingError(
            "an active rule pack requires an attack choice or ruling",
            missing=[item["mechanic_id"] for item in extension.pending],
            ruling_kind=rule_event_ruling_kind(extension.status, extension.pending),
        )
    for modifier in extension.modifiers:
        opcode = modifier["op"]
        if opcode == "modifier.add":
            target_field = str(modifier.get("target") or "")
            if target_field == "attack_bonus":
                attack_bonus += int(modifier.get("value", 0) or 0)
            elif target_field == "target_ac":
                target_ac += int(modifier.get("value", 0) or 0)
            else:
                raise CombatEngineError(f"unsupported attack modifier target: {target_field}")
        elif opcode == "advantage.add":
            context["advantage"] = True
            context.setdefault("advantage_sources", []).append(modifier["mechanic_id"])
        elif opcode == "disadvantage.add":
            context["disadvantage"] = True
            context.setdefault("disadvantage_sources", []).append(modifier["mechanic_id"])
    sneak_attack = _sneak_attack_plan(
        attacker,
        target,
        weapon=weapon,
        context=context,
        encounter=encounter,
        requested=bool(action.get("use_sneak_attack", False)),
    )
    core_boundary_ids: list[str] = []
    if weapon.get("item_id") == "unarmed-strike":
        core_boundary_ids.append("dnd5e.core.attack.unarmed_strike")
    if attack_mode == "ranged" and range_result.get("enforced"):
        core_boundary_ids.append("dnd5e.core.attack.range")
    if close_combat_threat_ids:
        core_boundary_ids.append("dnd5e.core.attack.ranged_close_combat")
    if ammunition_item_id:
        core_boundary_ids.append("dnd5e.core.attack.ammunition")
    if ammunition_slaying is not None:
        core_boundary_ids.append("dnd5e.core.magic_ammunition.slaying")
    if dict(weapon.get("recharge") or {}):
        core_boundary_ids.append("dnd5e.core.activity.recharge")
    if cover_degree != "none":
        core_boundary_ids.append("dnd5e.core.attack.cover")
    if helped_by:
        core_boundary_ids.append("dnd5e.core.attack.help")
    if sneak_attack:
        core_boundary_ids.append("dnd5e.core.attack.sneak_attack")
    if properties & {"two_handed", "versatile"}:
        core_boundary_ids.append("dnd5e.core.attack.weapon_grip")
    if weapon_mastery is not None:
        core_boundary_ids.append("dnd5e.core.weapon.mastery")
    if mastery_followup:
        core_boundary_ids.append("dnd5e.core.weapon.mastery")
    return {
        "status": "ready",
        "kind": "attack",
        "attacker_id": actor_id(attacker),
        "target_id": actor_id(target),
        "attack_bonus": attack_bonus,
        "effect_roll_bonus": effect_roll_bonus,
        "target_ac": target_ac,
        "cover": {
            "degree": cover_degree,
            "armor_class_bonus": cover_bonus,
        },
        "context_ruling": deepcopy(context.get("agent_ruling")),
        "damage_expression": str(expression),
        "damage_modifiers": (
            [{"source": "Fighting Style: Dueling", "value": dueling_bonus}] if dueling_bonus else []
        ),
        "damage_type": damage_type,
        "weapon_grip": weapon_grip,
        "additional_damage": additional_damage,
        "on_hit_effect": on_hit_effect,
        "standard_on_hit_mechanics": list(weapon.get("standard_on_hit_mechanics") or []),
        "advantage": bool(context.get("advantage", False)),
        "disadvantage": bool(context.get("disadvantage", False)),
        "advantage_sources": list(context.get("advantage_sources") or []),
        "disadvantage_sources": list(context.get("disadvantage_sources") or []),
        "rulings": list(action.get("rulings") or []),
        "weapon_id": weapon.get("item_id"),
        "weapon_recharge": deepcopy(weapon.get("recharge") or {}),
        "weapon_reach_ft": int(weapon.get("reach_ft", 5) or 5),
        "ammunition_item_id": str(ammunition_item_id or ""),
        "ammunition_slaying": ammunition_slaying,
        "attack_mode": attack_mode,
        "resource_cost": deepcopy(weapon.get("resource_cost") or {}),
        "range": range_result,
        "close_combat_threat_ids": close_combat_threat_ids,
        "automatic_critical_on_hit": automatic_critical,
        "ruleset": ruleset,
        "target_uses_death_saves": bool(
            target.get("death_saves", True) or target.get("zero_hp_recovery", False)
        ),
        "attacker_uses_death_saves": bool(
            attacker.get("death_saves", True)
            or attacker.get("zero_hp_recovery", False)
        ),
        "knock_out": bool(action.get("knock_out", False)),
        "melee_attack": attack_mode == "melee",
        "attacker_was_hidden": bool(attacker.get("hidden", False)),
        "target_can_see_attacker": target_can_see_attacker,
        "helped_by": helped_by,
        "next_attack_advantage_effect_id": next_attack_advantage_effect_id,
        "next_attack_disadvantage_effect_id": next_attack_disadvantage_effect_id,
        "sneak_attack": sneak_attack,
        "weapon_mastery": weapon_mastery,
        "weapon_mastery_followup": mastery_followup or None,
        "halfling_lucky": _has_halfling_lucky(actor_sheet(attacker)),
        "rule_receipts": [
            *core_receipts(
                rules,
                core_boundary_ids,
                "attack.preflight",
            ),
            *extension.receipts,
        ],
        "ruleset_fingerprint": rules.fingerprint if rules else "",
    }


def preflight_spell_attack(
    attacker: dict[str, Any],
    target: dict[str, Any],
    *,
    spell_id: str,
    cast_level: int,
    encounter: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    rules: ResolutionContext | None = None,
) -> dict[str, Any]:
    """Build an attack only from the spell card's reviewed resolution contract."""
    sheet = actor_sheet(attacker)
    spell = next(
        (
            item
            for item in sheet.get("content", {}).get("spells", [])
            if str(item.get("id") or "") == str(spell_id)
        ),
        None,
    )
    if spell is None:
        raise CombatEngineError("spell is not recorded on the attacker card")
    resolution = dict(spell.get("resolution") or {})
    if resolution.get("kind") != "spell_attack":
        raise CombatEngineError("spell does not have a reviewed spell-attack resolution")
    attack = dict(resolution.get("attack") or {})
    damage = dict(attack.get("damage") or {})
    derived = deepcopy(actor_derived(attacker))
    spellcasting = dict(derived.get("spellcasting") or {})
    attack_bonus = attack.get("attack_bonus_override")
    if attack_bonus is None:
        attack_bonus = spellcasting.get("attack_bonus")
    if attack_bonus is None:
        raise CombatEngineError("spell attack bonus is not derivable from the attacker card")
    attack_mode = str(attack.get("mode") or "").casefold()
    definition_range = dict(dict(spell.get("definition") or {}).get("range") or {})
    range_ft = attack.get("range_ft_override")
    if range_ft is None:
        if definition_range.get("kind") == "touch":
            range_ft = 5
        else:
            range_ft = definition_range.get("normal_ft")
    range_ft = int(range_ft or 0)
    if range_ft <= 0:
        raise NeedsRulingError(
            "spell attack has no recorded range",
            missing=(f"spell.range:{spell_id}",),
        )
    synthetic_id = f"spell-attack:{spell_id}"
    synthetic = {
        "item_id": synthetic_id,
        "name": str(spell.get("name") or spell_id),
        "attack_type": attack_mode,
        "attack_bonus": int(attack_bonus),
        "damage_expression": scaled_roll_expression(
            damage,
            cast_level=int(cast_level),
            actor_level=int(sheet.get("progression", {}).get("level", 1) or 1),
        ),
        "damage_type": str(damage.get("damage_type") or ""),
        "on_hit_effect": str(attack.get("on_hit_ruling") or ""),
        "standard_on_hit_mechanics": list(attack.get("on_hit_mechanics") or []),
        "properties": [],
    }
    if attack_mode == "ranged":
        synthetic["range_ft"] = {"normal": range_ft, "long": range_ft}
    else:
        synthetic["reach_ft"] = range_ft
    derived.setdefault("inventory", {}).setdefault("weapon_attacks", []).append(synthetic)
    spell_attacker = {**deepcopy(attacker), "derived": derived}
    plan = preflight_attack(
        spell_attacker,
        target,
        action={
            "weapon_id": synthetic_id,
            "attack_mode": attack_mode,
            "context": deepcopy(context or {}),
        },
        encounter=encounter,
        require_attack_action=False,
        rules=rules,
    )
    plan.update(
        kind="spell_attack",
        spell_id=str(spell_id),
        spell_name=str(spell.get("name") or spell_id),
        cast_level=int(cast_level),
        mechanic_id=SPELL_RESOLUTION_MECHANIC_ID,
    )
    plan["rule_receipts"] = [
        *list(plan.get("rule_receipts") or []),
        *core_receipts(
            rules,
            [SPELL_RESOLUTION_MECHANIC_ID],
            "spell.attack.preflight",
        ),
    ]
    return plan


def roll_attack_action(
    *,
    plan: dict[str, Any],
    rng: Any = None,
) -> dict[str, Any]:
    """Roll one prepared attack without rolling damage or changing actor state."""
    attack = resolve_attack(
        armor_class=int(plan["target_ac"]),
        attack_bonus=int(plan["attack_bonus"]),
        advantage=bool(plan.get("advantage")),
        disadvantage=bool(plan.get("disadvantage")),
        reroll_ones=bool(plan.get("halfling_lucky")),
        rng=rng,
    )
    if attack["hit"] and plan.get("automatic_critical_on_hit"):
        attack["critical"] = True
    return {
        **attack,
        "attacker_id": str(plan["attacker_id"]),
        "target_id": str(plan["target_id"]),
        "damage": None,
    }


def apply_attack_ac_bonus(
    attack: dict[str, Any],
    *,
    bonus: int,
    source_id: str,
) -> dict[str, Any]:
    """Re-evaluate a stored attack roll against a reaction AC bonus."""
    value = deepcopy(attack)
    amount = int(bonus)
    if amount <= 0:
        raise CombatEngineError("reaction AC bonus must be positive")
    base_ac = int(value.get("armor_class", 0) or 0)
    effective_ac = base_ac + amount
    hit = bool(
        int(value.get("natural", 0) or 0) == 20
        or (not bool(value.get("fumble")) and int(value.get("total", 0) or 0) >= effective_ac)
    )
    value.update(
        base_armor_class=base_ac,
        armor_class=effective_ac,
        hit=hit,
        critical=bool(hit and value.get("critical")),
        defense={
            "source_id": str(source_id),
            "armor_class_bonus": amount,
            "effective_armor_class": effective_ac,
        },
    )
    return value


def available_attack_defenses(
    target: dict[str, Any],
    *,
    plan: dict[str, Any],
    attack: dict[str, Any],
    encounter: dict[str, Any] | None = None,
    extra_defenses: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return structured reaction defenses legal after this stored attack roll."""
    if not bool(attack.get("hit")):
        return []
    target_id_value = actor_id(target)
    target_conditions = _condition_set(actor_sheet(target).get("conditions"))
    if encounter is not None:
        combatant = next(
            (
                item
                for item in encounter.get("combatants", [])
                if item.get("actor_id") == target_id_value
            ),
            None,
        )
        if combatant is None:
            raise CombatEngineError("attack target is not a combatant")
        target_conditions |= _condition_set(combatant.get("conditions"))
        if int(dict(combatant.get("turn_budget") or {}).get("reaction", 0) or 0) <= 0:
            return []
    if target_conditions & INCAPACITATING_STATE_IDS:
        return []
    equipped_melee = any(
        str(item.get("attack_type") or "").casefold() == "melee"
        for item in actor_derived(target).get("inventory", {}).get("weapon_attacks", [])
    )
    options: list[dict[str, Any]] = []
    for activity in actor_sheet(target).get("content", {}).get("activities", []):
        if str(dict(activity.get("activation") or {}).get("type") or "").casefold() != "reaction":
            continue
        mechanic = _reviewed_attack_ac_bonus(activity)
        if mechanic is None:
            continue
        modes = {
            str(item).casefold() for item in mechanic.get("attack_modes", []) if str(item).strip()
        }
        if str(plan.get("attack_mode") or "").casefold() not in modes:
            continue
        if mechanic.get("requires_visible_attacker") and not plan.get("target_can_see_attacker"):
            continue
        if mechanic.get("requires_wielded_melee_weapon") and not equipped_melee:
            continue
        bonus = int(mechanic.get("bonus", 0) or 0)
        if bonus <= 0:
            continue
        projected = apply_attack_ac_bonus(
            attack,
            bonus=bonus,
            source_id=str(activity.get("id") or ""),
        )
        options.append(
            {
                "id": str(activity.get("id") or ""),
                "name": str(activity.get("name") or "Reaction defense"),
                "kind": "armor_class_bonus",
                "bonus": bonus,
                "projected_hit": bool(projected["hit"]),
                "source_key": str(activity.get("source_key") or ""),
                "rule_refs": deepcopy(list(activity.get("rule_refs") or [])),
                "plan_id": mechanic["plan_id"],
                "plan_fingerprint": mechanic["plan_fingerprint"],
                "solution_version": mechanic["solution_version"],
                "compiled_by": deepcopy(mechanic["compiled_by"]),
                "citations": deepcopy(mechanic["citations"]),
            }
        )
    known_ids = {str(item.get("id") or "") for item in options}
    for candidate in extra_defenses or []:
        candidate_id = str(candidate.get("id") or "")
        bonus = int(candidate.get("bonus", 0) or 0)
        if (
            not candidate_id
            or candidate_id in known_ids
            or str(candidate.get("kind") or "").casefold()
            not in {"armor_class_bonus", "spell_armor_class_bonus"}
            or bonus <= 0
        ):
            continue
        projected = apply_attack_ac_bonus(attack, bonus=bonus, source_id=candidate_id)
        options.append(
            {
                **deepcopy(candidate),
                "id": candidate_id,
                "bonus": bonus,
                "projected_hit": bool(projected["hit"]),
            }
        )
        known_ids.add(candidate_id)
    return options


def _reviewed_attack_ac_bonus(activity: dict[str, Any]) -> dict[str, Any] | None:
    """Return one source-bound contextual defense compiled by an Agent.

    The reaction window is engine-owned, but nonstandard card semantics are not.
    Only the narrow contextual primitive recorded in a durable content solution may
    contribute a candidate; legacy choice dictionaries and prose never do.
    """

    raw_plan = activity.get("resolution_plan")
    raw_solution = activity.get("resolution_solution")
    if not isinstance(raw_plan, dict) or not isinstance(raw_solution, dict):
        return None
    try:
        compiled = compile_resolution_plan(raw_plan)
        solution = normalize_content_solution(
            raw_solution,
            plan=compiled,
            source_card=activity,
        )
    except (ContentSolutionError, ResolutionPlanCompilationError) as error:
        raise CombatEngineError(f"reaction defense content solution is invalid: {error}") from error
    if (
        compiled.source_card_kind != "activity"
        or compiled.source_card_id != str(activity.get("id") or "")
        or compiled.trigger != "attack.after_hit"
        or compiled.trigger_filter not in ({}, {"hit": True})
        or compiled.slots
        or len(compiled.steps) != 1
    ):
        return None
    step = compiled.steps[0]
    if step.get("op") != "attack.ac_bonus" or "when" in step:
        return None
    arguments = dict(step.get("args") or {})
    if set(arguments) - {
        "bonus",
        "attack_modes",
        "requires_visible_attacker",
        "requires_wielded_melee_weapon",
    }:
        return None
    bonus = arguments.get("bonus")
    modes = arguments.get("attack_modes")
    if (
        isinstance(bonus, bool)
        or not isinstance(bonus, int)
        or not 1 <= bonus <= 20
        or not isinstance(modes, list)
        or not modes
        or any(not isinstance(mode, str) for mode in modes)
        or len(modes) != len(set(modes))
        or any(mode not in {"melee", "ranged"} for mode in modes)
        or any(
            field in arguments and not isinstance(arguments[field], bool)
            for field in (
                "requires_visible_attacker",
                "requires_wielded_melee_weapon",
            )
        )
    ):
        raise CombatEngineError("reviewed attack.ac_bonus arguments are malformed")
    return {
        **deepcopy(arguments),
        "plan_id": compiled.id,
        "plan_fingerprint": compiled.fingerprint,
        "solution_version": solution["solution_version"],
        "compiled_by": deepcopy(solution["compiled_by"]),
        "citations": [deepcopy(item) for item in compiled.citations],
    }






def resolve_attack_damage(
    attacker: dict[str, Any],
    target: dict[str, Any],
    *,
    plan: dict[str, Any],
    attack: dict[str, Any],
    rules: ResolutionContext | None = None,
    rng: Any = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Resolve damage and after-effects from one already rolled attack."""
    updated_attacker = deepcopy(attacker)
    updated_target = deepcopy(target)
    result: dict[str, Any] = deepcopy(attack)
    result.update(
        attacker_id=actor_id(attacker),
        target_id=actor_id(target),
        weapon_id=str(plan.get("weapon_id") or ""),
        attack_mode=str(plan.get("attack_mode") or ""),
        cover=deepcopy(plan.get("cover") or {}),
        context_ruling=deepcopy(plan.get("context_ruling")),
        damage=None,
    )
    expression = str(plan.get("damage_expression") or "")
    if attack["hit"] and expression:
        damage_expression = _critical_expression(expression) if attack["critical"] else expression
        damage_roll = roll(damage_expression, rng=rng)
        sneak_plan = dict(plan.get("sneak_attack") or {})
        sneak_roll = None
        if sneak_plan:
            sneak_expression = str(sneak_plan["expression"])
            rolled_sneak_expression = (
                _critical_expression(sneak_expression) if attack["critical"] else sneak_expression
            )
            sneak_roll = roll(rolled_sneak_expression, rng=rng)
        target_sheet = actor_sheet(updated_target)
        rolled_parts = [
            {
                "expression": expression,
                "rolled_expression": damage_expression,
                "rolls": list(damage_roll.rolls),
                "detail": damage_roll.detail,
                "amount": max(0, damage_roll.total + (sneak_roll.total if sneak_roll else 0)),
                "damage_type": str(plan.get("damage_type") or ""),
            }
        ]
        additional_damage = deepcopy(list(plan.get("additional_damage") or []))
        for extra in additional_damage:
            extra_expression = str(extra.get("damage_expression") or "")
            if not extra_expression:
                continue
            rolled_expression = (
                _critical_expression(extra_expression) if attack["critical"] else extra_expression
            )
            extra_roll = roll(rolled_expression, rng=rng)
            rolled_parts.append(
                {
                    "expression": extra_expression,
                    "rolled_expression": rolled_expression,
                    "rolls": list(extra_roll.rolls),
                    "detail": extra_roll.detail,
                    "amount": max(0, extra_roll.total),
                    "damage_type": str(extra.get("damage_type") or ""),
                    "source": str(extra.get("source") or ""),
                }
            )
        if len(rolled_parts) == 1:
            damage = apply_damage_to_sheet(
                target_sheet,
                amount=rolled_parts[0]["amount"],
                damage_type=rolled_parts[0]["damage_type"],
                source=actor_id(attacker),
                critical=bool(attack["critical"]),
                ruleset=str(plan.get("ruleset") or DEFAULT_CHARACTER_EDITION),
                death_saves=bool(plan.get("target_uses_death_saves", True)),
                knock_out=bool(plan.get("knock_out", False)),
                melee=bool(plan.get("melee_attack", False)),
                weapon_attack=str(plan.get("kind") or "") == "attack",
            )
        else:
            damage = apply_damage_parts_to_sheet(
                target_sheet,
                rolled_parts,
                source=actor_id(attacker),
                critical=bool(attack["critical"]),
                ruleset=str(plan.get("ruleset") or DEFAULT_CHARACTER_EDITION),
                death_saves=bool(plan.get("target_uses_death_saves", True)),
                knock_out=bool(plan.get("knock_out", False)),
                melee=bool(plan.get("melee_attack", False)),
                weapon_attack=str(plan.get("kind") or "") == "attack",
            )
        updated_target["sheet"] = damage["sheet"]
        result["damage"] = {
            **damage,
            "expression": expression,
            "rolled_expression": damage_expression,
            "rolls": list(damage_roll.rolls),
            "detail": damage_roll.detail,
            "roll_parts": rolled_parts,
        }
        if sneak_roll is not None:
            result["sneak_attack"] = {
                **sneak_plan,
                "used": True,
                "rolled_expression": sneak_roll.expression,
                "rolls": list(sneak_roll.rolls),
                "total": sneak_roll.total,
                "detail": sneak_roll.detail,
            }
            result["damage"]["sneak_attack"] = deepcopy(result["sneak_attack"])
        if plan.get("on_hit_effect"):
            result["on_hit_ruling"] = {
                "required": True,
                "effect": str(plan["on_hit_effect"]),
                "default_resolver": "agent",
                "ruling_kind": "source_or_scene_fact",
            }
    elif attack["hit"] and plan.get("on_hit_effect"):
        result["on_hit_ruling"] = {
            "required": True,
            "effect": str(plan["on_hit_effect"]),
            "default_resolver": "agent",
            "ruling_kind": "source_or_scene_fact",
        }
    elif plan.get("sneak_attack"):
        result["sneak_attack"] = {**dict(plan["sneak_attack"]), "used": False}
    if attack["hit"] and plan.get("standard_on_hit_mechanics"):
        result["standard_on_hit_mechanics"] = list(plan["standard_on_hit_mechanics"])
    mastery = dict(plan.get("weapon_mastery") or {})
    if mastery:
        mastery_id = str(mastery.get("id") or "")
        mastery_result: dict[str, Any] = {
            "id": mastery_id,
            "weapon_id": str(mastery.get("weapon_id") or ""),
            "applied": False,
        }
        if mastery_id == "graze" and not attack["hit"]:
            graze_amount = max(0, int(mastery.get("attack_ability_modifier", 0) or 0))
            if graze_amount:
                graze_damage = apply_damage_to_sheet(
                    actor_sheet(updated_target),
                    amount=graze_amount,
                    damage_type=str(plan.get("damage_type") or ""),
                    source=actor_id(attacker),
                    critical=False,
                    ruleset=str(plan.get("ruleset") or "2024"),
                    death_saves=bool(plan.get("target_uses_death_saves", True)),
                    weapon_attack=True,
                )
                updated_target["sheet"] = graze_damage["sheet"]
                result["damage"] = {
                    **graze_damage,
                    "expression": str(graze_amount),
                    "rolled_expression": str(graze_amount),
                    "rolls": [],
                    "detail": "Graze: attack ability modifier only",
                    "roll_parts": [
                        {
                            "expression": str(graze_amount),
                            "rolled_expression": str(graze_amount),
                            "rolls": [],
                            "detail": "Graze: attack ability modifier only",
                            "amount": graze_amount,
                            "damage_type": str(plan.get("damage_type") or ""),
                            "source": "weapon_mastery_graze",
                        }
                    ],
                }
            mastery_result.update(
                applied=graze_amount > 0,
                amount=graze_amount,
                damage_type=str(plan.get("damage_type") or ""),
                cannot_be_increased=True,
            )
        elif attack["hit"] and mastery_id == "topple":
            save_dc = int(mastery.get("save_dc", 0) or 0)
            save = resolve_actor_check(
                updated_target,
                kind="save",
                ability="constitution",
                dc=save_dc,
                save_source_kind="nonmagical_effect",
                save_effect_conditions=["prone"],
                ruleset="2024",
                rules=context_with_facts(
                    rules,
                    save_source_kind="nonmagical_effect",
                    save_effect_conditions=["prone"],
                    subject="target",
                ),
                rng=rng,
            )
            if not save["success"]:
                target_sheet = actor_sheet(updated_target)
                apply_condition_change(target_sheet, condition_id="prone", add=True)
                updated_target["sheet"] = validate_character_sheet(target_sheet)
            mastery_result.update(
                applied=not bool(save["success"]),
                save=save,
                condition="prone",
            )
        elif attack["hit"] and mastery_id in {"push", "sap"}:
            mastery_result.update(
                applied=True,
                encounter_effect=(
                    {
                        "kind": "forced_movement",
                        "distance_ft": 10,
                        "direction": "directly_away",
                    }
                    if mastery_id == "push"
                    else {"kind": "next_attack_disadvantage"}
                ),
            )
        elif attack["hit"] and mastery_id in {"slow", "vex"}:
            damage_dealt = (
                int(dict(result.get("damage") or {}).get("applied_amount", 0) or 0)
                > 0
            )
            mastery_result.update(
                applied=damage_dealt,
                encounter_effect=(
                    {"kind": "speed_penalty", "penalty_ft": 10}
                    if mastery_id == "slow"
                    else {
                        "kind": "next_attack_advantage",
                        "eligible_actor_id": actor_id(attacker),
                    }
                )
                if damage_dealt
                else None,
            )
        elif attack["hit"] and mastery_id == "cleave":
            mastery_result.update(
                applied=True,
                encounter_effect={
                    "kind": "cleave_attack_entitlement",
                    "weapon_id": str(mastery.get("weapon_id") or ""),
                    "target_id": str(mastery.get("secondary_target_id") or ""),
                    "include_attack_ability_modifier": False,
                },
            )
        result["weapon_mastery"] = mastery_result
    was_hidden = bool(plan.get("attacker_was_hidden", updated_attacker.get("hidden")))
    if was_hidden:
        updated_attacker["hidden"] = False
        result["reveals_attacker"] = True
    updated_attacker_sheet = actor_sheet(updated_attacker)
    ended_invisibility_effect_ids = _end_attack_broken_invisibility(updated_attacker_sheet)
    if ended_invisibility_effect_ids:
        updated_attacker["sheet"] = updated_attacker_sheet
        result["ended_invisibility_effect_ids"] = ended_invisibility_effect_ids
    resolution_boundaries: list[str] = []
    if was_hidden:
        resolution_boundaries.append("dnd5e.core.attack.hidden_reveal")
    if isinstance(result.get("damage"), dict):
        resolution_boundaries.append("dnd5e.core.damage.zero_hp")
        if bool(plan.get("knock_out", False)):
            resolution_boundaries.append("dnd5e.core.damage.knockout")
    if mastery:
        resolution_boundaries.append("dnd5e.core.weapon.mastery")
    extension_receipts: list[dict[str, Any]] = [
        *list(plan.get("rule_receipts") or []),
        *core_receipts(
            rules,
            resolution_boundaries,
            "attack.resolve",
        ),
    ]
    facts = {
        "kind": "attack",
        "hit": bool(result.get("hit")),
        "critical": bool(result.get("critical")),
        "attacker_id": actor_id(attacker),
        "target_id": actor_id(target),
    }
    attacker_rules = apply_rule_event(
        actor_sheet(updated_attacker),
        "attack.after",
        context_with_facts(rules, **facts, subject="attacker"),
    )
    target_rules = apply_rule_event(
        actor_sheet(updated_target),
        "attack.after",
        context_with_facts(rules, **facts, subject="target"),
    )
    updated_attacker["sheet"] = attacker_rules.sheet
    updated_target["sheet"] = target_rules.sheet
    extension_receipts.extend(attacker_rules.receipts)
    extension_receipts.extend(target_rules.receipts)
    result["rule_receipts"] = extension_receipts
    result["ruleset_fingerprint"] = rules.fingerprint if rules else ""
    return updated_attacker, updated_target, result


def _end_attack_broken_invisibility(sheet: dict[str, Any]) -> list[str]:
    """End the 2014 Invisibility spell after its target makes an attack."""

    ended: list[str] = []
    for effect in sheet.get("effects", []):
        spell_id = str(effect.get("source_spell_id") or "")
        if (
            effect.get("active")
            and spell_id.rsplit(".", 1)[-1] == "invisibility"
        ):
            effect["active"] = False
            effect["ended_reason"] = "actor_attacked"
            ended.append(str(effect.get("id") or ""))
    if ended:
        reconcile_ended_effect_conditions(
            sheet,
            ended_effects=[
                effect
                for effect in sheet.get("effects", [])
                if str(effect.get("id") or "") in set(ended)
            ],
        )
    return ended


def resolve_attack_action(
    attacker: dict[str, Any],
    target: dict[str, Any],
    *,
    plan: dict[str, Any],
    rules: ResolutionContext | None = None,
    rng: Any = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Resolve an attack atomically when no post-hit reaction window is needed."""
    attack = roll_attack_action(plan=plan, rng=rng)
    return resolve_attack_damage(
        attacker,
        target,
        plan=plan,
        attack=attack,
        rules=rules,
        rng=rng,
    )


def _sneak_attack_plan(
    attacker: dict[str, Any],
    target: dict[str, Any],
    *,
    weapon: dict[str, Any],
    context: dict[str, Any],
    encounter: dict[str, Any] | None,
    requested: bool,
) -> dict[str, Any] | None:
    """Validate the shared 2014/2024 Sneak Attack eligibility boundary."""
    if not requested:
        return None
    sheet = actor_sheet(attacker)
    feature = next(
        (
            item
            for item in sheet.get("content", {}).get("features", [])
            if item.get("id") == "dnd5e.content.srd2014.feature.rogue-sneak-attack"
            or (
                str(item.get("name") or "").casefold() == "sneak attack"
                and str(item.get("source_key") or "").casefold() == "rogue"
            )
        ),
        None,
    )
    if feature is None:
        raise CombatEngineError("Sneak Attack is not recorded on this actor card")
    rogue_level = sum(
        int(item.get("level", 0) or 0)
        for item in sheet.get("progression", {}).get("classes", [])
        if str(item.get("name") or "").casefold() == "rogue"
    )
    if rogue_level < 1:
        raise CombatEngineError("Sneak Attack requires at least one Rogue level")
    properties = {str(item).casefold() for item in weapon.get("properties", [])}
    if (
        str(weapon.get("attack_type") or "melee") != "ranged"
        and "finesse" not in properties
    ):
        raise CombatEngineError("Sneak Attack requires a finesse or ranged weapon")
    advantage = bool(context.get("advantage"))
    disadvantage = bool(context.get("disadvantage"))
    if disadvantage and not advantage:
        raise CombatEngineError("Sneak Attack cannot be used while the attack has disadvantage")
    effective_advantage = advantage and not disadvantage
    turn_token = ""
    nearby_enemy = False
    if encounter is not None:
        current = current_combatant(encounter)
        turn_token = (
            f"{int(encounter.get('round', 1))}:"
            f"{int(encounter.get('turn_index', 0))}:"
            f"{str((current or {}).get('actor_id') or '')}"
        )
        attacker_state = next(
            (
                item
                for item in encounter.get("combatants", [])
                if item.get("actor_id") == actor_id(attacker)
            ),
            None,
        )
        if attacker_state is None:
            raise CombatEngineError("Sneak Attack attacker is not in the encounter")
        if (
            dict(attacker_state.get("turn_flags") or {}).get("sneak_attack_turn_token")
            == turn_token
        ):
            raise CombatEngineError("Sneak Attack has already been used on this turn")
        target_state = next(
            (
                item
                for item in encounter.get("combatants", [])
                if item.get("actor_id") == actor_id(target)
            ),
            None,
        )
        if target_state is None:
            raise CombatEngineError("Sneak Attack target is not in the encounter")
        target_position = _position(target_state.get("position"))
        target_disposition = _normalize_disposition(target_state.get("disposition"))
        for candidate in encounter.get("combatants", []):
            if candidate.get("actor_id") in {actor_id(attacker), actor_id(target)}:
                continue
            if _condition_set(candidate.get("conditions")) & INCAPACITATING_STATE_IDS:
                continue
            candidate_position = _position(candidate.get("position"))
            if target_position is None or candidate_position is None:
                continue
            candidate_disposition = _normalize_disposition(candidate.get("disposition"))
            if {target_disposition, candidate_disposition} == {
                "friendly",
                "hostile",
            } and _grid_distance(target_position, candidate_position) <= 5:
                nearby_enemy = True
                break
    if not effective_advantage and not nearby_enemy:
        raise CombatEngineError(
            "Sneak Attack needs effective advantage or another active enemy within 5 feet "
            "of the target"
        )
    expression = f"{(rogue_level + 1) // 2}d6"
    return {
        "feature_id": str(feature.get("id") or "sneak-attack"),
        "expression": expression,
        "turn_token": turn_token,
        "eligibility": "advantage" if effective_advantage else "adjacent_enemy",
    }


def _dueling_damage_bonus(
    attacker: dict[str, Any],
    weapon: dict[str, Any],
    *,
    attack_mode: str,
    weapon_grip: str,
) -> int:
    sheet = actor_sheet(attacker)
    has_style = any(
        str(item.get("name") or "").casefold() == "fighting style"
        and str(item.get("source_key") or "").casefold() == "fighter"
        and str(dict(item.get("choices") or {}).get("option") or "").casefold() == "dueling"
        for item in sheet.get("content", {}).get("features", [])
    )
    if not has_style or attack_mode != "melee" or weapon_grip != "one_handed":
        return 0
    weapon_id = str(weapon.get("item_id") or "")
    selected = next(
        (
            item
            for item in sheet.get("inventory", {}).get("items", [])
            if item.get("id") == weapon_id
        ),
        None,
    )
    if selected is None or selected.get("equipped_slot") not in WEAPON_HAND_SLOTS:
        return 0
    other_weapons = [
        item
        for item in sheet.get("inventory", {}).get("items", [])
        if item.get("id") != weapon_id
        and item.get("kind") == "weapon"
        and item.get("equipped")
        and item.get("equipped_slot") in WEAPON_HAND_SLOTS
    ]
    return 0 if other_weapons else 2


def _validated_multiattack_options(attacker: dict[str, Any]) -> list[dict[str, Any]]:
    sheet = actor_sheet(attacker)
    weapons = {
        str(item.get("item_id") or ""): item
        for item in actor_derived(attacker).get("inventory", {}).get("weapon_attacks", [])
    }
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for activity in sheet.get("content", {}).get("activities", []):
        if not is_multiattack_activity(activity):
            continue
        if str(dict(activity.get("activation") or {}).get("type") or "") != "action":
            raise CombatEngineError("recorded Multiattack must use action activation")
        options = dict(activity.get("choices") or {}).get("multiattack_options")
        if not isinstance(options, list) or not options:
            raise CombatEngineError("recorded Multiattack has no structured options")
        for raw_option in options:
            if not isinstance(raw_option, dict):
                raise CombatEngineError("Multiattack option must be an object")
            option_id = str(raw_option.get("id") or "").strip()
            if not option_id or option_id in seen_ids:
                raise CombatEngineError("Multiattack option ids must be nonempty and unique")
            seen_ids.add(option_id)
            raw_attacks = raw_option.get("attacks")
            raw_activities = raw_option.get("activities", [])
            if not isinstance(raw_attacks, list) or not isinstance(raw_activities, list):
                raise CombatEngineError("Multiattack option attacks and activities must be lists")
            if not raw_attacks and not raw_activities:
                raise CombatEngineError("Multiattack option must list its components")
            attacks: list[dict[str, Any]] = []
            entries: list[dict[str, Any]] = []
            total = 0
            for raw_attack in raw_attacks:
                if not isinstance(raw_attack, dict):
                    raise CombatEngineError("Multiattack attack entry must be an object")
                weapon_id = str(raw_attack.get("weapon_id") or "")
                attack_mode = str(raw_attack.get("attack_mode") or "melee").lower()
                count = int(raw_attack.get("count", 0) or 0)
                if weapon_id not in weapons:
                    raise CombatEngineError(
                        "Multiattack references a weapon absent from derived attacks"
                    )
                if attack_mode not in {"melee", "ranged"}:
                    raise CombatEngineError("Multiattack attack_mode must be melee or ranged")
                weapon = weapons[weapon_id]
                if (
                    attack_mode == "ranged"
                    and str(weapon.get("attack_type") or "melee") != "ranged"
                ):
                    thrown = weapon.get("thrown_range_ft")
                    if not isinstance(thrown, dict) or not thrown.get("normal"):
                        raise CombatEngineError(
                            "Multiattack ranged entry needs a ranged or thrown weapon"
                        )
                if count < 1:
                    raise CombatEngineError("Multiattack attack count must be positive")
                attack = {
                    "weapon_id": weapon_id,
                    "attack_mode": attack_mode,
                    "count": count,
                }
                attacks.append(attack)
                entries.append(attack)
                total += count
            activities: list[dict[str, Any]] = []
            actor_activities = {
                str(item.get("id") or ""): item
                for item in sheet.get("content", {}).get("activities", [])
            }
            for raw_activity in raw_activities:
                if not isinstance(raw_activity, dict):
                    raise CombatEngineError("Multiattack activity entry must be an object")
                activity_id = str(raw_activity.get("activity_id") or "")
                count = int(raw_activity.get("count", 0) or 0)
                component_activity = actor_activities.get(activity_id)
                if (
                    component_activity is None
                    or is_multiattack_activity(component_activity)
                ):
                    raise CombatEngineError("Multiattack references an invalid component activity")
                if (
                    str(dict(component_activity.get("activation") or {}).get("type") or "")
                    != "action"
                    or count < 1
                ):
                    raise CombatEngineError(
                        "Multiattack component activity must be an action with a positive count"
                    )
                component = {"activity_id": activity_id, "count": count}
                activities.append(component)
                entries.append(component)
                total += count
            if total < 2 or total > 10:
                raise CombatEngineError("Multiattack option must contain 2 to 10 attacks")
            result.append(
                {
                    "activity_id": str(activity.get("id") or "multiattack"),
                    "id": option_id,
                    "attacks": attacks,
                    "activities": activities,
                    "entries": entries,
                }
            )
    return result


def _select_multiattack_option(
    options: list[dict[str, Any]], option_id: str | None
) -> dict[str, Any]:
    if option_id:
        selected = next((item for item in options if item["id"] == option_id), None)
        if selected is None:
            raise CombatEngineError("multiattack_option_id is not recorded on the actor card")
        return selected
    if len(options) != 1:
        raise CombatEngineError("multiattack_option_id is required for this actor")
    return options[0]


def _consume_multiattack_attack_entry(
    entries: Any, weapon_id: str, attack_mode: str
) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        raise CombatEngineError("active Multiattack state is malformed")
    remaining = deepcopy(entries)
    match = next(
        (
            item
            for item in remaining
            if item.get("weapon_id") == weapon_id
            and item.get("attack_mode") == attack_mode
            and int(item.get("count", 0) or 0) > 0
        ),
        None,
    )
    if match is None:
        raise CombatEngineError("attack is not allowed by the remaining Multiattack sequence")
    match["count"] = int(match["count"]) - 1
    return [item for item in remaining if int(item.get("count", 0) or 0) > 0]


def _consume_multiattack_activity_entry(
    entries: Any,
    activity_id: str,
) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        raise CombatEngineError("active Multiattack state is malformed")
    remaining = deepcopy(entries)
    match = next(
        (
            item
            for item in remaining
            if item.get("activity_id") == activity_id and int(item.get("count", 0) or 0) > 0
        ),
        None,
    )
    if match is None:
        raise CombatEngineError("activity is not allowed by the remaining Multiattack sequence")
    match["count"] = int(match["count"]) - 1
    return [item for item in remaining if int(item.get("count", 0) or 0) > 0]


def apply_damage_to_sheet(
    sheet: dict[str, Any],
    *,
    amount: int,
    damage_type: str = "",
    source: str = "",
    critical: bool = False,
    ruleset: str | None = None,
    death_saves: bool = True,
    knock_out: bool = False,
    melee: bool = False,
    weapon_attack: bool = False,
) -> dict[str, Any]:
    """Apply one typed damage part with temp HP and trait ordering."""
    raw, adjusted, normalized, adjustment, defense_sources = _adjust_damage_amount(
        sheet,
        amount=amount,
        damage_type=damage_type,
        weapon_attack=weapon_attack,
    )
    result = _apply_adjusted_damage(
        sheet,
        raw=raw,
        adjusted=adjusted,
        damage_type=normalized,
        adjustment=adjustment,
        source=source,
        critical=critical,
        ruleset=ruleset,
        death_saves=death_saves,
        knock_out=knock_out,
        melee=melee,
    )
    result["defense_sources"] = defense_sources
    return result


def apply_hit_point_loss_to_sheet(
    sheet: dict[str, Any],
    *,
    amount: int,
    death_saves: bool = True,
    zero_hp_recovery: bool = False,
) -> dict[str, Any]:
    """Apply source-authored hit-point loss without treating it as damage.

    Source text can explicitly say that a target loses hit points rather than
    taking damage. That wording bypasses temporary hit points and damage
    resistance, does not trigger concentration or massive-damage rules, and
    still applies the normal zero-hit-point state unless a recorded standard
    trait can recover the creature from 0 hit points.
    """

    requested = int(amount)
    if requested <= 0:
        raise CombatEngineError("hit-point loss amount must be positive")
    value = deepcopy(sheet)
    combat = value.setdefault("combat", {})
    hp = dict(combat.setdefault("hp", {"value": 0, "max": 0, "temp": 0}))
    before_hp = int(hp.get("value", 0) or 0)
    hp["value"] = max(0, before_hp - requested)
    combat["hp"] = hp
    conditions = _condition_set(value.get("conditions"))
    became_zero = before_hp > 0 and hp["value"] == 0
    standard_recovery = (
        _validated_standard_relentless_endurance_feature(value)
        if became_zero
        else None
    )
    zero_hp_recovery_result = None
    if became_zero and standard_recovery is not None:
        feature, _ = standard_recovery
        zero_hp_recovery_result = _consume_standard_zero_hp_recovery(feature)
        hp["value"] = 1
    elif became_zero:
        conditions.update({"prone", "unconscious"})
        if not death_saves and not zero_hp_recovery:
            conditions.discard("unconscious")
            conditions.add("dead")
    conditions = reconcile_condition_projection(value, conditions)
    ended_effect_ids: list[str] = []
    if became_zero and conditions & {"unconscious", "dead"}:
        ended_effect_ids = end_concentration_for_incapacitating_conditions(
            value,
            ended_reason="unconscious",
        )
    return {
        "sheet": value,
        "requested_amount": requested,
        "before_hp": before_hp,
        "after_hp": hp["value"],
        "hit_point_loss": before_hp - hp["value"],
        "bypassed_temp_hp": int(hp.get("temp", 0) or 0),
        "ended_effect_ids": ended_effect_ids,
        "zero_hp_recovery": zero_hp_recovery_result,
    }




def _apply_adjusted_damage(
    sheet: dict[str, Any],
    *,
    raw: int,
    adjusted: int,
    damage_type: str,
    adjustment: str,
    source: str,
    critical: bool,
    ruleset: str | None,
    death_saves: bool,
    knock_out: bool,
    melee: bool,
) -> dict[str, Any]:
    """Apply an already trait-adjusted simultaneous damage instance once."""
    value = deepcopy(sheet)
    combat = value.setdefault("combat", {})
    hp = dict(combat.setdefault("hp", {"value": 0, "max": 0, "temp": 0}))
    before_temp = int(hp.get("temp", 0) or 0)
    before_hp = int(hp.get("value", 0) or 0)
    absorbed = min(before_temp, adjusted)
    hp_damage = adjusted - absorbed
    hp["temp"] = before_temp - absorbed
    hp["value"] = max(0, before_hp - hp_damage)
    combat["hp"] = hp
    conditions = _condition_set(value.get("conditions"))
    ended_turn_effects: list[dict[str, Any]] = []
    ended_effect_ids: list[str] = []
    if adjusted > 0:
        for effect in value.get("effects", []):
            if effect.get("active") and (
                effect.get("kind") == "turn_undead"
                or _is_hypnotic_pattern_target_effect(effect)
            ):
                effect["active"] = False
                effect["ended_reason"] = "damaged"
                ended_turn_effects.append(effect)
                ended_effect_ids.append(str(effect.get("id") or ""))
    max_hp = effective_hit_point_maximum(value)
    massive_excess = max(0, hp_damage - before_hp)
    became_zero = hp["value"] == 0 and before_hp > 0
    if knock_out and not melee:
        raise CombatEngineError("only a melee attack can knock a creature out")
    normalized_ruleset = _normalize_ruleset(ruleset or value.get("edition"))
    zero_hp_recovery_result = None
    conditions_before_zero = set(conditions)
    if became_zero:
        conditions.update({"prone", "unconscious"})
        if knock_out and melee:
            if normalized_ruleset == "2024":
                hp["value"] = 1
                conditions.discard("stable")
            else:
                conditions.add("stable")
        elif massive_excess >= max_hp:
            conditions.discard("unconscious")
            conditions.add("dead")
        elif standard_recovery := _validated_standard_relentless_endurance_feature(value):
            feature, _ = standard_recovery
            zero_hp_recovery_result = _consume_standard_zero_hp_recovery(feature)
            hp["value"] = 1
            conditions = conditions_before_zero
        elif not death_saves:
            conditions.discard("unconscious")
            conditions.add("dead")
    death = dict(combat.setdefault("death_saves", {"successes": 0, "failures": 0}))
    if before_hp == 0 and hp_damage > 0 and "dead" not in conditions and death_saves:
        conditions.discard("stable")
        conditions.update({"prone", "unconscious"})
        if hp_damage >= max_hp:
            conditions.discard("unconscious")
            conditions.add("dead")
        else:
            death["failures"] = min(3, int(death.get("failures", 0)) + (2 if critical else 1))
            if death["failures"] >= 3:
                conditions.discard("unconscious")
                conditions.add("dead")
    combat["death_saves"] = death
    knocked_out_2024 = became_zero and knock_out and melee and normalized_ruleset == "2024"
    conditions = reconcile_condition_projection(value, conditions)
    if ended_turn_effects:
        reconcile_ended_effect_conditions(value, ended_effects=ended_turn_effects)
    if hp["value"] > 0 and not knocked_out_2024:
        apply_condition_change(value, condition_id="unconscious", add=False)
    conditions = _condition_set(value.get("conditions"))
    if hp["value"] == 0 and ("unconscious" in conditions or "dead" in conditions):
        ended_effect_ids.extend(
            effect_id
            for effect_id in end_concentration_for_incapacitating_conditions(
                value,
                ended_reason="unconscious",
            )
            if effect_id not in ended_effect_ids
        )
    concentration_effects = [
        effect.get("id")
        for effect in value.get("effects", [])
        if effect.get("active") and effect.get("concentration")
    ]
    concentration = None
    if adjusted > 0 and hp["value"] > 0 and concentration_effects:
        dc = max(10, adjusted // 2)
        if normalized_ruleset == "2024":
            dc = min(30, dc)
        concentration = {
            "dc": dc,
            "effect_ids": concentration_effects,
            "status": "pending",
        }
    return {
        "sheet": value,
        "input_amount": raw,
        "applied_amount": adjusted,
        "absorbed_temp": absorbed,
        "hp_damage": hp_damage,
        "before_temp": before_temp,
        "after_temp": hp["temp"],
        "before_hp": before_hp,
        "after_hp": hp["value"],
        "damage_type": damage_type,
        "adjustment": adjustment,
        "source": source,
        "concentration": concentration,
        "ended_effect_ids": ended_effect_ids,
        "massive_damage": massive_excess >= max_hp,
        "zero_hp_recovery": zero_hp_recovery_result,
    }


def apply_damage_parts_to_sheet(
    sheet: dict[str, Any],
    parts: Iterable[dict[str, Any]],
    *,
    source: str = "",
    critical: bool = False,
    ruleset: str | None = None,
    death_saves: bool = True,
    knock_out: bool = False,
    melee: bool = False,
    weapon_attack: bool = False,
) -> dict[str, Any]:
    """Apply one simultaneous multi-type damage instance and preserve each part.

    Damage types are adjusted separately, but temporary HP, dropping to zero,
    death-save failures, instant death, and concentration are settled once for
    the combined instance. Separate sources must call this function separately.
    """
    grouped: dict[str, int] = {}
    for part in parts:
        if not isinstance(part, dict):
            raise CombatEngineError("damage parts must be objects")
        amount = int(part.get("amount", 0))
        if amount < 0:
            raise CombatEngineError("damage amount cannot be negative")
        damage_type = str(part.get("damage_type") or part.get("type") or "").strip().lower()
        grouped[damage_type] = grouped.get(damage_type, 0) + amount
    details: list[dict[str, Any]] = []
    for grouped_type, grouped_amount in grouped.items():
        raw, adjusted, damage_type, adjustment, defense_sources = _adjust_damage_amount(
            sheet,
            amount=grouped_amount,
            damage_type=grouped_type,
            weapon_attack=weapon_attack,
        )
        details.append(
            {
                "input_amount": raw,
                "applied_amount": adjusted,
                "damage_type": damage_type,
                "adjustment": adjustment,
                "defense_sources": defense_sources,
            }
        )
    if not details:
        raise CombatEngineError("damage packet must contain at least one part")
    applied = _apply_adjusted_damage(
        sheet,
        raw=sum(item["input_amount"] for item in details),
        adjusted=sum(item["applied_amount"] for item in details),
        damage_type="mixed" if len(details) > 1 else details[0]["damage_type"],
        adjustment="per_part" if len(details) > 1 else details[0]["adjustment"],
        source=source,
        critical=critical,
        ruleset=ruleset,
        death_saves=death_saves,
        knock_out=knock_out,
        melee=melee,
    )
    remaining_temp = applied["before_temp"]
    for detail in details:
        absorbed = min(remaining_temp, detail["applied_amount"])
        remaining_temp -= absorbed
        detail["absorbed_temp"] = absorbed
        detail["hp_damage"] = detail["applied_amount"] - absorbed
    return {
        "sheet": applied["sheet"],
        "parts": details,
        "input_amount": applied["input_amount"],
        "applied_amount": applied["applied_amount"],
        "hp_damage": applied["hp_damage"],
        "before_hp": applied["before_hp"],
        "after_hp": applied["after_hp"],
        "before_temp": applied["before_temp"],
        "after_temp": applied["after_temp"],
        "concentration": applied["concentration"],
        "ended_effect_ids": applied["ended_effect_ids"],
        "massive_damage": applied["massive_damage"],
    }




def _validated_standard_source_trait(
    sheet: dict[str, Any],
    kind: str,
) -> dict[str, Any] | None:
    mechanic_ids = {"evasion": "dnd5e.core.save.evasion"}
    mechanic_id = mechanic_ids.get(kind)
    if mechanic_id is None:
        raise CombatEngineError(f"unsupported standard source trait: {kind}")
    matches = []
    for feature in [
        *dict(sheet.get("content") or {}).get("features", []),
        *dict(sheet.get("content") or {}).get("activities", []),
    ]:
        if not isinstance(feature, dict):
            continue
        trait = dict(dict(feature.get("choices") or {}).get("source_trait") or {})
        mechanic_refs = {str(item) for item in feature.get("mechanic_refs", [])}
        if trait.get("kind") == kind and mechanic_id in mechanic_refs:
            matches.append(trait)
    if not matches:
        return None
    if len(matches) != 1:
        raise CombatEngineError(
            f"actor card has more than one standard {kind.replace('_', ' ').title()} trait"
        )
    trait = matches[0]
    validators = {
        "evasion": (
            trait.get("trigger") == "dexterity_save_for_half_damage"
            and trait.get("save_ability") == "dexterity"
            and trait.get("ordinary_successful_save") == "half"
            and trait.get("successful_save") == "none"
            and trait.get("failed_save") == "half"
            and set(trait.get("unavailable_conditions") or [])
            <= {"incapacitated"}
            and trait.get("automatic") is True
            and bool(str(trait.get("source_excerpt") or "").strip())
        ),
    }
    if kind not in validators or not validators[kind]:
        raise CombatEngineError(
            f"standard {kind.replace('_', ' ').title()} trait is malformed"
        )
    return trait


def _validated_standard_relentless_endurance_feature(
    sheet: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return the exact available 2014 core-card feature, never a prose match."""

    if _normalize_ruleset(sheet.get("edition")) != "2014":
        return None
    matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for feature in dict(sheet.get("content") or {}).get("features", []):
        if not isinstance(feature, dict):
            continue
        mechanic_refs = {str(item) for item in feature.get("mechanic_refs", [])}
        if CORE_RELENTLESS_ENDURANCE_MECHANIC_ID not in mechanic_refs:
            continue
        trait = dict(dict(feature.get("choices") or {}).get("source_trait") or {})
        if trait.get("kind") == "relentless_endurance":
            matches.append((feature, trait))
    if not matches:
        return None
    if len(matches) != 1:
        raise CombatEngineError(
            "actor card has more than one standard Relentless Endurance trait"
        )
    feature, trait = matches[0]
    uses = dict(feature.get("uses") or {})
    valid = (
        trait.get("trigger") == "reduced_to_zero_not_killed_outright"
        and trait.get("result_hp") == 1
        and trait.get("automatic") is True
        and bool(str(trait.get("source_excerpt") or "").strip())
        and uses.get("max") == 1
        and uses.get("value") in {0, 1}
        and uses.get("recovers_on") == "long_rest"
        and uses.get("unlimited") is False
    )
    if not valid:
        raise CombatEngineError("standard Relentless Endurance trait is malformed")
    if uses["value"] == 0:
        return None
    return feature, trait


def _consume_standard_zero_hp_recovery(feature: dict[str, Any]) -> dict[str, Any]:
    uses = dict(feature["uses"])
    mutation = mutate_bounded_resource(uses, amount=1, direction="spend")
    feature["uses"] = uses
    return {
        "mechanic_id": CORE_RELENTLESS_ENDURANCE_MECHANIC_ID,
        "feature_id": str(feature.get("id") or ""),
        "result_hp": 1,
        "spent": mutation["amount"],
        "remaining": uses["value"],
    }














def _damage_defense_traits(
    sheet: dict[str, Any],
) -> tuple[dict[str, set[str]], dict[str, dict[str, list[str]]]]:
    traits = dict(sheet.get("traits") or {})
    defenses = {
        key: _trait_set(traits.get(key))
        for key in ("immunities", "resistances", "vulnerabilities")
    }
    sources: dict[str, dict[str, list[str]]] = {
        key: {} for key in ("immunities", "resistances", "vulnerabilities")
    }
    for item in dict(sheet.get("inventory") or {}).get("items", []):
        if (
            not isinstance(item, dict)
            or item.get("kind") != "magic_item"
            or item.get("equipped") is not True
        ):
            continue
        mechanics = dict(item.get("mechanics") or {})
        # The canonical item attunement field already records both the
        # requirement ("required") and active state ("attuned").  Do not add a
        # second, potentially divergent requirement flag under mechanics.
        if item.get("attunement") == "required":
            continue
        grants = dict(mechanics.get("grants") or {})
        item_id = str(item.get("id") or "")
        for defense in defenses:
            values = _trait_set(grants.get(defense))
            if not values:
                continue
            defenses[defense].update(values)
            sources[defense][item_id] = sorted(values)
    return defenses, sources


def _adjust_damage_amount(
    sheet: dict[str, Any],
    *,
    amount: int,
    damage_type: str,
    weapon_attack: bool = False,
) -> tuple[int, int, str, str, list[str]]:
    raw = int(amount)
    if raw < 0:
        raise CombatEngineError("damage amount cannot be negative")
    normalized = damage_type.strip().lower()
    defenses, item_sources = _damage_defense_traits(sheet)
    immunities = defenses["immunities"]
    resistances = defenses["resistances"]
    vulnerabilities = defenses["vulnerabilities"]
    active_sources = [
        f"magic_item:{item_id}"
        for defense in ("immunities", "resistances", "vulnerabilities")
        for item_id, values in item_sources[defense].items()
        if normalized in values
    ]
    blade_ward_sources = [
        str(effect.get("id") or "")
        for effect in sheet.get("effects", [])
        if (
            isinstance(effect, dict)
            and effect.get("active")
            and effect.get("kind") == "spell_blade_ward"
            and effect.get("source") == CORE_BLADE_WARD_MECHANIC_ID
            and weapon_attack
            and normalized in {"bludgeoning", "piercing", "slashing"}
        )
    ]
    active_sources.extend(
        f"spell:{effect_id}" for effect_id in blade_ward_sources if effect_id
    )
    if normalized in immunities:
        return raw, 0, normalized, "immune", active_sources
    adjusted = raw
    resistant = (
        normalized in resistances
        or "petrified" in _condition_set(sheet.get("conditions"))
        or bool(blade_ward_sources)
    )
    if resistant:
        adjusted //= 2
    if normalized in vulnerabilities:
        adjusted *= 2
    if resistant and normalized in vulnerabilities:
        adjustment = "resistant_and_vulnerable"
    elif resistant:
        adjustment = "resistant"
    elif normalized in vulnerabilities:
        adjustment = "vulnerable"
    else:
        adjustment = "normal"
    return raw, adjusted, normalized, adjustment, active_sources


def resolve_death_save_to_sheet(
    sheet: dict[str, Any],
    *,
    advantage: bool = False,
    disadvantage: bool = False,
    bonus: int = 0,
    ruleset: str | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    """Resolve and persist one death save, including natural-20 recovery."""
    value = deepcopy(sheet)
    combat = value.setdefault("combat", {})
    hp = dict(combat.setdefault("hp", {"value": 0, "max": 1, "temp": 0}))
    if int(hp.get("value", 0) or 0) > 0:
        raise CombatEngineError("death saves are only available at 0 hit points")
    conditions = _condition_set(value.get("conditions"))
    if "dead" in conditions:
        raise CombatEngineError("dead actors cannot make death saves")
    if "stable" in conditions:
        raise CombatEngineError("stable actors do not make additional death saves")
    exhaustion_adjustment = d20_exhaustion_adjustment(
        ruleset=str(ruleset or value.get("edition") or DEFAULT_CHARACTER_EDITION),
        exhaustion=int(combat.get("exhaustion", 0) or 0),
        kind="death_save",
        bonus=bonus,
        disadvantage=disadvantage,
    )
    death = dict(combat.setdefault("death_saves", {"successes": 0, "failures": 0}))
    result = resolve_death_save(
        successes=int(death.get("successes", 0)),
        failures=int(death.get("failures", 0)),
        advantage=advantage,
        disadvantage=bool(exhaustion_adjustment["disadvantage"]),
        bonus=int(exhaustion_adjustment["bonus"]),
        reroll_ones=_has_halfling_lucky(value),
        rng=rng,
    )
    if result["outcome"] == "revived":
        hp["value"] = max(1, int(hp.get("value", 0)))
        combat["hp"] = hp
        apply_condition_change(value, condition_id="unconscious", add=False)
    death.update(successes=result["successes"], failures=result["failures"])
    if result["outcome"] == "stable":
        death.update(successes=0, failures=0)
        reconcile_condition_projection(
            value,
            condition_ids(value.get("conditions")) | {"stable", "unconscious"},
        )
    combat["death_saves"] = death
    if result["outcome"] == "dead":
        final_conditions = set(value.get("conditions", []))
        final_conditions.discard("stable")
        final_conditions.discard("unconscious")
        final_conditions.add("dead")
        reconcile_condition_projection(value, final_conditions)
    return {"sheet": value, **result}


def stabilize_sheet(sheet: dict[str, Any]) -> dict[str, Any]:
    """Make one living creature at 0 HP stable and clear its death-save tally."""
    value = deepcopy(sheet)
    combat = value.setdefault("combat", {})
    hp = dict(combat.setdefault("hp", {"value": 0, "max": 1, "temp": 0}))
    if int(hp.get("value", 0) or 0) != 0:
        raise CombatEngineError("only a creature at 0 hit points can be stabilized")
    conditions = _condition_set(value.get("conditions"))
    if "dead" in conditions:
        raise CombatEngineError("a dead creature cannot be stabilized")
    if "stable" in conditions:
        raise CombatEngineError("the creature is already stable")
    before = dict(combat.setdefault("death_saves", {"successes": 0, "failures": 0}))
    combat["death_saves"] = {"successes": 0, "failures": 0}
    reconcile_condition_projection(value, conditions | {"stable", "unconscious"})
    return {
        "sheet": value,
        "status": "stable",
        "before_death_saves": before,
        "after_death_saves": {"successes": 0, "failures": 0},
        "conditions": list(value["conditions"]),
    }


def apply_concentration_result(
    sheet: dict[str, Any],
    *,
    effect_ids: Iterable[str],
    success: bool,
) -> dict[str, Any]:
    """Keep concentration on a successful save and deactivate named effects on failure."""
    value = deepcopy(sheet)
    ids = {str(item) for item in effect_ids}
    ended_effect_ids: list[str] = []
    if not success:
        for effect in value.get("effects", []):
            if effect.get("id") in ids:
                effect["active"] = False
                effect["ended_reason"] = "failed_concentration_save"
                ended_effect_ids.append(str(effect.get("id") or ""))
        clear_ended_invisibility_spell_condition(value, ended_effect_ids=ended_effect_ids)
    return value


def spend_movement(
    encounter: dict[str, Any],
    actor_id_value: str,
    distance: int,
    *,
    destination: Any = None,
    path: list[Any] | None = None,
    movement_mode: str = "voluntary",
    crawl: bool = False,
) -> dict[str, Any]:
    """Consume movement and open opportunity-reaction windows from known geometry.

    Explicit token positions, reach values, map bounds/blocked cells, and
    difficult cells crossed by a cell-by-cell path are automated. Other terrain,
    forced-movement causes, and line-of-effect remain DM-rulable.
    """
    value = deepcopy(encounter)
    distance = int(distance)
    if distance < 0:
        raise CombatEngineError("movement distance cannot be negative")
    movement_mode = str(movement_mode).strip().lower().replace("-", "_")
    if movement_mode not in {"voluntary", "forced", "teleport"}:
        raise CombatEngineError(
            "movement_mode must be voluntary, forced, or teleport"
        )
    willing_movement = movement_mode == "voluntary"
    combatant = next(
        (item for item in value.get("combatants", []) if item.get("actor_id") == actor_id_value),
        None,
    )
    if combatant is None:
        raise CombatEngineError(f"combatant not found: {actor_id_value}")
    if not value.get("active", True):
        raise CombatEngineError("combat is not active")
    if any(
        item.get("kind") == "reaction"
        and item.get("target_id") == actor_id_value
        and item.get("status", "pending") == "pending"
        for item in value.get("pending", [])
    ):
        raise CombatEngineError("pending reaction must be resolved before this actor moves again")
    current = current_combatant(value)
    if current is None:
        raise CombatEngineError("combat has no current actor")
    if current.get("actor_id") != actor_id_value:
        raise CombatEngineError("it is not this actor's turn")
    conditions = _condition_set(combatant.get("conditions"))
    if conditions & {
        "dead",
        "unconscious",
        "stunned",
        "paralyzed",
        "petrified",
        "restrained",
    }:
        raise CombatEngineError("actor cannot move under its current conditions")
    if "grappled" in conditions:
        raise NeedsRulingError(
            "grapple source is needed to determine movement",
            missing=("grapple_source",),
            ruling_kind="missing_or_conflicting_source_review",
        )
    if "prone" in conditions and not crawl:
        raise CombatEngineError("a prone actor must crawl or stand before moving")
    if combatant.get("surprised") and _normalize_ruleset(value.get("ruleset")) == "2014":
        raise CombatEngineError("surprised actor cannot move on its first turn")
    budget = dict(combatant.get("turn_budget") or {})
    available = int(budget.get("movement", 0) or 0)
    origin = _position(combatant.get("position"))
    waypoints: list[tuple[float, float]] = []
    if path is not None:
        if not path:
            raise CombatEngineError("path must contain at least one waypoint")
        if origin is None:
            raise CombatEngineError("a waypoint path requires a known origin")
        waypoints = [_position(item) for item in path]
        if any(item is None for item in waypoints):
            raise CombatEngineError("path waypoints must contain numeric x and y coordinates")
        if waypoints[0] != origin:
            waypoints.insert(0, origin)
        destination = path[-1]
        segment_distance = sum(
            _grid_distance(left, right) for left, right in zip(waypoints, waypoints[1:])
        )
        if segment_distance != distance:
            raise CombatEngineError("movement distance must equal the path segment distance")
    target_position = _position(destination)
    if destination is not None and target_position is None:
        raise CombatEngineError("destination must contain numeric x and y coordinates")
    if path is None and origin is not None and target_position is not None:
        geometric_distance = _grid_distance(origin, target_position)
        if geometric_distance != distance:
            raise CombatEngineError(
                "movement distance must equal the grid distance between origin and destination"
            )
    battle_map = dict(value.get("battle_map") or {})
    difficult_cells = set(battle_map.get("difficult_cells") or [])
    terrain_cost = 0
    if willing_movement and difficult_cells and distance > 0:
        if path is None and distance > 5:
            raise NeedsRulingError(
                "a cell-by-cell path is required to settle difficult terrain",
                missing=("movement_path_for_difficult_terrain",),
            )
        route = waypoints[1:] if path is not None else [target_position]
        if path is not None and any(
            _grid_distance(left, right) != 5 for left, right in zip(waypoints, waypoints[1:])
        ):
            raise CombatEngineError(
                "difficult-terrain paths must enumerate each crossed five-foot cell"
            )
        terrain_cost = sum(
            5
            for point in route
            if point is not None and f"{int(point[0])},{int(point[1])}" in difficult_cells
        )
    movement_cost = distance + (distance if crawl else 0) + terrain_cost
    if movement_cost > available:
        raise CombatEngineError("movement exceeds the remaining speed")
    if target_position is not None:
        occupants = [
            item
            for item in value.get("combatants", [])
            if item.get("actor_id") != actor_id_value
            and _position(item.get("position")) == target_position
            and "dead" not in _condition_set(item.get("conditions"))
        ]
        sharing_allowed = bool(combatant.get("can_share_space")) or any(
            bool(item.get("can_share_space")) for item in occupants
        )
        if occupants and not sharing_allowed:
            if willing_movement:
                raise CombatEngineError(
                    "an actor cannot willingly end movement in another creature's space"
                )
            raise NeedsRulingError(
                "an effect-specific ruling is required for an occupied destination",
                missing=("occupied_destination_resolution",),
            )
    turning = dict(combatant.get("turned") or {})
    if (
        willing_movement
        and "turned" in conditions
        and origin is not None
        and target_position is not None
    ):
        source_id = str(turning.get("source_actor_id") or "")
        source = next(
            (
                item
                for item in value.get("combatants", [])
                if str(item.get("actor_id") or "") == source_id
            ),
            None,
        )
        source_position = _position((source or {}).get("position"))
        if source_position is None:
            raise NeedsRulingError(
                "turned movement requires the turning source position",
                missing=("turn_undead_source_position",),
            )
        before_distance = _grid_distance(origin, source_position)
        after_distance = _grid_distance(target_position, source_position)
        if after_distance <= before_distance:
            raise CombatEngineError(
                "a turned creature must voluntarily move farther from the turning source"
            )
        if before_distance >= 30 and after_distance < 30:
            raise CombatEngineError(
                "a turned creature cannot willingly move within 30 feet of the turning source"
            )
    if (
        willing_movement
        and "frightened" in conditions
        and origin is not None
        and target_position is not None
    ):
        fear_source_ids = list(
            dict(combatant.get("condition_sources") or {}).get("frightened") or []
        )
        if not fear_source_ids:
            raise NeedsRulingError(
                "frightened movement requires the fear source",
                missing=("frightened_source",),
                ruling_kind="missing_or_conflicting_source_review",
            )
        for fear_source_id in fear_source_ids:
            fear_source = next(
                (
                    item
                    for item in value.get("combatants", [])
                    if str(item.get("actor_id") or "") == str(fear_source_id)
                ),
                None,
            )
            if fear_source is None:
                raise NeedsRulingError(
                    "frightened movement source is not in the encounter",
                    missing=("frightened_source_combatant",),
                )
            if not _can_see(combatant, fear_source):
                continue
            fear_source_position = _position(fear_source.get("position"))
            if fear_source_position is None:
                raise NeedsRulingError(
                    "frightened movement requires the visible source position",
                    missing=("frightened_source_position",),
                )
            if _grid_distance(target_position, fear_source_position) < _grid_distance(
                origin, fear_source_position
            ):
                raise CombatEngineError(
                    "a frightened creature cannot willingly move closer to its visible fear source"
                )
    budget["movement"] = available - movement_cost
    combatant["turn_budget"] = budget
    if destination is not None:
        from sagasmith_dnd.spatial import validate_position

        if battle_map:
            for point in path or [destination]:
                validate_position(battle_map, point)
        combatant["position"] = deepcopy(destination)
    if (
        willing_movement
        and origin is not None
        and target_position is not None
        and not _disengaged(combatant)
    ):
        existing = {
            (item.get("event"), item.get("actor_id"), item.get("target_id"))
            for item in value.get("pending", [])
            if item.get("status", "pending") == "pending"
        }
        movement_segments = (
            list(zip(waypoints, waypoints[1:])) if path is not None else [(origin, target_position)]
        )
        for threat in value.get("combatants", []):
            if not _can_make_opportunity_attack(threat, combatant):
                continue
            threat_position = _position(threat.get("position"))
            if threat_position is None:
                continue
            reach = _nonnegative_int(threat.get("reach_ft"), default=5)
            leaving_segment = next(
                (
                    start
                    for start, end in movement_segments
                    if _grid_distance(start, threat_position)
                    <= reach
                    < _grid_distance(end, threat_position)
                ),
                None,
            )
            if leaving_segment is not None:
                key = ("movement.leave_reach", threat.get("actor_id"), actor_id_value)
                if key in existing:
                    continue
                value["pending"] = [
                    *list(value.get("pending") or []),
                    {
                        "id": f"reaction-{uuid4().hex}",
                        "kind": "reaction",
                        "actor_id": threat["actor_id"],
                        "target_id": actor_id_value,
                        "target_position": {"x": leaving_segment[0], "y": leaving_segment[1]},
                        "target_visible": True,
                        "event": "movement.leave_reach",
                        "trigger": "opportunity_attack",
                        "candidates": [
                            {"id": "opportunity_attack"},
                            {"id": "decline"},
                        ],
                        "deadline": "before_commit",
                        "status": "pending",
                    },
                ]
    return reconcile_witch_bolt_range(value)["encounter"]


def stand_up(encounter: dict[str, Any], actor_id_value: str) -> dict[str, Any]:
    """Spend half the recorded speed to end Prone without spending an action."""
    value = deepcopy(encounter)
    combatant = next(
        (item for item in value.get("combatants", []) if item.get("actor_id") == actor_id_value),
        None,
    )
    if combatant is None or current_combatant(value) is None:
        raise CombatEngineError("actor is not the current combatant")
    if current_combatant(value).get("actor_id") != actor_id_value:
        raise CombatEngineError("it is not this actor's turn")
    conditions = _condition_set(combatant.get("conditions"))
    if "prone" not in conditions:
        raise CombatEngineError("actor is not prone")
    if conditions & {"dead", "unconscious", "stunned", "paralyzed", "petrified"}:
        raise CombatEngineError("actor cannot stand under its current conditions")
    budget = dict(combatant.get("turn_budget") or {})
    cost = int(budget.get("speed", 0) or 0) // 2
    if int(budget.get("movement", 0) or 0) < cost:
        raise CombatEngineError("standing requires half the actor's speed in remaining movement")
    budget["movement"] = int(budget["movement"]) - cost
    combatant["turn_budget"] = budget
    combatant["conditions"] = [
        item for item in combatant.get("conditions", []) if str(item).casefold() != "prone"
    ]
    return value


def _force_move_directly(
    encounter: dict[str, Any],
    *,
    source_actor_id: str,
    target_actor_id: str,
    distance_ft: int,
    direction: str,
) -> dict[str, Any]:
    """Move a creature on the source-target ray without voluntary-move effects."""

    value = deepcopy(encounter)
    distance = int(distance_ft)
    if distance < 0 or distance % 5:
        raise CombatEngineError("forced movement distance must be five-foot increments")
    if direction not in {"directly_away", "toward_source"}:
        raise CombatEngineError("forced movement direction is unsupported")
    source = next(
        (
            item
            for item in value.get("combatants", [])
            if str(item.get("actor_id") or "") == str(source_actor_id)
        ),
        None,
    )
    target = next(
        (
            item
            for item in value.get("combatants", [])
            if str(item.get("actor_id") or "") == str(target_actor_id)
        ),
        None,
    )
    if source is None or target is None:
        raise CombatEngineError("forced movement source and target must be combatants")
    source_position = _position(source.get("position"))
    target_position = _position(target.get("position"))
    if source_position is None or target_position is None:
        raise NeedsRulingError(
            "direct forced movement requires recorded source and target positions",
            missing=("forced_movement_positions",),
        )
    if direction == "directly_away":
        delta_x = target_position[0] - source_position[0]
        delta_y = target_position[1] - source_position[1]
    else:
        delta_x = source_position[0] - target_position[0]
        delta_y = source_position[1] - target_position[1]
    if delta_x == 0 and delta_y == 0:
        raise NeedsRulingError(
            "direct forced movement is ambiguous for overlapping tokens",
            missing=("forced_movement_direction",),
        )
    grid_span = max(abs(delta_x), abs(delta_y))

    def _grid_ray_offset(component: float, step: int) -> int:
        """Round the continuous source-target ray to the nearest grid cell."""

        scaled = (component * step) / grid_span
        return int(scaled + 0.5) if scaled >= 0 else int(scaled - 0.5)

    battle_map = dict(value.get("battle_map") or {})
    occupied = {
        _position(item.get("position"))
        for item in value.get("combatants", [])
        if str(item.get("actor_id") or "") != str(target_actor_id)
        and "dead" not in _condition_set(item.get("conditions"))
        and _position(item.get("position")) is not None
    }
    destination = target_position
    moved_cells = 0
    from sagasmith_dnd.spatial import BattleMapError, validate_position

    for step in range(1, (distance // 5) + 1):
        candidate = (
            target_position[0] + _grid_ray_offset(delta_x, step),
            target_position[1] + _grid_ray_offset(delta_y, step),
        )
        candidate_dict = {"x": int(candidate[0]), "y": int(candidate[1])}
        if candidate in occupied:
            break
        if battle_map:
            try:
                validate_position(battle_map, candidate_dict)
            except BattleMapError:
                break
        destination = candidate
        moved_cells += 1
    target["position"] = {"x": int(destination[0]), "y": int(destination[1])}
    moved_distance = moved_cells * 5
    value["log"] = [
        *list(value.get("log") or []),
        {
            "type": "forced_movement",
            "source_actor_id": str(source_actor_id),
            "target_actor_id": str(target_actor_id),
            "requested_distance_ft": distance,
            "moved_distance_ft": moved_distance,
            "direction": direction,
            "opportunity_reactions": False,
        },
    ][-100:]
    reconciled = reconcile_witch_bolt_range(value)
    return {
        "encounter": reconciled["encounter"],
        "source_actor_id": str(source_actor_id),
        "target_actor_id": str(target_actor_id),
        "requested_distance_ft": distance,
        "moved_distance_ft": moved_distance,
        "destination": deepcopy(target["position"]),
        "direction": direction,
        "ended_witch_bolt_tether_ids": [
            str(item.get("id") or "") for item in reconciled["ended"]
        ],
    }


def force_move_directly_away(
    encounter: dict[str, Any],
    *,
    source_actor_id: str,
    target_actor_id: str,
    distance_ft: int,
) -> dict[str, Any]:
    """Move a creature the farthest legal grid distance directly from a source."""

    return _force_move_directly(
        encounter,
        source_actor_id=source_actor_id,
        target_actor_id=target_actor_id,
        distance_ft=distance_ft,
        direction="directly_away",
    )


def force_move_directly_toward(
    encounter: dict[str, Any],
    *,
    source_actor_id: str,
    target_actor_id: str,
    distance_ft: int,
) -> dict[str, Any]:
    """Move a creature the farthest legal grid distance toward a source."""

    return _force_move_directly(
        encounter,
        source_actor_id=source_actor_id,
        target_actor_id=target_actor_id,
        distance_ft=distance_ft,
        direction="toward_source",
    )


def apply_weapon_mastery_to_encounter(
    encounter: dict[str, Any],
    result: dict[str, Any],
    *,
    attacker_id: str,
    target_id: str,
) -> dict[str, Any]:
    """Commit the encounter-scoped portion of one resolved 2024 mastery effect."""

    value = deepcopy(encounter)
    mastery = dict(result.get("weapon_mastery") or {})
    if not mastery or not mastery.get("applied"):
        return {"encounter": value, "effect": None}
    if _normalize_ruleset(value.get("ruleset")) != "2024":
        raise CombatEngineError("Weapon Mastery requires a 2024 encounter")
    combatants = {
        str(item.get("actor_id") or ""): item for item in value.get("combatants", [])
    }
    attacker = combatants.get(attacker_id)
    target = combatants.get(target_id)
    if attacker is None or target is None:
        raise CombatEngineError("Weapon Mastery source and target must be combatants")
    mastery_id = str(mastery.get("id") or "")
    encounter_effect = dict(mastery.get("encounter_effect") or {})
    if mastery_id == "push":
        moved = force_move_directly_away(
            value,
            source_actor_id=attacker_id,
            target_actor_id=target_id,
            distance_ft=int(encounter_effect.get("distance_ft", 10) or 10),
        )
        moved_value = moved["encounter"]
        effect = {
            "kind": "weapon_mastery_push",
            **{key: item for key, item in moved.items() if key != "encounter"},
        }
        moved_value["log"] = [
            *list(moved_value.get("log") or []),
            {
                "type": "weapon_mastery",
                "attacker_id": attacker_id,
                "target_id": target_id,
                "effect": deepcopy(effect),
            },
        ][-100:]
        return {
            "encounter": moved_value,
            "effect": effect,
        }
    if mastery_id == "topple":
        conditions = _condition_set(target.get("conditions"))
        if "prone" not in conditions:
            target["conditions"] = sorted({*conditions, "prone"})
        effect = {
            "kind": "weapon_mastery_topple",
            "target_id": target_id,
            "condition": "prone",
        }
    elif mastery_id in {"sap", "slow", "vex"}:
        kind = str(encounter_effect.get("kind") or "")
        expected_kind = {
            "sap": "next_attack_disadvantage",
            "slow": "speed_penalty",
            "vex": "next_attack_advantage",
        }[mastery_id]
        if kind != expected_kind:
            raise CombatEngineError("resolved Weapon Mastery encounter effect is malformed")
        source_turns = int(attacker.get("turns_completed", 0) or 0)
        effect = {
            "id": f"weapon-mastery-{mastery_id}-{uuid4().hex}",
            "kind": kind,
            "mechanic_id": "dnd5e.core.weapon.mastery",
            "mastery": mastery_id,
            "active": True,
            "source_actor_id": attacker_id,
            "target_id": target_id,
            "created_source_turns_completed": source_turns,
            "expires_on": "source_turn_end" if mastery_id == "vex" else "source_turn_start",
            "expires_after_source_turns_completed": source_turns
            + (2 if mastery_id == "vex" else 1),
        }
        if mastery_id == "slow":
            effect["penalty_ft"] = 10
        if mastery_id == "vex":
            effect["eligible_actor_id"] = attacker_id
        if mastery_id == "slow":
            for existing in value.get("ongoing_effects", []):
                if (
                    isinstance(existing, dict)
                    and existing.get("active", True)
                    and existing.get("kind") == "speed_penalty"
                    and existing.get("mastery") == "slow"
                    and str(existing.get("target_id") or "") == target_id
                ):
                    existing["active"] = False
                    existing["ended_reason"] = "replaced_by_slow_mastery"
        value["ongoing_effects"] = [*list(value.get("ongoing_effects") or []), effect]
    elif mastery_id == "cleave":
        if attacker.get("turn_flags", {}).get("weapon_mastery_cleave_used"):
            raise CombatEngineError("Cleave can grant an extra attack only once per turn")
        if attacker.get("turn_flags", {}).get("weapon_mastery_followup"):
            raise CombatEngineError("a Weapon Mastery follow-up is already pending")
        if encounter_effect.get("kind") != "cleave_attack_entitlement":
            raise CombatEngineError("resolved Cleave entitlement is malformed")
        weapon_id = str(encounter_effect.get("weapon_id") or "")
        secondary_target_id = str(encounter_effect.get("target_id") or "")
        if not weapon_id or not secondary_target_id:
            raise CombatEngineError("resolved Cleave entitlement is incomplete")
        flags = dict(attacker.get("turn_flags") or {})
        flags["weapon_mastery_cleave_used"] = True
        flags["weapon_mastery_followup"] = {
            "kind": "cleave",
            "weapon_id": weapon_id,
            "target_id": secondary_target_id,
            "include_attack_ability_modifier": False,
        }
        attacker["turn_flags"] = flags
        budget = dict(attacker.get("turn_budget") or {})
        budget["attack_budget"] = int(budget.get("attack_budget", 0) or 0) + 1
        attacker["turn_budget"] = budget
        effect = {
            "kind": "weapon_mastery_cleave",
            "source_actor_id": attacker_id,
            "target_id": secondary_target_id,
            "weapon_id": weapon_id,
            "settlement": deepcopy(mastery),
        }
    elif mastery_id in {"graze", "nick"}:
        effect = {
            "kind": f"weapon_mastery_{mastery_id}",
            "source_actor_id": attacker_id,
            "target_id": target_id,
            "settlement": deepcopy(mastery),
        }
    else:
        raise CombatEngineError("unsupported Weapon Mastery result")
    value["log"] = [
        *list(value.get("log") or []),
        {
            "type": "weapon_mastery",
            "attacker_id": attacker_id,
            "target_id": target_id,
            "effect": deepcopy(effect),
        },
    ][-100:]
    return {"encounter": value, "effect": deepcopy(effect)}


def consume_weapon_mastery_attack_effects(
    encounter: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Consume Vex or Sap after the attack roll for which it supplied a modifier."""

    value = deepcopy(encounter)
    consumed: list[str] = []
    requested = {
        str(plan.get("next_attack_advantage_effect_id") or ""),
        str(plan.get("next_attack_disadvantage_effect_id") or ""),
    }
    requested.discard("")
    for effect in value.get("ongoing_effects", []):
        if (
            isinstance(effect, dict)
            and str(effect.get("id") or "") in requested
            and effect.get("mechanic_id") == "dnd5e.core.weapon.mastery"
            and effect.get("active", True)
        ):
            effect["active"] = False
            effect["ended_reason"] = "consumed_by_attack_roll"
            consumed.append(str(effect["id"]))
    attacker_id = str(plan.get("attacker_id") or "")
    weapon_id = str(plan.get("weapon_id") or "")
    target_id = str(plan.get("target_id") or "")
    for combatant in value.get("combatants", []):
        if str(combatant.get("actor_id") or "") != attacker_id:
            continue
        flags = dict(combatant.get("turn_flags") or {})
        pending = dict(flags.get("pending_weapon_attack_modifier") or {})
        if not pending:
            break
        restricted_target = str(pending.get("target_id") or "")
        if str(pending.get("weapon_id") or "") != weapon_id or (
            restricted_target and restricted_target != target_id
        ):
            raise CombatEngineError(
                "pending weapon attack modifier does not match the resolved attack"
            )
        flags.pop("pending_weapon_attack_modifier", None)
        if flags:
            combatant["turn_flags"] = flags
        else:
            combatant.pop("turn_flags", None)
        break
    return {"encounter": value, "consumed_effect_ids": consumed}


def resolve_common_action(
    encounter: dict[str, Any],
    *,
    actor_id_value: str,
    action: str,
    target_id: str | None = None,
    trigger: str | None = None,
    payload: dict[str, Any] | None = None,
    payment: str | None = None,
) -> dict[str, Any]:
    """Settle the non-attack actions that have deterministic action-economy effects.

    Narrative outcomes (a successful hide/search/help consequence) remain a DM
    ruling.  The action payment and temporary tactical flags do not.
    """
    value = deepcopy(encounter)
    action = str(action).strip().lower().replace("-", "_")
    supported = {
        "cast",
        "dash",
        "disengage",
        "dodge",
        "escape",
        "help",
        "hide",
        "interact_object",
        "ready",
        "search",
        "shake_hypnotic_pattern",
        "influence",
        "improvise",
        "study",
        "stabilize",
        "utilize",
        "use_object",
    }
    if action not in supported:
        raise CombatEngineError(f"unsupported common action: {action}")
    current = current_combatant(value)
    combatant = next(
        (item for item in value.get("combatants", []) if item.get("actor_id") == actor_id_value),
        None,
    )
    if combatant is None:
        raise CombatEngineError("actor is not a combatant")
    out_of_turn_reaction = action == "cast" and payment == "reaction"
    if not out_of_turn_reaction and (current is None or current.get("actor_id") != actor_id_value):
        raise CombatEngineError("it is not this actor's turn")
    initial_budget = dict(combatant.get("turn_budget") or {})
    initial_forbidden_extra_actions = set(
        dict(combatant.get("turn_flags") or {}).get(
            "extra_action_forbidden_actions", []
        )
    )
    if (
        action in initial_forbidden_extra_actions
        and (
            payment == "extra_action"
            or (
                payment is None
                and int(initial_budget.get("main_action", 0) or 0) <= 0
                and int(initial_budget.get("extra_action", 0) or 0) > 0
            )
        )
    ):
        raise CombatEngineError(
            f"the extra action cannot be used to {action} under its source rule"
        )
    available_action = action
    if action == "cast" and payment in {"bonus_action", "reaction"}:
        available_action = payment
    if not out_of_turn_reaction and available_action not in available_actions(
        value, actor_id_value
    ):
        raise CombatEngineError("actor has no legal action payment available")
    acting = combatant if out_of_turn_reaction else current
    assert acting is not None
    if (
        out_of_turn_reaction
        and _condition_set(acting.get("conditions")) & INCAPACITATING_STATE_IDS
    ):
        raise CombatEngineError("actor cannot take a reaction under its current conditions")
    budget = dict(acting.get("turn_budget") or {})
    payment = payment or (
        "object_interaction"
        if action == "interact_object"
        else "extra_action"
        if budget.get("extra_action", 0) > 0
        else "main_action"
    )
    if payment not in {
        "main_action",
        "extra_action",
        "bonus_action",
        "reaction",
        "object_interaction",
    }:
        raise CombatEngineError("invalid action payment")
    forbidden_extra_actions = set(
        dict(acting.get("turn_flags") or {}).get(
            "extra_action_forbidden_actions", []
        )
    )
    if payment == "extra_action" and action in forbidden_extra_actions:
        raise CombatEngineError(
            f"the extra action cannot be used to {action} under its source rule"
        )
    if int(budget.get(payment, 0) or 0) <= 0:
        raise CombatEngineError("actor has no action payment available")
    budget[payment] = int(budget[payment]) - 1
    acting["turn_budget"] = budget
    _record_action_payment(
        value,
        acting,
        action=action,
        payment=payment,
    )
    flags = dict(acting.get("turn_flags") or {})
    if "turned" in _condition_set(acting.get("conditions")):
        if action not in {"dash", "dodge", "escape"}:
            raise CombatEngineError("a turned creature can use its action only to Dash or escape")
        if action == "dodge" and dict(payload or {}).get("nowhere_to_move") is not True:
            raise CombatEngineError(
                "a turned creature can Dodge only after the DM confirms nowhere to move"
            )
        if action == "escape" and not _condition_set(acting.get("conditions")) & {
            "grappled",
            "restrained",
        }:
            raise CombatEngineError(
                "a turned creature can try to escape only from an effect preventing movement"
            )
    if action == "cast":
        flags["cast_declared"] = deepcopy(payload or {})
    elif action == "dash":
        budget["movement"] = int(budget.get("movement", 0) or 0) + int(budget.get("speed", 0) or 0)
    elif action == "disengage":
        flags["disengaged"] = True
    elif action == "dodge":
        flags["dodging"] = True
    elif action == "help":
        if not target_id:
            raise CombatEngineError("help requires a target actor")
        flags["helping"] = {"target_id": target_id, "payload": deepcopy(payload or {})}
    elif action == "stabilize":
        if not target_id:
            raise CombatEngineError("stabilize requires a target actor")
        flags["stabilizing"] = {
            "target_id": target_id,
            "payload": deepcopy(payload or {}),
        }
    elif action == "escape":
        flags["escape_declared"] = deepcopy(payload or {})
    elif action == "interact_object":
        interaction_payload = deepcopy(payload or {})
        object_description = " ".join(
            str(interaction_payload.get("object_description") or "").split()
        )
        interaction = " ".join(
            str(interaction_payload.get("interaction") or "").split()
        )
        if not object_description or not interaction:
            raise CombatEngineError(
                "interact_object requires an object_description and interaction"
            )
        flags["object_interaction_declared"] = interaction_payload
    elif action in {
        "hide",
        "search",
        "shake_hypnotic_pattern",
        "influence",
        "improvise",
        "study",
        "utilize",
        "use_object",
    }:
        flags[f"{action}_declared"] = deepcopy(payload or {})
    elif action == "ready":
        if not trigger:
            raise CombatEngineError("ready requires an explicit trigger")
        ready_payload = deepcopy(payload or {})
        if ready_payload.get("spell_id") or ready_payload.get("kind") == "spell":
            raise CombatEngineError(
                "readying a spell is not supported by the generic Ready action; "
                "it requires spell-slot and concentration settlement"
            )
        value["readied"] = [
            *list(value.get("readied") or []),
            {
                "id": f"ready-{uuid4().hex}",
                "actor_id": actor_id_value,
                "trigger": trigger,
                "payload": ready_payload,
                "status": "armed",
            },
        ]
    acting["turn_flags"] = flags
    value["log"] = [
        *list(value.get("log") or []),
        {
            "type": "common_action",
            "action": action,
            "actor_id": actor_id_value,
            "target_id": target_id,
            "payload": deepcopy(payload or {}),
            "round": int(value.get("round", 1) or 1),
            "turn_index": int(value.get("turn_index", 0) or 0),
        },
    ][-100:]
    return value


def arm_readied_spell(
    encounter: dict[str, Any],
    *,
    actor_id_value: str,
    spell_id: str,
    trigger: str,
    holding_effect_id: str,
    release_concentration: bool,
    release_duration: dict[str, Any],
    release_effect_kind: str,
    declaration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Record a paid, concentrated spell until its trigger or the next turn."""
    value = deepcopy(encounter)
    if not str(trigger).strip():
        raise CombatEngineError("readying a spell requires an explicit perceivable trigger")
    if any(
        item.get("actor_id") == actor_id_value and item.get("status") in {"armed", "triggered"}
        for item in value.get("readied", [])
    ):
        raise CombatEngineError("actor already has a readied action")
    value["readied"] = [
        *list(value.get("readied") or []),
        {
            "id": f"ready-spell-{uuid4().hex}",
            "kind": "spell",
            "actor_id": actor_id_value,
            "spell_id": spell_id,
            "trigger": str(trigger).strip(),
            "holding_effect_id": holding_effect_id,
            "release_concentration": bool(release_concentration),
            "release_duration": deepcopy(release_duration),
            "release_effect_kind": release_effect_kind,
            "declaration": deepcopy(declaration or {}),
            "status": "armed",
        },
    ]
    return value


def trigger_readied_spell(
    encounter: dict[str, Any], *, readied_id: str, event: str
) -> dict[str, Any]:
    """Open the owned reaction window after the DM confirms the trigger occurred."""
    value = deepcopy(encounter)
    event_text = str(event).strip()
    if not event_text:
        raise CombatEngineError("triggering a readied spell requires the observed event")
    readied = next(
        (item for item in value.get("readied", []) if item.get("id") == readied_id), None
    )
    if readied is None or readied.get("kind") != "spell" or readied.get("status") != "armed":
        raise CombatEngineError("readied spell is not armed")
    if any(item.get("status", "pending") == "pending" for item in value.get("pending", [])):
        raise CombatEngineError("resolve the pending save or choice before another trigger")
    readied["status"] = "triggered"
    window = {
        "id": f"reaction-{uuid4().hex}",
        "kind": "reaction",
        "actor_id": readied["actor_id"],
        "event": event_text,
        "trigger": "readied_spell",
        "readied_id": readied_id,
        "candidates": [{"id": "release"}, {"id": "decline"}],
        "deadline": "immediate_after_trigger",
        "status": "pending",
    }
    value["pending"] = [*list(value.get("pending") or []), window]
    return value


def trigger_readied_action(
    encounter: dict[str, Any], *, readied_id: str, event: str
) -> dict[str, Any]:
    """Open the reaction choice after Agent-as-DM confirms the trigger."""
    value = deepcopy(encounter)
    event_text = str(event).strip()
    readied = next(
        (item for item in value.get("readied", []) if item.get("id") == readied_id), None
    )
    if (
        not event_text
        or readied is None
        or readied.get("kind") == "spell"
        or readied.get("status") != "armed"
    ):
        raise CombatEngineError("readied non-spell action is not armed or has no observed trigger")
    if any(item.get("status", "pending") == "pending" for item in value.get("pending", [])):
        raise CombatEngineError("resolve the pending save or choice before another trigger")
    readied["status"] = "triggered"
    value["pending"] = [
        *list(value.get("pending") or []),
        {
            "id": f"reaction-{uuid4().hex}",
            "kind": "reaction",
            "actor_id": readied["actor_id"],
            "event": event_text,
            "trigger": "readied_action",
            "readied_id": readied_id,
            "candidates": [{"id": "release"}, {"id": "decline"}],
            "deadline": "immediate_after_trigger",
            "status": "pending",
        },
    ]
    return value


def resolve_readied_action_window(
    encounter: dict[str, Any], *, actor_id_value: str, choice_id: str, release: bool
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Spend a reaction; the generic effect returns to Agent-as-DM adjudication."""
    value = deepcopy(encounter)
    window = next((item for item in value.get("pending", []) if item.get("id") == choice_id), None)
    if (
        not isinstance(window, dict)
        or window.get("trigger") != "readied_action"
        or window.get("actor_id") != actor_id_value
    ):
        raise CombatEngineError("choice_id is not this actor's readied-action window")
    readied = next(
        (item for item in value.get("readied", []) if item.get("id") == window.get("readied_id")),
        None,
    )
    if readied is None or readied.get("status") != "triggered":
        raise CombatEngineError("readied action is no longer available")
    value = resolve_choice_window(
        value,
        choice_id=choice_id,
        actor_id_value=actor_id_value,
        selection={"id": "release" if release else "decline"},
    )
    if release:
        combatant = next(
            item for item in value.get("combatants", []) if item.get("actor_id") == actor_id_value
        )
        budget = dict(combatant.get("turn_budget") or {})
        if int(budget.get("reaction", 0) or 0) <= 0:
            raise CombatEngineError("actor has no reaction remaining")
        budget["reaction"] = int(budget["reaction"]) - 1
        combatant["turn_budget"] = budget
        value["readied"] = [
            item for item in value.get("readied", []) if item.get("id") != readied["id"]
        ]
    else:
        readied["status"] = "armed"
    return value, deepcopy(readied)


def resolve_readied_spell_window(
    encounter: dict[str, Any],
    *,
    actor_id_value: str,
    choice_id: str,
    release: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Release held energy with a reaction or ignore this occurrence of the trigger."""
    value = deepcopy(encounter)
    window = next((item for item in value.get("pending", []) if item.get("id") == choice_id), None)
    if (
        window is None
        or window.get("kind") != "reaction"
        or window.get("trigger") != "readied_spell"
        or window.get("actor_id") != actor_id_value
    ):
        raise CombatEngineError("choice_id is not this actor's readied-spell window")
    readied = next(
        (item for item in value.get("readied", []) if item.get("id") == window.get("readied_id")),
        None,
    )
    if readied is None or readied.get("status") != "triggered":
        raise CombatEngineError("readied spell is no longer available")
    value = resolve_choice_window(
        value,
        choice_id=choice_id,
        actor_id_value=actor_id_value,
        selection={"id": "release" if release else "decline"},
    )
    readied = next(item for item in value["readied"] if item.get("id") == readied["id"])
    if release:
        combatant = next(
            item for item in value.get("combatants", []) if item.get("actor_id") == actor_id_value
        )
        budget = dict(combatant.get("turn_budget") or {})
        if int(budget.get("reaction", 0) or 0) <= 0:
            raise CombatEngineError("actor has no reaction remaining")
        budget["reaction"] = int(budget["reaction"]) - 1
        combatant["turn_budget"] = budget
        value["readied"] = [
            item for item in value.get("readied", []) if item.get("id") != readied["id"]
        ]
    else:
        readied["status"] = "armed"
    return value, deepcopy(readied)


def pay_activity_activation(
    encounter: dict[str, Any],
    *,
    actor_id_value: str,
    activation_type: str,
    action_kind: str | None = None,
) -> dict[str, Any]:
    """Pay only the action-economy portion of a structured activity card.

    Effects, targets, and choices intentionally remain outside this helper: a
    content card describes resources and timing, not a universally safe way to
    infer narrative resolution.
    """
    value = deepcopy(encounter)
    activation = str(activation_type).strip().lower()
    if activation not in {"action", "bonus_action", "reaction", "special"}:
        raise CombatEngineError("activity activation type is not usable in combat")
    combatant = next(
        (item for item in value.get("combatants", []) if item.get("actor_id") == actor_id_value),
        None,
    )
    if combatant is None:
        raise CombatEngineError("actor is not a combatant")
    if _condition_set(combatant.get("conditions")) & {
        "dead",
        "unconscious",
        "stunned",
        "incapacitated",
        "paralyzed",
        "petrified",
        "turned",
    }:
        raise CombatEngineError("actor cannot activate content under its current conditions")
    if activation in {"action", "bonus_action"}:
        current = current_combatant(value)
        if current is None or current.get("actor_id") != actor_id_value:
            raise CombatEngineError("it is not this actor's turn")
    budget = dict(combatant.get("turn_budget") or {})
    if activation == "action":
        normalized_action_kind = str(action_kind or "activity").strip().casefold()
        forbidden = set(
            dict(combatant.get("turn_flags") or {}).get(
                "extra_action_forbidden_actions", []
            )
        )
        extra_is_forbidden = (
            normalized_action_kind in forbidden
            or (normalized_action_kind == "magic" and "cast" in forbidden)
        )
        if extra_is_forbidden and int(budget.get("main_action", 0) or 0) > 0:
            payment = "main_action"
        else:
            payment = (
                "extra_action"
                if int(budget.get("extra_action", 0) or 0) > 0
                else "main_action"
            )
        if payment == "extra_action" and extra_is_forbidden:
            raise CombatEngineError(
                "the extra action cannot be used for this activity under its source rule"
            )
    elif activation in {"bonus_action", "reaction"}:
        payment = activation
    else:
        payment = None
    if payment is not None:
        if int(budget.get(payment, 0) or 0) <= 0:
            raise CombatEngineError(f"actor has no {activation} remaining")
        budget[payment] = int(budget[payment]) - 1
        combatant["turn_budget"] = budget
        _record_action_payment(
            value,
            combatant,
            action=f"activity:{str(action_kind or activation).strip().casefold()}",
            payment=payment,
        )
    value["log"] = [
        *list(value.get("log") or []),
        {"type": "activity_activation", "actor_id": actor_id_value, "activation": activation},
    ][-100:]
    return value


def pay_legendary_action(
    encounter: dict[str, Any],
    *,
    actor_id_value: str,
    activity_id: str,
    spec: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Pay one standard 2014 legendary action at another creature's turn end."""

    value = deepcopy(encounter)
    if spec.get("kind") != "legendary_action_2014":
        raise CombatEngineError("unsupported legendary-action contract")
    pool = dict(spec.get("pool") or {})
    if (
        pool.get("kind") != "legendary_action_pool_2014"
        or pool.get("trigger") != "end_of_another_creature_turn"
        or pool.get("recovers_on") != "source_turn_start"
        or pool.get("one_option_per_trigger") is not True
    ):
        raise CombatEngineError("legendary-action pool contract is malformed")
    maximum = int(pool.get("maximum", 0) or 0)
    cost = int(spec.get("cost", 0) or 0)
    if maximum < 1 or not 1 <= cost <= maximum:
        raise CombatEngineError("legendary-action cost or pool is invalid")
    current = current_combatant(value)
    if current is None:
        raise CombatEngineError("combat has no current actor")
    if str(current.get("actor_id") or "") == actor_id_value:
        raise CombatEngineError(
            "a legendary action can be used only at the end of another creature's turn"
        )
    current_budget = dict(current.get("turn_budget") or {})
    for key in (
        "main_action",
        "extra_action",
        "bonus_action",
        "object_interaction",
        "movement",
        "attack_budget",
    ):
        if key in current_budget:
            current_budget[key] = 0
    current["turn_budget"] = current_budget
    current_flags = dict(current.get("turn_flags") or {})
    current_flags["turn_end_committed"] = True
    current["turn_flags"] = current_flags
    combatant = next(
        (
            item
            for item in value.get("combatants", [])
            if str(item.get("actor_id") or "") == actor_id_value
        ),
        None,
    )
    if combatant is None:
        raise CombatEngineError("legendary-action actor is not a combatant")
    if _condition_set(combatant.get("conditions")) & INCAPACITATING_STATE_IDS:
        raise CombatEngineError(
            "an incapacitated creature cannot take legendary actions"
        )
    if (
        _normalize_ruleset(value.get("ruleset")) == "2014"
        and bool(combatant.get("surprised"))
        and int(combatant.get("turns_completed", 0) or 0) == 0
    ):
        raise CombatEngineError(
            "a surprised creature cannot take legendary actions until after its first turn"
        )
    turn_token = _combat_turn_token(value)
    state = dict(combatant.get("legendary_actions") or {})
    if state and int(state.get("maximum", 0) or 0) != maximum:
        raise CombatEngineError("legendary-action pool conflicts with the source card")
    if state.get("last_used_turn_token") == turn_token:
        raise CombatEngineError(
            "only one legendary action option can be used after this creature's turn"
        )
    flags = dict(combatant.get("turn_flags") or {})
    if "legendary_weapon_attack" in flags:
        raise CombatEngineError(
            "the previous legendary action must be completed before another is used"
        )
    remaining = int(state.get("remaining", maximum) or 0)
    if remaining < cost:
        raise CombatEngineError("not enough legendary actions remain")
    state.update(
        maximum=maximum,
        remaining=remaining - cost,
        last_used_turn_token=turn_token,
        last_activity_id=str(activity_id),
    )
    combatant["legendary_actions"] = state
    effect = dict(spec.get("effect") or {})
    if effect.get("kind") == "weapon_attack":
        flags["legendary_weapon_attack"] = {
            "activity_id": str(activity_id),
            "weapon_id": str(effect.get("weapon_id") or ""),
            "attack_mode": str(effect.get("attack_mode") or ""),
            "turn_token": turn_token,
        }
        combatant["turn_flags"] = flags
    payment = {
        "kind": "legendary_action",
        "activity_id": str(activity_id),
        "cost": cost,
        "remaining": state["remaining"],
        "maximum": maximum,
        "turn_token": turn_token,
    }
    value["log"] = [
        *list(value.get("log") or []),
        {
            "type": "legendary_action_payment",
            "actor_id": actor_id_value,
            **payment,
        },
    ][-100:]
    return value, payment


def settle_core_activity_effect(
    encounter: dict[str, Any],
    *,
    actor_id_value: str,
    activity_id: str,
    declaration: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Settle narrow engine-owned effects for canonical Core activity cards."""
    value = deepcopy(encounter)
    action_surge_ids = {
        "dnd5e.content.srd2014.feature.fighter-action-surge",
        "dnd5e.content.srd2024.feature.fighter-action-surge",
    }
    cunning_action_ids = {
        "dnd5e.content.srd2014.feature.rogue-cunning-action",
        "dnd5e.content.srd2024.feature.rogue-cunning-action",
    }
    if activity_id not in {
        *action_surge_ids,
        *cunning_action_ids,
    }:
        return value, None
    current = current_combatant(value)
    if current is None or current.get("actor_id") != actor_id_value:
        raise CombatEngineError("this Core activity can be used only on the actor's turn")
    combatant = next(
        item for item in value.get("combatants", []) if item.get("actor_id") == actor_id_value
    )
    if activity_id in cunning_action_ids:
        selected = str(dict(declaration or {}).get("action") or "")
        selected = selected.strip().lower().replace("-", "_").replace(" ", "_")
        if selected not in {"dash", "disengage", "hide"}:
            raise CombatEngineError(
                "Cunning Action declaration.action must be dash, disengage, or hide"
            )
        budget = dict(combatant.get("turn_budget") or {})
        flags = dict(combatant.get("turn_flags") or {})
        if selected == "dash":
            budget["movement"] = int(budget.get("movement", 0) or 0) + int(
                budget.get("speed", 0) or 0
            )
            combatant["turn_budget"] = budget
        elif selected == "disengage":
            flags["disengaged"] = True
            combatant["turn_flags"] = flags
        else:
            flags["hide_declared"] = {
                "source_activity_id": activity_id,
                "declaration": deepcopy(declaration or {}),
            }
            combatant["turn_flags"] = flags
        effect = {
            "kind": "cunning_action",
            "action": selected,
            "requires_ruling": selected == "hide",
        }
        if selected == "hide":
            effect["ruling_requirement"] = {
                "default_resolver": "agent",
                "ruling_kind": "source_or_scene_fact",
                "reason": (
                    "Determine from the current cover, visibility, and observer facts "
                    "whether hiding is possible and resolve the Stealth boundary."
                ),
            }
        value["log"] = [
            *list(value.get("log") or []),
            {"type": "cunning_action", "actor_id": actor_id_value, "effect": effect},
        ][-100:]
        return value, effect
    flags = dict(combatant.get("turn_flags") or {})
    if flags.get("action_surge_used"):
        raise CombatEngineError("Action Surge can be used only once on the same turn")
    budget = dict(combatant.get("turn_budget") or {})
    budget["extra_action"] = int(budget.get("extra_action", 0) or 0) + 1
    combatant["turn_budget"] = budget
    flags["action_surge_used"] = True
    if activity_id.endswith("srd2024.feature.fighter-action-surge"):
        flags["extra_action_forbidden_actions"] = ["cast"]
    combatant["turn_flags"] = flags
    effect = {
        "kind": "action_surge",
        "extra_actions_granted": 1,
        "extra_actions_available": budget["extra_action"],
    }
    value["log"] = [
        *list(value.get("log") or []),
        {"type": "action_surge", "actor_id": actor_id_value, "effect": effect},
    ][-100:]
    return value, effect


def resolve_second_wind_to_sheet(sheet: dict[str, Any], *, rng: Any = None) -> dict[str, Any]:
    """Roll and apply the 2014/2024 Fighter's canonical Second Wind healing."""
    value = deepcopy(sheet)
    fighter_level = sum(
        int(item.get("level", 0) or 0)
        for item in value.get("progression", {}).get("classes", [])
        if str(item.get("name") or "").strip().casefold() == "fighter"
    )
    if fighter_level <= 0:
        raise CombatEngineError("Second Wind requires a recorded Fighter class level")
    rolled = asdict(roll("1d10", rng=rng))
    amount = int(rolled["total"]) + fighter_level
    healed = apply_healing_to_sheet(value, amount=amount)
    return {
        "sheet": healed["sheet"],
        "kind": "second_wind",
        "fighter_level": fighter_level,
        "roll": rolled,
        "healing_amount": amount,
        "before_hp": healed["before_hp"],
        "after_hp": healed["after_hp"],
        "applied_amount": healed["amount"],
    }


def available_reactions(encounter: dict[str, Any], actor_id_value: str) -> list[dict[str, Any]]:
    """Return reaction windows owned by an actor, even outside its own turn."""
    combatant = next(
        (
            item
            for item in encounter.get("combatants", [])
            if item.get("actor_id") == actor_id_value
        ),
        None,
    )
    if combatant is None:
        raise CombatEngineError(f"combatant not found: {actor_id_value}")
    if int(dict(combatant.get("turn_budget") or {}).get("reaction", 0) or 0) <= 0:
        return []
    if _condition_set(combatant.get("conditions")) & INCAPACITATING_STATE_IDS:
        return []
    return [
        deepcopy(item)
        for item in encounter.get("pending", [])
        if item.get("kind") == "reaction"
        and item.get("actor_id") == actor_id_value
        and item.get("status", "pending") == "pending"
    ]


def add_choice_window(
    encounter: dict[str, Any],
    *,
    kind: str,
    actor_id_value: str,
    event: str,
    candidates: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Persist a DM/actor choice without resolving a narrative fact implicitly."""
    value = deepcopy(encounter)
    window = ChoiceWindow(
        id=f"choice-{uuid4().hex}",
        kind=kind,
        actor_id=actor_id_value,
        event=event,
        candidates=tuple(deepcopy(item) for item in candidates),
    )
    value["pending"] = [*list(value.get("pending") or []), asdict(window)]
    return value


def resolve_choice_window(
    encounter: dict[str, Any],
    *,
    choice_id: str,
    actor_id_value: str,
    selection: dict[str, Any],
) -> dict[str, Any]:
    """Resolve one pending choice and append its auditable selection."""
    value = deepcopy(encounter)
    pending = list(value.get("pending") or [])
    window = next((item for item in pending if item.get("id") == choice_id), None)
    if window is None:
        raise CombatEngineError("choice window not found")
    if window.get("actor_id") != actor_id_value:
        raise CombatEngineError("actor cannot resolve this choice window")
    candidates = list(window.get("candidates") or [])
    if (
        candidates
        and selection not in candidates
        and selection.get("id") not in {item.get("id") for item in candidates}
    ):
        raise CombatEngineError("selection is not one of the choice candidates")
    value["pending"] = [item for item in pending if item.get("id") != choice_id]
    value["log"] = [
        *list(value.get("log") or []),
        {
            "type": "choice",
            "choice_id": choice_id,
            "actor_id": actor_id_value,
            "selection": deepcopy(selection),
        },
    ][-100:]
    return value


def apply_healing_to_sheet(
    sheet: dict[str, Any],
    *,
    amount: int,
    source_sheet: dict[str, Any] | None = None,
    spell_id: str | None = None,
    spell_level: int | None = None,
) -> dict[str, Any]:
    """Apply healing and settle source-linked spell modifiers before HP clamping."""
    value = deepcopy(sheet)
    hp = dict(value.setdefault("combat", {}).setdefault("hp", {"value": 0, "max": 0, "temp": 0}))
    before = int(hp.get("value", 0) or 0)
    if "dead" in _condition_set(value.get("conditions")):
        raise CombatEngineError("ordinary healing cannot restore a dead actor")
    requested_amount = int(amount)
    source_supplied = source_sheet is not None or spell_id is not None or spell_level is not None
    if requested_amount < 0 or (requested_amount == 0 and not source_supplied):
        raise CombatEngineError("healing amount must be positive unless a spell rolled zero")
    bonus = 0
    source: dict[str, Any] | None = None
    if source_supplied:
        if source_sheet is None or not spell_id or spell_level is None:
            raise CombatEngineError(
                "spell healing requires source_sheet, spell_id, and spell_level"
            )
        spell = next(
            (
                item
                for item in source_sheet.get("content", {}).get("spells", [])
                if str(item.get("id") or "") == str(spell_id)
            ),
            None,
        )
        if spell is None:
            raise CombatEngineError("healing spell is not recorded on the source actor card")
        base_level = int(spell.get("level", 0) or 0)
        cast_level = int(spell_level)
        if base_level < 1 or cast_level < base_level:
            raise CombatEngineError(
                "spell healing requires a level 1+ spell and a legal cast level"
            )
        disciple = next(
            (
                item
                for item in source_sheet.get("content", {}).get("features", [])
                if item.get("id") == "dnd5e.content.srd2014.feature.life-domain-disciple-of-life"
                or (
                    str(item.get("name") or "").casefold() == "disciple of life"
                    and str(item.get("source_key") or "").casefold() == "life domain"
                )
            ),
            None,
        )
        if disciple is not None:
            bonus = 2 + cast_level
        source = {
            "kind": "spell",
            "spell_id": str(spell_id),
            "spell_name": str(spell.get("name") or spell_id),
            "spell_level": cast_level,
            "modifiers": (
                [
                    {
                        "feature_id": str(disciple.get("id") or "disciple-of-life"),
                        "name": "Disciple of Life",
                        "amount": bonus,
                    }
                ]
                if disciple is not None
                else []
            ),
        }
    effective_amount = max(0, requested_amount + bonus)
    try:
        basic = apply_basic_healing_to_sheet(value, amount=effective_amount)
    except ValueError as error:
        raise CombatEngineError(str(error)) from error
    value = basic["sheet"]
    hp = value["combat"]["hp"]
    return {
        "sheet": value,
        "before_hp": before,
        "after_hp": hp["value"],
        "amount": basic["amount"],
        "requested_amount": requested_amount,
        "bonus_amount": bonus,
        "effective_amount": effective_amount,
        "source": source,
    }


def resolve_preserve_life_to_sheets(
    source_sheet: dict[str, Any],
    target_sheets: dict[str, dict[str, Any]],
    *,
    allocations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Settle the edition-specific Life Domain Channel Divinity allocation."""
    edition = _normalize_ruleset(source_sheet.get("edition"))
    feature = next(
        (
            item
            for item in source_sheet.get("content", {}).get("features", [])
            if str(item.get("id") or "").endswith(
                "life-domain-channel-divinity-preserve-life"
            )
            or str(item.get("id") or "").endswith("life-domain-preserve-life")
            or str(item.get("name") or "").casefold()
            in {"channel divinity: preserve life", "preserve life"}
            or "dnd5e.core.activity.preserve_life"
            in {str(ref) for ref in item.get("mechanic_refs", [])}
        ),
        None,
    )
    if feature is None:
        raise CombatEngineError("source actor does not have Preserve Life")
    cleric_level = next(
        (
            int(item.get("level", 0) or 0)
            for item in source_sheet.get("progression", {}).get("classes", [])
            if str(item.get("name") or "").casefold() == "cleric"
        ),
        0,
    )
    minimum_level = 3 if edition == "2024" else 2
    if cleric_level < minimum_level:
        raise CombatEngineError(
            f"Preserve Life requires at least {minimum_level} Cleric levels"
        )
    pool = cleric_level * 5
    if not isinstance(allocations, list) or not allocations:
        raise CombatEngineError("Preserve Life requires at least one healing allocation")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    total = 0
    for allocation in allocations:
        if not isinstance(allocation, dict):
            raise CombatEngineError("each Preserve Life allocation must be an object")
        target_id = str(allocation.get("target_id") or "").strip()
        amount = allocation.get("amount")
        if not target_id or target_id in seen:
            raise CombatEngineError("Preserve Life target ids must be present and unique")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount < 1:
            raise CombatEngineError("Preserve Life amounts must be positive integers")
        target = target_sheets.get(target_id)
        if target is None:
            raise CombatEngineError(f"Preserve Life target sheet is missing: {target_id}")
        creature_type = str(
            target.get("progression", {}).get("species") or ""
        ).casefold()
        if edition == "2014" and (
            "undead" in creature_type or "construct" in creature_type
        ):
            raise CombatEngineError("Preserve Life has no effect on Undead or Constructs")
        hp = dict(target.get("combat", {}).get("hp") or {})
        current = int(hp.get("value", 0) or 0)
        maximum = int(hp.get("max", 0) or 0)
        capacity = maximum // 2 - current
        if capacity < 1 or amount > capacity:
            raise CombatEngineError(
                f"Preserve Life allocation would raise {target_id} above half maximum HP"
            )
        seen.add(target_id)
        total += amount
        normalized.append({"target_id": target_id, "amount": amount})
    if total > pool:
        raise CombatEngineError("Preserve Life allocations exceed five times Cleric level")
    updated = {target_id: deepcopy(sheet) for target_id, sheet in target_sheets.items()}
    results: list[dict[str, Any]] = []
    for allocation in normalized:
        target_id = allocation["target_id"]
        healed = apply_healing_to_sheet(updated[target_id], amount=allocation["amount"])
        updated[target_id] = healed["sheet"]
        results.append(
            {
                "target_id": target_id,
                "before_hp": healed["before_hp"],
                "after_hp": healed["after_hp"],
                "amount": healed["amount"],
            }
        )
    return {
        "sheets": updated,
        "edition": edition,
        "pool": pool,
        "allocated": total,
        "remaining_unallocated": pool - total,
        "targets": results,
    }


def resolve_divine_spark_to_sheet(
    source_actor: dict[str, Any],
    target_actor: dict[str, Any],
    *,
    mode: str,
    damage_type: str | None = None,
    rules: ResolutionContext | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    """Resolve the source-bound 2024 Cleric Divine Spark option."""
    source_sheet = actor_sheet(source_actor)
    if _normalize_ruleset(source_sheet.get("edition")) != "2024":
        raise CombatEngineError("Divine Spark is available only under the 2024 rules")
    feature = next(
        (
            item
            for item in source_sheet.get("content", {}).get("features", [])
            if (
                item.get("id")
                == "dnd5e.content.srd2024.feature.cleric-channel-divinity"
                or "dnd5e.core.activity.divine_spark"
                in {str(ref) for ref in item.get("mechanic_refs", [])}
            )
            and "divine spark"
            in {
                str(option).strip().casefold()
                for option in dict(item.get("choices") or {}).get("options", [])
            }
        ),
        None,
    )
    if feature is None:
        raise CombatEngineError("source actor does not have source-bound Divine Spark")
    cleric_level = sum(
        int(item.get("level", 0) or 0)
        for item in source_sheet.get("progression", {}).get("classes", [])
        if str(item.get("name") or "").casefold() == "cleric"
    )
    if cleric_level < 2:
        raise CombatEngineError("Divine Spark requires at least two Cleric levels")
    if actor_id(source_actor) == actor_id(target_actor):
        raise CombatEngineError("Divine Spark targets another creature")
    if "dead" in condition_ids(actor_sheet(target_actor).get("conditions")):
        raise CombatEngineError("Divine Spark cannot target a dead creature")
    normalized_mode = str(mode or "").strip().casefold()
    if normalized_mode not in {"heal", "damage"}:
        raise CombatEngineError("Divine Spark mode must be heal or damage")
    normalized_damage_type = str(damage_type or "").strip().casefold()
    if normalized_mode == "heal" and normalized_damage_type:
        raise CombatEngineError("healing Divine Spark does not accept a damage type")
    if normalized_mode == "damage" and normalized_damage_type not in {
        "necrotic",
        "radiant",
    }:
        raise CombatEngineError(
            "damaging Divine Spark requires necrotic or radiant damage"
        )
    save_dc = 0
    if normalized_mode == "damage":
        save_dc = int(
            dict(actor_derived(source_actor).get("spellcasting") or {}).get(
                "save_dc", 0
            )
            or 0
        )
        if save_dc < 1:
            raise CombatEngineError("Divine Spark requires the Cleric spell save DC")
    wisdom_score = effective_ability_scores(source_sheet)["wisdom"]
    wisdom_modifier = ability_modifier(wisdom_score)
    dice = 1 + sum(cleric_level >= threshold for threshold in (7, 13, 18))
    spark_roll = roll(f"{dice}d8", rng=rng)
    total = max(0, spark_roll.total + wisdom_modifier)
    base = {
        "kind": "divine_spark",
        "mode": normalized_mode,
        "target_id": actor_id(target_actor),
        "expression": f"{dice}d8 {'+' if wisdom_modifier >= 0 else '-'} "
        f"{abs(wisdom_modifier)}",
        "rolls": list(spark_roll.rolls),
        "rolled_total": spark_roll.total,
        "wisdom_modifier": wisdom_modifier,
        "total": total,
    }
    if normalized_mode == "heal":
        healed = apply_healing_to_sheet(actor_sheet(target_actor), amount=total)
        return {
            **base,
            "sheet": healed["sheet"],
            "healing": {key: value for key, value in healed.items() if key != "sheet"},
            "save": None,
            "damage": None,
            "damage_type": None,
        }
    saved = resolve_actor_check(
        target_actor,
        kind="save",
        ability="constitution",
        dc=save_dc,
        save_source_kind="magical_effect",
        rules=context_with_facts(
            rules,
            save_source_kind="magical_effect",
            save_effect_conditions=[],
        ),
        rng=rng,
    )
    amount = total // 2 if saved["success"] else total
    damaged = apply_damage_to_sheet(
        actor_sheet(target_actor),
        amount=amount,
        damage_type=normalized_damage_type,
        source="Divine Spark",
        ruleset="2024",
        death_saves=bool(
            target_actor.get("death_saves", False)
            or target_actor.get("zero_hp_recovery", False)
        ),
    )
    return {
        **base,
        "sheet": damaged["sheet"],
        "healing": None,
        "save": saved,
        "damage": {key: value for key, value in damaged.items() if key != "sheet"},
        "damage_type": normalized_damage_type,
    }


def resolve_turn_undead_to_sheets(
    source_actor: dict[str, Any],
    target_actors: dict[str, dict[str, Any]],
    *,
    rules: ResolutionContext | None = None,
    rng: Any = None,
    sear_undead: bool = False,
) -> dict[str, Any]:
    """Resolve Turn Undead using the exact 2014 or 2024 condition contract."""
    source_sheet = actor_sheet(source_actor)
    edition = _normalize_ruleset(source_sheet.get("edition"))
    feature = next(
        (
            item
            for item in source_sheet.get("content", {}).get("features", [])
            if (
                item.get("id")
                in {
                    "dnd5e.content.srd2014.feature.cleric-channel-divinity",
                    "dnd5e.content.srd2024.feature.cleric-channel-divinity",
                }
                or "dnd5e.core.activity.turn_undead"
                in {str(ref) for ref in item.get("mechanic_refs", [])}
            )
            and "turn undead"
            in {
                str(option).strip().casefold()
                for option in dict(item.get("choices") or {}).get("options", [])
            }
        ),
        None,
    )
    if feature is None:
        raise CombatEngineError("source actor does not have source-bound Turn Undead")
    cleric_level = sum(
        int(item.get("level", 0) or 0)
        for item in source_sheet.get("progression", {}).get("classes", [])
        if str(item.get("name") or "").casefold() == "cleric"
    )
    if cleric_level < 2:
        raise CombatEngineError("Turn Undead requires at least two Cleric levels")
    save_dc = int(
        dict(actor_derived(source_actor).get("spellcasting") or {}).get("save_dc", 0) or 0
    )
    if save_dc < 1:
        raise CombatEngineError("Turn Undead requires the cleric's canonical spell save DC")
    if not target_actors:
        raise CombatEngineError("Turn Undead requires at least one perceiving undead target")
    sear_feature = next(
        (
            item
            for item in source_sheet.get("content", {}).get("features", [])
            if str(item.get("name") or "").strip().casefold() == "sear undead"
            or "dnd5e.core.activity.sear_undead"
            in {str(ref) for ref in item.get("mechanic_refs", [])}
        ),
        None,
    )
    if sear_undead and (
        edition != "2024" or cleric_level < 5 or sear_feature is None
    ):
        raise CombatEngineError(
            "Sear Undead requires its source-bound 2024 level 5 Cleric feature"
        )
    sear_roll = None
    if sear_undead:
        wisdom_score = effective_ability_scores(source_sheet)["wisdom"]
        sear_dice = max(1, ability_modifier(wisdom_score))
        sear_roll = roll(f"{sear_dice}d8", rng=rng)

    updated: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for target_id, target_actor in target_actors.items():
        target_sheet = actor_sheet(target_actor)
        creature_type = str(target_sheet.get("progression", {}).get("species") or "").casefold()
        if "undead" not in creature_type:
            raise CombatEngineError(f"Turn Undead target is not Undead: {target_id}")
        effect_conditions = (
            ["frightened", "incapacitated"]
            if edition == "2024"
            else ["turned"]
        )
        save = resolve_actor_check(
            target_actor,
            kind="save",
            ability="wisdom",
            dc=save_dc,
            save_source_kind="magical_effect",
            save_effect_conditions=effect_conditions,
            rules=context_with_facts(
                rules,
                save_source_kind="magical_effect",
                save_effect_conditions=effect_conditions,
            ),
            rng=rng,
        )
        effect_id = None
        sear_damage = None
        if not save["success"]:
            value = deepcopy(target_sheet)
            if sear_roll is not None:
                sear_damage = apply_damage_to_sheet(
                    value,
                    amount=sear_roll.total,
                    damage_type="radiant",
                    source="Sear Undead",
                    ruleset=edition,
                    death_saves=bool(
                        target_actor.get("death_saves", False)
                        or target_actor.get("zero_hp_recovery", False)
                    ),
                )
                value = sear_damage["sheet"]
            replaced: list[dict[str, Any]] = []
            for effect in value.get("effects", []):
                if effect.get("active") and effect.get("kind") == "turn_undead":
                    effect["active"] = False
                    effect["ended_reason"] = "replaced_by_turn_undead"
                    replaced.append(effect)
            if replaced:
                reconcile_ended_effect_conditions(value, ended_effects=replaced)
            target_dead = "dead" in condition_ids(value.get("conditions"))
            effect_id = None if target_dead else f"turn-undead-{uuid4().hex}"
            effect = {
                "id": effect_id,
                "name": "Turn Undead",
                "kind": "turn_undead",
                "source": actor_id(source_actor),
                "active": True,
                "concentration": False,
                "duration": {"period": "minute", "remaining": 1},
                "changes": [
                    {"path": "conditions", "mode": "add", "value": condition}
                    for condition in effect_conditions
                ],
                "description": (
                    (
                        "Frightened and Incapacitated; on its turns it tries to move "
                        "as far from the turning source as it can. Ends on any damage, "
                        "or if the source is Incapacitated or dies."
                    )
                    if edition == "2024"
                    else (
                        "Must move as far from the turning source as possible; cannot "
                        "willingly approach within 30 feet; cannot react; action is Dash, "
                        "escape, or Dodge only if nowhere to move. Ends on any damage."
                    )
                ),
            }
            if not target_dead:
                value.setdefault("effects", []).append(effect)
                apply_effect_conditions(value, effect)
            updated[target_id] = value
        else:
            updated[target_id] = target_sheet
        results.append(
            {
                "target_id": target_id,
                "save": save,
                "save_failed": not save["success"],
                "turned": not save["success"] and effect_id is not None,
                "effect_id": effect_id,
                "conditions": effect_conditions if effect_id is not None else [],
                "sear_damage": (
                    {
                        key: value
                        for key, value in sear_damage.items()
                        if key != "sheet"
                    }
                    if sear_damage is not None
                    else None
                ),
            }
        )
    return {
        "sheets": updated,
        "edition": edition,
        "save_dc": save_dc,
        "duration": {"period": "minute", "remaining": 1},
        "sear_undead": (
            {
                "expression": sear_roll.expression,
                "rolls": list(sear_roll.rolls),
                "total": sear_roll.total,
                "damage_type": "radiant",
                "does_not_end_turn_undead": True,
            }
            if sear_roll is not None
            else None
        ),
        "targets": results,
    }


def resolve_actor_check(
    actor: dict[str, Any],
    *,
    kind: str,
    ability: str,
    dc: int,
    proficient: bool = False,
    bonus: int = 0,
    advantage: bool = False,
    disadvantage: bool = False,
    save_source_kind: str | None = None,
    save_effect_conditions: list[str] | None = None,
    save_purpose: str | None = None,
    ruleset: str | None = None,
    rules: ResolutionContext | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    if kind not in ACTOR_CHECK_KINDS:
        raise CombatEngineError("unsupported check kind")
    sheet = actor_sheet(actor)
    derived = actor_derived(actor)
    normalized_ruleset = _normalize_ruleset(ruleset or sheet.get("edition"))
    conditions = _condition_set(sheet.get("conditions"))
    exhaustion = int(sheet.get("combat", {}).get("exhaustion", 0) or 0)
    effect_roll_bonus = active_effect_roll_bonus(sheet, kind)
    roll_bonus = int(bonus) + effect_roll_bonus
    extension = apply_rule_event(sheet, "check.before", rules)
    if extension.status != "committed":
        raise NeedsRulingError(
            "an active rule pack requires a check choice or ruling",
            missing=[item["mechanic_id"] for item in extension.pending],
            ruling_kind=rule_event_ruling_kind(extension.status, extension.pending),
        )
    for modifier in extension.modifiers:
        if modifier["op"] == "modifier.add" and modifier.get("target") == "check_bonus":
            roll_bonus += int(modifier.get("value", 0) or 0)
        elif modifier["op"] == "advantage.add":
            advantage = True
        elif modifier["op"] == "disadvantage.add":
            disadvantage = True
    normalized_ability = str(ability).strip().casefold().replace(" ", "_")
    level = int(sheet.get("progression", {}).get("level", 1) or 1)
    skill = dict(sheet.get("skills", {}).get(normalized_ability) or {})
    skill_proficiency = str(skill.get("proficiency") or "none")
    jack_of_all_trades_bonus = 0
    if (
        kind in ABILITY_CHECK_KINDS
        and _jack_of_all_trades_bonus(sheet)
        and (
            (
                normalized_ability in SKILL_ABILITIES
                and skill_proficiency == "none"
            )
            or (
                normalized_ruleset == "2014"
                and normalized_ability not in SKILL_ABILITIES
                and not proficient
            )
        )
    ):
        jack_of_all_trades_bonus = _jack_of_all_trades_bonus(sheet)
        roll_bonus += jack_of_all_trades_bonus
    armor_stealth_disadvantage = (
        kind in ABILITY_CHECK_KINDS
        and normalized_ability == "stealth"
        and bool(derived.get("stealth_disadvantage", False))
    )
    if armor_stealth_disadvantage:
        disadvantage = True
    boundary_ids = []
    rule_facts = dict(rules.facts) if rules is not None else {}
    normalized_save_purpose = (
        str(
            save_purpose
            if save_purpose is not None
            else rule_facts.get("save_purpose") or "effect"
        )
        .strip()
        .casefold()
    )
    if kind == "save" and normalized_save_purpose not in {
        "effect",
        "concentration",
    }:
        raise CombatEngineError(
            "save_purpose must be effect or concentration"
        )
    if kind == "save" and _long_ability_name(ability) == "dexterity" and "restrained" in conditions:
        boundary_ids.append("dnd5e.core.save.restrained_dexterity")
    if armor_stealth_disadvantage:
        boundary_ids.append("dnd5e.core.check.armor_stealth_disadvantage")
    if jack_of_all_trades_bonus:
        boundary_ids.append(_JACK_OF_ALL_TRADES_BOUNDARY_ID)

    def with_rule_receipts(result: dict[str, Any]) -> dict[str, Any]:
        result["effect_roll_bonus"] = effect_roll_bonus
        result["rule_receipts"] = [
            *core_receipts(rules, boundary_ids, "check.resolve"),
            *extension.receipts,
        ]
        result["ruleset_fingerprint"] = rules.fingerprint if rules else ""
        return result

    abilities = dict(sheet.get("abilities") or {})
    ability_scores = effective_ability_scores(sheet)
    if kind == "save" and _long_ability_name(ability) in {"strength", "dexterity"}:
        automatic = conditions & {"paralyzed", "petrified", "stunned", "unconscious"}
        if automatic:
            return with_rule_receipts(
                {
                    "kind": "save",
                    "dc": dc,
                    "natural": None,
                    "rolls": [],
                    "critical": False,
                    "fumble": False,
                    "total": None,
                    "success": False,
                    "automatic_failure": True,
                    "reason": sorted(automatic)[0],
                }
            )
    if kind in ABILITY_CHECK_KINDS and "poisoned" in conditions:
        disadvantage = True
    if kind == "save" and _long_ability_name(ability) == "dexterity" and "restrained" in conditions:
        disadvantage = True
    exhaustion_adjustment = d20_exhaustion_adjustment(
        ruleset=normalized_ruleset,
        exhaustion=exhaustion,
        kind=kind,
        bonus=roll_bonus,
        disadvantage=disadvantage,
    )
    roll_bonus = int(exhaustion_adjustment["bonus"])
    disadvantage = bool(exhaustion_adjustment["disadvantage"])
    derived_skills = dict(derived.get("skills") or {})
    if kind in ABILITY_CHECK_KINDS and normalized_ability in derived_skills:
        score_ability = SKILL_ABILITIES[normalized_ability]
        entry = dict(sheet.get("abilities", {}).get(score_ability) or {})
        score = int(ability_scores.get(score_ability, entry.get("score", 10)))
        proficiency_value = proficiency_bonus(level)
        skill_bonus = int(skill.get("bonus", 0) or 0) + roll_bonus
        skill_is_proficient = skill_proficiency in {"proficient", "expertise"}
        if skill_proficiency == "half":
            skill_bonus += proficiency_value // 2
        elif skill_proficiency == "expertise":
            skill_bonus += proficiency_value
        return with_rule_receipts(
            resolve_check(
                dc=dc,
                ability_score=score,
                proficient=skill_is_proficient,
                level=level,
                bonus=skill_bonus,
                advantage=advantage,
                disadvantage=disadvantage,
                kind="ability",
                reroll_ones=_has_halfling_lucky(sheet),
                rng=rng,
            )
        )
    entry = abilities.get(ability) or abilities.get(_long_ability_name(ability)) or {}
    long_ability = _long_ability_name(ability)
    score = int(
        ability_scores.get(
            long_ability,
            entry.get("score", 10) if isinstance(entry, dict) else entry,
        )
    )
    if kind == "save" and isinstance(entry, dict):
        proficient = bool(entry.get("save_proficient", False))
        bonus = int(entry.get("bonus", 0) or 0) + roll_bonus
    else:
        bonus = roll_bonus
    if kind == "ability" and ability in dict(sheet.get("skills") or {}):
        skill = dict(sheet.get("skills", {}).get(ability) or {})
        multiplier = {"none": 0, "half": 0.5, "proficient": 1, "expertise": 2}.get(
            str(skill.get("proficiency", "none")), 0
        )
        bonus = int(skill.get("bonus", 0) or 0)
        proficient = multiplier > 0
        if multiplier == 2:
            bonus += proficiency_bonus(level)
    if kind == "attack":
        raise CombatEngineError("use resolve_attack for attacks")
    if kind == "death_save":
        death = dict(sheet.get("combat", {}).get("death_saves") or {})
        return with_rule_receipts(
            resolve_death_save(
                successes=int(death.get("successes", 0)),
                failures=int(death.get("failures", 0)),
                advantage=advantage,
                disadvantage=disadvantage,
                bonus=roll_bonus,
                reroll_ones=_has_halfling_lucky(sheet),
                rng=rng,
            )
        )
    return with_rule_receipts(
        resolve_check(
            dc=dc,
            ability_score=score,
            proficient=proficient,
            level=level,
            bonus=bonus,
            advantage=advantage,
            disadvantage=disadvantage,
            kind="save" if kind == "save" else "ability",
            reroll_ones=_has_halfling_lucky(sheet),
            rng=rng,
        )
    )


def resolve_actor_group_check(
    actors: list[dict[str, Any]],
    *,
    ability: str,
    dc: int,
    proficient: bool = False,
    bonus: int = 0,
    advantage: bool = False,
    disadvantage: bool = False,
    rules_by_actor_id: dict[str, ResolutionContext] | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    """Resolve the 2014 group ability-check procedure.

    Every participant makes the same ability or skill check.  The group
    succeeds when at least half of its members succeed.  Individual actor-card
    modifiers, conditions, armor, and rule-pack effects remain authoritative.
    """

    if len(actors) < 2:
        raise CombatEngineError("a group ability check requires at least two actors")
    actor_ids = [actor_id(actor) for actor in actors]
    if len(actor_ids) != len(set(actor_ids)):
        raise CombatEngineError("group ability-check actors must be unique")
    if isinstance(dc, bool) or not isinstance(dc, int) or not 0 <= dc <= 40:
        raise CombatEngineError("group ability-check DC must be an integer from 0 to 40")
    if advantage and disadvantage:
        raise CombatEngineError(
            "group ability check cannot have both source advantage and disadvantage"
        )
    normalized_rules = dict(rules_by_actor_id or {})
    unknown_rule_actor_ids = sorted(set(normalized_rules) - set(actor_ids))
    if unknown_rule_actor_ids:
        raise CombatEngineError(
            "group ability-check rule contexts contain actors outside the group"
        )
    incompatible_rule_actor_ids = sorted(
        actor_id_value
        for actor_id_value, context in normalized_rules.items()
        if context.core_pack.edition != "2014"
    )
    if incompatible_rule_actor_ids:
        raise CombatEngineError(
            "group ability checks are a 2014 rules procedure; incompatible rule "
            "contexts: " + ", ".join(incompatible_rule_actor_ids)
        )

    checks: list[dict[str, Any]] = []
    for actor in actors:
        participant_id = actor_id(actor)
        check = resolve_actor_check(
            actor,
            kind="ability",
            ability=ability,
            dc=dc,
            proficient=proficient,
            bonus=bonus,
            advantage=advantage,
            disadvantage=disadvantage,
            rules=normalized_rules.get(participant_id),
            rng=rng,
        )
        checks.append(
            {
                "actor_id": participant_id,
                "check": check,
                "success": bool(check["success"]),
            }
        )

    success_count = sum(1 for participant in checks if participant["success"])
    required_successes = (len(checks) + 1) // 2
    group_rules = next(
        (
            normalized_rules[participant_id]
            for participant_id in actor_ids
            if participant_id in normalized_rules
        ),
        None,
    )
    return {
        "kind": "ability_group_check",
        "ability": str(ability),
        "dc": dc,
        "participant_count": len(checks),
        "success_count": success_count,
        "failure_count": len(checks) - success_count,
        "required_successes": required_successes,
        "success": success_count >= required_successes,
        "participants": checks,
        "rule_receipts": core_receipts(
            group_rules,
            ["dnd5e.core.check.group"],
            "check.group.resolve",
        ),
        "ruleset_fingerprint": group_rules.fingerprint if group_rules else "",
    }


def resolve_save_damage_to_sheets(
    target_actors: list[dict[str, Any]],
    *,
    save_ability: str,
    save_dc: int,
    damage_expression: str,
    damage_type: str,
    half_on_success: bool,
    source: str,
    advantage: bool = False,
    disadvantage: bool = False,
    death_saves: bool = True,
    death_saves_by_actor_id: dict[str, bool] | None = None,
    save_bonuses_by_actor_id: dict[str, int] | None = None,
    ruleset: str | None = None,
    rules: ResolutionContext | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    """Roll shared damage once, then settle every target save and sheet."""

    ability = _long_ability_name(save_ability)
    expression = "".join(str(damage_expression or "").split()).casefold()
    normalized_damage_type = str(damage_type or "").strip().casefold()
    if (
        ability not in {
            "strength",
            "dexterity",
            "constitution",
            "intelligence",
            "wisdom",
            "charisma",
        }
        or isinstance(save_dc, bool)
        or not isinstance(save_dc, int)
        or not 1 <= save_dc <= 40
        or re.fullmatch(r"[1-9]\d*d[1-9]\d*(?:[+-]\d+)?", expression) is None
        or not normalized_damage_type
        or not isinstance(half_on_success, bool)
        or not isinstance(advantage, bool)
        or not isinstance(disadvantage, bool)
        or (advantage and disadvantage)
        or not str(source or "").strip()
        or not target_actors
    ):
        raise CombatEngineError(
            "save damage requires an ability, DC, dice expression, damage type, "
            "success reduction, and source"
        )
    target_ids = [actor_id(target) for target in target_actors]
    if len(target_ids) != len(set(target_ids)):
        raise CombatEngineError("save-damage targets must be unique")
    normalized_save_bonuses = dict(save_bonuses_by_actor_id or {})
    if normalized_save_bonuses and (
        set(normalized_save_bonuses) != set(target_ids)
        or any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not -100 <= value <= 100
            for value in normalized_save_bonuses.values()
        )
    ):
        raise CombatEngineError(
            "save-damage target bonuses must cover every target with bounded integers"
        )
    damage_roll = asdict(roll(expression, rng=rng))
    sheets: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for target_actor in target_actors:
        target_id = actor_id(target_actor)
        target_sheet = actor_sheet(target_actor)
        normalized_ruleset = _normalize_ruleset(
            ruleset or target_sheet.get("edition")
        )
        saved = resolve_actor_check(
            target_actor,
            kind="save",
            ability=ability,
            dc=save_dc,
            bonus=int(normalized_save_bonuses.get(target_id, 0)),
            advantage=advantage,
            disadvantage=disadvantage,
            save_effect_conditions=[],
            ruleset=normalized_ruleset,
            rules=context_with_facts(
                rules,
                save_effect_conditions=[],
            ),
            rng=rng,
        )
        reduction_settlement = standard_save_damage_reduction(
            target_actor,
            ability=ability,
            success=bool(saved["success"]),
            ordinary_successful_save="half" if half_on_success else "none",
            rules=rules,
        )
        reduction = str(reduction_settlement["damage_reduction"])
        damage_amount = damage_amount_after_reduction(
            int(damage_roll["total"]),
            reduction,
        )
        damaged_result: dict[str, Any] | None = None
        updated_sheet = target_sheet
        if damage_amount:
            damaged = apply_damage_to_sheet(
                target_sheet,
                amount=damage_amount,
                damage_type=normalized_damage_type,
                source=str(source),
                ruleset=normalized_ruleset,
                death_saves=(
                    bool(death_saves_by_actor_id[target_id])
                    if death_saves_by_actor_id
                    and target_id in death_saves_by_actor_id
                    else death_saves
                ),
            )
            updated_sheet = damaged["sheet"]
            damaged_result = {
                key: value for key, value in damaged.items() if key != "sheet"
            }
        sheets[target_id] = updated_sheet
        results.append(
            {
                "target_id": target_id,
                "save": saved,
                "success": bool(saved["success"]),
                "save_bonus": int(
                    normalized_save_bonuses.get(target_id, 0)
                ),
                "damage_reduction": reduction,
                "rule_receipts": list(
                    reduction_settlement.get("rule_receipts") or []
                ),
                "damage_amount": damage_amount,
                "damage": damaged_result,
            }
        )
    return {
        "sheets": sheets,
        "result": {
            "kind": "save_damage",
            "damage_roll": damage_roll,
            "targets": results,
        },
    }


def resolve_save_damage_to_sheet(
    target_actor: dict[str, Any],
    **kwargs: Any,
) -> dict[str, Any]:
    """Resolve the single-target form of the generic save-damage contract."""

    target_id = actor_id(target_actor)
    settled = resolve_save_damage_to_sheets([target_actor], **kwargs)
    target_result = dict(settled["result"]["targets"][0])
    return {
        "sheet": settled["sheets"][target_id],
        "result": {
            "kind": "save_damage",
            "damage_roll": deepcopy(settled["result"]["damage_roll"]),
            **{
                key: value
                for key, value in target_result.items()
                if key != "target_id"
            },
        },
    }


def resolve_actor_contest(
    source_actor: dict[str, Any],
    target_actor: dict[str, Any],
    *,
    source_ability: str,
    target_ability: str,
    source_proficient: bool = False,
    target_proficient: bool = False,
    source_bonus: int = 0,
    target_bonus: int = 0,
    source_advantage: bool = False,
    source_disadvantage: bool = False,
    target_advantage: bool = False,
    target_disadvantage: bool = False,
    source_rules: ResolutionContext | None = None,
    target_rules: ResolutionContext | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    """Resolve a 2014 ability contest without inventing a fixed DC.

    Both participants roll the ability or skill appropriate to their effort.
    The higher total wins; on a tie the situation remains unchanged.
    """
    source_id = actor_id(source_actor)
    target_id = actor_id(target_actor)
    if source_id == target_id:
        raise CombatEngineError("an ability contest requires two different actors")
    if source_advantage and source_disadvantage:
        raise CombatEngineError("contest source cannot have advantage and disadvantage together")
    if target_advantage and target_disadvantage:
        raise CombatEngineError("contest target cannot have advantage and disadvantage together")

    def contest_check(
        actor: dict[str, Any],
        *,
        ability: str,
        proficient: bool,
        bonus: int,
        advantage: bool,
        disadvantage: bool,
        rules: ResolutionContext | None,
    ) -> dict[str, Any]:
        result = resolve_actor_check(
            actor,
            kind="ability",
            ability=ability,
            dc=0,
            proficient=proficient,
            bonus=bonus,
            advantage=advantage,
            disadvantage=disadvantage,
            rules=rules,
            rng=rng,
        )
        # A contest compares totals rather than treating either roll as a
        # success against a synthetic DC.
        result.pop("dc", None)
        result.pop("success", None)
        return result

    source_check = contest_check(
        source_actor,
        ability=source_ability,
        proficient=source_proficient,
        bonus=source_bonus,
        advantage=source_advantage,
        disadvantage=source_disadvantage,
        rules=source_rules,
    )
    target_check = contest_check(
        target_actor,
        ability=target_ability,
        proficient=target_proficient,
        bonus=target_bonus,
        advantage=target_advantage,
        disadvantage=target_disadvantage,
        rules=target_rules,
    )
    source_total = int(source_check["total"])
    target_total = int(target_check["total"])
    tie = source_total == target_total
    winner_actor_id = "" if tie else source_id if source_total > target_total else target_id
    return {
        "kind": "ability_contest",
        "source_actor_id": source_id,
        "target_actor_id": target_id,
        "source_ability": str(source_ability),
        "target_ability": str(target_ability),
        "source_check": source_check,
        "target_check": target_check,
        "tie": tie,
        "winner_actor_id": winner_actor_id,
        "outcome": (
            "tie_no_change"
            if tie
            else "source_wins"
            if winner_actor_id == source_id
            else "target_wins"
        ),
    }








def end_turn(encounter: dict[str, Any], *, actor_id_value: str | None = None) -> dict[str, Any]:
    value = deepcopy(encounter)
    current = current_combatant(value)
    if current is None:
        raise CombatEngineError("combat has no participants")
    if actor_id_value and current.get("actor_id") != actor_id_value:
        raise CombatEngineError("it is not this actor's turn")
    if any(item.get("status", "pending") == "pending" for item in value.get("pending", [])):
        raise CombatEngineError("pending choice or save must be resolved before ending the turn")
    current_turn_token = _combat_turn_token(value)
    if any(
        dict(
            dict(combatant.get("turn_flags") or {}).get(
                "legendary_weapon_attack"
            )
            or {}
        ).get("turn_token")
        == current_turn_token
        for combatant in value.get("combatants", [])
    ):
        raise CombatEngineError(
            "a paid legendary weapon attack must be resolved before ending the turn"
        )
    current_conditions = _condition_set(current.get("conditions"))
    current_flags = dict(current.get("turn_flags") or {})
    if (
        current.get("death_saves", False)
        and "unconscious" in current_conditions
        and not current_conditions & DEATH_SAVE_SETTLED_CONDITIONS
        and not current_flags.get("death_save_used")
    ):
        raise CombatEngineError("a required death save must be resolved before ending the turn")
    was_surprised = bool(current.get("surprised"))
    current["surprised"] = False
    current["turns_completed"] = int(current.get("turns_completed", 0) or 0) + 1
    for effect in value.get("ongoing_effects", []):
        if (
            isinstance(effect, dict)
            and effect.get("active", True)
            and effect.get("mechanic_id") == "dnd5e.core.weapon.mastery"
            and effect.get("expires_on") == "source_turn_end"
            and str(effect.get("source_actor_id") or "")
            == str(current.get("actor_id") or "")
            and int(current["turns_completed"])
            >= int(effect.get("expires_after_source_turns_completed", 0) or 0)
        ):
            effect["active"] = False
            effect["ended_reason"] = "source_turn_end"
    retained_flags = {
        key: deepcopy(item)
        for key, item in current_flags.items()
        if key in {"dodging", "helping"}
    }
    if retained_flags:
        current["turn_flags"] = retained_flags
    else:
        current.pop("turn_flags", None)
    if was_surprised and _normalize_ruleset(value.get("ruleset")) == "2014":
        # A surprised creature regains access to reactions as soon as its first
        # turn ends, not at the start of its second turn.
        current.setdefault("turn_budget", {})["reaction"] = 1
    combatants = list(value.get("combatants") or [])
    next_index = (int(value.get("turn_index", 0)) + 1) % len(combatants)

    def begin_next_round() -> None:
        nonlocal combatants
        value["round"] = int(value.get("round", 1)) + 1
        joining = [
            item
            for item in value.get("reinforcements", [])
            if int(item.get("join_round", 0) or 0) <= int(value["round"])
        ]
        if joining:
            for item in joining:
                item.pop("join_round", None)
            combatants.extend(joining)
            combatants.sort(
                key=lambda item: (
                    -int(item.get("initiative", 0) or 0),
                    int(item.get("tie_breaker", 0) or 0),
                    str(item.get("actor_id") or ""),
                )
            )
            value["combatants"] = combatants
            joined_ids = {str(item.get("actor_id") or "") for item in joining}
            value["reinforcements"] = [
                item
                for item in value.get("reinforcements", [])
                if str(item.get("actor_id") or "") not in joined_ids
            ]
            value["log"] = [
                *list(value.get("log") or []),
                *[
                    {
                        "type": "reinforcement_joined",
                        "actor_id": item.get("actor_id"),
                        "round": value["round"],
                    }
                    for item in joining
                ],
            ][-100:]
        combatants = list(value.get("combatants") or combatants)

    if next_index == 0:
        begin_next_round()

    # Dead creatures no longer take turns.  Do not apply this to an unconscious
    # combatant that uses death saves: that turn is still required so the save
    # can be resolved at its start.  Keep one dead index if nobody remains able
    # to take a turn so the encounter can still be ended explicitly.
    skipped_dead: list[str] = []
    checked = 0
    while checked < len(combatants) and "dead" in _condition_set(
        combatants[next_index].get("conditions")
    ):
        skipped_dead.append(str(combatants[next_index].get("actor_id") or ""))
        checked += 1
        candidate_index = (next_index + 1) % len(combatants)
        if candidate_index == 0:
            begin_next_round()
        next_index = candidate_index
    if skipped_dead and checked < len(combatants):
        value["log"] = [
            *list(value.get("log") or []),
            *[
                {
                    "type": "turn_skipped",
                    "actor_id": skipped_actor_id,
                    "reason": "dead",
                    "round": int(value.get("round", 1)),
                }
                for skipped_actor_id in skipped_dead
            ],
        ][-100:]
    value["turn_index"] = next_index
    next_actor = current_combatant(value)
    if next_actor:
        next_actor_id = str(next_actor.get("actor_id") or "")
        for effect in value.get("ongoing_effects", []):
            if (
                isinstance(effect, dict)
                and effect.get("active", True)
                and effect.get("mechanic_id") == "dnd5e.core.weapon.mastery"
                and effect.get("expires_on") == "source_turn_start"
                and str(effect.get("source_actor_id") or "") == next_actor_id
                and int(next_actor.get("turns_completed", 0) or 0)
                >= int(effect.get("expires_after_source_turns_completed", 0) or 0)
            ):
                effect["active"] = False
                effect["ended_reason"] = "source_turn_start"
        legendary_actions = dict(next_actor.get("legendary_actions") or {})
        if legendary_actions:
            legendary_actions["remaining"] = int(
                legendary_actions.get("maximum", 0) or 0
            )
            legendary_actions["last_recovered_turn_token"] = (
                _combat_turn_token(value)
            )
            next_actor["legendary_actions"] = legendary_actions
        next_flags = dict(next_actor.get("turn_flags") or {})
        next_flags.pop("dodging", None)
        next_flags.pop("helping", None)
        next_flags.pop("death_save_used", None)
        next_flags.pop("action_payments", None)
        if next_flags:
            next_actor["turn_flags"] = next_flags
        else:
            next_actor.pop("turn_flags", None)
        value["readied"] = [
            item
            for item in value.get("readied", [])
            if item.get("actor_id") != next_actor.get("actor_id")
        ]
        budget = dict(next_actor.get("turn_budget") or {})
        slow_penalty = max(
            [
                int(effect.get("penalty_ft", 0) or 0)
                for effect in value.get("ongoing_effects", [])
                if isinstance(effect, dict)
                and effect.get("active", True)
                and effect.get("mechanic_id") == "dnd5e.core.weapon.mastery"
                and effect.get("kind") == "speed_penalty"
                and str(effect.get("target_id") or "") == next_actor_id
            ]
            or [0]
        )
        base_turn_speed = int(
            next_actor.get("base_speed", budget.get("speed", 30)) or 30
        )
        effective_turn_speed = max(0, base_turn_speed - slow_penalty)
        budget.update(
            main_action=1,
            bonus_action=1,
            reaction=1,
            speed=effective_turn_speed,
            movement=int(
                effective_turn_speed
                * float(next_actor.get("speed_multiplier", 1.0) or 1.0)
            ),
            object_interaction=1,
            attack_budget=0,
            extra_action=0,
        )
        if "turned" in _condition_set(next_actor.get("conditions")):
            budget["reaction"] = 0
        if next_actor.get("surprised") and _normalize_ruleset(value.get("ruleset")) == "2014":
            budget.update(
                main_action=0,
                bonus_action=0,
                movement=0,
                reaction=0,
                object_interaction=0,
            )
        next_actor["turn_budget"] = budget
    value["turn_spell_casts"] = {}
    return value


def _has_halfling_lucky(sheet: dict[str, Any]) -> bool:
    return any(
        item.get("id") == "dnd5e.content.srd2014.species-feature.lightfoot-lucky"
        or (
            str(item.get("name") or "").casefold() == "lucky"
            and str(item.get("source_key") or "").casefold() in {"halfling", "lightfoot"}
        )
        for item in sheet.get("content", {}).get("features", [])
    )


def _normalize_ruleset(value: Any) -> str:
    try:
        return normalize_dnd_edition(value)
    except ValueError as exc:
        raise CombatEngineError("ruleset must be 2014 or 2024") from exc


def _normalize_disposition(value: Any) -> str:
    normalized = str(value or "neutral").strip().lower()
    if normalized not in {"friendly", "neutral", "hostile"}:
        raise CombatEngineError("disposition must be friendly, neutral, or hostile")
    return normalized


def _positive_int(value: Any, *, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result > 0 else default


def _nonnegative_int(value: Any, *, default: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result >= 0 else default


def _position(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, dict):
        return None
    try:
        return float(value["x"]), float(value["y"])
    except (KeyError, TypeError, ValueError):
        return None


def _grid_distance(left: tuple[float, float], right: tuple[float, float]) -> int:
    """Use the D&D diagonal-grid convention: one square is five feet."""
    return int(max(abs(left[0] - right[0]), abs(left[1] - right[1])) * 5)


def _disengaged(combatant: dict[str, Any]) -> bool:
    return bool(dict(combatant.get("turn_flags") or {}).get("disengaged"))


def _can_make_opportunity_attack(threat: dict[str, Any], moving: dict[str, Any]) -> bool:
    if threat.get("actor_id") == moving.get("actor_id"):
        return False
    if not _can_see(threat, moving):
        # Whether a particular creature can perceive a hidden or invisible
        # mover is a DM fact unless visible_to_actor_ids records it explicitly.
        return False
    if _condition_set(threat.get("conditions")) & {
        "dead",
        "unconscious",
        "stunned",
        "incapacitated",
        "paralyzed",
        "petrified",
        "turned",
    }:
        return False
    if int(dict(threat.get("turn_budget") or {}).get("reaction", 0) or 0) <= 0:
        return False
    return _are_hostile(threat, moving)


def _are_hostile(left: dict[str, Any], right: dict[str, Any]) -> bool:
    """Resolve an opposed relationship from factions first, then party disposition."""
    # Disposition is relative to the party, so the check is symmetric: a
    # friendly PC opposes a hostile NPC just as the NPC opposes the PC.
    left_faction = left.get("faction") or left.get("team")
    right_faction = right.get("faction") or right.get("team")
    if left_faction and right_faction:
        return left_faction != right_faction
    left_disposition = _normalize_disposition(left.get("disposition"))
    right_disposition = _normalize_disposition(right.get("disposition"))
    return {left_disposition, right_disposition} == {"hostile", "friendly"}


def _can_see(viewer: dict[str, Any], subject: dict[str, Any]) -> bool:
    """Resolve only recorded visibility, defaulting ordinary creatures to visible."""
    visible_to = subject.get("visible_to_actor_ids")
    if isinstance(visible_to, list):
        return actor_id(viewer) in {str(item) for item in visible_to}
    if subject.get("hidden", False):
        return False
    conditions = (
        subject.get("conditions")
        if "conditions" in subject
        else actor_sheet(subject).get("conditions")
    )
    return "invisible" not in _condition_set(conditions)


def _attack_range(
    attacker: dict[str, Any],
    target: dict[str, Any],
    weapon: dict[str, Any],
    *,
    attack_mode: str,
) -> dict[str, Any]:
    """Validate only deterministic range facts when both combatants are positioned."""
    attacker_position = _position(attacker.get("position"))
    target_position = _position(target.get("position"))
    if attacker_position is None or target_position is None:
        return {"enforced": False, "distance_ft": None, "disadvantage": False}
    distance = _grid_distance(attacker_position, target_position)
    range_data = (
        weapon.get("range_ft")
        if str(weapon.get("attack_type") or "melee").lower() == "ranged"
        else weapon.get("thrown_range_ft")
    )
    if attack_mode == "melee":
        reach = _nonnegative_int(weapon.get("reach_ft"), default=5)
        if distance > reach:
            raise CombatEngineError("target is outside melee reach")
        return {
            "enforced": True,
            "distance_ft": distance,
            "normal_ft": reach,
            "long_ft": reach,
            "disadvantage": False,
        }
    if not isinstance(range_data, dict) or not range_data.get("normal"):
        raise NeedsRulingError(
            "weapon ranged attack has no recorded range",
            missing=[f"weapon.range:{weapon.get('item_id') or 'unknown'}"],
        )
    normal = _positive_int(range_data.get("normal"), default=5)
    long = _positive_int(range_data.get("long"), default=normal)
    if long < normal:
        long = normal
    if distance > long:
        raise CombatEngineError("target is outside weapon range")
    return {
        "enforced": True,
        "distance_ft": distance,
        "normal_ft": normal,
        "long_ft": long,
        "disadvantage": distance > normal,
    }


def _trait_set(value: Any) -> set[str]:
    if isinstance(value, dict):
        value = value.get("value", [])
    return {str(item).strip().lower() for item in value or []}


def _condition_set(value: Any) -> set[str]:
    return condition_ids(value)


def _long_ability_name(value: str) -> str:
    return {
        "str": "strength",
        "dex": "dexterity",
        "con": "constitution",
        "int": "intelligence",
        "wis": "wisdom",
        "cha": "charisma",
    }.get(value.lower(), value)


def _critical_expression(expression: str) -> str:
    import re

    return re.sub(
        r"(?<!\d)(\d*)d(\d+)",
        lambda match: f"{int(match.group(1) or 1) * 2}d{match.group(2)}",
        expression,
    )
