from __future__ import annotations

from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import (
    add_inventory_item,
    equip_inventory_item,
    validate_character_sheet,
)
from sagasmith_dnd.ground_transfer import drop_held_items, pickup_ground_item


def _weapon(
    item_id: str,
    *,
    name: str | None = None,
    attunement: str = "none",
    ammo_id: str | None = None,
) -> dict:
    return {
        "id": item_id,
        "name": name or item_id.title(),
        "kind": "weapon",
        "attunement": attunement,
        "mechanics": {
            "category": "simple",
            "attack_type": "ranged" if ammo_id else "melee",
            "attack_ability": "dexterity" if ammo_id else "strength",
            "damage_formula": "1d8",
            "damage_type": "piercing" if ammo_id else "bludgeoning",
            "properties": ["ammunition"] if ammo_id else [],
            "ammunition_item_id": ammo_id,
        },
    }


def _location() -> dict:
    return {"mode": "grid", "position": {"x": 2, "y": 3}}


def test_drop_moves_both_held_roots_but_not_worn_shield() -> None:
    sheet = validate_character_sheet({})
    sheet, sword_id = add_inventory_item(sheet, _weapon("sword"))
    sheet, shield_id = add_inventory_item(
        sheet,
        {"id": "shield", "name": "Shield", "kind": "shield", "mechanics": {"ac_bonus": 2}},
    )
    sheet = equip_inventory_item(sheet, sword_id, "main_hand")
    sheet = equip_inventory_item(sheet, shield_id, "shield")

    result = drop_held_items(
        {"actor": sheet},
        [],
        "actor",
        record_ids={"sword": "ground-sword"},
        scene_id="scene",
        encounter_id="encounter",
        campaign_revision=4,
        location=_location(),
    )

    assert [item["id"] for item in result["dropped"][0]["items"]] == ["sword"]
    remaining = result["sheets"]["actor"]
    assert [item["id"] for item in remaining["inventory"]["items"]] == ["shield"]
    assert remaining["inventory"]["equipment_slots"]["shield"] == "shield"


def test_drop_clears_only_moved_weapon_ammunition_link() -> None:
    sheet = validate_character_sheet({})
    sheet, arrows_id = add_inventory_item(
        sheet,
        {"id": "arrows", "name": "Arrows", "kind": "ammunition", "quantity": 20},
    )
    sheet, bow_id = add_inventory_item(sheet, _weapon("bow", ammo_id=arrows_id))
    sheet = equip_inventory_item(sheet, bow_id, "main_hand")

    result = drop_held_items(
        {"actor": sheet},
        [],
        "actor",
        record_ids={"bow": "ground-bow"},
        scene_id=None,
        encounter_id=None,
        campaign_revision=1,
        location={"mode": "agent", "anchor_actor_id": "actor"},
    )

    moved = result["dropped"][0]["items"][0]
    assert moved["mechanics"]["ammunition_item_id"] is None
    assert result["sheets"]["actor"]["inventory"]["items"][0]["id"] == arrows_id


def test_pickup_preserves_attunement_for_owner_and_requires_it_for_other_actor() -> None:
    owner = validate_character_sheet({})
    owner, item_id = add_inventory_item(
        owner,
        _weapon("bonded", attunement="attuned"),
    )
    owner = equip_inventory_item(owner, item_id, "main_hand")
    dropped = drop_held_items(
        {"owner": owner},
        [],
        "owner",
        record_ids={item_id: "ground-bonded"},
        scene_id=None,
        encounter_id=None,
        campaign_revision=2,
        location={"mode": "agent", "anchor_actor_id": "owner"},
    )

    other = validate_character_sheet({})
    other_result = pickup_ground_item(
        {"owner": dropped["sheets"]["owner"], "other": other},
        dropped["ground_items"],
        "other",
        "ground-bonded",
    )
    assert other_result["picked_up"]["items"][0]["attunement"] == "required"

    owner_result = pickup_ground_item(
        {"owner": dropped["sheets"]["owner"]},
        dropped["ground_items"],
        "owner",
        "ground-bonded",
    )
    assert owner_result["picked_up"]["items"][0]["attunement"] == "attuned"


def test_failed_transfer_does_not_mutate_inputs() -> None:
    sheet = validate_character_sheet({})
    sheets = {"actor": sheet}
    ground: list[dict] = []
    before_sheets = deepcopy(sheets)
    before_ground = deepcopy(ground)

    with pytest.raises(ValueError, match="actor_id"):
        drop_held_items(
            sheets,
            ground,
            "missing",
            record_ids={},
            scene_id=None,
            encounter_id=None,
            campaign_revision=0,
            location={"mode": "agent", "anchor_actor_id": "missing"},
        )
    assert sheets == before_sheets
    assert ground == before_ground


def test_drop_preserves_background_grant_through_external_ground_reference() -> None:
    sheet = validate_character_sheet({})
    sheet, item_id = add_inventory_item(sheet, _weapon("background-sword"))
    sheet["progression"]["background_grants"]["equipment_item_ids"] = [item_id]
    sheet = validate_character_sheet(equip_inventory_item(sheet, item_id, "main_hand"))

    result = drop_held_items(
        {"actor": sheet},
        [],
        "actor",
        record_ids={item_id: "ground-background"},
        scene_id="scene",
        encounter_id=None,
        campaign_revision=1,
        location=_location(),
    )

    refs = result["sheets"]["actor"]["inventory"]["external_items"]
    assert refs == [
        {
            "id": item_id,
            "name": "Background-Sword",
            "attunement": "none",
            "location": {"kind": "ground", "ground_id": "ground-background", "item_id": item_id},
        }
    ]
    assert result["sheets"]["actor"]["progression"]["background_grants"]["equipment_item_ids"] == [
        item_id
    ]


def test_pickup_remaps_id_collision_deterministically() -> None:
    source = validate_character_sheet({})
    source, item_id = add_inventory_item(source, _weapon("shared"))
    source = equip_inventory_item(source, item_id, "main_hand")
    dropped = drop_held_items(
        {"source": source},
        [],
        "source",
        record_ids={item_id: "ground-shared"},
        scene_id=None,
        encounter_id=None,
        campaign_revision=1,
        location={"mode": "agent", "anchor_actor_id": "source"},
    )
    target = validate_character_sheet({})
    target, _ = add_inventory_item(target, _weapon("shared", name="Existing"))

    result = pickup_ground_item(
        {"source": dropped["sheets"]["source"], "target": target},
        dropped["ground_items"],
        "target",
        "ground-shared",
    )

    remapped = result["picked_up"]["id_map"]["shared"]
    assert remapped == result["picked_up"]["items"][0]["id"]
    assert remapped != "shared"
    assert len(remapped) <= 100


def test_background_owner_roundtrip_restores_original_item_id_after_collision() -> None:
    owner = validate_character_sheet({})
    owner, item_id = add_inventory_item(owner, _weapon("sword"))
    owner["progression"]["background_grants"]["equipment_item_ids"] = [item_id]
    owner = validate_character_sheet(equip_inventory_item(owner, item_id, "main_hand"))
    first = drop_held_items(
        {"owner": owner},
        [],
        "owner",
        record_ids={item_id: "ground-first"},
        scene_id=None,
        encounter_id=None,
        campaign_revision=1,
        location=_location(),
    )
    other = validate_character_sheet({})
    other, _ = add_inventory_item(other, _weapon("sword", name="Other Sword"))
    picked = pickup_ground_item(
        {"owner": first["sheets"]["owner"], "other": other},
        first["ground_items"],
        "other",
        "ground-first",
    )
    incoming_id = picked["picked_up"]["root_item_id"]
    other_after = equip_inventory_item(picked["sheets"]["other"], incoming_id, "main_hand")
    second = drop_held_items(
        {"owner": picked["sheets"]["owner"], "other": other_after},
        picked["ground_items"],
        "other",
        record_ids={incoming_id: "ground-return"},
        scene_id=None,
        encounter_id=None,
        campaign_revision=2,
        location=_location(),
    )
    returned = pickup_ground_item(
        {"owner": second["sheets"]["owner"]},
        second["ground_items"],
        "owner",
        "ground-return",
    )

    assert returned["picked_up"]["root_item_id"] == item_id
    assert returned["sheets"]["owner"]["progression"]["background_grants"][
        "equipment_item_ids"
    ] == [item_id]
    assert returned["sheets"]["owner"]["inventory"]["external_items"] == []
