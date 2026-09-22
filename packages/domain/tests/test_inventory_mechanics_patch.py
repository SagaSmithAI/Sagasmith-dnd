from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import (
    default_character_sheet,
    update_inventory_item,
    validate_character_sheet,
)


def bow_sheet():
    sheet = default_character_sheet()
    sheet["inventory"]["items"] = [
        {"id": "arrows", "name": "Arrows", "kind": "ammunition", "quantity": 20},
        {
            "id": "bow",
            "name": "Shortbow",
            "kind": "weapon",
            "mechanics": {
                "category": "simple",
                "attack_type": "ranged",
                "attack_ability": "dexterity",
                "damage_formula": "1d6",
                "damage_type": "piercing",
                "properties": ["ammunition", "two-handed"],
                "normal_range_ft": 80,
                "long_range_ft": 320,
            },
        },
    ]
    return validate_character_sheet(sheet)


def test_ammunition_binding_preserves_weapon_and_other_sheet_state():
    original = bow_sheet()
    expected = deepcopy(original)
    expected["inventory"]["items"][1]["mechanics"]["ammunition_item_id"] = "arrows"
    result = update_inventory_item(original, "bow", {"mechanics": {"ammunition_item_id": "arrows"}})
    assert result == expected
    assert original == bow_sheet()
    assert (
        update_inventory_item(result, "bow", {"mechanics": {"ammunition_item_id": None}})
        == original
    )


def test_mechanics_patch_still_validates_fields_and_references():
    for patch, message in [
        ({"ammunition_item_id": "missing"}, "ammunition_item_id"),
        ({"unknown_weapon_field": True}, "unsupported"),
    ]:
        with pytest.raises(ValueError, match=message):
            update_inventory_item(bow_sheet(), "bow", {"mechanics": patch})
