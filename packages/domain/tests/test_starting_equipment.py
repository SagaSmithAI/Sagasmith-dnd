from __future__ import annotations

from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.random_stream import CampaignRandomStream
from sagasmith_dnd.starting_equipment import (
    apply_starting_equipment,
    normalize_starting_equipment_contract,
)


def contract():
    return {
        "items": [{"artifact_id": "pack", "quantity": 1}],
        "choices": [
            {
                "id": "weapon",
                "count": 2,
                "options": ["sword", "bow"],
                "allow_duplicates": True,
            }
        ],
        "gold_alternative": {
            "dice": "5d4",
            "multiplier": 10,
            "denomination": "gp",
            "replaces_background_equipment": True,
        },
    }


def templates():
    return {
        key: {"id": key, "name": key, "kind": "equipment"}
        for key in ("pack", "sword", "bow")
    }


def test_equipment_adds_fixed_and_duplicate_choice_without_equipping():
    sheet = default_character_sheet()
    result = apply_starting_equipment(
        sheet,
        contract=contract(),
        selection={"mode": "equipment", "choices": {"weapon": ["sword", "sword"]}},
        item_templates=templates(),
        source_key="eberron:artificer",
    )
    assert len(result["item_ids"]) == 3
    assert all(item["source_key"] == "eberron:artificer" for item in result["sheet"]["inventory"]["items"])
    assert all(not item["equipped"] for item in result["sheet"]["inventory"]["items"])
    assert result["wallet"] == {}
    assert sheet == default_character_sheet()


def test_gold_extremes_are_recorded_and_replace_background():
    stream = CampaignRandomStream("c", "a" * 64, 0, "starting-equipment", "k")
    result = apply_starting_equipment(
        default_character_sheet(),
        contract=contract(),
        selection={"mode": "gold"},
        item_templates=templates(),
        source_key="eberron:artificer",
        rng=stream,
    )
    assert 50 <= result["wallet"]["gp"] <= 200
    assert result["roll"]["expression"] == "5d4"
    assert result["replaces_background_equipment"] is True
    assert result["item_ids"] == []


def test_bad_selection_does_not_consume_rng_or_mutate_inputs():
    stream = CampaignRandomStream("c", "b" * 64, 0, "starting-equipment", "k")
    sheet = default_character_sheet()
    original_sheet = deepcopy(sheet)
    original_contract = deepcopy(contract())
    with pytest.raises(ValueError, match="does not allow duplicates"):
        bad = deepcopy(contract())
        bad["choices"][0]["allow_duplicates"] = False
        apply_starting_equipment(
            sheet,
            contract=bad,
            selection={"mode": "equipment", "choices": {"weapon": ["sword", "sword"]}},
            item_templates=templates(),
            source_key="x",
            rng=stream,
        )
    assert stream.position == 0
    assert sheet == original_sheet
    assert bad == {**original_contract, "choices": [{**original_contract["choices"][0], "allow_duplicates": False}]}


@pytest.mark.parametrize(
    "bad",
    [
        {"items": [{"artifact_id": "x", "quantity": True}]},
        {"items": [{"artifact_id": "x", "quantity": 1, "extra": 2}]},
        {"gold_alternative": {"dice": "0d4", "multiplier": 1, "denomination": "gp", "replaces_background_equipment": False}},
    ],
)
def test_contract_rejects_invalid_shapes(bad):
    with pytest.raises(ValueError):
        normalize_starting_equipment_contract(bad)


def test_missing_template_is_rejected_before_equipment_mutation():
    with pytest.raises(ValueError, match="template is missing"):
        apply_starting_equipment(
            default_character_sheet(),
            contract=contract(),
            selection={"mode": "equipment", "choices": {"weapon": ["sword", "bow"]}},
            item_templates={"pack": templates()["pack"], "sword": templates()["sword"]},
            source_key="x",
        )


def test_equipment_path_does_not_consume_rng():
    stream = CampaignRandomStream("c", "c" * 64, 0, "starting-equipment", "k")
    apply_starting_equipment(
        default_character_sheet(),
        contract=contract(),
        selection={"mode": "equipment", "choices": {"weapon": ["sword", "bow"]}},
        item_templates=templates(),
        source_key="x",
        rng=stream,
    )
    assert stream.position == 0
