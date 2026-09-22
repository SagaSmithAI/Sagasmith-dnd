from copy import deepcopy
from random import Random

import pytest

from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.combat_engine import (
    NeedsRulingError,
    preflight_attack,
    resolve_actor_check,
    resolve_actor_contest,
    resolve_actor_group_check,
    roll_attack_action,
)
from sagasmith_dnd.core_rule_pack import get_core_rule_pack
from sagasmith_dnd.objects import object_attack_plan
from sagasmith_dnd.rule_engine import ResolutionContext, resolution_context
from sagasmith_dnd.standard_content import build_standard2014_content
from sagasmith_dnd.sunlight import SUNLIGHT_MECHANIC, sunlight_disadvantage


def actor(identifier="observer", sensitive=True):
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
    if sensitive:
        _, artifacts = build_standard2014_content()
        drow = next(a for a in artifacts if a["id"].endswith("species.drow"))
        feature = next(
            f for f in drow["card"]["grants"]["features"] if f["name"] == "Sunlight Sensitivity"
        )
        sheet["content"]["features"] = [{**feature, "id": "drow-sunlight"}]
    return {"id": identifier, "sheet": sheet, "derived": derive_character_sheet(sheet)}


def context(observer=False, subject=False, sight=True, subject_id="subject"):
    return {
        "facts": {
            "actor_id": "observer",
            "subject": {"kind": "actor", "id": subject_id, "scene_id": "scene"},
            "actor_in_direct_sunlight": observer,
            "subject_in_direct_sunlight": subject,
            "relies_on_sight": sight,
        }
    }


def rules(ctx):
    return ResolutionContext(
        "sunlight-test", get_core_rule_pack("2014"), (), {}, {"_sunlight": ctx}
    )


@pytest.mark.parametrize(
    "observer,subject", [(False, False), (False, True), (True, False), (True, True)]
)
@pytest.mark.parametrize("advantage", [False, True])
def test_attack_uses_both_source_locations_and_normal_cancellation(observer, subject, advantage):
    attacker, target = actor(), actor("subject", sensitive=False)
    plan = preflight_attack(
        attacker,
        target,
        action={
            "context": {
                "sunlight": context(observer, subject),
                "advantage": advantage,
            }
        },
        rules=rules(None),
        require_attack_action=False,
    )
    assert plan["disadvantage"] == (observer or subject)
    result = roll_attack_action(plan=plan, rng=Random(2))
    assert len(result["rolls"]) == (2 if advantage != (observer or subject) else 1)
    assert any(r["mechanic_id"] == SUNLIGHT_MECHANIC for r in plan["rule_receipts"])


@pytest.mark.parametrize("condition", ["blinded", "poisoned", "prone", "restrained"])
def test_disadvantage_does_not_stack_and_advantage_still_cancels(condition):
    attacker = actor()
    attacker["sheet"]["conditions"] = [condition]
    attacker["derived"] = derive_character_sheet(attacker["sheet"])
    plan = preflight_attack(
        attacker,
        actor("subject", False),
        action={
            "context": {
                "sunlight": context(True),
                "advantage": True,
            }
        },
        require_attack_action=False,
    )
    assert plan["disadvantage"] and plan["advantage"]
    assert len(roll_attack_action(plan=plan, rng=Random(2))["rolls"]) == 1


@pytest.mark.parametrize(
    "observer,subject,sight",
    [(False, True, True), (True, False, True), (True, True, False), (False, False, True)],
)
def test_perception_requires_sight_and_uses_the_observed_subject(observer, subject, sight):
    result = resolve_actor_check(
        actor(),
        kind="check",
        ability="perception",
        dc=10,
        rules=rules(context(observer, subject, sight)),
        rng=Random(2),
    )
    assert len(result["rolls"]) == (2 if sight and (observer or subject) else 1)
    assert any(r["mechanic_id"] == SUNLIGHT_MECHANIC for r in result["rule_receipts"])


@pytest.mark.parametrize(
    "invalid",
    [None, {"facts": {}}, context(None), context(False, None), context(subject_id="wrong")],
)
def test_missing_ambiguous_or_wrong_subject_stops_before_rng(invalid):
    attacker = actor()
    before = deepcopy(attacker)
    with pytest.raises(NeedsRulingError):
        preflight_attack(
            attacker,
            actor("subject", False),
            action={"context": {"sunlight": invalid}},
            require_attack_action=False,
        )
    assert attacker == before


def test_unknown_sight_reliance_is_not_inferred_and_hearing_needs_no_sunlight():
    with pytest.raises(NeedsRulingError, match="relies on sight"):
        resolve_actor_check(
            actor(), kind="check", ability="perception", dc=10, rules=rules(context(sight=None))
        )
    result = resolve_actor_check(
        actor(),
        kind="check",
        ability="perception",
        dc=10,
        rules=rules(context(None, None, False)),
        rng=Random(2),
    )
    assert len(result["rolls"]) == 1


def test_object_attack_and_exact_source_scope_do_not_use_creature_shortcuts():
    attacker = actor()
    profile = {
        "id": "door",
        "scene_id": "scene",
        "name": "Door",
        "material": "wood",
        "size": "medium",
        "resilience": "fragile",
        "armor_class": 15,
        "hit_points": 10,
    }
    plan = object_attack_plan(
        attacker,
        profile,
        weapon_id="unarmed-strike",
        rules=rules(context(False, True, subject_id="door")),
    )
    assert plan["disadvantage"]
    # The SRD monster wording has a different self-only scope. Do not broaden it.
    attacker["sheet"]["content"]["features"][0]["choices"]["source_trait"]["scope"] = "self"
    with pytest.raises(NeedsRulingError, match="exact source mechanic"):
        sunlight_disadvantage(attacker["sheet"], context(False, True), actor_id="observer")


def test_unrelated_checks_and_2024_rules_are_unaffected():
    result = resolve_actor_check(
        actor(), kind="check", ability="investigation", dc=10, rng=Random(2)
    )
    assert len(result["rolls"]) == 1
    attacker = actor()
    attacker["sheet"]["edition"] = "2024"
    assert sunlight_disadvantage(attacker["sheet"], None, actor_id="observer") is None


@pytest.mark.parametrize("group", [False, True])
def test_all_observers_are_validated_before_any_group_or_contest_roll(group):
    first, second = actor("first", False), actor("observer")
    rng = Random(2)
    before = rng.getstate()
    with pytest.raises(NeedsRulingError):
        if group:
            resolve_actor_group_check([first, second], ability="perception", dc=10, rng=rng)
        else:
            resolve_actor_contest(
                first, second, source_ability="stealth", target_ability="perception", rng=rng
            )
    assert rng.getstate() == before


@pytest.mark.parametrize("source", ["unseen_attacker", "reviewed_pack_tactics"])
def test_scene_and_reviewed_extension_advantage_cancel_sunlight(source):
    ctx = {"sunlight": context(False, True)}
    mechanics = []
    if source == "unseen_attacker":
        ctx["target_can_see_attacker"] = False
    else:
        # Source review owns Pack Tactics eligibility. Core combines its result.
        mechanics = [
            {
                "id": "dnd5e.extension.pack_tactics",
                "event": "attack.preflight",
                "operations": [{"op": "advantage.add"}],
                "citations": [{"source": "local:reviewed-pack-tactics"}],
            }
        ]
    plan = preflight_attack(
        actor(),
        actor("subject", False),
        action={"context": ctx},
        require_attack_action=False,
        rules=resolution_context(
            {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": mechanics}
        ),
    )
    assert plan["advantage"] and plan["disadvantage"]
    assert len(roll_attack_action(plan=plan, rng=Random(2))["rolls"]) == 1
