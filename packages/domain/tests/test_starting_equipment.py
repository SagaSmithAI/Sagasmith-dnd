from __future__ import annotations

from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.content_validation import (
    build_selection_contract,
    selection_contract_errors,
    selection_input_errors,
    selection_schema_for_artifact,
)
from sagasmith_dnd.random_stream import CampaignRandomStream
from sagasmith_dnd.starting_equipment import (
    apply_starting_equipment,
    normalize_starting_equipment_contract,
    normalize_starting_equipment_selection,
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


def test_class_equipment_selection_schema_is_bound_and_opt_in():
    artifact = {
        "id": "fixture.class.training", "kind": "class",
        "card": {"name": "Training", "class_definition": {
            "hit_die": 8, "saving_throw_proficiencies": ["constitution", "intelligence"],
            "armor_proficiencies": [], "weapon_proficiencies": [], "tool_proficiencies": [],
            "skill_choice_count": 1, "skill_options": ["arcana"],
        }},
    }
    assert selection_schema_for_artifact(artifact)["selection_fields"] == ["skills", "tools"]
    artifact["card"]["class_definition"]["starting_equipment"] = contract()
    artifact["selection_contract"] = build_selection_contract(artifact, status="ready")
    assert selection_schema_for_artifact(artifact)["selection_fields"] == [
        "skills", "tools", "starting_equipment",
    ]
    assert selection_input_errors(artifact, {"starting_equipment": {"mode": "gold"}}) == []
    artifact["card"]["class_definition"]["starting_equipment"]["gold_alternative"][
        "multiplier"
    ] = 100
    assert selection_contract_errors(artifact)


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
    assert all(
        item["source_key"] == "eberron:artificer"
        for item in result["sheet"]["inventory"]["items"]
    )
    assert all(not item["equipped"] for item in result["sheet"]["inventory"]["items"])
    assert result["wallet"] == {}
    assert sheet == default_character_sheet()


def test_public_selection_normalizer_is_pure_and_canonical():
    original = {"mode": "equipment", "choices": {"weapon": ["sword", "bow"]}}
    normalized = normalize_starting_equipment_selection(contract(), original)
    assert normalized == original
    assert original == {"mode": "equipment", "choices": {"weapon": ["sword", "bow"]}}


def test_gold_only_contract_cannot_succeed_as_empty_equipment():
    gold_only = {"items": [], "choices": [], "gold_alternative": contract()["gold_alternative"]}
    with pytest.raises(ValueError, match="at least one equipment"):
        normalize_starting_equipment_selection(gold_only, {"mode": "equipment"})


def test_gold_roll_is_recorded_and_replaces_background():
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
    assert stream.position == 5


@pytest.mark.parametrize("face,expected", [(1, 50), (4, 200)])
def test_gold_exact_minimum_and_maximum_with_five_draws(face, expected):
    class FixedFaces:
        calls = 0

        def randint(self, low, high):
            assert (low, high) == (1, 4)
            self.calls += 1
            return face

    rng = FixedFaces()
    source = contract()
    source["gold_alternative"]["dice"] = "5D4"
    sheet = default_character_sheet()
    sheet["inventory"]["wallet"]["gp"] = 7
    before = deepcopy(sheet)
    result = apply_starting_equipment(
        sheet, contract=source, selection={"mode": " GOLD "}, item_templates={},
        source_key="fixture:gold", rng=rng,
    )
    assert rng.calls == 5
    assert result["wallet"] == {"gp": expected}
    assert result["sheet"]["inventory"]["wallet"]["gp"] == expected + 7
    assert result["roll"]["rolls"] == (face,) * 5
    assert sheet == before


@pytest.mark.parametrize("source", ["", "x" * 301])
def test_invalid_source_does_not_consume_gold_rng(source):
    stream = CampaignRandomStream("c", "a" * 64, 0, "starting-equipment", "k")
    with pytest.raises(ValueError, match="source_key"):
        apply_starting_equipment(
            default_character_sheet(), contract=contract(), selection={"mode": "gold"},
            item_templates={}, source_key=source, rng=stream,
        )
    assert stream.position == 0


def test_gold_requires_explicit_rng():
    with pytest.raises(ValueError, match="explicit rng"):
        apply_starting_equipment(
            default_character_sheet(), contract=contract(), selection={"mode": "gold"},
            item_templates={}, source_key="fixture:gold",
        )


def test_selected_inventory_and_receipt_do_not_alias_inputs():
    selection = {"mode": "equipment", "choices": {"weapon": ["sword", "bow"]}}
    items = templates()
    items["sword"]["mechanics"] = {}
    original_items = deepcopy(items)
    result = apply_starting_equipment(
        default_character_sheet(), contract=contract(), selection=selection,
        item_templates=items, source_key="fixture:items",
    )
    result["selection"]["choices"]["weapon"].clear()
    result["sheet"]["inventory"]["items"][0]["name"] = "changed"
    assert selection["choices"]["weapon"] == ["sword", "bow"]
    assert items == original_items


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
    assert bad == {
        **original_contract,
        "choices": [{**original_contract["choices"][0], "allow_duplicates": False}],
    }


@pytest.mark.parametrize("attunement", ["required", "attuned"])
def test_template_cannot_force_equipped_or_attuned(attunement):
    item_templates = templates()
    item_templates["pack"].update(
        equipped=True, equipped_slot="armor", attunement=attunement
    )
    result = apply_starting_equipment(
        default_character_sheet(),
        contract=contract(),
        selection={"mode": "equipment", "choices": {"weapon": ["sword", "bow"]}},
        item_templates=item_templates,
        source_key="x",
    )
    item = next(item for item in result["sheet"]["inventory"]["items"] if item["name"] == "pack")
    assert item["equipped"] is False
    assert item["equipped_slot"] is None
    assert item["attunement"] == "required"


@pytest.mark.parametrize(
    "bad",
    [
        {"items": [{"artifact_id": "x", "quantity": True}]},
        {"items": [{"artifact_id": "x", "quantity": 1, "extra": 2}]},
        {
            "gold_alternative": {
                "dice": "0d4",
                "multiplier": 1,
                "denomination": "gp",
                "replaces_background_equipment": False,
            }
        },
    ],
)
def test_contract_rejects_invalid_shapes(bad):
    with pytest.raises(ValueError):
        normalize_starting_equipment_contract(bad)


def test_contract_rejects_count_above_options_without_duplicates():
    bad = {
        "items": [{"artifact_id": "x", "quantity": 1}],
        "choices": [
            {"id": "group", "count": 2, "options": ["x"], "allow_duplicates": False}
        ],
    }
    with pytest.raises(ValueError, match="exceeds"):
        normalize_starting_equipment_contract(bad)


@pytest.mark.parametrize("dice", ["101d4", "1d1001", "5d1"])
def test_invalid_gold_dice_does_not_consume_rng(dice):
    stream = CampaignRandomStream("c", "d" * 64, 0, "starting-equipment", "k")
    bad = deepcopy(contract())
    bad["gold_alternative"]["dice"] = dice
    with pytest.raises(ValueError, match="engine limits"):
        apply_starting_equipment(
            default_character_sheet(),
            contract=bad,
            selection={"mode": "gold"},
            item_templates=templates(),
            source_key="x",
            rng=stream,
        )
    assert stream.position == 0


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
