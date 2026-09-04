from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import (
    attune_inventory_item,
    default_character_sheet,
    derive_character_sheet,
    validate_character_sheet,
)


def ref(item_id, name="Wand", attunement="required", *, kind="ground"):
    location = (
        {"kind": "ground", "ground_id": "scene-1", "item_id": "physical-1"}
        if kind == "ground"
        else {"kind": "actor", "actor_id": "actor-2", "item_id": "physical-1"}
    )
    return {"id": item_id, "name": name, "attunement": attunement, "location": location}


def test_external_refs_are_default_strict_and_background_grants_are_preserved():
    sheet = default_character_sheet()
    assert validate_character_sheet(sheet)["inventory"]["external_items"] == []
    sheet["inventory"]["external_items"] = [ref("wand-ref")]
    sheet["progression"]["background_grants"]["equipment_item_ids"] = ["wand-ref"]
    original = deepcopy(sheet)
    normalized = validate_character_sheet(sheet)
    assert normalized["inventory"]["external_items"][0] == sheet["inventory"]["external_items"][0]
    assert normalized["progression"]["background_grants"]["equipment_item_ids"] == ["wand-ref"]
    assert sheet == original


@pytest.mark.parametrize(
    "bad",
    [
        {
            "id": "x",
            "name": "x",
            "attunement": "required",
            "location": {"kind": "ground", "ground_id": "g", "item_id": "i", "extra": 1},
        },
        {
            "id": "x",
            "name": "x",
            "attunement": "required",
            "location": {"kind": "ground", "ground_id": True, "item_id": "i"},
        },
        {
            "id": "x",
            "name": "x",
            "attunement": "required",
            "location": {"kind": "teleport", "ground_id": "g", "item_id": "i"},
        },
        {
            "id": "",
            "name": "x",
            "attunement": "required",
            "location": {"kind": "ground", "ground_id": "g", "item_id": "i"},
        },
    ],
)
def test_external_refs_reject_malformed_records(bad):
    sheet = default_character_sheet()
    sheet["inventory"]["external_items"] = [bad]
    with pytest.raises(ValueError):
        validate_character_sheet(sheet)


def test_external_ids_and_attunement_capacity_include_carried_items():
    sheet = default_character_sheet()
    sheet["inventory"]["external_items"] = [
        ref(f"ref-{i}", f"Item {i}", "attuned") for i in range(3)
    ]
    with pytest.raises(ValueError, match="more than three"):
        sheet["inventory"]["external_items"].append(ref("ref-3", "Item 3", "attuned"))
        validate_character_sheet(sheet)
    duplicate = default_character_sheet()
    duplicate["inventory"]["external_items"] = [ref("same"), ref("same", "Other")]
    with pytest.raises(ValueError, match="duplicate"):
        validate_character_sheet(duplicate)
    collision = default_character_sheet()
    collision["inventory"]["items"] = [{"id": "same", "name": "sword", "kind": "equipment"}]
    collision["inventory"]["external_items"] = [ref("same")]
    with pytest.raises(ValueError, match="collide"):
        validate_character_sheet(collision)


def test_attune_external_ref_and_dead_character_clear_all_attunement():
    sheet = default_character_sheet()
    sheet["inventory"]["external_items"] = [ref("ref-1", "Wand", "required")]
    attuned = attune_inventory_item(sheet, "ref-1")
    assert attuned["inventory"]["external_items"][0]["attunement"] == "attuned"
    attuned["conditions"] = ["dead"]
    dead = validate_character_sheet(attuned)
    assert dead["inventory"]["external_items"][0]["attunement"] == "required"


def test_external_refs_are_not_carried_weight_or_equipment_or_weapon_benefits():
    plain = validate_character_sheet(default_character_sheet())
    with_ref = default_character_sheet()
    with_ref["inventory"]["external_items"] = [ref("armor-ref", "Plate", "attuned")]
    derived = derive_character_sheet(with_ref)
    assert (
        derived["inventory"]["total_weight_oz"]
        == derive_character_sheet(plain)["inventory"]["total_weight_oz"]
    )
    assert all(value is None for value in plain["inventory"]["equipment_slots"].values())
    assert all(
        attack["item_id"] != "armor-ref" for attack in derived["inventory"]["weapon_attacks"]
    )


def test_attuned_duplicate_named_external_and_carried_item_is_rejected():
    sheet = default_character_sheet()
    sheet["inventory"]["items"] = [
        {"id": "wand-carried", "name": "Wand", "kind": "equipment", "attunement": "attuned"}
    ]
    sheet["inventory"]["external_items"] = [ref("wand-ref", "Wand", "attuned", kind="actor")]
    with pytest.raises(ValueError, match="one copy"):
        validate_character_sheet(sheet)
