from types import SimpleNamespace

import pytest
from sagasmith_dnd import madness
from sagasmith_dnd.character_schema import add_effect, default_character_sheet
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


def test_confusion_selected_target_and_nearest_madness_force_exact_attack_targets():
    encounter = {
        "turn_index": 0,
        "combatants": [
            {
                "actor_id": "actor",
                "turn_flags": {
                    "madness_confusion": {
                        "outcome": "attack_random_creature_in_reach",
                        "random_target_actor_id": "near-a",
                    },
                },
            }
        ],
    }
    service = object.__new__(CombatService)
    service.require_madness_attack_target(
        encounter, "actor", "near-a", attack_mode="melee"
    )
    with pytest.raises(CombatEngineError, match="engine-selected random"):
        service.require_madness_attack_target(encounter, "actor", "near-b")
    with pytest.raises(CombatEngineError, match="requires a melee attack"):
        service.require_madness_attack_target(
            encounter, "actor", "near-a", attack_mode="ranged"
        )
    nearest_only = {
        "positioning_mode": "grid",
        "battle_map": {"width_cells": 5, "height_cells": 1},
        "combatants": [
            {
                "actor_id": "actor",
                "position": {"x": 3, "y": 0},
                "turn_flags": {"madness_nearest_attack": {"effect_ids": ["madness"]}},
            }
        ]
        + [
            {"actor_id": "near", "position": {"x": 4, "y": 0}},
            {"actor_id": "old-nearest", "position": {"x": 0, "y": 0}},
        ],
    }
    actor_sheet = default_character_sheet()
    effect = madness.resolve_madness("short_term", 55, duration_die=1)["runtime_effect"]
    effect["id"] = "madness"
    actor_sheet, _ = add_effect(actor_sheet, effect)
    service.characters = SimpleNamespace(
        get=lambda actor_id: SimpleNamespace(sheet=actor_sheet)
        if actor_id == "actor"
        else SimpleNamespace(sheet=default_character_sheet())
        if actor_id in {"near", "old-nearest"}
        else None
    )
    with pytest.raises(CombatEngineError, match="nearest creature"):
        service.require_madness_attack_target(nearest_only, "actor", "old-nearest")
    service.require_madness_attack_target(nearest_only, "actor", "near")


def test_nearest_madness_turn_requires_attack_action():
    encounter = {
        "turn_index": 0,
        "combatants": [
            {
                "actor_id": "actor",
                "turn_flags": {"madness_nearest_attack": {"nearest_actor_ids": ["near"]}},
            }
        ],
    }
    CombatService.require_mounted_action(encounter, "actor", "move")
    CombatService.require_mounted_action(encounter, "actor", "attack")
    with pytest.raises(CombatEngineError, match="requires an attack"):
        CombatService.require_mounted_action(encounter, "actor", "cast")


def test_confusion_madness_prohibits_reactions():
    service = object.__new__(CombatService)
    service.characters = SimpleNamespace(
        get=lambda actor_id: SimpleNamespace(
            sheet={
                "effects": [
                    {
                        "id": "confusion-1",
                        "kind": "madness_confusion",
                        "source": "bundled:srd2014/08_Gamemastering/Madness.md",
                        "active": True,
                        "metadata": {"madness_confusion": {"source_effect_id": "parent"}},
                    }
                ]
            }
        )
    )
    with pytest.raises(CombatEngineError, match="prohibits reactions"):
        service.require_madness_reaction("actor")


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
