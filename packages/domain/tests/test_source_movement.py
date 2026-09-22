from copy import deepcopy

import pytest
from test_movement_continuations import _decline, _encounter

from sagasmith_dnd.combat_engine import CombatEngineError, spend_movement
from sagasmith_dnd.movement_continuations import resume_pending_movement, spend_source_movement
from sagasmith_dnd.resolution_plan import compile_resolution_plan


def _mover(encounter):
    return next(a for a in encounter["combatants"] if a["actor_id"] == "mover")


def _off_turn():
    value = _encounter(second=True)
    value["turn_index"] = 1
    _mover(value)["turn_budget"].update(main_action=1, reaction=1)
    return value


@pytest.mark.parametrize("payment", ["action", "reaction", "movement"])
def test_off_turn_movement_pays_once_and_preserves_all_reach_exits(payment):
    encounter = _off_turn()
    before = deepcopy(encounter)
    paused = spend_source_movement(
        encounter,
        "mover",
        30,
        payment=payment,
        distance_limit="speed",
        source={"id": "reviewed.source", "fingerprint": "exact"},
        destination={"x": 6, "y": 0},
    )
    assert encounter == before
    assert _mover(paused)["position"] == {"x": 2, "y": 0}
    assert _mover(paused)["source_movement"]["remaining"] == 20
    later = resume_pending_movement(_decline(paused))
    assert _mover(later)["position"] == {"x": 5, "y": 0}
    final = resume_pending_movement(_decline(later))
    mover = _mover(final)
    assert mover["position"] == {"x": 6, "y": 0}
    assert mover["turn_budget"].get("movement_spent", 0) == (30 if payment == "movement" else 0)
    for key, resource in (("main_action", "action"), ("reaction", "reaction")):
        assert mover["turn_budget"][key] == (0 if payment == resource else 1)
    assert "source_movement" not in mover
    assert len([e for e in final["log"] if e["type"] == "source_movement_payment"]) == 1
    assert final["log"][-2]["source"]["fingerprint"] == "exact"


@pytest.mark.parametrize("mode", ["forced", "teleport"])
def test_external_movement_remains_exempt_off_turn(mode):
    encounter = _off_turn()
    budget = deepcopy(_mover(encounter)["turn_budget"])
    moved = spend_movement(encounter, "mover", 30, movement_mode=mode, destination={"x": 6, "y": 0})
    assert not moved["pending"]
    assert _mover(moved)["turn_budget"] == budget


@pytest.mark.parametrize("failure", ["reaction", "incapacitated", "distance", "terrain"])
def test_source_movement_failure_is_pure_and_does_not_spend(failure):
    encounter = _off_turn()
    distance = 30
    request = {"destination": {"x": 6, "y": 0}}
    if failure == "reaction":
        _mover(encounter)["turn_budget"]["reaction"] = 0
    elif failure == "incapacitated":
        _mover(encounter)["conditions"] = ["incapacitated"]
    elif failure == "distance":
        distance, request["destination"] = 35, {"x": 7, "y": 0}
    else:
        encounter["battle_map"]["difficult_cells"] = ["1,0"]
        request["path"] = [{"x": x, "y": 0} for x in range(7)]
    before = deepcopy(encounter)
    with pytest.raises(CombatEngineError):
        spend_source_movement(
            encounter,
            "mover",
            distance,
            payment="reaction",
            distance_limit="speed",
            source={"id": "reviewed.source"},
            **request,
        )
    assert encounter == before


def test_source_movement_slows_or_dies_without_refunding_reaction():
    for change in ("speed", "death"):
        paused = spend_source_movement(
            _off_turn(),
            "mover",
            30,
            payment="reaction",
            distance_limit="speed",
            source={"id": "reviewed.source"},
            destination={"x": 6, "y": 0},
        )
        resolved = _decline(paused)
        if change == "speed":
            _mover(resolved)["speed_multiplier"] = 0.5
        else:
            _mover(resolved)["hit_points"] = 0
        stopped = resume_pending_movement(resolved)
        assert _mover(stopped)["position"] == {"x": 2, "y": 0}
        assert _mover(stopped)["turn_budget"]["reaction"] == 0
        assert "source_movement" not in _mover(stopped)
        assert stopped["log"][-1]["status"] == "cancelled"


def test_compelled_self_powered_movement_still_provokes_and_has_its_own_allowance():
    encounter = _off_turn()
    mover = _mover(encounter)
    mover["turn_budget"].update(movement_spent=30, movement=0)
    mover["conditions"] = ["frightened"]
    mover["condition_sources"] = {"frightened": ["later"]}
    with pytest.raises(CombatEngineError, match="frightened"):
        spend_source_movement(
            encounter,
            "mover",
            15,
            payment="reaction",
            distance_limit="speed",
            source={"id": "reviewed.source"},
            destination={"x": 3, "y": 0},
        )
    paused = spend_source_movement(
        encounter,
        "mover",
        15,
        payment="reaction",
        distance_limit="speed",
        voluntary=False,
        source={"id": "reviewed.source"},
        destination={"x": 3, "y": 0},
    )
    assert paused["pending"][0]["trigger"] == "opportunity_attack"
    final = resume_pending_movement(_decline(paused))
    assert _mover(final)["position"] == {"x": 3, "y": 0}
    assert _mover(final)["turn_budget"]["movement_spent"] == 30


@pytest.mark.parametrize("field", ["payment", "distance_limit", "voluntary"])
def test_movement_authority_cannot_be_bound_by_the_caller(field):
    plan = {
        "schema_version": 2,
        "id": "test.movement",
        "source_card_id": "move",
        "source_card_kind": "activity",
        "trigger": "action",
        "slots": {"authority": {"kind": "text", "owner": "agent", "description": "unsafe"}},
        "steps": [
            {
                "id": "move",
                "op": "movement.move",
                "args": {
                    "actor_id": "mover",
                    "payment": "reaction",
                    "distance_limit": "speed",
                    field: {"$slot": "authority"},
                },
            }
        ],
        "citations": [{"source": "test", "source_excerpt": "Reaction movement."}],
    }
    with pytest.raises(ValueError, match="fixed by the source template"):
        compile_resolution_plan(plan)
