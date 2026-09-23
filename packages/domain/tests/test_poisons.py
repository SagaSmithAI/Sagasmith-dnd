from copy import deepcopy
from pathlib import Path

import pytest

from sagasmith_dnd.character_schema import (
    add_effect,
    default_character_sheet,
    remove_effect,
    validate_character_sheet,
    validate_party_state,
)
from sagasmith_dnd.combat_engine import (
    CombatEngineError,
    apply_damage_to_sheet,
    apply_healing_to_sheet,
)
from sagasmith_dnd.core_content import build_srd2014_content
from sagasmith_dnd.lifecycle import advance_effect_durations
from sagasmith_dnd.poisons import (
    BASIC_POISON_MECHANIC_ID,
    BASIC_POISON_SOURCE_REF,
    DAY_TICKS,
    HOUR_TICKS,
    MINUTE_TICKS,
    POISON_ITEM_SOURCE_PREFIX,
    POISON_SOURCE_REF,
    POISONS_2014,
    build_midnight_tears_effect,
    build_poison_effect,
    contact_exposes,
    injury_exposes,
    next_anchored_midnight_elapsed_ticks,
    poison_profile,
    record_poison_healing_lock,
    settle_poison_repeat_save,
    validate_ingested_delivery,
    validate_poison_coatings,
    wake_poison_effects,
)


def test_srd_2014_sample_poison_catalog_matches_delivery_and_save_sources() -> None:
    assert len(POISONS_2014) == 14
    assert {item.delivery for item in POISONS_2014.values()} == {
        "contact",
        "ingested",
        "inhaled",
        "injury",
    }
    assert all(item.save_dc in range(10, 20) for item in POISONS_2014.values())
    assert POISON_SOURCE_REF.endswith("08_Gamemastering/Poisons.md")
    assert poison_profile("Assassin's Blood").initial_damage == "1d12"
    assert poison_profile("purple_worm_poison").half_on_success is True
    assert poison_profile("crawler_mucus").harvest_source == "crawler:dead_or_incapacitated"
    assert poison_profile("serpent_venom").harvest_source == (
        "giant_poisonous_snake:dead_or_incapacitated"
    )


@pytest.mark.parametrize(
    ("poison_id", "duration_ticks", "condition", "secondary"),
    [
        ("assassins_blood", DAY_TICKS, "poisoned", ""),
        ("crawler_mucus", MINUTE_TICKS, "poisoned", "paralyzed"),
        ("drow_poison", HOUR_TICKS, "poisoned", "unconscious"),
        ("essence_of_ether", 8 * HOUR_TICKS, "poisoned", "unconscious"),
        ("malice", HOUR_TICKS, "poisoned", "blinded"),
        ("oil_of_taggit", DAY_TICKS, "poisoned", "unconscious"),
        ("truth_serum", HOUR_TICKS, "poisoned", ""),
    ],
)
def test_sample_poison_conditions_keep_exact_source_duration(
    poison_id: str, duration_ticks: int, condition: str, secondary: str
) -> None:
    profile = poison_profile(poison_id)
    assert profile.duration_ticks == duration_ticks
    assert profile.condition == condition
    assert profile.secondary_condition == secondary


def test_truth_serum_keeps_its_source_owned_truth_constraint_explicit() -> None:
    effect = build_poison_effect(
        "truth_serum",
        effect_id="truth-serum-instance",
        elapsed_ticks=30,
        save_succeeded=False,
    )
    assert effect is not None
    state = effect["metadata"]["poison_state"]
    assert state["truth_constraint"] is True
    assert state["truth_constraint_kind"] == "cannot_knowingly_speak_a_lie"
    assert state["source_ref"] == POISON_SOURCE_REF


def test_contact_delivery_requires_exposed_skin_on_the_same_smeared_object() -> None:
    assert contact_exposes(same_smeared_object=True, exposed_skin_touch=True)
    assert not contact_exposes(same_smeared_object=True, exposed_skin_touch=False)
    assert not contact_exposes(same_smeared_object=False, exposed_skin_touch=True)
    with pytest.raises(CombatEngineError, match="authoritative"):
        contact_exposes(same_smeared_object="yes", exposed_skin_touch=True)


def test_injury_delivery_requires_a_real_wound_from_the_coated_object() -> None:
    facts = {
        "coated_object_id": "weapon-1",
        "damaging_object_id": "weapon-1",
        "damage_type": "piercing",
        "damage_applied": 1,
    }
    assert injury_exposes(**facts)
    assert not injury_exposes(**{**facts, "damaging_object_id": "weapon-2"})
    assert not injury_exposes(**{**facts, "damage_type": "bludgeoning"})
    assert not injury_exposes(**{**facts, "damage_applied": 0})
    with pytest.raises(CombatEngineError, match="applied damage"):
        injury_exposes(**{**facts, "damage_applied": -1})


def test_ingested_delivery_requires_whole_dose_or_one_source_permitted_partial_ruling() -> None:
    assert validate_ingested_delivery(swallowed_entire_dose=True) == "whole_dose"
    assert (
        validate_ingested_delivery(swallowed_entire_dose=False, partial_ruling="advantage_on_save")
        == "advantage_on_save"
    )
    assert (
        validate_ingested_delivery(
            swallowed_entire_dose=False, partial_ruling="half_damage_on_failure"
        )
        == "half_damage_on_failure"
    )
    with pytest.raises(CombatEngineError, match="authorized"):
        validate_ingested_delivery(swallowed_entire_dose=False)
    with pytest.raises(CombatEngineError, match="cannot also"):
        validate_ingested_delivery(swallowed_entire_dose=True, partial_ruling="advantage_on_save")


def test_official_poison_inventory_dose_keeps_its_exact_source_identity() -> None:
    workspace = Path(__file__).resolve().parents[3]
    _, artifacts = build_srd2014_content(workspace / "skills")
    card = next(item["card"] for item in artifacts if item["id"].endswith("assassins_blood"))
    sheet = default_character_sheet()
    item = {"id": "dose-1", **card["inventory_template"]}
    sheet["inventory"]["items"].append(item)

    normalized = validate_character_sheet(sheet)["inventory"]["items"][0]
    assert normalized["mechanics"]["poison_dose"] == {
        "poison_id": "assassins_blood",
        "delivery": "ingested",
        "edition": "2014",
        "source_ref": POISON_SOURCE_REF,
    }
    assert normalized["source_key"] == POISON_ITEM_SOURCE_PREFIX + "assassins_blood"

    spoofed = {**item, "source_key": "custom.item.assassins-blood"}
    spoofed_sheet = deepcopy(sheet)
    spoofed_sheet["inventory"]["items"] = [spoofed]
    with pytest.raises(ValueError, match="exact source-bound consumable"):
        validate_character_sheet(spoofed_sheet)


def test_basic_poison_is_a_gear_sourced_hit_coating_with_exact_minute_potency() -> None:
    workspace = Path(__file__).resolve().parents[3]
    _, artifacts = build_srd2014_content(workspace / "skills")
    artifact = next(
        item for item in artifacts if item["id"] == POISON_ITEM_SOURCE_PREFIX + "basic_poison"
    )
    item = {"id": "basic-dose", **artifact["card"]["inventory_template"]}
    normalized = validate_character_sheet(
        {**default_character_sheet(), "edition": "2014", "inventory": {"items": [item]}}
    )["inventory"]["items"][0]

    assert normalized["mechanics"]["poison_dose"] == {
        "poison_id": "basic_poison",
        "delivery": "injury",
        "edition": "2014",
        "source_ref": BASIC_POISON_SOURCE_REF,
    }
    assert artifact["card"]["mechanic_refs"] == [BASIC_POISON_MECHANIC_ID]
    profile = poison_profile("basic_poison")
    assert profile.save_dc == 10
    assert profile.initial_damage == "1d4"
    assert profile.half_on_success is False
    assert profile.condition == ""
    assert profile.duration_ticks == MINUTE_TICKS

    coating = {
        "id": "basic-coating",
        "poison_id": "basic_poison",
        "object_actor_id": "actor-1",
        "object_item_id": "arrow-1",
        "applied_by_actor_id": "actor-1",
        "dose_item_id": "basic-dose",
        "source_key": POISON_ITEM_SOURCE_PREFIX + "basic_poison",
        "source_ref": BASIC_POISON_SOURCE_REF,
        "created_at_elapsed_ticks": 0,
        "active": True,
    }
    assert validate_poison_coatings({"coatings": [coating]})["coatings"] == [coating]


def test_poison_effects_keep_exact_condition_wake_and_duration_provenance() -> None:
    drow_margin_four = build_poison_effect(
        "drow_poison",
        effect_id="drow-4",
        elapsed_ticks=10,
        save_succeeded=False,
        save_failed_by=4,
    )
    drow_margin_five = build_poison_effect(
        "drow_poison",
        effect_id="drow-5",
        elapsed_ticks=10,
        save_succeeded=False,
        save_failed_by=5,
    )
    assert drow_margin_four["changes"][0]["value"] == ["poisoned"]
    assert drow_margin_five["changes"][0]["value"] == ["poisoned", "unconscious"]
    assert drow_margin_five["metadata"]["poison_state"]["wake_policy"] == ["damage", "action_shake"]
    assert drow_margin_five["duration"] == {"period": "hour", "remaining": 1}

    with pytest.raises(CombatEngineError, match="4d6-hour"):
        build_poison_effect("torpor", effect_id="torpor", elapsed_ticks=0, save_succeeded=False)
    torpor = build_poison_effect(
        "torpor",
        effect_id="torpor",
        elapsed_ticks=0,
        save_succeeded=False,
        duration_roll_hours=12,
    )
    assert torpor["duration"] == {"period": "hour", "remaining": 12}


def test_burnt_othur_and_pale_tincture_count_successes_and_anchor_due_time() -> None:
    burnt = build_poison_effect(
        "burnt_othur_fumes",
        effect_id="burnt",
        elapsed_ticks=0,
        save_succeeded=False,
    )
    assert burnt["duration"] == {"period": "manual", "remaining": 0}
    first = settle_poison_repeat_save(burnt, save_succeeded=True, elapsed_ticks=0)
    assert first["effect"]["active"] is True and first["successes"] == 1
    second = settle_poison_repeat_save(first["effect"], save_succeeded=True, elapsed_ticks=0)
    third = settle_poison_repeat_save(second["effect"], save_succeeded=True, elapsed_ticks=0)
    assert third["ended"] is True and third["effect"]["active"] is False

    pale = build_poison_effect(
        "pale_tincture",
        effect_id="pale",
        elapsed_ticks=100,
        save_succeeded=False,
    )
    assert pale["metadata"]["poison_state"]["next_due_elapsed_ticks"] == 100 + DAY_TICKS
    with pytest.raises(CombatEngineError, match="not due"):
        settle_poison_repeat_save(pale, save_succeeded=False, elapsed_ticks=100 + DAY_TICKS - 1)
    due = settle_poison_repeat_save(pale, save_succeeded=False, elapsed_ticks=100 + DAY_TICKS)
    assert due["damage_expression"] == "1d6"
    assert due["effect"]["metadata"]["poison_state"]["next_due_elapsed_ticks"] == (
        100 + 2 * DAY_TICKS
    )


def test_pale_tincture_ends_after_exactly_seven_daily_successes() -> None:
    effect = build_poison_effect(
        "pale_tincture",
        effect_id="pale-seven-successes",
        elapsed_ticks=0,
        save_succeeded=False,
    )

    for success_count in range(1, 8):
        due = effect["metadata"]["poison_state"]["next_due_elapsed_ticks"]
        result = settle_poison_repeat_save(
            effect,
            save_succeeded=True,
            elapsed_ticks=due,
        )
        effect = result["effect"]
        assert result["successes"] == success_count
        assert result["ended"] is (success_count == 7)
        assert effect["active"] is (success_count < 7)


def test_overlapping_poison_instances_keep_shared_condition_until_both_end() -> None:
    first = build_poison_effect(
        "malice",
        effect_id="malice-one",
        elapsed_ticks=0,
        save_succeeded=False,
    )
    second = build_poison_effect(
        "oil_of_taggit",
        effect_id="taggit-two",
        elapsed_ticks=0,
        save_succeeded=False,
    )
    sheet, _ = add_effect(default_character_sheet(), first)
    sheet, _ = add_effect(sheet, second)

    after_first_ends = remove_effect(sheet, "malice-one")
    assert "poisoned" in after_first_ends["conditions"]
    assert "unconscious" in after_first_ends["conditions"]
    assert "blinded" not in after_first_ends["conditions"]
    assert next(item for item in after_first_ends["effects"] if item["id"] == "taggit-two")[
        "active"
    ] is True

    after_second_ends = remove_effect(after_first_ends, "taggit-two")
    assert after_second_ends["conditions"] == []


def test_poison_coatings_are_persisted_and_campaign_owned() -> None:
    coating = {
        "id": "coat-1",
        "poison_id": "drow_poison",
        "object_actor_id": "actor-1",
        "object_item_id": "weapon-1",
        "applied_by_actor_id": "actor-1",
        "dose_item_id": "dose-1",
        "source_key": POISON_ITEM_SOURCE_PREFIX + "drow_poison",
        "source_ref": POISON_SOURCE_REF,
        "created_at_elapsed_ticks": 120,
        "active": True,
    }
    normalized = validate_poison_coatings({"coatings": [coating]})
    assert normalized["coatings"] == [coating]
    assert validate_party_state({})["poison_coatings"] == {
        "schema_version": 1,
        "coatings": [],
    }
    with pytest.raises(CombatEngineError, match="exact official dose"):
        validate_poison_coatings({"coatings": [{**coating, "source_key": "custom"}]})
    with pytest.raises(ValueError, match="system-owned state fields"):
        from sagasmith_dnd.campaign_state import merge_reviewed_campaign_state

        merge_reviewed_campaign_state({}, {"poison_coatings": {"coatings": []}})


def test_pale_tincture_only_blocks_healing_of_its_own_missing_hp() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"].update(value=2, max=10)
    effect = build_poison_effect(
        "pale_tincture",
        effect_id="pale-1",
        elapsed_ticks=0,
        save_succeeded=False,
    )
    effect = record_poison_healing_lock(effect, hp_damage=5)
    sheet, _ = add_effect(sheet, effect)

    healed = apply_healing_to_sheet(sheet, amount=8)
    assert healed["after_hp"] == 5
    assert healed["amount"] == 3
    assert healed["poison_locked_hp"] == 5

    overlapping = build_poison_effect(
        "pale_tincture",
        effect_id="pale-2",
        elapsed_ticks=0,
        save_succeeded=False,
    )
    overlapping = record_poison_healing_lock(overlapping, hp_damage=4)
    sheet, _ = add_effect(sheet, overlapping)
    blocked = apply_healing_to_sheet(sheet, amount=8)
    assert blocked["after_hp"] == 2
    assert blocked["amount"] == 0
    assert blocked["poison_locked_hp"] == 9


@pytest.mark.parametrize(
    ("elapsed_ticks", "expected_due"),
    [(14399, 14400), (14400, 28800), (14401, 28800)],
)
def test_midnight_tears_uses_the_next_strict_anchored_midnight(
    elapsed_ticks: int, expected_due: int
) -> None:
    effect = build_midnight_tears_effect(
        effect_id="midnight-1",
        target_actor_id="actor-1",
        elapsed_ticks=elapsed_ticks,
        world_time={"calendar_offset_ticks": 0},
    )
    assert effect["metadata"]["poison_state"]["midnight_due_elapsed_ticks"] == expected_due


def test_midnight_tears_rejects_unanchored_world_time_before_creating_state() -> None:
    with pytest.raises(CombatEngineError, match="anchored campaign calendar"):
        next_anchored_midnight_elapsed_ticks(25, {})


def test_damage_wakes_only_poison_owned_unconsciousness() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["combat"]["hp"].update(value=10, max=10)
    effect = build_poison_effect(
        "essence_of_ether",
        effect_id="ether-1",
        source_actor_id="source",
        elapsed_ticks=0,
        save_succeeded=False,
    )
    sheet, _ = add_effect(sheet, effect)

    damaged = apply_damage_to_sheet(
        sheet,
        amount=1,
        damage_type="slashing",
        source="attack:damage-wake",
        ruleset="2014",
        death_saves=True,
    )
    updated = damaged["sheet"]
    active = next(item for item in updated["effects"] if item["id"] == "ether-1")
    assert damaged["poison_wakes"] == ["ether-1"]
    assert "unconscious" not in updated["conditions"]
    assert "poisoned" in updated["conditions"]
    assert active["active"] is True
    assert active["metadata"]["poison_state"]["unconscious_wake_reason"] == "damage"
    assert active["changes"] == [{"path": "conditions", "mode": "add", "value": ["poisoned"]}]

    shaken, woken = wake_poison_effects(
        sheet,
        trigger="action_shake",
        effect_ids=["ether-1"],
    )
    assert woken == ["ether-1"]
    assert "unconscious" not in shaken["conditions"]
    shaken_effect = next(item for item in shaken["effects"] if item["id"] == "ether-1")
    assert shaken_effect["metadata"]["poison_state"]["unconscious_wake_reason"] == "action_shake"
    assert shaken_effect["active"] is True

    zero_hp = default_character_sheet()
    zero_hp["edition"] = "2014"
    zero_hp["combat"]["hp"].update(value=1, max=10)
    zero_hp, _ = add_effect(zero_hp, effect)
    knocked_out = apply_damage_to_sheet(
        zero_hp,
        amount=1,
        damage_type="slashing",
        source="attack:zero-hp",
        ruleset="2014",
        death_saves=True,
    )["sheet"]
    still_unconscious_poison = next(
        item for item in knocked_out["effects"] if item["id"] == "ether-1"
    )
    assert "unconscious" in knocked_out["conditions"]
    assert "unconscious" in still_unconscious_poison["changes"][0]["value"]


@pytest.mark.parametrize(("poison_id", "hours"), [("malice", 1), ("torpor", 4)])
def test_malice_and_torpor_expiry_remove_only_their_owned_riders(
    poison_id: str, hours: int
) -> None:
    effect_id = f"{poison_id}-instance"
    effect = build_poison_effect(
        poison_id,
        effect_id=effect_id,
        elapsed_ticks=0,
        save_succeeded=False,
        duration_roll_hours=hours if poison_id == "torpor" else None,
    )
    sheet, _ = add_effect(default_character_sheet(), effect)
    sheet["conditions"].append("prone")

    advanced = advance_effect_durations(sheet, period="hour", amount=hours)

    assert effect_id in advanced["expired"]
    assert advanced["sheet"]["conditions"] == ["prone"]


def test_oil_of_taggit_wakes_only_on_damage_and_keeps_poison_active() -> None:
    effect = build_poison_effect(
        "oil_of_taggit",
        effect_id="taggit-instance",
        elapsed_ticks=0,
        save_succeeded=False,
    )
    sheet, _ = add_effect(default_character_sheet(), effect)
    sheet["combat"]["hp"].update(value=20, max=20)

    shaken, woken = wake_poison_effects(sheet, trigger="action_shake")
    assert woken == []
    assert "unconscious" in shaken["conditions"]

    damaged = apply_damage_to_sheet(
        sheet,
        amount=1,
        damage_type="slashing",
        ruleset="2014",
    )
    assert damaged["poison_wakes"] == ["taggit-instance"]
    assert "unconscious" not in damaged["sheet"]["conditions"]
    assert "poisoned" in damaged["sheet"]["conditions"]
    active = next(item for item in damaged["sheet"]["effects"] if item["id"] == "taggit-instance")
    assert active["active"] is True
    assert active["metadata"]["poison_state"]["unconscious_wake_reason"] == "damage"
