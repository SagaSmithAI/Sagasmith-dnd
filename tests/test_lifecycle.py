import pytest

from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.combat_engine import CombatEngineError
from sagasmith_dnd.lifecycle import (
    advance_effect_durations,
    advance_world_effect_durations,
    apply_rest,
    record_rest_completion,
    recover_stable_creature,
    roll_rest_hit_dice,
    stand_outside_combat,
)


def test_rest_completion_enforces_duration_and_daily_limit() -> None:
    sheet = default_character_sheet()
    with pytest.raises(CombatEngineError, match="at least 480"):
        record_rest_completion(
            sheet,
            rest_type="long_rest",
            started_elapsed_minutes=0,
            completed_elapsed_minutes=479,
        )

    recorded = record_rest_completion(
        sheet,
        rest_type="long_rest",
        started_elapsed_minutes=0,
        completed_elapsed_minutes=480,
    )
    assert recorded["combat"]["rest_history"]["last_long_rest_elapsed_minutes"] == 480
    with pytest.raises(CombatEngineError, match="in 24 hours"):
        record_rest_completion(
            recorded,
            rest_type="long_rest",
            started_elapsed_minutes=1000,
            completed_elapsed_minutes=1480,
        )

    next_day = record_rest_completion(
        recorded,
        rest_type="long_rest",
        started_elapsed_minutes=1440,
        completed_elapsed_minutes=1920,
    )
    assert next_day["combat"]["rest_history"]["last_long_rest_elapsed_minutes"] == 1920


class _SequenceRng:
    def __init__(self, *values: int) -> None:
        self.values = list(values)

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0)
        assert minimum <= value <= maximum
        return value


def test_rest_hit_dice_are_engine_rolled_from_validated_counts() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hit_dice"] = {
        "fighter:d10": {
            "label": "Fighter d10",
            "value": 2,
            "max": 2,
            "recovers_on": "long_rest",
        }
    }

    result = roll_rest_hit_dice(
        sheet,
        [{"key": "fighter:d10", "count": 2}],
        rng=_SequenceRng(4, 9),
    )

    assert result["spends"] == [
        {"key": "fighter:d10", "roll": 4},
        {"key": "fighter:d10", "roll": 9},
    ]
    assert [item["total"] for item in result["rolls"]] == [4, 9]
    with pytest.raises(CombatEngineError, match="only key and count"):
        roll_rest_hit_dice(sheet, [{"key": "fighter:d10", "roll": 10}])
    with pytest.raises(CombatEngineError, match="not enough"):
        roll_rest_hit_dice(sheet, [{"key": "fighter:d10", "count": 3}])


def test_effect_duration_and_long_rest_recovery_are_card_local() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 2, "max": 10, "temp": 4}
    sheet["resources"] = {
        "feature": {
            "label": "Feature",
            "value": 0,
            "max": 2,
            "recovers_on": "long_rest",
            "source_key": "x",
        }
    }
    sheet["effects"] = [
        {
            "id": "bless",
            "name": "Bless",
            "active": True,
            "duration": {"period": "round", "remaining": 2},
        }
    ]
    advanced = advance_effect_durations(sheet, period="round_end")
    assert advanced["sheet"]["effects"][0]["duration"]["remaining"] == 1
    result = apply_rest(advanced["sheet"], rest_type="long_rest")
    assert result["sheet"]["combat"]["hp"] == {"value": 10, "max": 10, "temp": 0}
    assert result["recovered"]["feature"] == 2


def test_long_rest_also_recovers_short_rest_resources() -> None:
    sheet = default_character_sheet()
    sheet["resources"] = {
        "channel_divinity": {
            "label": "Channel Divinity",
            "value": 0,
            "max": 1,
            "recovers_on": "short_rest",
            "source_key": "Cleric",
        }
    }

    result = apply_rest(sheet, rest_type="long_rest")

    assert result["sheet"]["resources"]["channel_divinity"]["value"] == 1
    assert result["recovered"]["channel_divinity"] == 1


def test_elapsed_time_only_advances_matching_effect_periods() -> None:
    sheet = default_character_sheet()
    sheet["effects"] = [
        {
            "id": "minute-effect",
            "name": "Minute Effect",
            "active": True,
            "duration": {"period": "minute", "remaining": 1},
        },
        {
            "id": "hour-effect",
            "name": "Hour Effect",
            "active": True,
            "duration": {"period": "hour", "remaining": 1},
        },
    ]
    result = advance_effect_durations(sheet, period="minute")
    assert result["expired"] == ["minute-effect"]
    assert result["sheet"]["effects"][1]["active"] is True


def test_effect_duration_advance_accepts_audited_multi_period_amount() -> None:
    sheet = default_character_sheet()
    sheet["effects"] = [
        {
            "id": "hourly-ward",
            "name": "Hourly Ward",
            "active": True,
            "duration": {"period": "hour", "remaining": 3},
        }
    ]

    result = advance_effect_durations(sheet, period="hour", amount=2)

    assert result["amount"] == 2
    assert result["advanced"] == ["hourly-ward"]
    assert result["sheet"]["effects"][0]["duration"]["remaining"] == 1

    expired = advance_effect_durations(result["sheet"], period="hour", amount=2)
    assert expired["expired"] == ["hourly-ward"]
    assert expired["sheet"]["effects"][0]["active"] is False


def test_effect_duration_advance_rejects_nonpositive_amount() -> None:
    with pytest.raises(CombatEngineError, match="positive integer"):
        advance_effect_durations(default_character_sheet(), period="hour", amount=0)


def test_world_effect_duration_uses_the_same_expiry_boundary() -> None:
    state = {
        "world_effects": [
            {
                "id": "mace-light",
                "active": True,
                "duration": {"period": "hour", "remaining": 1},
            }
        ]
    }
    result = advance_world_effect_durations(state, period="hour", amount=3)
    assert result["expired"] == ["mace-light"]
    assert result["state"]["world_effects"][0]["active"] is False


def test_short_rest_engine_rolls_hit_die_and_2024_long_rest_recovers_all() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2024"
    sheet["abilities"]["constitution"]["score"] = 14
    sheet["combat"]["hp"] = {"value": 2, "max": 20, "temp": 0}
    sheet["combat"]["hit_dice"] = {
        "d8": {"label": "d8", "value": 1, "max": 3, "recovers_on": "none", "source_key": "cleric"}
    }
    short_rest = apply_rest(
        sheet,
        rest_type="short_rest",
        hit_dice_spends=[{"key": "d8", "count": 1}],
        rng=_SequenceRng(4),
    )
    assert short_rest["hit_die_healing"] == 6
    assert short_rest["hit_dice_rolls"][0]["total"] == 4
    assert short_rest["sheet"]["combat"]["hp"]["value"] == 8
    long_rest = apply_rest(short_rest["sheet"], rest_type="long_rest")
    assert long_rest["sheet"]["combat"]["hit_dice"]["d8"]["value"] == 3


def test_rest_rejects_irrelevant_recovery_inputs_before_rng() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 5, "max": 10, "temp": 0}
    sheet["combat"]["hit_dice"] = {
        "d8": {"label": "d8", "value": 1, "max": 1, "recovers_on": "none"}
    }

    with pytest.raises(CombatEngineError, match="only during a short rest"):
        apply_rest(
            sheet,
            rest_type="long_rest",
            hit_dice_spends=[{"key": "d8", "count": 1}],
            rng=_SequenceRng(),
        )
    with pytest.raises(CombatEngineError, match="recover only during a long rest"):
        apply_rest(sheet, rest_type="short_rest", hit_dice_recovery={"d8": 1})
    with pytest.raises(CombatEngineError, match="only on a long rest"):
        apply_rest(sheet, rest_type="short_rest", food_and_drink=True)


def test_arcane_recovery_is_a_once_per_long_rest_short_rest_choice() -> None:
    sheet = default_character_sheet()
    sheet["progression"] = {
        "level": 2,
        "classes": [{"name": "Wizard", "level": 2, "hit_die": 6}],
    }
    sheet["combat"]["hp"] = {"value": 8, "max": 12, "temp": 0}
    sheet["spellcasting"]["spell_slots"] = {
        "1": {
            "label": "Level 1 spell slots",
            "value": 0,
            "max": 3,
            "recovers_on": "long_rest",
            "source_key": "Wizard",
            "slot_level": 1,
        }
    }
    sheet["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2014.feature.wizard-arcane-recovery",
            "name": "Arcane Recovery",
            "source_key": "Wizard",
            "uses": {
                "label": "",
                "value": 0,
                "max": 0,
                "recovers_on": "none",
            },
        }
    ]

    recovered = apply_rest(
        sheet,
        rest_type="short_rest",
        arcane_recovery={"1": 1},
        world_day=1,
    )

    assert recovered["arcane_recovery"] == {
        "allowance": 1,
        "used_levels": 1,
        "recovered": {"1": 1},
        "campaign_day": 1,
    }
    assert recovered["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] == 1
    feature_uses = recovered["sheet"]["content"]["features"][0]["uses"]
    assert feature_uses["value"] == 0
    assert feature_uses["max"] == 1
    assert feature_uses["recovers_on"] == "manual"
    with pytest.raises(CombatEngineError, match="campaign day"):
        apply_rest(
            recovered["sheet"],
            rest_type="short_rest",
            arcane_recovery={"1": 1},
            world_day=1,
        )
    long_rested = apply_rest(recovered["sheet"], rest_type="long_rest")
    assert long_rested["sheet"]["content"]["features"][0]["uses"]["value"] == 0
    next_day_sheet = long_rested["sheet"]
    next_day_sheet["spellcasting"]["spell_slots"]["1"]["value"] = 0
    next_day = apply_rest(
        next_day_sheet,
        rest_type="short_rest",
        arcane_recovery={"1": 1},
        world_day=2,
    )
    assert next_day["arcane_recovery"]["campaign_day"] == 2

    with pytest.raises(CombatEngineError, match="exceeds half"):
        apply_rest(
            sheet,
            rest_type="short_rest",
            arcane_recovery={"1": 2},
            world_day=1,
        )
    with pytest.raises(CombatEngineError, match="only when finishing a short rest"):
        apply_rest(
            sheet,
            rest_type="long_rest",
            arcane_recovery={"1": 1},
            world_day=1,
        )


def test_stable_creature_recovers_one_hp_after_rolled_hours() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 0, "max": 12, "temp": 0}
    sheet["combat"]["death_saves"] = {"successes": 0, "failures": 0}
    sheet["conditions"] = ["prone", "stable", "unconscious"]

    result = recover_stable_creature(sheet, recovery_hours=3)

    assert result["recovery_hours"] == 3
    assert result["sheet"]["combat"]["hp"]["value"] == 1
    assert result["sheet"]["combat"]["death_saves"] == {"successes": 0, "failures": 0}
    assert result["sheet"]["conditions"] == ["prone"]
    assert sheet["combat"]["hp"]["value"] == 0


def test_stable_recovery_rejects_nonstable_dead_or_invalid_roll() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 0, "max": 12, "temp": 0}
    with pytest.raises(CombatEngineError, match="Stable creature at 0"):
        recover_stable_creature(sheet, recovery_hours=1)
    sheet["conditions"] = ["dead", "stable", "unconscious"]
    with pytest.raises(CombatEngineError, match="dead creature"):
        recover_stable_creature(sheet, recovery_hours=1)
    with pytest.raises(CombatEngineError, match="integer from 1 to 4"):
        recover_stable_creature(sheet, recovery_hours=5)


def test_conscious_recovered_creature_can_stand_outside_combat() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 1, "max": 12, "temp": 0}
    sheet["conditions"] = ["prone"]

    result = stand_outside_combat(sheet)

    assert result["status"] == "stood"
    assert result["sheet"]["conditions"] == []
    assert sheet["conditions"] == ["prone"]


def test_outside_combat_stand_rejects_unconscious_or_nonprone_creature() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 1, "max": 12, "temp": 0}
    sheet["conditions"] = ["prone", "unconscious"]
    with pytest.raises(CombatEngineError, match="conscious living creature"):
        stand_outside_combat(sheet)
    sheet["conditions"] = []
    with pytest.raises(CombatEngineError, match="Prone condition"):
        stand_outside_combat(sheet)
