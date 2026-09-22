"""Source prerequisites and current cards govern the one Working Together roll."""

from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.combat_engine import CombatEngineError, NeedsRulingError
from sagasmith_dnd.working_together import resolve_working_together


class SequenceRng:
    def __init__(self, *rolls):
        self.rolls = list(rolls)

    def randint(self, low, high):
        value = self.rolls.pop(0)
        assert low <= value <= high
        return value


def actor(identifier, *, score=10):
    sheet = default_character_sheet()
    sheet["abilities"]["dexterity"]["score"] = score
    return {"id": identifier, "sheet": sheet}


def task(**patch):
    return {"productive": True, "requirements": {"tools": [], "skills": [], "features": []},
            **patch}


def together(actors=None, **kwargs):
    actors = actors or [actor("leader", score=14), actor("helper")]
    # Rebuild the projection after each card mutation, as Runtime does.
    actors = [{**a, "derived": derive_character_sheet(a["sheet"])} for a in actors]
    return resolve_working_together(
        actors, **{"ability": "dexterity", "dc": 15, "task": task(),
                   "rng": SequenceRng(3, 15), **kwargs},
    )


def test_highest_ability_not_highest_skill_selects_one_leader_without_mutation():
    actors = [actor("leader", score=14), actor("helper", score=12)]
    actors[1]["sheet"]["skills"]["stealth"]["proficiency"] = "expertise"
    before = deepcopy(actors)
    result = together(actors, ability="stealth")
    assert result["leader_id"] == "leader" and result["helper_ids"] == ["helper"]
    assert result["check"]["rolls"] == [3, 15] and result["check"]["total"] == 17
    assert actors == before


def test_declared_leader_and_different_ability_variant_use_their_current_card():
    actors = [actor("one", score=18), actor("two", score=8)]
    actors[1]["sheet"]["abilities"]["intelligence"]["score"] = 16
    actors[1]["sheet"]["skills"]["stealth"]["proficiency"] = "expertise"
    variant = together(actors, ability="stealth", skill_ability="intelligence")
    assert variant["leader_id"] == "two" and variant["check"]["total"] == 22
    explicit = together(actors, leader_id="two")
    assert explicit["leader_id"] == "two" and explicit["check"]["total"] == 14


def test_tied_leaders_require_a_choice_before_rng():
    with pytest.raises(NeedsRulingError, match="leader_id"):
        together([actor("one"), actor("two")], rng=SequenceRng())
    assert together([actor("one"), actor("two")], leader_id="two")["leader_id"] == "two"


@pytest.mark.parametrize("condition", ["dead", "unconscious", "incapacitated", "stunned",
                                       "petrified", "paralyzed"])
def test_incapacitated_helper_cannot_qualify_or_consume_rng(condition):
    actors = [actor("leader", score=14), actor("helper")]
    actors[1]["sheet"]["conditions"] = [condition]
    with pytest.raises(CombatEngineError, match="incapacitated"):
        together(actors, rng=SequenceRng())


@pytest.mark.parametrize("flag,condition", [("relies_on_sight", "blinded"),
                                          ("relies_on_hearing", "deafened")])
def test_helper_sensory_eligibility_needs_the_reviewed_task(flag, condition):
    actors = [actor("leader", score=14), actor("helper")]
    actors[1]["sheet"]["conditions"] = [condition]
    with pytest.raises(NeedsRulingError, match="sensory"):
        together(actors, rng=SequenceRng())
    with pytest.raises(CombatEngineError, match="alone"):
        together(actors, task=task(**{flag: True}), rng=SequenceRng())
    assert together(actors, task=task(**{flag: False}))["check"]["roll_mode"] == "advantage"


def test_tool_eligibility_expertise_and_advantage_cancellation_come_from_cards():
    actors = [actor("leader", score=14), actor("helper")]
    lock = task(requirements={"tools": ["Thieves' Tools"], "skills": [], "features": []})
    with pytest.raises(CombatEngineError, match="tool proficiency"):
        together(actors, task=lock, rng=SequenceRng())
    for item in actors:
        item["sheet"]["traits"]["proficiencies"]["tools"] = ["thieves’ tools"]
    actors[0]["sheet"]["traits"]["proficiencies"]["tool_expertise_all"] = True
    result = together(actors, task=lock, tool="Thieves' Tools")
    assert result["check"]["total"] == 21  # 15 + Dex 2 + twice PB 2
    actors[0]["sheet"]["conditions"] = ["poisoned"]
    result = together(actors, task=lock, tool="Thieves' Tools", rng=SequenceRng(10))
    assert result["check"]["roll_mode"] == "normal" and result["check"]["total"] == 16


def test_required_skills_and_features_are_card_prerequisites():
    actors = [actor("leader", score=14), actor("helper")]
    for requirements in ({"tools": [], "skills": ["arcana"], "features": []},
                         {"tools": [], "skills": [], "features": ["source.feature"]}):
        with pytest.raises(CombatEngineError, match="lacks"):
            together(actors, task=task(requirements=requirements), rng=SequenceRng())


def test_effects_can_change_highest_ability_and_roll_modifiers():
    actors = [actor("one", score=14), actor("two")]
    actors[1]["sheet"]["effects"] = [{
        "id": "source-effect", "name": "Task aid", "active": True,
        "changes": [{"path": "abilities.dexterity.score", "mode": "override", "value": 18},
                    {"path": "rolls.ability_check.bonus", "mode": "add", "value": 3}],
    }]
    result = together(actors)
    assert result["leader_id"] == "two" and result["check"]["total"] == 22


@pytest.mark.parametrize("patch", [
    {"task": task(productive=False)}, {"task": task(productive=1)},
    {"task": task(requirements={})}, {"ability": "fake"},
    {"skill_ability": "strength"}, {"ability": "stealth", "tool": "Thieves' Tools"},
    {"leader_id": "absent"}, {"dc": True},
])
def test_invalid_or_nonproductive_tasks_fail_before_rng(patch):
    with pytest.raises(CombatEngineError):
        together(**patch, rng=SequenceRng())


def test_2024_has_no_unreviewed_working_together_rule():
    actors = [actor("leader", score=14), actor("helper")]
    for item in actors:
        item["sheet"]["edition"] = "2024"
    with pytest.raises(CombatEngineError, match="2014"):
        together(actors, rng=SequenceRng())
