from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.combat_engine import CombatEngineError
from sagasmith_dnd.lifecycle import (
    advance_effect_durations,
    advance_elapsed_effect_durations,
    advance_elapsed_world_effect_durations,
    advance_source_turn_effect_durations,
    advance_world_effect_durations,
    apply_raise_dead_to_sheet,
    apply_rest,
    expire_combat_bound_effects,
    initialize_source_state,
    knock_prone_outside_combat,
    minimum_rest_minutes,
    record_rest_completion,
    recover_stable_creature,
    roll_rest_hit_dice,
    stand_outside_combat,
)
from sagasmith_dnd.rule_engine import resolution_context


def test_rest_minimums_have_one_runtime_authority() -> None:
    assert minimum_rest_minutes("short_rest") == 60
    assert minimum_rest_minutes("long_rest") == 480
    assert minimum_rest_minutes("long_rest", allows_trance=True) == 240


def test_2024_partial_short_rest_resource_recovery_and_full_long_rest() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2024"
    sheet["combat"]["hp"] = {"value": 5, "max": 10, "temp": 0}
    sheet["resources"]["second_wind"] = {
        "label": "Second Wind",
        "value": 0,
        "max": 3,
        "recovers_on": "short_rest",
        "recovery_amounts": {"short_rest": 1, "long_rest": "all"},
        "source_key": "Fighter",
    }

    short = apply_rest(sheet, rest_type="short_rest")
    assert short["sheet"]["resources"]["second_wind"]["value"] == 1
    assert short["recovered"]["second_wind"] == 1

    long = apply_rest(short["sheet"], rest_type="long_rest")
    assert long["sheet"]["resources"]["second_wind"]["value"] == 3
    assert long["recovered"]["second_wind"] == 2


def test_raise_dead_restores_one_hp_and_reduces_its_ordeal_each_long_rest() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["combat"]["hp"] = {"value": 0, "max": 24, "temp": 0}
    sheet["combat"]["death_saves"] = {"successes": 1, "failures": 3}
    sheet["conditions"] = ["dead", "poisoned", "prone", "unconscious"]
    sheet["effects"] = [
        {
            "id": "wyvern-poison",
            "name": "Wyvern poison",
            "kind": "poison",
            "source": "wyvern-stinger",
            "active": True,
            "concentration": False,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [{"path": "conditions", "mode": "add", "value": "poisoned"}],
            "description": "A poison affecting the creature at death.",
        },
        {
            "id": "magical-curse",
            "name": "Magical curse",
            "kind": "curse",
            "source": "hexed-idol",
            "active": True,
            "concentration": False,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [],
            "description": "Raise Dead does not remove curses.",
        },
    ]

    revived = apply_raise_dead_to_sheet(
        sheet,
        elapsed_days=1,
        soul_willing=True,
        body_intact=True,
        source_ref="module:rise-of-tiamat:p57",
    )
    assert revived["status"] == "revived"
    assert revived["sheet"]["combat"]["hp"]["value"] == 1
    assert revived["sheet"]["combat"]["death_saves"] == {"successes": 0, "failures": 0}
    assert revived["sheet"]["conditions"] == ["prone"]
    assert revived["neutralized_effect_ids"] == ["wyvern-poison"]
    assert not next(
        effect for effect in revived["sheet"]["effects"] if effect["id"] == "wyvern-poison"
    )["active"]
    assert next(
        effect for effect in revived["sheet"]["effects"] if effect["id"] == "magical-curse"
    )["active"]
    ordeal = next(
        effect for effect in revived["sheet"]["effects"] if effect["kind"] == "revival_ordeal"
    )
    assert {change["value"] for change in ordeal["changes"]} == {-4}

    current = revived["sheet"]
    for expected in (-3, -2, -1):
        rested = apply_rest(current, rest_type="long_rest")
        assert rested["revival_ordeals"][0]["after"] == expected
        current = rested["sheet"]
        active = next(
            effect
            for effect in current["effects"]
            if effect["kind"] == "revival_ordeal" and effect["active"]
        )
        assert {change["value"] for change in active["changes"]} == {expected}
    final = apply_rest(current, rest_type="long_rest")
    assert final["revival_ordeals"][0]["after"] == 0
    assert not any(
        effect["active"] and effect["kind"] == "revival_ordeal"
        for effect in final["sheet"]["effects"]
    )


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"elapsed_days": 11}, "10 days"),
        ({"soul_willing": False}, "willing soul"),
        ({"body_intact": False}, "body parts"),
    ],
)
def test_raise_dead_rejects_source_conditions_that_make_the_spell_fail(
    patch: dict, message: str
) -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["combat"]["hp"] = {"value": 0, "max": 8, "temp": 0}
    sheet["conditions"] = ["dead", "prone"]
    arguments = {
        "elapsed_days": 1,
        "soul_willing": True,
        "body_intact": True,
        "source_ref": "srd2014:raise-dead",
        **patch,
    }
    with pytest.raises(CombatEngineError, match=message):
        apply_raise_dead_to_sheet(sheet, **arguments)


def test_rest_completion_enforces_duration_and_daily_limit() -> None:
    sheet = default_character_sheet()
    long_schedule = {
        "sleep_minutes": 360,
        "light_activity_minutes": 120,
        "strenuous_activity_minutes": 0,
    }
    short_schedule = {
        "sleep_minutes": 0,
        "light_activity_minutes": 60,
        "strenuous_activity_minutes": 0,
    }
    with pytest.raises(CombatEngineError, match="at least 480"):
        record_rest_completion(
            sheet,
            rest_type="long_rest",
            started_elapsed_ticks=0,
            completed_elapsed_ticks=4790,
        )

    recorded = record_rest_completion(
        sheet,
        rest_type="long_rest",
        started_elapsed_ticks=0,
        completed_elapsed_ticks=4800,
        rest_schedule=long_schedule,
    )
    assert recorded["combat"]["rest_history"]["last_long_rest_elapsed_ticks"] == 4800
    with pytest.raises(CombatEngineError, match="in 24 hours"):
        record_rest_completion(
            recorded,
            rest_type="long_rest",
            started_elapsed_ticks=10000,
            completed_elapsed_ticks=14800,
            rest_schedule=long_schedule,
        )
    with pytest.raises(CombatEngineError, match="same campaign time"):
        record_rest_completion(
            recorded,
            rest_type="short_rest",
            started_elapsed_ticks=4200,
            completed_elapsed_ticks=4800,
            rest_schedule=short_schedule,
        )

    next_day = record_rest_completion(
        recorded,
        rest_type="long_rest",
        started_elapsed_ticks=14400,
        completed_elapsed_ticks=19200,
        rest_schedule=long_schedule,
    )
    assert next_day["combat"]["rest_history"]["last_long_rest_elapsed_ticks"] == 19200


def test_rest_completion_rejects_incomplete_or_interrupted_schedules() -> None:
    sheet = default_character_sheet()
    with pytest.raises(CombatEngineError, match="explicit rest_schedule"):
        record_rest_completion(
            sheet,
            rest_type="short_rest",
            started_elapsed_ticks=0,
            completed_elapsed_ticks=600,
        )
    with pytest.raises(CombatEngineError, match="more strenuous"):
        record_rest_completion(
            sheet,
            rest_type="short_rest",
            started_elapsed_ticks=0,
            completed_elapsed_ticks=600,
            rest_schedule={
                "sleep_minutes": 0,
                "light_activity_minutes": 59,
                "strenuous_activity_minutes": 1,
            },
        )
    with pytest.raises(CombatEngineError, match="at least 6 hours"):
        record_rest_completion(
            sheet,
            rest_type="long_rest",
            started_elapsed_ticks=0,
            completed_elapsed_ticks=4800,
            rest_schedule={
                "sleep_minutes": 359,
                "light_activity_minutes": 121,
                "strenuous_activity_minutes": 0,
            },
        )
    with pytest.raises(CombatEngineError, match="interrupts"):
        record_rest_completion(
            sheet,
            rest_type="long_rest",
            started_elapsed_ticks=0,
            completed_elapsed_ticks=4800,
            rest_schedule={
                "sleep_minutes": 360,
                "light_activity_minutes": 60,
                "strenuous_activity_minutes": 60,
            },
        )


def test_source_granted_trance_completes_a_long_rest_in_four_hours() -> None:
    sheet = default_character_sheet()
    sheet["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2014.species-feature.elf-trance",
            "name": "Trance",
            "source_key": "Elf",
            "description": "Four hours of trance grants the benefit of eight hours of sleep.",
        }
    ]
    schedule = {
        "sleep_minutes": 0,
        "trance_minutes": 240,
        "light_activity_minutes": 0,
        "strenuous_activity_minutes": 0,
    }

    recorded = record_rest_completion(
        sheet,
        rest_type="long_rest",
        started_elapsed_ticks=0,
        completed_elapsed_ticks=2400,
        rest_schedule=schedule,
    )

    assert recorded["combat"]["rest_history"]["last_long_rest_elapsed_ticks"] == 2400
    with pytest.raises(CombatEngineError, match="at least 480"):
        record_rest_completion(
            default_character_sheet(),
            rest_type="long_rest",
            started_elapsed_ticks=0,
            completed_elapsed_ticks=2400,
            rest_schedule=schedule,
        )


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


def test_expiring_timed_conditions_removes_only_conditions_owned_by_the_effect() -> None:
    sheet = default_character_sheet()
    sheet["conditions"] = ["poisoned", "paralyzed", "prone"]
    sheet["effects"] = [
        {
            "id": "giant-spider-poison",
            "name": "Giant Spider Poison",
            "kind": "timed_conditions",
            "active": True,
            "duration": {"period": "hour", "remaining": 1},
            "changes": [
                {
                    "path": "conditions",
                    "mode": "add",
                    "value": ["poisoned", "paralyzed"],
                }
            ],
        }
    ]

    result = advance_effect_durations(sheet, period="hour")

    assert result["expired"] == ["giant-spider-poison"]
    assert result["sheet"]["conditions"] == ["prone"]
    assert result["sheet"]["effects"][0]["ended_reason"] == "duration_expired"


def test_expiring_timed_conditions_preserves_condition_from_an_active_effect() -> None:
    sheet = default_character_sheet()
    sheet["conditions"] = ["poisoned"]
    sheet["effects"] = [
        {
            "id": "expiring-poison",
            "name": "Expiring Poison",
            "kind": "timed_conditions",
            "active": True,
            "duration": {"period": "hour", "remaining": 1},
            "changes": [{"path": "conditions", "mode": "add", "value": "poisoned"}],
        },
        {
            "id": "ongoing-poison",
            "name": "Ongoing Poison",
            "kind": "timed_conditions",
            "active": True,
            "duration": {"period": "hour", "remaining": 2},
            "changes": [{"path": "conditions", "mode": "add", "value": "poisoned"}],
        },
    ]

    result = advance_effect_durations(sheet, period="hour")

    assert result["sheet"]["conditions"] == ["poisoned"]
    assert result["sheet"]["effects"][1]["duration"]["remaining"] == 1


def test_source_turn_start_expires_only_effects_owned_by_that_source() -> None:
    sheet = default_character_sheet()
    sheet["conditions"] = ["charmed", "frightened", "prone"]
    sheet["effects"] = [
        {
            "id": "dazing-gazer-a",
            "name": "Dazing Ray",
            "kind": "timed_conditions",
            "source": "gazer-a",
            "active": True,
            "duration": {"period": "source_turn_start", "remaining": 1},
            "changes": [{"path": "conditions", "mode": "add", "value": "charmed"}],
        },
        {
            "id": "fear-gazer-b",
            "name": "Fear Ray",
            "kind": "timed_conditions",
            "source": "gazer-b",
            "active": True,
            "duration": {"period": "source_turn_start", "remaining": 1},
            "changes": [{"path": "conditions", "mode": "add", "value": "frightened"}],
        },
    ]

    result = advance_source_turn_effect_durations(sheet, source_actor_id="gazer-a")

    assert result["expired"] == ["dazing-gazer-a"]
    assert result["sheet"]["conditions"] == ["frightened", "prone"]
    assert result["sheet"]["effects"][1]["active"] is True


def test_combat_end_expires_every_combat_clock_but_preserves_elapsed_effects() -> None:
    sheet = default_character_sheet()
    sheet["conditions"] = ["frightened", "poisoned"]
    sheet["effects"] = [
        {
            "id": "fear-ray",
            "name": "Fear Ray",
            "kind": "timed_conditions",
            "source": "gazer",
            "active": True,
            "duration": {"period": "source_turn_start", "remaining": 1},
            "changes": [{"path": "conditions", "mode": "add", "value": "frightened"}],
        },
        {
            "id": "shield",
            "name": "Shield",
            "kind": "spell_shield",
            "active": True,
            "duration": {"period": "turn_start", "remaining": 1},
            "changes": [],
        },
        {
            "id": "encounter-bonus",
            "name": "Encounter Bonus",
            "kind": "custom",
            "active": True,
            "duration": {"period": "encounter", "remaining": 3},
            "changes": [],
        },
        {
            "id": "long-poison",
            "name": "Long Poison",
            "kind": "timed_conditions",
            "active": True,
            "duration": {"period": "hour", "remaining": 1},
            "changes": [{"path": "conditions", "mode": "add", "value": "poisoned"}],
        },
    ]

    result = expire_combat_bound_effects(sheet)

    assert result["expired"] == ["fear-ray", "shield", "encounter-bonus"]
    assert result["sheet"]["conditions"] == ["poisoned"]
    by_id = {effect["id"]: effect for effect in result["sheet"]["effects"]}
    assert all(
        by_id[effect_id]["ended_reason"] == "combat_ended" for effect_id in result["expired"]
    )
    assert by_id["long-poison"]["active"] is True


def test_elapsed_ticks_accumulate_for_hour_actor_effects() -> None:
    sheet = default_character_sheet()
    sheet["conditions"] = ["poisoned", "paralyzed", "prone"]
    sheet["effects"] = [
        {
            "id": "giant-spider-poison",
            "name": "Giant Spider Poison",
            "kind": "timed_conditions",
            "active": True,
            "duration": {"period": "hour", "remaining": 1},
            "changes": [
                {
                    "path": "conditions",
                    "mode": "add",
                    "value": ["poisoned", "paralyzed"],
                }
            ],
        }
    ]

    first = advance_elapsed_effect_durations(sheet, elapsed_ticks=300)
    assert first["expired"] == []
    assert first["sheet"]["effects"][0]["duration"] == {
        "period": "hour",
        "remaining": 1,
        "elapsed_ticks_remainder": 300,
    }

    second = advance_elapsed_effect_durations(first["sheet"], elapsed_ticks=300)
    assert second["expired"] == ["giant-spider-poison"]
    assert second["sheet"]["conditions"] == ["prone"]
    assert second["sheet"]["effects"][0]["active"] is False


def test_elapsed_ticks_clear_invisibility_when_spell_expires() -> None:
    sheet = default_character_sheet()
    sheet["conditions"] = ["invisible", "prone"]
    sheet["effects"] = [
        {
            "id": "invisibility",
            "name": "Invisibility",
            "kind": "concentration",
            "source_spell_id": "dnd5e.content.srd2014.spell.invisibility",
            "active": True,
            "concentration": True,
            "duration": {"period": "hour", "remaining": 1},
            "changes": [],
        }
    ]

    result = advance_elapsed_effect_durations(sheet, elapsed_ticks=600)

    assert result["expired"] == ["invisibility"]
    assert result["sheet"]["conditions"] == ["prone"]
    assert result["sheet"]["effects"][0]["ended_reason"] == "duration_expired"


def test_elapsed_ticks_clear_turned_when_turn_undead_expires() -> None:
    sheet = default_character_sheet()
    sheet["conditions"] = ["turned", "prone"]
    sheet["effects"] = [
        {
            "id": "turn-undead",
            "name": "Turn Undead",
            "kind": "turn_undead",
            "active": True,
            "duration": {"period": "minute", "remaining": 1},
        }
    ]

    result = advance_elapsed_effect_durations(sheet, elapsed_ticks=10)

    assert result["expired"] == ["turn-undead"]
    assert result["sheet"]["conditions"] == ["prone"]


def test_elapsed_ticks_advance_minute_hour_and_day_world_effects() -> None:
    state = {
        "world_effects": [
            {
                "id": "minutes",
                "active": True,
                "duration": {"period": "minute", "remaining": 90},
            },
            {
                "id": "hours",
                "active": True,
                "duration": {"period": "hour", "remaining": 2},
            },
            {
                "id": "days",
                "active": True,
                "duration": {"period": "day", "remaining": 1},
            },
        ]
    }

    first = advance_elapsed_world_effect_durations(state, elapsed_ticks=600)
    assert first["state"]["world_effects"][0]["duration"]["remaining"] == 30
    assert first["state"]["world_effects"][1]["duration"]["remaining"] == 1
    assert first["state"]["world_effects"][2]["duration"] == {
        "period": "day",
        "remaining": 1,
        "elapsed_ticks_remainder": 600,
    }

    second = advance_elapsed_world_effect_durations(first["state"], elapsed_ticks=13800)
    assert set(second["expired"]) == {"minutes", "hours", "days"}


def test_minute_effect_duration_is_relative_to_its_start_tick() -> None:
    sheet = default_character_sheet()
    sheet["effects"] = [
        {
            "id": "one-minute",
            "name": "One Minute",
            "active": True,
            "duration": {"period": "minute", "remaining": 1},
        }
    ]

    after_seven_rounds = advance_elapsed_effect_durations(sheet, elapsed_ticks=7)
    assert after_seven_rounds["expired"] == []
    assert after_seven_rounds["sheet"]["effects"][0]["duration"] == {
        "period": "minute",
        "remaining": 1,
        "elapsed_ticks_remainder": 7,
    }

    after_ten_rounds = advance_elapsed_effect_durations(
        after_seven_rounds["sheet"],
        elapsed_ticks=3,
    )
    assert after_ten_rounds["expired"] == ["one-minute"]


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


def test_2014_long_rest_does_not_heal_above_exhaustion_reduced_maximum() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["combat"]["hp"] = {"value": 1, "max": 37, "temp": 0}
    sheet["combat"]["exhaustion"] = 4

    without_supplies = apply_rest(sheet, rest_type="long_rest")
    with_supplies = apply_rest(sheet, rest_type="long_rest", food_and_drink=True)

    assert without_supplies["sheet"]["combat"]["exhaustion"] == 4
    assert without_supplies["sheet"]["combat"]["hp"]["value"] == 18
    assert with_supplies["sheet"]["combat"]["exhaustion"] == 3
    assert with_supplies["sheet"]["combat"]["hp"]["value"] == 37


def test_long_rest_clears_stable_and_unconscious_case_insensitively() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 1, "max": 10, "temp": 0}
    sheet["conditions"] = ["Stable", "UNCONSCIOUS", "prone"]

    result = apply_rest(sheet, rest_type="long_rest")

    assert result["sheet"]["combat"]["hp"]["value"] == 10
    assert result["sheet"]["conditions"] == ["prone"]


def test_long_rest_does_not_remove_condition_owned_by_persistent_effect() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 1, "max": 10, "temp": 0}
    sheet["conditions"] = ["stable", "unconscious"]
    sheet["effects"] = [
        {
            "id": "persistent-unconsciousness",
            "name": "Persistent Unconsciousness",
            "kind": "timed_conditions",
            "source": "source:module",
            "active": True,
            "concentration": False,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [{"path": "conditions", "mode": "add", "value": "unconscious"}],
            "description": "",
        }
    ]

    result = apply_rest(sheet, rest_type="long_rest")

    assert result["sheet"]["combat"]["hp"]["value"] == 10
    assert result["sheet"]["conditions"] == ["unconscious"]


@pytest.mark.parametrize("rest_type", ["short_rest", "long_rest"])
def test_ki_recovery_requires_thirty_minutes_of_meditation(rest_type: str) -> None:
    sheet = default_character_sheet()
    sheet["resources"] = {
        "ki": {
            "label": "Ki Points",
            "value": 0,
            "max": 3,
            "recovers_on": "short_rest",
            "recovery_requirements": {
                "activity_minutes": {"meditation": 30},
            },
            "source_key": "Monk",
        }
    }

    not_meditated = apply_rest(sheet, rest_type=rest_type)
    assert not_meditated["sheet"]["resources"]["ki"]["value"] == 0
    assert not_meditated["unmet_recovery_requirements"]["ki"] == {
        "activity_minutes": {"meditation": {"required_minutes": 30, "actual_minutes": 0}}
    }

    meditated = apply_rest(
        sheet,
        rest_type=rest_type,
        rest_activity_minutes={"meditation": 30},
    )
    assert meditated["sheet"]["resources"]["ki"]["value"] == 3
    assert meditated["recovered"]["ki"] == 3
    assert meditated["unmet_recovery_requirements"] == {}


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


def test_turn_undead_condition_expires_with_its_minute_effect() -> None:
    sheet = default_character_sheet()
    sheet["conditions"] = ["turned"]
    sheet["effects"] = [
        {
            "id": "turned",
            "name": "Turn Undead",
            "kind": "turn_undead",
            "active": True,
            "duration": {"period": "minute", "remaining": 1},
        }
    ]

    result = advance_effect_durations(sheet, period="minute")

    assert result["expired"] == ["turned"]
    assert "turned" not in result["sheet"]["conditions"]
    assert result["sheet"]["effects"][0]["ended_reason"] == "duration_expired"


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
    assert expired["sheet"]["effects"][0]["ended_reason"] == "duration_expired"


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
    assert result["state"]["world_effects"][0]["ended_reason"] == "duration_expired"


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


def test_2014_short_rest_healing_uses_the_effective_exhaustion_hp_maximum() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["combat"]["exhaustion"] = 4
    sheet["combat"]["hp"] = {"value": 5, "max": 20, "temp": 0}
    sheet["combat"]["hit_dice"] = {
        "d8": {
            "label": "d8",
            "value": 1,
            "max": 1,
            "recovers_on": "long_rest",
            "source_key": "fighter",
        }
    }

    rested = apply_rest(
        sheet,
        rest_type="short_rest",
        hit_dice_spends=[{"key": "d8", "count": 1}],
        rng=_SequenceRng(8),
    )

    assert rested["hit_die_healing"] == 8
    assert rested["hit_die_applied_healing"] == 5
    assert rested["sheet"]["combat"]["hp"] == {"value": 10, "max": 20, "temp": 0}
    assert rested["sheet"]["combat"]["hit_dice"]["d8"]["value"] == 0


def test_song_of_rest_applies_once_per_eligible_creature() -> None:
    target = default_character_sheet()
    target["edition"] = "2014"
    target["abilities"]["constitution"]["score"] = 14
    target["combat"]["hp"] = {"value": 2, "max": 20, "temp": 0}
    target["combat"]["hit_dice"] = {
        "d8": {
            "label": "d8",
            "value": 2,
            "max": 2,
            "recovers_on": "long_rest",
            "source_key": "Cleric",
        }
    }
    bard = default_character_sheet()
    bard["edition"] = "2014"
    bard["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
    bard["progression"] = {
        "level": 9,
        "classes": [{"name": "Bard", "level": 9, "hit_die": 8}],
    }
    bard["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2014.feature.bard-song-of-rest",
            "name": "Song of Rest",
            "source_key": "Bard",
            "rule_refs": ["bundled:srd2014/02_Classes/Bard.md"],
        }
    ]

    rested = apply_rest(
        target,
        rest_type="short_rest",
        hit_dice_spends=[{"key": "d8", "count": 2}],
        song_of_rest_source_sheet=bard,
        rules=resolution_context({"edition": "2014"}),
        rng=_SequenceRng(4, 5, 6),
    )

    assert rested["hit_die_healing"] == 13
    assert rested["hit_die_applied_healing"] == 13
    assert rested["song_of_rest"]["die"] == "1d8"
    assert rested["song_of_rest"]["roll"]["total"] == 6
    assert rested["song_of_rest"]["rolled_healing"] == 6
    assert rested["song_of_rest"]["applied_healing"] == 5
    assert rested["sheet"]["combat"]["hp"]["value"] == 20
    assert {receipt["mechanic_id"] for receipt in rested["rule_receipts"]} >= {
        "dnd5e.core.rest.hit_dice",
        "dnd5e.core.rest.song_of_rest",
    }

    no_hit_die = apply_rest(
        target,
        rest_type="short_rest",
        song_of_rest_source_sheet=bard,
        rng=_SequenceRng(),
    )
    assert no_hit_die["song_of_rest"] is None
    assert no_hit_die["sheet"]["combat"]["hp"]["value"] == 2
    full_target = deepcopy(target)
    full_target["combat"]["hp"]["value"] = full_target["combat"]["hp"]["max"]
    no_recovery = apply_rest(
        full_target,
        rest_type="short_rest",
        hit_dice_spends=[{"key": "d8", "count": 1}],
        song_of_rest_source_sheet=bard,
        rng=_SequenceRng(4),
    )
    assert no_recovery["hit_die_applied_healing"] == 0
    assert no_recovery["song_of_rest"] is None


def test_song_of_rest_rejects_unqualified_or_unconscious_sources() -> None:
    target = default_character_sheet()
    target["edition"] = "2014"
    target["combat"]["hp"] = {"value": 2, "max": 10, "temp": 0}
    target["combat"]["hit_dice"] = {
        "d8": {"label": "d8", "value": 1, "max": 1, "recovers_on": "long_rest"}
    }
    bard = default_character_sheet()
    bard["edition"] = "2014"
    bard["combat"]["hp"] = {"value": 8, "max": 8, "temp": 0}
    bard["progression"] = {
        "level": 2,
        "classes": [{"name": "Bard", "level": 2, "hit_die": 8}],
    }

    with pytest.raises(CombatEngineError, match="source-bound Bard"):
        apply_rest(
            target,
            rest_type="short_rest",
            hit_dice_spends=[{"key": "d8", "count": 1}],
            song_of_rest_source_sheet=bard,
            rng=_SequenceRng(),
        )

    bard["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2014.feature.bard-song-of-rest",
            "name": "Song of Rest",
            "source_key": "Bard",
            "rule_refs": ["bundled:srd2014/02_Classes/Bard.md"],
        }
    ]
    bard["conditions"] = ["unconscious"]
    with pytest.raises(CombatEngineError, match="conscious living bard"):
        apply_rest(
            target,
            rest_type="short_rest",
            hit_dice_spends=[{"key": "d8", "count": 1}],
            song_of_rest_source_sheet=bard,
            rng=_SequenceRng(),
        )
    with pytest.raises(CombatEngineError, match="only when finishing a short rest"):
        apply_rest(
            target,
            rest_type="long_rest",
            song_of_rest_source_sheet=bard,
        )


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


def test_arcane_recovery_is_a_once_per_day_short_rest_choice() -> None:
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
        game_day=1,
    )

    assert recovered["arcane_recovery"] == {
        "allowance": 1,
        "used_levels": 1,
        "recovered": {"1": 1},
        "edition": "2014",
        "reset_on": "game_day",
        "game_day": 1,
    }
    assert recovered["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] == 1
    feature_uses = recovered["sheet"]["content"]["features"][0]["uses"]
    assert feature_uses["value"] == 0
    assert feature_uses["max"] == 1
    assert feature_uses["recovers_on"] == "manual"
    assert recovered["sheet"]["content"]["features"][0]["choices"] == {
        "_arcane_recovery_last_used_game_day": 1
    }
    with pytest.raises(CombatEngineError, match="game day"):
        apply_rest(
            recovered["sheet"],
            rest_type="short_rest",
            arcane_recovery={"1": 1},
            game_day=1,
        )
    long_rested = apply_rest(recovered["sheet"], rest_type="long_rest")
    assert long_rested["sheet"]["content"]["features"][0]["uses"]["value"] == 0
    next_day_sheet = long_rested["sheet"]
    next_day_sheet["spellcasting"]["spell_slots"]["1"]["value"] = 0
    next_day = apply_rest(
        next_day_sheet,
        rest_type="short_rest",
        arcane_recovery={"1": 1},
        game_day=2,
    )
    assert next_day["arcane_recovery"]["game_day"] == 2

    with pytest.raises(CombatEngineError, match="exceeds half"):
        apply_rest(
            sheet,
            rest_type="short_rest",
            arcane_recovery={"1": 2},
            game_day=1,
        )
    with pytest.raises(CombatEngineError, match="only when finishing a short rest"):
        apply_rest(
            sheet,
            rest_type="long_rest",
            arcane_recovery={"1": 1},
            game_day=1,
        )


def test_2024_arcane_recovery_resets_only_on_a_long_rest() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2024"
    sheet["progression"] = {
        "level": 2,
        "classes": [{"name": "Wizard", "level": 2, "hit_die": 6}],
    }
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
            "id": "dnd5e.content.srd2024.feature.wizard-arcane-recovery",
            "name": "Arcane Recovery",
            "source_key": "Wizard",
            "uses": {
                "label": "Arcane Recovery",
                "value": 1,
                "max": 1,
                "recovers_on": "long_rest",
            },
        }
    ]

    rules = resolution_context(
        {
            "edition": "2024",
            "fingerprint": "arcane-recovery-pack",
            "lock": [],
            "mechanics": [],
        }
    )
    recovered = apply_rest(
        sheet,
        rest_type="short_rest",
        arcane_recovery={"1": 1},
        rules=rules,
    )
    assert recovered["arcane_recovery"]["reset_on"] == "long_rest"
    assert "game_day" not in recovered["arcane_recovery"]
    with pytest.raises(CombatEngineError, match="since the last long rest"):
        apply_rest(
            recovered["sheet"],
            rest_type="short_rest",
            arcane_recovery={"1": 1},
            rules=rules,
        )

    long_rested = apply_rest(recovered["sheet"], rest_type="long_rest", rules=rules)
    long_rested["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] = 0
    used_again = apply_rest(
        long_rested["sheet"],
        rest_type="short_rest",
        arcane_recovery={"1": 1},
        rules=rules,
    )
    assert used_again["sheet"]["content"]["features"][0]["uses"]["value"] == 0
    assert {receipt["mechanic_id"] for receipt in recovered["rule_receipts"]} >= {
        "dnd5e.core.rest.arcane_recovery"
    }


def test_natural_recovery_is_once_per_long_rest() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["progression"] = {
        "level": 4,
        "classes": [
            {
                "name": "Druid",
                "level": 4,
                "subclass": "Circle of the Land",
                "hit_die": 8,
            }
        ],
    }
    sheet["combat"]["hp"] = {"value": 20, "max": 24, "temp": 0}
    sheet["spellcasting"]["spell_slots"] = {
        "1": {
            "label": "Level 1 spell slots",
            "value": 1,
            "max": 4,
            "recovers_on": "long_rest",
            "source_key": "Druid",
            "slot_level": 1,
        },
        "2": {
            "label": "Level 2 spell slots",
            "value": 0,
            "max": 3,
            "recovers_on": "long_rest",
            "source_key": "Druid",
            "slot_level": 2,
        },
    }
    sheet["content"]["features"] = [
        {
            "id": ("dnd5e.content.srd2014.feature.circle-of-the-land-natural-recovery"),
            "name": "Natural Recovery",
            "source_key": "Circle of the Land",
            "rule_refs": ["bundled:srd2014/02_Classes/Druid.md"],
        }
    ]

    recovered = apply_rest(
        sheet,
        rest_type="short_rest",
        natural_recovery={"2": 1},
        rest_activity_minutes={"meditation": 60},
        rules=resolution_context({"edition": "2014"}),
    )

    assert recovered["natural_recovery"] == {
        "allowance": 2,
        "used_levels": 2,
        "recovered": {"2": 1},
        "druid_level": 4,
    }
    assert recovered["sheet"]["spellcasting"]["spell_slots"]["2"]["value"] == 1
    feature_uses = recovered["sheet"]["content"]["features"][0]["uses"]
    assert feature_uses == {
        "label": "Natural Recovery",
        "value": 0,
        "max": 1,
        "recovers_on": "long_rest",
        "source_key": "Circle of the Land",
        "slot_level": 0,
    }
    with pytest.raises(CombatEngineError, match="already been used"):
        apply_rest(
            recovered["sheet"],
            rest_type="short_rest",
            natural_recovery={"1": 1},
            rest_activity_minutes={"meditation": 60},
        )
    with pytest.raises(CombatEngineError, match="declared meditation"):
        apply_rest(
            sheet,
            rest_type="short_rest",
            natural_recovery={"1": 1},
        )

    long_rested = apply_rest(recovered["sheet"], rest_type="long_rest")
    assert long_rested["sheet"]["content"]["features"][0]["uses"]["value"] == 1
    long_rested["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] = 0
    second = apply_rest(
        long_rested["sheet"],
        rest_type="short_rest",
        natural_recovery={"1": 1},
        rest_activity_minutes={"meditation": 60},
    )
    assert second["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] == 1


def test_sorcerous_restoration_recovers_four_points() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["progression"] = {
        "level": 20,
        "classes": [{"name": "Sorcerer", "level": 20, "hit_die": 6}],
    }
    sheet["combat"]["hp"] = {"value": 100, "max": 100, "temp": 0}
    sheet["resources"]["sorcery_points"] = {
        "label": "Sorcery Points",
        "value": 3,
        "max": 20,
        "recovers_on": "long_rest",
        "source_key": "Sorcerer",
    }
    sheet["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2014.feature.sorcerer-sorcerous-restoration",
            "name": "Sorcerous Restoration",
            "source_key": "Sorcerer",
            "rule_refs": ["bundled:srd2014/02_Classes/Sorcerer.md"],
        }
    ]

    rested = apply_rest(
        sheet,
        rest_type="short_rest",
        rules=resolution_context({"edition": "2014"}),
    )

    assert rested["sorcerous_restoration"] == {
        "sorcerer_level": 20,
        "before": 3,
        "recovered": 4,
        "after": 7,
        "maximum": 20,
    }
    assert rested["sheet"]["resources"]["sorcery_points"]["value"] == 7
    assert "dnd5e.core.rest.sorcerous_restoration" in {
        receipt["mechanic_id"] for receipt in rested["rule_receipts"]
    }


def test_2024_sorcerous_restoration_uses_declared_points_once_per_long_rest() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2024"
    sheet["progression"] = {
        "level": 9,
        "classes": [{"name": "Sorcerer", "level": 9, "hit_die": 6}],
    }
    sheet["resources"]["sorcery_points"] = {
        "label": "Sorcery Points",
        "value": 2,
        "max": 9,
        "recovers_on": "long_rest",
        "source_key": "Sorcerer",
    }
    sheet["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2024.feature.sorcerer-sorcerous-restoration",
            "name": "Sorcerous Restoration",
            "source_key": "Sorcerer",
            "uses": {
                "label": "Sorcerous Restoration",
                "value": 1,
                "max": 1,
                "recovers_on": "long_rest",
                "source_key": "Sorcerer",
            },
            "rule_refs": ["bundled:srd2024/DND5eSRD_064-076.md#level-5-sorcerous-restoration"],
            "mechanic_refs": ["dnd5e.core.rest.sorcerous_restoration"],
        }
    ]

    rested = apply_rest(
        sheet,
        rest_type="short_rest",
        sorcerous_restoration_points=4,
        rules=resolution_context({"edition": "2024"}),
    )

    assert rested["sorcerous_restoration"] == {
        "sorcerer_level": 9,
        "before": 2,
        "recovered": 4,
        "after": 6,
        "maximum": 9,
        "edition": "2024",
        "feature_uses_remaining": 0,
    }
    assert rested["sheet"]["resources"]["sorcery_points"]["value"] == 6
    assert rested["sheet"]["content"]["features"][0]["uses"]["value"] == 0
    with pytest.raises(CombatEngineError, match="already been used"):
        apply_rest(
            rested["sheet"],
            rest_type="short_rest",
            sorcerous_restoration_points=1,
        )
    with pytest.raises(CombatEngineError, match="half the Sorcerer level"):
        apply_rest(
            sheet,
            rest_type="short_rest",
            sorcerous_restoration_points=5,
        )

    refreshed = apply_rest(rested["sheet"], rest_type="long_rest")
    assert refreshed["sheet"]["content"]["features"][0]["uses"]["value"] == 1


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


def test_source_authored_stable_unconscious_state_is_atomic_and_narrow() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 0, "max": 12, "temp": 0}
    sheet["combat"]["death_saves"] = {"successes": 1, "failures": 2}

    result = initialize_source_state(sheet, state="stable_unconscious")

    assert result["status"] == "initialized"
    assert result["source_state"] == "stable_unconscious"
    assert result["sheet"]["combat"]["hp"]["value"] == 0
    assert result["sheet"]["combat"]["death_saves"] == {"successes": 0, "failures": 0}
    assert result["sheet"]["conditions"] == ["prone", "stable", "unconscious"]
    assert sheet["conditions"] == []


def test_source_state_rejects_broad_condition_edits_and_incompatible_hp() -> None:
    sheet = default_character_sheet()
    with pytest.raises(CombatEngineError, match="must be stable_unconscious"):
        initialize_source_state(sheet, state="restrained")
    with pytest.raises(CombatEngineError, match="requires 0 hit points"):
        initialize_source_state(sheet, state="stable_unconscious")
    sheet["combat"]["hp"]["value"] = 0
    sheet["conditions"] = ["dead"]
    with pytest.raises(CombatEngineError, match="dead creature"):
        initialize_source_state(sheet, state="stable_unconscious")


def test_conscious_recovered_creature_can_stand_outside_combat() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 1, "max": 12, "temp": 0}
    sheet["conditions"] = ["prone"]

    result = stand_outside_combat(sheet)

    assert result["status"] == "stood"
    assert result["sheet"]["conditions"] == []
    assert sheet["conditions"] == ["prone"]


def test_conscious_creature_can_be_knocked_prone_outside_combat() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 7, "max": 12, "temp": 0}

    result = knock_prone_outside_combat(sheet)
    replay = knock_prone_outside_combat(result["sheet"])

    assert result["status"] == "knocked_prone"
    assert result["added_condition"] == "prone"
    assert result["sheet"]["conditions"] == ["prone"]
    assert replay["status"] == "already_prone"
    assert sheet["conditions"] == []


def test_outside_combat_prone_changes_honor_immunity_and_effect_ownership() -> None:
    immune = default_character_sheet()
    immune["combat"]["hp"] = {"value": 7, "max": 12, "temp": 0}
    immune["traits"]["condition_immunities"] = ["prone"]

    resisted = knock_prone_outside_combat(immune)

    assert resisted["status"] == "immune"
    assert resisted["sheet"]["conditions"] == []

    sourced = default_character_sheet()
    sourced["combat"]["hp"] = {"value": 7, "max": 12, "temp": 0}
    sourced["conditions"] = ["prone"]
    sourced["effects"] = [
        {
            "id": "held-prone",
            "name": "Held Prone",
            "kind": "timed_conditions",
            "active": True,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [{"path": "conditions", "mode": "add", "value": "prone"}],
        }
    ]

    with pytest.raises(CombatEngineError, match="still owned"):
        stand_outside_combat(sourced)


def test_outside_combat_knock_prone_rejects_incapacitated_creature() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 0, "max": 12, "temp": 0}
    sheet["conditions"] = ["unconscious"]

    with pytest.raises(CombatEngineError, match="conscious living creature"):
        knock_prone_outside_combat(sheet)


def test_outside_combat_stand_rejects_unconscious_or_nonprone_creature() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 1, "max": 12, "temp": 0}
    sheet["conditions"] = ["prone", "unconscious"]
    with pytest.raises(CombatEngineError, match="conscious living creature"):
        stand_outside_combat(sheet)
    sheet["conditions"] = []
    with pytest.raises(CombatEngineError, match="Prone condition"):
        stand_outside_combat(sheet)


def test_short_rest_removes_all_and_only_chase_exhaustion() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["combat"]["exhaustion"] = 3
    sheet["effects"] = [
        {
            "id": "chase-fatigue",
            "name": "Chase Exhaustion",
            "kind": "chase_exhaustion",
            "source": "DMG 2014 chapter 8 chase dashing",
            "active": True,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [
                {
                    "path": "combat.exhaustion",
                    "mode": "chase_levels",
                    "value": 2,
                }
            ],
        }
    ]

    result = apply_rest(sheet, rest_type="short_rest")

    assert result["sheet"]["combat"]["exhaustion"] == 1
    assert result["chase_exhaustion_recovery"] == {
        "before": 3,
        "recovered": 2,
        "after": 1,
    }
    assert result["sheet"]["effects"][0]["active"] is False
    assert result["sheet"]["effects"][0]["ended_reason"] == "short_or_long_rest"


def test_2024_human_resourceful_grants_heroic_inspiration_on_long_rest() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2024"
    sheet["content"]["features"].append(
        {
            "id": "dnd5e.content.srd2024.species-feature.human-resourceful",
            "name": "Resourceful",
            "source_key": "Human",
            "choices": {"grant_heroic_inspiration_on": "long_rest"},
            "rule_refs": ["bundled:srd2024/DND5eSRD_077-086.md#human-resourceful"],
        }
    )
    rules = resolution_context({"edition": "2024", "fingerprint": "", "lock": []})

    result = apply_rest(sheet, rest_type="long_rest", rules=rules)

    assert result["sheet"]["combat"]["inspiration"] is True
    assert result["heroic_inspiration"] == {"outcome": "granted"}
    assert "dnd5e.core.heroic_inspiration" in {
        receipt["mechanic_id"] for receipt in result["rule_receipts"]
    }
