from copy import deepcopy

import pytest

from sagasmith_dnd.actor_inventory_transfer import transfer_actor_inventory_item
from sagasmith_dnd.character_schema import (
    default_character_sheet,
    equip_inventory_item,
    validate_character_sheet,
)


def _weapon(item_id: str, *, attunement: str = "none", quantity: int = 1) -> dict:
    return {
        "id": item_id,
        "name": "Longsword",
        "kind": "weapon",
        "quantity": quantity,
        "weight_oz": 48,
        "attunement": attunement,
        "mechanics": {
            "category": "martial",
            "attack_type": "melee",
            "attack_ability": "strength",
            "damage_formula": "1d8",
            "damage_type": "slashing",
            "properties": [],
            "ammunition_item_id": None,
        },
    }


def _sheet(*items: dict) -> dict:
    sheet = default_character_sheet()
    sheet["inventory"]["items"] = deepcopy(list(items))
    return validate_character_sheet(sheet)


def test_full_transfer_moves_attuned_bond() -> None:
    source = _sheet(_weapon("bonded", attunement="attuned"))
    source = equip_inventory_item(source, "bonded", "main_hand")
    sheets = {"source": source, "target": _sheet()}
    result = transfer_actor_inventory_item(sheets, [], "source", "target", "bonded")
    moved = result["item"]
    assert moved["attunement"] == "required"
    assert moved["equipped"] is False
    assert result["sheets"]["source"]["inventory"]["items"] == []
    assert result["sheets"]["source"]["inventory"]["external_items"][0]["attunement"] == "attuned"
    assert result["sheets"]["source"]["inventory"]["external_items"][0]["location"] == {
        "kind": "actor",
        "actor_id": "target",
        "item_id": moved["id"],
    }


def test_attuned_item_return_restores_original_id_and_bond() -> None:
    first = transfer_actor_inventory_item(
        {"a": _sheet(_weapon("amulet", attunement="attuned")), "b": _sheet()},
        [],
        "a",
        "b",
        "amulet",
    )
    second = transfer_actor_inventory_item(
        first["sheets"],
        [],
        "b",
        "a",
        first["item"]["id"],
    )
    assert second["item"]["id"] == "amulet"
    assert second["item"]["attunement"] == "attuned"
    assert second["sheets"]["a"]["inventory"]["external_items"] == []


def test_transfer_from_container_detaches_root_and_preserves_children() -> None:
    container = {
        "id": "pack",
        "name": "Pack",
        "kind": "container",
        "quantity": 1,
        "weight_oz": 16,
    }
    child = _weapon("inside")
    child["container_id"] = "pack"
    result = transfer_actor_inventory_item(
        {"a": _sheet(container, child), "b": _sheet()},
        [],
        "a",
        "b",
        "inside",
    )
    assert result["item"]["container_id"] is None
    assert result["sheets"]["a"]["inventory"]["items"][0]["id"] == "pack"


def test_partial_nonattuned_stack_moves_only_requested_quantity() -> None:
    result = transfer_actor_inventory_item(
        {"source": _sheet(_weapon("stack", quantity=4)), "target": _sheet()},
        [],
        "source",
        "target",
        "stack",
        quantity=2,
    )
    assert result["item"]["quantity"] == 2
    assert result["sheets"]["source"]["inventory"]["items"][0]["quantity"] == 2
    with pytest.raises(ValueError, match="split an attunable"):
        transfer_actor_inventory_item(
            {
                "source": _sheet(_weapon("magic", attunement="required", quantity=2)),
                "target": _sheet(),
            },
            [],
            "source",
            "target",
            "magic",
            quantity=1,
        )


def test_destination_collision_remaps_item_and_preserves_ammunition_property() -> None:
    bow = _weapon("bow")
    bow["name"] = "Shortbow"
    bow["mechanics"]["properties"] = ["ammunition"]
    bow["mechanics"]["ammunition_item_id"] = "arrows"
    arrows = {
        "id": "arrows",
        "name": "Arrows",
        "kind": "ammunition",
        "quantity": 20,
        "weight_oz": 1,
    }
    result = transfer_actor_inventory_item(
        {"source": _sheet(bow, arrows), "target": _sheet(_weapon("bow"))},
        [],
        "source",
        "target",
        "bow",
    )
    moved = result["item"]
    assert moved["id"] != "bow"
    assert moved["mechanics"]["properties"] == ["ammunition"]
    assert moved["mechanics"]["ammunition_item_id"] is None
    assert all(item["id"] != "arrows" for item in result["sheets"]["target"]["inventory"]["items"])


def test_inputs_are_not_mutated_and_unknown_item_is_rejected() -> None:
    sheets = {"source": _sheet(_weapon("sword")), "target": _sheet()}
    before = deepcopy(sheets)
    with pytest.raises(LookupError):
        transfer_actor_inventory_item(sheets, [], "source", "target", "missing")
    assert sheets == before


def test_third_carrier_collision_then_return_restores_original_bond_and_id():
    sheets = {
        "a": _sheet(_weapon("blade", attunement="attuned")),
        "b": _sheet(),
        "c": _sheet(_weapon("blade")),
    }
    first = transfer_actor_inventory_item(sheets, [], "a", "b", "blade")
    second = transfer_actor_inventory_item(first["sheets"], [], "b", "c", "blade")
    assert second["item"]["id"] != "blade"
    assert second["sheets"]["b"]["inventory"]["external_items"] == []
    assert second["sheets"]["a"]["inventory"]["external_items"][0]["attunement"] == "attuned"
    returned = transfer_actor_inventory_item(second["sheets"], [], "c", "a", second["item"]["id"])
    assert returned["item"]["id"] == "blade"
    assert returned["item"]["attunement"] == "attuned"
    assert returned["sheets"]["a"]["inventory"]["external_items"] == []


def test_mundane_background_equipment_keeps_original_history():
    source = _sheet(_weapon("starting-weapon"))
    source["progression"]["background_grants"]["equipment_item_ids"] = ["starting-weapon"]
    first = transfer_actor_inventory_item(
        {"a": source, "b": _sheet()}, [], "a", "b", "starting-weapon"
    )
    assert first["sheets"]["a"]["progression"]["background_grants"]["equipment_item_ids"] == [
        "starting-weapon"
    ]
    assert first["sheets"]["a"]["inventory"]["external_items"][0]["attunement"] == "none"
    returned = transfer_actor_inventory_item(first["sheets"], [], "b", "a", first["item"]["id"])
    assert returned["sheets"]["a"] == validate_character_sheet(source)


def test_container_tree_remaps_ammunition_and_preserves_each_child_bond():
    bag = {"id": "bag", "name": "Bag", "kind": "container"}
    bow = _weapon("bow", attunement="attuned")
    bow["name"] = "Magic Bow"
    bow["container_id"] = "bag"
    bow["mechanics"].update(properties=["ammunition"], ammunition_item_id="arrows")
    arrows = {
        "id": "arrows",
        "name": "Arrows",
        "kind": "ammunition",
        "quantity": 20,
        "container_id": "bag",
    }
    ring = {
        "id": "ring",
        "name": "Magic Ring",
        "kind": "equipment",
        "attunement": "attuned",
        "container_id": "bag",
    }
    result = transfer_actor_inventory_item(
        {
            "a": _sheet(bow, ring, arrows, bag),
            "b": _sheet(_weapon("bow"), {**arrows, "container_id": None}),
        },
        [],
        "a",
        "b",
        "bag",
    )
    mapping = result["id_map"]
    received = {item["id"]: item for item in result["sheets"]["b"]["inventory"]["items"]}
    assert received[mapping["bow"]]["mechanics"]["ammunition_item_id"] == mapping["arrows"]
    assert received[mapping["bow"]]["container_id"] == mapping["bag"]
    assert received[mapping["ring"]]["attunement"] == "required"
    assert received[mapping["bow"]]["attunement"] == "required"
    assert {
        ref["id"]
        for ref in result["sheets"]["a"]["inventory"]["external_items"]
        if ref["attunement"] == "attuned"
    } == {"bow", "ring"}


def test_partial_stack_collision_is_deterministic_bounded_and_non_mutating():
    item_id = "x" * 100
    sheets = {"a": _sheet(_weapon(item_id, quantity=4)), "b": _sheet(_weapon(item_id))}
    before = deepcopy(sheets)
    first = transfer_actor_inventory_item(sheets, [], "a", "b", item_id, 2)
    assert first == transfer_actor_inventory_item(sheets, [], "a", "b", item_id, 2)
    assert sheets == before
    assert len(first["item"]["id"]) <= 100
    assert first["item"]["id"] != item_id
    assert first["sheets"]["a"]["inventory"]["items"][0]["quantity"] == 2


def test_transferred_spellcasting_item_does_not_delete_ongoing_effect():
    source = _sheet(
        {
            "id": "stone",
            "name": "Light Stone",
            "kind": "magic_item",
            "mechanics": {"spellcasting": {"spells": [{"card": {"id": "test-light"}}]}},
        }
    )
    source["effects"] = [
        {
            "id": "light-effect",
            "name": "Light",
            "source_spell_id": "test-light",
            "duration": {"period": "round", "remaining": 5},
        }
    ]
    source = validate_character_sheet(source)
    result = transfer_actor_inventory_item({"a": source, "b": _sheet()}, [], "a", "b", "stone")
    assert result["sheets"]["a"]["effects"] == [{**source["effects"][0], "source": "actor:a"}]
    assert result["sheets"]["b"]["effects"] == []
