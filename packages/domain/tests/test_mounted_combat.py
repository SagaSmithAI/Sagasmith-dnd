from __future__ import annotations

import pytest
from test_combat_engine import _actor, _grid_encounter

from sagasmith_dnd.combat_engine import available_actions, current_combatant, end_turn
from sagasmith_dnd.mounted_combat import (
    dismount_2014,
    forced_dismount_2014,
    mount_2014,
)


def _facts(*, trained=True, capacity=1):
    return {
        "decision_id": "dm-mount-1",
        "reason": "The mount accepts the rider and has suitable anatomy.",
        "source_ref": "scene:stable/horse-1",
        "source_excerpt": "The riding horse is saddled and trained.",
        "willing": True,
        "suitable_anatomy": True,
        "trained": trained,
        "capacity": capacity,
    }


def _pair(*, trained=True):
    rider, mount = _actor("rider"), _actor("mount")
    rider.update(initiative=20, position={"x": 0, "y": 0}, size="medium")
    mount.update(initiative=10, position={"x": 1, "y": 0}, size="large")
    mount["sheet"]["traits"]["size"] = "large"
    encounter = _grid_encounter([rider, mount])
    return encounter, rider, mount, trained


def test_controlled_mount_uses_own_initiative_slot_and_keeps_budget_separate():
    encounter, rider, mount, trained = _pair()
    mounted = mount_2014(
        encounter,
        rider_actor_id="rider",
        mount_actor_id="mount",
        facts=_facts(trained=trained),
    )
    current = current_combatant(mounted)
    horse = next(item for item in mounted["combatants"] if item["actor_id"] == "mount")
    mounted_rider = next(item for item in mounted["combatants"] if item["actor_id"] == "rider")
    assert current["actor_id"] == "rider"
    assert horse["initiative"] == current["initiative"] == 20
    assert horse["mounted_turn"]["mode"] == "controlled"
    assert horse["mounted_turn"]["owner_actor_id"] == "rider"
    assert horse["turn_budget"]["movement_spent"] == 0
    assert mounted_rider["turn_budget"]["movement_spent"] == 15
    assert mounted_rider["position"] == horse["position"]
    assert "move" not in available_actions(mounted, "rider")
    assert "dismount" not in available_actions(mounted, "rider")

    mount_turn = end_turn(mounted, actor_id_value="rider")
    assert current_combatant(mount_turn)["actor_id"] == "mount"
    assert set(available_actions(mount_turn, "mount")) == {
        "move", "dash", "disengage", "dodge",
    }
    rider_turn = end_turn(mount_turn, actor_id_value="mount")
    assert current_combatant(rider_turn)["actor_id"] == "rider"
    assert "dismount" in available_actions(rider_turn, "rider")
    dismounted = dismount_2014(
        rider_turn,
        rider_actor_id="rider",
        destination={"x": 0, "y": 0},
    )
    rider_after = next(item for item in dismounted["combatants"] if item["actor_id"] == "rider")
    mount_after = next(item for item in dismounted["combatants"] if item["actor_id"] == "mount")
    assert rider_after["position"] == {"x": 0, "y": 0}
    assert rider_after["turn_budget"]["movement_spent"] == 15
    assert mount_after["initiative"] == 10
    assert not dismounted["mount_relations"][0]["active"]


def test_independent_mount_keeps_its_initiative_and_agent_needs_spatial_facts():
    encounter, rider, mount, _ = _pair(trained=False)
    mounted = mount_2014(
        encounter,
        rider_actor_id="rider",
        mount_actor_id="mount",
        facts=_facts(trained=False),
    )
    horse = next(item for item in mounted["combatants"] if item["actor_id"] == "mount")
    assert horse["initiative"] == 10
    assert "mounted_turn" not in horse
    assert mounted["mount_relations"][0]["mode"] == "independent"

    agent = dict(encounter, positioning_mode="agent")
    agent["combatants"][0]["position"] = None
    agent["combatants"][1]["position"] = None
    with pytest.raises(Exception, match="reviewed consent"):
        mount_2014(
            agent,
            rider_actor_id="rider",
            mount_actor_id="mount",
            facts=_facts(trained=False),
        )


def test_dismount_cannot_follow_mounting_in_the_same_turn():
    encounter, rider, mount, trained = _pair()
    mounted = mount_2014(
        encounter,
        rider_actor_id="rider",
        mount_actor_id="mount",
        facts=_facts(trained=trained),
    )
    with pytest.raises(Exception, match="already used this turn"):
        dismount_2014(
            mounted,
            rider_actor_id="rider",
            destination={"x": 0, "y": 0},
        )


def test_forced_fall_ends_relation_without_movement_payment_or_turn_requirement():
    encounter, _, _, _ = _pair()
    mounted = mount_2014(
        encounter,
        rider_actor_id="rider",
        mount_actor_id="mount",
        facts=_facts(),
    )
    mounted["turn_index"] = next(
        index for index, actor in enumerate(mounted["combatants"])
        if actor["actor_id"] == "mount"
    )

    fallen = forced_dismount_2014(
        mounted,
        rider_actor_id="rider",
        reason="mount_moved_unwillingly",
        prone=True,
    )

    rider_after = next(item for item in fallen["combatants"] if item["actor_id"] == "rider")
    mount_after = next(item for item in fallen["combatants"] if item["actor_id"] == "mount")
    assert "prone" in rider_after["conditions"]
    assert fallen["mount_relations"][0]["active"] is False
    assert mount_after["initiative"] == 10
    assert rider_after["turn_budget"]["movement_spent"] == 15
    assert fallen["log"][-1]["reason"] == "mount_moved_unwillingly"


def test_agent_forced_dismount_records_relative_landing_without_coordinates():
    encounter, _, _, _ = _pair()
    agent = dict(encounter, positioning_mode="agent")
    agent["combatants"] = [dict(item, position=None) for item in encounter["combatants"]]
    mounted = mount_2014(
        agent,
        rider_actor_id="rider",
        mount_actor_id="mount",
        facts={**_facts(), "within_5_feet": True},
    )
    fallen = forced_dismount_2014(
        mounted,
        rider_actor_id="rider",
        reason="mount_moved_unwillingly",
        prone=True,
    )
    rider_after = next(item for item in fallen["combatants"] if item["actor_id"] == "rider")
    assert rider_after["position"] is None
    assert fallen["mount_relations"][0]["active"] is False
    assert fallen["log"][-1]["agent_landing"] == {
        "kind": "within_range_of_actor",
        "actor_id": "mount",
        "maximum_distance_ft": 5,
    }

