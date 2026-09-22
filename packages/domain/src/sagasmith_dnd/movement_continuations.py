"""Persist movement prefixes and resume only after their reactions settle."""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import uuid4

from . import combat_engine as engine
from .spatial import BattleMapError


def _point(point):
    return {
        key: int(value) if float(value).is_integer() else value
        for key, value in zip(("x", "y"), point)
    }


def _route(segments):
    points = [segments[0][0]]
    for start, end in segments:
        points.extend((engine._inferred_grid_waypoints(start, end) or [start, end])[1:])
    return points


def _window(mover, threat_id, offset, weapon_ids, **fields):
    return {
        "id": f"reaction-{uuid4().hex}",
        "kind": "reaction",
        "actor_id": threat_id,
        "target_id": mover["actor_id"],
        "target_visible": True,
        "event": "movement.leave_reach",
        "trigger": "opportunity_attack",
        "opportunity_attack_weapon_ids": weapon_ids,
        "movement_offset_ft": offset,
        "candidates": [{"id": "opportunity_attack"}, {"id": "decline"}],
        "deadline": "before_commit",
        "status": "pending",
        **fields,
    }


def grid_reaction_windows(encounter, mover, segments):
    """Plan every per-weapon crossing in route order, without committing any."""
    if not segments:
        return []
    points = _route(segments)
    windows = []
    traveled = 0
    for index, (start, end) in enumerate(zip(points, points[1:])):
        for threat in encounter.get("combatants", []):
            if not engine._can_make_opportunity_attack(threat, mover):
                continue
            position = engine._position(threat.get("position"))
            if position is None:
                continue
            options = engine._recorded_opportunity_attack_options(threat)
            before, after = (
                engine._grid_distance(start, position),
                engine._grid_distance(end, position),
            )
            if encounter.get("ruleset") == "2014":
                from .spaces import distance_between, grid_space

                battle_map = encounter.get("battle_map") or {}
                threat_ft = grid_space(threat, position, battle_map)["space_ft"]
                before = distance_between(
                    start, grid_space(mover, start, battle_map)["space_ft"], position, threat_ft,
                )
                after = distance_between(
                    end, grid_space(mover, end, battle_map)["space_ft"], position, threat_ft,
                )
            for reach in sorted({option["reach_ft"] for option in options}):
                if not before <= reach < after:
                    continue
                boundary = engine._movement_boundary_position(start, end, before, after, reach)
                windows.append(
                    _window(
                        mover,
                        threat["actor_id"],
                        traveled + engine._grid_distance(start, boundary),
                        [option["weapon_id"] for option in options if option["reach_ft"] == reach],
                        target_position=_point(boundary),
                        opportunity_attack_reach_ft=reach,
                        movement_segment_index=index,
                    )
                )
        traveled += engine._grid_distance(start, end)
    return sorted(
        windows,
        key=lambda w: (w["movement_offset_ft"], w["actor_id"], w["opportunity_attack_reach_ft"]),
    )


def agent_reaction_windows(encounter, mover, facts):
    """Require explicit coordinate-free boundary facts rather than invent timing."""
    threat_ids = facts.get("opportunity_attack_actor_ids", [])
    boundaries = facts.get("opportunity_attack_boundaries")
    if not threat_ids:
        if boundaries:
            raise engine.CombatEngineError("movement boundaries must match the declared threats")
        return []
    if not isinstance(boundaries, list) or not boundaries:
        raise engine.NeedsRulingError(
            "Agent movement requires the distance and weapon options at each reach exit",
            missing=("movement.spatial_facts.opportunity_attack_boundaries",),
            ruling_kind="agent_spatial_decision",
        )
    actors = {actor["actor_id"]: actor for actor in encounter.get("combatants", [])}
    windows, seen = [], set()
    for boundary in boundaries:
        if not isinstance(boundary, dict) or set(boundary) - {
            "actor_id",
            "distance_ft",
            "weapon_ids",
            "difficult_terrain_extra_ft",
        }:
            raise engine.CombatEngineError("invalid Agent movement boundary")
        threat_id = boundary.get("actor_id")
        threat = actors.get(threat_id)
        if threat_id not in threat_ids or threat is None or threat_id == mover["actor_id"]:
            raise engine.CombatEngineError("movement boundary requires a declared current threat")
        offset = boundary.get("distance_ft")
        if (
            isinstance(offset, bool)
            or not isinstance(offset, int)
            or not 0 <= offset < facts["distance_ft"]
            or offset % 5
        ):
            raise engine.CombatEngineError(
                "movement boundary distance must be a crossed five-foot increment"
            )
        terrain = boundary.get("difficult_terrain_extra_ft", 0)
        total_terrain = facts.get("difficult_terrain_extra_ft", 0)
        if "space_segments" in facts:
            from .spaces import slice_segments, validate_segments

            segments = validate_segments(facts["space_segments"], facts["distance_ft"])
            total_terrain = sum(s["distance_ft"] for s in segments if s["difficult_terrain"])
            derived_terrain = sum(s["distance_ft"] for s in slice_segments(segments, 0, offset)
                                  if s["difficult_terrain"])
            if "difficult_terrain_extra_ft" in boundary and terrain != derived_terrain:
                raise engine.CombatEngineError("boundary terrain disagrees with reviewed segments")
            terrain = derived_terrain
        if (
            total_terrain and "space_segments" not in facts
            and "difficult_terrain_extra_ft" not in boundary
        ):
            raise engine.NeedsRulingError(
                "Agent movement requires difficult-terrain cost before each reaction",
                missing=("movement.boundary.difficult_terrain_extra_ft",),
            )
        if (
            isinstance(terrain, bool)
            or not isinstance(terrain, int)
            or terrain < 0
            or terrain % 5
            or terrain > total_terrain
            or terrain > offset
        ):
            raise engine.CombatEngineError("invalid movement boundary terrain cost")
        options = {
            option["weapon_id"]: option["reach_ft"]
            for option in engine._recorded_opportunity_attack_options(threat)
        }
        weapon_ids = boundary.get("weapon_ids")
        if (
            not isinstance(weapon_ids, list)
            or not weapon_ids
            or any(not isinstance(w, str) or w not in options for w in weapon_ids)
            or len(set(weapon_ids)) != len(weapon_ids)
        ):
            raise engine.CombatEngineError("movement boundary requires current melee weapon IDs")
        reaches = {options[w] for w in weapon_ids}
        if len(reaches) != 1:
            raise engine.CombatEngineError("one movement boundary must bind one weapon reach")
        key = (threat_id, offset, next(iter(reaches)))
        if key in seen:
            raise engine.CombatEngineError("duplicate movement boundary")
        seen.add(key)
        if not engine._can_make_opportunity_attack(threat, mover):
            continue
        windows.append(
            _window(
                mover,
                threat_id,
                offset,
                list(weapon_ids),
                target_position=None,
                opportunity_attack_reach_ft=next(iter(reaches)),
                movement_terrain_extra_ft=terrain,
                spatial_ruling_id=facts.get("decision_id"),
            )
        )
    if {b[0] for b in seen} != set(threat_ids):
        raise engine.CombatEngineError("movement boundaries must cover every declared threat")
    windows.sort(
        key=lambda w: (w["movement_offset_ft"], w["actor_id"], w["opportunity_attack_reach_ft"])
    )
    for left, right in zip(windows, windows[1:]):
        if right["movement_terrain_extra_ft"] < left["movement_terrain_extra_ft"] or (
            right["movement_offset_ft"] == left["movement_offset_ft"]
            and right["movement_terrain_extra_ft"] != left["movement_terrain_extra_ft"]
        ):
            raise engine.CombatEngineError("movement boundary terrain costs must follow path order")
    return windows


def _crossing(window):
    return [window["actor_id"], window["opportunity_attack_reach_ft"]]


def spend_source_movement(
    encounter,
    actor_id,
    distance,
    *,
    payment,
    distance_limit,
    source,
    voluntary=True,
    **request,
):
    """Execute a trusted source grant; public callers cannot manufacture this grant.

    Action/reaction movement has its own source allowance. Movement paid with
    ordinary movement still spends the actor's remaining movement, even off-turn.
    The Runtime binds this call to a reviewed plan or a stored Ready response.
    """
    value = deepcopy(encounter)
    if value.get("movement_continuation"):
        raise engine.CombatEngineError("resolve the pending movement before another source move")
    mover = next((a for a in value.get("combatants", []) if a["actor_id"] == actor_id), None)
    if mover is None or not isinstance(source, dict) or not source.get("id"):
        raise engine.CombatEngineError(
            "source movement requires a current actor and source identity"
        )
    if payment not in {"movement", "action", "reaction"} or not isinstance(voluntary, bool):
        raise engine.CombatEngineError("invalid source movement payment or volition")
    if mover.get("hit_points", 1) <= 0 or (
        payment in {"action", "reaction"}
        and engine._condition_set(mover.get("conditions")) & engine.INCAPACITATING_STATE_IDS
    ):
        raise engine.CombatEngineError("actor cannot pay for source movement")
    speed = engine._effective_speed_ft(mover, request.get("travel_mode", "walk"))
    limit = (
        speed
        if distance_limit == "speed"
        else speed // 2
        if distance_limit == "half_speed"
        else distance_limit
    )
    if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
        raise engine.CombatEngineError("source movement requires a positive distance allowance")
    if payment != "movement":
        budget = mover.setdefault("turn_budget", {})
        key = "main_action" if payment == "action" else "reaction"
        if int(budget.get(key, 0)) <= 0:
            raise engine.CombatEngineError(f"actor has no {payment} remaining")
        budget[key] -= 1
    grant_id = f"movement-{uuid4().hex}"
    mover["source_movement"] = {
        "id": grant_id,
        "payment": payment,
        "remaining": limit,
        "allowance_ft": limit,
        "distance_limit": distance_limit,
        "voluntary": voluntary,
        "source": deepcopy(source),
        "turn_token": engine._combat_turn_token(value),
    }
    value.setdefault("log", []).append(
        {
            "type": "source_movement_payment",
            "actor_id": actor_id,
            "grant_id": grant_id,
            "payment": payment,
            "source": deepcopy(source),
            "allowance_ft": limit,
            "voluntary": voluntary,
        }
    )
    return start_movement(value, actor_id, distance, _source_movement_id=grant_id, **request)


def _finish_source_movement(value, actor_id, grant_id, status):
    if not grant_id:
        return
    mover = next((a for a in value.get("combatants", []) if a["actor_id"] == actor_id), None)
    grant = (mover or {}).get("source_movement", {})
    if grant.get("id") == grant_id:
        mover.pop("source_movement")
    value["log"] = [
        *value.get("log", []),
        {
            "type": "source_movement_finished",
            "actor_id": actor_id,
            "grant_id": grant_id,
            "status": status,
            "source": deepcopy(grant.get("source", {})),
            "position": deepcopy((mover or {}).get("position")),
            "turn_budget": deepcopy((mover or {}).get("turn_budget")),
        },
    ][-100:]


def start_movement(encounter, actor_id, distance, *, _ignored=(), **request):
    if encounter.get("movement_continuation") and request.get("movement_mode", "voluntary") in {
        "voluntary",
        "aggressive",
    }:
        raise engine.CombatEngineError("resolve the pending movement before starting another move")
    planned = engine._spend_movement_uninterrupted(encounter, actor_id, distance, **request)
    prior_ids = {w["id"] for w in encounter.get("pending", [])}
    windows = [
        w
        for w in planned.get("pending", [])
        if w["id"] not in prior_ids and w.get("trigger") == "opportunity_attack"
    ]
    windows = [
        w for w in windows if not (w["movement_offset_ft"] == 0 and _crossing(w) in _ignored)
    ]
    if not windows:
        planned["pending"] = [w for w in planned.get("pending", []) if w["id"] in prior_ids]
        _finish_source_movement(planned, actor_id, request.get("_source_movement_id"), "completed")
        return planned
    window = windows[0]
    offset = window["movement_offset_ft"]
    prefix, remaining = deepcopy(request), deepcopy(request)
    mover = next(a for a in encounter["combatants"] if a["actor_id"] == actor_id)
    if encounter.get("positioning_mode", "grid") == "grid":
        origin = engine._position(mover["position"])
        path = [engine._position(p) for p in request.get("path") or []]
        if not path:
            path = [engine._position(request["destination"])]
        if path[0] != origin:
            path.insert(0, origin)
        points = _route(list(zip(path, path[1:])))
        # Validate the complete route before persisting even its first prefix.
        if encounter.get("battle_map"):
            from .spatial import validate_position

            for point in points:
                validate_position(encounter["battle_map"], _point(point))
        index = window["movement_segment_index"]
        boundary = engine._position(window["target_position"])
        prefix_points = points[: index + 1]
        if prefix_points[-1] != boundary:
            prefix_points.append(boundary)
        remainder_points = [boundary, *points[index + 1 :]]

        def as_positions(pts):
            return [_point(point) for point in pts]

        prefix.update(
            destination=deepcopy(window["target_position"]), path=as_positions(prefix_points)
        )
        remaining.update(destination=as_positions(points)[-1], path=as_positions(remainder_points))
    else:
        facts = deepcopy(request["spatial_facts"])
        if "space_segments" in facts:
            from .spaces import slice_segments

            # Segment facts own the terrain total at every boundary, including
            # boundaries whose optional legacy scalar was not supplied.
            facts["difficult_terrain_extra_ft"] = sum(
                s["distance_ft"] for s in facts["space_segments"] if s["difficult_terrain"]
            )
            for boundary_fact in facts["opportunity_attack_boundaries"]:
                boundary_fact["difficult_terrain_extra_ft"] = sum(
                    s["distance_ft"] for s in slice_segments(
                        facts["space_segments"], 0, boundary_fact["distance_ft"],
                    ) if s["difficult_terrain"]
                )
        terrain = window["movement_terrain_extra_ft"]
        prefix["spatial_facts"] = {
            **facts,
            "distance_ft": offset,
            "difficult_terrain_extra_ft": terrain,
            "opportunity_attack_actor_ids": [],
            "opportunity_attack_boundaries": [],
        }
        rest_boundaries = [
            {
                **b,
                "distance_ft": b["distance_ft"] - offset,
                "difficult_terrain_extra_ft": b.get("difficult_terrain_extra_ft", 0) - terrain,
            }
            for b in facts["opportunity_attack_boundaries"]
            if b["distance_ft"] >= offset
        ]
        remaining["spatial_facts"] = {
            **facts,
            "distance_ft": distance - offset,
            "difficult_terrain_extra_ft": facts.get("difficult_terrain_extra_ft", 0) - terrain,
            "opportunity_attack_actor_ids": sorted({b["actor_id"] for b in rest_boundaries}),
            "opportunity_attack_boundaries": rest_boundaries,
        }
        if "space_segments" in facts:
            from .spaces import slice_segments

            prefix["spatial_facts"]["space_segments"] = slice_segments(
                facts["space_segments"], 0, offset,
            )
            remaining["spatial_facts"]["space_segments"] = slice_segments(
                facts["space_segments"], offset, distance,
            )
    paused = engine._spend_movement_uninterrupted(
        encounter, actor_id, offset, _intermediate_destination=True, **prefix
    )
    paused["pending"] = [*deepcopy(encounter.get("pending", [])), deepcopy(window)]
    ignored = [*deepcopy(_ignored), _crossing(window)] if offset == 0 else [_crossing(window)]
    paused["movement_continuation"] = {
        "actor_id": actor_id,
        "distance": distance - offset,
        "request": remaining,
        "position": deepcopy(window["target_position"]),
        "turn_token": engine._combat_turn_token(encounter),
        "choice_id": window["id"],
        "ignored_crossings": ignored,
    }
    return paused


def resume_pending_movement(encounter: dict[str, Any]) -> dict[str, Any]:
    """Revalidate after all nested choices; never roll dice or charge a prefix twice."""
    continuation = encounter.get("movement_continuation")
    if not isinstance(continuation, dict):
        return encounter
    value = deepcopy(encounter)
    actor_id = continuation["actor_id"]
    # Damage, concentration and zero-HP rescue choices may still change the
    # mover's final state. Never cancel or advance their enclosing movement early.
    if any(
        window.get("status", "pending") == "pending"
        and window.get("id") != continuation["choice_id"]
        for window in value.get("pending", [])
    ):
        return value
    mover = next((a for a in value.get("combatants", []) if a["actor_id"] == actor_id), None)
    reason = None
    conditions = engine._condition_set((mover or {}).get("conditions"))
    if mover is None or not value.get("active", True):
        reason = "combat_or_mover_unavailable"
    elif continuation["turn_token"] != engine._combat_turn_token(value):
        reason = "turn_changed"
    elif (
        conditions
        & {"dead", "unconscious", "stunned", "paralyzed", "petrified", "restrained", "grappled"}
        or mover.get("hit_points", 1) <= 0
    ):
        reason = "mover_cannot_continue"
    elif engine._effective_speed_ft(mover, continuation["request"].get("travel_mode", "walk")) <= 0:
        reason = "speed_zero"
    elif continuation["position"] is not None and engine._position(
        mover.get("position")
    ) != engine._position(continuation["position"]):
        reason = "position_changed"
    if reason is None:
        owned_window = next(
            (w for w in value.get("pending", []) if w["id"] == continuation["choice_id"]), None
        )
        if owned_window:
            threat = next(
                (a for a in value["combatants"] if a["actor_id"] == owned_window["actor_id"]), None
            )
            if threat is None or not engine._can_make_opportunity_attack(threat, mover):
                value["pending"] = [w for w in value["pending"] if w["id"] != owned_window["id"]]
        if any(w.get("status", "pending") == "pending" for w in value.get("pending", [])):
            return value
    value.pop("movement_continuation", None)
    if reason is None:
        try:
            value = start_movement(
                value,
                actor_id,
                continuation["distance"],
                _ignored=continuation["ignored_crossings"],
                **continuation["request"],
            )
        except (engine.CombatEngineError, BattleMapError) as error:
            reason = str(error)
    if reason is not None:
        value["pending"] = [
            w for w in value.get("pending", []) if w["id"] != continuation["choice_id"]
        ]
        _finish_source_movement(
            value, actor_id, continuation["request"].get("_source_movement_id"), "cancelled"
        )
    value["log"] = [
        *value.get("log", []),
        {
            "type": "movement_continuation",
            "actor_id": actor_id,
            "status": "cancelled"
            if reason is not None
            else "paused"
            if value.get("movement_continuation")
            else "completed",
            "reason": reason,
        },
    ][-100:]
    return value
