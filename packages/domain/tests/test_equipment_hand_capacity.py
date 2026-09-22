from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    equip_inventory_item,
)


def equipped_pair():
    sheet = default_character_sheet()
    for item_id, kind in (("sword", "weapon"), ("spear", "weapon"), ("shield", "shield")):
        sheet, _ = add_inventory_item(sheet, {
            "id": item_id, "name": item_id, "kind": kind,
            "mechanics": {"ac_bonus": 2} if kind == "shield" else {},
        })
    return equip_inventory_item(sheet, "sword", "main_hand")


@pytest.mark.parametrize("first,slot,last,last_slot", [
    ("shield", "shield", "spear", "off_hand"),
    ("spear", "off_hand", "shield", "shield"),
])
def test_three_held_items_rejected_without_mutating_sheet(first, slot, last, last_slot):
    sheet = equip_inventory_item(equipped_pair(), first, slot)
    before = deepcopy(sheet)
    with pytest.raises(ValueError, match="exceed functional hands"):
        equip_inventory_item(sheet, last, last_slot)
    assert sheet == before


def test_spare_weapon_can_replace_held_weapon():
    sheet = equip_inventory_item(equipped_pair(), "shield", "shield")
    result = equip_inventory_item(sheet, "spear", "main_hand")
    assert result["inventory"]["equipment_slots"]["main_hand"] == "spear"
    sword = next(item for item in result["inventory"]["items"] if item["id"] == "sword")
    assert not sword["equipped"]


def test_one_functional_hand_cannot_hold_weapon_and_shield():
    sheet = equipped_pair()
    sheet["traits"]["anatomy"]["functional_hands"] = 1
    with pytest.raises(ValueError, match="exceed functional hands"):
        equip_inventory_item(sheet, "shield", "shield")


def test_legacy_overoccupied_sheet_can_be_repaired_by_stowing():
    sheet = equip_inventory_item(equipped_pair(), "shield", "shield")
    spear = next(item for item in sheet["inventory"]["items"] if item["id"] == "spear")
    spear.update(equipped=True, equipped_slot="off_hand")
    sheet["inventory"]["equipment_slots"]["off_hand"] = "spear"
    repaired = equip_inventory_item(sheet, "spear", None)
    assert repaired["inventory"]["equipment_slots"]["off_hand"] is None
