from copy import deepcopy

import pytest
from test_external_custody import _dropped

from sagasmith_dnd.character_schema import attune_inventory_item, validate_character_sheet
from sagasmith_dnd.ground_transfer import pickup_ground_item
from sagasmith_dnd.item_attunement_ownership import complete_item_attunement_ownership


def _carried_by_other():
    sheets, ground = _dropped(attunement="attuned")
    sheets["other"] = validate_character_sheet({})
    return pickup_ground_item(sheets, ground, "other", "ground-sword")


def test_new_completed_attunement_ends_old_bond_without_losing_history():
    picked = _carried_by_other()
    picked["sheets"]["other"] = attune_inventory_item(picked["sheets"]["other"], "sword")
    before = deepcopy(picked)
    settled = complete_item_attunement_ownership(
        picked["sheets"], picked["ground_items"], {"other": "sword"}
    )
    assert settled["owner"]["inventory"]["external_items"][0] == {
        "id": "sword",
        "name": "Sword",
        "attunement": "required",
        "location": {"kind": "actor", "actor_id": "other", "item_id": "sword"},
    }
    assert settled["other"]["inventory"]["items"][0]["attunement"] == "attuned"
    assert picked == before


def test_custody_without_completed_attunement_preserves_old_bond():
    picked = _carried_by_other()
    settled = complete_item_attunement_ownership(picked["sheets"], picked["ground_items"], {})
    assert settled["owner"]["inventory"]["external_items"][0]["attunement"] == "attuned"
    assert settled["other"]["inventory"]["items"][0]["attunement"] == "required"


@pytest.mark.parametrize(
    "completed", [{"missing": "sword"}, {"other": "missing"}, {"other": "sword"}]
)
def test_uncompleted_or_missing_attunement_is_not_synthesized(completed):
    picked = _carried_by_other()
    before = deepcopy(picked)
    with pytest.raises(ValueError):
        complete_item_attunement_ownership(picked["sheets"], picked["ground_items"], completed)
    assert picked == before
