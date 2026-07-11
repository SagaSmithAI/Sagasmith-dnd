import random

import pytest

from sagasmith_dnd.engine import ability_modifier, proficiency_bonus, resolve_check, roll
from sagasmith_dnd.system import validate_character_sheet


def test_character_sheet_defaults_and_validation() -> None:
    sheet = validate_character_sheet(
        {
            "progression": {"level": 3},
            "abilities": {"strength": {"score": 16}},
            "combat": {"ac": {"base": 16}},
        }
    )

    assert sheet["schema_version"] == 2
    assert sheet["abilities"]["strength"]["score"] == 16
    assert sheet["abilities"]["wisdom"]["score"] == 10
    assert sheet["combat"]["ac"]["base"] == 16

    with pytest.raises(ValueError):
        validate_character_sheet({"abilities": {"strength": {"score": 31}}})
    with pytest.raises(ValueError):
        validate_character_sheet({"level": 3})


def test_dice_and_check_are_deterministic_with_seed() -> None:
    rng = random.Random(7)
    result = roll("2d6+3", rng=rng)

    assert result.total == sum(result.rolls) + 3
    assert ability_modifier(16) == 3
    assert proficiency_bonus(5) == 3

    check = resolve_check(dc=1, ability_score=10, rng=random.Random(1))
    assert check["success"] is True
