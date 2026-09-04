from __future__ import annotations

from copy import deepcopy

from sagasmith_dnd.character_schema import (
    add_inventory_item,
    equip_inventory_item,
    validate_character_sheet,
)
from sagasmith_dnd.held_items import held_item_roots


def _weapon(item_id: str, name: str = "Weapon", *, ammunition_item_id: str | None = None) -> dict:
    return {
        "id": item_id,
        "name": name,
        "kind": "weapon",
        "mechanics": {
            "category": "simple",
            "attack_type": "ranged" if ammunition_item_id else "melee",
            "attack_ability": "dexterity" if ammunition_item_id else "strength",
            "damage_formula": "1d8",
            "damage_type": "piercing" if ammunition_item_id else "bludgeoning",
            "properties": ["ammunition"] if ammunition_item_id else [],
            "ammunition_item_id": ammunition_item_id,
        },
    }


def _shield() -> dict:
    return {
        "id": "shield",
        "name": "Shield",
        "kind": "shield",
        "mechanics": {"ac_bonus": 2, "magic_bonus": 0},
    }


def test_held_roots_selects_hands_not_strapped_shield_and_preserves_input() -> None:
    sheet = validate_character_sheet({})
    sheet, weapon_id = add_inventory_item(sheet, _weapon("sword"))
    sheet, shield_id = add_inventory_item(sheet, _shield())
    sheet = equip_inventory_item(sheet, weapon_id, "main_hand")
    sheet = equip_inventory_item(sheet, shield_id, "shield")
    before = deepcopy(sheet)

    assert held_item_roots(sheet) == ["sword"]
    assert sheet == before


def test_held_roots_returns_both_hands_in_slot_order_once() -> None:
    sheet = validate_character_sheet({})
    sheet, first = add_inventory_item(sheet, _weapon("first"))
    sheet, second = add_inventory_item(sheet, _weapon("second"))
    sheet = equip_inventory_item(sheet, first, "main_hand")
    sheet = equip_inventory_item(sheet, second, "off_hand")

    assert held_item_roots(sheet) == ["first", "second"]


def test_held_roots_excludes_worn_only_items() -> None:
    sheet = validate_character_sheet({})
    sheet, armor_id = add_inventory_item(
        sheet,
        {
            "id": "armor",
            "name": "Leather Armor",
            "kind": "armor",
            "mechanics": {"base_ac": 11, "dexterity_mode": "full", "magic_bonus": 0},
        },
    )
    sheet, shield_id = add_inventory_item(sheet, _shield())
    sheet = equip_inventory_item(sheet, armor_id, "armor")
    sheet = equip_inventory_item(sheet, shield_id, "shield")

    assert held_item_roots(sheet) == []


def test_held_roots_does_not_include_linked_ammunition() -> None:
    sheet = validate_character_sheet({})
    sheet, ammunition_id = add_inventory_item(
        sheet,
        {"id": "arrows", "name": "Arrows", "kind": "ammunition", "quantity": 20},
    )
    sheet, bow_id = add_inventory_item(sheet, _weapon("bow", ammunition_item_id=ammunition_id))
    sheet = equip_inventory_item(sheet, bow_id, "main_hand")

    assert held_item_roots(sheet) == ["bow"]
