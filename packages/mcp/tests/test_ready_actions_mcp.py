import asyncio
from copy import deepcopy

import pytest
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.random_stream import CampaignRandomStream, use_random_stream
from sagasmith_dnd.spells import CORE_SHIELD_MECHANIC_ID, CORE_SHIELD_SPELL_ID
from test_combat_transaction_boundaries_mcp import _config
from test_opportunity_sneak_attack_mcp import _call, _raw
from test_opportunity_turn_lifecycle_mcp import _FixedD20

import sagasmith_dnd_mcp.server as server_module
from sagasmith_dnd_mcp.server import close_server, create_server


async def prepare(tmp_path, response_kind, *, shield=False):
    config = _config(tmp_path)
    server = create_server(config)
    campaign = await _call(
        server,
        "campaign_create",
        {
            "name": "Ready responses",
            "edition": "2014",
            "idempotency_key": "campaign",
        },
    )
    actors = []
    for name in ("ready", "target"):
        sheet = default_character_sheet()
        sheet["combat"]["hp"].update(value=100, max=100)
        sheet["combat"]["ac"]["override"] = 1
        if name == "target" and shield:
            sheet["combat"]["ac"]["override"] = 14
            sheet["spellcasting"]["spell_slots"] = {
                "1": {"label": "1st", "value": 1, "max": 1, "recovers_on": "long_rest"},
            }
            sheet["content"]["spells"] = [
                {
                    "id": CORE_SHIELD_SPELL_ID,
                    "name": "Shield",
                    "level": 1,
                    "grant": {"source_type": "class", "source_key": "wizard", "method": "known"},
                    "access": {"known": True, "prepared": True},
                    "definition": {
                        "casting_time": "1 reaction, which you take when hit by an attack",
                        "duration": {
                            "kind": "timed",
                            "value": 1,
                            "unit": "round",
                            "concentration": False,
                        },
                        "components": {"verbal": True, "somatic": True},
                    },
                    "mechanic_refs": [CORE_SHIELD_MECHANIC_ID],
                }
            ]
        actors.append(
            await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": name,
                        "sheet": sheet,
                    },
                    "idempotency_key": name,
                },
            )
        )
    cid = campaign["id"]
    started = await _raw(
        server,
        "combat_start",
        {
            "campaign_id": cid,
            "positioning_mode": "grid",
            "battle_map": {"width_cells": 10, "height_cells": 5},
            "participant_ids": [a["id"] for a in actors],
            "participant_config": [
                {
                    "actor_id": a["id"],
                    "initiative": 20 - i * 10,
                    "position": {"x": i, "y": 0},
                    "disposition": "friendly" if i == 0 else "hostile",
                }
                for i, a in enumerate(actors)
            ],
            "expected_revision": campaign["revision"],
            "idempotency_key": "start",
        },
    )
    response = {
        "dash": {"action": "dash"},
        "attack": {
            "action": "attack",
            "target_id": actors[1]["id"],
            "attack": {"weapon_id": "unarmed-strike"},
        },
        "move": {"action": "move", "distance": 15, "destination": {"x": 0, "y": 3}},
        "ruling": {
            "action": "ruling",
            "response": "Pull the lever",
            "source": "Room lever",
            "question": "Does the lever currently release the gate?",
        },
    }[response_kind]
    armed = await _raw(
        server,
        "combat_common_action",
        {
            "campaign_id": cid,
            "actor_id": actors[0]["id"],
            "action": "ready",
            "trigger": "the bell rings",
            "payload": response,
            "expected_revision": started["campaign_revision"],
            "idempotency_key": "arm",
        },
    )
    advanced = await _raw(
        server,
        "combat_end_turn",
        {
            "campaign_id": cid,
            "actor_id": actors[0]["id"],
            "expected_revision": armed["campaign_revision"],
            "idempotency_key": "end",
        },
    )
    return server, config, cid, actors, response, advanced


async def trigger(server, cid, state, key):
    return await _raw(
        server,
        "combat_ready",
        {
            "campaign_id": cid,
            "action": "trigger_action",
            "payload": {
                "readied_id": state["combat"]["readied"][0]["id"],
                "event": "the bell rings",
            },
            "expected_revision": state["campaign_revision"],
            "idempotency_key": key,
        },
    )


@pytest.mark.parametrize("kind", ["dash", "attack", "move", "ruling"])
def test_ready_releases_exact_stored_response_and_survives_restart(tmp_path, monkeypatch, kind):
    rolls = []
    real_roll = server_module.roll_attack_action

    def roll(*, plan):
        rolls.append(deepcopy(plan))
        return real_roll(plan=plan, rng=_FixedD20(15))

    monkeypatch.setattr(server_module, "roll_attack_action", roll)

    async def exercise():
        server, config, cid, actors, original, advanced = await prepare(tmp_path, kind)
        aid = actors[0]["id"]
        try:
            triggered = await trigger(server, cid, advanced, "trigger")
            declined = await _raw(
                server,
                "combat_ready",
                {
                    "campaign_id": cid,
                    "action": "resolve_action",
                    "payload": {
                        "actor_id": aid,
                        "choice_id": triggered["combat"]["pending"][0]["id"],
                        "release": False,
                    },
                    "expected_revision": triggered["campaign_revision"],
                    "idempotency_key": "decline",
                },
            )
            assert declined["status"] == "armed"
            triggered = await trigger(server, cid, declined, "trigger-again")
            request = {
                "campaign_id": cid,
                "action": "resolve_action",
                "payload": {
                    "actor_id": aid,
                    "choice_id": triggered["combat"]["pending"][0]["id"],
                    "release": True,
                },
                "expected_revision": triggered["campaign_revision"],
                "idempotency_key": "release",
            }
            query = {"view": "get", "payload": {"campaign_id": cid}}
            before = await _call(server, "campaign_query", query)
            with pytest.raises(Exception, match="cannot replace"):
                await _raw(
                    server,
                    "combat_ready",
                    {
                        **request,
                        "payload": {**request["payload"], "declaration": {"action": "escape"}},
                    },
                )
            with pytest.raises(Exception, match="revision conflict"):
                await _raw(server, "combat_ready", {**request, "expected_revision": 0})
            assert not rolls
            assert await _call(server, "campaign_query", query) == before
            close_server(server)
            server = create_server(config)
            released = await _raw(server, "combat_ready", request)
            assert released["declaration"] == original
            if kind == "ruling":
                assert released["status"] == "pending_ruling"
                assert not released["released"]
                assert await _call(server, "campaign_query", query) == before
                return
            close_server(server)
            server = create_server(config)
            assert await _raw(server, "combat_ready", request) == released
            actor = next(a for a in released["combat"]["combatants"] if a["actor_id"] == aid)
            assert not released["combat"]["readied"]
            assert actor["turn_budget"]["reaction"] == 0
            assert actor["turn_budget"]["main_action"] == 0
            assert len(rolls) == (1 if kind == "attack" else 0)
            if kind == "dash":
                assert actor["turn_budget"]["movement"] == 60
            elif kind == "attack":
                assert released["result"]["hit"]
                target = next(a for a in released["combat"]["combatants"] if a["actor_id"] != aid)
                assert target["hit_points"] < 100
            else:
                assert actor["position"] == {"x": 0, "y": 1}
                assert released["status"] == "pending_reaction"
                window = released["combat"]["pending"][0]
                finished = await _raw(
                    server,
                    "combat_choice",
                    {
                        "campaign_id": cid,
                        "actor_id": actors[1]["id"],
                        "action": "resolve",
                        "payload": {"choice_id": window["id"], "selection": {"id": "decline"}},
                        "expected_revision": released["campaign_revision"],
                        "idempotency_key": "decline-oa",
                    },
                )
                actor = next(a for a in finished["combat"]["combatants"] if a["actor_id"] == aid)
                assert actor["position"] == {"x": 0, "y": 3}
                assert actor["turn_budget"]["reaction"] == 0
                assert actor["turn_budget"].get("movement_spent", 0) == 0
            current = await _call(server, "campaign_query", query)
            event = next(
                e
                for e in current["state"]["combat"]["log"]
                if e["type"] == "readied_action_released"
            )
            assert event["declaration"] == original
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_readied_attack_revalidates_changed_target_before_any_roll(tmp_path, monkeypatch):
    def forbidden_roll(**kwargs):
        pytest.fail("an invalid readied target must not roll")

    monkeypatch.setattr(server_module, "roll_attack_action", forbidden_roll)

    async def exercise():
        server, _config, cid, actors, original, state = await prepare(tmp_path, "attack")
        try:
            state = await _raw(
                server,
                "combat_movement",
                {
                    "campaign_id": cid,
                    "actor_id": actors[1]["id"],
                    "action": "move",
                    "payload": {
                        "movement_mode": "teleport",
                        "distance": 30,
                        "destination": {"x": 7, "y": 0},
                    },
                    "expected_revision": state["campaign_revision"],
                    "idempotency_key": "relocate",
                },
            )
            triggered = await trigger(server, cid, state, "trigger")
            request = {
                "campaign_id": cid,
                "action": "resolve_action",
                "payload": {
                    "actor_id": actors[0]["id"],
                    "choice_id": triggered["combat"]["pending"][0]["id"],
                    "release": True,
                },
                "expected_revision": triggered["campaign_revision"],
                "idempotency_key": "release",
            }
            query = {"view": "get", "payload": {"campaign_id": cid}}
            before = await _call(server, "campaign_query", query)
            with pytest.raises(Exception, match="cannot replace"):
                await _raw(
                    server,
                    "combat_ready",
                    {
                        **request,
                        "payload": {
                            **request["payload"],
                            "declaration": {**original, "target_id": actors[0]["id"]},
                        },
                    },
                )
            with pytest.raises(Exception, match="range|reach"):
                await _raw(server, "combat_ready", request)
            assert await _call(server, "campaign_query", query) == before
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_readied_attack_rolls_back_random_stream_and_replays_once(tmp_path, monkeypatch):
    attempts = []
    real_damage = server_module.resolve_attack_damage

    def damage(*args, **kwargs):
        attempts.append(deepcopy(kwargs["attack"]))
        if len(attempts) == 1:
            raise RuntimeError("injected settlement failure")
        return real_damage(*args, **kwargs)

    monkeypatch.setattr(server_module, "resolve_attack_damage", damage)

    async def exercise():
        server, _config, cid, actors, _original, state = await prepare(tmp_path, "attack")
        try:
            triggered = await trigger(server, cid, state, "trigger")
            request = {
                "campaign_id": cid,
                "action": "resolve_action",
                "payload": {
                    "actor_id": actors[0]["id"],
                    "choice_id": triggered["combat"]["pending"][0]["id"],
                    "release": True,
                },
                "expected_revision": triggered["campaign_revision"],
                "idempotency_key": "release",
            }
            query = {"view": "get", "payload": {"campaign_id": cid}}
            before = await _call(server, "campaign_query", query)

            def random_context():
                return use_random_stream(
                    CampaignRandomStream.from_campaign_state(
                        cid,
                        before["state"],
                        operation="combat_ready",
                        idempotency_key="release",
                        campaign_revision=before["revision"],
                    )
                )

            # The historical in-process MCP API has no request context. Supply
            # the same authoritative stream that the actual Runtime scope owns.
            with random_context(), pytest.raises(Exception, match="injected settlement failure"):
                await _raw(server, "combat_ready", request)
            assert await _call(server, "campaign_query", query) == before
            with random_context():
                settled = await _raw(server, "combat_ready", request)
            assert attempts[0] == attempts[1]
            assert await _raw(server, "combat_ready", request) == settled
            assert len(attempts) == 2
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_readied_attack_preserves_nested_shield_choice_across_restart(tmp_path, monkeypatch):
    rolls = []
    real_roll = server_module.roll_attack_action

    def roll(*, plan):
        rolls.append(plan)
        return real_roll(plan=plan, rng=_FixedD20(15))

    monkeypatch.setattr(server_module, "roll_attack_action", roll)

    async def exercise():
        server, config, cid, actors, original, state = await prepare(
            tmp_path, "attack", shield=True
        )
        try:
            triggered = await trigger(server, cid, state, "trigger")
            request = {
                "campaign_id": cid,
                "action": "resolve_action",
                "payload": {
                    "actor_id": actors[0]["id"],
                    "choice_id": triggered["combat"]["pending"][0]["id"],
                    "release": True,
                },
                "expected_revision": triggered["campaign_revision"],
                "idempotency_key": "release",
            }
            paused = await _raw(server, "combat_ready", request)
            assert paused["status"] == "pending_reaction"
            assert paused["declaration"] == original
            assert not paused["combat"]["readied"]
            close_server(server)
            server = create_server(config)
            assert await _raw(server, "combat_ready", request) == paused
            window = paused["choice"]
            shield_option = next(c for c in window["candidates"] if c["id"] != "decline")
            settled = await _raw(
                server,
                "combat_choice",
                {
                    "campaign_id": cid,
                    "actor_id": actors[1]["id"],
                    "action": "resolve_defense",
                    "payload": {
                        "choice_id": window["id"],
                        "selection": {"id": shield_option["id"], "cast_level": 1},
                    },
                    "expected_revision": paused["campaign_revision"],
                    "idempotency_key": "shield",
                },
            )
            assert not settled["result"]["hit"]
            assert len(rolls) == 1
            assert all(a["hit_points"] == 100 for a in settled["combat"]["combatants"])
        finally:
            close_server(server)

    asyncio.run(exercise())
