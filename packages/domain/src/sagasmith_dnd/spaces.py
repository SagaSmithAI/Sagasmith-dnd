"""2014 occupied spaces and squeezing, derived from size and spatial facts."""

from __future__ import annotations

import math
from typing import Any

SPACE_RULE = "dnd5e.core.movement.creature_spaces"
SIZES = ("tiny", "small", "medium", "large", "huge", "gargantuan")
SPACE_FT = dict(zip(SIZES, (2.5, 5, 5, 10, 15, 20)))


def _error(message):
    from .combat_engine import CombatEngineError

    return CombatEngineError(message)


def size_of(actor: dict[str, Any]) -> str:
    size = actor.get("size", "medium")
    if size not in SIZES:
        raise _error("creature space requires a canonical effective size")
    return size


def passage_space(actor: dict, width: Any) -> dict:
    size = size_of(actor)
    normal = SPACE_FT[size]
    if width is None:
        return {"size": size, "space_ft": normal, "squeezing": False, "passage_width_ft": None}
    if type(width) not in (int, float) or not math.isfinite(width) or not 0 < width <= 1000:
        raise _error("passage width must be positive bounded feet, or null for open space")
    smaller = SPACE_FT[SIZES[max(0, SIZES.index(size) - 1)]]
    if width < smaller:
        raise _error("passage is too narrow for this creature, even while squeezing")
    return {"size": size, "space_ft": smaller if width < normal else normal,
            "squeezing": width < normal, "passage_width_ft": width}


def _fits(point, space_ft, battle_map, *, check_bounds=True):
    x, y = point
    cells = space_ft / 5
    bounds = battle_map.get("bounds") or {}
    if check_bounds and bounds and not (0 <= x and 0 <= y and x + cells <= bounds["width_cells"]
                       and y + cells <= bounds["height_cells"]):
        return False
    blocked = set(battle_map.get("blocked_cells") or [])
    return not any(
        f"{cx},{cy}" in blocked
        for cx in range(math.floor(x), math.ceil(x + cells))
        for cy in range(math.floor(y), math.ceil(y + cells))
    )


def grid_space(actor: dict, point, battle_map: dict) -> dict:
    """Anchor a creature's square at its recorded grid cell; never invent a route."""
    normal = passage_space(actor, None)
    if _fits(point, normal["space_ft"], battle_map, check_bounds=False):
        if _fits(point, normal["space_ft"], battle_map):
            return normal
        raise _error("creature footprint extends beyond the reviewed battle map")
    size = size_of(actor)
    smaller = SPACE_FT[SIZES[max(0, SIZES.index(size) - 1)]]
    if smaller < normal["space_ft"] and _fits(point, smaller, battle_map):
        return {"size": size, "space_ft": smaller, "squeezing": True,
                "passage_width_ft": smaller}
    raise _error("creature footprint cannot fit on this part of the battle map")


def overlap(left, left_ft, right, right_ft):
    return (
        left[0] < right[0] + right_ft / 5 and right[0] < left[0] + left_ft / 5
        and left[1] < right[1] + right_ft / 5 and right[1] < left[1] + left_ft / 5
    )


def _validate_occupants(mover, occupants, *, endpoint, voluntary):
    from .combat_engine import NeedsRulingError, _are_hostile

    for other in occupants:
        if mover.get("can_share_space") or other.get("can_share_space"):
            continue
        if endpoint:
            if voluntary:
                raise _error("an actor cannot willingly end movement in another creature's space")
            raise NeedsRulingError(
                "an effect-specific ruling is required for an occupied destination",
                missing=("occupied_destination_resolution",),
            )
        if _are_hostile(mover, other):
            if abs(SIZES.index(size_of(mover)) - SIZES.index(size_of(other))) < 2:
                raise _error("hostile space traversal requires two size categories difference")


def grid_route(encounter, mover, points, *, voluntary, check_endpoint=True):
    """Validate every crossed footprint and return non-stacking terrain and squeeze costs."""
    from .combat_engine import _condition_set, _grid_distance, _position

    battle_map = encounter.get("battle_map") or {}
    others = []
    for other in encounter["combatants"]:
        position = _position(other.get("position"))
        if (other["actor_id"] == mover["actor_id"] or position is None
                or "dead" in _condition_set(other.get("conditions"))):
            continue
        others.append((other, position, grid_space(other, position, battle_map)["space_ft"]))
    difficult = set(battle_map.get("difficult_cells") or [])
    terrain, squeezed, occupied_ids = 0, 0, set()
    final = grid_space(mover, points[0], battle_map)
    for index, (start, point) in enumerate(zip(points, points[1:]), 1):
        final = grid_space(mover, point, battle_map)
        occupants = [other for other, position, size in others
                     if overlap(point, final["space_ft"], position, size)]
        _validate_occupants(mover, occupants,
                            endpoint=check_endpoint and index == len(points) - 1,
                            voluntary=voluntary)
        length = _grid_distance(start, point)
        cells = final["space_ft"] / 5
        ground_difficult = any(
            f"{x},{y}" in difficult
            for x in range(math.floor(point[0]), math.ceil(point[0] + cells))
            for y in range(math.floor(point[1]), math.ceil(point[1] + cells))
        )
        terrain += length if ground_difficult or occupants else 0
        squeezed += length if final["squeezing"] else 0
        occupied_ids.update(other["actor_id"] for other in occupants)
    return {"terrain_extra_ft": terrain, "squeezing_extra_ft": squeezed, "final": final,
            "occupied_actor_ids": sorted(occupied_ids)}


def validate_segments(segments, distance):
    if not isinstance(segments, list) or len(segments) > 200:
        raise _error("space_segments must be a bounded list of reviewed path segments")
    total = 0
    for segment in segments:
        if not isinstance(segment, dict) or set(segment) != {
            "distance_ft", "occupant_ids", "passage_width_ft", "difficult_terrain",
        }:
            raise _error("space segments require distance_ft, occupant_ids, passage_width_ft, "
                         "and difficult_terrain")
        length = segment["distance_ft"]
        ids = segment["occupant_ids"]
        if (type(length) is not int or length <= 0 or length % 5
                or not isinstance(ids, list) or any(not isinstance(x, str) or not x for x in ids)
                or len(ids) != len(set(ids)) or type(segment["difficult_terrain"]) is not bool):
            raise _error("space segment distance, occupants, or terrain are malformed")
        # Validate the width independently of a creature's size.
        width = segment["passage_width_ft"]
        if width is not None and (type(width) not in (int, float)
                                  or not math.isfinite(width) or not 0 < width <= 1000):
            raise _error("space segment passage width is malformed")
        total += length
    if total != distance:
        raise _error("space segments must cover exactly the declared movement distance")
    return segments


def agent_route(encounter, mover, segments, distance, *, voluntary, check_endpoint=True):
    from .combat_engine import _condition_set

    actors = {a["actor_id"]: a for a in encounter["combatants"]}
    terrain, squeezed, occupied_ids = 0, 0, set()
    final = {"size": size_of(mover), "space_ft": mover.get("space_ft", SPACE_FT[size_of(mover)]),
             "squeezing": bool(mover.get("squeezing"))}
    for index, segment in enumerate(validate_segments(segments, distance)):
        ids = segment["occupant_ids"]
        if mover["actor_id"] in ids or set(ids) - set(actors):
            raise _error("space occupants must identify other current combatants")
        occupants = [actors[x] for x in ids
                     if "dead" not in _condition_set(actors[x].get("conditions"))]
        _validate_occupants(mover, occupants, voluntary=voluntary,
                            endpoint=check_endpoint and index == len(segments) - 1)
        final = passage_space(mover, segment["passage_width_ft"])
        terrain += segment["distance_ft"] if segment["difficult_terrain"] or occupants else 0
        squeezed += segment["distance_ft"] if final["squeezing"] else 0
        occupied_ids.update(a["actor_id"] for a in occupants)
    return {"terrain_extra_ft": terrain, "squeezing_extra_ft": squeezed, "final": final,
            "occupied_actor_ids": sorted(occupied_ids)}


def slice_segments(segments, start, end):
    result, offset = [], 0
    for segment in segments:
        stop = offset + segment["distance_ft"]
        length = min(stop, end) - max(offset, start)
        if length > 0:
            result.append({**segment, "distance_ft": length})
        offset = stop
    return result


def distance_between(left, left_ft, right, right_ft):
    """Five-foot-grid distance between nearest occupied cells of two footprints."""
    left_span, right_span = max(0, left_ft / 5 - 1), max(0, right_ft / 5 - 1)
    return int(5 * max(0, left[0] - right[0] - right_span,
                       right[0] - left[0] - left_span, left[1] - right[1] - right_span,
                       right[1] - left[1] - left_span))


def squeezing(encounter, identifier, sheet=None):
    if not encounter or encounter.get("ruleset") != "2014":
        return False
    actor = next(
        (a for a in encounter.get("combatants", []) if a.get("actor_id") == identifier), None,
    )
    if actor is None:
        return False
    if sheet is not None:
        from .character_schema import effective_size

        actor = {**actor, "size": effective_size(sheet)}
    if encounter.get("positioning_mode") == "grid" and actor.get("position"):
        position = actor["position"]
        return grid_space(actor, (position["x"], position["y"]),
                          encounter.get("battle_map") or {})["squeezing"]
    return passage_space(actor, actor.get("passage_width_ft"))["squeezing"]
