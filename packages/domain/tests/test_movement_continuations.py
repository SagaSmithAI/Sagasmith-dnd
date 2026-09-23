from copy import deepcopy

import pytest
from test_combat_engine import _actor, _grid_encounter

from sagasmith_dnd.combat_engine import (
    add_choice_window,
    current_combatant,
    resolve_choice_window,
    spend_movement,
)
from sagasmith_dnd.movement_continuations import resume_pending_movement


def _encounter(*, second=False):
    mover, threat = _actor("mover"), _actor("threat")
    mover.update(initiative=30, position={"x": 0, "y": 0}, disposition="friendly")
    threat.update(initiative=20, position={"x": 1, "y": 1}, disposition="hostile")
    actors = [mover, threat]
    if second:
        later = _actor("later")
        later.update(initiative=10, position={"x": 4, "y": 1}, disposition="hostile")
        actors.append(later)
    return _grid_encounter(actors)


def _decline(encounter):
    window = encounter["pending"][0]
    return resolve_choice_window(
        encounter,
        choice_id=window["id"],
        actor_id_value=window["actor_id"],
        selection={"id": "decline"},
    )


def test_movement_pauses_resumes_and_charges_only_committed_prefixes():
    encounter = _encounter(second=True)
    before = deepcopy(encounter)
    paused = spend_movement(encounter, "mover", 30, destination={"x": 6, "y": 0})
    assert encounter == before
    assert current_combatant(paused)["position"] == {"x": 2, "y": 0}
    assert current_combatant(paused)["turn_budget"]["movement_spent"] == 10
    assert [w["actor_id"] for w in paused["pending"]] == ["threat"]
    later = resume_pending_movement(_decline(paused))
    assert current_combatant(later)["position"] == {"x": 5, "y": 0}
    assert current_combatant(later)["turn_budget"]["movement_spent"] == 25
    assert [w["actor_id"] for w in later["pending"]] == ["later"]
    finished = resume_pending_movement(_decline(later))
    assert current_combatant(finished)["position"] == {"x": 6, "y": 0}
    assert current_combatant(finished)["turn_budget"]["movement_spent"] == 30
    assert "movement_continuation" not in finished
    assert resume_pending_movement(finished) == finished


def test_jump_landing_check_waits_until_every_opportunity_reaction_finishes():
    paused = spend_movement(
        _encounter(second=True), "mover", 30, destination={"x": 6, "y": 0}
    )
    paused["movement_continuation"]["deferred_landing_check"] = {
        "actor_id": "mover",
        "kind": "check",
        "ability": "acrobatics",
        "dc": 10,
        "ruleset": "2014",
    }

    first = resume_pending_movement(_decline(paused))
    assert current_combatant(first)["conditions"] == []
    assert first["movement_continuation"]["deferred_landing_check"] == {
        "actor_id": "mover",
        "kind": "check",
        "ability": "acrobatics",
        "dc": 10,
        "ruleset": "2014",
    }

    finished = resume_pending_movement(_decline(first))
    assert "movement_continuation" not in finished
    assert current_combatant(finished)["conditions"] == []
    assert finished["jump_landing_check_due"] == {
        "actor_id": "mover",
        "kind": "check",
        "ability": "acrobatics",
        "dc": 10,
        "ruleset": "2014",
    }


@pytest.mark.parametrize(
    "condition", ["dead", "unconscious", "stunned", "paralyzed", "restrained", "grappled", "prone"]
)
def test_disabling_reaction_stops_at_boundary_and_never_offers_later_threats(condition):
    paused = spend_movement(_encounter(second=True), "mover", 30, destination={"x": 6, "y": 0})
    resolved = _decline(paused)
    current_combatant(resolved)["conditions"] = [condition]
    stopped = resume_pending_movement(resolved)
    assert current_combatant(stopped)["position"] == {"x": 2, "y": 0}
    assert current_combatant(stopped)["turn_budget"]["movement_spent"] == 10
    assert not stopped["pending"]
    assert "movement_continuation" not in stopped
    assert stopped["log"][-1]["status"] == "cancelled"


def test_nested_reaction_choices_must_finish_before_resuming():
    paused = spend_movement(_encounter(), "mover", 15, destination={"x": 3, "y": 0})
    resolved = _decline(paused)
    resolved = add_choice_window(
        resolved,
        kind="concentration",
        actor_id_value="mover",
        event="concentration",
        candidates=[{"id": "save"}],
    )
    assert resume_pending_movement(resolved) == resolved
    current_combatant(resolved)["hit_points"] = 0
    current_combatant(resolved)["conditions"] = ["unconscious"]
    assert resume_pending_movement(resolved) == resolved


@pytest.mark.parametrize("change", ["position", "speed", "hp", "reaction", "blocked"])
def test_continuation_revalidates_authoritative_state(change):
    paused = spend_movement(_encounter(), "mover", 15, destination={"x": 3, "y": 0})
    if change == "reaction":
        next(a for a in paused["combatants"] if a["actor_id"] == "threat")["turn_budget"][
            "reaction"
        ] = 0
        finished = resume_pending_movement(paused)
        assert current_combatant(finished)["position"] == {"x": 3, "y": 0}
    else:
        resolved = _decline(paused)
        actor = current_combatant(resolved)
        if change == "position":
            actor["position"] = {"x": 2, "y": 2}
        elif change == "speed":
            actor["speed_multiplier"] = 0
        elif change == "blocked":
            resolved["battle_map"]["blocked_cells"] = ["3,0"]
        else:
            actor["hit_points"] = 0
        finished = resume_pending_movement(resolved)
        assert current_combatant(finished)["position"] == actor["position"]
        assert finished["log"][-1]["status"] == "cancelled"
    assert "movement_continuation" not in finished


def test_difficult_terrain_is_charged_on_the_correct_side_of_a_reaction():
    encounter = _encounter()
    encounter["battle_map"]["difficult_cells"] = ["1,0", "3,0"]
    paused = spend_movement(encounter, "mover", 20, path=[{"x": x, "y": 0} for x in range(5)])
    assert current_combatant(paused)["turn_budget"]["movement_spent"] == 15
    finished = resume_pending_movement(_decline(paused))
    assert current_combatant(finished)["turn_budget"]["movement_spent"] == 30
    assert current_combatant(finished)["position"] == {"x": 4, "y": 0}


def test_three_reach_bands_are_equivalent_for_whole_and_segmented_routes():
    mover, threat = _actor("mover"), _actor("threat")
    mover.update(initiative=20, position={"x": 1, "y": 0}, disposition="friendly")
    threat.update(initiative=10, position={"x": 0, "y": 0}, disposition="hostile")
    threat["derived"]["inventory"]["weapon_attacks"] = [
        {"item_id": name, "attack_type": "melee", "reach_ft": reach}
        for name, reach in (("short", 5), ("long", 10), ("longer", 15))
    ]

    def travel(destinations):
        encounter = _grid_encounter([mover, threat])
        trace = []
        for x in destinations:
            position = current_combatant(encounter)["position"]["x"]
            encounter = spend_movement(
                encounter, "mover", (x - position) * 5, destination={"x": x, "y": 0}
            )
            while encounter.get("pending"):
                window = encounter["pending"][0]
                trace.append((window["target_position"], window["opportunity_attack_weapon_ids"]))
                encounter = resume_pending_movement(_decline(encounter))
        assert current_combatant(encounter)["turn_budget"]["movement_spent"] == 20
        return trace

    assert (
        travel([5])
        == travel([2, 3, 4, 5])
        == [
            ({"x": 1, "y": 0}, ["short", "unarmed-strike"]),
            ({"x": 2, "y": 0}, ["long"]),
            ({"x": 3, "y": 0}, ["longer"]),
        ]
    )
