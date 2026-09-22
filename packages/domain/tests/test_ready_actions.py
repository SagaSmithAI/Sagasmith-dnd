from copy import deepcopy

import pytest
from test_movement_continuations import _encounter

from sagasmith_dnd.combat_engine import (
    CombatEngineError,
    end_turn,
    resolve_common_action,
    resolve_readied_action_window,
    trigger_readied_action,
)
from sagasmith_dnd.ready_actions import execute_non_attack


def _arm(response):
    return resolve_common_action(
        _encounter(),
        actor_id_value="mover",
        action="ready",
        trigger="the bell rings",
        payload=response,
    )


def _trigger(armed):
    return trigger_readied_action(
        armed,
        readied_id=armed["readied"][0]["id"],
        event="the bell rings",
    )


def test_decline_rearms_the_same_response_without_spending_reaction():
    armed = _arm({"action": "dash"})
    triggered = _trigger(end_turn(armed))
    before = deepcopy(triggered)
    declined, ready = resolve_readied_action_window(
        triggered,
        actor_id_value="mover",
        choice_id=triggered["pending"][0]["id"],
        release=False,
    )
    assert triggered == before
    assert ready["status"] == declined["readied"][0]["status"] == "armed"
    again = _trigger(declined)
    released, original = execute_non_attack(again, "mover", again["pending"][0]["id"])
    mover = released["combatants"][0]
    assert original["payload"] == {"action": "dash"}
    assert mover["turn_budget"]["reaction"] == 0
    assert mover["turn_budget"]["movement"] == 60
    assert not released["readied"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"action": "attack"},
        {"action": "move", "distance": 300},
        {"action": "dash", "new_target": "other"},
    ],
)
def test_unbounded_ready_payload_cannot_spend_an_action(payload):
    encounter = _encounter()
    original = deepcopy(encounter)
    with pytest.raises(CombatEngineError):
        resolve_common_action(
            encounter,
            actor_id_value="mover",
            action="ready",
            trigger="event",
            payload=payload,
        )
    assert encounter == original


@pytest.mark.parametrize("kind", ["expired", "no_reaction", "incapacitated"])
def test_ready_expiry_and_lost_reaction_do_not_execute(kind):
    armed = end_turn(_arm({"action": "dash"}))
    if kind == "expired":
        assert not end_turn(armed)["readied"]
        return
    triggered = _trigger(armed)
    mover = triggered["combatants"][0]
    if kind == "no_reaction":
        mover["turn_budget"]["reaction"] = 0
    else:
        mover["conditions"] = ["incapacitated"]
    original = deepcopy(triggered)
    with pytest.raises(CombatEngineError):
        execute_non_attack(triggered, "mover", triggered["pending"][0]["id"])
    assert triggered == original


def test_readied_disengage_expires_at_end_of_the_triggering_turn():
    triggered = _trigger(end_turn(_arm({"action": "disengage"})))
    released, _ = execute_non_attack(triggered, "mover", triggered["pending"][0]["id"])
    assert released["combatants"][0]["turn_flags"]["disengaged"]
    advanced = end_turn(released)
    assert not advanced["combatants"][0].get("turn_flags", {}).get("disengaged")
