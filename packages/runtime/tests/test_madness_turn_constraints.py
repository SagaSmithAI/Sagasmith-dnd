import pytest
from sagasmith_dnd.combat_engine import CombatEngineError
from sagasmith_dnd_runtime.services.combat import CombatService


@pytest.mark.parametrize("action", ["move", "attack", "cast", "dodge"])
def test_confusion_no_action_or_movement_blocks_turn_actions(action):
    encounter = {
        "turn_index": 0,
        "combatants": [
            {
                "actor_id": "actor",
                "turn_flags": {
                    "madness_confusion": {"outcome": "no_action_or_movement"}
                },
            }
        ],
    }

    with pytest.raises(CombatEngineError, match="prohibits actions and movement"):
        CombatService.require_mounted_action(encounter, "actor", action)


def test_confusion_random_movement_and_attack_results_constrain_actions():
    random_move = {
        "turn_index": 0,
        "combatants": [
            {"actor_id": "actor", "turn_flags": {"madness_confusion": {
                "outcome": "move_random_direction"
            }}}
        ],
    }
    CombatService.require_mounted_action(random_move, "actor", "move")
    with pytest.raises(CombatEngineError, match="prohibits actions"):
        CombatService.require_mounted_action(random_move, "actor", "attack")

    forced_attack = {
        "turn_index": 0,
        "combatants": [
            {"actor_id": "actor", "turn_flags": {"madness_confusion": {
                "outcome": "attack_random_creature_in_reach"
            }}}
        ],
    }
    CombatService.require_mounted_action(forced_attack, "actor", "move")
    CombatService.require_mounted_action(forced_attack, "actor", "attack")
    with pytest.raises(CombatEngineError, match="requires a random melee attack"):
        CombatService.require_mounted_action(forced_attack, "actor", "dodge")


def test_confusion_turn_flag_does_not_block_an_actor_during_another_turn():
    encounter = {
        "turn_index": 1,
        "combatants": [
            {"actor_id": "actor", "turn_flags": {"madness_confusion": {
                "outcome": "no_action_or_movement"
            }}},
            {"actor_id": "other"},
        ],
    }

    CombatService.require_mounted_action(encounter, "actor", "attack")
