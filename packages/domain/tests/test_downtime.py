import pytest

from sagasmith_dnd.downtime import (
    completed_activity_days,
    crafting_day,
    crafting_lifestyle_cost_cp,
    lifestyle_daily_cost_cp,
    lifestyle_period_cost_cp,
    profession_support_tier,
    qualifies_downtime_day,
    record_downtime_day,
    recuperation_outcome,
    research_result,
    select_lifestyle,
    training_progress,
)

SOURCE_REF = "module:city-guide#downtime"
SOURCE_EXCERPT = "The source gives the governing downtime procedure."


@pytest.mark.parametrize(("hours", "qualifies"), [(0, False), (7, False), (8, True), (24, True)])
def test_downtime_day_requires_eight_hours(hours, qualifies):
    assert qualifies_downtime_day(hours) is qualifies


def test_downtime_day_rejects_invalid_hours_and_source():
    with pytest.raises(ValueError, match="24"):
        qualifies_downtime_day(25)
    with pytest.raises(ValueError, match="source_ref"):
        record_downtime_day(
            {}, activity="research", day_key="day-1", hours=8,
            source_ref="", source_excerpt=SOURCE_EXCERPT,
        )


def test_nonconsecutive_qualifying_days_are_source_recorded_and_deduplicated():
    state = record_downtime_day(
        {}, activity="research", day_key="day-1", hours=8,
        source_ref=SOURCE_REF, source_excerpt=SOURCE_EXCERPT,
    )
    state = record_downtime_day(
        state, activity="research", day_key="day-5", hours=7,
        source_ref=SOURCE_REF, source_excerpt=SOURCE_EXCERPT,
    )
    state = record_downtime_day(
        state, activity="research", day_key="day-9", hours=9,
        source_ref=SOURCE_REF, source_excerpt=SOURCE_EXCERPT,
    )
    assert completed_activity_days(state, "research") == 2
    assert state["days"][0]["source"]["source_ref"] == SOURCE_REF
    with pytest.raises(ValueError, match="already has"):
        record_downtime_day(
            state, activity="research", day_key="day-1", hours=8,
            source_ref=SOURCE_REF, source_excerpt=SOURCE_EXCERPT,
        )


def test_lifestyle_costs_preserve_aristocratic_minimum_and_selection_source():
    assert lifestyle_daily_cost_cp("squalid") == 10
    assert lifestyle_daily_cost_cp("modest") == 100
    assert lifestyle_period_cost_cp("aristocratic", 3) == 3000
    assert lifestyle_period_cost_cp("aristocratic", 2, aristocratic_cost_cp=1500) == 3000
    with pytest.raises(ValueError, match="at least 1000"):
        lifestyle_daily_cost_cp("aristocratic", aristocratic_cost_cp=999)
    selected = select_lifestyle(
        "comfortable", days=4, source_ref=SOURCE_REF, source_excerpt=SOURCE_EXCERPT,
    )
    assert selected["cost_cp"] == 800
    assert selected["lifestyle"] == "comfortable"


def test_crafting_enforces_proficiency_facilities_materials_and_collaboration():
    result = crafting_day(
        market_value_remaining_gp=100,
        qualifying_crafters=3,
        required_tools="smith's tools",
        proficient_tools_by_crafter=["smith's tools"] * 3,
        materials_available_cp=750,
        collaborating_in_same_place=True,
        facility_required=True,
        facility_available=True,
    )
    assert result == {"progress_gp": 15, "materials_cost_cp": 750}
    with pytest.raises(ValueError, match="every crafting collaborator"):
        crafting_day(
            market_value_remaining_gp=20, qualifying_crafters=2,
            required_tools="smith's tools",
            proficient_tools_by_crafter=["smith's tools", "weaver's tools"],
            materials_available_cp=1000, collaborating_in_same_place=True,
            facility_required=False, facility_available=True,
        )
    with pytest.raises(ValueError, match="same place"):
        crafting_day(
            market_value_remaining_gp=20, qualifying_crafters=2,
            required_tools="smith's tools", proficient_tools_by_crafter=["smith's tools"] * 2,
            materials_available_cp=1000, collaborating_in_same_place=False,
            facility_required=False, facility_available=True,
        )
    with pytest.raises(ValueError, match="facility"):
        crafting_day(
            market_value_remaining_gp=20, qualifying_crafters=1,
            required_tools="smith's tools", proficient_tools_by_crafter=["smith's tools"],
            materials_available_cp=1000, collaborating_in_same_place=True,
            facility_required=True, facility_available=False,
        )
    with pytest.raises(ValueError, match="raw materials"):
        crafting_day(
            market_value_remaining_gp=20, qualifying_crafters=1,
            required_tools="smith's tools", proficient_tools_by_crafter=["smith's tools"],
            materials_available_cp=249, collaborating_in_same_place=True,
            facility_required=False, facility_available=True,
        )


def test_crafting_lifestyle_adjustment_and_profession_support_tiers():
    assert crafting_lifestyle_cost_cp("modest") == 0
    assert crafting_lifestyle_cost_cp("comfortable") == 100
    assert profession_support_tier(
        organization_employment=False, performance_proficient=False, performance_used=False,
    ) == "modest"
    assert profession_support_tier(
        organization_employment=True, performance_proficient=False, performance_used=False,
    ) == "comfortable"
    assert profession_support_tier(
        organization_employment=False, performance_proficient=True, performance_used=True,
    ) == "wealthy"
    assert profession_support_tier(
        organization_employment=False, performance_proficient=True, performance_used=False,
    ) == "modest"


def test_recuperation_requires_days_success_and_one_provenance_bound_choice():
    with pytest.raises(ValueError, match="three qualifying days"):
        recuperation_outcome(qualifying_days=2, save_success=True)
    assert recuperation_outcome(qualifying_days=3, save_success=False) == {
        "save_dc": 15, "success": False, "choice": None,
    }
    with pytest.raises(ValueError, match="exactly one"):
        recuperation_outcome(qualifying_days=3, save_success=True)
    effect = recuperation_outcome(
        qualifying_days=3, save_success=True, choice="end_hp_recovery_effect",
        effect_id="curse-1", blocking_effect_ids=["curse-1"],
    )
    assert effect["choice"]["effect_id"] == "curse-1"
    with pytest.raises(ValueError, match="preventing HP"):
        recuperation_outcome(
            qualifying_days=3, save_success=True, choice="end_hp_recovery_effect",
            effect_id="unrelated", blocking_effect_ids=["curse-1"],
        )
    condition = recuperation_outcome(
        qualifying_days=3, save_success=True, choice="advantage_against_condition",
        condition_id="poison-1", condition_kind="poison", current_condition_ids=["poison-1"],
    )
    assert condition["choice"]["duration_hours"] == 24
    with pytest.raises(ValueError, match="current disease or poison"):
        recuperation_outcome(
            qualifying_days=3, save_success=True, choice="advantage_against_condition",
            condition_id="old-disease", condition_kind="disease", current_condition_ids=[],
        )


def test_research_waits_for_gm_requirements_and_records_disclosed_source():
    plan = {
        "available": True, "required_days": 2, "qualifying_days": 2,
        "restrictions_satisfied": True, "required_check_ids": ["library-check"],
        "passed_check_ids": [], "lifestyle": "modest",
        "plan_source_ref": SOURCE_REF, "plan_source_excerpt": SOURCE_EXCERPT,
    }
    incomplete = research_result(**plan)
    assert incomplete["complete"] is False
    assert incomplete["information_source"] is None
    assert incomplete["missing_check_ids"] == ["library-check"]
    with pytest.raises(ValueError, match="cannot be supplied"):
        research_result(
            **plan,
            information_source_ref=SOURCE_REF,
            information_source_excerpt=SOURCE_EXCERPT,
        )
    complete = research_result(
        **{**plan, "passed_check_ids": ["library-check"]},
        information_source_ref=SOURCE_REF, information_source_excerpt=SOURCE_EXCERPT,
    )
    assert complete["complete"] is True
    assert complete["research_cost_cp"] == 200
    assert complete["lifestyle_cost_cp"] == 200
    assert complete["information_source"]["source_ref"] == SOURCE_REF


def test_training_requires_willing_persisted_instructor_and_exact_completion():
    args = {
        "qualifying_days": 249, "days_to_add": 1, "instructor_id": "teacher-1",
        "instructor_persisted": True, "instructor_willing": True,
        "target_kind": "language", "target_id": "dwarvish",
        "lifestyle": "poor",
    }
    result = training_progress(**args)
    assert result["complete"] is True
    assert result["cost_gp"] == 1
    assert result["lifestyle_cost_cp"] == 20
    with pytest.raises(ValueError, match="persisted instructor"):
        training_progress(**{**args, "instructor_persisted": False})
    with pytest.raises(ValueError, match="willing instructor"):
        training_progress(**{**args, "instructor_willing": False})
    assert not training_progress(
        **{**args, "qualifying_days": 248, "required_check_ids": ["teach-check"],
           "passed_check_ids": []},
    )["complete"]
    with pytest.raises(ValueError, match="target"):
        training_progress(**{**args, "target_kind": "ability_score"})
