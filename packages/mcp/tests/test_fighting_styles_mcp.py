"""Protection pauses before RNG, retains ownership, and resumes one bound attack."""

import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest
from sagasmith_core.state import StateMutationService
from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    equip_inventory_item,
)
from sagasmith_dnd_runtime.application import create_runtime
from sagasmith_dnd_runtime.operations import RequestIdentity

from sagasmith_dnd_mcp.server import close_server, create_server
from tests.test_source_object_authority import setup


async def prepare(tmp_path, *, mode="grid", protectors=1, adjacent=False, fighting_style=None):
    world = await setup(
        tmp_path,
        ammunition=False,
        fighting_style=fighting_style,
        random_seed="styles-1" if fighting_style else None,
    )
    world.close()
    world.config = replace(world.config, local_authority=True, bound_principal_id="system:local")
    world.runtime = create_runtime(world.config)
    original_call = world.call

    async def call(name, arguments, *, principal="system:local"):
        if name in {"combat_resolve_attack", "combat_reaction_attack"}:
            return await world.runtime.execute(name, arguments, context=RequestIdentity(principal))
        return await original_call(name, arguments, principal=principal)

    world.call = call
    created = []
    for index in range(protectors + 1):
        sheet = default_character_sheet()
        sheet["combat"]["hp"] = {"value": 50, "max": 50, "temp": 0}
        if index:
            sheet["content"]["features"] = [
                {
                    "id": "dnd5e.content.srd2014.feature.paladin-fighting-style",
                    "name": "Fighting Style",
                    "source_key": "Paladin",
                    "choices": {"option": "Protection"},
                }
            ]
            sheet, shield = add_inventory_item(
                sheet,
                {
                    "id": "shield",
                    "name": "Shield",
                    "kind": "shield",
                    "mechanics": {"ac_bonus": 2},
                },
            )
            sheet = equip_inventory_item(sheet, shield, "shield")
        actor = await world.call(
            "character_create_from",
            {
                "mode": "direct",
                "idempotency_key": f"character-{index}",
                "payload": {"campaign_id": world.cid, "name": f"Actor {index}", "sheet": sheet},
            },
        )
        created.append(actor["id"])
    world.target, world.protectors = created[0], created[1:]
    await world.call(
        "game_phase",
        {
            "campaign_id": world.cid,
            "action": "set",
            "tool_profile": "play",
            "idempotency_key": "play",
        },
    )
    ids = [world.aid, *created]
    participants = [
        {
            "actor_id": identifier,
            "initiative": 20 - i,
            "disposition": "hostile" if i == 0 else "friendly",
            **(
                {
                    "position": {
                        "x": 0 if i == 0 else (1 if adjacent else 3),
                        "y": (0 if i == 2 else 2) if i > 1 else 1,
                    }
                }
                if mode == "grid"
                else {}
            ),
        }
        for i, identifier in enumerate(ids)
    ]
    await world.call(
        "combat_start",
        {
            "campaign_id": world.cid,
            "participant_ids": ids,
            "participant_config": participants,
            "positioning_mode": mode,
            "idempotency_key": "start",
            **({"battle_map": {"width_cells": 10, "height_cells": 10}} if mode == "grid" else {}),
        },
    )
    return world


def attack(world, key="attack", **action):
    return {
        "campaign_id": world.cid,
        "actor_id": world.aid,
        "target_id": world.target,
        "action": {"weapon_id": "bow", "attack_mode": "ranged", **action},
        "idempotency_key": key,
    }


def choice(world, window, selection="protection", key="choose"):
    return {
        "campaign_id": world.cid,
        "actor_id": window["actor_id"],
        "action": "resolve",
        "payload": {"choice_id": window["id"], "selection": {"id": selection}},
        "idempotency_key": key,
    }


def reactions(result):
    return [w for w in result["combat"]["pending"] if w.get("trigger") == "protection"]


def budget(result, actor_id):
    return next(
        a["turn_budget"] for a in result["combat"]["combatants"] if a["actor_id"] == actor_id
    )


@pytest.mark.parametrize("selection", ["protection", "decline"])
def test_protection_public_protocol_before_roll_restart_and_exact_replay(tmp_path, selection):
    async def run():
        world = await prepare(tmp_path)
        try:
            before = await world.snapshot()
            args = attack(world)
            offered = await world.call("combat_resolve_attack", args)
            assert offered["status"] == "pending_reaction"
            assert offered["result"]["attack_rolled"] is False
            window = reactions(offered)[0]
            pending = await world.snapshot()
            assert pending[0]["state"]["random_stream"] == before[0]["state"]["random_stream"]
            assert budget(offered, world.aid)["main_action"] == 1
            assert budget(offered, world.protectors[0])["reaction"] == 1
            with pytest.raises(Exception, match="Protection"):
                await world.call("combat_resolve_attack", attack(world, "early"))
            with pytest.raises(Exception, match="Protection"):
                await world.call(
                    "combat_resolve_attack",
                    {**attack(world, "change"), "target_id": world.protectors[0]},
                )
            assert await world.snapshot() == pending
            picked = await world.call("combat_choice", choice(world, window, selection))
            assert budget(picked, world.protectors[0])["reaction"] == (
                0 if selection == "protection" else 1
            )
            after_choice = await world.snapshot()
            assert after_choice[0]["state"]["random_stream"] == before[0]["state"]["random_stream"]
            await world.call("combat_choice", choice(world, window, selection))
            assert await world.snapshot() == after_choice
            world.close()
            server = create_server(world.config)
            resume_args = attack(world, "resume")
            try:
                _, raw = await server.call_tool("combat_resolve_attack", resume_args)
                settled = raw
                assert settled["status"] == "committed"
                assert len(settled["result"]["rolls"]) == (2 if selection == "protection" else 1)
                if selection == "protection":
                    assert settled["result"]["protection"]["actor_ids"] == world.protectors
                assert "protection_intent" not in settled["combat"]
                assert budget(settled, world.aid)["main_action"] == 0
            finally:
                close_server(server)
            world.runtime = create_runtime(world.config)
            after = await world.snapshot()
            replay = await world.call("combat_resolve_attack", resume_args)
            assert replay["result"] == settled["result"]
            assert await world.snapshot() == after
            replay_offer = await world.call("combat_resolve_attack", args)
            assert replay_offer["status"] == "pending_reaction"
            assert await world.snapshot() == after
        finally:
            world.close()

    asyncio.run(run())


def test_protection_guard_race_rolls_back_offer_and_reaction(tmp_path, monkeypatch):
    async def run():
        world = await prepare(tmp_path)
        original = StateMutationService.replace
        try:
            for operation, command in [
                ("combat.attack.protection.offer", "offer"),
                ("combat.protection.resolve", "choose"),
            ]:
                if command == "choose":
                    offered = await world.call("combat_resolve_attack", attack(world))
                    args = choice(world, reactions(offered)[0])
                else:
                    args = attack(world)
                before = await world.snapshot()

                def race(self, campaign_id, **kwargs):
                    if kwargs.get("operation") == operation:
                        assert world.protectors[0] in {
                            u.character_id for u in kwargs["character_updates"]
                        }
                        kwargs["character_updates"] = [
                            replace(u, expected_revision=-1)
                            if u.character_id == world.protectors[0]
                            else u
                            for u in kwargs["character_updates"]
                        ]
                    return original(self, campaign_id, **kwargs)

                monkeypatch.setattr(StateMutationService, "replace", race)
                with pytest.raises(Exception, match="revision conflict"):
                    await world.call(
                        "combat_resolve_attack" if command == "offer" else "combat_choice", args
                    )
                assert await world.snapshot() == before
                monkeypatch.setattr(StateMutationService, "replace", original)
        finally:
            world.close()

    asyncio.run(run())


def test_protection_multiple_owners_and_no_early_resume(tmp_path):
    async def run():
        world = await prepare(tmp_path, protectors=2)
        try:
            offered = await world.call("combat_resolve_attack", attack(world))
            windows = reactions(offered)
            assert len(windows) == 2
            await world.call("combat_choice", choice(world, windows[0], key="first"))
            with pytest.raises(Exception, match="Protection"):
                await world.call("combat_resolve_attack", attack(world, "early"))
            window = windows[1]
            for scope, payload in [
                ("campaign", {"role": "player"}),
                ("actor", {"actor_id": world.aid, "can_control": True}),
            ]:
                await world.call(
                    "access_grant",
                    {
                        "scope": scope,
                        "campaign_id": world.cid,
                        "principal_id": "user:attacker",
                        "payload": payload,
                    },
                )
            world.close()
            world.config = replace(world.config, local_authority=False, bound_principal_id=None)
            world.runtime = create_runtime(world.config)
            campaign = (await world.snapshot())[0]
            with pytest.raises(Exception, match="control|access|permission"):
                await world.call(
                    "combat_choice",
                    {**choice(world, window), "expected_revision": campaign["revision"]},
                    principal="user:attacker",
                )
            before_forgery = await world.snapshot()
            with pytest.raises(Exception, match="another actor"):
                await world.call(
                    "combat_choice",
                    {
                        **choice(world, window, key="forged-owner"),
                        "actor_id": world.aid,
                        "expected_revision": campaign["revision"],
                    },
                    principal="user:attacker",
                )
            assert await world.snapshot() == before_forgery
            public = await world.call(
                "combat_query",
                {"campaign_id": world.cid, "view": "status"},
                principal="user:attacker",
            )
            assert "protection_intent" not in str(public)
        finally:
            world.close()

    asyncio.run(run())


def test_agent_protection_requires_explicit_complete_spatial_facts(tmp_path):
    async def run():
        world = await prepare(tmp_path, mode="agent")
        try:
            context = {
                "spatial_facts": {
                    "decision_id": "shot",
                    "reason": "The target is visible and in short bow range.",
                    "targetable": True,
                    "in_range": True,
                    "long_range": False,
                    "cover_degree": "none",
                    "attacker_can_see_target": True,
                    "target_can_see_attacker": True,
                    "target_within_5_ft": False,
                    "close_threat_actor_ids": [],
                    "helper_actor_ids": [],
                    "target_adjacent_ally_actor_ids": [],
                }
            }
            before = await world.snapshot()
            missing = await world.call("combat_resolve_attack", attack(world, context=context))
            assert missing["status"] == "pending_ruling"
            assert "attack.context.protection" in str(missing)
            assert await world.snapshot() == before
            context["protection"] = {
                "decision_id": "shield",
                "reason": "The shield bearer sees the attacker next to the target.",
                "actors": [
                    {"actor_id": world.protectors[0], "within_5_ft": True, "can_see_attacker": True}
                ],
            }
            offered = await world.call("combat_resolve_attack", attack(world, context=context))
            assert len(reactions(offered)) == 1
            await world.call("combat_choice", choice(world, reactions(offered)[0]))
            settled = await world.call(
                "combat_resolve_attack", attack(world, "resume", context=deepcopy(context))
            )
            assert len(settled["result"]["rolls"]) == 2
            assert all(a.get("position") is None for a in settled["combat"]["combatants"])
        finally:
            world.close()

    asyncio.run(run())


def test_semantic_attack_pauses_before_roll_and_resumes_exact_step(tmp_path, monkeypatch):
    import random

    from sagasmith_dnd.combat_engine import roll_attack_action

    from sagasmith_dnd_mcp import server as server_module
    from tests.test_semantic_continuations_mcp import prepare as prepare_semantic
    from tests.test_semantic_plan_mcp import _call, _raw

    plans = []

    def roll(*, plan):
        plans.append(deepcopy(plan))
        return roll_attack_action(plan=plan, rng=random.Random(5))

    monkeypatch.setattr(server_module, "roll_attack_action", roll)

    async def run():
        server, config, cid, actors, paid = await prepare_semantic(
            tmp_path,
            reaction=False,
            protection=True,
        )
        commitment = paid["result"]["declaration"]["agent_resolution_commitment"]
        args = {
            "campaign_id": cid,
            "actor_id": actors[0]["id"],
            "action": "execute_plan",
            "payload": {"commitment": commitment},
            "expected_revision": paid["campaign_revision"],
            "idempotency_key": "execute",
        }
        try:
            pending = await _call(server, "combat_choice", args)
            assert pending["status"] == "pending_choice"
            assert not plans
            window = reactions(pending)[0]
            close_server(server)
            server = create_server(config)
            resolved = await _call(
                server,
                "combat_choice",
                {
                    "campaign_id": cid,
                    "actor_id": actors[2]["id"],
                    "action": "resolve",
                    "payload": {"choice_id": window["id"], "selection": {"id": "protection"}},
                    "expected_revision": pending["campaign_revision"],
                    "idempotency_key": "protect",
                },
            )
            assert not plans
            resumed = await _call(
                server,
                "combat_choice",
                {
                    **args,
                    "idempotency_key": "resume",
                    "expected_revision": resolved["campaign_revision"],
                },
            )
            assert resumed["status"] == "committed"
            assert len(plans) == 2
            assert plans[0]["protection"]["actor_ids"] == [actors[2]["id"]]
            assert "protection" not in plans[1]
            assert "protection_intent" not in resumed["combat"]
            assert budget(resumed, actors[0]["id"])["main_action"] == 0
            assert budget(resumed, actors[2]["id"])["reaction"] == 0
            after = await _call(
                server, "campaign_query", {"view": "get", "payload": {"campaign_id": cid}}
            )
            await _raw(
                server,
                "combat_choice",
                {
                    **args,
                    "idempotency_key": "resume",
                    "expected_revision": resolved["campaign_revision"],
                },
            )
            assert len(plans) == 2
            assert (
                await _call(
                    server, "campaign_query", {"view": "get", "payload": {"campaign_id": cid}}
                )
                == after
            )
        finally:
            close_server(server)

    asyncio.run(run())


@pytest.mark.parametrize("kind", ["opportunity", "ready"])
def test_protection_nests_in_reaction_attack_without_early_payment_or_movement(tmp_path, kind):
    async def run():
        world = await prepare(tmp_path, adjacent=True)
        try:
            if kind == "ready":
                await world.call(
                    "combat_common_action",
                    {
                        "campaign_id": world.cid,
                        "actor_id": world.aid,
                        "action": "ready",
                        "trigger": "the bell rings",
                        "payload": {
                            "action": "attack",
                            "target_id": world.target,
                            "attack": {"weapon_id": "unarmed-strike"},
                        },
                        "idempotency_key": "ready",
                    },
                )
            await world.call(
                "combat_end_turn",
                {
                    "campaign_id": world.cid,
                    "actor_id": world.aid,
                    "idempotency_key": "end",
                },
            )
            if kind == "opportunity":
                moved = await world.call(
                    "combat_movement",
                    {
                        "campaign_id": world.cid,
                        "actor_id": world.target,
                        "action": "move",
                        "payload": {"distance": 10, "destination": {"x": 3, "y": 1}},
                        "idempotency_key": "move",
                    },
                )
                window = next(
                    w
                    for w in moved["combat"]["pending"]
                    if w.get("trigger") == "opportunity_attack"
                )
                name = "combat_reaction_attack"
                args = {
                    "campaign_id": world.cid,
                    "actor_id": world.aid,
                    "target_id": world.target,
                    "choice_id": window["id"],
                    "action": {"weapon_id": "unarmed-strike"},
                    "idempotency_key": "release",
                }
            else:
                campaign = (await world.snapshot())[0]
                readied = campaign["state"]["combat"]["readied"][0]
                triggered = await world.call(
                    "combat_ready",
                    {
                        "campaign_id": world.cid,
                        "action": "trigger_action",
                        "payload": {"readied_id": readied["id"], "event": "the bell rings"},
                        "idempotency_key": "trigger",
                    },
                )
                window = next(
                    w
                    for w in triggered["combat"]["pending"]
                    if w.get("trigger") == "readied_action"
                )
                name = "combat_ready"
                args = {
                    "campaign_id": world.cid,
                    "action": "resolve_action",
                    "payload": {"actor_id": world.aid, "choice_id": window["id"], "release": True},
                    "idempotency_key": "release",
                }
            before = await world.snapshot()
            offered = await world.call(name, args)
            assert offered["status"] == "pending_reaction"
            assert budget(offered, world.aid)["reaction"] == 1
            assert len(reactions(offered)) == 1
            assert any(w["id"] == window["id"] for w in offered["combat"]["pending"])
            await world.call("combat_choice", choice(world, reactions(offered)[0]))
            paused = await world.snapshot()
            assert paused[0]["state"]["random_stream"] == before[0]["state"]["random_stream"]
            if kind == "opportunity":
                assert paused[0]["state"]["combat"].get("movement_continuation")
            result = await world.call(name, {**args, "idempotency_key": "resume"})
            assert result["status"] == "committed"
            assert len(result["result"]["rolls"]) == 2
            assert budget(result, world.aid)["reaction"] == 0
            assert budget(result, world.protectors[0])["reaction"] == 0
            assert "protection_intent" not in result["combat"]
            if kind == "opportunity":
                assert not result["combat"].get("movement_continuation")
                assert next(
                    a for a in result["combat"]["combatants"] if a["actor_id"] == world.target
                )["position"] == {"x": 3, "y": 1}
        finally:
            world.close()

    asyncio.run(run())


@pytest.mark.parametrize("style", ["Archery", "Great Weapon Fighting"])
@pytest.mark.parametrize("in_combat", [False, True])
def test_selected_weapon_style_settles_real_rng_and_replays(tmp_path, style, in_combat):
    async def run():
        world = (
            await prepare(tmp_path, protectors=0, adjacent=True, fighting_style=style)
            if in_combat
            else await setup(
                tmp_path, ammunition=False, fighting_style=style, random_seed="styles-1"
            )
        )
        try:
            before = await world.snapshot()
            gwf = style == "Great Weapon Fighting"
            if in_combat:
                args = attack(
                    world, attack_mode="melee" if gwf else "ranged", use_great_weapon_fighting=gwf
                )
                # Adjacent ranged attacks are normally at disadvantage; retain
                # that scene rule while checking the selected style's +2.
                name = "combat_resolve_attack"
            else:
                args = await world.arguments(use_great_weapon_fighting=gwf)
                name = "character_action"
            settled = await world.call(name, args)
            result = settled["result"] if in_combat else settled["attack"]
            if gwf:
                assert result["natural"] == 4
                assert result["great_weapon_fighting"]["rerolls"] == [
                    {"index": 0, "sides": 6, "from": 2, "to": 2, "source": "great_weapon_fighting"},
                ]
                assert (
                    settled["damage"]["rolls"] == [2, 6]
                    if not in_combat
                    else (result["damage"]["roll_parts"][0]["rolls"] == [2, 6])
                )
                assert settled["random_stream_receipt"]["draw_count"] == 4
            else:
                assert result["attack_bonus"] == 22
            after = await world.snapshot()
            assert (
                after[0]["state"]["random_stream"]["position"]
                > before[0]["state"]["random_stream"]["position"]
            )
            world.close()
            world.runtime = create_runtime(world.config)
            replay = await world.call(name, args)
            assert (replay["result"] if in_combat else replay["attack"]) == result
            assert await world.snapshot() == after
        finally:
            world.close()

    asyncio.run(run())
