from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

from sagasmith_dnd.character_schema import (
    add_effect,
    remove_effect,
    validate_character_sheet,
)
from sagasmith_dnd.combat_engine import (
    CombatEngineError,
    _condition_set,
    _long_ability_name,
    _normalize_ruleset,
    _update_movement_accounting,
    source_speed_multiplier,
)
from sagasmith_dnd.conditions import (
    condition_ids,
)
from sagasmith_dnd.standard_feature_ids import (
    CORE_TORTLE_SHELL_DEFENSE_MECHANIC_ID,
    TORTLE_SHELL_DEFENSE_ARTIFACT_ID,
    TORTLE_SHELL_DEFENSE_CURRENT_PACK_VERSION,
    TORTLE_SHELL_DEFENSE_CURRENT_SELECTION_MECHANIC_REFS,
    TORTLE_SHELL_DEFENSE_EFFECT_ID,
    TORTLE_SHELL_DEFENSE_FEATURE_ID,
    TORTLE_SHELL_DEFENSE_LEGACY_PACK_ID,
    TORTLE_SHELL_DEFENSE_LEGACY_PACK_VERSIONS,
    TORTLE_SHELL_DEFENSE_SOURCE_RULE_REF_PREFIX,
)

_TORTLE_SHELL_DEFENSE_FLAG = "tortle_shell_defense"


_TORTLE_SHELL_DEFENSE_DESCRIPTION = (
    "Withdrawn into the shell: AC +4; Strength and Constitution save advantage; "
    "prone; speed 0 and cannot increase; Dexterity save disadvantage; no reactions; "
    "only a bonus action to emerge."
)


def _tortle_shell_defense_rule_refs(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(item, str) and item.startswith(TORTLE_SHELL_DEFENSE_SOURCE_RULE_REF_PREFIX)
            for item in value
        )
    )


def _tortle_shell_defense_mechanic_refs(
    value: Any, *, pack_version: Any, feature: bool = False
) -> bool:
    refs = {str(item) for item in value or []}
    if pack_version in TORTLE_SHELL_DEFENSE_LEGACY_PACK_VERSIONS:
        return refs == set()
    if pack_version != TORTLE_SHELL_DEFENSE_CURRENT_PACK_VERSION:
        return False
    if feature:
        return refs == {CORE_TORTLE_SHELL_DEFENSE_MECHANIC_ID}
    return refs == set(TORTLE_SHELL_DEFENSE_CURRENT_SELECTION_MECHANIC_REFS)


def tortle_shell_defense_available(sheet: dict[str, Any]) -> bool:
    """Return whether an actor has the exact finalized 2014 Tortle trait."""

    if sheet.get("edition") != "2014":
        return False
    selections = [
        item
        for item in dict(sheet.get("content") or {}).get("selections", [])
        if isinstance(item, dict)
        and item.get("kind") == "species"
        and item.get("pack_id") == TORTLE_SHELL_DEFENSE_LEGACY_PACK_ID
        and item.get("pack_version")
        in {
            *TORTLE_SHELL_DEFENSE_LEGACY_PACK_VERSIONS,
            TORTLE_SHELL_DEFENSE_CURRENT_PACK_VERSION,
        }
        and item.get("artifact_id") == TORTLE_SHELL_DEFENSE_ARTIFACT_ID
        and _tortle_shell_defense_mechanic_refs(
            item.get("mechanic_refs"), pack_version=item.get("pack_version")
        )
        and _tortle_shell_defense_rule_refs(item.get("rule_refs"))
    ]
    features = [
        item
        for item in dict(sheet.get("content") or {}).get("features", [])
        if isinstance(item, dict)
        and item.get("id") == TORTLE_SHELL_DEFENSE_FEATURE_ID
        and item.get("name") == "Shell Defense"
        and item.get("source_key") == "Tortle"
        and item.get("pack_id") == TORTLE_SHELL_DEFENSE_LEGACY_PACK_ID
        and item.get("pack_version")
        in {
            *TORTLE_SHELL_DEFENSE_LEGACY_PACK_VERSIONS,
            TORTLE_SHELL_DEFENSE_CURRENT_PACK_VERSION,
        }
        and _tortle_shell_defense_mechanic_refs(
            item.get("mechanic_refs"), pack_version=item.get("pack_version"), feature=True
        )
        and _tortle_shell_defense_rule_refs(item.get("rule_refs"))
        and item.get("description")
        == (
            "Source-bound action: withdraw or emerge and apply the cited AC, save, speed, "
            "prone, reaction, and action restrictions."
        )
    ]
    if len(selections) > 1 or len(features) > 1:
        raise CombatEngineError("actor card has duplicate Tortle Shell Defense provenance")
    return (
        len(selections) == 1
        and len(features) == 1
        and selections[0]["pack_version"] == features[0]["pack_version"]
    )


def _tortle_shell_defense_effect_changes(*, adds_prone: bool) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = [
        {"path": "derived.armor_class", "mode": "add", "value": 4},
        {"path": "combat.speed.multiplier", "mode": "multiply", "value": 0},
    ]
    if adds_prone:
        changes.append({"path": "conditions", "mode": "add", "value": "prone"})
    return changes


def tortle_shell_defense_active_effect(sheet: dict[str, Any]) -> dict[str, Any] | None:
    """Return the one exact engine-owned Shell Defense effect, if active."""

    matches = [
        item
        for item in sheet.get("effects", [])
        if isinstance(item, dict) and item.get("id") == TORTLE_SHELL_DEFENSE_EFFECT_ID
    ]
    if len(matches) > 1:
        raise CombatEngineError("actor card has duplicate Tortle Shell Defense effects")
    if not matches:
        return None
    effect = matches[0]
    changes = list(effect.get("changes") or [])
    valid_changes = changes == _tortle_shell_defense_effect_changes(
        adds_prone=False
    ) or changes == _tortle_shell_defense_effect_changes(adds_prone=True)
    if not (
        effect.get("name") == "Shell Defense"
        and effect.get("kind") == "timed_conditions"
        and effect.get("source") == TORTLE_SHELL_DEFENSE_ARTIFACT_ID
        and effect.get("source_spell_id") == ""
        and effect.get("active") is True
        and effect.get("concentration") is False
        and dict(effect.get("duration") or {}).get("period") == "manual"
        and effect.get("description") == _TORTLE_SHELL_DEFENSE_DESCRIPTION
        and valid_changes
    ):
        raise CombatEngineError("the persisted Tortle Shell Defense effect is malformed")
    return deepcopy(effect)


def enter_tortle_shell_defense(sheet: dict[str, Any]) -> dict[str, Any]:
    """Persist the exact indefinite Shell Defense effect on a source-bound Tortle."""

    value = validate_character_sheet(sheet)
    if not tortle_shell_defense_available(value):
        raise CombatEngineError("actor does not have source-bound 2014 Tortle Shell Defense")
    if tortle_shell_defense_active_effect(value) is not None:
        raise CombatEngineError("actor is already withdrawn into its shell")
    adds_prone = "prone" not in condition_ids(value.get("conditions"))
    value, effect_id = add_effect(
        value,
        {
            "id": TORTLE_SHELL_DEFENSE_EFFECT_ID,
            "name": "Shell Defense",
            "kind": "timed_conditions",
            "source": TORTLE_SHELL_DEFENSE_ARTIFACT_ID,
            "active": True,
            "concentration": False,
            "duration": {"period": "manual", "remaining": 0},
            "changes": _tortle_shell_defense_effect_changes(adds_prone=adds_prone),
            "description": _TORTLE_SHELL_DEFENSE_DESCRIPTION,
        },
    )
    assert effect_id == TORTLE_SHELL_DEFENSE_EFFECT_ID
    return value


def emerge_tortle_shell_defense(sheet: dict[str, Any]) -> dict[str, Any]:
    """End only the engine-owned Shell Defense effect and its owned prone state."""

    value = validate_character_sheet(sheet)
    if not tortle_shell_defense_available(value):
        raise CombatEngineError("actor does not have source-bound 2014 Tortle Shell Defense")
    if tortle_shell_defense_active_effect(value) is None:
        raise CombatEngineError("actor is not withdrawn into its shell")
    return remove_effect(value, TORTLE_SHELL_DEFENSE_EFFECT_ID)


def _tortle_shell_defense_flag(
    effect: dict[str, Any],
    *,
    other_speed_multiplier: float,
    reaction_on_emerge: int,
) -> dict[str, Any]:
    return {
        "mechanic_id": CORE_TORTLE_SHELL_DEFENSE_MECHANIC_ID,
        "source_artifact_id": TORTLE_SHELL_DEFENSE_ARTIFACT_ID,
        "effect_id": TORTLE_SHELL_DEFENSE_EFFECT_ID,
        "other_speed_multiplier": float(other_speed_multiplier),
        "reaction_on_emerge": max(0, int(reaction_on_emerge)),
        "added_prone": any(
            item.get("path") == "conditions"
            and item.get("mode") == "add"
            and item.get("value") == "prone"
            for item in effect.get("changes", [])
            if isinstance(item, dict)
        ),
    }


def reconcile_tortle_shell_defense_projection(
    combatant: dict[str, Any],
    sheet: dict[str, Any],
) -> None:
    """Synchronize encounter capability/state from the authoritative actor card."""

    capabilities = {str(item) for item in combatant.get("source_capabilities", []) if str(item)}
    available = tortle_shell_defense_available(sheet)
    if available:
        capabilities.add(CORE_TORTLE_SHELL_DEFENSE_MECHANIC_ID)
    else:
        capabilities.discard(CORE_TORTLE_SHELL_DEFENSE_MECHANIC_ID)
    combatant["source_capabilities"] = sorted(capabilities)
    flags = dict(combatant.get("turn_flags") or {})
    effect = tortle_shell_defense_active_effect(sheet)
    if effect is not None and not available:
        raise CombatEngineError("active Tortle Shell Defense lacks its exact source provenance")
    if effect is None:
        flags.pop(_TORTLE_SHELL_DEFENSE_FLAG, None)
    else:
        other_effects = [
            item
            for item in sheet.get("effects", [])
            if not isinstance(item, dict) or item.get("id") != TORTLE_SHELL_DEFENSE_EFFECT_ID
        ]
        other_speed_multiplier = source_speed_multiplier({**sheet, "effects": other_effects})
        existing_flag = dict(flags.get(_TORTLE_SHELL_DEFENSE_FLAG) or {})
        reaction_on_emerge = int(
            existing_flag.get(
                "reaction_on_emerge",
                dict(combatant.get("turn_budget") or {}).get("reaction", 1),
            )
            or 0
        )
        flags[_TORTLE_SHELL_DEFENSE_FLAG] = _tortle_shell_defense_flag(
            effect,
            other_speed_multiplier=other_speed_multiplier,
            reaction_on_emerge=reaction_on_emerge,
        )
        combatant.setdefault("turn_budget", {})["reaction"] = 0
    if flags:
        combatant["turn_flags"] = flags
    else:
        combatant.pop("turn_flags", None)


def encounter_tortle_shell_defense_save_modifiers(
    encounter: dict[str, Any] | None,
    actor_id_value: str,
    *,
    ability: str,
) -> tuple[bool, bool]:
    """Return Shell Defense advantage/disadvantage from authoritative combat state."""

    if encounter is None or _normalize_ruleset(encounter.get("ruleset")) != "2014":
        return False, False
    combatant = next(
        (
            item
            for item in encounter.get("combatants", [])
            if str(item.get("actor_id") or "") == str(actor_id_value)
        ),
        None,
    )
    flag = dict(
        dict((combatant or {}).get("turn_flags") or {}).get(_TORTLE_SHELL_DEFENSE_FLAG) or {}
    )
    if (
        flag.get("mechanic_id") != CORE_TORTLE_SHELL_DEFENSE_MECHANIC_ID
        or flag.get("source_artifact_id") != TORTLE_SHELL_DEFENSE_ARTIFACT_ID
        or flag.get("effect_id") != TORTLE_SHELL_DEFENSE_EFFECT_ID
    ):
        return False, False
    normalized = _long_ability_name(ability)
    return normalized in {"strength", "constitution"}, normalized == "dexterity"


def _tortle_shell_defense_combatant_active(combatant: dict[str, Any]) -> bool:
    flag = dict(dict(combatant.get("turn_flags") or {}).get(_TORTLE_SHELL_DEFENSE_FLAG) or {})
    return (
        flag.get("mechanic_id") == CORE_TORTLE_SHELL_DEFENSE_MECHANIC_ID
        and flag.get("source_artifact_id") == TORTLE_SHELL_DEFENSE_ARTIFACT_ID
        and flag.get("effect_id") == TORTLE_SHELL_DEFENSE_EFFECT_ID
    )


def _require_tortle_shell_emergence_only(combatant: dict[str, Any]) -> None:
    if _tortle_shell_defense_combatant_active(combatant):
        raise CombatEngineError("a withdrawn Tortle can take only the bonus action to emerge")


def apply_stance_action(action, acting, flags, budget):
    if action == "shell_defense":
        if CORE_TORTLE_SHELL_DEFENSE_MECHANIC_ID not in {
            str(item) for item in acting.get("source_capabilities", [])
        }:
            raise CombatEngineError("actor does not have source-bound Tortle Shell Defense")
        if _tortle_shell_defense_combatant_active(acting):
            raise CombatEngineError("actor is already withdrawn into its shell")
        added_prone = "prone" not in _condition_set(acting.get("conditions"))
        prior_speed_multiplier = float(acting.get("speed_multiplier", 1.0) or 0.0)
        flags[_TORTLE_SHELL_DEFENSE_FLAG] = {
            "mechanic_id": CORE_TORTLE_SHELL_DEFENSE_MECHANIC_ID,
            "source_artifact_id": TORTLE_SHELL_DEFENSE_ARTIFACT_ID,
            "effect_id": TORTLE_SHELL_DEFENSE_EFFECT_ID,
            "other_speed_multiplier": prior_speed_multiplier,
            "reaction_on_emerge": max(0, int(budget.get("reaction", 0) or 0)),
            "added_prone": added_prone,
        }
        acting["conditions"] = sorted(_condition_set(acting.get("conditions")) | {"prone"})
        condition_sources = {
            str(key): list(items)
            for key, items in dict(acting.get("condition_sources") or {}).items()
        }
        condition_sources["prone"] = list(
            dict.fromkeys(
                [
                    *condition_sources.get("prone", []),
                    TORTLE_SHELL_DEFENSE_ARTIFACT_ID,
                ]
            )
        )
        acting["condition_sources"] = condition_sources
        acting["speed_multiplier"] = 0.0
        budget["reaction"] = 0
        _update_movement_accounting(acting, budget)
    elif action == "emerge_shell":
        shell_flag = dict(flags.get(_TORTLE_SHELL_DEFENSE_FLAG) or {})
        if not _tortle_shell_defense_combatant_active(acting):
            raise CombatEngineError("actor is not withdrawn into its shell")
        flags.pop(_TORTLE_SHELL_DEFENSE_FLAG, None)
        acting["speed_multiplier"] = float(shell_flag.get("other_speed_multiplier", 1.0) or 0.0)
        budget["reaction"] = max(0, int(shell_flag.get("reaction_on_emerge", 0) or 0))
        condition_sources = {
            str(key): list(items)
            for key, items in dict(acting.get("condition_sources") or {}).items()
        }
        condition_sources["prone"] = [
            item
            for item in condition_sources.get("prone", [])
            if item != TORTLE_SHELL_DEFENSE_ARTIFACT_ID
        ]
        if not condition_sources["prone"]:
            condition_sources.pop("prone", None)
        acting["condition_sources"] = condition_sources
        if shell_flag.get("added_prone") and "prone" not in condition_sources:
            acting["conditions"] = sorted(_condition_set(acting.get("conditions")) - {"prone"})
        _update_movement_accounting(acting, budget)


def _available(actor):
    return CORE_TORTLE_SHELL_DEFENSE_MECHANIC_ID in actor.get("source_capabilities", [])


def _start_turn(actor, flags, budget):
    if _tortle_shell_defense_combatant_active(actor):
        flag = dict(flags.get(_TORTLE_SHELL_DEFENSE_FLAG) or {})
        flag["reaction_on_emerge"] = 1
        flags[_TORTLE_SHELL_DEFENSE_FLAG] = flag
        actor["turn_flags"] = flags
        budget["reaction"] = 0


HANDLER = SimpleNamespace(
    actions=("shell_defense", "emerge_shell"),
    enter_actions=("shell_defense",),
    exit_actions=("emerge_shell",),
    active=_tortle_shell_defense_combatant_active,
    require_action=_require_tortle_shell_emergence_only,
    reconcile=reconcile_tortle_shell_defense_projection,
    available=_available,
    apply=apply_stance_action,
    saves=encounter_tortle_shell_defense_save_modifiers,
    mechanic_id=CORE_TORTLE_SHELL_DEFENSE_MECHANIC_ID,
    flag=_TORTLE_SHELL_DEFENSE_FLAG,
    start_turn=_start_turn,
)
