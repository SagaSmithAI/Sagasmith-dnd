"""Creature footprints, exact size thresholds and source-defined squeezing."""

from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.combat_engine import (
    CombatEngineError,
    force_move_directly_away,
    preflight_attack,
    resolve_actor_check,
    spend_movement,
    start_encounter,
)
from sagasmith_dnd.spaces import (
    SPACE_FT,
    agent_route,
    grid_route,
    grid_space,
    passage_space,
    slice_segments,
    validate_segments,
)


def actor(identifier, size="medium", position=(0, 0), disposition="friendly"):
    sheet = default_character_sheet()
    sheet["traits"]["size"] = size
    sheet["combat"]["hp"] = {"value": 30, "max": 30, "temp": 0}
    sheet["combat"]["speed"]["walk"] = 60
    return {"id": identifier, "name": identifier, "sheet": sheet,
            "derived": derive_character_sheet(sheet), "initiative": 20 if identifier == "a" else 10,
            "position": dict(zip(("x", "y"), position)), "disposition": disposition}


def encounter(*actors, blocked=(), difficult=()):
    return start_encounter(list(actors), positioning_mode="grid", ruleset="2014", battle_map={
        "bounds": {"width_cells": 12, "height_cells": 12},
        "blocked_cells": list(blocked), "difficult_cells": list(difficult),
    })


@pytest.mark.parametrize("size,feet", list(SPACE_FT.items()))
def test_grid_footprint_uses_each_source_size(size, feet):
    state = grid_space({"size": size}, (0, 0), {})
    assert state["space_ft"] == feet and not state["squeezing"]


@pytest.mark.parametrize("size,width,allowed", [
    ("small", 2.5, True), ("small", 2, False), ("medium", 2.5, False),
    ("large", 5, True), ("large", 4, False), ("huge", 10, True), ("huge", 5, False),
    ("gargantuan", 15, True), ("gargantuan", 10, False),
])
def test_squeezing_fits_one_size_smaller_only(size, width, allowed):
    if allowed:
        assert passage_space({"size": size}, width)["squeezing"]
    else:
        with pytest.raises(CombatEngineError, match="narrow"):
            passage_space({"size": size}, width)


def test_map_edge_does_not_invent_a_narrow_physical_passage():
    with pytest.raises(CombatEngineError, match="beyond"):
        grid_space({"size": "large"}, (1, 0), {
            "bounds": {"width_cells": 2, "height_cells": 2}, "blocked_cells": [],
        })


def test_friendly_footprint_traversal_is_difficult_without_stacking_terrain():
    state = encounter(actor("a"), actor("b", "large", (1, 0)), difficult=["2,0"])
    result = spend_movement(state, "a", 15, destination={"x": 3, "y": 0},
                            path=[{"x": x, "y": 0} for x in (1, 2, 3)])
    assert result["combatants"][0]["turn_budget"]["movement_spent"] == 25
    with pytest.raises(CombatEngineError, match="end movement"):
        spend_movement(state, "a", 10, destination={"x": 2, "y": 0},
                       path=[{"x": 1, "y": 0}, {"x": 2, "y": 0}])
    assert state["combatants"][0]["position"] == {"x": 0, "y": 0}


@pytest.mark.parametrize("mover_size,other_size,allowed", [
    ("small", "large", True), ("medium", "large", False), ("medium", "huge", True),
    ("large", "small", True), ("large", "medium", False), ("tiny", "medium", True),
])
def test_hostile_traversal_requires_two_categories_in_either_direction(
    mover_size, other_size, allowed,
):
    mover = {"actor_id": "a", "size": mover_size, "disposition": "friendly"}
    other = {"actor_id": "b", "size": other_size, "disposition": "hostile"}
    state = {"combatants": [mover, other]}
    segments = [{"distance_ft": 5, "occupant_ids": ["b"], "passage_width_ft": None,
                 "difficult_terrain": False},
                {"distance_ft": 5, "occupant_ids": [], "passage_width_ft": None,
                 "difficult_terrain": False}]
    if allowed:
        assert agent_route(state, mover, segments, 10, voluntary=True)["terrain_extra_ft"] == 5
    else:
        with pytest.raises(CombatEngineError, match="two size"):
            agent_route(state, mover, segments, 10, voluntary=True)


def test_grid_squeezing_cost_state_attacks_and_dexterity_saves():
    a, b = actor("a", "large"), actor("b", position=(1, 1))
    state = encounter(a, b, blocked=["1,2", "1,3"])
    moved = spend_movement(state, "a", 5, destination={"x": 0, "y": 1})
    assert moved["combatants"][0]["squeezing"]
    assert moved["combatants"][0]["turn_budget"]["movement_spent"] == 10
    assert preflight_attack(a, b, action={"weapon_id": "unarmed-strike"},
                            encounter=moved)["disadvantage"]
    assert preflight_attack(b, a, action={"weapon_id": "unarmed-strike"}, encounter=moved,
                            require_attack_action=False, allow_out_of_turn=True)["advantage"]
    dex = resolve_actor_check(a, kind="save", ability="dexterity", dc=10, encounter=moved)
    strength = resolve_actor_check(a, kind="save", ability="strength", dc=10, encounter=moved)
    assert dex["roll_mode"] == "disadvantage" and strength["roll_mode"] == "normal"
    cleared = spend_movement(moved, "a", 15, destination={"x": 0, "y": 4})
    assert not cleared["combatants"][0]["squeezing"]
    assert cleared["combatants"][0]["turn_budget"]["movement_spent"] == 35


def test_grid_traversal_checks_every_large_footprint_cell():
    state = encounter(actor("a"), actor("b", "large", (1, 1)))
    mover = state["combatants"][0]
    result = grid_route(state, mover, [(0, 2), (1, 2), (2, 2), (3, 2)], voluntary=True)
    assert result["terrain_extra_ft"] == 10 and result["occupied_actor_ids"] == ["b"]


def test_agent_segments_slice_exactly_at_reaction_boundary_and_keep_all_costs():
    mover = {"actor_id": "a", "size": "large", "disposition": "friendly"}
    other = {"actor_id": "b", "size": "medium", "disposition": "friendly"}
    state = {"combatants": [mover, other]}
    segments = [{"distance_ft": 10, "occupant_ids": ["b"], "passage_width_ft": 5,
                 "difficult_terrain": True},
                {"distance_ft": 5, "occupant_ids": [], "passage_width_ft": None,
                 "difficult_terrain": False}]
    before = deepcopy(segments)
    prefix = agent_route(state, mover, slice_segments(segments, 0, 5), 5,
                         voluntary=True, check_endpoint=False)
    rest = agent_route(state, mover, slice_segments(segments, 5, 15), 10, voluntary=True)
    assert prefix["terrain_extra_ft"] == prefix["squeezing_extra_ft"] == 5
    assert prefix["final"]["squeezing"]
    assert rest["terrain_extra_ft"] == rest["squeezing_extra_ft"] == 5
    assert not rest["final"]["squeezing"] and segments == before


def test_attack_and_opportunity_reach_use_nearest_occupied_cells():
    a, b = actor("a", "large"), actor("b", position=(2, 0), disposition="hostile")
    state = encounter(a, b)
    plan = preflight_attack(a, b, action={"weapon_id": "unarmed-strike"}, encounter=state)
    assert plan["range"]["distance_ft"] == 5
    state["combatants"][0]["position"] = {"x": 2, "y": 0}
    state["combatants"][1]["position"] = {"x": 0, "y": 0}
    paused = spend_movement(state, "a", 10, destination={"x": 4, "y": 0})
    # Already ten feet from the Medium threat: no five-foot reach exit.
    assert not paused["pending"]
    larger_threat = encounter(actor("a", position=(2, 0)),
                               actor("b", "large", position=(0, 0), disposition="hostile"))
    paused = spend_movement(larger_threat, "a", 15, destination={"x": 5, "y": 0})
    assert paused["combatants"][0]["position"] == {"x": 2, "y": 0}
    assert paused["pending"][0]["actor_id"] == "b"


def test_forced_movement_stops_at_full_footprint_and_updates_squeezing():
    source = actor("b", position=(0, 0))
    target = actor("a", "large", position=(1, 0))
    state = encounter(target, source, blocked=["2,1", "3,1"])
    before = deepcopy(state["combatants"][0]["turn_budget"])
    moved = force_move_directly_away(state, source_actor_id="b", target_actor_id="a", distance_ft=5)
    target = moved["encounter"]["combatants"][0]
    assert target["position"] == {"x": 2, "y": 0} and target["squeezing"]
    assert target["turn_budget"] == before and not moved["encounter"]["pending"]
    obstacle = {**actor("c", "large", position=(2, 1)), "initiative": 5}
    blocked = encounter(actor("a", position=(1, 2)), actor("b", position=(0, 2)), obstacle)
    result = force_move_directly_away(
        blocked, source_actor_id="b", target_actor_id="a", distance_ft=10,
    )
    assert result["moved_distance_ft"] == 0


@pytest.mark.parametrize("field,value", [
    ("distance_ft", True), ("distance_ft", -5), ("distance_ft", 3),
    ("occupant_ids", "a"), ("occupant_ids", ["a", "a"]), ("occupant_ids", [False]),
    ("passage_width_ft", True), ("passage_width_ft", float("nan")),
    ("passage_width_ft", -5), ("difficult_terrain", 1), ("modifier", 2),
])
def test_agent_geometry_rejects_malformed_facts_and_modifier_conclusions(field, value):
    segment = {"distance_ft": 5, "occupant_ids": [], "passage_width_ft": None,
               "difficult_terrain": False, field: value}
    with pytest.raises(CombatEngineError):
        validate_segments([segment], 5)
