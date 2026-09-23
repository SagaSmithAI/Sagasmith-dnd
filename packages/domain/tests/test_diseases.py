import pytest

from sagasmith_dnd.character_schema import active_effect_roll_bonus, default_character_sheet
from sagasmith_dnd.diseases import (
    DISEASE_SOURCE_REF,
    DiseaseError,
    advance_disease_clock,
    apply_sight_rot_ointment,
    cackle_carrier_immunity,
    cure_disease,
    infection_state,
    resolve_cackle_end_turn,
    resolve_cackle_spread,
    resolve_cackle_stress,
    resolve_long_rest,
    resolve_sewer_plague_rest_exhaustion,
    sewer_plague_hit_die_healing,
    sight_rot_ability_check_penalty,
    sight_rot_attack_penalty,
)


@pytest.mark.parametrize(
    ("disease", "roll", "due"),
    [("cackle_fever", 2, 2 * 600), ("sewer_plague", 3, 3 * 14400), ("sight_rot", None, 14400)],
)
def test_failed_exposure_persists_source_and_absolute_symptom_boundary(disease, roll, due):
    state = infection_state(
        disease,
        actor_id="actor-1",
        elapsed_ticks=0,
        save_succeeded=False,
        incubation_roll=roll,
    )
    assert state["source_ref"] == DISEASE_SOURCE_REF
    assert state["symptoms_due_elapsed_ticks"] == due
    assert state["symptomatic"] is False
    assert advance_disease_clock(state, elapsed_ticks=due - 1)["symptomatic"] is False
    assert advance_disease_clock(state, elapsed_ticks=due)["symptomatic"] is True
    assert advance_disease_clock(state, elapsed_ticks=due * 10)["symptomatic"] is True


def test_successful_or_rejected_exposure_consumes_no_incubation_die():
    assert (
        infection_state("cackle_fever", actor_id="actor-1", elapsed_ticks=0, save_succeeded=True)
        is None
    )
    with pytest.raises(DiseaseError, match="cannot consume incubation"):
        infection_state(
            "cackle_fever",
            actor_id="actor-1",
            elapsed_ticks=0,
            save_succeeded=True,
            incubation_roll=2,
        )
    with pytest.raises(DiseaseError, match="disease is not"):
        infection_state(
            "unreviewed_variant",
            actor_id="actor-1",
            elapsed_ticks=0,
            save_succeeded=False,
            incubation_roll=2,
        )


def test_cackle_recovery_save_is_required_and_third_failure_rolls_bundled_madness_once():
    state = infection_state(
        "cackle_fever",
        actor_id="actor-1",
        elapsed_ticks=0,
        save_succeeded=False,
        incubation_roll=1,
    )
    state = advance_disease_clock(state, elapsed_ticks=600)
    with pytest.raises(DiseaseError, match="requires a long-rest save"):
        resolve_long_rest(state, save_succeeded=None, elapsed_ticks=600)
    skipped = {"state": state}
    for count in range(2):
        result = resolve_long_rest(skipped["state"], save_succeeded=False, elapsed_ticks=600)
        skipped = result
    result = resolve_long_rest(
        result["state"], save_succeeded=False, elapsed_ticks=600, madness_d100=99
    )
    event = result["events"][-1]
    assert event["kind"] == "indefinite_madness"
    assert event["madness"]["source_ref"] == "bundled:srd2014/08_Gamemastering/Madness.md"
    again = resolve_long_rest(result["state"], save_succeeded=False, elapsed_ticks=600)
    assert all(event["kind"] != "indefinite_madness" for event in again["events"])


@pytest.mark.parametrize(
    ("die", "expected", "active"), [(3, 10, True), (6, 7, True), (1, 12, True)]
)
def test_cackle_success_reduces_both_dcs_and_cure_releases_exhaustion(die, expected, active):
    state = infection_state(
        "cackle_fever",
        actor_id="actor-1",
        elapsed_ticks=0,
        save_succeeded=False,
        incubation_roll=1,
    )
    state = advance_disease_clock(state, elapsed_ticks=600)
    result = resolve_long_rest(state, save_succeeded=True, elapsed_ticks=600, recovery_die=die)
    assert result["state"]["recovery_dc"] == expected
    assert result["state"]["laughter_dc"] == expected
    assert result["state"]["active"] is active
    with pytest.raises(DiseaseError):
        resolve_long_rest(state, save_succeeded=True, elapsed_ticks=600)


def test_cackle_dc_reduction_clamps_to_zero_and_cures():
    state = infection_state(
        "cackle_fever",
        actor_id="actor-1",
        elapsed_ticks=0,
        save_succeeded=False,
        incubation_roll=1,
    )
    state = advance_disease_clock(state, elapsed_ticks=600)
    state["recovery_dc"] = 1
    state["laughter_dc"] = 1
    result = resolve_long_rest(state, save_succeeded=True, elapsed_ticks=600, recovery_die=6)
    assert result["state"]["active"] is False
    assert result["state"]["recovery_dc"] == result["state"]["laughter_dc"] == 0
    assert result["state"]["exhaustion_lock"] is False


@pytest.mark.parametrize("trigger", ["entering_combat", "taking_damage", "fear", "nightmare"])
def test_cackle_stress_triggers_start_laughter_and_end_turn_save_ends_it(trigger):
    state = infection_state(
        "cackle_fever",
        actor_id="actor-1",
        elapsed_ticks=0,
        save_succeeded=False,
        incubation_roll=1,
    )
    state = advance_disease_clock(state, elapsed_ticks=600)
    failed = resolve_cackle_stress(
        state,
        trigger=trigger,
        save_succeeded=False,
        elapsed_ticks=600,
        psychic_damage_roll=7,
    )
    assert failed["events"][0] == {"kind": "psychic_damage", "rolled": 7, "damage_type": "psychic"}
    assert failed["state"]["laughing_until_elapsed_ticks"] == 610
    with pytest.raises(DiseaseError, match="stress trigger requires"):
        resolve_cackle_stress(
            failed["state"],
            trigger=trigger,
            save_succeeded=False,
            elapsed_ticks=600,
            psychic_damage_roll=4,
        )
    assert (
        resolve_cackle_end_turn(failed["state"], save_succeeded=False)["state"][
            "incapacitation_owned"
        ]
        is True
    )
    ended = resolve_cackle_end_turn(failed["state"], save_succeeded=True)
    assert ended["state"]["incapacitation_owned"] is False
    timed_out = advance_disease_clock(failed["state"], elapsed_ticks=610)
    assert timed_out["laughing"] is False
    assert timed_out["incapacitation_owned"] is False


def test_cackle_successful_spread_immunity_is_target_carrier_specific_and_24_hours():
    immunity = cackle_carrier_immunity(target_id="target", carrier_id="carrier", elapsed_ticks=120)
    assert immunity["expires_elapsed_ticks"] == 14520
    assert immunity["target_actor_id"] == "target"
    assert immunity["carrier_actor_id"] == "carrier"
    with pytest.raises(DiseaseError, match="identities"):
        cackle_carrier_immunity(target_id="", carrier_id="carrier", elapsed_ticks=0)


def test_cackle_spread_enforces_carrier_range_taxonomy_and_pair_immunity():
    carrier = infection_state(
        "cackle_fever",
        actor_id="carrier",
        elapsed_ticks=0,
        save_succeeded=False,
        incubation_roll=1,
    )
    carrier = advance_disease_clock(carrier, elapsed_ticks=600)
    carrier = resolve_cackle_stress(
        carrier,
        trigger="entering_combat",
        save_succeeded=False,
        elapsed_ticks=600,
        psychic_damage_roll=3,
    )["state"]
    ineligible = resolve_cackle_spread(
        carrier,
        target_actor_id="beast",
        target_creature_type="beast",
        target_species_id="",
        distance_ft=5,
        elapsed_ticks=600,
    )
    gnome = resolve_cackle_spread(
        carrier,
        target_actor_id="gnome",
        target_creature_type="humanoid",
        target_species_id="gnome",
        distance_ft=5,
        elapsed_ticks=600,
    )
    distant = resolve_cackle_spread(
        carrier,
        target_actor_id="distant",
        target_creature_type="humanoid",
        target_species_id="elf",
        distance_ft=10.1,
        elapsed_ticks=600,
    )
    assert (ineligible["status"], gnome["status"], distant["status"]) == (
        "ineligible",
        "immune",
        "out_of_range",
    )
    range_only = resolve_cackle_spread(
        carrier,
        target_actor_id="range-only",
        target_creature_type="humanoid",
        target_species_id="elf",
        within_10_feet=True,
        elapsed_ticks=600,
        save_succeeded=True,
    )
    assert range_only["status"] == "saved"
    saved = resolve_cackle_spread(
        carrier,
        target_actor_id="target",
        target_creature_type="humanoid",
        target_species_id="elf",
        distance_ft=10,
        elapsed_ticks=600,
        save_succeeded=True,
    )
    assert saved["status"] == "saved" and saved["dc"] == 10
    pair_immune = resolve_cackle_spread(
        carrier,
        target_actor_id="target",
        target_creature_type="humanoid",
        target_species_id="elf",
        distance_ft=0,
        elapsed_ticks=601,
        immunity=saved["immunity"],
    )
    assert pair_immune["status"] == "immune_to_carrier"
    expired = resolve_cackle_spread(
        carrier,
        target_actor_id="target",
        target_creature_type="humanoid",
        target_species_id="elf",
        distance_ft=0,
        elapsed_ticks=saved["immunity"]["expires_elapsed_ticks"],
        immunity=saved["immunity"],
        save_succeeded=False,
        incubation_roll=4,
    )
    assert expired["status"] == "infected"
    assert expired["infection"]["symptoms_due_elapsed_ticks"] == (
        saved["immunity"]["expires_elapsed_ticks"] + 4 * 600
    )
    with pytest.raises(DiseaseError, match="currently laughing"):
        resolve_cackle_spread(
            advance_disease_clock(
                infection_state(
                    "cackle_fever",
                    actor_id="not-laughing",
                    elapsed_ticks=0,
                    save_succeeded=False,
                    incubation_roll=1,
                ),
                elapsed_ticks=600,
            ),
            target_actor_id="target",
            target_creature_type="humanoid",
            target_species_id="elf",
            distance_ft=1,
            elapsed_ticks=600,
        )


def test_sewer_rest_transition_explicitly_follows_ordinary_exhaustion_recovery():
    state = infection_state(
        "sewer_plague",
        actor_id="actor-1",
        elapsed_ticks=0,
        save_succeeded=False,
        incubation_roll=1,
    )
    state = advance_disease_clock(state, elapsed_ticks=14400)
    successful = resolve_long_rest(state, save_succeeded=True, elapsed_ticks=14400)
    failed = resolve_long_rest(state, save_succeeded=False, elapsed_ticks=14400)
    success_delta = next(
        event for event in successful["events"] if event["kind"] == "exhaustion_reduce_total"
    )
    assert success_delta["after_ordinary_rest_recovery"] is True
    assert success_delta["cure_below"] == 1
    assert {event["kind"] for event in failed["events"]} == {
        "long_rest_hp_recovery_blocked",
        "hit_dice_recovery_halved",
        "exhaustion_increase",
    }


def test_sewer_exhaustion_order_applies_ordinary_recovery_before_disease_delta_and_death():
    cured = resolve_sewer_plague_rest_exhaustion(
        current_exhaustion=2,
        disease_save_succeeded=True,
        ordinary_2014_recovery_applies=True,
    )
    failed = resolve_sewer_plague_rest_exhaustion(
        current_exhaustion=5,
        disease_save_succeeded=False,
        ordinary_2014_recovery_applies=False,
    )
    already_dead = resolve_sewer_plague_rest_exhaustion(
        current_exhaustion=6,
        disease_save_succeeded=False,
        ordinary_2014_recovery_applies=False,
    )
    assert cured["exhaustion"] == 0 and cured["disease_cured"] is True
    assert [event["kind"] for event in cured["events"]] == [
        "ordinary_2014_rest_recovery",
        "sewer_plague_rest_success",
    ]
    assert failed["exhaustion"] == 6 and failed["dead"] is True
    assert (
        already_dead["dead"] is True
        and already_dead["events"][0]["kind"] == "already_dead_at_exhaustion_6"
    )


def test_sewer_plague_halves_normal_hit_die_healing_before_hp_cap():
    healing = sewer_plague_hit_die_healing(
        [{"total": 4}, {"total": 4}], constitution_modifier=1
    )
    assert healing == {"normal_hit_die_healing": 10, "disease_hit_die_healing": 5}
    negative_modifier = sewer_plague_hit_die_healing(
        [{"total": 1}, {"total": 1}], constitution_modifier=-2
    )
    assert negative_modifier == {"normal_hit_die_healing": 0, "disease_hit_die_healing": 0}


def test_sight_rot_ointment_prevents_one_rest_and_three_doses_cure_without_restoring_blindness():
    state = infection_state("sight_rot", actor_id="actor-1", elapsed_ticks=0, save_succeeded=False)
    state = advance_disease_clock(state, elapsed_ticks=14400)
    state["sight_penalty"] = 4
    state["blindness_owned"] = True
    applied = apply_sight_rot_ointment(state)
    rested = resolve_long_rest(applied["state"], save_succeeded=True, elapsed_ticks=28800)
    assert rested["state"]["sight_penalty"] == 4
    assert rested["state"]["blindness_owned"] is True
    cured = apply_sight_rot_ointment(rested["state"], doses=2)
    assert cured["state"]["active"] is False
    assert cured["state"]["blindness_owned"] is True
    assert {event["kind"] for event in cured["events"]} == {
        "rest_worsening_prevented",
        "disease_cured",
    }
    with pytest.raises(DiseaseError, match="extra doses are not consumed"):
        apply_sight_rot_ointment(rested["state"], doses=3)
    magic_cure = cure_disease(cured["state"], disease_id="sight_rot")
    assert magic_cure["state"]["blindness_owned"] is True
    assert any(event["kind"] == "blindness_restoration_required" for event in magic_cure["events"])


def test_sight_rot_long_rest_needs_no_save_and_ointment_can_be_pre_symptom():
    state = infection_state("sight_rot", actor_id="actor-1", elapsed_ticks=0, save_succeeded=False)
    applied = apply_sight_rot_ointment(state)
    rested = resolve_long_rest(applied["state"], save_succeeded=None, elapsed_ticks=14400)
    assert rested["state"]["sight_penalty"] == 0


def test_sight_rot_penalty_is_attack_and_contextual_sight_check_only():
    state = infection_state("sight_rot", actor_id="actor-1", elapsed_ticks=0, save_succeeded=False)
    assert sight_rot_attack_penalty(state) == 0
    state = advance_disease_clock(state, elapsed_ticks=14400)
    state["sight_penalty"] = 3
    assert sight_rot_attack_penalty(state) == -3
    assert sight_rot_ability_check_penalty(state, relies_on_sight=True) == -3
    assert sight_rot_ability_check_penalty(state, relies_on_sight=False) == 0
    with pytest.raises(DiseaseError, match="resolved check context"):
        sight_rot_ability_check_penalty(state, relies_on_sight="caller says so")
    sheet = default_character_sheet()
    sheet["effects"].append(
        {
            "id": "sight-rot-penalty",
            "name": "Sight Rot penalty",
            "kind": "disease_state",
            "source": DISEASE_SOURCE_REF,
            "active": True,
            "changes": [
                {
                    "path": "rolls.attack.bonus",
                    "mode": "add",
                    "value": sight_rot_attack_penalty(state),
                },
            ],
            "metadata": {"disease_state": state},
        }
    )
    assert active_effect_roll_bonus(sheet, "attack") == -3
    assert active_effect_roll_bonus(sheet, "ability") == 0
    state = apply_sight_rot_ointment(state, doses=3)["state"]
    assert sight_rot_attack_penalty(state) == 0


def test_disease_cure_only_releases_its_owned_riders():
    state = infection_state(
        "cackle_fever",
        actor_id="actor-1",
        elapsed_ticks=0,
        save_succeeded=False,
        incubation_roll=1,
    )
    state = advance_disease_clock(state, elapsed_ticks=600)
    cured = cure_disease(state, disease_id="cackle_fever")
    assert cured["state"]["active"] is False
    assert cured["state"]["exhaustion_lock"] is False
    assert any(event["kind"] == "exhaustion_removal_released" for event in cured["events"])
    with pytest.raises(DiseaseError, match="exact disease instance"):
        cure_disease(state, disease_id="sight_rot")
