import pytest

from sagasmith_dnd.character_schema import default_character_sheet, validate_character_sheet
from sagasmith_dnd.combat_engine import CombatEngineError
from sagasmith_dnd.progression import advance_single_class_level


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

    result = advance_single_class_level(sheet, class_name="Wizard", hp_method="rolled", hp_roll=3)

    updated = validate_character_sheet(result["sheet"])
    assert updated["combat"]["hp"]["max"] == 11
    assert updated["spellcasting"]["spell_slots"]["1"]["value"] == 1
    assert updated["spellcasting"]["preparation"]["max_prepared"] == 4
    assert result["spell_choices"]["leveled_spells_to_add"] == 2


def test_level_advancement_rejects_multiclass_mismatch_and_invalid_roll() -> None:
    sheet = _single_class_sheet("Fighter", hit_die=10, constitution=14, hp=(12, 12))
    with pytest.raises(CombatEngineError, match="match"):
        advance_single_class_level(sheet, class_name="Rogue", hp_method="fixed")
    with pytest.raises(CombatEngineError, match="1 to 10"):
        advance_single_class_level(
            sheet, class_name="Fighter", hp_method="rolled", hp_roll=11
        )
    sheet["progression"]["classes"].append(
        {"name": "Rogue", "level": 1, "subclass": "", "hit_die": 8}
    )
    with pytest.raises(CombatEngineError, match="single-class"):
        advance_single_class_level(sheet, class_name="Fighter", hp_method="fixed")
