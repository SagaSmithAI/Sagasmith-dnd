from __future__ import annotations

import random

import pytest

from sagasmith_dnd.ability_generation import (
    apply_ability_generation,
    apply_pending_rolled_ability_generation,
    begin_rolled_ability_generation,
    roll_ability_scores,
)
from sagasmith_dnd.character_schema import validate_character_sheet


def _assignments() -> dict[str, int]:
    return {
        "strength": 15,
        "dexterity": 14,
        "constitution": 13,
        "intelligence": 12,
        "wisdom": 10,
        "charisma": 8,
    }


def test_standard_array_and_point_buy_are_validated_and_recorded() -> None:
    standard = validate_character_sheet(
        apply_ability_generation(
            validate_character_sheet({}),
            method="standard_array",
            assignments=_assignments(),
        )
    )
    assert standard["abilities"]["strength"]["score"] == 15
    assert standard["ability_generation"]["method"] == "standard_array"

    manual_assignments = {**_assignments(), "strength": 18, "charisma": 16}
    manual = validate_character_sheet(
        apply_ability_generation(
            validate_character_sheet({}),
            method="manual",
            assignments=manual_assignments,
        )
    )
    assert manual["ability_generation"]["method"] == "manual"
    assert manual["ability_generation"]["rolls"] == []
    assert manual["abilities"]["strength"]["score"] == 18
    with pytest.raises(ValueError, match="between 1 and 30"):
        apply_ability_generation(
            validate_character_sheet({}),
            method="manual",
            assignments={**manual_assignments, "strength": 31},
        )

    point_buy = validate_character_sheet(
        apply_ability_generation(
            validate_character_sheet({}),
            method="point_buy",
            assignments=_assignments(),
        )
    )
    assert point_buy["ability_generation"]["point_buy"] == {"budget": 27, "spent": 27}

    with pytest.raises(ValueError, match="standard array"):
        apply_ability_generation(
            validate_character_sheet({}),
            method="standard_array",
            assignments={**_assignments(), "charisma": 9},
        )
    with pytest.raises(ValueError, match="exactly 27"):
        apply_ability_generation(
            validate_character_sheet({}),
            method="point_buy",
            assignments={**_assignments(), "wisdom": 8},
        )


def test_rolled_scores_record_4d6_drop_lowest_and_require_full_assignment() -> None:
    rolled = roll_ability_scores("2014", rng=random.Random(7))
    scores = sorted(item["score"] for item in rolled["rolls"])
    assignments = dict(zip(_assignments(), scores, strict=True))
    sheet = validate_character_sheet(
        apply_ability_generation(
            validate_character_sheet({}),
            method="roll_4d6_drop_lowest",
            assignments=assignments,
            rolls=rolled["rolls"],
        )
    )
    assert sheet["ability_generation"]["rolls"] == rolled["rolls"]
    assert all(item["score"] == sum(item["dice"]) - item["dropped"] for item in rolled["rolls"])


def test_engine_owned_rolls_are_recorded_before_player_assignment() -> None:
    original = validate_character_sheet({})
    pending = begin_rolled_ability_generation(original, rng=random.Random(11))
    pending_sheet = validate_character_sheet(pending["sheet"])

    assert pending["status"] == "pending_choice"
    assert pending_sheet["ability_generation"]["method"] == "roll_4d6_drop_lowest_pending"
    assert pending_sheet["ability_generation"]["assignments"] == {}
    assert original["ability_generation"]["method"] == "unrecorded"
    scores = sorted(item["score"] for item in pending["rolls"])
    assignments = dict(zip(_assignments(), scores, strict=True))

    completed = validate_character_sheet(
        apply_pending_rolled_ability_generation(
            pending_sheet,
            assignments=assignments,
        )
    )

    assert completed["ability_generation"]["method"] == "roll_4d6_drop_lowest"
    assert completed["ability_generation"]["rolls"] == pending["rolls"]
    with pytest.raises(ValueError, match="already been generated"):
        begin_rolled_ability_generation(completed, rng=random.Random(12))
