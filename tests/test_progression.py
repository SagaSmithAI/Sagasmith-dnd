import pytest

from sagasmith_dnd.character_schema import default_character_sheet, validate_character_sheet
from sagasmith_dnd.combat_engine import CombatEngineError
from sagasmith_dnd.progression import (
    advance_single_class_level,
    apply_per_level_hit_point_bonus,
    award_experience,
    experience_status,
)


class _SequenceRng:
    def __init__(self, *values: int) -> None:
        self.values = list(values)

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0)
        assert minimum <= value <= maximum
        return value


def _single_class_sheet(
    class_name: str, *, hit_die: int, constitution: int, hp: tuple[int, int]
) -> dict:
    sheet = default_character_sheet()
    sheet["progression"]["classes"] = [
        {"name": class_name, "level": 1, "subclass": "", "hit_die": hit_die}
    ]
    sheet["abilities"]["constitution"]["score"] = constitution
    sheet["combat"]["hp"] = {"value": hp[0], "max": hp[1], "temp": 0}
    sheet["combat"]["hit_dice"] = {
        f"d{hit_die}": {
            "label": f"d{hit_die}",
            "value": 1,
            "max": 1,
            "recovers_on": "long_rest",
            "source_key": class_name,
        }
    }
    return sheet


def test_fixed_level_advancement_updates_max_hp_and_hit_die_without_healing() -> None:
    sheet = _single_class_sheet("Rogue", hit_die=8, constitution=14, hp=(5, 10))

    result = advance_single_class_level(
        sheet,
        class_name="Rogue",
        hp_method="fixed",
        source="module milestone",
    )

    updated = validate_character_sheet(result["sheet"])
    assert updated["progression"]["level"] == 2
    assert updated["progression"]["classes"][0]["level"] == 2
    assert updated["combat"]["hp"] == {"value": 5, "max": 17, "temp": 0}
    assert updated["combat"]["hit_dice"]["d8"]["value"] == 2
    assert updated["combat"]["hit_dice"]["d8"]["max"] == 2
    assert updated["combat"]["hp_progression"] == [
        {"level": 2, "method": "fixed", "value": 7, "source": "module milestone"}
    ]
    assert result["spellcasting"]["kind"] == "none"
    assert result["spell_choices"] == {"cantrips_to_add": 0, "leveled_spells_to_add": 0}
    assert sheet["progression"]["level"] == 1


def test_per_level_hp_bonus_is_separate_from_the_minimum_class_gain() -> None:
    sheet = _single_class_sheet("Cleric", hit_die=8, constitution=16, hp=(7, 12))
    sheet["abilities"]["wisdom"]["score"] = 14
    sheet["spellcasting"]["ability"] = "wisdom"
    sheet["spellcasting"]["preparation"] = {
        "mode": "prepared",
        "max_prepared": 3,
        "changes_on": "long_rest",
        "selected_spell_ids": [],
    }
    sheet["spellcasting"]["spell_slots"] = {
        "1": {
            "label": "Level 1 spell slots",
            "value": 0,
            "max": 2,
            "recovers_on": "long_rest",
            "source_key": "Cleric",
            "slot_level": 1,
        }
    }

    result = advance_single_class_level(
        sheet,
        class_name="Cleric",
        hp_method="fixed",
        hp_per_level_bonus=1,
    )

    updated = validate_character_sheet(result["sheet"])
    assert updated["combat"]["hp"] == {"value": 7, "max": 21, "temp": 0}
    assert result["hit_points"]["class_gain"] == 8
    assert result["hit_points"]["per_level_bonus"] == 1
    assert updated["spellcasting"]["spell_slots"]["1"]["max"] == 3
    assert updated["spellcasting"]["spell_slots"]["1"]["value"] == 1
    assert updated["spellcasting"]["preparation"]["max_prepared"] == 4


def test_wizard_gains_only_new_slot_capacity_and_reports_spellbook_choices() -> None:
    sheet = _single_class_sheet("Wizard", hit_die=6, constitution=12, hp=(1, 7))
    sheet["abilities"]["intelligence"]["score"] = 14
    sheet["spellcasting"]["ability"] = "intelligence"
    sheet["spellcasting"]["preparation"] = {
        "mode": "spellbook",
        "max_prepared": 3,
        "changes_on": "long_rest",
        "selected_spell_ids": [],
    }
    sheet["spellcasting"]["spellbook"] = {"enabled": True, "spell_ids": []}
    sheet["spellcasting"]["spell_slots"] = {
        "1": {
            "label": "Level 1 spell slots",
            "value": 0,
            "max": 2,
            "recovers_on": "long_rest",
            "source_key": "Wizard",
            "slot_level": 1,
        }
    }

    result = advance_single_class_level(
        sheet,
        class_name="Wizard",
        hp_method="rolled",
        rng=_SequenceRng(3),
    )

    updated = validate_character_sheet(result["sheet"])
    assert updated["combat"]["hp"]["max"] == 11
    assert updated["spellcasting"]["spell_slots"]["1"]["value"] == 1
    assert updated["spellcasting"]["preparation"]["max_prepared"] == 4
    assert result["spell_choices"]["leveled_spells_to_add"] == 2
    assert result["hit_points"]["roll"]["expression"] == "1d6"
    assert result["hit_points"]["roll"]["total"] == 3


def test_level_advancement_rejects_multiclass_mismatch_and_invalid_method() -> None:
    sheet = _single_class_sheet("Fighter", hit_die=10, constitution=14, hp=(12, 12))
    with pytest.raises(CombatEngineError, match="match"):
        advance_single_class_level(sheet, class_name="Rogue", hp_method="fixed")
    with pytest.raises(CombatEngineError, match="fixed or rolled"):
        advance_single_class_level(sheet, class_name="Fighter", hp_method="unknown")
    sheet["progression"]["classes"].append(
        {"name": "Rogue", "level": 1, "subclass": "", "hit_die": 8}
    )
    with pytest.raises(CombatEngineError, match="single-class"):
        advance_single_class_level(sheet, class_name="Fighter", hp_method="fixed")


def test_experience_award_reports_eligibility_without_auto_leveling() -> None:
    sheet = _single_class_sheet("Fighter", hit_die=10, constitution=14, hp=(12, 12))

    first = award_experience(sheet, amount=299)
    assert first["sheet"]["progression"]["level"] == 1
    assert first["advancement"] == {
        "level": 1,
        "xp": 299,
        "current_level_threshold": 0,
        "next_level": 2,
        "next_level_threshold": 300,
        "xp_to_next_level": 1,
        "eligible": False,
    }

    second = award_experience(first["sheet"], amount=1)
    assert second["sheet"]["progression"]["level"] == 1
    assert second["advancement"]["eligible"] is True
    assert second["advancement"]["xp_to_next_level"] == 0
    assert sheet["progression"]["xp"] == 0

    with pytest.raises(CombatEngineError, match="positive integer"):
        award_experience(sheet, amount=0)


def test_experience_status_handles_level_twenty_without_a_false_next_level() -> None:
    sheet = _single_class_sheet("Fighter", hit_die=10, constitution=14, hp=(12, 12))
    sheet["progression"]["level"] = 20
    sheet["progression"]["classes"][0]["level"] = 20
    sheet["progression"]["xp"] = 400_000

    assert experience_status(sheet) == {
        "level": 20,
        "xp": 400_000,
        "current_level_threshold": 355_000,
        "next_level": None,
        "next_level_threshold": None,
        "xp_to_next_level": None,
        "eligible": False,
    }


def test_per_level_hit_point_bonus_updates_every_recorded_level() -> None:
    sheet = _single_class_sheet("Cleric", hit_die=8, constitution=16, hp=(11, 20))
    sheet["progression"]["level"] = 2
    sheet["progression"]["classes"][0]["level"] = 2
    sheet["combat"]["hp_progression"] = [
        {"level": 1, "method": "manual", "value": 11, "source": "Cleric level 1"},
        {"level": 2, "method": "fixed", "value": 9, "source": "Cleric level 2"},
    ]

    updated = apply_per_level_hit_point_bonus(
        sheet,
        amount=1,
        source="Hill Dwarf: Dwarven Toughness",
    )

    assert updated["combat"]["hp"] == {"value": 13, "max": 22, "temp": 0}
    assert [entry["value"] for entry in updated["combat"]["hp_progression"]] == [12, 10]
    assert all(
        "Dwarven Toughness" in entry["source"]
        for entry in updated["combat"]["hp_progression"]
    )
    assert sheet["combat"]["hp"]["max"] == 20


def test_per_level_hit_point_bonus_rejects_a_partial_existing_ledger() -> None:
    sheet = _single_class_sheet("Cleric", hit_die=8, constitution=16, hp=(11, 20))
    sheet["progression"]["level"] = 2
    sheet["progression"]["classes"][0]["level"] = 2
    sheet["combat"]["hp_progression"] = [
        {"level": 2, "method": "fixed", "value": 9, "source": "Cleric level 2"}
    ]

    with pytest.raises(CombatEngineError, match="every existing level"):
        apply_per_level_hit_point_bonus(
            sheet,
            amount=1,
            source="Hill Dwarf: Dwarven Toughness",
        )
