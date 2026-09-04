from __future__ import annotations

from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import (
    add_inventory_item,
    equip_inventory_item,
    validate_character_sheet,
)
from sagasmith_dnd.external_custody import validate_external_inventory_custody
from sagasmith_dnd.ground_transfer import drop_held_items, pickup_ground_item


def _weapon(item_id: str, *, attunement: str = "none") -> dict:
    return {
        "id": item_id,
        "name": item_id.title(),
        "kind": "weapon",
        "attunement": attunement,
        "mechanics": {
            "category": "simple",
            "attack_type": "melee",
            "attack_ability": "strength",
            "damage_formula": "1d8",
            "damage_type": "bludgeoning",
        },
    }


def _dropped(*, attunement: str = "none") -> tuple[dict, dict]:
    owner = validate_character_sheet({})
    owner, item_id = add_inventory_item(owner, _weapon("sword", attunement=attunement))
    owner = equip_inventory_item(owner, item_id, "main_hand")
    result = drop_held_items(
        {"owner": owner},
        [],
        "owner",
        record_ids={item_id: "ground-sword"},
        scene_id=None,
        encounter_id=None,
        campaign_revision=1,
        location={"mode": "agent", "anchor_actor_id": "owner"},
    )
    return result["sheets"], result["ground_items"]


def test_real_ground_pickup_custody_is_valid() -> None:
    sheets, ground = _dropped(attunement="attuned")
    sheets["other"] = validate_character_sheet({})
    picked = pickup_ground_item(sheets, ground, "other", "ground-sword")

    validate_external_inventory_custody(picked["sheets"], picked["ground_items"])


def test_stale_actor_reference_after_transfer_or_removal_is_rejected() -> None:
    sheets, ground = _dropped()
    sheets["other"] = validate_character_sheet({})
    picked = pickup_ground_item(sheets, ground, "other", "ground-sword")
    validate_external_inventory_custody(picked["sheets"], picked["ground_items"])
    removed = picked["sheets"]["other"]["inventory"]["items"].pop()
    picked["sheets"]["third"] = validate_character_sheet({})
    picked["sheets"]["third"]["inventory"]["items"].append(removed)
    with pytest.raises(ValueError, match="missing physical item"):
        validate_external_inventory_custody(picked["sheets"], picked["ground_items"])


def test_two_attuned_actor_views_of_one_physical_item_are_rejected() -> None:
    sheets, ground = _dropped(attunement="attuned")
    other = validate_character_sheet({})
    other["inventory"]["external_items"] = deepcopy(sheets["owner"]["inventory"]["external_items"])
    sheets["other"] = validate_character_sheet(other)
    with pytest.raises(ValueError, match="multiple attuned owners"):
        validate_external_inventory_custody(sheets, ground)


def test_return_to_original_attuned_owner_is_valid_after_intermediate_drop() -> None:
    sheets, ground = _dropped(attunement="attuned")
    sheets["other"] = validate_character_sheet({})
    picked = pickup_ground_item(sheets, ground, "other", "ground-sword")
    incoming_id = picked["picked_up"]["root_item_id"]
    picked["sheets"]["other"] = equip_inventory_item(
        picked["sheets"]["other"], incoming_id, "main_hand"
    )
    second = drop_held_items(
        picked["sheets"],
        picked["ground_items"],
        "other",
        record_ids={incoming_id: "ground-return"},
        scene_id=None,
        encounter_id=None,
        campaign_revision=2,
        location={"mode": "agent", "anchor_actor_id": "other"},
    )

    returned = pickup_ground_item(
        second["sheets"], second["ground_items"], "owner", "ground-return"
    )
    before = deepcopy(returned)
    validate_external_inventory_custody(returned["sheets"], returned["ground_items"])
    assert returned == before


def test_new_carrier_attunement_cannot_leave_original_bond_active() -> None:
    sheets, ground = _dropped(attunement="attuned")
    sheets["other"] = validate_character_sheet({})
    picked = pickup_ground_item(sheets, ground, "other", "ground-sword")
    picked["sheets"]["other"]["inventory"]["items"][0]["attunement"] = "attuned"
    with pytest.raises(ValueError, match="multiple attuned owners"):
        validate_external_inventory_custody(picked["sheets"], picked["ground_items"])


def test_dead_owner_may_leave_historical_attuned_ground_snapshot() -> None:
    sheets, ground = _dropped(attunement="attuned")
    sheets["owner"]["conditions"] = ["dead"]
    validate_external_inventory_custody(sheets, ground)


def test_non_attunable_item_rejects_attuned_reference() -> None:
    sheets, ground = _dropped()
    sheets["owner"]["inventory"]["external_items"][0]["attunement"] = "attuned"
    with pytest.raises(ValueError, match="non-attunable"):
        validate_external_inventory_custody(sheets, ground)
