"""Canonical v2 spell preparation and casting-resource settlement."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from sagasmith_dnd.combat_engine import CombatEngineError, NeedsRulingError
from sagasmith_dnd.conditions import (
    apply_effect_conditions,
    reconcile_ended_effect_conditions,
)
from sagasmith_dnd.editions import normalize_dnd_edition
from sagasmith_dnd.engine import ability_modifier
from sagasmith_dnd.resources import mutate_bounded_resource
from sagasmith_dnd.rule_engine import ResolutionContext, apply_rule_event, core_receipts
from sagasmith_dnd.standard_spell_ids import (
    CORE_BLADE_WARD_MECHANIC_ID,
    CORE_BLADE_WARD_SPELL_ID,
    CORE_FLY_MECHANIC_ID,
    CORE_FLY_SPELL_IDS,
    CORE_HYPNOTIC_PATTERN_MECHANIC_ID,
    CORE_HYPNOTIC_PATTERN_SPELL_IDS,
    CORE_INVISIBILITY_MECHANIC_ID,
    CORE_INVISIBILITY_SPELL_IDS,
    CORE_WITCH_BOLT_MECHANIC_ID,
    CORE_WITCH_BOLT_SPELL_ID,
)
from sagasmith_dnd.vocabulary import PREPARED_SELECTION_MODES, WEAPON_HAND_SLOTS

_SPELL_POINT_COSTS = {1: 2, 2: 3, 3: 5, 4: 6, 5: 7, 6: 9, 7: 10, 8: 11, 9: 13}
CORE_SHIELD_MECHANIC_ID = "dnd5e.core.spell.shield"
CORE_SHIELD_ATTACK_BOUNDARY_ID = "dnd5e.core.spell.shield_attack_ac"
CORE_SHIELD_MAGIC_MISSILE_BOUNDARY_ID = "dnd5e.core.spell.shield_magic_missile"
CORE_SHIELD_ITEM_BOUNDARY_ID = "dnd5e.core.spell.shield_item_ac"
CORE_SHIELD_SPELL_ID = "dnd5e.content.srd2014.spell.shield"
CORE_MAGE_ARMOR_MECHANIC_ID = "dnd5e.core.spell.mage_armor"
CORE_MAGE_ARMOR_SPELL_ID = "dnd5e.content.srd2014.spell.mage-armor"
CORE_2024_MAGE_ARMOR_SPELL_ID = "dnd5e.content.srd2024.spell.mage-armor"
CORE_MAGE_ARMOR_SPELL_IDS = frozenset({CORE_MAGE_ARMOR_SPELL_ID, CORE_2024_MAGE_ARMOR_SPELL_ID})
CORE_MAGIC_ITEM_LAST_CHARGE_MECHANIC_ID = "dnd5e.core.magic_item.last_charge"
CORE_MAGIC_ITEM_RECHARGE_MECHANIC_ID = "dnd5e.core.magic_item.charge_recovery"
CORE_MAGIC_ITEM_SPELL_MECHANIC_ID = "dnd5e.core.spell.magic_item_charges"
CORE_MAGIC_MISSILE_MECHANIC_ID = "dnd5e.core.spell.magic_missile"
CORE_MAGIC_MISSILE_BOUNDARY_ID = "dnd5e.core.spell.magic_missile_darts"
CORE_MAGIC_MISSILE_SPELL_ID = "dnd5e.content.srd2014.spell.magic-missile"
SLOT_PAYMENT_ECONOMIES = frozenset({"slots", "pact_magic"})


def _agent_ruling_requirements(kinds: list[str]) -> list[dict[str, str]]:
    """Make post-payment spell boundaries self-describing to every caller."""

    return [
        {
            "kind": kind,
            "default_resolver": "agent",
            "ruling_kind": "generic_spell_effect",
        }
        for kind in kinds
    ]


PREPARED_SPELL_LIMITS_2024 = {
    "bard": (4, 5, 6, 7, 9, 10, 11, 12, 14, 15, 16, 16, 17, 17, 18, 18, 19, 20, 21, 22),
    "cleric": (4, 5, 6, 7, 9, 10, 11, 12, 14, 15, 16, 16, 17, 17, 18, 18, 19, 20, 21, 22),
    "druid": (4, 5, 6, 7, 9, 10, 11, 12, 14, 15, 16, 16, 17, 17, 18, 18, 19, 20, 21, 22),
    "paladin": (2, 3, 4, 5, 6, 6, 7, 7, 9, 9, 10, 10, 11, 11, 12, 12, 14, 14, 15, 15),
    "ranger": (2, 3, 4, 5, 6, 6, 7, 7, 9, 9, 10, 10, 11, 11, 12, 12, 14, 14, 15, 15),
    "sorcerer": (2, 4, 6, 7, 9, 10, 11, 12, 14, 15, 16, 16, 17, 17, 18, 18, 19, 20, 21, 22),
    "warlock": (2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 11, 11, 12, 12, 13, 13, 14, 14, 15, 15),
    "wizard": (4, 5, 6, 7, 9, 10, 11, 12, 14, 15, 16, 16, 17, 18, 19, 21, 22, 23, 24, 25),
}
_PREPARED_2024 = PREPARED_SPELL_LIMITS_2024
_LONG_REST_ANY_2024 = {"cleric", "druid", "wizard"}
_LONG_REST_ONE_2024 = {"paladin", "ranger"}
_LEVEL_UP_ONE_2024 = {"bard", "sorcerer", "warlock"}
_PREPARED_2014 = {"cleric", "druid", "paladin", "wizard"}


def is_core_shield_spell(spell: dict[str, Any]) -> bool:
    """Recognize only the source-bound Core Shield mechanic."""
    return str(spell.get("id") or "") == CORE_SHIELD_SPELL_ID or CORE_SHIELD_MECHANIC_ID in {
        str(item) for item in spell.get("mechanic_refs", [])
    }


def is_core_magic_missile_spell(spell: dict[str, Any]) -> bool:
    """Recognize only the source-bound Core Magic Missile mechanic."""
    return str(spell.get("id") or "") == CORE_MAGIC_MISSILE_SPELL_ID or (
        CORE_MAGIC_MISSILE_MECHANIC_ID in {str(item) for item in spell.get("mechanic_refs", [])}
    )


def is_core_mage_armor_spell(spell: dict[str, Any]) -> bool:
    """Recognize either edition's source-bound Mage Armor card."""

    return str(spell.get("id") or "") in CORE_MAGE_ARMOR_SPELL_IDS or (
        CORE_MAGE_ARMOR_MECHANIC_ID in {str(item) for item in spell.get("mechanic_refs", [])}
    )


def is_core_blade_ward_spell(spell: dict[str, Any]) -> bool:
    """Recognize only the source-bound standard 2014 Blade Ward mechanic."""

    return str(spell.get("id") or "") == CORE_BLADE_WARD_SPELL_ID or (
        CORE_BLADE_WARD_MECHANIC_ID in {str(item) for item in spell.get("mechanic_refs", [])}
    )


def is_core_hypnotic_pattern_spell(spell: dict[str, Any]) -> bool:
    """Recognize only a source-bound SRD Hypnotic Pattern mechanic."""

    return str(spell.get("id") or "") in CORE_HYPNOTIC_PATTERN_SPELL_IDS or (
        CORE_HYPNOTIC_PATTERN_MECHANIC_ID in {str(item) for item in spell.get("mechanic_refs", [])}
    )


def is_core_fly_spell(spell: dict[str, Any]) -> bool:
    """Recognize only a source-bound SRD Fly mechanic."""

    return str(spell.get("id") or "") in CORE_FLY_SPELL_IDS or (
        CORE_FLY_MECHANIC_ID in {str(item) for item in spell.get("mechanic_refs", [])}
    )


def is_core_invisibility_spell(spell: dict[str, Any]) -> bool:
    """Recognize only a source-bound SRD Invisibility mechanic."""

    return str(spell.get("id") or "") in CORE_INVISIBILITY_SPELL_IDS or (
        CORE_INVISIBILITY_MECHANIC_ID in {str(item) for item in spell.get("mechanic_refs", [])}
    )


def invisibility_target_limit(cast_level: int) -> int:
    """Return Invisibility's exact creature target cap for one legal slot."""

    level = int(cast_level)
    if level < 2 or level > 9:
        raise CombatEngineError("Invisibility cast_level must be between 2 and 9")
    return level - 1


def apply_core_invisibility_effects(
    sheets: dict[str, dict[str, Any]],
    *,
    caster_id: str,
    target_ids: list[str],
    spell_id: str,
    cast_level: int,
    concentration_effect_id: str,
) -> dict[str, Any]:
    """Make the explicit touched creatures invisible under one concentration."""

    if spell_id not in CORE_INVISIBILITY_SPELL_IDS:
        raise CombatEngineError("Invisibility requires its exact source-bound SRD spell id")
    if caster_id not in sheets:
        raise CombatEngineError("Invisibility caster sheet is missing")
    normalized_targets = [str(item).strip() for item in target_ids]
    if (
        not normalized_targets
        or any(not item for item in normalized_targets)
        or len(normalized_targets) != len(set(normalized_targets))
    ):
        raise CombatEngineError("Invisibility target_ids must be unique and non-empty")
    if len(normalized_targets) > invisibility_target_limit(cast_level):
        raise CombatEngineError("Invisibility target count exceeds the cast level")
    missing = [item for item in normalized_targets if item not in sheets]
    if missing:
        raise CombatEngineError(f"Invisibility target sheets are missing: {missing}")
    source_effect = next(
        (
            effect
            for effect in sheets[caster_id].get("effects", [])
            if effect.get("active")
            and effect.get("concentration")
            and str(effect.get("id") or "") == concentration_effect_id
            and str(effect.get("source_spell_id") or "") == spell_id
        ),
        None,
    )
    if source_effect is None:
        raise CombatEngineError("Invisibility requires its exact active concentration effect")

    value = {actor_id: deepcopy(sheet) for actor_id, sheet in sheets.items()}
    effect_ids: dict[str, str] = {}
    for target_id in normalized_targets:
        effect_id = f"invisibility-{uuid4().hex}"
        effect = {
            "id": effect_id,
            "name": "Invisibility",
            "kind": "timed_conditions",
            "source": CORE_INVISIBILITY_MECHANIC_ID,
            "source_spell_id": spell_id,
            "dependency": "source_effect_active",
            "source_actor_id": caster_id,
            "source_effect_id": concentration_effect_id,
            "active": True,
            "concentration": False,
            "duration": {"period": "hour", "remaining": 1},
            "changes": [
                {
                    "path": "conditions",
                    "mode": "add",
                    "value": "invisible",
                }
            ],
            "description": "",
        }
        value[target_id].setdefault("effects", []).append(effect)
        apply_effect_conditions(value[target_id], effect)
        effect_ids[target_id] = effect_id
    return {
        "sheets": value,
        "target_ids": normalized_targets,
        "effect_ids": effect_ids,
        "concentration_effect_id": concentration_effect_id,
        "cast_level": int(cast_level),
        "target_limit": invisibility_target_limit(cast_level),
    }


def fly_target_limit(cast_level: int) -> int:
    """Return Fly's exact willing-creature target cap for one legal slot."""

    level = int(cast_level)
    if level < 3 or level > 9:
        raise CombatEngineError("Fly cast_level must be between 3 and 9")
    return level - 2


def apply_core_fly_effects(
    sheets: dict[str, dict[str, Any]],
    *,
    caster_id: str,
    target_ids: list[str],
    willing_target_ids: list[str],
    spell_id: str,
    cast_level: int,
    concentration_effect_id: str,
) -> dict[str, Any]:
    """Apply Fly's 60-foot speed to its explicit willing targets."""

    if spell_id not in CORE_FLY_SPELL_IDS:
        raise CombatEngineError("Fly requires its exact source-bound SRD spell id")
    if caster_id not in sheets:
        raise CombatEngineError("Fly caster sheet is missing")
    normalized_targets = [str(item).strip() for item in target_ids]
    normalized_willing = [str(item).strip() for item in willing_target_ids]
    if (
        not normalized_targets
        or any(not item for item in normalized_targets)
        or len(normalized_targets) != len(set(normalized_targets))
    ):
        raise CombatEngineError("Fly target_ids must be unique and non-empty")
    if (
        any(not item for item in normalized_willing)
        or len(normalized_willing) != len(set(normalized_willing))
        or set(normalized_willing) != set(normalized_targets)
    ):
        raise CombatEngineError("every Fly target must be explicitly willing")
    if len(normalized_targets) > fly_target_limit(cast_level):
        raise CombatEngineError("Fly target count exceeds the cast level")
    missing = [item for item in normalized_targets if item not in sheets]
    if missing:
        raise CombatEngineError(f"Fly target sheets are missing: {missing}")
    source_effect = next(
        (
            effect
            for effect in sheets[caster_id].get("effects", [])
            if effect.get("active")
            and effect.get("concentration")
            and str(effect.get("id") or "") == concentration_effect_id
            and str(effect.get("source_spell_id") or "") == spell_id
        ),
        None,
    )
    if source_effect is None:
        raise CombatEngineError("Fly requires its exact active concentration effect")

    value = {actor_id: deepcopy(sheet) for actor_id, sheet in sheets.items()}
    effect_ids: dict[str, str] = {}
    for target_id in normalized_targets:
        effect_id = f"fly-{uuid4().hex}"
        value[target_id].setdefault("effects", []).append(
            {
                "id": effect_id,
                "name": "Fly",
                "kind": "spell_fly",
                "source": CORE_FLY_MECHANIC_ID,
                "source_spell_id": spell_id,
                "dependency": "source_effect_active",
                "source_actor_id": caster_id,
                "source_effect_id": concentration_effect_id,
                "active": True,
                "concentration": False,
                "duration": {"period": "minute", "remaining": 10},
                "changes": [
                    {
                        "path": "combat.speed.fly",
                        "mode": "override",
                        "value": 60,
                    }
                ],
                "description": "",
            }
        )
        effect_ids[target_id] = effect_id
    return {
        "sheets": value,
        "target_ids": normalized_targets,
        "effect_ids": effect_ids,
        "concentration_effect_id": concentration_effect_id,
        "cast_level": int(cast_level),
        "target_limit": fly_target_limit(cast_level),
    }


def reconcile_source_effect_dependencies(
    sheets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """End actor effects whose recorded source effect is no longer active."""

    value = {actor_id: deepcopy(sheet) for actor_id, sheet in sheets.items()}
    ended: list[dict[str, str]] = []
    ended_effects: dict[str, list[dict[str, Any]]] = {}
    for target_actor_id, sheet in value.items():
        for effect in sheet.get("effects", []):
            if not effect.get("active") or effect.get("dependency") != "source_effect_active":
                continue
            source_actor_id = str(effect.get("source_actor_id") or "")
            source_effect_id = str(effect.get("source_effect_id") or "")
            if not source_actor_id or not source_effect_id:
                raise CombatEngineError("dependent actor effect is malformed")
            source_sheet = value.get(source_actor_id)
            source_active = bool(
                source_sheet
                and any(
                    source_effect.get("active")
                    and str(source_effect.get("id") or "") == source_effect_id
                    for source_effect in source_sheet.get("effects", [])
                )
            )
            if source_active:
                continue
            effect["active"] = False
            effect["ended_reason"] = "source_effect_ended"
            ended_effects.setdefault(target_actor_id, []).append(effect)
            ended.append(
                {
                    "target_actor_id": target_actor_id,
                    "target_effect_id": str(effect.get("id") or ""),
                    "source_actor_id": source_actor_id,
                    "source_effect_id": source_effect_id,
                }
            )
        if target_actor_id in ended_effects:
            reconcile_ended_effect_conditions(
                sheet,
                ended_effects=ended_effects[target_actor_id],
            )
    return {
        "sheets": value,
        "changed_actor_ids": sorted({item["target_actor_id"] for item in ended}),
        "ended": ended,
    }


def is_core_witch_bolt_spell(spell: dict[str, Any]) -> bool:
    """Recognize only the source-bound standard 2014 Witch Bolt mechanic."""

    return str(spell.get("id") or "") == CORE_WITCH_BOLT_SPELL_ID or (
        CORE_WITCH_BOLT_MECHANIC_ID in {str(item) for item in spell.get("mechanic_refs", [])}
    )


def magic_item_spell_card(
    sheet: dict[str, Any],
    *,
    source_item_id: str,
    spell_id: str,
) -> dict[str, Any]:
    """Return one server-hydrated spell card granted by a held magic item."""
    _, specification, card = _magic_item_spell_binding(
        sheet,
        source_item_id=source_item_id,
        spell_id=spell_id,
    )
    result = deepcopy(card)
    definition = deepcopy(dict(result.get("definition") or {}))
    casting_time = str(specification.get("casting_time") or "").strip()
    if casting_time:
        definition["casting_time"] = casting_time
    spellcasting = dict(
        dict(
            next(
                item
                for item in sheet.get("inventory", {}).get("items", [])
                if str(item.get("id") or "") == source_item_id
            ).get("mechanics")
            or {}
        ).get("spellcasting")
        or {}
    )
    if spellcasting.get("components_required") is False:
        definition["components"] = {
            "verbal": False,
            "somatic": False,
            "material": False,
            "material_description": "",
            "material_cost_cp": 0,
            "consumed": False,
        }
    result["definition"] = definition
    return result


def consume_magic_item_spell_cast(
    sheet: dict[str, Any],
    *,
    source_item_id: str,
    spell_id: str,
    cast_level: int | None = None,
    ritual: bool = False,
    rules: ResolutionContext | None = None,
) -> dict[str, Any]:
    """Pay one held magic item's charges and settle supported self-defense spells."""
    before = apply_rule_event(sheet, "spell.before", rules)
    if before.status != "committed":
        return {
            "sheet": deepcopy(sheet),
            "spell_id": spell_id,
            "status": before.status,
            "rule_receipts": list(before.receipts),
            "pending": list(before.pending),
        }
    value = before.sheet
    item, specification, card = _magic_item_spell_binding(
        value,
        source_item_id=source_item_id,
        spell_id=spell_id,
    )
    if ritual:
        raise CombatEngineError("magic item spell casting cannot be declared as a ritual")
    level = int(card.get("level", 0) or 0)
    if cast_level is not None and int(cast_level) != level:
        raise CombatEngineError("magic item spell cast_level must match its bound spell card")
    charge_cost = int(specification.get("charge_cost", 0) or 0)
    if charge_cost <= 0:
        raise CombatEngineError("magic item spell charge_cost must be positive")
    charges = item.get("charges")
    if not isinstance(charges, dict) or int(charges.get("max", 0) or 0) <= 0:
        raise CombatEngineError("magic item has no structured charge resource")
    if int(charges.get("value", 0) or 0) < charge_cost:
        raise CombatEngineError("magic item has insufficient charges")
    mutate_bounded_resource(charges, amount=charge_cost, direction="spend")
    last_charge_expended = int(charges["value"]) == 0
    charge_rules = dict(dict(item.get("mechanics") or {}).get("charge_rules") or {})
    last_charge_rule = (
        {
            "formula": str(charge_rules.get("last_charge_check_formula") or ""),
            "destroy_on": list(charge_rules.get("destroy_on") or []),
        }
        if last_charge_expended and charge_rules.get("last_charge_check_formula")
        else None
    )

    effect_id = None
    automatic_effect = None
    mechanic_ids = [CORE_MAGIC_ITEM_SPELL_MECHANIC_ID]
    if is_core_mage_armor_spell(card):
        effect_id = _apply_mage_armor_effect(
            value,
            spell_id=spell_id,
            source=f"magic_item:{source_item_id}",
        )
        automatic_effect = "mage_armor"
        mechanic_ids.append(CORE_MAGE_ARMOR_MECHANIC_ID)
    elif is_core_shield_spell(card):
        effect_id = _apply_shield_effect(
            value,
            spell_id=spell_id,
            source=f"magic_item:{source_item_id}",
        )
        automatic_effect = "shield"
        mechanic_ids.append(CORE_SHIELD_ITEM_BOUNDARY_ID)
    ended_invisibility_effect_ids = _end_spell_cast_broken_invisibility(value)
    duration = dict(card.get("definition", {}).get("duration") or {})
    concentration = bool(duration.get("concentration"))
    if concentration:
        _apply_concentration_effect(
            value,
            spell_id=spell_id,
            spell_name=str(card.get("name") or spell_id),
            duration=duration,
            source=f"magic_item:{source_item_id}",
        )

    after = apply_rule_event(value, "spell.after", rules)
    if after.status != "committed":
        return {
            "sheet": deepcopy(sheet),
            "spell_id": spell_id,
            "status": after.status,
            "rule_receipts": [*before.receipts, *after.receipts],
            "pending": list(after.pending),
        }
    ruling_required = [] if automatic_effect else ["targets_and_effect"]
    return {
        "sheet": after.sheet,
        "spell_id": spell_id,
        "cast_level": level,
        "payment": {
            "economy": "item_charges",
            "item_id": source_item_id,
            "cost": charge_cost,
            "level": level,
            "ritual": False,
        },
        "source_item_id": source_item_id,
        "effect_id": effect_id,
        "automatic_effect": automatic_effect,
        "last_charge_expended": last_charge_expended,
        "last_charge_rule": last_charge_rule,
        "concentration_started": concentration,
        "ended_invisibility_effect_ids": ended_invisibility_effect_ids,
        "ruling_required": ruling_required,
        "ruling_requirements": _agent_ruling_requirements(ruling_required),
        "status": "committed",
        "rule_receipts": [
            *core_receipts(rules, mechanic_ids, "spell.magic_item.cast"),
            *before.receipts,
            *after.receipts,
        ],
        "ruleset_fingerprint": rules.fingerprint if rules else "",
    }


def recharge_magic_item_charges(
    sheet: dict[str, Any],
    *,
    source_item_id: str,
    trigger: str,
    rolled_total: int,
) -> dict[str, Any]:
    """Apply a source-declared charge recovery roll to one magic item."""
    value = deepcopy(sheet)
    item = _magic_item_by_id(value, source_item_id)
    if str(item.get("condition") or "normal") != "normal":
        raise CombatEngineError("destroyed or damaged magic items cannot recover charges")
    charges = item.get("charges")
    if not isinstance(charges, dict) or int(charges.get("max", 0) or 0) <= 0:
        raise CombatEngineError("magic item has no structured charge resource")
    charge_rules = dict(dict(item.get("mechanics") or {}).get("charge_rules") or {})
    expected_trigger = str(charge_rules.get("recovery_trigger") or "")
    if not expected_trigger or not charge_rules.get("recovery_formula"):
        raise CombatEngineError("magic item has no source-declared charge recovery")
    if trigger != expected_trigger or str(charges.get("recovers_on") or "") != expected_trigger:
        raise CombatEngineError("magic item charge recovery trigger does not match its source")
    amount = int(rolled_total)
    if amount < 0:
        raise CombatEngineError("magic item charge recovery roll cannot be negative")
    mutation = mutate_bounded_resource(charges, amount=amount, direction="recover")
    return {
        "sheet": value,
        "item_id": source_item_id,
        "trigger": trigger,
        "formula": str(charge_rules["recovery_formula"]),
        "rolled_total": amount,
        "recovered": mutation["amount"],
        "charges": deepcopy(charges),
        "rule_receipts": [],
    }


def resolve_magic_item_last_charge(
    sheet: dict[str, Any],
    *,
    source_item_id: str,
    rolled_total: int,
) -> dict[str, Any]:
    """Resolve a source-declared check made after the item's last charge is spent."""
    value = deepcopy(sheet)
    item = _magic_item_by_id(value, source_item_id)
    charges = item.get("charges")
    if not isinstance(charges, dict) or int(charges.get("value", 0) or 0) != 0:
        raise CombatEngineError("last-charge check requires an empty charge resource")
    charge_rules = dict(dict(item.get("mechanics") or {}).get("charge_rules") or {})
    formula = str(charge_rules.get("last_charge_check_formula") or "")
    destroy_on = [int(result) for result in charge_rules.get("destroy_on") or []]
    if not formula or not destroy_on:
        raise CombatEngineError("magic item has no source-declared last-charge check")
    result = int(rolled_total)
    destroyed = result in destroy_on
    if destroyed:
        equipped_slot = item.get("equipped_slot")
        item["condition"] = "destroyed"
        item["equipped"] = False
        item["equipped_slot"] = None
        if equipped_slot:
            slots = value.get("inventory", {}).get("equipment_slots", {})
            if slots.get(equipped_slot) == source_item_id:
                slots[equipped_slot] = None
    return {
        "sheet": value,
        "item_id": source_item_id,
        "formula": formula,
        "rolled_total": result,
        "destroy_on": destroy_on,
        "destroyed": destroyed,
        "rule_receipts": [],
    }


def _magic_item_by_id(sheet: dict[str, Any], source_item_id: str) -> dict[str, Any]:
    item = next(
        (
            item
            for item in sheet.get("inventory", {}).get("items", [])
            if str(item.get("id") or "") == source_item_id
        ),
        None,
    )
    if item is None or item.get("kind") != "magic_item":
        raise CombatEngineError("source_item_id is not a magic item on this actor card")
    return item


def _magic_item_spell_binding(
    sheet: dict[str, Any],
    *,
    source_item_id: str,
    spell_id: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    item = _magic_item_by_id(sheet, source_item_id)
    if not item.get("equipped") or item.get("equipped_slot") not in WEAPON_HAND_SLOTS:
        raise CombatEngineError("magic item spell casting requires the item to be held")
    if str(item.get("condition") or "normal") != "normal":
        raise CombatEngineError("magic item is not in a usable condition")
    spellcasting = dict(dict(item.get("mechanics") or {}).get("spellcasting") or {})
    if spellcasting.get("requires_attunement") and item.get("attunement") != "attuned":
        raise CombatEngineError("magic item spell casting requires attunement")
    matches = [
        specification
        for specification in spellcasting.get("spells") or []
        if isinstance(specification, dict)
        and str(dict(specification.get("card") or {}).get("id") or "") == spell_id
    ]
    if len(matches) != 1:
        raise CombatEngineError("spell is not bound exactly once to this magic item")
    specification = matches[0]
    card = dict(specification.get("card") or {})
    if not card.get("pack_id") or not card.get("pack_version") or not card.get("rule_refs"):
        raise CombatEngineError("magic item spell card is not source-bound")
    if spellcasting.get("requires_class_spell_list"):
        actor_lists = {
            _class_key(value)
            for value in sheet.get("spellcasting", {}).get("class_lists", [])
            if str(value).strip()
        }
        allowed = {_class_key(value) for value in card.get("classes", []) if str(value).strip()}
        if not actor_lists:
            raise CombatEngineError("magic item requires a recorded actor spell class list")
        if not actor_lists & allowed:
            raise CombatEngineError("magic item spell is not on this actor's spell class list")
    return item, specification, card


def _apply_mage_armor_effect(
    sheet: dict[str, Any],
    *,
    spell_id: str,
    source: str,
) -> str:
    if sheet.get("inventory", {}).get("equipment_slots", {}).get("armor"):
        raise CombatEngineError("Mage Armor requires a target that is not wearing armor")
    for effect in sheet.get("effects", []):
        if effect.get("active") and effect.get("kind") == "spell_mage_armor":
            effect["active"] = False
            effect["ended_reason"] = "replaced_by_mage_armor"
    effect_id = f"mage-armor-{uuid4().hex}"
    sheet.setdefault("effects", []).append(
        {
            "id": effect_id,
            "name": "Mage Armor",
            "kind": "spell_mage_armor",
            "source": source,
            "source_spell_id": spell_id,
            "active": True,
            "concentration": False,
            "duration": {"period": "hour", "remaining": 8},
            "changes": [{"path": "combat.ac.unarmored_base", "mode": "override", "value": 13}],
            "description": "",
        }
    )
    return effect_id


def _apply_shield_effect(
    sheet: dict[str, Any],
    *,
    spell_id: str,
    source: str,
) -> str:
    for effect in sheet.get("effects", []):
        if effect.get("active") and effect.get("kind") == "spell_shield":
            effect["active"] = False
            effect["ended_reason"] = "replaced_by_shield"
    effect_id = f"shield-{uuid4().hex}"
    sheet.setdefault("effects", []).append(
        {
            "id": effect_id,
            "name": "Shield",
            "kind": "spell_shield",
            "source": source,
            "source_spell_id": spell_id,
            "active": True,
            "concentration": False,
            "duration": {"period": "turn_start", "remaining": 1},
            "changes": [{"path": "derived.armor_class", "mode": "add", "value": 5}],
            "description": "",
        }
    )
    return effect_id


def _apply_blade_ward_effect(
    sheet: dict[str, Any],
    *,
    spell_id: str,
) -> str:
    for effect in sheet.get("effects", []):
        if (
            effect.get("active")
            and effect.get("kind") == "spell_blade_ward"
            and effect.get("source") == CORE_BLADE_WARD_MECHANIC_ID
        ):
            effect["active"] = False
            effect["ended_reason"] = "replaced_by_blade_ward"
    effect_id = f"blade-ward-{uuid4().hex}"
    sheet.setdefault("effects", []).append(
        {
            "id": effect_id,
            "name": "Blade Ward",
            "kind": "spell_blade_ward",
            "source": CORE_BLADE_WARD_MECHANIC_ID,
            "source_spell_id": spell_id,
            "active": True,
            "concentration": False,
            # The casting turn's end consumes the first tick; the next turn's
            # end consumes the second and expires the effect.
            "duration": {"period": "turn_end", "remaining": 2},
            "changes": [],
            "description": "",
        }
    )
    return effect_id


def end_concentration_effects(
    sheet: dict[str, Any],
    *,
    effect_ids: list[str],
    ended_reason: str,
) -> dict[str, Any]:
    """End only the named concentration effects and preserve unrelated spells."""

    value = deepcopy(sheet)
    requested = {str(item).strip() for item in effect_ids if str(item).strip()}
    reason = str(ended_reason).strip()
    if not requested:
        return {"sheet": value, "ended_effect_ids": []}
    if not reason:
        raise CombatEngineError("ended_reason is required")
    ended: list[str] = []
    ended_effects: list[dict[str, Any]] = []
    for effect in value.get("effects", []):
        effect_id = str(effect.get("id") or "")
        if effect_id in requested and effect.get("active") and effect.get("concentration"):
            effect["active"] = False
            effect["ended_reason"] = reason
            ended.append(effect_id)
            ended_effects.append(effect)
    if ended_effects:
        reconcile_ended_effect_conditions(value, ended_effects=ended_effects)
    return {"sheet": value, "ended_effect_ids": ended}


def _apply_concentration_effect(
    sheet: dict[str, Any],
    *,
    spell_id: str,
    spell_name: str,
    duration: dict[str, Any],
    source: str,
) -> str:
    for effect in sheet.get("effects", []):
        if effect.get("active") and effect.get("concentration"):
            effect["active"] = False
            effect["ended_reason"] = "replaced_by_concentration"
    effect_id = f"concentration-{uuid4().hex}"
    sheet.setdefault("effects", []).append(
        {
            "id": effect_id,
            "name": f"Concentrating: {spell_name}",
            "kind": "concentration",
            "source": source,
            "source_spell_id": spell_id,
            "active": True,
            "concentration": True,
            "duration": {
                "period": _duration_period(duration.get("unit")),
                "remaining": int(duration.get("value", 0) or 0),
            },
            "changes": [],
            "description": "",
        }
    )
    return effect_id


def _end_spell_cast_broken_invisibility(sheet: dict[str, Any]) -> list[str]:
    """End only the exact Invisibility spell when its target casts a spell."""

    ended: list[str] = []
    ended_effects: list[dict[str, Any]] = []
    for effect in sheet.get("effects", []):
        spell_id = str(effect.get("source_spell_id") or "").strip().casefold()
        if effect.get("active") and spell_id and spell_id.rsplit(".", 1)[-1] == "invisibility":
            effect["active"] = False
            effect["ended_reason"] = "actor_cast_spell"
            ended.append(str(effect.get("id") or ""))
            ended_effects.append(effect)
    if ended:
        reconcile_ended_effect_conditions(sheet, ended_effects=ended_effects)
    return ended


def magic_missile_dart_count(cast_level: int) -> int:
    """Return the exact number of darts created by a legal slot level."""
    level = int(cast_level)
    if level < 1 or level > 9:
        raise CombatEngineError("Magic Missile cast_level must be between 1 and 9")
    return level + 2


def validate_magic_missile_allocations(
    allocations: list[dict[str, Any]], *, cast_level: int
) -> list[dict[str, Any]]:
    """Normalize target allocations while preserving one damage instance per dart."""
    if not isinstance(allocations, list) or not allocations:
        raise CombatEngineError("Magic Missile requires at least one target allocation")
    normalized: list[dict[str, Any]] = []
    by_target: dict[str, int] = {}
    for allocation in allocations:
        if not isinstance(allocation, dict):
            raise CombatEngineError("Magic Missile target allocations must be objects")
        target_id = str(allocation.get("target_id") or "").strip()
        darts = allocation.get("darts")
        if not target_id:
            raise CombatEngineError("Magic Missile target_id is required")
        if isinstance(darts, bool) or not isinstance(darts, int) or darts <= 0:
            raise CombatEngineError("Magic Missile darts must be a positive integer")
        if target_id not in by_target:
            normalized.append({"target_id": target_id, "darts": 0})
        by_target[target_id] = by_target.get(target_id, 0) + darts
    for allocation in normalized:
        allocation["darts"] = by_target[allocation["target_id"]]
    expected = magic_missile_dart_count(cast_level)
    actual = sum(item["darts"] for item in normalized)
    if actual != expected:
        raise CombatEngineError(
            f"Magic Missile level {cast_level} requires exactly {expected} darts, found {actual}"
        )
    return normalized


def available_shield_cast_options(
    sheet: dict[str, Any], *, spell_id: str, rules: ResolutionContext | None = None
) -> list[dict[str, Any]]:
    """Return legal levels and their resource economy for a Core Shield spell."""
    spell = next(
        (item for item in sheet.get("content", {}).get("spells", []) if item.get("id") == spell_id),
        None,
    )
    if spell is None or not is_core_shield_spell(spell):
        return []
    casting_time = str(spell.get("definition", {}).get("casting_time") or "").casefold()
    if not (casting_time.startswith("1 reaction") or casting_time.startswith("reaction")):
        return []
    base_level = int(spell.get("level", 0) or 0)
    if base_level != 1:
        return []
    candidates = [base_level] if spell.get("access", {}).get("at_will") else range(base_level, 10)
    options: list[dict[str, Any]] = []
    for level in candidates:
        try:
            applied = consume_spell_cast(
                sheet,
                spell_id=spell_id,
                cast_level=level,
                rules=rules,
            )
        except CombatEngineError:
            continue
        if applied.get("status") == "committed":
            resolved_level = int(applied.get("cast_level", level) or level)
            if not any(item["cast_level"] == resolved_level for item in options):
                options.append(
                    {
                        "cast_level": resolved_level,
                        "payment": deepcopy(dict(applied.get("payment") or {})),
                    }
                )
    return options


def available_shield_cast_levels(
    sheet: dict[str, Any], *, spell_id: str, rules: ResolutionContext | None = None
) -> list[int]:
    """Return the legal slot levels currently available for a Core Shield spell."""
    return [
        int(item["cast_level"])
        for item in available_shield_cast_options(sheet, spell_id=spell_id, rules=rules)
    ]


def available_shield_attack_defenses(
    sheet: dict[str, Any], *, rules: ResolutionContext | None = None
) -> list[dict[str, Any]]:
    """Build source-bound Shield candidates before combat eligibility filtering."""
    options: list[dict[str, Any]] = []
    for spell in sheet.get("content", {}).get("spells", []):
        spell_id = str(spell.get("id") or "")
        cast_options = available_shield_cast_options(sheet, spell_id=spell_id, rules=rules)
        if not cast_options:
            continue
        options.append(
            {
                "id": spell_id,
                "name": str(spell.get("name") or "Shield"),
                "kind": "spell_armor_class_bonus",
                "bonus": 5,
                "spell_id": spell_id,
                "cast_levels": [int(item["cast_level"]) for item in cast_options],
                "cast_options": cast_options,
                "mechanic_id": CORE_SHIELD_MECHANIC_ID,
                "source_key": str(spell.get("source_key") or ""),
                "rule_refs": deepcopy(list(spell.get("rule_refs") or [])),
            }
        )
    return options


def available_shield_magic_missile_defenses(
    sheet: dict[str, Any], *, rules: ResolutionContext | None = None
) -> list[dict[str, Any]]:
    """Build source-bound Shield choices for the Magic Missile targeting trigger."""
    return [
        {
            **candidate,
            "kind": "spell_magic_missile_immunity",
            "mechanic_id": CORE_SHIELD_MECHANIC_ID,
        }
        for candidate in available_shield_attack_defenses(sheet, rules=rules)
    ]


def consume_shield_reaction(
    sheet: dict[str, Any],
    *,
    spell_id: str,
    cast_level: int,
    trigger: str = "attack",
    rules: ResolutionContext | None = None,
) -> dict[str, Any]:
    """Pay Core Shield and apply its AC effect until the caster's next turn starts."""
    legal_levels = available_shield_cast_levels(sheet, spell_id=spell_id, rules=rules)
    level = int(cast_level)
    if level not in legal_levels:
        raise CombatEngineError("Shield cast_level is not currently available")
    applied = consume_spell_cast(
        sheet,
        spell_id=spell_id,
        cast_level=level,
        ritual=False,
        rules=rules,
    )
    if applied.get("status") != "committed":
        return applied
    value = applied["sheet"]
    effect_id = _apply_shield_effect(
        value,
        spell_id=spell_id,
        source=CORE_SHIELD_MECHANIC_ID,
    )
    normalized_trigger = str(trigger).strip().casefold().replace("-", "_")
    if normalized_trigger not in {"attack", "magic_missile"}:
        raise CombatEngineError("Shield trigger must be attack or magic_missile")
    boundary_id = (
        CORE_SHIELD_MAGIC_MISSILE_BOUNDARY_ID
        if normalized_trigger == "magic_missile"
        else CORE_SHIELD_ATTACK_BOUNDARY_ID
    )
    return {
        **{key: item for key, item in applied.items() if key != "sheet"},
        "sheet": value,
        "effect_id": effect_id,
        "armor_class_bonus": 5,
        "mechanic_id": CORE_SHIELD_MECHANIC_ID,
        "rule_receipts": [
            *list(applied.get("rule_receipts") or []),
            *core_receipts(
                rules,
                [boundary_id],
                f"spell.shield.{normalized_trigger}",
            ),
        ],
    }


def consume_spell_cast(
    sheet: dict[str, Any],
    *,
    spell_id: str,
    cast_level: int | None = None,
    ritual: bool = False,
    signature_free_cast: bool = False,
    feature_cast_source: str | None = None,
    component_ruling: dict[str, Any] | None = None,
    rules: ResolutionContext | None = None,
) -> dict[str, Any]:
    """Validate access and pay a spell's canonical slot or spell-point cost."""
    before = apply_rule_event(sheet, "spell.before", rules)
    if before.status != "committed":
        return {
            "sheet": deepcopy(sheet),
            "spell_id": spell_id,
            "status": before.status,
            "rule_receipts": list(before.receipts),
            "pending": list(before.pending),
        }
    value = before.sheet
    spell = next(
        (item for item in value.get("content", {}).get("spells", []) if item.get("id") == spell_id),
        None,
    )
    if spell is None:
        raise CombatEngineError("spell is not on this actor card")
    access = dict(spell.get("access") or {})
    feature_sources = [
        dict(item) for item in access.get("feature_casting_sources", []) if isinstance(item, dict)
    ]
    selected_feature_source = None
    if feature_cast_source:
        selected_feature_source = next(
            (
                item
                for item in feature_sources
                if str(item.get("source_key") or "").casefold() == feature_cast_source.casefold()
            ),
            None,
        )
        if selected_feature_source is None:
            raise CombatEngineError("feature_cast_source is not available for this spell")
    elif len(feature_sources) == 1 and not bool(feature_sources[0].get("allow_slot_cast")):
        selected_feature_source = feature_sources[0]
    if selected_feature_source is not None and int(
        value.get("progression", {}).get("level", 0) or 0
    ) < int(selected_feature_source.get("minimum_level", 1) or 1):
        raise CombatEngineError("feature spell minimum character level is not met")
    mode = str(value.get("spellcasting", {}).get("preparation", {}).get("mode") or "known")
    spell_mastery = _is_spell_mastery_choice(value, spell_id)
    signature_spell = _is_signature_spell_choice(value, spell_id)
    at_will_available = bool(access.get("at_will")) and (
        not spell_mastery or bool(access.get("prepared"))
    )
    ordinary_available = bool(access.get("always_prepared"))
    available = bool(
        at_will_available
        or ordinary_available
        or selected_feature_source is not None
        or any(bool(item.get("allow_slot_cast")) for item in feature_sources)
    )
    if base_level := int(spell.get("level", 0) or 0):
        if mode in PREPARED_SELECTION_MODES:
            ordinary_available = ordinary_available or bool(access.get("prepared"))
        else:
            ordinary_available = ordinary_available or bool(access.get("known"))
        available = available or ordinary_available
        if (
            ritual
            and access.get("ritual_available")
            and mode == "spellbook"
            and access.get("in_spellbook")
        ):
            available = True
    else:
        available = available or bool(access.get("known") or access.get("prepared"))
    if not available:
        raise CombatEngineError("spell is not available to cast")
    level = base_level if cast_level is None else int(cast_level)
    if base_level == 0 and level != 0:
        raise CombatEngineError("cantrips cannot be cast with a spell slot")
    if level < base_level or level > 9:
        raise CombatEngineError("cast_level is invalid for this spell")
    if (
        base_level > 0
        and access.get("at_will")
        and level > base_level
        and not spell_mastery
        and not ordinary_available
    ):
        raise CombatEngineError("an at-will spell must be cast at its lowest level")
    if signature_free_cast and not signature_spell:
        raise CombatEngineError("signature_free_cast requires a selected Signature Spell")
    if signature_free_cast and selected_feature_source is not None:
        raise CombatEngineError("signature_free_cast cannot be combined with feature_cast_source")
    if signature_free_cast and (base_level != 3 or level != 3 or ritual):
        raise CombatEngineError(
            "a Signature Spell free cast must be cast at 3rd level and cannot be a ritual"
        )
    spellcasting = value.setdefault("spellcasting", {})
    if ritual:
        feature_ritual = bool(
            selected_feature_source is not None and selected_feature_source.get("ritual_only")
        )
        if not feature_ritual and (
            not access.get("ritual_available") or not spellcasting.get("ritual_casting")
        ):
            raise CombatEngineError("spell cannot be cast as a ritual")
        if level != base_level:
            raise CombatEngineError("ritual casting does not allow an upcast spell level")
    casting_overrides = dict((selected_feature_source or {}).get("casting_overrides") or {})
    components = dict(spell.get("definition", {}).get("components") or {})
    if casting_overrides.get("ignore_material_components") is True:
        components.update(
            material=False,
            material_description="",
            material_cost_cp=0,
            consumed=False,
        )
    ruling = dict(component_ruling or {})
    custom_definition = dict(spell.get("custom_definition") or {})
    source_components_unknown = (
        custom_definition.get("component_details") == "not_repeated_in_statblock"
    )
    if source_components_unknown and ruling.get("source_components_confirmed") is not True:
        raise NeedsRulingError(
            "a source-bound spell whose components were not repeated in the statblock "
            "needs source_components_confirmed from reviewed source evidence",
            missing=("source_components",),
            ruling_kind="missing_or_conflicting_source_review",
        )
    if (
        int(components.get("material_cost_cp", 0) or 0) > 0 or components.get("consumed")
    ) and ruling.get("material_confirmed") is not True:
        raise NeedsRulingError(
            "a costly or consumed material component needs material_confirmed "
            "from Agent-as-DM adjudication",
            missing=("material_component",),
            ruling_kind="source_or_scene_fact",
        )
    paid: dict[str, Any] = {"economy": "none", "level": level, "ritual": ritual}
    free_at_will = bool(at_will_available and level == base_level)
    if selected_feature_source is not None:
        ritual_only = bool(selected_feature_source.get("ritual_only"))
        if ritual != ritual_only:
            raise CombatEngineError(
                "feature spell ritual declaration does not match its reviewed source"
            )
        if level != base_level:
            raise CombatEngineError("a feature spell use must be cast at its recorded spell level")
        resource_key = str(selected_feature_source.get("resource_key") or "")
        if resource_key:
            resource = value.setdefault("resources", {}).get(resource_key)
            if not isinstance(resource, dict) or int(resource.get("value", 0) or 0) <= 0:
                raise CombatEngineError("feature spell use is unavailable")
            mutate_bounded_resource(resource, amount=1, direction="spend")
        elif base_level > 0 and not ritual_only:
            raise CombatEngineError("a leveled feature spell needs its reviewed use resource")
        paid = {
            "economy": "feature_spell",
            "resource_key": resource_key or None,
            "source_key": str(selected_feature_source.get("source_key") or ""),
            "spellcasting_ability": str(selected_feature_source.get("spellcasting_ability") or ""),
            "level": level,
            "ritual": ritual_only,
        }
    elif signature_free_cast:
        resource_key = f"signature_spell:{spell_id}"
        resource = value.setdefault("resources", {}).get(resource_key)
        if not isinstance(resource, dict) or int(resource.get("value", 0) or 0) <= 0:
            raise CombatEngineError("Signature Spell free use is unavailable")
        mutate_bounded_resource(resource, amount=1, direction="spend")
        paid = {
            "economy": "signature_spell",
            "resource_key": resource_key,
            "level": level,
            "ritual": False,
        }
    else:
        grant_method = str(dict(spell.get("grant") or {}).get("method") or "")
        if grant_method == "innate" and ritual:
            raise CombatEngineError("innate spellcasting cannot be declared as a ritual")
        if grant_method == "innate" and not free_at_will:
            if level != base_level:
                raise CombatEngineError("innate spellcasting must use the spell's recorded level")
            resource_key = str(
                dict(spell.get("custom_definition") or {}).get("innate_resource_key")
                or f"innate_spell:{spell_id}"
            )
            resource = value.setdefault("resources", {}).get(resource_key)
            if not isinstance(resource, dict) or int(resource.get("value", 0) or 0) <= 0:
                raise CombatEngineError("innate spell use is unavailable")
            mutate_bounded_resource(resource, amount=1, direction="spend")
            paid = {
                "economy": "innate_spell",
                "resource_key": resource_key,
                "level": level,
                "ritual": False,
            }
        elif (
            base_level > 0 and not ritual and not free_at_will and grant_method == "mystic_arcanum"
        ):
            if level != base_level:
                raise CombatEngineError("Mystic Arcanum must be cast at its recorded spell level")
            resource_key = f"mystic_arcanum:{spell_id}"
            resource = value.setdefault("resources", {}).get(resource_key)
            if not isinstance(resource, dict) or int(resource.get("value", 0) or 0) <= 0:
                raise CombatEngineError("Mystic Arcanum use is unavailable")
            mutate_bounded_resource(resource, amount=1, direction="spend")
            paid = {
                "economy": "mystic_arcanum",
                "resource_key": resource_key,
                "level": level,
                "ritual": False,
            }
        elif (
            base_level > 0
            and not ritual
            and not free_at_will
            and spellcasting.get("casting_economy", "slots") == "spell_points"
        ):
            points = spellcasting.get("spell_points")
            if not isinstance(points, dict):
                raise CombatEngineError("spell-point casting is not configured")
            cost = int(spell.get("point_cost") or _SPELL_POINT_COSTS[level])
            if int(points.get("value", 0) or 0) < cost:
                raise CombatEngineError("insufficient spell points")
            mutate_bounded_resource(points, amount=cost, direction="spend")
            paid = {"economy": "spell_points", "cost": cost, "level": level, "ritual": False}
        elif base_level > 0 and not ritual and not free_at_will:
            slots = spellcasting.get("spell_slots", {})
            slot = slots.get(str(level)) or slots.get(f"spell{level}")
            if isinstance(slot, dict) and int(slot.get("value", 0) or 0) > 0:
                mutate_bounded_resource(slot, amount=1, direction="spend")
                paid = {"economy": "slots", "level": level, "ritual": False}
            else:
                pact_magic = spellcasting.get("pact_magic")
                pact_level = int(dict(pact_magic or {}).get("slot_level", 0) or 0)
                if (
                    not isinstance(pact_magic, dict)
                    or int(pact_magic.get("value", 0) or 0) <= 0
                    or pact_level < base_level
                ):
                    raise CombatEngineError(f"no level {level} spell slot remains")
                if cast_level is not None and int(cast_level) != pact_level:
                    raise CombatEngineError(
                        f"Pact Magic casts this spell at its level {pact_level} slot level"
                    )
                mutate_bounded_resource(pact_magic, amount=1, direction="spend")
                level = pact_level
                paid = {"economy": "pact_magic", "level": level, "ritual": False}
    ended_invisibility_effect_ids = _end_spell_cast_broken_invisibility(value)
    duration = dict(
        casting_overrides.get("duration") or spell.get("definition", {}).get("duration") or {}
    )
    concentration = bool(duration.get("concentration"))
    if concentration:
        _apply_concentration_effect(
            value,
            spell_id=spell_id,
            spell_name=str(spell.get("name") or spell_id),
            duration=duration,
            source="spell.cast",
        )
    automatic_effect = None
    effect_id = None
    if is_core_mage_armor_spell(spell):
        effect_id = _apply_mage_armor_effect(
            value,
            spell_id=spell_id,
            source="spell.cast",
        )
        automatic_effect = "mage_armor"
    elif is_core_blade_ward_spell(spell):
        effect_id = _apply_blade_ward_effect(
            value,
            spell_id=spell_id,
        )
        automatic_effect = "blade_ward"
    after = apply_rule_event(value, "spell.after", rules)
    if after.status != "committed":
        return {
            "sheet": deepcopy(sheet),
            "spell_id": spell_id,
            "status": after.status,
            "rule_receipts": [*before.receipts, *after.receipts],
            "pending": list(after.pending),
        }
    ruling_required = [
        *(["source_components"] if source_components_unknown else []),
        *(["verbal_component"] if components.get("verbal") else []),
        *(["somatic_component"] if components.get("somatic") else []),
        *(["material_component"] if components.get("material") else []),
        *(
            ["targets_and_effect"]
            if automatic_effect is None and not isinstance(spell.get("resolution"), dict)
            else []
        ),
    ]
    return {
        "sheet": after.sheet,
        "spell_id": spell_id,
        "cast_level": level,
        "payment": paid,
        "casting_overrides_applied": casting_overrides,
        "concentration_started": concentration,
        "automatic_effect": automatic_effect,
        "effect_id": effect_id,
        "ended_invisibility_effect_ids": ended_invisibility_effect_ids,
        "ruling_required": ruling_required,
        "ruling_requirements": _agent_ruling_requirements(ruling_required),
        "status": "committed",
        "rule_receipts": [
            *core_receipts(
                rules,
                [
                    "dnd5e.core.spell.cantrip_ritual_level",
                    "dnd5e.core.spell.material_components",
                    *([CORE_BLADE_WARD_MECHANIC_ID] if automatic_effect == "blade_ward" else []),
                    *(
                        ["dnd5e.core.spell.pact_magic"]
                        if rules and rules.core_pack.edition == "2014"
                        else []
                    ),
                ],
                "spell.cast",
            ),
            *before.receipts,
            *after.receipts,
        ],
        "ruleset_fingerprint": rules.fingerprint if rules else "",
    }


def _is_spell_mastery_choice(sheet: dict[str, Any], spell_id: str) -> bool:
    return _is_feature_spell_choice(sheet, "spell mastery", spell_id)


def _is_signature_spell_choice(sheet: dict[str, Any], spell_id: str) -> bool:
    return _is_feature_spell_choice(sheet, "signature spells", spell_id)


def _is_feature_spell_choice(
    sheet: dict[str, Any],
    feature_name: str,
    spell_id: str,
) -> bool:
    for feature in sheet.get("content", {}).get("features", []):
        if str(feature.get("name") or "").casefold() != feature_name:
            continue
        choices = [
            dict(feature.get("choices") or {}),
            *[
                dict(item.get("choices") or {})
                for item in feature.get("advancement_grants", [])
                if isinstance(item, dict)
            ],
        ]
        if any(
            spell_id in [str(item) for item in choice.get("spell_artifact_ids", [])]
            for choice in choices
        ):
            return True
    return False


def consume_readied_spell(
    sheet: dict[str, Any],
    *,
    spell_id: str,
    cast_level: int | None = None,
) -> dict[str, Any]:
    """Cast an action spell now and replace current concentration with held energy."""
    spell = next(
        (item for item in sheet.get("content", {}).get("spells", []) if item.get("id") == spell_id),
        None,
    )
    if spell is None:
        raise CombatEngineError("spell is not on this actor card")
    casting_time = str(spell.get("definition", {}).get("casting_time") or "")
    normalized_casting_time = casting_time.casefold().strip()
    if not (normalized_casting_time == "action" or normalized_casting_time.startswith("1 action")):
        raise CombatEngineError("only a spell with a casting time of one action can be readied")

    applied = consume_spell_cast(
        sheet,
        spell_id=spell_id,
        cast_level=cast_level,
        ritual=False,
    )
    value = applied["sheet"]
    for effect in value.get("effects", []):
        if effect.get("active") and effect.get("concentration"):
            effect["active"] = False
            effect["ended_reason"] = "replaced_by_readied_spell"

    duration = dict(spell.get("definition", {}).get("duration") or {})
    release_concentration = bool(duration.get("concentration"))
    holding_effect = None
    if release_concentration:
        candidates = [
            effect
            for effect in value.get("effects", [])
            if effect.get("source_spell_id") == spell_id and effect.get("concentration")
        ]
        if candidates:
            holding_effect = candidates[-1]
            holding_effect["active"] = True
            holding_effect.pop("ended_reason", None)
    if holding_effect is None:
        holding_effect = {
            "id": f"readied-spell-{uuid4().hex}",
            "name": f"Holding: {spell.get('name') or spell_id}",
            "kind": "readied_spell",
            "source": "spell.ready",
            "source_spell_id": spell_id,
            "active": True,
            "concentration": True,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [],
            "description": "",
        }
        value.setdefault("effects", []).append(holding_effect)
    release_duration = dict(holding_effect.get("duration") or {})
    release_kind = str(holding_effect.get("kind") or "concentration")
    holding_effect["duration"] = {"period": "manual", "remaining": 0}
    holding_effect["kind"] = "readied_spell"
    holding_effect["source"] = "spell.ready"
    return {
        **{key: item for key, item in applied.items() if key != "sheet"},
        "sheet": value,
        "casting_time": normalized_casting_time,
        "holding_effect_id": holding_effect["id"],
        "release_concentration": release_concentration,
        "release_duration": release_duration,
        "release_effect_kind": release_kind,
    }


def replace_prepared_spells(
    sheet: dict[str, Any], *, spell_ids: list[str], event: str
) -> dict[str, Any]:
    """Replace a complete prepared list under the 2014/2024 class rules."""
    value = deepcopy(sheet)
    preparation = value.get("spellcasting", {}).get("preparation", {})
    if preparation.get("mode") not in PREPARED_SELECTION_MODES:
        raise CombatEngineError("this character does not prepare level 1+ spells")
    normalized_event = str(event).strip().lower().replace("-", "_")
    if normalized_event not in {"setup", "long_rest", "level_up"}:
        raise CombatEngineError("preparation event must be setup, long_rest, or level_up")
    selected = [str(item).strip() for item in spell_ids]
    if any(not item for item in selected) or len(selected) != len(set(selected)):
        raise CombatEngineError("prepared spell ids must be non-empty and unique")

    spells = {str(item.get("id")): item for item in value.get("content", {}).get("spells", [])}
    missing = [item for item in selected if item not in spells]
    if missing:
        raise CombatEngineError(f"prepared spell is not on this actor card: {missing[0]}")
    classes = {
        _class_key(item.get("name")): int(item.get("level", 0) or 0)
        for item in value.get("progression", {}).get("classes", [])
    }
    if not classes:
        raise CombatEngineError("prepared spell rules require at least one recorded class")
    edition = _edition(value)
    if edition == "2014" and normalized_event == "level_up":
        raise CombatEngineError(
            "2014 prepared spell lists change only when finishing a long rest; "
            "wizard level-up spells are added to the spellbook instead"
        )

    def source_for(spell: dict[str, Any]) -> str:
        raw = str(spell.get("grant", {}).get("source_key") or "")
        source = _class_key(raw)
        if source in classes:
            return source
        if len(classes) == 1:
            return next(iter(classes))
        raise CombatEngineError(
            f"multiclass spell {spell.get('id')} needs grant.source_key identifying its class"
        )

    old_ids = list(preparation.get("selected_spell_ids") or [])
    relevant_ids = set(old_ids) | set(selected)
    by_source_old: dict[str, set[str]] = {}
    by_source_new: dict[str, set[str]] = {}
    for spell_id in relevant_ids:
        spell = spells.get(spell_id)
        if spell is None:
            raise CombatEngineError(f"prepared spell is not on this actor card: {spell_id}")
        if int(spell.get("level", 0) or 0) == 0:
            raise CombatEngineError("cantrips are known, not selected as prepared level 1+ spells")
        if spell.get("access", {}).get("always_prepared"):
            raise CombatEngineError("always-prepared spells must not count in the selected list")
        source = source_for(spell)
        if spell_id in old_ids:
            by_source_old.setdefault(source, set()).add(spell_id)
        if spell_id in selected:
            spellbook_ids = set(
                value.get("spellcasting", {}).get("spellbook", {}).get("spell_ids") or []
            )
            if preparation.get("mode") == "spellbook" and spell_id not in spellbook_ids:
                raise CombatEngineError("a wizard can prepare only spells in their spellbook")
            maximum_level = _maximum_spell_level(
                edition,
                source,
                classes[source],
                progression_profile=_class_spellcasting_profile(value, source),
            )
            if int(spell.get("level", 0) or 0) > maximum_level:
                raise CombatEngineError(
                    f"{source} level {classes[source]} cannot prepare spell level "
                    f"{spell.get('level')}"
                )
            by_source_new.setdefault(source, set()).add(spell_id)

    source_limits: dict[str, int] = {}
    for source, chosen in by_source_new.items():
        limit = prepared_spell_limit(value, edition, source, classes[source])
        source_limits[source] = limit
        if len(chosen) > limit:
            raise CombatEngineError(f"{source} prepared spell selection exceeds {limit}")

    changed_sources = {
        source
        for source in set(by_source_old) | set(by_source_new)
        if by_source_old.get(source, set()) != by_source_new.get(source, set())
    }
    for source in changed_sources:
        old = by_source_old.get(source, set())
        new = by_source_new.get(source, set())
        removed = old - new
        added = new - old
        if normalized_event == "long_rest":
            profile = _class_spellcasting_profile(value, source)
            maximum_replacements = (
                None
                if profile.get("preparation_mode") in PREPARED_SELECTION_MODES
                else _long_rest_replacements(edition, source)
            )
            if maximum_replacements == 0:
                raise CombatEngineError(f"{source} cannot change prepared spells on a long rest")
            if edition == "2024" and len(old) != len(new):
                raise CombatEngineError(
                    "a long-rest change replaces spells; additions belong to setup or level up"
                )
            if maximum_replacements is not None and (
                len(removed) > maximum_replacements or len(added) > maximum_replacements
            ):
                raise CombatEngineError(
                    f"{source} can replace only {maximum_replacements} spell per long rest"
                )
        elif normalized_event == "level_up":
            maximum_replacements = _level_up_replacements(edition, source)
            if len(new) < len(old):
                raise CombatEngineError(
                    "level-up preparation may add newly available spells or replace a legal "
                    "entry, but cannot shrink the prepared list"
                )
            if removed and maximum_replacements == 0:
                raise CombatEngineError(
                    f"{source} can add newly available preparations but cannot replace them "
                    "on level up"
                )
            if maximum_replacements is not None and len(removed) > maximum_replacements:
                raise CombatEngineError(
                    f"{source} can replace only {maximum_replacements} spell per level gained"
                )

    preparation_minutes = 0
    if edition == "2014" and normalized_event == "long_rest" and changed_sources:
        preparation_minutes = sum(
            int(spells[spell_id].get("level", 0) or 0) for spell_id in selected
        )

    preparation["selected_spell_ids"] = selected
    if source_limits and len(source_limits) == len(classes):
        preparation["max_prepared"] = sum(source_limits.values())
    for spell in spells.values():
        spell.setdefault("access", {})["prepared"] = bool(
            spell.get("access", {}).get("always_prepared") or spell.get("id") in selected
        )
    return {
        "sheet": value,
        "event": normalized_event,
        "selected_spell_ids": selected,
        "added": sorted(set(selected) - set(old_ids)),
        "removed": sorted(set(old_ids) - set(selected)),
        "limits": source_limits,
        "preparation_minutes": preparation_minutes,
    }


def validate_spell_grant(
    sheet: dict[str, Any],
    spell: dict[str, Any],
    *,
    source_class: str | None = None,
    artifact_id: str | None = None,
) -> str:
    """Validate a catalog spell against recorded class ownership and spell level."""
    classes = {
        _class_key(item.get("name")): int(item.get("level", 0) or 0)
        for item in sheet.get("progression", {}).get("classes", [])
    }
    if not classes:
        raise CombatEngineError("spell selection requires a recorded class")
    source = _class_key(source_class)
    if not source:
        if len(classes) != 1:
            raise CombatEngineError("multiclass spell selection requires source_class")
        source = next(iter(classes))
    if source not in classes:
        raise CombatEngineError(f"spell source class is not on this actor card: {source}")
    allowed = {_class_key(item) for item in spell.get("classes", []) if str(item).strip()}
    if not allowed:
        raise CombatEngineError("spell artifact has no structured class-list eligibility")
    progression = sheet.get("progression", {})
    spell_list_expansion = [
        *progression.get("background_grants", {}).get("spell_list_expansion", []),
        *progression.get("species_grants", {}).get("spell_list_expansion", []),
    ]
    expanded_artifact_ids = {
        str(item.get("artifact_id") or "")
        for item in spell_list_expansion
        if isinstance(item, dict)
    }
    class_profile = _class_spellcasting_profile(sheet, source)
    class_expansion = {
        str(item).casefold() for item in class_profile.get("spell_list_expansion", [])
    }
    expanded = bool(
        (artifact_id and artifact_id in expanded_artifact_ids)
        or str(spell.get("name") or "").casefold() in class_expansion
    )
    if source not in allowed and not expanded:
        raise CombatEngineError(f"{spell.get('name') or spell.get('id')} is not a {source} spell")
    if expanded and not _class_has_spell_list(sheet, source, classes[source]):
        raise CombatEngineError(f"spell-list expansion requires {source} spellcasting access")
    maximum = _maximum_spell_level(
        _edition(sheet),
        source,
        classes[source],
        progression_profile=class_profile,
    )
    level = int(spell.get("level", 0) or 0)
    if level > maximum:
        raise CombatEngineError(
            f"{source} level {classes[source]} cannot select spell level {level}"
        )
    return source


def _class_has_spell_list(sheet: dict[str, Any], source: str, level: int) -> bool:
    declared_lists = {
        _class_key(item)
        for item in sheet.get("spellcasting", {}).get("class_lists", [])
        if str(item).strip()
    }
    if source in declared_lists:
        return True
    edition = _edition(sheet)
    if source in {"bard", "cleric", "druid", "sorcerer", "warlock", "wizard", "artificer"}:
        return True
    if source in {"paladin", "ranger"}:
        return edition == "2024" or level >= 2
    target = next(
        (
            item
            for item in sheet.get("progression", {}).get("classes", [])
            if _class_key(item.get("name")) == source
        ),
        {},
    )
    subclass = str(target.get("subclass") or "").strip().casefold()
    if level >= 3 and (
        (source == "fighter" and subclass == "eldritch knight")
        or (source == "rogue" and subclass == "arcane trickster")
    ):
        return True
    # A reviewed custom class can declare its list on the actor card without
    # teaching the shared engine another class name.
    return bool(
        len(sheet.get("progression", {}).get("classes", [])) == 1
        and sheet.get("spellcasting", {}).get("ability")
        and (
            sheet.get("spellcasting", {}).get("spell_slots")
            or sheet.get("spellcasting", {}).get("pact_magic")
        )
    )


def _class_key(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    for prefix in ("class:", "class/", "class-"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text.split(":")[-1].split("/")[-1]


def _class_spellcasting_profile(sheet: dict[str, Any], source: str) -> dict[str, Any]:
    source_key = _class_key(source)
    match = next(
        (
            item
            for item in sheet.get("progression", {}).get("classes", [])
            if _class_key(item.get("name")) == source_key
        ),
        {},
    )
    return dict(match.get("spellcasting") or {})


def _profile_prepared_spell_limit(
    sheet: dict[str, Any], profile: dict[str, Any], level: int
) -> int:
    formula = dict(profile.get("prepared_limit") or {})
    if not formula:
        return 0
    divisor = int(formula["class_level_divisor"])
    class_levels = (
        (level + divisor - 1) // divisor if formula["rounding"] == "up" else level // divisor
    )
    score = int(sheet.get("abilities", {}).get(formula["ability"], {}).get("score", 10) or 10)
    return max(int(formula["minimum"]), class_levels + ability_modifier(score))


def _edition(sheet: dict[str, Any]) -> str:
    try:
        return normalize_dnd_edition(sheet.get("edition"))
    except ValueError as exc:
        raise CombatEngineError(str(exc)) from exc


def prepared_spell_limit(
    sheet: dict[str, Any],
    edition: str,
    source: str,
    level: int,
) -> int:
    """Return one class's canonical prepared-spell limit.

    The limit is derived from the current ability score for 2014 prepared
    casters and from the class table for 2024 casters.  Keeping this calculation
    public gives level advancement, content application, and maintenance
    migrations one authoritative implementation.
    """

    source = _class_key(source)
    profile = _class_spellcasting_profile(sheet, source)
    if profile and profile.get("preparation_mode") in PREPARED_SELECTION_MODES:
        return _profile_prepared_spell_limit(sheet, profile, level)
    if edition == "2024" and source in _PREPARED_2024:
        return _PREPARED_2024[source][level - 1]
    if edition == "2014" and source in _PREPARED_2014:
        if source == "paladin" and level < 2:
            return 0
        ability_name = {
            "cleric": "wisdom",
            "druid": "wisdom",
            "paladin": "charisma",
            "wizard": "intelligence",
        }[source]
        score = int(sheet.get("abilities", {}).get(ability_name, {}).get("score", 10) or 10)
        modifier = ability_modifier(score)
        class_levels = level // 2 if source == "paladin" else level
        return max(1, class_levels + modifier)
    if edition == "2014" and source in {"bard", "ranger", "sorcerer", "warlock"}:
        raise CombatEngineError(f"2014 {source} uses spells known, not prepared spells")
    return int(sheet.get("spellcasting", {}).get("preparation", {}).get("max_prepared", 0) or 0)


def synchronize_prepared_spell_limit(sheet: dict[str, Any]) -> dict[str, Any]:
    """Recompute the stored prepared-spell cap from class levels and abilities.

    ``max_prepared`` is persisted so the schema can validate atomic preparation
    changes, but it is a derived rule value.  Any operation that changes a class
    level or spellcasting ability must therefore be able to reconcile it without
    replaying the original level-up transaction.
    """

    value = deepcopy(sheet)
    preparation = value.get("spellcasting", {}).get("preparation", {})
    if preparation.get("mode") not in PREPARED_SELECTION_MODES:
        return {"sheet": value, "change": None, "limits": {}}
    edition = _edition(value)
    classes = {
        _class_key(item.get("name")): int(item.get("level", 0) or 0)
        for item in value.get("progression", {}).get("classes", [])
        if int(item.get("level", 0) or 0) > 0
    }
    limits = {
        source: prepared_spell_limit(value, edition, source, level)
        for source, level in classes.items()
        if (
            source in (_PREPARED_2014 if edition == "2014" else set(_PREPARED_2024))
            or _class_spellcasting_profile(value, source).get("preparation_mode")
            in PREPARED_SELECTION_MODES
        )
    }
    if not limits:
        return {"sheet": value, "change": None, "limits": {}}
    old_limit = int(preparation.get("max_prepared", 0) or 0)
    new_limit = sum(limits.values())
    selected_count = len(preparation.get("selected_spell_ids") or [])
    if selected_count > new_limit:
        raise CombatEngineError(
            "prepared spell selection exceeds the recomputed class and ability limit"
        )
    preparation["max_prepared"] = new_limit
    change = None
    if old_limit != new_limit:
        change = {
            "target": "spellcasting.preparation.max_prepared",
            "old_value": old_limit,
            "new_value": new_limit,
            "class_limits": limits,
        }
    return {"sheet": value, "change": change, "limits": limits}


def _maximum_spell_level(
    edition: str,
    source: str,
    level: int,
    *,
    progression_profile: dict[str, Any] | None = None,
) -> int:
    profile = dict(progression_profile or {})
    if profile:
        progression = str(profile.get("slot_progression") or "none")
        if progression == "none":
            return 0
        if progression == "full":
            return min(9, (level + 1) // 2)
        if progression == "half":
            return min(5, (level + 3) // 4) if level >= 2 else 0
        if progression == "half_round_up":
            return min(5, ((level - 1) // 4) + 1)
        if progression == "pact":
            return min(5, ((level + 1) // 2))
        raise CombatEngineError("class spellcasting slot progression is invalid")
    if source in {"paladin", "ranger"}:
        if edition == "2024":
            return min(5, ((level - 1) // 4) + 1)
        return min(5, (level + 3) // 4) if level >= 2 else 0
    if source == "warlock":
        return min(5, ((level + 1) // 2))
    return min(9, (level + 1) // 2)


def _long_rest_replacements(edition: str, source: str) -> int | None:
    if edition == "2024":
        if source in _LONG_REST_ANY_2024:
            return None
        if source in _LONG_REST_ONE_2024:
            return 1
        return 0
    return None if source in _PREPARED_2014 else 0


def _level_up_replacements(edition: str, source: str) -> int | None:
    if edition == "2024" and source in _LEVEL_UP_ONE_2024:
        return 1
    return 0


def _duration_period(unit: Any) -> str:
    return {"round": "round", "minute": "minute", "hour": "hour", "day": "day"}.get(
        str(unit or ""), "manual"
    )
