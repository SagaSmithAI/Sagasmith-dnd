import pytest

from sagasmith_dnd.character_schema import (
    default_character_sheet,
    derive_character_sheet,
    srd2014_rogue_stroke_of_luck_feature,
)
from sagasmith_dnd.combat_engine import (
    CombatEngineError,
    apply_srd2014_stroke_of_luck_to_check,
    blindsense_detects_location,
    can_see,
    preflight_attack,
    resolve_actor_check,
)
from sagasmith_dnd.core_content import _known_feature_structure
from sagasmith_dnd.core_rule_pack import get_core_rule_pack
from sagasmith_dnd.lifecycle import apply_rest
from sagasmith_dnd.rule_engine import ResolutionContext


class _SequenceRng:
    def __init__(self, *values):
        self.values = list(values)

    def randint(self, _minimum, _maximum):
        return self.values.pop(0)


def _rogue(level, feature_name, feature_id, mechanic_id):
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["progression"]["level"] = level
    sheet["progression"]["classes"] = [{
        "name": "Rogue", "level": level, "hit_die": 8,
    }]
    sheet["content"]["features"] = [{
        "id": feature_id,
        "name": feature_name,
        "source_key": "Rogue",
        "mechanic_refs": [mechanic_id],
    }]
    return sheet


def _rules():
    return ResolutionContext(
        "rogue-feature-test", get_core_rule_pack("2014"), (), {}, {},
    )


def test_reliable_talent_treats_proficient_low_roll_as_ten():
    sheet = _rogue(
        11,
        "Reliable Talent",
        "dnd5e.content.srd2014.feature.rogue-reliable-talent",
        "dnd5e.core.check.reliable_talent",
    )
    sheet["skills"]["stealth"]["proficiency"] = "proficient"
    actor = {"id": "rogue", "sheet": sheet, "derived": derive_character_sheet(sheet)}
    result = resolve_actor_check(
        actor,
        kind="check",
        ability="stealth",
        dc=13,
        rules=_rules(),
        rng=_SequenceRng(4),
    )

    assert result["natural"] == 10
    assert result["raw_natural"] == 4
    assert result["total"] == 14
    assert result["success"] is True
    assert result["reliable_talent_applied"] is True
    assert "dnd5e.core.check.reliable_talent" in {
        item["mechanic_id"] for item in result["rule_receipts"]
    }

    multiclass = {
        **sheet,
        "progression": {
            **sheet["progression"],
            "level": 11,
            "classes": [
                {"name": "Rogue", "level": 10, "hit_die": 8},
                {"name": "Fighter", "level": 1, "hit_die": 10},
            ],
        },
    }
    multiclass_actor = {
        "id": "multiclass-rogue",
        "sheet": multiclass,
        "derived": derive_character_sheet(multiclass),
    }
    below_rogue_threshold = resolve_actor_check(
        multiclass_actor,
        kind="check",
        ability="stealth",
        dc=13,
        rules=_rules(),
        rng=_SequenceRng(4),
    )
    assert below_rogue_threshold["natural"] == 4
    assert below_rogue_threshold.get("reliable_talent_applied", False) is False


def test_slippery_mind_adds_wisdom_save_proficiency():
    sheet = _rogue(
        15,
        "Slippery Mind",
        "dnd5e.content.srd2014.feature.rogue-slippery-mind",
        "dnd5e.core.save.slippery_mind",
    )
    actor = {"id": "rogue", "sheet": sheet, "derived": derive_character_sheet(sheet)}
    assert actor["derived"]["saving_throws"]["wisdom"] == 5
    result = resolve_actor_check(
        actor,
        kind="save",
        ability="wisdom",
        dc=10,
        rules=_rules(),
        rng=_SequenceRng(5),
    )

    assert result["total"] == 10
    assert result["success"] is True
    assert "dnd5e.core.save.slippery_mind" in {
        item["mechanic_id"] for item in result["rule_receipts"]
    }

    multiclass = _rogue(
        14,
        "Slippery Mind",
        "dnd5e.content.srd2014.feature.rogue-slippery-mind",
        "dnd5e.core.save.slippery_mind",
    )
    multiclass["progression"].update(
        {
            "level": 15,
            "classes": [
                {"name": "Rogue", "level": 14, "hit_die": 8},
                {"name": "Fighter", "level": 1, "hit_die": 10},
            ],
        }
    )
    assert derive_character_sheet(multiclass)["saving_throws"]["wisdom"] == 0


def test_elusive_removes_attack_advantage_unless_incapacitated():
    attacker_sheet = default_character_sheet()
    attacker = {
        "id": "attacker",
        "sheet": attacker_sheet,
        "derived": derive_character_sheet(attacker_sheet),
        "hidden": True,
    }
    target_sheet = _rogue(
        18,
        "Elusive",
        "dnd5e.content.srd2014.feature.rogue-elusive",
        "dnd5e.core.attack.elusive",
    )
    target = {
        "id": "elusive-rogue",
        "sheet": target_sheet,
        "derived": derive_character_sheet(target_sheet),
    }
    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "unarmed-strike"},
        rules=_rules(),
    )

    assert plan["advantage"] is False
    assert "dnd5e.core.attack.elusive" in {
        item["mechanic_id"] for item in plan["rule_receipts"]
    }

    target["conditions"] = ["incapacitated"]
    incapacitated_plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "unarmed-strike", "context": {"advantage": True}},
        rules=_rules(),
    )
    assert incapacitated_plan["advantage"] is True

    multiclass_target_sheet = _rogue(
        17,
        "Elusive",
        "dnd5e.content.srd2014.feature.rogue-elusive",
        "dnd5e.core.attack.elusive",
    )
    multiclass_target_sheet["progression"].update(
        {
            "level": 18,
            "classes": [
                {"name": "Rogue", "level": 17, "hit_die": 8},
                {"name": "Fighter", "level": 1, "hit_die": 10},
            ],
        }
    )
    multiclass_target = {
        "id": "multiclass-target",
        "sheet": multiclass_target_sheet,
        "derived": derive_character_sheet(multiclass_target_sheet),
    }
    still_advantaged = preflight_attack(
        attacker,
        multiclass_target,
        action={"weapon_id": "unarmed-strike", "context": {"advantage": True}},
        rules=_rules(),
    )
    assert still_advantaged["advantage"] is True


def test_blindsense_detects_location_without_granting_sight():
    viewer_sheet = _rogue(
        14,
        "Blindsense",
        "dnd5e.content.srd2014.feature.rogue-blindsense",
        "dnd5e.core.sense.blindsense",
    )
    viewer = {
        "id": "rogue",
        "sheet": viewer_sheet,
        "derived": derive_character_sheet(viewer_sheet),
        "position": {"x": 0, "y": 0},
    }
    subject_sheet = default_character_sheet()
    subject = {
        "id": "hidden-target",
        "sheet": subject_sheet,
        "derived": derive_character_sheet(subject_sheet),
        "position": {"x": 2, "y": 0},
        "hidden": True,
    }
    encounter = {"ruleset": "2014", "positioning_mode": "grid"}

    assert blindsense_detects_location(viewer, subject, encounter) is True
    assert can_see(viewer, subject, encounter) is False
    subject["position"] = {"x": 1, "y": 0}
    attack = preflight_attack(
        viewer,
        subject,
        action={"weapon_id": "unarmed-strike"},
        encounter=encounter,
        allow_out_of_turn=True,
        require_attack_action=False,
        rules=_rules(),
    )
    assert attack["blindsense_detects_target_location"] is True
    assert attack["disadvantage"] is True
    assert "dnd5e.core.sense.blindsense" in {
        item["mechanic_id"] for item in attack["rule_receipts"]
    }

    subject["position"] = {"x": 3, "y": 0}
    assert blindsense_detects_location(viewer, subject, encounter) is False

    agent_encounter = {"ruleset": "2014", "positioning_mode": "agent"}
    with pytest.raises(CombatEngineError, match="Blindsense needs sourced"):
        blindsense_detects_location(viewer, subject, agent_encounter)
    subject["position"] = None
    facts = {"attacker_can_hear_target": True, "target_within_10_ft": True}
    assert blindsense_detects_location(
        viewer, subject, agent_encounter, spatial_facts=facts
    ) is True
    assert blindsense_detects_location(
        viewer,
        subject,
        agent_encounter,
        spatial_facts={**facts, "attacker_can_hear_target": False},
    ) is False

    multiclass_sheet = _rogue(
        13,
        "Blindsense",
        "dnd5e.content.srd2014.feature.rogue-blindsense",
        "dnd5e.core.sense.blindsense",
    )
    multiclass_sheet["progression"].update(
        {
            "level": 14,
            "classes": [
                {"name": "Rogue", "level": 13, "hit_die": 8},
                {"name": "Fighter", "level": 1, "hit_die": 10},
            ],
        }
    )
    multiclass_viewer = {
        "id": "multiclass-blindsense",
        "sheet": multiclass_sheet,
        "derived": derive_character_sheet(multiclass_sheet),
        "position": {"x": 0, "y": 0},
    }
    subject["position"] = {"x": 1, "y": 0}
    assert blindsense_detects_location(
        multiclass_viewer, subject, {"ruleset": "2014", "positioning_mode": "grid"}
    ) is False


def test_stroke_of_luck_applies_to_a_failed_check_and_has_one_short_rest_use():
    sheet = _rogue(
        20,
        "Stroke of Luck",
        "dnd5e.content.srd2014.feature.rogue-stroke-of-luck",
        "dnd5e.core.rogue.stroke_of_luck",
    )
    feature = sheet["content"]["features"][0]
    feature.update(
        _known_feature_structure("Rogue", "Stroke of Luck", "once per short or long rest")
    )
    assert srd2014_rogue_stroke_of_luck_feature(sheet)["uses"] == {
        "max": 1,
        "value": 1,
        "recovers_on": "short_rest",
        "unlimited": False,
    }
    failed = {
        "kind": "ability",
        "natural": 7,
        "dc": 15,
        "total": 12,
        "success": False,
    }
    applied = apply_srd2014_stroke_of_luck_to_check(failed)

    assert applied["natural"] == 20
    assert applied["total"] == 25
    assert applied["success"] is True
    assert applied["stroke_of_luck_applied"] is True
    with pytest.raises(CombatEngineError, match="failed ability check"):
        apply_srd2014_stroke_of_luck_to_check({**failed, "success": True})


def test_stroke_of_luck_recovers_on_short_and_long_rest():
    sheet = _rogue(
        20,
        "Stroke of Luck",
        "dnd5e.content.srd2014.feature.rogue-stroke-of-luck",
        "dnd5e.core.rogue.stroke_of_luck",
    )
    feature = sheet["content"]["features"][0]
    feature.update(
        _known_feature_structure("Rogue", "Stroke of Luck", "once per short or long rest")
    )
    feature["uses"]["value"] = 0

    short = apply_rest(sheet, rest_type="short_rest")
    long = apply_rest(sheet, rest_type="long_rest")

    assert short["sheet"]["content"]["features"][0]["uses"]["value"] == 1
    assert long["sheet"]["content"]["features"][0]["uses"]["value"] == 1
