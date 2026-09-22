import asyncio
from dataclasses import replace

import pytest
from sagasmith_core.state import StateMutationService
from sagasmith_dnd import bardic_inspiration as b
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd_runtime.application import create_runtime

from tests.test_source_object_authority import setup


async def actor(world, identifier):
    return await world.call(
        "character_query", {"view": "get", "payload": {"character_id": identifier}}
    )


async def prepare(tmp_path, level=1, combat=False):
    world = await setup(tmp_path, random_seed="inspiration")
    world.close()
    world.config = replace(world.config, local_authority=True, bound_principal_id="system:local")
    world.runtime = create_runtime(world.config)
    sheet = default_character_sheet()
    sheet["progression"].update(
        level=level, classes=[{"name": "Bard", "level": level, "hit_die": 8}]
    )
    sheet["abilities"]["charisma"]["score"] = 18
    sheet["combat"]["hp"] = {"value": 30, "max": 30, "temp": 0}
    card = await world.call(
        "character_create_from",
        {
            "mode": "direct",
            "payload": {
                "campaign_id": world.cid,
                "name": "Bard",
                "sheet": sheet,
            },
            "idempotency_key": "bard",
        },
    )
    world.bard = card["id"]
    await world.call(
        "character_content_apply",
        {
            "character_id": world.bard,
            "artifact_id": b.FEATURE,
            "selection": {},
            "idempotency_key": "select-inspiration",
        },
    )
    await world.call(
        "game_phase",
        {
            "campaign_id": world.cid,
            "action": "set",
            "tool_profile": "play",
            "idempotency_key": "play",
        },
    )
    if combat:
        await world.call(
            "combat_start",
            {
                "campaign_id": world.cid,
                "participant_ids": [world.bard, world.aid],
                "participant_config": [
                    {"actor_id": world.bard, "initiative": 20, "position": {"x": 0, "y": 0}},
                    {"actor_id": world.aid, "initiative": 10, "position": {"x": 12, "y": 0}},
                ],
                "positioning_mode": "grid",
                "battle_map": {"width_cells": 20, "height_cells": 20},
                "idempotency_key": "combat",
            },
        )
    return world


def grant_args(world, *, combat=False, key="grant", target=None, **facts):
    declaration = {
        "target_id": target or world.aid,
        "scene_facts": {
            "decision_id": key,
            "reason": "DM reviewed hearing and distance in this scene.",
            "target_can_hear": True,
            **({} if combat else {"within_60_ft": True}),
            **facts,
        },
    }
    if combat:
        return "combat_use_activity", {
            "campaign_id": world.cid,
            "actor_id": world.bard,
            "activity_id": b.FEATURE,
            "declaration": declaration,
            "idempotency_key": key,
        }
    return "character_action", {
        "character_id": world.bard,
        "action": "use_activity",
        "payload": {"activity_id": b.FEATURE, "declaration": declaration},
        "idempotency_key": key,
    }


@pytest.mark.parametrize("accept", [True, False])
def test_grant_roll_choice_restart_exact_replay_and_private_outcome(tmp_path, accept):
    async def run():
        world = await prepare(tmp_path)
        try:
            request = grant_args(world)
            granted = await world.call(*request)
            assert granted["effect"]["metadata"]["die_size"] == 6
            assert b.feature((await actor(world, world.bard))["sheet"])["uses"]["value"] == 3
            assert await world.call(*request) == granted
            args = {
                "campaign_id": world.cid,
                "action": "check",
                "payload": {
                    "actor_id": world.aid,
                    "kind": "ability",
                    "ability": "wisdom",
                    "dc": 40,
                },
                "idempotency_key": "check",
            }
            pending = await world.call("character_check", args)
            assert pending["status"] == "pending_roll"
            assert (
                not {"success", "hit", "dc", "armor_class", "outcome"}
                & pending["choice"]["result"].keys()
            )
            before = await world.snapshot()
            assert before[0]["pending_roll"]["id"] == pending["choice"]["id"]
            assert "bardic_inspiration_grants" not in before[0]["state"]
            world.close()
            world.runtime = create_runtime(world.config)
            choice = {
                "character_id": world.aid,
                "action": "bardic_inspiration",
                "payload": {
                    "choice_id": pending["choice"]["id"],
                    "accept": accept,
                },
                "idempotency_key": "choose",
            }
            result = await world.call("character_state_change", choice)
            assert result["status"] == "committed"
            after = await world.snapshot()
            assert (b.held(after[1]["sheet"]) is None) is accept
            assert after[0]["state"]["random_stream"]["position"] == (
                before[0]["state"]["random_stream"]["position"] + int(accept)
            )
            replay = await world.call("character_state_change", choice)
            assert replay["operation_result"] == result["operation_result"]
            assert (await world.call("character_check", args))["choice"] == pending["choice"]
            assert await world.snapshot() == after
        finally:
            world.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "level,sides,recovery",
    [(4, 6, "long_rest"), (5, 8, "short_rest"), (10, 10, "short_rest"), (15, 12, "short_rest")],
)
def test_real_source_grant_scaling_and_ten_minute_expiry(tmp_path, level, sides, recovery):
    async def run():
        world = await prepare(tmp_path, level)
        try:
            granted = await world.call(*grant_args(world))
            assert granted["effect"]["metadata"]["die_size"] == sides
            assert (
                b.feature((await actor(world, world.bard))["sheet"])["uses"]["recovers_on"]
                == recovery
            )
            before = await world.snapshot()
            for key, facts in [
                ("deaf", {"target_can_hear": False}),
                ("far", {"within_60_ft": False}),
                ("string", {"target_can_hear": "true"}),
                ("duplicate", {}),
            ]:
                with pytest.raises(Exception):
                    await world.call(*grant_args(world, key=key, **facts))
                assert await world.snapshot() == before
            for minutes in (9, 1):
                tick = (await world.snapshot())[0]["state"]["game_time"]["elapsed_ticks"]
                await world.call(
                    "campaign_change",
                    {
                        "campaign_id": world.cid,
                        "action": "clock_advance",
                        "payload": {
                            "period": "minute",
                            "count": minutes,
                            "expected_elapsed_ticks": tick + minutes * 10,
                        },
                        "idempotency_key": str(minutes),
                    },
                )
                assert bool(b.held((await actor(world, world.aid))["sheet"])) is (minutes == 9)
            for rest_type, minutes in (("short_rest", 60), ("long_rest", 480)):
                card = await actor(world, world.bard)
                await world.call(
                    "campaign_change",
                    {
                        "campaign_id": world.cid,
                        "action": "party_rest",
                        "payload": {
                            "rest_type": rest_type,
                            "duration_minutes": minutes,
                            "members": [
                                {"character_id": world.bard, "expected_revision": card["revision"]},
                            ],
                        },
                        "idempotency_key": rest_type,
                    },
                )
                uses = b.feature((await actor(world, world.bard))["sheet"])["uses"]["value"]
                assert uses == (3 if level < 5 and rest_type == "short_rest" else 4)
        finally:
            world.close()

    asyncio.run(run())


async def decide(world, pending, key="use", accept=True):
    return await world.call(
        "character_state_change",
        {
            "character_id": pending["choice"]["actor_id"],
            "action": pending["choice"]["kind"],
            "payload": {"choice_id": pending["choice"]["id"], "accept": accept},
            "idempotency_key": key,
        },
    )


@pytest.mark.parametrize("operation", ["attack", "initiative", "group", "contest", "object"])
def test_inspiration_settles_each_authoritative_roll_path(tmp_path, operation):
    async def run():
        world = await prepare(tmp_path, combat=operation == "attack")
        try:
            await world.call(*grant_args(world, combat=operation == "attack"))
            if operation == "attack":
                await world.call(
                    "combat_end_turn",
                    {"campaign_id": world.cid, "actor_id": world.bard, "idempotency_key": "end"},
                )
                name, request = (
                    "combat_resolve_attack",
                    {
                        "campaign_id": world.cid,
                        "actor_id": world.aid,
                        "target_id": world.bard,
                        "action": {"weapon_id": "bow", "attack_mode": "ranged"},
                        "idempotency_key": "roll",
                    },
                )
            elif operation == "initiative":
                name, request = (
                    "combat_start",
                    {
                        "campaign_id": world.cid,
                        "participant_ids": [world.aid, world.bard],
                        "participant_config": [
                            {"actor_id": world.aid, "tie_breaker": 1},
                            {"actor_id": world.bard, "initiative": 30, "tie_breaker": 2},
                        ],
                        "positioning_mode": "agent",
                        "idempotency_key": "roll",
                    },
                )
            elif operation == "object":
                name, request = "character_action", await world.arguments(key="roll")
            else:
                payload = (
                    {"actor_ids": [world.aid, world.bard], "ability": "strength", "dc": 15}
                    if operation == "group"
                    else {
                        "source_actor_id": world.aid,
                        "target_actor_id": world.bard,
                        "source_ability": "strength",
                        "target_ability": "dexterity",
                    }
                )
                name, request = (
                    "character_check",
                    {
                        "campaign_id": world.cid,
                        "action": operation,
                        "payload": payload,
                        "idempotency_key": "roll",
                    },
                )
            pending = await world.call(name, request)
            assert pending["status"] == "pending_roll", pending
            settled = await decide(world, pending)
            assert settled["status"] == "committed", settled
            assert b.held((await actor(world, world.aid))["sheet"]) is None
            after = await world.snapshot()
            assert "pending_roll" not in after[0]
            assert (await world.call(name, request))["choice"] == pending["choice"]
            assert await world.snapshot() == after
        finally:
            world.close()

    asyncio.run(run())


def test_inspiration_then_legendary_resistance_keep_original_random_prefix(tmp_path):
    from sagasmith_dnd.legendary_resistance import feature

    from tests.test_legendary_resistance_mcp import dragon_sheet

    async def run():
        world = await prepare(tmp_path)
        try:
            await world.call(
                "game_phase",
                {
                    "campaign_id": world.cid,
                    "action": "set",
                    "tool_profile": "lobby",
                    "idempotency_key": "lobby",
                },
            )
            card = await world.call(
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": world.cid,
                        "name": "Dragon",
                        "sheet": dragon_sheet(),
                        "character_type": "monster",
                    },
                    "idempotency_key": "dragon",
                },
            )
            await world.call(
                "game_phase",
                {
                    "campaign_id": world.cid,
                    "action": "set",
                    "tool_profile": "play",
                    "idempotency_key": "play-again",
                },
            )
            await world.call(*grant_args(world, target=card["id"]))
            pending = await world.call(
                "character_check",
                {
                    "campaign_id": world.cid,
                    "action": "check",
                    "payload": {
                        "actor_id": card["id"],
                        "kind": "save",
                        "ability": "wisdom",
                        "dc": 40,
                    },
                    "idempotency_key": "save",
                },
            )
            assert pending["status"] == "pending_roll"
            failed = await decide(world, pending)
            assert failed["status"] == "pending_save"
            assert b.held((await actor(world, card["id"]))["sheet"])
            position = (await world.snapshot())[0]["state"]["random_stream"]["position"]
            final = await decide(world, failed, key="resist")
            assert final["status"] == "committed"
            sheet = (await actor(world, card["id"]))["sheet"]
            assert b.held(sheet) is None and feature(sheet)["uses"]["value"] == 2
            assert (await world.snapshot())[0]["state"]["random_stream"]["position"] == position
        finally:
            world.close()

    asyncio.run(run())


def test_grant_offer_and_spend_conflicts_rollback_every_resource(tmp_path, monkeypatch):
    async def run():
        world = await prepare(tmp_path)
        original = StateMutationService.replace
        try:
            name, request = grant_args(world)
            for stage in ("grant", "offer", "spend"):
                before = (await world.snapshot(), await actor(world, world.bard))

                def race(self, cid, **kwargs):
                    kwargs["character_updates"] = [
                        replace(row, expected_revision=-1) if row.character_id == world.aid else row
                        for row in kwargs.get("character_updates", [])
                    ]
                    return original(self, cid, **kwargs)

                monkeypatch.setattr(StateMutationService, "replace", race)
                with pytest.raises(Exception, match="revision conflict"):
                    await world.call(name, request)
                assert (await world.snapshot(), await actor(world, world.bard)) == before
                monkeypatch.setattr(StateMutationService, "replace", original)
                result = await world.call(name, request)
                if stage == "grant":
                    name, request = (
                        "character_check",
                        {
                            "campaign_id": world.cid,
                            "action": "check",
                            "payload": {
                                "actor_id": world.aid,
                                "kind": "ability",
                                "ability": "wisdom",
                                "dc": 10,
                            },
                            "idempotency_key": "roll",
                        },
                    )
                elif stage == "offer":
                    name, request = (
                        "character_state_change",
                        {
                            "character_id": world.aid,
                            "action": "bardic_inspiration",
                            "payload": {
                                "choice_id": result["choice"]["id"],
                                "accept": True,
                            },
                            "idempotency_key": "choose",
                        },
                    )
            assert result["status"] == "committed"
        finally:
            world.close()

    asyncio.run(run())


def test_combat_grant_spends_bonus_action_and_rejects_self(tmp_path):
    async def run():
        world = await prepare(tmp_path, combat=True)
        try:
            before = await world.snapshot()
            with pytest.raises(Exception, match="another creature"):
                await world.call(*grant_args(world, combat=True, target=world.bard, key="self"))
            assert await world.snapshot() == before
            await world.call(*grant_args(world, combat=True))
            state = (await world.snapshot())[0]["state"]["combat"]
            bard = next(a for a in state["combatants"] if a["actor_id"] == world.bard)
            assert bard["turn_budget"]["bonus_action"] == 0
        finally:
            world.close()

    asyncio.run(run())


def test_roll_choice_ownership_forgery_and_hidden_outcome(tmp_path):
    async def run():
        world = await prepare(tmp_path)
        try:
            await world.call(*grant_args(world))
            for principal, identifier in (
                ("player:owner", world.aid),
                ("player:other", world.bard),
            ):
                for scope, payload in (
                    ("campaign", {"role": "player"}),
                    ("actor", {"actor_id": identifier, "can_control": True}),
                ):
                    await world.call(
                        "access_grant",
                        {
                            "campaign_id": world.cid,
                            "scope": scope,
                            "principal_id": principal,
                            "payload": payload,
                        },
                    )
            world.close()
            world.config = replace(world.config, local_authority=False, bound_principal_id=None)
            world.runtime = create_runtime(world.config)
            campaign = (await world.snapshot())[0]
            pending = await world.call(
                "character_check",
                {
                    "campaign_id": world.cid,
                    "action": "check",
                    "payload": {
                        "actor_id": world.aid,
                        "kind": "save",
                        "ability": "wisdom",
                        "dc": 15,
                    },
                    "expected_revision": campaign["revision"],
                    "idempotency_key": "save",
                },
            )
            current = await actor(world, world.aid)
            request = {
                "character_id": world.aid,
                "action": "bardic_inspiration",
                "payload": {"choice_id": pending["choice"]["id"], "accept": True},
                "expected_revision": current["revision"],
                "idempotency_key": "choose",
            }
            before = await world.snapshot()
            with pytest.raises(Exception, match="control|access|permission"):
                await world.call("character_state_change", request, principal="player:other")
            with pytest.raises(Exception, match="boolean"):
                await world.call(
                    "character_state_change",
                    {**request, "payload": {**request["payload"], "accept": "true"}},
                    principal="player:owner",
                )
            assert await world.snapshot() == before
            for principal in ("player:owner", "player:other"):
                public = await world.call(
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": world.cid}},
                    principal=principal,
                )
                assert "_saving_throw_continuation" not in str(public)
                if principal == "player:other":
                    assert "result" not in public["pending_roll"]
                else:
                    assert "success" not in public["pending_roll"]["result"]
            settled = await world.call("character_state_change", request, principal="player:owner")
            assert settled["status"] == "committed" and "operation_result" not in settled
            roll = settled["resolved_roll"]
            assert roll["natural"] == pending["choice"]["result"]["natural"]
            assert roll["total"] == (
                pending["choice"]["result"]["total"] + roll["bardic_inspiration"]["rolled"]
            )
            assert not {"dc", "success", "armor_class", "hit"} & roll.keys()
            replay = await world.call("character_state_change", request, principal="player:owner")
            assert replay["resolved_roll"] == roll
        finally:
            world.close()

    asyncio.run(run())
