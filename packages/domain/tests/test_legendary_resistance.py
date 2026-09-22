from copy import deepcopy
from pathlib import Path

import pytest

from sagasmith_dnd.character_schema import derive_character_sheet
from sagasmith_dnd.combat_engine import resolve_actor_check, resolve_death_save_to_sheet
from sagasmith_dnd.legendary_resistance import (
    SaveDecisionRequiredError,
    feature,
    saving_throw_decisions,
    source_contract,
    succeed,
)
from sagasmith_dnd.random_stream import CampaignRandomStream, initial_random_stream
from sagasmith_dnd.statblocks import parse_2014_statblock

SOURCES = Path(__file__).parents[3] / "skills/full/skills/dnd-dm/srd/references-2014-en/10_Monsters"


def dragon():
    path = SOURCES / "Monsters_Each/Adult_Black_Dragon_(Chromatic).md"
    return parse_2014_statblock(path.read_text(encoding="utf-8"), source_key="black-dragon",
                               rule_refs=["bundled:srd2014/" + path.name]).sheet


class Dice:
    def __init__(self, value):
        self.value = value
        self.calls = 0

    def randint(self, low, high):
        self.calls += 1
        assert low <= self.value <= high
        return self.value


def test_every_bundled_legendary_resistance_leaf_imports_with_exact_daily_uses():
    paths = [p for p in (SOURCES / "Monsters_Each").glob("*.md")
             if "***Legendary Resistance" in p.read_text(encoding="utf-8")]
    assert len(paths) >= 20
    for path in paths:
        parsed = parse_2014_statblock(path.read_text(encoding="utf-8"), source_key=path.stem,
                                     rule_refs=[str(path.relative_to(SOURCES))])
        entry = feature(parsed.sheet)
        assert entry and entry["uses"]["value"] == entry["uses"]["max"] == 3
        assert entry["uses"]["recovers_on"] == "long_rest"
        assert not any("Legendary Resistance" in warning for warning in parsed.warnings)


@pytest.mark.parametrize("count", [1, 2, 5])
def test_source_count_is_not_assumed_to_be_three(count):
    text = "If the dragon fails a saving throw, it can choose to succeed instead."
    assert source_contract(f"Legendary Resistance ({count}/Day)", text)["maximum"] == count
    assert source_contract(f"Legendary Resistance ({count}/Day)", text + " Except fire.") is None


@pytest.mark.parametrize("kind,dc,uses", [("ability", 40, 3), ("save", 1, 3), ("save", 40, 0)])
def test_no_offer_for_ability_success_or_exhausted_uses(kind, dc, uses):
    sheet = dragon()
    feature(sheet)["uses"]["value"] = uses
    result = resolve_actor_check({"id": "dragon", "sheet": sheet,
                                  "derived": derive_character_sheet(sheet)},
                                 kind=kind, ability="wisdom", dc=dc, rng=Dice(10))
    assert "legendary_resistance" not in result


def test_failure_pauses_before_settlement_and_success_keeps_dice():
    sheet = dragon()
    rng = Dice(2)
    actor = {"id": "dragon", "sheet": sheet, "derived": derive_character_sheet(sheet)}
    with pytest.raises(SaveDecisionRequiredError) as raised:
        resolve_actor_check(actor, kind="save", ability="wisdom", dc=40, rng=rng)
    assert raised.value.actor_id == "dragon" and rng.calls == 1
    original = deepcopy(raised.value.result)
    selected = succeed(original, sheet)
    assert selected["success"] is True
    assert selected["natural"] == original["natural"] == 2
    assert original["success"] is False and feature(sheet)["uses"]["value"] == 3


@pytest.mark.parametrize("natural", [1, 2])
def test_failed_death_save_can_succeed_without_inventing_a_natural_twenty(natural):
    sheet = dragon()
    sheet["combat"]["hp"]["value"] = 0
    sheet["conditions"] = ["unconscious"]
    sheet["combat"]["death_saves"] = {"successes": 2, "failures": 2}
    rng = Dice(natural)
    with saving_throw_decisions(lambda actor, card, result, entry: succeed(result, card)):
        result = resolve_death_save_to_sheet(sheet, actor_id_value="dragon", rng=rng)
    assert result["outcome"] == "stable" and result["natural"] == natural
    assert result["sheet"]["combat"]["hp"]["value"] == 0 and rng.calls == 1
    assert "dead" not in result["sheet"]["conditions"]


def test_recorded_random_prefix_does_not_advance_or_rewind_persisted_stream():
    first = CampaignRandomStream.from_campaign_state(
        "c", {"random_stream": initial_random_stream("save")}, operation="save",
    )
    first.capture_draws = True
    expected = [first.randint(1, 20), first.randint(1, 6)]
    resumed = CampaignRandomStream.from_campaign_state(
        "c", {"random_stream": first.persisted_state()}, operation="resume",
    )
    resumed.replay_prefix = deepcopy(first.recorded_draws)
    assert [resumed.randint(1, 20), resumed.randint(1, 6)] == expected
    assert resumed.draw_count == 0 and resumed.position == first.position
    assert resumed.randint(1, 20) == first.randint(1, 20)
    assert resumed.draw_count == 1


def test_only_a_long_rest_restores_expended_legendary_resistance():
    from sagasmith_dnd.lifecycle import apply_rest

    sheet = dragon()
    feature(sheet)["uses"]["value"] = 1
    short = apply_rest(sheet, rest_type="short_rest")
    assert feature(short["sheet"])["uses"]["value"] == 1
    long = apply_rest(sheet, rest_type="long_rest")
    assert feature(long["sheet"])["uses"]["value"] == 3


def test_automatic_failed_save_is_a_choice_without_rolling_or_using_a_reaction():
    sheet = dragon()
    sheet["conditions"] = ["paralyzed"]
    rng = Dice(20)
    actor = {"id": "dragon", "sheet": sheet, "derived": derive_character_sheet(sheet)}
    with saving_throw_decisions(lambda actor, card, result, entry: succeed(result, card)):
        result = resolve_actor_check(actor, kind="save", ability="dexterity", dc=20, rng=rng)
    assert result["success"] is True and result["natural"] is None and rng.calls == 0
    assert result["automatic_failure"] is False
    assert result["legendary_resistance"]["original_result"]["automatic_failure"] is True
