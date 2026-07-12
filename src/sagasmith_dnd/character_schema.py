"""Validated v2 D&D character, inventory, and narrative document contracts."""

from __future__ import annotations

import copy
import uuid
from typing import Any

from sagasmith_dnd.ability_generation import normalize_ability_generation

ABILITY_NAMES = (
    "strength",
    "dexterity",
    "constitution",
    "intelligence",
    "wisdom",
    "charisma",
)
SKILL_ABILITIES = {
    "acrobatics": "dexterity",
    "animal_handling": "wisdom",
    "arcana": "intelligence",
    "athletics": "strength",
    "deception": "charisma",
    "history": "intelligence",
    "insight": "wisdom",
    "intimidation": "charisma",
    "investigation": "intelligence",
    "medicine": "wisdom",
    "nature": "intelligence",
    "perception": "wisdom",
    "performance": "charisma",
    "persuasion": "charisma",
    "religion": "intelligence",
    "sleight_of_hand": "dexterity",
    "stealth": "dexterity",
    "survival": "wisdom",
}
DENOMINATIONS = ("cp", "sp", "ep", "gp", "pp")
ITEM_KINDS = {
    "weapon",
    "armor",
    "shield",
    "equipment",
    "consumable",
    "tool",
    "container",
    "ammunition",
    "loot",
    "magic_item",
    "focus",
}
EQUIPMENT_SLOTS = (
    "armor",
    "shield",
    "main_hand",
    "off_hand",
    "head",
    "neck",
    "cloak",
    "gloves",
    "boots",
    "ring_1",
    "ring_2",
    "shoulders",
    "back",
    "chest",
    "wrists",
    "waist",
    "legs",
)
SLOT_ITEM_KINDS = {
    "armor": {"armor"},
    "shield": {"shield"},
    "main_hand": {"weapon", "equipment", "tool", "focus", "consumable", "magic_item", "loot"},
    "off_hand": {"weapon", "equipment", "tool", "focus", "consumable", "magic_item", "loot"},
    "head": {"equipment", "magic_item"},
    "neck": {"equipment", "magic_item"},
    "cloak": {"equipment", "magic_item"},
    "gloves": {"equipment", "magic_item"},
    "boots": {"equipment", "magic_item"},
    "ring_1": {"magic_item"},
    "ring_2": {"magic_item"},
    "shoulders": {"equipment", "magic_item"},
    "back": {"equipment", "magic_item"},
    "chest": {"equipment", "magic_item"},
    "wrists": {"equipment", "magic_item"},
    "waist": {"equipment", "magic_item"},
    "legs": {"equipment", "magic_item"},
}
SENSE_NAMES = ("darkvision", "blindsight", "tremorsense", "truesight")
ATTACK_ABILITIES = {"strength", "dexterity", "spell", "none"}
RECOVERY_PERIODS = {"none", "turn", "short_rest", "long_rest", "dawn", "manual"}
EFFECT_PERIODS = {
    "manual",
    "turn_start",
    "turn_end",
    "round",
    "encounter",
    "short_rest",
    "long_rest",
    "minute",
    "hour",
    "day",
}


def _uuid() -> str:
    return str(uuid.uuid4())


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return dict(value)


def _array(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be an array")
    return list(value)


def _text(value: Any, field: str, *, default: str = "", maximum: int = 4000) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if len(value) > maximum:
        raise ValueError(f"{field} exceeds {maximum} characters")
    return value


def _integer(
    value: Any,
    field: str,
    *,
    default: int = 0,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if value is None:
        value = default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field} must be at most {maximum}")
    return value


def _boolean(value: Any, field: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _string_list(value: Any, field: str) -> list[str]:
    values = _array(value or [], field)
    return [_text(item, f"{field}[]", maximum=300) for item in values]


def _reject_unknown(value: dict[str, Any], field: str, allowed: set[str]) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{field} has unsupported fields: {', '.join(unknown)}")


def _default_ability() -> dict[str, Any]:
    return {"score": 10, "save_proficient": False, "bonus": 0}


def _default_skill() -> dict[str, Any]:
    return {"proficiency": "none", "bonus": 0}


def _default_inventory() -> dict[str, Any]:
    return {
        "wallet": {denomination: 0 for denomination in DENOMINATIONS},
        "items": [],
        "equipment_slots": {slot: None for slot in EQUIPMENT_SLOTS},
        "encumbrance": {"mode": "standard", "ignore_currency_weight": True},
    }


def default_character_sheet() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "edition": "2014",
        "identity": {
            "gender": "",
            "age": "",
            "height_cm": None,
            "weight_lb": None,
            "faith": "",
            "deity": "",
            "hair": "",
            "skin": "",
            "eyes": "",
            "portrait_uri": "",
        },
        "progression": {
            "level": 1,
            "xp": 0,
            "classes": [],
            "background": "",
            "background_grants": {
                "feature": "",
                "equipment_item_ids": [],
                "languages": [],
                "tools": [],
                "choices": {},
            },
            "species": "",
        },
        "ability_generation": {
            "ruleset": "",
            "method": "unrecorded",
            "assignments": {},
            "point_buy": None,
            "rolls": [],
        },
        "abilities": {ability: _default_ability() for ability in ABILITY_NAMES},
        "skills": {skill: _default_skill() for skill in SKILL_ABILITIES},
        "combat": {
            "hp": {"value": 1, "max": 1, "temp": 0},
            "ac": {"base": 10, "override": None},
            "initiative": {"ability": "dexterity", "bonus": 0},
            "speed": {"walk": 30, "fly": 0, "swim": 0, "climb": 0, "burrow": 0},
            "hit_dice": {},
            "hp_progression": [],
            "death_saves": {"successes": 0, "failures": 0},
            "exhaustion": 0,
            "inspiration": False,
            "wounded": False,
        },
        "traits": {
            "size": "medium",
            "alignment": "",
            "languages": [],
            "proficiencies": {"armor": [], "weapons": [], "tools": []},
            "resistances": [],
            "immunities": [],
            "vulnerabilities": [],
            "condition_immunities": [],
            "senses": {
                "darkvision": 0,
                "blindsight": 0,
                "tremorsense": 0,
                "truesight": 0,
                "passive_perception_bonus": 0,
            },
        },
        "resources": {},
        "spellcasting": {
            "ability": None,
            "spell_slots": {},
            "pact_magic": None,
            "preparation": {
                "mode": "known",
                "max_prepared": 0,
                "changes_on": "long_rest",
                "selected_spell_ids": [],
            },
            "ritual_casting": False,
            "spellbook": {"enabled": False, "spell_ids": []},
            "casting_economy": "slots",
            "spell_points": None,
        },
        "content": {"spells": [], "features": [], "feats": [], "activities": []},
        "conditions": [],
        "effects": [],
        "adventure_state": {
            "reputation": {},
            "contributions": {},
            "blessings": [],
            "wards": [],
            "legendary_boons": [],
            "status_tags": [],
        },
        "inventory": _default_inventory(),
    }


def default_character_notes() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "profile": {
            "summary": "",
            "appearance": "",
            "personality_traits": [],
            "ideals": [],
            "bonds": [],
            "flaws": [],
            "motivation": "",
            "backstory": "",
            "dm_notes": "",
        },
        "memories": [],
        "relationships": [],
        "goals": [],
    }


def _merge_defaults(default: dict[str, Any], supplied: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(default)
    for key, value in supplied.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_defaults(result[key], value)
        else:
            result[key] = value
    return result


def _normalize_ability(value: Any, field: str) -> dict[str, Any]:
    item = _object(value, field)
    _reject_unknown(item, field, {"score", "save_proficient", "bonus"})
    return {
        "score": _integer(item.get("score"), f"{field}.score", default=10, minimum=1, maximum=30),
        "save_proficient": _boolean(item.get("save_proficient"), f"{field}.save_proficient"),
        "bonus": _integer(item.get("bonus"), f"{field}.bonus"),
    }


def _normalize_skill(value: Any, field: str) -> dict[str, Any]:
    item = _object(value, field)
    _reject_unknown(item, field, {"proficiency", "bonus"})
    proficiency = _text(item.get("proficiency"), f"{field}.proficiency", default="none")
    if proficiency not in {"none", "half", "proficient", "expertise"}:
        raise ValueError(f"{field}.proficiency is invalid")
    return {"proficiency": proficiency, "bonus": _integer(item.get("bonus"), f"{field}.bonus")}


def _normalize_resource(value: Any, field: str) -> dict[str, Any]:
    item = _object(value, field)
    _reject_unknown(item, field, {"label", "value", "max", "recovers_on", "source_key"})
    maximum = _integer(item.get("max"), f"{field}.max", minimum=0)
    current = _integer(item.get("value"), f"{field}.value", default=maximum, minimum=0)
    if current > maximum:
        raise ValueError(f"{field}.value cannot exceed max")
    recovery = _text(item.get("recovers_on"), f"{field}.recovers_on", default="none")
    if recovery not in RECOVERY_PERIODS:
        raise ValueError(f"{field}.recovers_on is invalid")
    return {
        "label": _text(item.get("label"), f"{field}.label", default="", maximum=200),
        "value": current,
        "max": maximum,
        "recovers_on": recovery,
        "source_key": _text(item.get("source_key"), f"{field}.source_key", default="", maximum=300),
    }


def _normalize_item_mechanics(kind: str, value: Any, field: str) -> dict[str, Any]:
    mechanics = _object(value or {}, field)
    if kind == "weapon":
        _reject_unknown(
            mechanics,
            field,
            {
                "category",
                "attack_type",
                "attack_ability",
                "damage_formula",
                "damage_type",
                "versatile_damage_formula",
                "properties",
                "normal_range_ft",
                "long_range_ft",
                "thrown_normal_range_ft",
                "thrown_long_range_ft",
                "ammunition_item_id",
                "proficient",
                "magic_bonus",
            },
        )
        category = _text(mechanics.get("category"), f"{field}.category", default="other")
        if category not in {"simple", "martial", "natural", "improvised", "other"}:
            raise ValueError(f"{field}.category is invalid")
        attack_type = _text(mechanics.get("attack_type"), f"{field}.attack_type", default="melee")
        if attack_type not in {"melee", "ranged"}:
            raise ValueError(f"{field}.attack_type is invalid")
        attack_ability = _text(
            mechanics.get("attack_ability"), f"{field}.attack_ability", default="strength"
        )
        if attack_ability not in ATTACK_ABILITIES:
            raise ValueError(f"{field}.attack_ability is invalid")
        properties = _string_list(mechanics.get("properties"), f"{field}.properties")
        return {
            "category": category,
            "attack_type": attack_type,
            "attack_ability": attack_ability,
            "damage_formula": _text(
                mechanics.get("damage_formula"), f"{field}.damage_formula", maximum=100
            ),
            "damage_type": _text(mechanics.get("damage_type"), f"{field}.damage_type", maximum=100),
            "versatile_damage_formula": _text(
                mechanics.get("versatile_damage_formula"),
                f"{field}.versatile_damage_formula",
                maximum=100,
            ),
            "properties": properties,
            "normal_range_ft": _integer(
                mechanics.get("normal_range_ft"), f"{field}.normal_range_ft", minimum=0
            ),
            "long_range_ft": _integer(
                mechanics.get("long_range_ft"), f"{field}.long_range_ft", minimum=0
            ),
            "thrown_normal_range_ft": _integer(
                mechanics.get("thrown_normal_range_ft"),
                f"{field}.thrown_normal_range_ft",
                minimum=0,
            ),
            "thrown_long_range_ft": _integer(
                mechanics.get("thrown_long_range_ft"),
                f"{field}.thrown_long_range_ft",
                minimum=0,
            ),
            "ammunition_item_id": (
                _text(mechanics["ammunition_item_id"], f"{field}.ammunition_item_id", maximum=100)
                if mechanics.get("ammunition_item_id") is not None
                else None
            ),
            "proficient": _boolean(
                mechanics.get("proficient"), f"{field}.proficient", default=True
            ),
            "magic_bonus": _integer(mechanics.get("magic_bonus"), f"{field}.magic_bonus"),
        }
    if kind == "container":
        _reject_unknown(
            mechanics, field, {"capacity_oz", "weightless_contents", "extra_dimensional"}
        )
        return {
            "capacity_oz": _integer(
                mechanics.get("capacity_oz"), f"{field}.capacity_oz", minimum=0
            ),
            "weightless_contents": _boolean(
                mechanics.get("weightless_contents"), f"{field}.weightless_contents"
            ),
            "extra_dimensional": _boolean(
                mechanics.get("extra_dimensional"), f"{field}.extra_dimensional"
            ),
        }
    if kind == "armor":
        _reject_unknown(
            mechanics,
            field,
            {"base_ac", "dexterity_mode", "dexterity_max", "magic_bonus"},
        )
        if "base_ac" not in mechanics:
            raise ValueError(f"{field}.base_ac is required for armor")
        dexterity_mode = _text(
            mechanics.get("dexterity_mode"), f"{field}.dexterity_mode", default="none"
        )
        if dexterity_mode not in {"none", "full", "max"}:
            raise ValueError(f"{field}.dexterity_mode is invalid")
        dexterity_max = mechanics.get("dexterity_max")
        if dexterity_mode == "max":
            if dexterity_max is None:
                raise ValueError(f"{field}.dexterity_max is required when dexterity_mode is max")
            dexterity_max = _integer(dexterity_max, f"{field}.dexterity_max", minimum=0, maximum=10)
        elif dexterity_max is not None:
            raise ValueError(f"{field}.dexterity_max is only valid when dexterity_mode is max")
        return {
            "base_ac": _integer(mechanics["base_ac"], f"{field}.base_ac", minimum=1),
            "dexterity_mode": dexterity_mode,
            "dexterity_max": dexterity_max,
            "magic_bonus": _integer(mechanics.get("magic_bonus"), f"{field}.magic_bonus"),
        }
    if kind == "shield":
        _reject_unknown(mechanics, field, {"ac_bonus", "magic_bonus"})
        if "ac_bonus" not in mechanics:
            raise ValueError(f"{field}.ac_bonus is required for shield")
        return {
            "ac_bonus": _integer(mechanics["ac_bonus"], f"{field}.ac_bonus", minimum=0),
            "magic_bonus": _integer(mechanics.get("magic_bonus"), f"{field}.magic_bonus"),
        }
    if kind == "magic_item" and "ac_bonus" in mechanics:
        mechanics["ac_bonus"] = _integer(mechanics["ac_bonus"], f"{field}.ac_bonus")
    return mechanics


def _validate_item_slot(item: dict[str, Any], slot: str) -> None:
    if item["kind"] not in SLOT_ITEM_KINDS[slot]:
        raise ValueError(f"{item['kind']} cannot be equipped in {slot}")


def _normalize_item(value: Any, field: str, *, generate_id: bool = True) -> dict[str, Any]:
    item = _object(value, field)
    allowed = {
        "id",
        "name",
        "kind",
        "quantity",
        "weight_oz",
        "price_cp",
        "description",
        "source_key",
        "container_id",
        "equipped",
        "equipped_slot",
        "identified",
        "attunement",
        "condition",
        "uses",
        "charges",
        "mechanics",
    }
    _reject_unknown(item, field, allowed)
    item_id = _text(
        item.get("id"), f"{field}.id", default=_uuid() if generate_id else "", maximum=100
    )
    if not item_id:
        raise ValueError(f"{field}.id is required")
    kind = _text(item.get("kind"), f"{field}.kind", default="equipment")
    if kind not in ITEM_KINDS:
        raise ValueError(f"{field}.kind is invalid")
    if (
        kind in {"armor", "shield"}
        and _integer(item.get("quantity"), f"{field}.quantity", default=1, minimum=1) != 1
    ):
        raise ValueError(f"{field}.quantity must be 1 for {kind}")
    attunement = _text(item.get("attunement"), f"{field}.attunement", default="none")
    if attunement not in {"none", "required", "attuned"}:
        raise ValueError(f"{field}.attunement is invalid")
    equipped_slot = item.get("equipped_slot")
    if equipped_slot is not None:
        equipped_slot = _text(equipped_slot, f"{field}.equipped_slot")
        if equipped_slot not in EQUIPMENT_SLOTS:
            raise ValueError(f"{field}.equipped_slot is invalid")
    uses = _normalize_resource(item.get("uses") or {}, f"{field}.uses")
    charges = _normalize_resource(item.get("charges") or {}, f"{field}.charges")
    return {
        "id": item_id,
        "name": _text(item.get("name"), f"{field}.name", maximum=300),
        "kind": kind,
        "quantity": _integer(item.get("quantity"), f"{field}.quantity", default=1, minimum=1),
        "weight_oz": _integer(item.get("weight_oz"), f"{field}.weight_oz", minimum=0),
        "price_cp": _integer(item.get("price_cp"), f"{field}.price_cp", minimum=0),
        "description": _text(item.get("description"), f"{field}.description", maximum=1200),
        "source_key": _text(item.get("source_key"), f"{field}.source_key", maximum=300),
        "container_id": (
            _text(item.get("container_id"), f"{field}.container_id", maximum=100)
            if item.get("container_id") is not None
            else None
        ),
        "equipped": _boolean(item.get("equipped"), f"{field}.equipped"),
        "equipped_slot": equipped_slot,
        "identified": _boolean(item.get("identified"), f"{field}.identified", default=True),
        "attunement": attunement,
        "condition": _text(
            item.get("condition"), f"{field}.condition", default="normal", maximum=100
        ),
        "uses": uses,
        "charges": charges,
        "mechanics": _normalize_item_mechanics(kind, item.get("mechanics"), f"{field}.mechanics"),
    }


def validate_inventory(value: Any) -> dict[str, Any]:
    inventory = _merge_defaults(_default_inventory(), _object(value or {}, "inventory"))
    _reject_unknown(inventory, "inventory", {"wallet", "items", "equipment_slots", "encumbrance"})
    wallet = _object(inventory["wallet"], "inventory.wallet")
    _reject_unknown(wallet, "inventory.wallet", set(DENOMINATIONS))
    normalized_wallet = {
        denomination: _integer(
            wallet.get(denomination), f"inventory.wallet.{denomination}", minimum=0
        )
        for denomination in DENOMINATIONS
    }
    items = [
        _normalize_item(item, f"inventory.items[{index}]")
        for index, item in enumerate(_array(inventory["items"], "inventory.items"))
    ]
    item_ids = {item["id"] for item in items}
    if len(item_ids) != len(items):
        raise ValueError("inventory.items contains duplicate ids")
    by_id = {item["id"]: item for item in items}
    for item in items:
        container_id = item["container_id"]
        if container_id is not None:
            container = by_id.get(container_id)
            if container is None or container["kind"] != "container":
                raise ValueError("inventory item references an invalid container")
            seen = {item["id"]}
            current = container
            while current["container_id"] is not None:
                parent_id = current["container_id"]
                if parent_id in seen:
                    raise ValueError("inventory containers must not form a cycle")
                seen.add(parent_id)
                current = by_id[parent_id]
        if item["kind"] == "weapon":
            ammunition_item_id = item["mechanics"]["ammunition_item_id"]
            if ammunition_item_id is not None:
                ammunition = by_id.get(ammunition_item_id)
                if ammunition is None or ammunition["kind"] != "ammunition":
                    raise ValueError(
                        "weapon ammunition_item_id must reference ammunition in inventory"
                    )
    for container in (item for item in items if item["kind"] == "container"):
        capacity = container["mechanics"]["capacity_oz"]
        contained_weight = sum(
            item["weight_oz"] * item["quantity"]
            for item in items
            if item["container_id"] == container["id"]
        )
        if capacity and contained_weight > capacity:
            raise ValueError("container contents exceed capacity_oz")
    slots = _object(inventory["equipment_slots"], "inventory.equipment_slots")
    _reject_unknown(slots, "inventory.equipment_slots", set(EQUIPMENT_SLOTS))
    normalized_slots: dict[str, str | None] = {}
    for slot in EQUIPMENT_SLOTS:
        item_id = slots.get(slot)
        if item_id is not None:
            item_id = _text(item_id, f"inventory.equipment_slots.{slot}", maximum=100)
            if item_id not in by_id:
                raise ValueError(f"inventory.equipment_slots.{slot} references an unknown item")
            item = by_id[item_id]
            _validate_item_slot(item, slot)
            if not item["equipped"] or item["equipped_slot"] != slot:
                raise ValueError("inventory equipment slot and item equipped state must agree")
        normalized_slots[slot] = item_id
    for item in items:
        equipped_slot = item["equipped_slot"]
        if item["equipped"]:
            if equipped_slot is None:
                raise ValueError("equipped item must declare an equipped_slot")
            if normalized_slots[equipped_slot] != item["id"]:
                raise ValueError("equipped item must be referenced by its equipment slot")
            _validate_item_slot(item, equipped_slot)
        elif equipped_slot is not None:
            raise ValueError("unequipped item cannot declare an equipped_slot")
    encumbrance = _object(inventory["encumbrance"], "inventory.encumbrance")
    _reject_unknown(encumbrance, "inventory.encumbrance", {"mode", "ignore_currency_weight"})
    mode = _text(encumbrance.get("mode"), "inventory.encumbrance.mode", default="standard")
    if mode not in {"standard", "variant"}:
        raise ValueError("inventory.encumbrance.mode is invalid")
    return {
        "wallet": normalized_wallet,
        "items": items,
        "equipment_slots": normalized_slots,
        "encumbrance": {
            "mode": mode,
            "ignore_currency_weight": _boolean(
                encumbrance.get("ignore_currency_weight"),
                "inventory.encumbrance.ignore_currency_weight",
                default=True,
            ),
        },
    }


def _normalize_spell(value: Any, field: str) -> dict[str, Any]:
    spell = _object(value, field)
    allowed = {
        "id",
        "source_key",
        "name",
        "level",
        "grant",
        "access",
        "definition",
        "point_cost",
        "custom_definition",
        "notes",
    }
    _reject_unknown(spell, field, allowed)
    grant = _object(spell.get("grant") or {}, f"{field}.grant")
    _reject_unknown(grant, f"{field}.grant", {"source_type", "source_key", "method"})
    access = _object(spell.get("access") or {}, f"{field}.access")
    _reject_unknown(
        access,
        f"{field}.access",
        {"known", "prepared", "always_prepared", "in_spellbook", "ritual_available", "at_will"},
    )
    definition = _object(spell.get("definition") or {}, f"{field}.definition")
    _reject_unknown(
        definition,
        f"{field}.definition",
        {"school", "casting_time", "range", "duration", "components", "effect"},
    )
    spell_range = _object(definition.get("range") or {}, f"{field}.definition.range")
    _reject_unknown(
        spell_range, f"{field}.definition.range", {"kind", "normal_ft", "long_ft", "area"}
    )
    range_kind = _text(spell_range.get("kind"), f"{field}.definition.range.kind", default="special")
    if range_kind not in {"self", "touch", "distance", "sight", "unlimited", "special"}:
        raise ValueError(f"{field}.definition.range.kind is invalid")
    duration = _object(definition.get("duration") or {}, f"{field}.definition.duration")
    _reject_unknown(
        duration, f"{field}.definition.duration", {"kind", "value", "unit", "concentration"}
    )
    duration_kind = _text(
        duration.get("kind"), f"{field}.definition.duration.kind", default="instantaneous"
    )
    if duration_kind not in {"instantaneous", "timed", "until_dispelled", "special"}:
        raise ValueError(f"{field}.definition.duration.kind is invalid")
    duration_unit = _text(
        duration.get("unit"), f"{field}.definition.duration.unit", default="round"
    )
    if duration_unit not in {"round", "minute", "hour", "day", "special"}:
        raise ValueError(f"{field}.definition.duration.unit is invalid")
    components = _object(definition.get("components") or {}, f"{field}.definition.components")
    _reject_unknown(
        components,
        f"{field}.definition.components",
        {"verbal", "somatic", "material", "material_description", "material_cost_cp", "consumed"},
    )
    return {
        "id": _text(spell.get("id"), f"{field}.id", default=_uuid(), maximum=100),
        "source_key": _text(spell.get("source_key"), f"{field}.source_key", maximum=300),
        "name": _text(spell.get("name"), f"{field}.name", maximum=300),
        "level": _integer(spell.get("level"), f"{field}.level", minimum=0, maximum=9),
        "grant": {
            "source_type": _text(
                grant.get("source_type"), f"{field}.grant.source_type", default="custom"
            ),
            "source_key": _text(grant.get("source_key"), f"{field}.grant.source_key", maximum=300),
            "method": _text(
                grant.get("method"), f"{field}.grant.method", default="known", maximum=100
            ),
        },
        "access": {
            "known": _boolean(access.get("known"), f"{field}.access.known"),
            "prepared": _boolean(access.get("prepared"), f"{field}.access.prepared"),
            "always_prepared": _boolean(
                access.get("always_prepared"), f"{field}.access.always_prepared"
            ),
            "in_spellbook": _boolean(access.get("in_spellbook"), f"{field}.access.in_spellbook"),
            "ritual_available": _boolean(
                access.get("ritual_available"), f"{field}.access.ritual_available"
            ),
            "at_will": _boolean(access.get("at_will"), f"{field}.access.at_will"),
        },
        "definition": {
            "school": _text(definition.get("school"), f"{field}.definition.school", maximum=100),
            "casting_time": _text(
                definition.get("casting_time"), f"{field}.definition.casting_time", maximum=200
            ),
            "range": {
                "kind": range_kind,
                "normal_ft": _integer(
                    spell_range.get("normal_ft"), f"{field}.definition.range.normal_ft", minimum=0
                ),
                "long_ft": _integer(
                    spell_range.get("long_ft"), f"{field}.definition.range.long_ft", minimum=0
                ),
                "area": _text(
                    spell_range.get("area"), f"{field}.definition.range.area", maximum=200
                ),
            },
            "duration": {
                "kind": duration_kind,
                "value": _integer(
                    duration.get("value"), f"{field}.definition.duration.value", minimum=0
                ),
                "unit": duration_unit,
                "concentration": _boolean(
                    duration.get("concentration"), f"{field}.definition.duration.concentration"
                ),
            },
            "components": {
                "verbal": _boolean(
                    components.get("verbal"), f"{field}.definition.components.verbal"
                ),
                "somatic": _boolean(
                    components.get("somatic"), f"{field}.definition.components.somatic"
                ),
                "material": _boolean(
                    components.get("material"), f"{field}.definition.components.material"
                ),
                "material_description": _text(
                    components.get("material_description"),
                    f"{field}.definition.components.material_description",
                    maximum=500,
                ),
                "material_cost_cp": _integer(
                    components.get("material_cost_cp"),
                    f"{field}.definition.components.material_cost_cp",
                    minimum=0,
                ),
                "consumed": _boolean(
                    components.get("consumed"), f"{field}.definition.components.consumed"
                ),
            },
            "effect": _text(definition.get("effect"), f"{field}.definition.effect", maximum=4000),
        },
        "point_cost": _integer(spell.get("point_cost"), f"{field}.point_cost", minimum=0),
        "custom_definition": (
            _object(spell["custom_definition"], f"{field}.custom_definition")
            if spell.get("custom_definition") is not None
            else None
        ),
        "notes": _text(spell.get("notes"), f"{field}.notes", maximum=1200),
    }


def _normalize_effect(value: Any, field: str) -> dict[str, Any]:
    effect = _object(value, field)
    allowed = {
        "id",
        "name",
        "kind",
        "source",
        "source_spell_id",
        "active",
        "concentration",
        "duration",
        "changes",
        "description",
    }
    _reject_unknown(effect, field, allowed)
    duration = _object(effect.get("duration") or {}, f"{field}.duration")
    _reject_unknown(duration, f"{field}.duration", {"period", "remaining"})
    period = _text(duration.get("period"), f"{field}.duration.period", default="manual")
    if period not in EFFECT_PERIODS:
        raise ValueError(f"{field}.duration.period is invalid")
    changes = []
    for index, change in enumerate(_array(effect.get("changes") or [], f"{field}.changes")):
        item = _object(change, f"{field}.changes[{index}]")
        _reject_unknown(item, f"{field}.changes[{index}]", {"path", "mode", "value"})
        changes.append(
            {
                "path": _text(item.get("path"), f"{field}.changes[{index}].path", maximum=300),
                "mode": _text(
                    item.get("mode"),
                    f"{field}.changes[{index}].mode",
                    default="override",
                    maximum=100,
                ),
                "value": item.get("value"),
            }
        )
    return {
        "id": _text(effect.get("id"), f"{field}.id", default=_uuid(), maximum=100),
        "name": _text(effect.get("name"), f"{field}.name", maximum=300),
        "kind": _text(effect.get("kind"), f"{field}.kind", default="custom", maximum=100),
        "source": _text(effect.get("source"), f"{field}.source", maximum=300),
        "source_spell_id": _text(
            effect.get("source_spell_id"), f"{field}.source_spell_id", maximum=100
        ),
        "active": _boolean(effect.get("active"), f"{field}.active", default=True),
        "concentration": _boolean(effect.get("concentration"), f"{field}.concentration"),
        "duration": {
            "period": period,
            "remaining": _integer(
                duration.get("remaining"), f"{field}.duration.remaining", minimum=0
            ),
        },
        "changes": changes,
        "description": _text(effect.get("description"), f"{field}.description", maximum=1200),
    }


def validate_character_sheet(sheet: dict[str, Any]) -> dict[str, Any]:
    value = _merge_defaults(default_character_sheet(), _object(sheet, "sheet"))
    allowed = {
        "schema_version",
        "edition",
        "identity",
        "progression",
        "ability_generation",
        "abilities",
        "skills",
        "combat",
        "traits",
        "resources",
        "spellcasting",
        "content",
        "conditions",
        "effects",
        "adventure_state",
        "inventory",
    }
    _reject_unknown(value, "sheet", allowed)
    if _integer(value["schema_version"], "sheet.schema_version") != 2:
        raise ValueError("sheet.schema_version must be 2")
    edition = _text(value["edition"], "sheet.edition")
    if edition not in {"2014", "2024"}:
        raise ValueError("sheet.edition must be 2014 or 2024")
    ability_generation = normalize_ability_generation(value["ability_generation"], edition)

    identity = _object(value["identity"], "sheet.identity")
    _reject_unknown(
        identity,
        "sheet.identity",
        {
            "gender",
            "age",
            "height_cm",
            "weight_lb",
            "faith",
            "deity",
            "hair",
            "skin",
            "eyes",
            "portrait_uri",
        },
    )

    progression = _object(value["progression"], "sheet.progression")
    _reject_unknown(
        progression,
        "sheet.progression",
        {"level", "xp", "classes", "background", "background_grants", "species"},
    )
    classes = []
    for index, item in enumerate(_array(progression["classes"], "sheet.progression.classes")):
        entry = _object(item, f"sheet.progression.classes[{index}]")
        _reject_unknown(
            entry, f"sheet.progression.classes[{index}]", {"name", "level", "subclass", "hit_die"}
        )
        classes.append(
            {
                "name": _text(
                    entry.get("name"), f"sheet.progression.classes[{index}].name", maximum=200
                ),
                "level": _integer(
                    entry.get("level"),
                    f"sheet.progression.classes[{index}].level",
                    minimum=1,
                    maximum=20,
                ),
                "subclass": _text(
                    entry.get("subclass"),
                    f"sheet.progression.classes[{index}].subclass",
                    maximum=200,
                ),
                "hit_die": _integer(
                    entry.get("hit_die"),
                    f"sheet.progression.classes[{index}].hit_die",
                    minimum=1,
                    maximum=20,
                ),
            }
        )
    level = _integer(progression["level"], "sheet.progression.level", minimum=1, maximum=20)
    if classes and sum(item["level"] for item in classes) != level:
        raise ValueError("sheet.progression.level must equal the total class levels")
    background_grants = _object(
        progression["background_grants"], "sheet.progression.background_grants"
    )
    _reject_unknown(
        background_grants,
        "sheet.progression.background_grants",
        {"feature", "equipment_item_ids", "languages", "tools", "choices"},
    )

    abilities = _object(value["abilities"], "sheet.abilities")
    _reject_unknown(abilities, "sheet.abilities", set(ABILITY_NAMES))
    normalized_abilities = {
        ability: _normalize_ability(abilities[ability], f"sheet.abilities.{ability}")
        for ability in ABILITY_NAMES
    }
    skills = _object(value["skills"], "sheet.skills")
    _reject_unknown(skills, "sheet.skills", set(SKILL_ABILITIES))
    normalized_skills = {
        skill: _normalize_skill(skills[skill], f"sheet.skills.{skill}") for skill in SKILL_ABILITIES
    }

    combat = _object(value["combat"], "sheet.combat")
    _reject_unknown(
        combat,
        "sheet.combat",
        {
            "hp",
            "ac",
            "initiative",
            "speed",
            "hit_dice",
            "hp_progression",
            "death_saves",
            "exhaustion",
            "inspiration",
            "wounded",
        },
    )
    hp = _object(combat["hp"], "sheet.combat.hp")
    _reject_unknown(hp, "sheet.combat.hp", {"value", "max", "temp"})
    hp_max = _integer(hp["max"], "sheet.combat.hp.max", minimum=1)
    hp_value = _integer(hp["value"], "sheet.combat.hp.value", minimum=0)
    if hp_value > hp_max:
        raise ValueError("sheet.combat.hp.value cannot exceed max")
    ac = _object(combat["ac"], "sheet.combat.ac")
    _reject_unknown(ac, "sheet.combat.ac", {"base", "override"})
    initiative = _object(combat["initiative"], "sheet.combat.initiative")
    _reject_unknown(initiative, "sheet.combat.initiative", {"ability", "bonus"})
    initiative_ability = _text(initiative["ability"], "sheet.combat.initiative.ability")
    if initiative_ability not in ABILITY_NAMES:
        raise ValueError("sheet.combat.initiative.ability is invalid")
    speed = _object(combat["speed"], "sheet.combat.speed")
    _reject_unknown(speed, "sheet.combat.speed", {"walk", "fly", "swim", "climb", "burrow"})
    hit_dice = _object(combat["hit_dice"], "sheet.combat.hit_dice")
    normalized_hit_dice = {
        key: _normalize_resource(item, f"sheet.combat.hit_dice.{key}")
        for key, item in hit_dice.items()
    }
    hp_progression = []
    recorded_hp_levels: set[int] = set()
    for index, gain in enumerate(_array(combat["hp_progression"], "sheet.combat.hp_progression")):
        entry = _object(gain, f"sheet.combat.hp_progression[{index}]")
        _reject_unknown(
            entry,
            f"sheet.combat.hp_progression[{index}]",
            {"level", "method", "value", "source"},
        )
        gain_level = _integer(
            entry.get("level"), f"sheet.combat.hp_progression[{index}].level", minimum=1, maximum=20
        )
        if gain_level in recorded_hp_levels:
            raise ValueError("sheet.combat.hp_progression has duplicate levels")
        recorded_hp_levels.add(gain_level)
        method = _text(
            entry.get("method"), f"sheet.combat.hp_progression[{index}].method", default="manual"
        )
        if method not in {"fixed", "rolled", "manual"}:
            raise ValueError(f"sheet.combat.hp_progression[{index}].method is invalid")
        hp_progression.append(
            {
                "level": gain_level,
                "method": method,
                "value": _integer(
                    entry.get("value"), f"sheet.combat.hp_progression[{index}].value", minimum=0
                ),
                "source": _text(
                    entry.get("source"), f"sheet.combat.hp_progression[{index}].source", maximum=300
                ),
            }
        )
    death_saves = _object(combat["death_saves"], "sheet.combat.death_saves")
    _reject_unknown(death_saves, "sheet.combat.death_saves", {"successes", "failures"})

    traits = _object(value["traits"], "sheet.traits")
    _reject_unknown(
        traits,
        "sheet.traits",
        {
            "size",
            "alignment",
            "languages",
            "proficiencies",
            "resistances",
            "immunities",
            "vulnerabilities",
            "condition_immunities",
            "senses",
        },
    )
    proficiencies = _object(traits["proficiencies"], "sheet.traits.proficiencies")
    _reject_unknown(proficiencies, "sheet.traits.proficiencies", {"armor", "weapons", "tools"})
    senses = _object(traits["senses"], "sheet.traits.senses")
    _reject_unknown(senses, "sheet.traits.senses", {*SENSE_NAMES, "passive_perception_bonus"})

    resources = _object(value["resources"], "sheet.resources")
    normalized_resources = {
        key: _normalize_resource(item, f"sheet.resources.{key}") for key, item in resources.items()
    }
    spellcasting = _object(value["spellcasting"], "sheet.spellcasting")
    _reject_unknown(
        spellcasting,
        "sheet.spellcasting",
        {
            "ability",
            "spell_slots",
            "pact_magic",
            "preparation",
            "ritual_casting",
            "spellbook",
            "casting_economy",
            "spell_points",
        },
    )
    spell_ability = spellcasting["ability"]
    if spell_ability is not None and spell_ability not in ABILITY_NAMES:
        raise ValueError("sheet.spellcasting.ability is invalid")
    slots = _object(spellcasting["spell_slots"], "sheet.spellcasting.spell_slots")
    normalized_slots = {
        key: _normalize_resource(item, f"sheet.spellcasting.spell_slots.{key}")
        for key, item in slots.items()
    }
    pact_magic = spellcasting["pact_magic"]
    if pact_magic is not None:
        pact_magic = _normalize_resource(pact_magic, "sheet.spellcasting.pact_magic")
    casting_economy = _text(
        spellcasting["casting_economy"], "sheet.spellcasting.casting_economy", default="slots"
    )
    if casting_economy not in {"slots", "spell_points"}:
        raise ValueError("sheet.spellcasting.casting_economy is invalid")
    spell_points = spellcasting["spell_points"]
    if spell_points is not None:
        spell_points = _normalize_resource(spell_points, "sheet.spellcasting.spell_points")
    if casting_economy == "spell_points" and spell_points is None:
        raise ValueError("sheet.spellcasting.spell_points is required for spell_points casting")
    preparation = _object(spellcasting["preparation"], "sheet.spellcasting.preparation")
    _reject_unknown(
        preparation,
        "sheet.spellcasting.preparation",
        {"mode", "max_prepared", "changes_on", "selected_spell_ids"},
    )
    preparation_mode = _text(preparation["mode"], "sheet.spellcasting.preparation.mode")
    if preparation_mode not in {"none", "known", "prepared", "spellbook"}:
        raise ValueError("sheet.spellcasting.preparation.mode is invalid")
    changes_on = _text(preparation["changes_on"], "sheet.spellcasting.preparation.changes_on")
    if changes_on not in {"long_rest", "manual"}:
        raise ValueError("sheet.spellcasting.preparation.changes_on is invalid")
    spellbook = _object(spellcasting["spellbook"], "sheet.spellcasting.spellbook")
    _reject_unknown(spellbook, "sheet.spellcasting.spellbook", {"enabled", "spell_ids"})

    content = _object(value["content"], "sheet.content")
    _reject_unknown(content, "sheet.content", {"spells", "features", "feats", "activities"})
    spells = [
        _normalize_spell(item, f"sheet.content.spells[{index}]")
        for index, item in enumerate(_array(content["spells"], "sheet.content.spells"))
    ]
    spell_ids = {spell["id"] for spell in spells}
    if len(spell_ids) != len(spells):
        raise ValueError("sheet.content.spells contains duplicate ids")
    selected_spell_ids = _string_list(
        preparation["selected_spell_ids"], "sheet.spellcasting.preparation.selected_spell_ids"
    )
    if len(selected_spell_ids) != len(set(selected_spell_ids)):
        raise ValueError("sheet.spellcasting.preparation.selected_spell_ids contains duplicates")
    if len(selected_spell_ids) > _integer(
        preparation["max_prepared"], "sheet.spellcasting.preparation.max_prepared", minimum=0
    ):
        raise ValueError("prepared spell selection exceeds max_prepared")
    for spell_id in selected_spell_ids:
        spell = next((item for item in spells if item["id"] == spell_id), None)
        if spell is None:
            raise ValueError("prepared spell selection references an unknown spell")
        if not (spell["access"]["known"] or spell["access"]["in_spellbook"]):
            raise ValueError("prepared spell must be known or in the spellbook")
    for spell in spells:
        spell["access"]["prepared"] = (
            spell["id"] in selected_spell_ids or spell["access"]["always_prepared"]
        )
    spellbook_ids = _string_list(spellbook["spell_ids"], "sheet.spellcasting.spellbook.spell_ids")
    if not set(spellbook_ids).issubset(spell_ids):
        raise ValueError("spellbook references an unknown spell")

    def _content_entries(name: str) -> list[dict[str, Any]]:
        result = []
        for index, item in enumerate(_array(content[name], f"sheet.content.{name}")):
            entry = _object(item, f"sheet.content.{name}[{index}]")
            _reject_unknown(
                entry,
                f"sheet.content.{name}[{index}]",
                {
                    "id",
                    "name",
                    "source_key",
                    "description",
                    "uses",
                    "resource_key",
                    "activation",
                    "scaling",
                    "choices",
                },
            )
            activation = _object(
                entry.get("activation") or {}, f"sheet.content.{name}[{index}].activation"
            )
            _reject_unknown(
                activation,
                f"sheet.content.{name}[{index}].activation",
                {"type", "cost", "trigger"},
            )
            activation_type = _text(
                activation.get("type"),
                f"sheet.content.{name}[{index}].activation.type",
                default="passive",
            )
            if activation_type not in {"passive", "action", "bonus_action", "reaction", "special"}:
                raise ValueError(f"sheet.content.{name}[{index}].activation.type is invalid")
            scaling = []
            for scale_index, scale in enumerate(
                _array(entry.get("scaling") or [], f"sheet.content.{name}[{index}].scaling")
            ):
                scale_entry = _object(
                    scale, f"sheet.content.{name}[{index}].scaling[{scale_index}]"
                )
                _reject_unknown(
                    scale_entry,
                    f"sheet.content.{name}[{index}].scaling[{scale_index}]",
                    {"level", "value", "description"},
                )
                scaling.append(
                    {
                        "level": _integer(
                            scale_entry.get("level"),
                            f"sheet.content.{name}[{index}].scaling[{scale_index}].level",
                            minimum=1,
                            maximum=20,
                        ),
                        "value": _integer(
                            scale_entry.get("value"),
                            f"sheet.content.{name}[{index}].scaling[{scale_index}].value",
                        ),
                        "description": _text(
                            scale_entry.get("description"),
                            f"sheet.content.{name}[{index}].scaling[{scale_index}].description",
                            maximum=1000,
                        ),
                    }
                )
            result.append(
                {
                    "id": _text(
                        entry.get("id"),
                        f"sheet.content.{name}[{index}].id",
                        default=_uuid(),
                        maximum=100,
                    ),
                    "name": _text(
                        entry.get("name"), f"sheet.content.{name}[{index}].name", maximum=300
                    ),
                    "source_key": _text(
                        entry.get("source_key"),
                        f"sheet.content.{name}[{index}].source_key",
                        maximum=300,
                    ),
                    "description": _text(
                        entry.get("description"),
                        f"sheet.content.{name}[{index}].description",
                        maximum=2000,
                    ),
                    "uses": _normalize_resource(
                        entry.get("uses") or {}, f"sheet.content.{name}[{index}].uses"
                    ),
                    "resource_key": _text(
                        entry.get("resource_key"),
                        f"sheet.content.{name}[{index}].resource_key",
                        maximum=200,
                    ),
                    "activation": {
                        "type": activation_type,
                        "cost": _integer(
                            activation.get("cost"),
                            f"sheet.content.{name}[{index}].activation.cost",
                            minimum=0,
                        ),
                        "trigger": _text(
                            activation.get("trigger"),
                            f"sheet.content.{name}[{index}].activation.trigger",
                            maximum=1000,
                        ),
                    },
                    "scaling": scaling,
                    "choices": _object(
                        entry.get("choices") or {}, f"sheet.content.{name}[{index}].choices"
                    ),
                }
            )
        return result

    conditions = _string_list(value["conditions"], "sheet.conditions")
    effects = [
        _normalize_effect(item, f"sheet.effects[{index}]")
        for index, item in enumerate(_array(value["effects"], "sheet.effects"))
    ]
    effect_ids = {effect["id"] for effect in effects}
    if len(effect_ids) != len(effects):
        raise ValueError("sheet.effects contains duplicate ids")
    active_concentration = [
        effect for effect in effects if effect["active"] and effect["concentration"]
    ]
    if len(active_concentration) > 1:
        raise ValueError("a character can have only one active concentration effect")
    for effect in effects:
        source_spell_id = effect["source_spell_id"]
        if source_spell_id and source_spell_id not in spell_ids:
            raise ValueError("effect source_spell_id references an unknown spell")

    adventure_state = _object(value["adventure_state"], "sheet.adventure_state")
    _reject_unknown(
        adventure_state,
        "sheet.adventure_state",
        {"reputation", "contributions", "blessings", "wards", "legendary_boons", "status_tags"},
    )
    reputation = _object(adventure_state["reputation"], "sheet.adventure_state.reputation")
    contributions = _object(adventure_state["contributions"], "sheet.adventure_state.contributions")
    inventory = validate_inventory(value["inventory"])
    background_item_ids = _string_list(
        background_grants["equipment_item_ids"],
        "sheet.progression.background_grants.equipment_item_ids",
    )
    inventory_item_ids = {item["id"] for item in inventory["items"]}
    if not set(background_item_ids).issubset(inventory_item_ids):
        raise ValueError("background equipment references an unknown inventory item")

    return {
        "schema_version": 2,
        "edition": edition,
        "identity": {
            "gender": _text(identity["gender"], "sheet.identity.gender", maximum=100),
            "age": _text(identity["age"], "sheet.identity.age", maximum=100),
            "height_cm": (
                _integer(identity["height_cm"], "sheet.identity.height_cm", minimum=1)
                if identity["height_cm"] is not None
                else None
            ),
            "weight_lb": (
                _integer(identity["weight_lb"], "sheet.identity.weight_lb", minimum=1)
                if identity["weight_lb"] is not None
                else None
            ),
            "faith": _text(identity["faith"], "sheet.identity.faith", maximum=200),
            "deity": _text(identity["deity"], "sheet.identity.deity", maximum=200),
            "hair": _text(identity["hair"], "sheet.identity.hair", maximum=100),
            "skin": _text(identity["skin"], "sheet.identity.skin", maximum=100),
            "eyes": _text(identity["eyes"], "sheet.identity.eyes", maximum=100),
            "portrait_uri": _text(
                identity["portrait_uri"], "sheet.identity.portrait_uri", maximum=2000
            ),
        },
        "ability_generation": ability_generation,
        "progression": {
            "level": level,
            "xp": _integer(progression["xp"], "sheet.progression.xp", minimum=0),
            "classes": classes,
            "background": _text(
                progression["background"], "sheet.progression.background", maximum=200
            ),
            "background_grants": {
                "feature": _text(
                    background_grants["feature"],
                    "sheet.progression.background_grants.feature",
                    maximum=300,
                ),
                "equipment_item_ids": background_item_ids,
                "languages": _string_list(
                    background_grants["languages"], "sheet.progression.background_grants.languages"
                ),
                "tools": _string_list(
                    background_grants["tools"], "sheet.progression.background_grants.tools"
                ),
                "choices": _object(
                    background_grants["choices"], "sheet.progression.background_grants.choices"
                ),
            },
            "species": _text(progression["species"], "sheet.progression.species", maximum=200),
        },
        "abilities": normalized_abilities,
        "skills": normalized_skills,
        "combat": {
            "hp": {
                "value": hp_value,
                "max": hp_max,
                "temp": _integer(hp["temp"], "sheet.combat.hp.temp", minimum=0),
            },
            "ac": {
                "base": _integer(ac["base"], "sheet.combat.ac.base", minimum=0),
                "override": (
                    _integer(ac["override"], "sheet.combat.ac.override", minimum=0)
                    if ac["override"] is not None
                    else None
                ),
            },
            "initiative": {
                "ability": initiative_ability,
                "bonus": _integer(initiative["bonus"], "sheet.combat.initiative.bonus"),
            },
            "speed": {
                mode: _integer(speed[mode], f"sheet.combat.speed.{mode}", minimum=0)
                for mode in ("walk", "fly", "swim", "climb", "burrow")
            },
            "hit_dice": normalized_hit_dice,
            "hp_progression": hp_progression,
            "death_saves": {
                "successes": _integer(
                    death_saves["successes"],
                    "sheet.combat.death_saves.successes",
                    minimum=0,
                    maximum=3,
                ),
                "failures": _integer(
                    death_saves["failures"],
                    "sheet.combat.death_saves.failures",
                    minimum=0,
                    maximum=3,
                ),
            },
            "exhaustion": _integer(
                combat["exhaustion"], "sheet.combat.exhaustion", minimum=0, maximum=6
            ),
            "inspiration": _boolean(combat["inspiration"], "sheet.combat.inspiration"),
            "wounded": _boolean(combat["wounded"], "sheet.combat.wounded"),
        },
        "traits": {
            "size": _text(traits["size"], "sheet.traits.size", maximum=100),
            "alignment": _text(traits["alignment"], "sheet.traits.alignment", maximum=100),
            "languages": _string_list(traits["languages"], "sheet.traits.languages"),
            "proficiencies": {
                key: _string_list(proficiencies[key], f"sheet.traits.proficiencies.{key}")
                for key in ("armor", "weapons", "tools")
            },
            "resistances": _string_list(traits["resistances"], "sheet.traits.resistances"),
            "immunities": _string_list(traits["immunities"], "sheet.traits.immunities"),
            "vulnerabilities": _string_list(
                traits["vulnerabilities"], "sheet.traits.vulnerabilities"
            ),
            "condition_immunities": _string_list(
                traits["condition_immunities"], "sheet.traits.condition_immunities"
            ),
            "senses": {
                sense: _integer(senses[sense], f"sheet.traits.senses.{sense}", minimum=0)
                for sense in SENSE_NAMES
            }
            | {
                "passive_perception_bonus": _integer(
                    senses["passive_perception_bonus"],
                    "sheet.traits.senses.passive_perception_bonus",
                ),
            },
        },
        "resources": normalized_resources,
        "spellcasting": {
            "ability": spell_ability,
            "spell_slots": normalized_slots,
            "pact_magic": pact_magic,
            "casting_economy": casting_economy,
            "spell_points": spell_points,
            "preparation": {
                "mode": preparation_mode,
                "max_prepared": _integer(
                    preparation["max_prepared"],
                    "sheet.spellcasting.preparation.max_prepared",
                    minimum=0,
                ),
                "changes_on": changes_on,
                "selected_spell_ids": selected_spell_ids,
            },
            "ritual_casting": _boolean(
                spellcasting["ritual_casting"], "sheet.spellcasting.ritual_casting"
            ),
            "spellbook": {
                "enabled": _boolean(spellbook["enabled"], "sheet.spellcasting.spellbook.enabled"),
                "spell_ids": spellbook_ids,
            },
        },
        "content": {
            "spells": spells,
            "features": _content_entries("features"),
            "feats": _content_entries("feats"),
            "activities": _content_entries("activities"),
        },
        "conditions": conditions,
        "effects": effects,
        "adventure_state": {
            "reputation": {
                key: _integer(entry, f"sheet.adventure_state.reputation.{key}")
                for key, entry in reputation.items()
            },
            "contributions": {
                key: _integer(entry, f"sheet.adventure_state.contributions.{key}")
                for key, entry in contributions.items()
            },
            "blessings": _string_list(
                adventure_state["blessings"], "sheet.adventure_state.blessings"
            ),
            "wards": _string_list(adventure_state["wards"], "sheet.adventure_state.wards"),
            "legendary_boons": _string_list(
                adventure_state["legendary_boons"], "sheet.adventure_state.legendary_boons"
            ),
            "status_tags": _string_list(
                adventure_state["status_tags"], "sheet.adventure_state.status_tags"
            ),
        },
        "inventory": inventory,
    }


def validate_character_notes(
    notes: dict[str, Any], *, character_type: str | None = None
) -> dict[str, Any]:
    value = _merge_defaults(default_character_notes(), _object(notes, "notes"))
    _reject_unknown(
        value, "notes", {"schema_version", "profile", "memories", "relationships", "goals"}
    )
    if _integer(value["schema_version"], "notes.schema_version") != 2:
        raise ValueError("notes.schema_version must be 2")
    profile = _object(value["profile"], "notes.profile")
    _reject_unknown(
        profile,
        "notes.profile",
        {
            "summary",
            "appearance",
            "personality_traits",
            "ideals",
            "bonds",
            "flaws",
            "motivation",
            "backstory",
            "dm_notes",
        },
    )
    memories = []
    for index, item in enumerate(_array(value["memories"], "notes.memories")):
        memory = _object(item, f"notes.memories[{index}]")
        _reject_unknown(
            memory,
            f"notes.memories[{index}]",
            {
                "id",
                "kind",
                "summary",
                "importance",
                "participants",
                "source_event_id",
                "visibility",
                "status",
            },
        )
        visibility = _text(
            memory.get("visibility"), f"notes.memories[{index}].visibility", default="dm"
        )
        if visibility not in {"dm", "party", "public"}:
            raise ValueError("memory visibility is invalid")
        status = _text(memory.get("status"), f"notes.memories[{index}].status", default="active")
        if status not in {"active", "resolved", "superseded"}:
            raise ValueError("memory status is invalid")
        memories.append(
            {
                "id": _text(
                    memory.get("id"), f"notes.memories[{index}].id", default=_uuid(), maximum=100
                ),
                "kind": _text(
                    memory.get("kind"), f"notes.memories[{index}].kind", default="fact", maximum=100
                ),
                "summary": _text(
                    memory.get("summary"), f"notes.memories[{index}].summary", maximum=1200
                ),
                "importance": _integer(
                    memory.get("importance"),
                    f"notes.memories[{index}].importance",
                    default=3,
                    minimum=1,
                    maximum=5,
                ),
                "participants": _string_list(
                    memory.get("participants") or [], f"notes.memories[{index}].participants"
                ),
                "source_event_id": _text(
                    memory.get("source_event_id"),
                    f"notes.memories[{index}].source_event_id",
                    maximum=100,
                ),
                "visibility": visibility,
                "status": status,
            }
        )
    normalized = {
        "schema_version": 2,
        "profile": {
            "summary": _text(profile["summary"], "notes.profile.summary", maximum=1200),
            "appearance": _text(profile["appearance"], "notes.profile.appearance", maximum=1200),
            "personality_traits": _string_list(
                profile["personality_traits"], "notes.profile.personality_traits"
            ),
            "ideals": _string_list(profile["ideals"], "notes.profile.ideals"),
            "bonds": _string_list(profile["bonds"], "notes.profile.bonds"),
            "flaws": _string_list(profile["flaws"], "notes.profile.flaws"),
            "motivation": _text(profile["motivation"], "notes.profile.motivation", maximum=1200),
            "backstory": _text(profile["backstory"], "notes.profile.backstory", maximum=8000),
            "dm_notes": _text(profile["dm_notes"], "notes.profile.dm_notes", maximum=4000),
        },
        "memories": memories,
        "relationships": [
            _object(item, "notes.relationships[]")
            for item in _array(value["relationships"], "notes.relationships")
        ],
        "goals": [_object(item, "notes.goals[]") for item in _array(value["goals"], "notes.goals")],
    }
    if character_type in {"npc", "monster"} and not normalized["profile"]["summary"]:
        raise ValueError(f"{character_type} notes.profile.summary is required")
    return normalized


def validate_party_state(state: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(_object(state, "campaign.state"))
    party = _object(value.get("party") or {}, "campaign.state.party")
    _reject_unknown(party, "campaign.state.party", {"inventory", "notes"})
    value["party"] = {
        "inventory": validate_inventory(party.get("inventory") or {}),
        "notes": _text(party.get("notes"), "campaign.state.party.notes", maximum=1200),
    }
    return value


def _derive_armor_class(
    value: dict[str, Any], ability_modifiers: dict[str, int], active_effects: list[dict[str, Any]]
) -> tuple[int, dict[str, Any], set[str]]:
    inventory = value["inventory"]
    items = {item["id"]: item for item in inventory["items"]}
    ac = value["combat"]["ac"]
    override = ac["override"]
    breakdown: dict[str, Any] = {
        "mode": "override" if override is not None else "base",
        "base": override if override is not None else ac["base"],
        "armor": None,
        "shield": None,
        "magic_items": [],
        "effects": [],
    }
    total = breakdown["base"]
    if override is None:
        armor_id = inventory["equipment_slots"]["armor"]
        if armor_id:
            armor = items[armor_id]
            mechanics = armor["mechanics"]
            dexterity_modifier = ability_modifiers["dexterity"]
            dexterity_mode = mechanics["dexterity_mode"]
            if dexterity_mode == "none":
                dexterity_bonus = 0
            elif dexterity_mode == "full":
                dexterity_bonus = dexterity_modifier
            else:
                dexterity_bonus = min(dexterity_modifier, mechanics["dexterity_max"])
            total = mechanics["base_ac"] + dexterity_bonus + mechanics["magic_bonus"]
            breakdown["mode"] = "armor"
            breakdown["base"] = mechanics["base_ac"]
            breakdown["armor"] = {
                "item_id": armor_id,
                "name": armor["name"],
                "dexterity_bonus": dexterity_bonus,
                "magic_bonus": mechanics["magic_bonus"],
            }
        shield_id = inventory["equipment_slots"]["shield"]
        if shield_id:
            shield = items[shield_id]
            mechanics = shield["mechanics"]
            bonus = mechanics["ac_bonus"] + mechanics["magic_bonus"]
            total += bonus
            breakdown["shield"] = {
                "item_id": shield_id,
                "name": shield["name"],
                "bonus": bonus,
            }
        for item in inventory["items"]:
            if item["kind"] != "magic_item" or not item["equipped"]:
                continue
            bonus = item["mechanics"].get("ac_bonus", 0)
            if bonus:
                total += bonus
                breakdown["magic_items"].append(
                    {"item_id": item["id"], "name": item["name"], "bonus": bonus}
                )

    unresolved_effects: set[str] = set()
    for effect in active_effects:
        for change in effect["changes"]:
            if change["path"] not in {"derived.armor_class", "combat.ac"}:
                unresolved_effects.add(effect["id"])
                continue
            if (
                change["mode"] not in {"add", "override"}
                or isinstance(change["value"], bool)
                or not isinstance(change["value"], int)
            ):
                unresolved_effects.add(effect["id"])
                continue
            if change["mode"] == "add":
                total += change["value"]
            else:
                total = change["value"]
            breakdown["effects"].append(
                {
                    "effect_id": effect["id"],
                    "name": effect["name"],
                    "mode": change["mode"],
                    "value": change["value"],
                }
            )
    breakdown["total"] = total
    return total, breakdown, unresolved_effects


def _inventory_weight_oz(inventory: dict[str, Any]) -> float:
    """Return carried weight after extra-dimensional container exceptions."""
    items = {item["id"]: item for item in inventory["items"]}

    def weight(item_id: str) -> float:
        item = items[item_id]
        own_weight = item["weight_oz"] * item["quantity"]
        contents = sum(
            weight(child["id"]) for child in items.values() if child["container_id"] == item_id
        )
        if item["kind"] == "container" and item["mechanics"]["weightless_contents"]:
            contents = 0
        return own_weight + contents

    total = sum(weight(item_id) for item_id, item in items.items() if item["container_id"] is None)
    if not inventory["encumbrance"]["ignore_currency_weight"]:
        total += sum(inventory["wallet"].values()) * 0.32  # 50 coins per pound.
    return total


def _weapon_attacks(
    inventory: dict[str, Any],
    ability_modifiers: dict[str, int],
    proficiency: int,
    spell_ability: str | None,
) -> list[dict[str, Any]]:
    attacks = []
    for item in inventory["items"]:
        if item["kind"] != "weapon":
            continue
        mechanics = item["mechanics"]
        ability = mechanics["attack_ability"]
        modifier = (
            ability_modifiers.get(spell_ability or "", 0)
            if ability == "spell"
            else ability_modifiers.get(ability, 0)
        )
        attack_bonus = modifier + mechanics["magic_bonus"]
        if mechanics["proficient"]:
            attack_bonus += proficiency
        damage_bonus = modifier + mechanics["magic_bonus"]
        damage_formula = mechanics["damage_formula"]
        damage_expression = damage_formula
        if damage_formula and damage_bonus:
            damage_expression = (
                f"{damage_formula} {'+' if damage_bonus > 0 else '-'} {abs(damage_bonus)}"
            )
        attacks.append(
            {
                "item_id": item["id"],
                "name": item["name"],
                "equipped_slot": item["equipped_slot"],
                "attack_type": mechanics["attack_type"],
                "attack_ability": ability,
                "attack_bonus": attack_bonus,
                "damage_formula": damage_formula,
                "damage_bonus": damage_bonus,
                "damage_expression": damage_expression,
                "damage_type": mechanics["damage_type"],
                "versatile_damage_formula": mechanics["versatile_damage_formula"],
                "properties": mechanics["properties"],
                "range_ft": {
                    "normal": mechanics["normal_range_ft"],
                    "long": mechanics["long_range_ft"],
                },
                "thrown_range_ft": {
                    "normal": mechanics["thrown_normal_range_ft"],
                    "long": mechanics["thrown_long_range_ft"],
                },
                "ammunition_item_id": mechanics["ammunition_item_id"],
            }
        )
    return attacks


def derive_character_sheet(sheet: dict[str, Any]) -> dict[str, Any]:
    value = validate_character_sheet(sheet)
    level = value["progression"]["level"]
    proficiency = 2 + (level - 1) // 4
    ability_modifiers = {
        ability: (entry["score"] - 10) // 2 for ability, entry in value["abilities"].items()
    }
    saves = {
        ability: ability_modifiers[ability]
        + entry["bonus"]
        + (proficiency if entry["save_proficient"] else 0)
        for ability, entry in value["abilities"].items()
    }
    multipliers = {"none": 0, "half": 0.5, "proficient": 1, "expertise": 2}
    skills = {
        skill: ability_modifiers[SKILL_ABILITIES[skill]]
        + entry["bonus"]
        + int(proficiency * multipliers[entry["proficiency"]])
        for skill, entry in value["skills"].items()
    }
    inventory = value["inventory"]
    total_weight = _inventory_weight_oz(inventory)
    wallet_cp = sum(
        inventory["wallet"][name] * multiplier
        for name, multiplier in {"cp": 1, "sp": 10, "ep": 50, "gp": 100, "pp": 1000}.items()
    )
    spell_ability = value["spellcasting"]["ability"]
    active_effects = [effect for effect in value["effects"] if effect["active"]]
    armor_class, armor_class_breakdown, unresolved_effects = _derive_armor_class(
        value, ability_modifiers, active_effects
    )
    size_multiplier = {
        "tiny": 0.5,
        "small": 1,
        "medium": 1,
        "large": 2,
        "huge": 4,
        "gargantuan": 8,
    }.get(value["traits"]["size"].lower(), 1)
    strength = value["abilities"]["strength"]["score"]
    maximum_weight = strength * 240 * size_multiplier
    encumbrance = inventory["encumbrance"]
    encumbrance_summary = {
        "mode": encumbrance["mode"],
        "carried_weight_oz": total_weight,
        "light_threshold_oz": strength * 80 * size_multiplier,
        "heavy_threshold_oz": strength * 160 * size_multiplier,
        "maximum_oz": maximum_weight,
        "state": (
            "over_capacity"
            if total_weight > maximum_weight
            else "heavily_encumbered"
            if encumbrance["mode"] == "variant" and total_weight > strength * 160 * size_multiplier
            else "encumbered"
            if encumbrance["mode"] == "variant" and total_weight > strength * 80 * size_multiplier
            else "normal"
        ),
    }
    return {
        "proficiency_bonus": proficiency,
        "ability_modifiers": ability_modifiers,
        "saving_throws": saves,
        "skills": skills,
        "passive_perception": 10
        + skills["perception"]
        + value["traits"]["senses"]["passive_perception_bonus"],
        "armor_class": armor_class,
        "armor_class_breakdown": armor_class_breakdown,
        "initiative": ability_modifiers[value["combat"]["initiative"]["ability"]]
        + value["combat"]["initiative"]["bonus"],
        "hit_points": dict(value["combat"]["hp"]),
        "hit_point_progression": {
            "gains": list(value["combat"]["hp_progression"]),
            "recorded_gain_total": sum(gain["value"] for gain in value["combat"]["hp_progression"]),
        },
        "speed": dict(value["combat"]["speed"]),
        "spellcasting": (
            {
                "ability": spell_ability,
                "attack_bonus": ability_modifiers[spell_ability] + proficiency,
                "save_dc": 8 + ability_modifiers[spell_ability] + proficiency,
                "prepared_spell_ids": [
                    spell["id"]
                    for spell in value["content"]["spells"]
                    if spell["access"]["prepared"]
                ],
            }
            if spell_ability
            else None
        ),
        "inventory": {
            "total_weight_oz": total_weight,
            "wallet_value_cp": wallet_cp,
            "encumbrance": encumbrance_summary,
            "weapon_attacks": _weapon_attacks(
                inventory, ability_modifiers, proficiency, spell_ability
            ),
        },
        "active_effects": [
            {"id": effect["id"], "name": effect["name"]} for effect in active_effects
        ],
        "unresolved_rules": sorted(unresolved_effects),
    }


def add_inventory_item(sheet: dict[str, Any], item: dict[str, Any]) -> tuple[dict[str, Any], str]:
    value = validate_character_sheet(sheet)
    entry = _normalize_item(item, "item")
    if any(current["id"] == entry["id"] for current in value["inventory"]["items"]):
        raise ValueError("item id already exists in inventory")
    value["inventory"]["items"].append(entry)
    return validate_character_sheet(value), entry["id"]


def update_inventory_item(
    sheet: dict[str, Any], item_id: str, patch: dict[str, Any]
) -> dict[str, Any]:
    value = validate_character_sheet(sheet)
    item = next((entry for entry in value["inventory"]["items"] if entry["id"] == item_id), None)
    if item is None:
        raise LookupError(item_id)
    replacement = {**item, **_object(patch, "item patch"), "id": item_id}
    replacement = _normalize_item(replacement, "item", generate_id=False)
    index = value["inventory"]["items"].index(item)
    value["inventory"]["items"][index] = replacement
    return validate_character_sheet(value)


def remove_inventory_item(
    sheet: dict[str, Any], item_id: str, quantity: int | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    value = validate_character_sheet(sheet)
    items = value["inventory"]["items"]
    item = next((entry for entry in items if entry["id"] == item_id), None)
    if item is None:
        raise LookupError(item_id)
    count = quantity if quantity is not None else item["quantity"]
    count = _integer(count, "quantity", minimum=1)
    if count > item["quantity"]:
        raise ValueError("quantity exceeds the item stack")
    moved = copy.deepcopy(item)
    moved["quantity"] = count
    if count == item["quantity"]:
        if any(entry["container_id"] == item_id for entry in items):
            raise ValueError("cannot remove a container while it still has contents")
        items.remove(item)
        for slot, equipped_id in value["inventory"]["equipment_slots"].items():
            if equipped_id == item_id:
                value["inventory"]["equipment_slots"][slot] = None
    else:
        item["quantity"] -= count
        moved["id"] = _uuid()
    return validate_character_sheet(value), moved


def consume_weapon_ammunition(
    sheet: dict[str, Any], weapon_id: str, quantity: int = 1
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Consume the ammunition linked by a weapon's structured mechanics."""
    value = validate_character_sheet(sheet)
    weapon = next((item for item in value["inventory"]["items"] if item["id"] == weapon_id), None)
    if weapon is None or weapon["kind"] != "weapon":
        raise ValueError("weapon_id must reference a weapon in inventory")
    ammunition_item_id = weapon["mechanics"]["ammunition_item_id"]
    if ammunition_item_id is None:
        raise ValueError("weapon has no linked ammunition")
    updated, removed = remove_inventory_item(value, ammunition_item_id, quantity)
    return updated, {
        "item_id": ammunition_item_id,
        "name": removed["name"],
        "quantity": removed["quantity"],
    }


def receive_inventory_item(sheet: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    value = validate_character_sheet(sheet)
    entry = _normalize_item(item, "item", generate_id=False)
    if any(current["id"] == entry["id"] for current in value["inventory"]["items"]):
        entry["id"] = _uuid()
    entry["container_id"] = None
    entry["equipped"] = False
    entry["equipped_slot"] = None
    value["inventory"]["items"].append(entry)
    return validate_character_sheet(value)


def adjust_wallet(sheet: dict[str, Any], denomination: str, amount: int) -> dict[str, Any]:
    if denomination not in DENOMINATIONS:
        raise ValueError("denomination must be cp, sp, ep, gp, or pp")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount == 0:
        raise ValueError("amount must be a non-zero integer")
    value = validate_character_sheet(sheet)
    wallet = value["inventory"]["wallet"]
    if wallet[denomination] + amount < 0:
        raise ValueError("wallet balance cannot be negative")
    wallet[denomination] += amount
    return validate_character_sheet(value)


def equip_inventory_item(sheet: dict[str, Any], item_id: str, slot: str | None) -> dict[str, Any]:
    value = validate_character_sheet(sheet)
    item = next((entry for entry in value["inventory"]["items"] if entry["id"] == item_id), None)
    if item is None:
        raise LookupError(item_id)
    if slot is not None and slot not in EQUIPMENT_SLOTS:
        raise ValueError("invalid equipment slot")
    if slot is not None:
        _validate_item_slot(item, slot)
    for key, current_id in value["inventory"]["equipment_slots"].items():
        if current_id == item_id:
            value["inventory"]["equipment_slots"][key] = None
    item["equipped"] = slot is not None
    item["equipped_slot"] = slot
    if slot is not None:
        previous_id = value["inventory"]["equipment_slots"][slot]
        if previous_id:
            previous = next(
                entry for entry in value["inventory"]["items"] if entry["id"] == previous_id
            )
            previous["equipped"] = False
            previous["equipped_slot"] = None
        value["inventory"]["equipment_slots"][slot] = item_id
    return validate_character_sheet(value)


def add_effect(sheet: dict[str, Any], effect: dict[str, Any]) -> tuple[dict[str, Any], str]:
    value = validate_character_sheet(sheet)
    entry = _normalize_effect(effect, "effect")
    if any(current["id"] == entry["id"] for current in value["effects"]):
        raise ValueError("effect id already exists")
    value["effects"].append(entry)
    return validate_character_sheet(value), entry["id"]


def remove_effect(sheet: dict[str, Any], effect_id: str) -> dict[str, Any]:
    value = validate_character_sheet(sheet)
    effects = value["effects"]
    effect = next((entry for entry in effects if entry["id"] == effect_id), None)
    if effect is None:
        raise LookupError(effect_id)
    effects.remove(effect)
    return validate_character_sheet(value)


def set_spell_prepared(sheet: dict[str, Any], spell_id: str, prepared: bool) -> dict[str, Any]:
    value = validate_character_sheet(sheet)
    preparation = value["spellcasting"]["preparation"]
    if preparation["mode"] not in {"prepared", "spellbook"}:
        raise ValueError("this character does not prepare spells")
    spell = next((entry for entry in value["content"]["spells"] if entry["id"] == spell_id), None)
    if spell is None:
        raise LookupError(spell_id)
    selected = preparation["selected_spell_ids"]
    if prepared:
        if spell_id not in selected:
            if len(selected) >= preparation["max_prepared"]:
                raise ValueError("prepared spell selection exceeds max_prepared")
            selected.append(spell_id)
    elif spell_id in selected:
        selected.remove(spell_id)
    return validate_character_sheet(value)


def set_resource_value(sheet: dict[str, Any], key: str, value: int) -> dict[str, Any]:
    result = validate_character_sheet(sheet)
    resource = result["resources"].get(key)
    if resource is None:
        raise LookupError(key)
    resource["value"] = _integer(value, "resource value", minimum=0)
    if resource["value"] > resource["max"]:
        raise ValueError("resource value cannot exceed max")
    return validate_character_sheet(result)


def add_memory(notes: dict[str, Any], memory: dict[str, Any]) -> tuple[dict[str, Any], str]:
    value = validate_character_notes(notes)
    candidate = _object(memory, "memory")
    candidate.setdefault("id", _uuid())
    value["memories"].append(candidate)
    normalized = validate_character_notes(value)
    return normalized, candidate["id"]


def resolve_memory(
    notes: dict[str, Any], memory_id: str, status: str = "resolved"
) -> dict[str, Any]:
    value = validate_character_notes(notes)
    memory = next((entry for entry in value["memories"] if entry["id"] == memory_id), None)
    if memory is None:
        raise LookupError(memory_id)
    memory["status"] = status
    return validate_character_notes(value)
