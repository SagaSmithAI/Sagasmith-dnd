from copy import deepcopy
from pathlib import Path

import pytest

from sagasmith_dnd import bardic_inspiration as b
from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.combat_engine import resolve_actor_check, resolve_death_save_to_sheet
from sagasmith_dnd.core_content import PACK_ID, PACK_VERSION, build_srd2014_content
from sagasmith_dnd.engine import resolve_attack
from sagasmith_dnd.lifecycle import advance_elapsed_effect_durations


def bard(level=1):
    _, artifacts = build_srd2014_content(Path(__file__).parents[3] / "skills")
    source = next(a for a in artifacts if a["id"] == b.FEATURE)
    sheet = default_character_sheet()
    sheet["progression"].update(level=level, classes=[{"name": "Bard", "level": level}])
    sheet["combat"]["hp"] = {"value": 30, "max": 30, "temp": 0}
    sheet["content"]["features"] = [
        {
            **deepcopy(source["card"]),
            "id": b.FEATURE,
            "pack_id": PACK_ID,
            "pack_version": PACK_VERSION,
            "rule_refs": source["rule_refs"],
            "mechanic_refs": source["mechanic_refs"],
        }
    ]
    return sheet


def inspired():
    sheet = default_character_sheet()
    sheet["effects"] = [b.grant_effect(bard(), sheet, source_actor_id="bard", effect_id="gift")]
    return sheet


class Dice:
    def __init__(self, *values):
        self.values = list(values)
        self.calls = []

    def randint(self, low, high):
        value = self.values.pop(0)
        assert low <= value <= high
        self.calls.append((low, high, value))
        return value


@pytest.mark.parametrize(
    "level,sides", [(1, 6), (4, 6), (5, 8), (9, 8), (10, 10), (14, 10), (15, 12)]
)
def test_source_die_uses_bard_class_level_at_grant_time(level, sides):
    source = bard(level)
    source["progression"]["level"] = 20  # Other class levels never increase the die.
    effect = b.grant_effect(source, default_character_sheet(), source_actor_id="b", effect_id="e")
    assert effect["metadata"]["die_size"] == sides


@pytest.mark.parametrize("kind", ["ability", "save"])
def test_post_d20_boundary_does_not_roll_die_until_acceptance(kind):
    sheet = inspired()
    actor = {"id": "a", "sheet": sheet, "derived": derive_character_sheet(sheet)}
    rng = Dice(9)
    with pytest.raises(b.InspirationDecisionRequiredError):
        resolve_actor_check(actor, kind=kind, ability="strength", dc=10, rng=rng)
    assert rng.calls == [(1, 20, 9)]
    rng = Dice(9, 1)
    with b.inspiration_decisions(
        lambda aid, card, result, effect, dice: b.add_die(result, card, effect, rng=dice)
    ):
        result = resolve_actor_check(actor, kind=kind, ability="strength", dc=10, rng=rng)
    assert result["success"] and result["total"] == 10 and result["natural"] == 9
    assert rng.calls == [(1, 20, 9), (1, 6, 1)]


@pytest.mark.parametrize("natural", [1, 20])
def test_inspiration_keeps_attack_and_death_save_natural_rules(natural):
    sheet = inspired()
    roll = resolve_attack(armor_class=22, attack_bonus=0, rng=Dice(natural))
    result = b.add_die(roll, sheet, b.held(sheet), rng=Dice(6))
    assert result["hit"] is (natural == 20)
    sheet["combat"]["hp"]["value"] = 0
    sheet["combat"]["death_saves"] = {"successes": 0, "failures": 1}
    with b.inspiration_decisions(
        lambda aid, card, result, effect, dice: b.add_die(result, card, effect, rng=dice)
    ):
        death = resolve_death_save_to_sheet(sheet, actor_id_value="a", rng=Dice(natural, 6))
    assert death["outcome"] == ("revived" if natural == 20 else "dead")


def test_expiry_and_nonrolling_checks_never_offer_or_spend():
    sheet = inspired()
    actor = {"id": "a", "sheet": sheet, "derived": derive_character_sheet(sheet)}
    passive = resolve_actor_check(actor, kind="ability", ability="strength", dc=10, passive=True)
    assert passive["natural"] is None
    assert b.held(advance_elapsed_effect_durations(sheet, elapsed_ticks=99)["sheet"])
    expired = advance_elapsed_effect_durations(sheet, elapsed_ticks=100)["sheet"]
    assert b.held(expired) is None
    sheet["edition"] = "2024"
    assert b.held(sheet) is None
