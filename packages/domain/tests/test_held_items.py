from __future__ import annotations

from copy import deepcopy

import pytest

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


@pytest.mark.parametrize("kind", ["focus", "equipment"])
def test_held_roots_includes_nonweapon_hand_items(kind: str) -> None:
    sheet = validate_character_sheet({})
    sheet, item_id = add_inventory_item(
        sheet,
        {"id": kind, "name": kind.title(), "kind": kind},
    )
    sheet = equip_inventory_item(sheet, item_id, "off_hand")

    assert held_item_roots(sheet) == [kind]


def test_held_roots_keeps_attuned_weapon_as_a_held_root() -> None:
    sheet = validate_character_sheet({})
    sheet, item_id = add_inventory_item(
        sheet,
        {**_weapon("attuned-sword"), "attunement": "attuned"},
    )
    sheet = equip_inventory_item(sheet, item_id, "main_hand")

    assert held_item_roots(sheet) == [item_id]


def test_held_roots_rejects_stale_hand_slot_reference() -> None:
    sheet = validate_character_sheet({})
    sheet["inventory"]["equipment_slots"]["main_hand"] = "missing-item"

    with pytest.raises(ValueError, match="references an unknown item"):
        held_item_roots(sheet)
