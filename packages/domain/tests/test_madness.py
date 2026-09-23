from __future__ import annotations

import pytest

from sagasmith_dnd import madness
from sagasmith_dnd.character_schema import (
    add_effect,
    default_character_sheet,
    validate_character_sheet,
)


@pytest.mark.parametrize("category", ["short_term", "long_term", "indefinite"])
def test_every_d100_value_resolves_to_one_source_table_entry(category: str) -> None:
    for roll in range(1, 101):
        kwargs = {} if category == "indefinite" else {"duration_die": 1}
        if category == "long_term" and 56 <= roll <= 65:
            kwargs["conditional_d100"] = 25
        result = madness.resolve_madness(category, roll, **kwargs)
        assert result["roll"] == roll
        assert result["source_ref"] == madness.SOURCE_REF
        assert result["effect_key"]
        assert result["mechanics"]


def test_madness_durations_and_conditional_long_term_result() -> None:
    short = madness.resolve_madness("short_term", 20, duration_die=10)
    long = madness.resolve_madness("long_term", 20, duration_die=7)
    blind = madness.resolve_madness("long_term", 56, duration_die=1, conditional_d100=25)
    deaf = madness.resolve_madness("long_term", 65, duration_die=1, conditional_d100=26)
    assert short["duration"] == {"unit": "minute", "amount": 10}
    assert long["duration"] == {"unit": "hour", "amount": 70}
    assert short["runtime_effect"]["duration"] == {"period": "minute", "remaining": 10}
    assert long["runtime_effect"]["duration"] == {"period": "hour", "remaining": 70}
    assert blind["mechanics"]["condition"] == "blinded"
    assert deaf["mechanics"]["condition"] == "deafened"


@pytest.mark.parametrize(
    ("category", "d100", "duration_die", "conditional_d100"),
    [
        ("short_term", 0, 1, None),
        ("short_term", 101, 1, None),
        ("short_term", 1, 0, None),
        ("long_term", 56, 1, None),
        ("indefinite", 1, 1, None),
        ("short_term", 1, 1, 1),
    ],
)
def test_madness_rejects_invalid_or_unowned_rolls(
    category: str,
    d100: int,
    duration_die: int,
    conditional_d100: int | None,
) -> None:
    kwargs = {"duration_die": duration_die}
    if conditional_d100 is not None:
        kwargs["conditional_d100"] = conditional_d100
    with pytest.raises(madness.MadnessError):
        madness.resolve_madness(category, d100, **kwargs)


def test_madness_cure_tiers_follow_bundled_categories() -> None:
    assert madness.cure_tier("short_term", "lesser_restoration") == "cure"
    assert madness.cure_tier("long_term", "lesser_restoration") == "cure"
    assert madness.cure_tier("indefinite", "lesser_restoration") == "insufficient"
    assert madness.cure_tier("indefinite", "greater_restoration") == "cure"
    assert madness.cure_tier("long_term", "calm_emotions") == "suppress"
    assert madness.cure_tier("indefinite", "remove_curse") == "requires_source_authorization"
    assert madness.cure_tier("indefinite", "remove_curse", source_authorizes=True) == "cure"


def test_lucky_charm_choice_is_source_owned_and_penalty_has_exact_distance_boundary() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    effect = madness.resolve_madness("long_term", 46, duration_die=10)["runtime_effect"]
    effect["id"] = "lucky-charm"
    sheet, _ = add_effect(sheet, effect)

    with pytest.raises(madness.MadnessError, match="actor choice"):
        madness.range_limited_disadvantage_effect_ids(
            sheet, distance_ft=31, roll_kind="attack_rolls"
        )
    chosen = madness.choose_lucky_charm(
        sheet, effect_id="lucky-charm", charm_actor_id="ally-1"
    )
    assert madness.range_limited_disadvantage_effect_ids(
        chosen, distance_ft=30, roll_kind="attack_rolls"
    ) == []
    assert madness.range_limited_disadvantage_effect_ids(
        chosen, distance_ft=31, roll_kind="attack_rolls"
    ) == ["lucky-charm"]
    with pytest.raises(madness.MadnessError, match="already bound"):
        madness.choose_lucky_charm(
            chosen, effect_id="lucky-charm", charm_actor_id="ally-2"
        )


@pytest.mark.parametrize(
    ("category", "roll", "choice"),
    [
        ("long_term", 1, {"kind": "activity", "activity_id": "counting_floor_tiles"}),
        ("long_term", 31, {"kind": "target_actor", "actor_id": "actor:guard"}),
        ("long_term", 41, {"kind": "potion", "potion_name": "healing"}),
        ("indefinite", 26, {"kind": "person_actor", "actor_id": "actor:scribe"}),
        ("indefinite", 36, {"kind": "goal", "goal_id": "find_the_crown"}),
    ],
)
def test_source_defined_narrative_madness_choices_are_typed_and_persisted(
    category: str, roll: int, choice: dict[str, str]
) -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    kwargs = {} if category == "indefinite" else {"duration_die": 1}
    effect = madness.resolve_madness(category, roll, **kwargs)["runtime_effect"]
    effect.update({"id": f"madness-{roll}", "name": "Source-defined madness"})
    sheet, _ = add_effect(sheet, effect)

    chosen = madness.choose_madness_outcome(sheet, effect_id=effect["id"], choice=choice)
    stored = next(item for item in chosen["effects"] if item["id"] == effect["id"])
    assert stored["metadata"]["madness"]["choice"] == choice
    assert madness.choose_madness_outcome(chosen, effect_id=effect["id"], choice=choice) == chosen


def test_source_defined_narrative_madness_choice_rejects_wrong_kind_or_unneeded_choice() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    effect = madness.resolve_madness("long_term", 1, duration_die=1)["runtime_effect"]
    effect.update({"id": "repeat-activity", "name": "Repeated activity"})
    sheet, _ = add_effect(sheet, effect)
    with pytest.raises(madness.MadnessError, match="kind or fields"):
        madness.choose_madness_outcome(
            sheet,
            effect_id="repeat-activity",
            choice={"kind": "goal", "goal_id": "anything"},
        )
    effect = madness.resolve_madness("long_term", 20, duration_die=1)["runtime_effect"]
    effect.update({"id": "hallucination", "name": "Hallucination"})
    sheet, _ = add_effect(sheet, effect)
    with pytest.raises(madness.MadnessError, match="does not require a typed choice"):
        madness.choose_madness_outcome(
            sheet,
            effect_id="hallucination",
            choice={"kind": "activity", "activity_id": "unprompted"},
        )


def test_typed_madness_choice_rejects_non_string_values() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    effect = madness.resolve_madness("long_term", 1, duration_die=1)["runtime_effect"]
    effect.update({"id": "repeat-activity", "name": "Repeated activity"})
    sheet, _ = add_effect(sheet, effect)
    with pytest.raises(madness.MadnessError, match="must be a string"):
        madness.choose_madness_outcome(
            sheet,
            effect_id="repeat-activity",
            choice={"kind": "activity", "activity_id": True},
        )


def test_indefinite_madness_is_persisted_without_a_timed_expiry() -> None:
    result = madness.resolve_madness("indefinite", 1)
    assert result["duration"] == {"kind": "until_cured"}
    assert result["runtime_effect"]["duration"] == {"period": "manual", "remaining": 0}
    assert result["mechanics"]["narrative_only"] is True


def test_table_effects_validate_as_source_owned_character_effects() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    effect = madness.resolve_madness("short_term", 1, duration_die=2)["runtime_effect"]
    effect["name"] = "Short-term madness: retreat paralysis"
    effect["metadata"]["madness"]["source_ref"] = madness.SOURCE_REF
    sheet["effects"].append(effect)
    validated = validate_character_sheet(sheet)
    assert validated["effects"][-1]["changes"] == [
        {"path": "conditions", "mode": "add", "value": "paralyzed"}
    ]


def test_damage_triggered_confusion_requires_actual_damage_and_exact_source_effect() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    madness_effect = madness.resolve_madness("long_term", 86, duration_die=1)["runtime_effect"]
    madness_effect["id"] = "damage-confusion"
    sheet, _ = add_effect(sheet, madness_effect)

    assert madness.damage_triggered_confusion_effect_ids(sheet) == ["damage-confusion"]
    save = {
        "kind": "save",
        "ability": "wisdom",
        "dc": 15,
        "success": False,
        "total": 8,
        "rolls": [{"die": "1d20", "total": 4}],
    }
    settled = madness.apply_damage_triggered_confusion(
        sheet,
        source_effect_id="damage-confusion",
        damage_taken=1,
        save=save,
        confusion_effect_id="confusion-from-event-1",
    )
    confusion = settled["sheet"]["effects"][-1]
    assert confusion["duration"] == {"period": "round", "remaining": 10}
    assert confusion["metadata"]["madness_confusion"]["trigger"]["save"] == save
    assert settled["event"]["confusion_applied"] is True
    assert madness.apply_damage_triggered_confusion(
        settled["sheet"],
        source_effect_id="damage-confusion",
        damage_taken=1,
        save=save,
        confusion_effect_id="confusion-from-event-1",
    ) == settled

    success = madness.apply_damage_triggered_confusion(
        sheet,
        source_effect_id="damage-confusion",
        damage_taken=1,
        save={**save, "success": True},
        confusion_effect_id="confusion-not-applied",
    )
    assert success["event"]["confusion_applied"] is False
    assert len(success["sheet"]["effects"]) == len(sheet["effects"])

    with pytest.raises(madness.MadnessError, match="positive damage"):
        madness.apply_damage_triggered_confusion(
            sheet,
            source_effect_id="damage-confusion",
            damage_taken=0,
            save=save,
            confusion_effect_id="no-damage",
        )
    with pytest.raises(madness.MadnessError, match="exact active source-owned"):
        madness.apply_damage_triggered_confusion(
            sheet,
            source_effect_id="forged-effect",
            damage_taken=1,
            save=save,
            confusion_effect_id="forged-confusion",
        )


@pytest.mark.parametrize(
    ("roll", "outcome"),
    [
        (1, "move_random_direction"),
        (2, "no_action_or_movement"),
        (6, "no_action_or_movement"),
        (7, "attack_random_creature_in_reach"),
        (8, "attack_random_creature_in_reach"),
        (9, "act_normally"),
        (10, "act_normally"),
    ],
)
def test_confusion_turn_table_maps_all_boundaries(roll: int, outcome: str) -> None:
    assert madness.resolve_confusion_turn(roll)["outcome"] == outcome


@pytest.mark.parametrize("roll", [0, 11, True])
def test_confusion_turn_table_rejects_unowned_rolls(roll: int) -> None:
    with pytest.raises(madness.MadnessError, match="d10"):
        madness.resolve_confusion_turn(roll)


def test_calm_emotions_suppression_keeps_duration_and_resumes_at_campaign_deadline() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    effect = madness.resolve_madness("long_term", 11, duration_die=8)["runtime_effect"]
    effect["id"] = "long-hallucinations"
    sheet, _ = add_effect(sheet, effect)
    duration_before = dict(sheet["effects"][0]["duration"])

    suppressed = madness.suppress_madness_effect(
        sheet,
        effect_id="long-hallucinations",
        started_elapsed_ticks=120,
    )
    suppressed_effect = suppressed["effects"][0]
    assert suppressed_effect["active"] is True
    assert suppressed_effect["changes"] == []
    assert suppressed_effect["duration"] == duration_before
    assert suppressed_effect["metadata"]["madness"]["suppression"][
        "expires_elapsed_ticks"
    ] == 130
    assert madness.advance_madness_suppression(suppressed, elapsed_ticks=129)[
        "sheet"
    ]["effects"][0]["changes"] == []

    resumed = madness.advance_madness_suppression(suppressed, elapsed_ticks=130)
    assert resumed["resumed_effect_ids"] == ["long-hallucinations"]
    assert resumed["sheet"]["effects"][0]["changes"] == effect["changes"]


def test_suppression_preserves_overlaps_and_does_not_revive_a_cured_effect() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    madness_effect = madness.resolve_madness("short_term", 1, duration_die=1)["runtime_effect"]
    madness_effect["id"] = "madness-paralysis"
    sheet, _ = add_effect(sheet, madness_effect)
    sheet, _ = add_effect(
        sheet,
        {
            "id": "other-paralysis",
            "name": "Independent paralysis",
            "kind": "timed_conditions",
            "source": "test:independent-paralysis",
            "changes": [{"path": "conditions", "mode": "add", "value": "paralyzed"}],
        },
    )

    suppressed = madness.suppress_madness_effect(
        sheet,
        effect_id="madness-paralysis",
        started_elapsed_ticks=0,
    )
    assert "paralyzed" in suppressed["conditions"]
    cured = suppressed
    cured["effects"][0]["active"] = False
    resumed = madness.advance_madness_suppression(cured, elapsed_ticks=10)
    assert resumed["resumed_effect_ids"] == []
    assert resumed["sheet"]["effects"][0]["changes"] == []
    assert "paralyzed" in resumed["sheet"]["conditions"]
