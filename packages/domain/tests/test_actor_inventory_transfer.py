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


def test_full_transfer_preserves_effects_and_moves_attuned_bond() -> None:
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
