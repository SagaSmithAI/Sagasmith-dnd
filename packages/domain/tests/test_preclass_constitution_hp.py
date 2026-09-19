import pytest

from sagasmith_dnd.character_schema import default_character_sheet, validate_character_sheet
from sagasmith_dnd.progression import (
    apply_constitution_score_hit_point_change,
    apply_per_level_hit_point_bonus,
    initialize_base_class,
)


def initialize(sheet):
    return initialize_base_class(
        sheet,
        class_name="Rogue",
        skill_choices=[],
        source="fixture class",
        class_definition={
            "hit_die": 8,
            "saving_throw_proficiencies": ["dexterity", "intelligence"],
            "armor_proficiencies": [],
            "weapon_proficiencies": [],
            "tool_proficiencies": [],
            "skill_choice_count": 0,
            "skill_options": [],
        },
    )["sheet"]


@pytest.mark.parametrize("order", ["class-con-bonus", "con-bonus-class", "bonus-con-class"])
def test_initial_hit_points_do_not_depend_on_selection_order(order):
    sheet = default_character_sheet()
    sheet["abilities"]["constitution"]["score"] = 14
    for step in order.split("-"):
        if step == "class":
            sheet = initialize(sheet)
        elif step == "con":
            sheet["abilities"]["constitution"]["score"] = 16
            sheet = apply_constitution_score_hit_point_change(
                sheet,
                previous_score=14,
                new_score=16,
                source="fixture species",
                adjust_current=True,
            )
        else:
            sheet = apply_per_level_hit_point_bonus(
                sheet,
                amount=1,
                source="fixture toughness",
                adjust_current=True,
            )
        sheet = validate_character_sheet(sheet)
    assert sheet["combat"]["hp"] == {"max": 12, "value": 12, "temp": 0}
    assert "preclass_constitution_hp_adjustment" not in sheet["combat"]


def test_manual_classless_hit_points_still_receive_constitution_adjustment():
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"max": 9, "value": 7, "temp": 0}
    changed = apply_constitution_score_hit_point_change(
        sheet,
        previous_score=14,
        new_score=16,
        source="fixture manual actor",
    )
    assert changed["combat"]["hp"] == {"max": 10, "value": 7, "temp": 0}
