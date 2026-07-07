import random

import pytest

from sagasmith_dnd.checks import resolve_character_check
from sagasmith_dnd.engine import ability_modifier, proficiency_bonus, resolve_check, roll
from sagasmith_dnd.system import validate_character_sheet


def test_character_sheet_defaults_and_validation() -> None:
    sheet = validate_character_sheet({"abilities": {"strength": 16}, "level": 3})

    assert sheet["abilities"]["strength"] == 16
    assert sheet["abilities"]["wisdom"] == 10
    assert sheet["armor_class"] == 10

    with pytest.raises(ValueError):
        validate_character_sheet({"abilities": {"strength": 31}})


def test_dice_and_check_are_deterministic_with_seed() -> None:
    rng = random.Random(7)
    result = roll("2d6+3", rng=rng)

    assert result.total == sum(result.rolls) + 3
    assert ability_modifier(16) == 3
    assert proficiency_bonus(5) == 3

    check = resolve_check(dc=1, ability_score=10, rng=random.Random(1))
    assert check["success"] is True


def test_2014_character_check_uses_skill_and_expertise() -> None:
    sheet = validate_character_sheet(
        {
            "level": 5,
            "abilities": {"wisdom": 16, "dexterity": 14},
            "proficiencies": ["skill:perception", "save:dexterity"],
            "expertise": ["perception"],
        }
    )

    perception = resolve_character_check(
        sheet=sheet,
        check_type="skill",
        skill="perception",
        dc=1,
    )
    assert perception["ruleset"] == "5e-2014"
    assert perception["ability"] == "wisdom"
    assert perception["expertise"] is True
    assert perception["proficiency_bonus"] == 6

    save = resolve_character_check(
        sheet=sheet,
        check_type="save",
        ability="dexterity",
        dc=1,
    )
    assert save["ability"] == "dexterity"
    assert save["proficient"] is True
    assert save["proficiency_bonus"] == 3

