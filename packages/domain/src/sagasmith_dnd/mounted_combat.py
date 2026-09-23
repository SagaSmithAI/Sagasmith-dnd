"""2014 mounted-combat links with independent turn and movement budgets."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import combat_engine as engine
from .spaces import SIZES, grid_space, overlap

MOUNTED_COMBAT_RULE = "dnd5e.core.combat.mounted_2014"


def active_mount_relations(encounter: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item for item in encounter.get("mount_relations", [])
        if isinstance(item, dict) and item.get("active", True)
    ]


def riders_of(encounter: dict[str, Any], mount_actor_id: str) -> list[str]:
    return sorted({
        str(item.get("rider_actor_id") or "")
        for item in active_mount_relations(encounter)
        if str(item.get("mount_actor_id") or "") == str(mount_actor_id)
    })


def relation_for_rider(encounter: dict[str, Any], rider_actor_id: str):
    return next(
        (item for item in active_mount_relations(encounter)
         if str(item.get("rider_actor_id") or "") == str(rider_actor_id)),
        None,
    )


def relation_for_mount(encounter: dict[str, Any], mount_actor_id: str):
    return next(
        (item for item in active_mount_relations(encounter)
         if str(item.get("mount_actor_id") or "") == str(mount_actor_id)),
        None,
    )


def _combatant(encounter: dict[str, Any], actor_id: str):
    return next(
        (item for item in encounter.get("combatants", [])
         if str(item.get("actor_id") or "") == str(actor_id)),
        None,
    )


def _facts(value: Any, *, agent: bool) -> dict[str, Any]:
    required = {
        "decision_id", "reason", "source_ref", "source_excerpt", "willing",
        "suitable_anatomy", "trained", "capacity",
    } | ({"within_5_feet"} if agent else set())
    if not isinstance(value, dict) or set(value) != required:
        raise engine.NeedsRulingError(
            "mounting requires reviewed consent, anatomy, training, and rider-capacity facts",
            missing=("mounting.dm_facts",),
            ruling_kind="agent_dm_adjudication",
        )
    for name, maximum in (("decision_id", 160), ("reason", 500),
                          ("source_ref", 500), ("source_excerpt", 2000)):
        text = value.get(name)
        if not isinstance(text, str) or not text.strip() or len(text) > maximum:
            raise engine.CombatEngineError(f"mounting {name} must be bounded non-empty text")
    for name in ("willing", "suitable_anatomy", "trained"):
        if type(value.get(name)) is not bool:
            raise engine.CombatEngineError(f"mounting {name} must be boolean")
    if type(value.get("capacity")) is not int or not 1 <= value["capacity"] <= 32:
        raise engine.CombatEngineError(
            "mount rider capacity must be an authorized integer from 1 to 32"
        )
    if agent and type(value.get("within_5_feet")) is not bool:
        raise engine.CombatEngineError("Agent mounting requires a bounded within_5_feet ruling")
    return deepcopy(value)


def _half_speed_cost(rider: dict[str, Any]) -> int:
    speed = engine._effective_speed_ft(rider)
    if speed <= 0:
        raise engine.CombatEngineError("mounting and dismounting require positive rider speed")
    return speed // 2


def _charge_rider_movement(encounter: dict[str, Any], rider: dict[str, Any]) -> int:
    cost = _half_speed_cost(rider)
    if engine._remaining_movement_ft(rider) < cost:
        raise engine.CombatEngineError("mounting or dismounting requires half the rider's speed")
    budget = dict(rider.get("turn_budget") or {})
    engine._update_movement_accounting(rider, budget, spent_delta=cost)
    return cost


def _require_current_rider_turn(encounter: dict[str, Any], rider_actor_id: str):
    current = engine.current_combatant(encounter)
    if current is None or str(current.get("actor_id") or "") != str(rider_actor_id):
        raise engine.CombatEngineError("mounting and dismounting occur on the rider's turn")
    if any(item.get("status", "pending") == "pending" for item in encounter.get("pending", [])):
        raise engine.CombatEngineError("resolve pending choices before mounting or dismounting")
    rider = _combatant(encounter, rider_actor_id)
    if rider is None:
        raise engine.CombatEngineError("rider is not a combatant")
    token = engine._combat_turn_token(encounter)
    flags = dict(rider.get("turn_flags") or {})
    if flags.get("mounting_used_turn_token") == token:
        raise engine.CombatEngineError("mounting or dismounting was already used this turn")
    return rider, token


def mount_2014(
    encounter: dict[str, Any],
    *,
    rider_actor_id: str,
    mount_actor_id: str,
    facts: dict[str, Any],
) -> dict[str, Any]:
    if engine._normalize_ruleset(encounter.get("ruleset")) != "2014":
        raise engine.CombatEngineError("mounted combat contract is source-bound to 2014 rules")
    value = deepcopy(encounter)
    rider, token = _require_current_rider_turn(value, rider_actor_id)
    mount = _combatant(value, mount_actor_id)
    if mount is None or mount_actor_id == rider_actor_id:
        raise engine.CombatEngineError("mount must be a different combatant in the encounter")
    normalized_facts = _facts(facts, agent=value.get("positioning_mode") == "agent")
    if not normalized_facts["willing"] or not normalized_facts["suitable_anatomy"]:
        raise engine.CombatEngineError("mounting requires a willing creature with suitable anatomy")
    if engine._condition_set(mount.get("conditions")) & {
        "dead", "unconscious", "stunned", "paralyzed", "petrified", "incapacitated",
    }:
        raise engine.CombatEngineError("an unavailable creature cannot be mounted")
    if relation_for_rider(value, rider_actor_id):
        raise engine.CombatEngineError("rider is already mounted")
    rider_size, mount_size = rider.get("size"), mount.get("size")
    if rider_size not in SIZES or mount_size not in SIZES:
        raise engine.NeedsRulingError(
            "mounting requires authoritative creature sizes",
            missing=("rider.size", "mount.size"),
        )
    if SIZES.index(mount_size) - SIZES.index(rider_size) < 1:
        raise engine.CombatEngineError("mount must be at least one size larger than the rider")
    if value.get("positioning_mode") == "grid":
        rider_position = engine._position(rider.get("position"))
        mount_position = engine._position(mount.get("position"))
        if rider_position is None or mount_position is None:
            raise engine.NeedsRulingError("Grid mounting requires both recorded positions")
        if engine._grid_distance(rider_position, mount_position) > 5:
            raise engine.CombatEngineError("rider must be within 5 feet to mount")
    elif normalized_facts["within_5_feet"] is not True:
        raise engine.CombatEngineError("Agent ruling places the mount more than 5 feet away")
    existing_riders = riders_of(value, mount_actor_id)
    existing_relations = [
        item for item in active_mount_relations(value)
        if str(item.get("mount_actor_id") or "") == str(mount_actor_id)
    ]
    if existing_relations and any(
        int(item.get("capacity", 0)) != normalized_facts["capacity"]
        for item in existing_relations
    ):
        raise engine.NeedsRulingError("mount rider capacity ruling conflicts with an active rider")
    mode = "controlled" if normalized_facts["trained"] else "independent"
    if existing_relations and any(item.get("mode") != mode for item in existing_relations):
        raise engine.NeedsRulingError("active riders disagree about the mount's turn mode")
    if mode == "controlled" and existing_riders:
        raise engine.NeedsRulingError(
            "a controlled mount with multiple riders requires a reviewed shared-turn contract",
            missing=("mount.controlled_multi_rider_turn",),
            ruling_kind="agent_dm_adjudication",
        )
    if len(existing_riders) + 1 > normalized_facts["capacity"]:
        raise engine.CombatEngineError("mount rider capacity is full")
    cost = _charge_rider_movement(value, rider)
    rider.setdefault("turn_flags", {})["mounting_used_turn_token"] = token
    original_initiative = int(mount.get("initiative", 0) or 0)
    relation = {
        "id": f"mount:{rider_actor_id}:{mount_actor_id}",
        "rider_actor_id": rider_actor_id,
        "mount_actor_id": mount_actor_id,
        "mode": mode,
        "capacity": normalized_facts["capacity"],
        "active": True,
        "facts": normalized_facts,
        "movement_cost_ft": cost,
    }
    if mode == "controlled":
        relation["original_initiative"] = original_initiative
        mount["initiative"] = int(rider.get("initiative", 0) or 0)
        mount["mounted_turn"] = {
            "mode": "controlled",
            "owner_actor_id": rider_actor_id,
            "original_initiative": original_initiative,
        }
    value.setdefault("mount_relations", []).append(relation)
    if value.get("positioning_mode") == "grid":
        rider["position"] = deepcopy(mount.get("position"))
    engine._sort_combatants(value["combatants"])
    value["turn_index"] = next(
        index for index, actor in enumerate(value["combatants"])
        if str(actor.get("actor_id") or "") == str(rider_actor_id)
    )
    value.setdefault("log", []).append({
        "type": "mount_relation_started",
        "relation": deepcopy(relation),
        "turn_token": token,
    })
    return value


def dismount_2014(
    encounter: dict[str, Any],
    *,
    rider_actor_id: str,
    destination: dict[str, Any] | None,
    spatial_facts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if engine._normalize_ruleset(encounter.get("ruleset")) != "2014":
        raise engine.CombatEngineError("mounted combat contract is source-bound to 2014 rules")
    value = deepcopy(encounter)
    rider, token = _require_current_rider_turn(value, rider_actor_id)
    relation = relation_for_rider(value, rider_actor_id)
    if relation is None:
        raise engine.CombatEngineError("rider is not mounted")
    normalized_facts = None
    mount = _combatant(value, str(relation.get("mount_actor_id") or ""))
    if mount is None:
        raise engine.NeedsRulingError("mounted creature is no longer in the encounter")
    if value.get("positioning_mode") == "grid":
        mount_position = engine._position(mount.get("position"))
        landing = engine._position(destination)
        if mount_position is None or landing is None:
            raise engine.NeedsRulingError(
                "Grid dismount requires a recorded legal landing position"
            )
        if engine._grid_distance(mount_position, landing) > 5:
            raise engine.CombatEngineError("dismount landing must be within 5 feet of the mount")
        battle_map = dict(value.get("battle_map") or {})
        rider_space = grid_space(rider, landing, battle_map)["space_ft"]
        for other in value.get("combatants", []):
            if other is rider or other is mount:
                continue
            if "dead" in engine._condition_set(other.get("conditions")):
                continue
            other_position = engine._position(other.get("position"))
            if other_position is None:
                continue
            other_space = grid_space(other, other_position, battle_map)["space_ft"]
            if overlap(landing, rider_space, other_position, other_space):
                raise engine.CombatEngineError("dismount landing space is occupied")
        rider["position"] = deepcopy(destination)
    else:
        if destination is not None:
            raise engine.CombatEngineError("Agent dismounting does not accept coordinates")
        required = {"decision_id", "reason", "scene_ref", "scene_excerpt", "within_5_feet",
                    "destination_legal"}
        if not isinstance(spatial_facts, dict) or set(spatial_facts) != required:
            raise engine.NeedsRulingError(
                "Agent dismounting requires a bounded legal landing-space ruling",
                missing=("dismount.spatial_facts",),
                ruling_kind="agent_dm_adjudication",
            )
        if spatial_facts.get("within_5_feet") is not True or spatial_facts.get(
            "destination_legal"
        ) is not True:
            raise engine.CombatEngineError("Agent ruling does not provide a legal dismount landing")
        normalized_facts = deepcopy(spatial_facts)
    cost = _charge_rider_movement(value, rider)
    rider.setdefault("turn_flags", {})["mounting_used_turn_token"] = token
    relation["active"] = False
    relation["ended_reason"] = "voluntary_dismount"
    relation["movement_cost_ft"] = cost
    remaining = [
        item for item in active_mount_relations(value)
        if str(item.get("mount_actor_id") or "") == str(mount.get("actor_id") or "")
    ]
    if not remaining:
        mounted_turn = dict(mount.get("mounted_turn") or {})
        mount.pop("mounted_turn", None)
        if relation.get("mode") == "controlled":
            mount["initiative"] = int(
                mounted_turn.get(
                    "original_initiative",
                    relation.get("original_initiative", mount.get("initiative", 0)),
                )
            )
    engine._sort_combatants(value["combatants"])
    value["turn_index"] = next(
        index for index, actor in enumerate(value["combatants"])
        if str(actor.get("actor_id") or "") == str(rider_actor_id)
    )
    value.setdefault("log", []).append({
        "type": "mount_relation_ended",
        "relation_id": relation["id"],
        "reason": "voluntary_dismount",
        "movement_cost_ft": cost,
        **({"spatial_facts": normalized_facts} if normalized_facts else {}),
    })
    return value


def sync_mounted_riders(encounter: dict[str, Any], mount_actor_id: str) -> None:
    mount = _combatant(encounter, mount_actor_id)
    if mount is None or encounter.get("positioning_mode") != "grid":
        return
    for rider_actor_id in riders_of(encounter, mount_actor_id):
        rider = _combatant(encounter, rider_actor_id)
        if rider is not None:
            rider["position"] = deepcopy(mount.get("position"))


def _nearest_grid_landing(
    encounter: dict[str, Any], rider: dict[str, Any], mount: dict[str, Any]
) -> dict[str, int]:
    from .spaces import distance_between, grid_space

    mount_position = engine._position(mount.get("position"))
    if mount_position is None:
        raise engine.NeedsRulingError(
            "forced dismount requires the mount's recorded position",
            missing=("mount.position",),
        )
    battle_map = dict(encounter.get("battle_map") or {})
    mount_space = grid_space(mount, mount_position, battle_map)["space_ft"]
    bounds = dict(battle_map.get("bounds") or {})
    width = bounds.get("width_cells")
    height = bounds.get("height_cells")
    if isinstance(width, int) and isinstance(height, int):
        candidates = (
            (x, y)
            for x in range(width)
            for y in range(height)
        )
    else:
        candidates = (
            (x, y)
            for x in range(int(mount_position[0]) - 8, int(mount_position[0]) + 9)
            for y in range(int(mount_position[1]) - 8, int(mount_position[1]) + 9)
        )
    occupants = [
        item for item in encounter.get("combatants", [])
        if item is not rider and item is not mount
        and "dead" not in engine._condition_set(item.get("conditions"))
    ]
    legal = []
    for point in candidates:
        point_tuple = (int(point[0]), int(point[1]))
        position = {"x": point_tuple[0], "y": point_tuple[1]}
        try:
            rider_space = grid_space(rider, point_tuple, battle_map)["space_ft"]
        except (ValueError, KeyError):
            continue
        if overlap(point_tuple, rider_space, mount_position, mount_space):
            continue
        if distance_between(point_tuple, rider_space, mount_position, mount_space) > 5:
            continue
        if any(
            overlap(
                point_tuple,
                rider_space,
                engine._position(other.get("position")),
                grid_space(
                    other,
                    engine._position(other.get("position")) or (0, 0),
                    battle_map,
                )["space_ft"],
            )
            for other in occupants
            if engine._position(other.get("position")) is not None
        ):
            continue
        legal.append(position)
    if not legal:
        raise engine.NeedsRulingError(
            "no legal Grid space is available within 5 feet of the mount",
            missing=("mount_fall.landing_space",),
            ruling_kind="agent_spatial_decision",
        )
    return min(
        legal,
        key=lambda point: (
            engine._grid_distance(mount_position, (point["x"], point["y"])),
            point["x"],
            point["y"],
        ),
    )


def forced_dismount_2014(
    encounter: dict[str, Any],
    *,
    rider_actor_id: str,
    reason: str,
    prone: bool,
) -> dict[str, Any]:
    """End one mounted relation after a source-defined forced fall."""
    if engine._normalize_ruleset(encounter.get("ruleset")) != "2014":
        raise engine.CombatEngineError("forced dismount is source-bound to 2014 rules")
    if reason not in {
        "mount_moved_unwillingly",
        "rider_knocked_prone",
        "mount_knocked_prone",
        "mount_unavailable",
        "rider_unavailable",
    }:
        raise engine.CombatEngineError("unsupported forced-dismount trigger")
    if not isinstance(prone, bool):
        raise engine.CombatEngineError("forced-dismount prone outcome must be boolean")
    value = deepcopy(encounter)
    relation = relation_for_rider(value, rider_actor_id)
    if relation is None:
        raise engine.CombatEngineError("rider is not mounted")
    rider = _combatant(value, rider_actor_id)
    mount = _combatant(value, str(relation.get("mount_actor_id") or ""))
    if rider is None or mount is None:
        raise engine.CombatEngineError("mounted relation names a missing combatant")
    agent_landing = None
    if value.get("positioning_mode") == "grid":
        position = _nearest_grid_landing(value, rider, mount)
        if position is None:
            raise engine.NeedsRulingError(
                "Grid forced dismount requires a legal landing position",
                missing=("mount_fall.landing_space",),
            )
        rider["position"] = {"x": int(position["x"]), "y": int(position["y"])}
    else:
        # Agent scenes do not own coordinates. Preserve the source's relative
        # landing constraint as a fact in the combat log without inventing a
        # position or running Grid occupancy checks in narrative space.
        rider["position"] = None
        agent_landing = {
            "kind": "within_range_of_actor",
            "actor_id": str(mount.get("actor_id") or ""),
            "maximum_distance_ft": 5,
        }
    relation["active"] = False
    relation["ended_reason"] = reason
    remaining = [
        item for item in active_mount_relations(value)
        if str(item.get("mount_actor_id") or "") == str(mount.get("actor_id") or "")
    ]
    if not remaining:
        mounted_turn = dict(mount.get("mounted_turn") or {})
        mount.pop("mounted_turn", None)
        if relation.get("mode") == "controlled":
            mount["initiative"] = int(
                mounted_turn.get(
                    "original_initiative",
                    relation.get("original_initiative", mount.get("initiative", 0)),
                )
            )
        engine._sort_combatants(value["combatants"])
    if prone:
        conditions = list(rider.get("conditions") or [])
        if "prone" not in engine._condition_set(conditions):
            conditions.append("prone")
        rider["conditions"] = conditions
    value.setdefault("log", []).append(
        {
            "type": "mount_forced_dismount",
            "relation_id": relation["id"],
            "rider_actor_id": rider_actor_id,
            "mount_actor_id": str(mount.get("actor_id") or ""),
            "reason": reason,
            "prone": prone,
            "position": deepcopy(rider.get("position")),
            **({"agent_landing": agent_landing} if agent_landing is not None else {}),
        }
    )
    return value
