"""Public movement preserves footprint rules, actor CAS and paused squeezing."""

import asyncio
from dataclasses import replace

import pytest
from sagasmith_core.state import StateMutationService
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.spaces import SPACE_RULE
from sagasmith_dnd_runtime.application import create_runtime
from sagasmith_dnd_runtime.application_support import CampaignService, SagaSmithStorage

from sagasmith_dnd_mcp.server import close_server, create_server
from tests.test_source_object_authority import setup


async def prepare(tmp_path, *, squeezing=False, mode="grid", sizes=None):
    world = await setup(tmp_path)
    actors = []
    sizes = sizes or (("large", "medium") if squeezing else ("medium", "large"))
    for index, size in enumerate(sizes):
        sheet = default_character_sheet()
        sheet["traits"]["size"] = size
        sheet["combat"]["hp"] = {"value": 30, "max": 30, "temp": 0}
        sheet["combat"]["speed"]["walk"] = 60
        actors.append(await world.call("character_create_from", {
            "mode": "direct", "idempotency_key": f"actor-{index}", "payload": {
                "campaign_id": world.cid, "name": f"Actor {index}", "sheet": sheet,
            },
        }))
    world.aid = actors[0]["id"]
    world.other = actors[1]["id"]
    world.close()
    world.config = replace(world.config, local_authority=True, bound_principal_id="system:local")
    world.runtime = create_runtime(world.config)
    await world.call("game_phase", {"campaign_id": world.cid, "action": "set",
                                   "tool_profile": "play", "idempotency_key": "play"})
    config = [
        {"actor_id": world.aid, "initiative": 20, "disposition": "friendly"},
        {"actor_id": world.other, "initiative": 10,
         "disposition": "hostile" if mode == "agent" else "friendly"},
    ]
    if mode == "grid":
        config[0]["position"] = {"x": 0, "y": 0}
        config[1]["position"] = {"x": 1, "y": 1 if squeezing else 0}
    elif squeezing:
        config[0]["passage_width_ft"] = 5
    await world.call("combat_start", {
        "campaign_id": world.cid, "positioning_mode": mode,
        "participant_ids": [world.aid, world.other], "participant_config": config,
        "idempotency_key": "start", **({"battle_map": {
            "width_cells": 12, "height_cells": 12,
            "blocked_cells": [{"x": 1, "y": 2}, {"x": 1, "y": 3}] if squeezing else [],
        }} if mode == "grid" else {}),
    })
    return world


def move(world, distance, key="move", **payload):
    return {"campaign_id": world.cid, "actor_id": world.aid, "action": "move",
            "payload": {"distance": distance, **payload}, "idempotency_key": key}


def test_occupied_path_guards_other_actor_and_replays_atomically(tmp_path, monkeypatch):
    async def run():
        world = await prepare(tmp_path)
        try:
            before = await world.snapshot()
            args = move(world, 15, destination={"x": 3, "y": 0})
            original = StateMutationService.replace

            def race(self, campaign_id, **kwargs):
                if kwargs.get("operation") == "combat.movement.spend":
                    assert {x.character_id for x in kwargs["character_updates"]} == {
                        world.aid, world.other,
                    }
                    kwargs["character_updates"] = [
                        replace(x, expected_revision=-1) if x.character_id == world.other else x
                        for x in kwargs["character_updates"]
                    ]
                return original(self, campaign_id, **kwargs)

            monkeypatch.setattr(StateMutationService, "replace", race)
            with pytest.raises(Exception, match="revision conflict"):
                await world.call("combat_movement", args)
            assert await world.snapshot() == before
            monkeypatch.setattr(StateMutationService, "replace", original)
            result = await world.call("combat_movement", args)
            mover = next(a for a in result["combat"]["combatants"] if a["actor_id"] == world.aid)
            assert mover["turn_budget"]["movement_spent"] == 25
            after = await world.snapshot()
            assert after[0]["state"]["random_stream"] == before[0]["state"]["random_stream"]
            assert SPACE_RULE in str(result)
            world.close()
            server = create_server(world.config)
            try:
                _, replay = await server.call_tool("combat_movement", args)
                replay = replay.get("result", replay)
                assert replay["combat"] == result["combat"]
            finally:
                close_server(server)
            world.runtime = create_runtime(world.config)
            replay = await world.call("combat_movement", args)
            assert replay["combat"] == result["combat"]
            assert await world.snapshot() == after
        finally:
            world.close()

    asyncio.run(run())


@pytest.mark.parametrize("sizes,allowed", [
    (("medium", "large"), False), (("small", "large"), True),
    (("large", "medium"), False), (("large", "small"), True),
])
def test_agent_hostile_size_threshold_uses_current_actor_cards(tmp_path, sizes, allowed):
    async def run():
        world = await prepare(tmp_path, mode="agent", sizes=sizes)
        try:
            facts = {
                "decision_id": "cross", "reason": "Cross the other creature's space.",
                "destination_legal": True, "distance_ft": 10,
                "opportunity_attack_actor_ids": [], "space_segments": [
                    {"distance_ft": 5, "occupant_ids": [world.other],
                     "passage_width_ft": None, "difficult_terrain": False},
                    {"distance_ft": 5, "occupant_ids": [], "passage_width_ft": None,
                     "difficult_terrain": False},
                ],
            }
            before = await world.snapshot()
            args = move(world, 10, spatial_facts=facts)
            if not allowed:
                with pytest.raises(Exception, match="two size"):
                    await world.call("combat_movement", args)
                assert await world.snapshot() == before
                return
            result = await world.call("combat_movement", args)
            mover = next(a for a in result["combat"]["combatants"] if a["actor_id"] == world.aid)
            assert mover["turn_budget"]["movement_spent"] == 15
            assert SPACE_RULE in str(result)
        finally:
            world.close()

    asyncio.run(run())


def test_occupied_endpoint_and_invalid_large_geometry_do_not_commit(tmp_path):
    async def run():
        world = await prepare(tmp_path)
        try:
            before = await world.snapshot()
            with pytest.raises(Exception, match="end movement"):
                await world.call("combat_movement", move(
                    world, 10, destination={"x": 2, "y": 0},
                ))
            assert await world.snapshot() == before
        finally:
            world.close()
        large = await prepare(tmp_path / "large", sizes=("large", "medium"))
        try:
            before = await large.snapshot()
            with pytest.raises(Exception, match="beyond"):
                await large.call("combat_movement", move(
                    large, 55, destination={"x": 0, "y": 11},
                ))
            assert await large.snapshot() == before
        finally:
            large.close()

    asyncio.run(run())


def test_squeezing_grid_attack_and_save_use_current_persisted_geometry(tmp_path):
    async def run():
        world = await prepare(tmp_path, squeezing=True)
        try:
            result = await world.call("combat_movement", move(
                world, 5, destination={"x": 0, "y": 1},
            ))
            mover = next(a for a in result["combat"]["combatants"] if a["actor_id"] == world.aid)
            assert mover["squeezing"] and mover["turn_budget"]["movement_spent"] == 10
            plan = await world.call("combat_preflight_attack", {
                "campaign_id": world.cid, "actor_id": world.aid, "target_id": world.other,
                "action": {"weapon_id": "unarmed-strike"},
            })
            assert plan["disadvantage"]
            assert SPACE_RULE in {r["mechanic_id"] for r in plan["rule_receipts"]}
            save = await world.call("combat_check", {
                "campaign_id": world.cid, "actor_id": world.aid, "kind": "save",
                "ability": "dexterity", "dc": 10, "idempotency_key": "save",
            })
            assert save["roll_mode"] == "disadvantage" and len(save["rolls"]) == 2
            world.close()
            world.runtime = create_runtime(world.config)
            result = await world.call("combat_movement", move(
                world, 15, "leave", destination={"x": 0, "y": 4},
            ))
            mover = next(a for a in result["combat"]["combatants"] if a["actor_id"] == world.aid)
            assert not mover["squeezing"] and mover["turn_budget"]["movement_spent"] == 35
        finally:
            world.close()

    asyncio.run(run())


@pytest.mark.parametrize("slowed", [False, True])
def test_agent_squeezing_reaction_preserves_prefix_and_remaining_geometry(
    tmp_path, monkeypatch, slowed,
):
    async def run():
        world = await prepare(tmp_path, squeezing=True, mode="agent")
        try:
            if slowed:
                # Seed an encounter-owned slowing effect in this isolated fixture.
                # Reading the unchanged character as a CAS guard must not erase it.
                world.close()
                storage = SagaSmithStorage(world.config)
                try:
                    campaigns = CampaignService(storage.database)
                    current = campaigns.get(world.cid)
                    current.state["combat"]["combatants"][0]["speed_multiplier"] = 0.5
                    campaigns.update(world.cid, state=current.state,
                                     expected_revision=current.revision)
                finally:
                    storage.vectors.dispose()
                    storage.database.dispose()
                world.runtime = create_runtime(world.config)
            facts = {
                "decision_id": "hallway-exit", "reason": "The corridor widens after five feet.",
                "destination_legal": True, "distance_ft": 10,
                "opportunity_attack_actor_ids": [world.other],
                "opportunity_attack_boundaries": [{
                    "actor_id": world.other, "distance_ft": 5, "weapon_ids": ["unarmed-strike"],
                }], "space_segments": [
                    {"distance_ft": 5, "occupant_ids": [], "passage_width_ft": 5,
                     "difficult_terrain": True},
                    {"distance_ft": 5, "occupant_ids": [], "passage_width_ft": None,
                     "difficult_terrain": False},
                ],
            }
            paused = await world.call("combat_movement", move(world, 10, spatial_facts=facts))
            assert paused["status"] == "pending_reaction"
            mover = next(a for a in paused["combat"]["combatants"] if a["actor_id"] == world.aid)
            assert mover["squeezing"] and mover["turn_budget"]["movement_spent"] == 15
            assert mover["speed_multiplier"] == (0.5 if slowed else 1)
            window = paused["combat"]["pending"][0]
            world.close()
            world.runtime = create_runtime(world.config)
            args = {"campaign_id": world.cid, "actor_id": world.other, "action": "resolve",
                    "payload": {"choice_id": window["id"], "selection": {"id": "decline"}},
                    "idempotency_key": "decline"}
            before = await world.snapshot()
            original = StateMutationService.replace

            def stale_mover(self, campaign_id, **kwargs):
                assert {u.character_id for u in kwargs["character_updates"]} == {
                    world.aid, world.other,
                }
                kwargs["character_updates"] = [
                    replace(u, expected_revision=-1) if u.character_id == world.aid else u
                    for u in kwargs["character_updates"]
                ]
                return original(self, campaign_id, **kwargs)

            monkeypatch.setattr(StateMutationService, "replace", stale_mover)
            with pytest.raises(Exception, match="revision conflict"):
                await world.call("combat_choice", args)
            assert await world.snapshot() == before
            monkeypatch.setattr(StateMutationService, "replace", original)
            completed = await world.call("combat_choice", args)
            mover = next(a for a in completed["combat"]["combatants"] if a["actor_id"] == world.aid)
            assert mover["turn_budget"]["movement_spent"] == 20 and not mover["squeezing"]
            assert mover["speed_multiplier"] == (0.5 if slowed else 1)
            assert "movement_continuation" not in completed["combat"]
            assert (await world.call("combat_choice", args))["combat"] == completed["combat"]
        finally:
            world.close()

    asyncio.run(run())
