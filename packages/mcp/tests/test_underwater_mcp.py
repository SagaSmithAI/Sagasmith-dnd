"""Water facts cross public Runtime, MCP, local Host and durable transactions."""

import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest
from sagasmith_core.idempotency import IdempotencyService
from sagasmith_core.state import StateMutationService
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.water import WATER_RULE
from sagasmith_dnd_runtime.application import create_runtime

from sagasmith_dnd_mcp.server import close_server, create_server
from tests.test_source_object_authority import EXCERPT, RULING, setup

WATER = EXCERPT + " The vault is flooded. The submerged wall and swimmers are fully immersed."


def enable_local(world):
    world.close()
    world.config = replace(world.config, local_authority=True, bound_principal_id="system:local")
    world.runtime = create_runtime(world.config)


async def arguments(world, key="water", *, actors=None, objects=None):
    campaign, _ = await world.snapshot()
    return {
        "campaign_id": world.cid, "action": "water", "idempotency_key": key,
        "expected_revision": campaign["revision"], "payload": {
            "source_ref": world.source, "source_excerpt": WATER,
            "reason": "The DM confirms these water facts from the flooded vault scene.",
            "actors": actors if actors is not None else [
                {"actor_id": world.aid, "underwater": True, "fully_immersed": True},
            ], "objects": objects or [],
        },
    }


@pytest.mark.parametrize("local", [False, True])
def test_water_public_authority_restart_replay_and_host_context(tmp_path, local):
    async def run():
        world = await setup(tmp_path, source_description=WATER)
        try:
            other = await world.call("character_create_from", {
                "mode": "direct", "idempotency_key": "other-swimmer", "payload": {
                    "campaign_id": world.cid, "name": "Other swimmer",
                    "sheet": default_character_sheet(),
                },
            })
            if local:
                enable_local(world)
            args = await arguments(world)
            args["payload"]["actors"].append({
                "actor_id": other["id"], "underwater": True, "fully_immersed": False,
            })
            before = await world.snapshot()
            if local:
                args.pop("expected_revision")
            first = await world.call("environment_change", args)
            after = await world.snapshot()
            assert first["status"] == "committed"
            assert after[0]["revision"] == before[0]["revision"] + 1
            assert after[1]["revision"] == before[1]["revision"] + 1
            assert after[0]["state"]["random_stream"] == before[0]["state"]["random_stream"]
            state = after[1]["sheet"]["combat"]["water_environment"]
            assert state["underwater"] and state["fully_immersed"]
            assert state["resolution_id"] == first["resolution_id"]
            assert WATER_RULE in {r["mechanic_id"] for r in first["rule_receipts"]}
            assert "Order_of_Combat.md#underwater-combat" in str(first["rule_receipts"])
            assert WATER not in str(state)  # The module excerpt remains DM-only.
            if local:
                assert {a["id"] for a in first["local_context"]["actors"]} == {
                    world.aid, other["id"],
                }
                assert next(a for a in first["local_context"]["actors"] if a["id"] == world.aid)[
                    "sheet"
                ]["combat"]["water_environment"] == state
            world.close()
            server = create_server(world.config)
            try:
                _, replay = await server.call_tool("environment_change", args)
                replay = replay.get("result", replay)
                assert replay["resolution_id"] == first["resolution_id"]
                assert replay["actors"] == first["actors"]
            finally:
                close_server(server)
            world.runtime = create_runtime(world.config)
            assert await world.snapshot() == after
            dry = await arguments(world, "leave", actors=[{
                "actor_id": world.aid, "underwater": False, "fully_immersed": False,
            }])
            await world.call("environment_change", dry)
            assert not (await world.snapshot())[1]["sheet"]["combat"]["water_environment"][
                "fully_immersed"
            ]
        finally:
            world.close()

    asyncio.run(run())


def test_water_rejects_unreviewed_facts_and_unauthorized_actor_card_changes(tmp_path):
    async def run():
        world = await setup(tmp_path, source_description=WATER)
        try:
            args = await arguments(world)
            before = await world.snapshot()
            for index, change in enumerate((
                {"actors": [{"actor_id": world.aid, "underwater": 1, "fully_immersed": True}]},
                {"actors": [{"actor_id": world.aid, "underwater": False, "fully_immersed": True}]},
                {"actors": args["payload"]["actors"] * 2},
                {"source_excerpt": "This made-up ocean is not part of the source."},
                {"resistance": "fire"},
            )):
                with pytest.raises(Exception):
                    await world.call("environment_change", {
                        **args, "idempotency_key": f"invalid-{index}",
                        "payload": {**args["payload"], **change},
                    })
                assert await world.snapshot() == before
            with pytest.raises(Exception, match="revision conflict"):
                await world.call("environment_change", {**args, "expected_revision": 0})
            with pytest.raises(Exception):
                await world.call("environment_change", args, principal="user:outsider")
            sheet = deepcopy(before[1]["sheet"])
            sheet["combat"]["water_environment"] = {"underwater": True, "fully_immersed": True}
            with pytest.raises(Exception, match="engine-owned"):
                await world.call("character_create_from", {
                    "mode": "direct", "idempotency_key": "forged", "payload": {
                        "campaign_id": world.cid, "name": "Forged water card", "sheet": sheet,
                    },
                })
            assert await world.snapshot() == before
        finally:
            world.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["actor_cas", "receipt"])
def test_water_commit_failure_is_atomic_and_retryable(tmp_path, monkeypatch, failure):
    async def run():
        world = await setup(tmp_path, source_description=WATER)
        try:
            args = await arguments(world)
            before = await world.snapshot()
            original_replace = StateMutationService.replace
            original_remember = IdempotencyService.remember_write_in_session

            def race(self, campaign_id, **kwargs):
                if kwargs.get("operation") == "environment.water.transition":
                    kwargs["character_updates"] = [
                        replace(item, expected_revision=-1) for item in kwargs["character_updates"]
                    ]
                return original_replace(self, campaign_id, **kwargs)

            def fail(self, session, **kwargs):
                if kwargs.get("key") == "water":
                    raise RuntimeError("injected water receipt failure")
                return original_remember(self, session, **kwargs)

            if failure == "actor_cas":
                monkeypatch.setattr(StateMutationService, "replace", race)
            else:
                monkeypatch.setattr(IdempotencyService, "remember_write_in_session", fail)
            with pytest.raises(Exception, match="revision conflict|injected water receipt"):
                await world.call("environment_change", args)
            assert await world.snapshot() == before
            monkeypatch.setattr(StateMutationService, "replace", original_replace)
            monkeypatch.setattr(IdempotencyService, "remember_write_in_session", original_remember)
            assert (await world.call("environment_change", args))["status"] == "committed"
        finally:
            world.close()

    asyncio.run(run())


def test_underwater_source_object_range_and_fire_defense(tmp_path):
    async def run():
        world = await setup(tmp_path, kind="fire", source_description=WATER, random_seed="water")
        try:
            world.profile["fully_immersed"] = True
            first = await world.call("character_action", await world.arguments())
            assert first["attack"]["hit"]
            assert first["damage"]["hp_loss"] == first["damage"]["amount"] // 2
            assert WATER_RULE in {r["mechanic_id"] for r in first["rule_receipts"]}
            approval = first["object"]["profile_approval"]
            await world.call("environment_change", await arguments(world, objects=[{
                "scene_id": world.profile["scene_id"], "object_id": world.profile["id"],
                "fully_immersed": False,
            }]))
            before = await world.snapshot()
            with pytest.raises(Exception, match="range classification"):
                await world.call("character_action", await world.arguments("missing"))
            assert await world.snapshot() == before
            args = await world.arguments("long", attack_ruling={**RULING, "long_range": True})
            missed = await world.call("character_action", args)
            assert missed["attack"]["automatic_miss"]
            assert missed["attack"]["rolls"] == []
            assert "random_stream_receipt" not in missed
            assert missed["object"]["profile_approval"] == approval
            assert missed["object"]["profile"]["fully_immersed"] is True
            assert missed["object"]["water_environment"]["fully_immersed"] is False
            assert missed["object"]["fully_immersed"] is False
            after = await world.snapshot()
            assert after[0]["state"]["random_stream"] == before[0]["state"]["random_stream"]
            assert next(i for i in after[1]["sheet"]["inventory"]["items"] if i["id"] == "arrows")[
                "quantity"
            ] == 18
            assert (await world.call("character_action", args))["attack"] == missed["attack"]
        finally:
            world.close()

    asyncio.run(run())


def test_underwater_combat_automatic_miss_spends_budget_and_ammunition(tmp_path):
    async def run():
        world = await setup(tmp_path, source_description=WATER)
        try:
            enable_local(world)
            sheet = default_character_sheet()
            sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
            target = await world.call("character_create_from", {
                "mode": "direct", "idempotency_key": "target", "payload": {
                    "campaign_id": world.cid, "name": "Target", "sheet": sheet,
                },
            })
            await world.call("game_phase", {"campaign_id": world.cid, "action": "set",
                                           "tool_profile": "play", "idempotency_key": "play"})
            await world.call("combat_start", {
                "campaign_id": world.cid, "positioning_mode": "grid",
                "participant_ids": [world.aid, target["id"]], "idempotency_key": "start",
                "participant_config": [
                    {"actor_id": world.aid, "initiative": 20, "position": {"x": 0, "y": 0}},
                    {"actor_id": target["id"], "initiative": 10, "position": {"x": 18, "y": 0}},
                ], "battle_map": {"width_cells": 25, "height_cells": 10},
            })
            await world.call("environment_change", await arguments(world))
            before = await world.snapshot()
            args = {"campaign_id": world.cid, "actor_id": world.aid, "target_id": target["id"],
                    "action": {"weapon_id": "bow"}, "idempotency_key": "shot"}
            shot = await world.call("combat_resolve_attack", args)
            assert shot["automatic_miss"] and not shot["hit"]
            after = await world.snapshot()
            assert after[0]["state"]["random_stream"] == before[0]["state"]["random_stream"]
            attacker = next(a for a in after[0]["state"]["combat"]["combatants"]
                            if a["actor_id"] == world.aid)
            assert attacker["turn_budget"]["main_action"] == 0
            assert next(i for i in after[1]["sheet"]["inventory"]["items"] if i["id"] == "arrows")[
                "quantity"
            ] == 19
            assert await world.call("combat_resolve_attack", args) == shot
            damage = await world.call("combat_hp_change", {
                "campaign_id": world.cid, "target_id": world.aid, "action": "damage",
                "payload": {"parts": [{"amount": 7, "damage_type": "fire"},
                                      {"amount": 2, "damage_type": "fire"}]},
                "idempotency_key": "fire",
            })
            assert damage["result"]["applied_amount"] == 4
            assert damage["result"]["environment_receipts"][0]["mechanic_id"] == WATER_RULE
        finally:
            world.close()

    asyncio.run(run())
