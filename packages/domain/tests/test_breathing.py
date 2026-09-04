from copy import deepcopy

import pytest

from sagasmith_dnd.breathing import (
    BREATHING_EFFECT_ID,
    advance_breathing_rounds,
    begin_holding_breath,
    restore_breathing,
    tortle_hold_breath_available,
)
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.combat_engine import resolve_death_save_to_sheet, stabilize_sheet
from sagasmith_dnd.hit_points import apply_basic_healing_to_sheet
from sagasmith_dnd.lifecycle import advance_effect_durations, advance_elapsed_effect_durations
from sagasmith_dnd.standard_feature_ids import (
    TORTLE_HOLD_BREATH_ARTIFACT_ID,
    TORTLE_HOLD_BREATH_FEATURE_ID,
    TORTLE_HOLD_BREATH_LEGACY_PACK_ID,
)


def test_2014_breathing_uses_one_round_clock_and_locks_recovery() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["abilities"]["constitution"]["score"] = 12
    started = begin_holding_breath(sheet)["sheet"]
    timer = next(item for item in started["effects"] if item["id"] == BREATHING_EFFECT_ID)
    assert timer["metadata"]["hold_remaining_rounds"] == 20
    suffocating = advance_breathing_rounds(started, rounds=20)["sheet"]
    assert "suffocating" in suffocating["conditions"]
    # The PHB restriction starts at 0 HP; the grace rounds are still a
    # conscious creature's opportunity to reach air and can receive healing.
    healed = apply_basic_healing_to_sheet(suffocating, amount=1)["sheet"]
    assert healed["combat"]["hp"]["value"] == 1
    dropped = advance_breathing_rounds(suffocating, rounds=1)["sheet"]
    assert dropped["combat"]["hp"]["value"] == 0
    assert "unconscious" in dropped["conditions"]
    with pytest.raises(ValueError, match="cannot regain"):
        apply_basic_healing_to_sheet(dropped, amount=1)
    death_save = resolve_death_save_to_sheet(dropped, rng=_SequenceRng(20))
    assert death_save["outcome"] == "pending"
    assert death_save["sheet"]["combat"]["hp"]["value"] == 0
    three_successes = deepcopy(dropped)
    three_successes["combat"]["death_saves"] = {"successes": 2, "failures": 0}
    blocked_stable = resolve_death_save_to_sheet(three_successes, rng=_SequenceRng(10))
    assert blocked_stable["outcome"] == "pending"
    assert "stable" not in blocked_stable["sheet"]["conditions"]
    with pytest.raises(Exception, match="cannot become stable"):
        stabilize_sheet(dropped)
    restored = restore_breathing(dropped)["sheet"]
    assert "suffocating" not in restored["conditions"]


def test_suffocation_ends_concentration_and_death_save_recovery_stays_locked() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["effects"] = [
        {
            "id": "spell-concentration",
            "active": True,
            "concentration": True,
            "duration": {"period": "hour", "remaining": 1},
            "changes": [],
        }
    ]
    dropped = advance_breathing_rounds(
        begin_holding_breath(sheet, choking=True)["sheet"], rounds=1
    )["sheet"]
    assert dropped["combat"]["hp"]["value"] == 0
    assert dropped["effects"][0]["active"] is False
    dropped["combat"]["death_saves"] = {"successes": 3, "failures": 2}
    failed = resolve_death_save_to_sheet(dropped, rng=_SequenceRng(5))
    assert failed["outcome"] == "dead"
    assert failed["sheet"]["combat"]["death_saves"] == {"successes": 3, "failures": 3}

    nat20 = deepcopy(dropped)
    nat20["combat"]["death_saves"] = {"successes": 1, "failures": 2}
    pending = resolve_death_save_to_sheet(nat20, rng=_SequenceRng(20))
    assert pending["outcome"] == "pending"
    assert pending["sheet"]["combat"]["death_saves"] == {"successes": 2, "failures": 2}


def test_suffocation_does_not_reset_existing_death_save_failures() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"]["value"] = 0
    sheet["combat"]["death_saves"] = {"successes": 1, "failures": 2}
    sheet["conditions"] = ["unconscious"]
    dropped = advance_breathing_rounds(
        begin_holding_breath(sheet, choking=True)["sheet"], rounds=1
    )["sheet"]
    # SRD 2014: only regaining HP or becoming stable resets the counters.
    assert dropped["combat"]["death_saves"] == {"successes": 1, "failures": 2}
    assert resolve_death_save_to_sheet(dropped, rng=_SequenceRng(5))["outcome"] == "dead"


@pytest.mark.parametrize(
    ("failures", "roll", "outcome"),
    [(1, 5, "pending"), (2, 5, "dead"), (1, 1, "dead"), (2, 10, "stable"), (2, 20, "revived")],
)
def test_restored_air_death_save_uses_the_new_roll(failures: int, roll: int, outcome: str) -> None:
    sheet = advance_breathing_rounds(
        begin_holding_breath(default_character_sheet(), choking=True)["sheet"], rounds=1
    )["sheet"]
    sheet["combat"]["death_saves"] = {"successes": 3, "failures": failures}
    breathing_again = restore_breathing(sheet)["sheet"]
    assert "stable" not in breathing_again["conditions"]
    result = resolve_death_save_to_sheet(breathing_again, rng=_SequenceRng(roll))
    assert result["outcome"] == outcome


class _SequenceRng:
    def __init__(self, *values: int) -> None:
        self.values = iter(values)

    def randint(self, _lower: int, _upper: int) -> int:
        return next(self.values)


def test_tortle_hold_breath_requires_exact_source_provenance() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["content"]["selections"] = [
        {
            "artifact_id": "forged",
            "kind": "species",
            "name": "Tortle",
            "pack_id": "forged",
            "pack_version": "1.0.1",
            "rule_refs": [],
            "mechanic_refs": [],
            "selection": {},
        }
    ]
    assert not tortle_hold_breath_available(sheet)
    assert begin_holding_breath(sheet)["effect"]["metadata"]["hold_remaining_rounds"] == 10


def test_narrative_ticks_settle_the_same_breathing_round_clock() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    started = begin_holding_breath(sheet)["sheet"]
    advanced = advance_elapsed_effect_durations(started, elapsed_ticks=10)["sheet"]
    timer = next(item for item in advanced["effects"] if item["id"] == BREATHING_EFFECT_ID)
    assert timer["metadata"]["phase"] == "suffocating"
    assert timer["metadata"]["suffocation_remaining_rounds"] == 1


@pytest.mark.parametrize("pack_version", ["1.0.0", "1.0.1"])
def test_tortle_provenance_gets_the_fixed_one_hour_hold(pack_version: str) -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    source_ref = "rule-source:user.rulebook.d-d-5e-the-tortle-package.e3234de670da#chunk:9"
    sheet["content"]["selections"] = [
        {
            "artifact_id": TORTLE_HOLD_BREATH_ARTIFACT_ID,
            "kind": "species",
            "name": "Tortle",
            "pack_id": TORTLE_HOLD_BREATH_LEGACY_PACK_ID,
            "pack_version": pack_version,
            "rule_refs": [source_ref],
            "mechanic_refs": [],
            "selection": {},
        }
    ]
    sheet["content"]["features"] = [
        {
            "id": TORTLE_HOLD_BREATH_FEATURE_ID,
            "name": "Hold Breath",
            "source_key": "Tortle",
            "pack_id": TORTLE_HOLD_BREATH_LEGACY_PACK_ID,
            "pack_version": pack_version,
            "rule_refs": [source_ref],
            "mechanic_refs": [],
        }
    ]
    assert tortle_hold_breath_available(sheet)
    timer = begin_holding_breath(sheet)["effect"]
    assert timer["metadata"]["hold_remaining_rounds"] == 600
    sheet["content"]["features"][0]["pack_version"] = (
        "1.0.1" if pack_version == "1.0.0" else "1.0.0"
    )
    assert not tortle_hold_breath_available(sheet)
    assert begin_holding_breath(sheet)["effect"]["metadata"]["hold_remaining_rounds"] == 10


def test_suffocation_expiry_waits_for_the_actor_turn_start() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    started = begin_holding_breath(sheet)["sheet"]
    grace = advance_breathing_rounds(started, rounds=10, defer_drop_until_turn_start=True)["sheet"]
    timer = next(item for item in grace["effects"] if item["id"] == BREATHING_EFFECT_ID)
    assert timer["metadata"]["phase"] == "suffocating"
    assert timer["metadata"]["suffocation_remaining_rounds"] == 1
    early_turn = advance_effect_durations(grace, period="turn_start")["sheet"]
    assert early_turn == grace
    still_grace = advance_breathing_rounds(grace, rounds=1, defer_drop_until_turn_start=True)[
        "sheet"
    ]
    assert still_grace["combat"]["hp"]["value"] > 0
    expired = advance_effect_durations(still_grace, period="turn_start")["sheet"]
    assert expired["combat"]["hp"]["value"] == 0


def test_choking_starts_in_the_suffocation_phase_without_hold_time() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    choking = begin_holding_breath(sheet, choking=True)["sheet"]
    timer = next(item for item in choking["effects"] if item["id"] == BREATHING_EFFECT_ID)
    assert timer["metadata"]["phase"] == "suffocating"
    assert timer["metadata"]["hold_remaining_rounds"] == 0
    assert "suffocating" in choking["conditions"]
