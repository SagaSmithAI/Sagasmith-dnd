from __future__ import annotations

import asyncio
import random
from copy import deepcopy
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.content_actors import build_srd2014_preset_actors
from sagasmith_dnd.random_stream import (
    CampaignRandomStream,
    initial_random_stream,
    use_random_stream,
)

import sagasmith_dnd_mcp.server as server_module
from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


async def _call_raw(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    if name in {"character_action", "combat_movement", "combat_hp_change"}:
        return result["result"]
    return result


def _config(tmp_path: Path) -> McpConfig:
    return McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "dnd",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
    )


def test_engine_rolled_initiative_tie_rewinds_before_explicit_retry(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "Initiative tie retry",
                "edition": "2014",
                "random_seed": "initiative-tie-5",
                "idempotency_key": "campaign",
            },
        )
        actors = []
        for index in range(2):
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": f"Tie actor {index}",
                        "sheet": default_character_sheet(),
                    },
                    "principal_id": "system:local",
                    "idempotency_key": f"actor-{index}",
                },
            )
            actors.append(actor)
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        phase = await _call(
            server,
            "game_phase",
            {
                "campaign_id": campaign["id"],
                "action": "set",
                "tool_profile": "play",
                "expected_revision": campaign["revision"],
                "idempotency_key": "phase",
            },
        )
        before = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        assert before["state"]["random_stream"]["position"] == 0

        pending_stream = server_module.CampaignRandomStream.from_campaign_state(
            campaign["id"],
            before["state"],
            operation="combat_start",
            idempotency_key="start-pending",
        )
        with server_module.use_random_stream(pending_stream):
            pending = await _call(
                server,
                "combat_start",
                {
                    "positioning_mode": "agent",
                    "campaign_id": campaign["id"],
                    "participant_ids": [actor["id"] for actor in actors],
                    "participant_config": [
                        {"actor_id": actor["id"]} for actor in actors
                    ],
                    "expected_revision": phase["campaign_revision"],
                    "idempotency_key": "start-pending",
                },
            )
        assert pending["status"] == "pending_ruling"
        assert pending["ruling_kind"] == "player_owned_choice"
        assert pending["committed"] is False
        assert pending_stream.position == 0

        unchanged = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        assert unchanged["revision"] == phase["campaign_revision"]
        assert unchanged["state"]["random_stream"]["position"] == 0

        retry_stream = server_module.CampaignRandomStream.from_campaign_state(
            campaign["id"],
            unchanged["state"],
            operation="combat_start",
            idempotency_key="start-resolved",
        )
        with server_module.use_random_stream(retry_stream):
            started = await _call(
                server,
                "combat_start",
                {
                    "positioning_mode": "agent",
                    "campaign_id": campaign["id"],
                    "participant_ids": [actor["id"] for actor in actors],
                    "participant_config": [
                        {"actor_id": actor["id"], "tie_breaker": index}
                        for index, actor in enumerate(actors)
                    ],
                    "expected_revision": phase["campaign_revision"],
                    "idempotency_key": "start-resolved",
                },
            )
        assert retry_stream.has_unpersisted_draws is False
        assert retry_stream.receipt()["position_before"] == 0
        assert retry_stream.receipt()["position_after"] == 2
        assert len({item["initiative"] for item in started["combat"]["combatants"]}) == 1

        committed = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        assert committed["state"]["random_stream"]["position"] == 2

    asyncio.run(exercise())


def test_combat_query_exposes_dm_transaction_history_and_receipts(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "Combat transaction evidence",
                "edition": "2014",
                "idempotency_key": "campaign",
            },
        )
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Receipt actor"},
                "principal_id": "system:local",
                "idempotency_key": "receipt-actor",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        phase = await _call(
            server,
            "game_phase",
            {
                "campaign_id": campaign["id"],
                "action": "set",
                "tool_profile": "play",
                "expected_revision": campaign["revision"],
                "idempotency_key": "receipt-phase",
            },
        )

        history = await _call(
            server,
            "combat_query",
            {
                "campaign_id": campaign["id"],
                "view": "transaction_history",
                "payload": {"limit": 100},
            },
        )
        actor_revision = next(
            item for item in history if item["idempotency_key"] == "receipt-phase"
        )
        assert actor_revision["request_hash"]

        receipt = await _call(
            server,
            "combat_query",
            {
                "campaign_id": campaign["id"],
                "view": "transaction_receipt",
                "payload": {"idempotency_key": "receipt-phase"},
            },
        )
        assert receipt["key"] == "receipt-phase"
        assert receipt["response"]["campaign_id"] == campaign["id"]
        assert receipt["response"]["campaign_revision"] == phase["campaign_revision"]
        assert actor["id"]

    asyncio.run(exercise())


def test_end_turn_does_not_revision_unchanged_character_documents(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Lean turn revisions", "edition": "2014", "idempotency_key": "campaign"},
        )
        actors = []
        for index in range(2):
            actors.append(
                await _call(
                    server,
                    "character_create_from",
                    {
                        "mode": "direct",
                        "payload": {"campaign_id": campaign["id"], "name": f"Actor {index + 1}"},
                        "principal_id": "system:local",
                        "idempotency_key": f"actor-{index + 1}",
                    },
                )
            )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        started = await _call(
            server,
            "combat_start",
            {
                "positioning_mode": "agent",
                "campaign_id": campaign["id"],
                "participant_ids": [item["id"] for item in actors],
                "participant_config": [
                    {"actor_id": actors[0]["id"], "initiative": 20},
                    {"actor_id": actors[1]["id"], "initiative": 10},
                ],
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            },
        )

        ended = await _call(
            server,
            "combat_end_turn",
            {
                "campaign_id": campaign["id"],
                "actor_id": actors[0]["id"],
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "end",
            },
        )
        current = [
            await _call(
                server,
                "character_query",
                {
                    "view": "get",
                    "payload": {"character_id": item["id"]},
                    "principal_id": "system:local",
                },
            )
            for item in actors
        ]

        assert [item["entity_type"] for item in ended["revisions"]] == ["campaign"]
        assert [item["revision"] for item in current] == [item["revision"] for item in actors]

    asyncio.run(exercise())


def test_available_actions_explicitly_discovers_required_death_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_death_save = server_module.resolve_death_save_to_sheet

    def deterministic_death_save(sheet, **kwargs):
        return original_death_save(sheet, **kwargs, rng=random.Random(1))

    monkeypatch.setattr(server_module, "resolve_death_save_to_sheet", deterministic_death_save)

    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Death action", "edition": "2014", "idempotency_key": "campaign"},
        )
        sheet = default_character_sheet()
        sheet["combat"]["hp"] = {"value": 0, "max": 10, "temp": 0}
        sheet["conditions"] = ["prone", "unconscious"]
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Dying PC", "sheet": sheet},
                "principal_id": "system:local",
                "idempotency_key": "actor",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        started = await _call(
            server,
            "combat_start",
            {
                "positioning_mode": "agent",
                "campaign_id": campaign["id"],
                "participant_ids": [actor["id"]],
                "participant_config": [
                    {"actor_id": actor["id"], "initiative": 10, "death_saves": True}
                ],
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            },
        )

        available = await _call(
            server,
            "combat_query",
            {
                "campaign_id": campaign["id"],
                "view": "available_actions",
                "actor_id": actor["id"],
                "principal_id": "system:local",
            },
        )

        assert started["combat"]["round"] == 1
        assert available["actions"] == ["death_save"]
        resolved = await _call_raw(
            server,
            "combat_check",
            {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "kind": "death_save",
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "death-save",
            },
        )
        assert resolved["result"]["kind"] == "death_save"
        assert resolved["result"]["outcome"] == "pending"

        after = await _call(
            server,
            "combat_query",
            {
                "campaign_id": campaign["id"],
                "view": "available_actions",
                "actor_id": actor["id"],
                "principal_id": "system:local",
            },
        )
        assert after["actions"] == []

    asyncio.run(exercise())


def test_incapacitated_actor_can_settle_free_object_interaction(tmp_path: Path) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "Incapacitated interaction",
                "edition": "2014",
                "idempotency_key": "campaign",
            },
        )
        sheet = default_character_sheet()
        sheet["conditions"] = ["incapacitated", "prone"]
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Incapacitated PC",
                    "sheet": sheet,
                },
                "principal_id": "system:local",
                "idempotency_key": "actor",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        started = await _call(
            server,
            "combat_start",
            {
                "positioning_mode": "grid",
                "battle_map": {"width_cells": 12, "height_cells": 4},
                "campaign_id": campaign["id"],
                "participant_ids": [actor["id"]],
                "participant_config": [
                    {
                        "actor_id": actor["id"],
                        "initiative": 10,
                        "position": {"x": 0, "y": 0},
                    }
                ],
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            },
        )

        available_before_effect = await _call(
            server,
            "combat_query",
            {
                "campaign_id": campaign["id"],
                "view": "available_actions",
                "actor_id": actor["id"],
                "principal_id": "system:local",
            },
        )
        assert available_before_effect["actions"] == ["move", "interact_object"]

        storage = server_module.SagaSmithStorage(config)
        campaigns = server_module.CampaignService(storage.database)
        stored = campaigns.get(campaign["id"])
        state_with_effect = deepcopy(stored.state)
        affected = state_with_effect["combat"]["combatants"][0]
        assert affected["turn_budget"]["movement"] == 30
        # Match the encounter projection produced by a speed effect synced after
        # turn start: the multiplier changes while the remaining budget is stale.
        affected["speed_multiplier"] = 0.0
        effect_applied = campaigns.update(
            campaign["id"],
            state=state_with_effect,
            expected_revision=stored.revision,
        )

        available = await _call(
            server,
            "combat_query",
            {
                "campaign_id": campaign["id"],
                "view": "available_actions",
                "actor_id": actor["id"],
                "principal_id": "system:local",
            },
        )
        assert available["actions"] == ["interact_object"]
        before_blocked_move = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        stale_move = {
            "campaign_id": campaign["id"],
            "actor_id": actor["id"],
            "action": "move",
            "payload": {"distance": 5, "destination": {"x": 1, "y": 0}},
            "expected_revision": started["campaign_revision"],
            "idempotency_key": "stale-blocked-move",
        }
        with pytest.raises(Exception, match="campaign revision conflict"):
            await _call_raw(server, "combat_movement", stale_move)

        blocked_move = {
            **stale_move,
            "expected_revision": effect_applied.revision,
            "idempotency_key": "blocked-move",
        }
        for _attempt in range(2):
            with pytest.raises(Exception, match="effective speed is zero"):
                await _call_raw(server, "combat_movement", blocked_move)
        blocked_stand = {
            "campaign_id": campaign["id"],
            "actor_id": actor["id"],
            "action": "stand",
            "payload": {},
            "expected_revision": effect_applied.revision,
            "idempotency_key": "blocked-stand",
        }
        for _attempt in range(2):
            with pytest.raises(Exception, match="effective speed is zero"):
                await _call_raw(server, "combat_movement", blocked_stand)
        after_blocked_move = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        assert after_blocked_move["revision"] == before_blocked_move["revision"]
        assert after_blocked_move["state"] == before_blocked_move["state"]

        interacted = await _call(
            server,
            "combat_common_action",
            {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "action": "interact_object",
                "payload": {
                    "object_description": "an unlocked door",
                    "interaction": "open",
                },
                "expected_revision": effect_applied.revision,
                "idempotency_key": "open-door",
            },
        )
        combatant = interacted["combat"]["combatants"][0]
        assert interacted["status"] == "committed"
        assert interacted["campaign_revision"] == effect_applied.revision + 1
        assert combatant["turn_budget"]["object_interaction"] == 0
        assert combatant["turn_budget"]["main_action"] == 1
        assert combatant["turn_budget"]["bonus_action"] == 1
        assert combatant["turn_budget"]["movement"] == 30
        assert interacted["combat"]["log"][-1] == {
            "type": "common_action",
            "action": "interact_object",
            "actor_id": actor["id"],
            "target_id": None,
            "payload": {
                "object_description": "an unlocked door",
                "interaction": "open",
            },
            "round": 1,
            "turn_index": 0,
        }

        after = await _call(
            server,
            "combat_query",
            {
                "campaign_id": campaign["id"],
                "view": "available_actions",
                "actor_id": actor["id"],
                "principal_id": "system:local",
            },
        )
        assert after["actions"] == []

    asyncio.run(exercise())


def test_combat_dexterity_save_uses_dodge_normalization_and_atomic_randomness(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "Dodge Dexterity saves",
                "edition": "2014",
                "random_seed": "dodge-save-seed",
                "idempotency_key": "campaign",
            },
        )
        actors = []
        for index, name in enumerate(("Dodger", "Sentinel")):
            actors.append(
                await _call(
                    server,
                    "character_create_from",
                    {
                        "mode": "direct",
                        "payload": {
                            "campaign_id": campaign["id"],
                            "name": name,
                            "character_type": "pc",
                        },
                        "idempotency_key": f"actor-{index}",
                    },
                )
            )
        campaign = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        started = await _call_raw(
            server,
            "combat_start",
            {
                "campaign_id": campaign["id"],
                "positioning_mode": "agent",
                "participant_ids": [item["id"] for item in actors],
                "participant_config": [
                    {"actor_id": actors[0]["id"], "initiative": 20},
                    {"actor_id": actors[1]["id"], "initiative": 10},
                ],
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            },
        )
        dodged = await _call_raw(
            server,
            "combat_common_action",
            {
                "campaign_id": campaign["id"],
                "actor_id": actors[0]["id"],
                "action": "dodge",
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "dodge",
            },
        )
        advanced = await _call_raw(
            server,
            "combat_end_turn",
            {
                "campaign_id": campaign["id"],
                "actor_id": actors[0]["id"],
                "expected_revision": dodged["campaign_revision"],
                "idempotency_key": "end-turn",
            },
        )
        stale_arguments = {
            "campaign_id": campaign["id"],
            "actor_id": actors[0]["id"],
            "kind": "save",
            "ability": " DEX ",
            "dc": 12,
            "expected_revision": dodged["campaign_revision"],
            "idempotency_key": "stale-save",
        }
        with pytest.raises(Exception, match="revision conflict"):
            await _call_raw(server, "combat_check", stale_arguments)
        unchanged = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        assert unchanged["revision"] == advanced["campaign_revision"]

        arguments = {
            **stale_arguments,
            "expected_revision": advanced["campaign_revision"],
            "idempotency_key": "dodge-save",
        }
        stream = CampaignRandomStream(
            campaign_id=campaign["id"],
            seed=initial_random_stream("dodge-save-seed")["seed"],
            position=0,
            operation="combat_check",
            idempotency_key="dodge-save",
        )
        with use_random_stream(stream):
            settled = await _call_raw(server, "combat_check", arguments)
        assert len(settled["result"]["rolls"]) == 2
        assert [
            receipt["mechanic_id"] for receipt in settled["result"]["rule_receipts"]
        ] == ["dnd5e.core.action.dodge"]
        assert settled["random_stream_receipt"]["draw_count"] == 2
        assert await _call_raw(server, "combat_check", arguments) == settled

        cancelled_stream = CampaignRandomStream(
            campaign_id=campaign["id"],
            seed=stream.seed,
            position=stream.position,
            operation="combat_check",
            idempotency_key="cancelled-save",
        )
        with use_random_stream(cancelled_stream):
            cancelled = await _call_raw(
                server,
                "combat_check",
                {
                    **arguments,
                    "ability": "DEXTERITY",
                    "disadvantage": True,
                    "expected_revision": settled["campaign_revision"],
                    "idempotency_key": "cancelled-save",
                },
            )
        assert len(cancelled["result"]["rolls"]) == 1
        assert cancelled["random_stream_receipt"]["draw_count"] == 1

    asyncio.run(exercise())


def test_combat_stand_charges_half_current_effective_speed(tmp_path: Path) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "Effective-speed stand",
                "edition": "2014",
                "idempotency_key": "campaign",
            },
        )
        sheet = default_character_sheet()
        sheet["conditions"] = ["prone"]
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Slowed prone actor",
                    "sheet": sheet,
                },
                "principal_id": "system:local",
                "idempotency_key": "actor",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        await _call(
            server,
            "combat_start",
            {
                "positioning_mode": "agent",
                "campaign_id": campaign["id"],
                "participant_ids": [actor["id"]],
                "participant_config": [{"actor_id": actor["id"], "initiative": 10}],
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            },
        )

        storage = server_module.SagaSmithStorage(config)
        campaigns = server_module.CampaignService(storage.database)
        stored = campaigns.get(campaign["id"])
        state_with_effect = deepcopy(stored.state)
        affected = state_with_effect["combat"]["combatants"][0]
        assert affected["turn_budget"]["movement"] == 30
        affected["speed_multiplier"] = 0.5
        effect_applied = campaigns.update(
            campaign["id"],
            state=state_with_effect,
            expected_revision=stored.revision,
        )

        stand_request = {
            "campaign_id": campaign["id"],
            "actor_id": actor["id"],
            "action": "stand",
            "payload": {},
            "expected_revision": effect_applied.revision,
            "idempotency_key": "stand",
        }
        stood = await _call_raw(server, "combat_movement", stand_request)
        replayed = await _call_raw(server, "combat_movement", stand_request)

        assert replayed == stood
        assert stood["status"] == "committed"
        assert stood["campaign_revision"] == effect_applied.revision + 1
        combatant = stood["combat"]["combatants"][0]
        assert combatant["turn_budget"]["movement"] == 8
        assert combatant["turn_budget"]["movement_spent"] == 7
        assert "prone" not in combatant["conditions"]
        after = campaigns.get(campaign["id"])
        assert after.revision == effect_applied.revision + 1
        assert after.state["combat"]["combatants"][0]["turn_budget"]["movement"] == 8

    asyncio.run(exercise())


def test_zero_speed_dodge_ends_atomically_and_replays_without_writes(tmp_path: Path) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Dodge lifecycle", "edition": "2014", "idempotency_key": "campaign"},
        )
        sheet = default_character_sheet()
        sheet["conditions"] = ["grappled"]
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Grappled dodger",
                    "sheet": sheet,
                },
                "principal_id": "system:local",
                "idempotency_key": "actor",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        started = await _call(
            server,
            "combat_start",
            {
                "positioning_mode": "agent",
                "campaign_id": campaign["id"],
                "participant_ids": [actor["id"]],
                "participant_config": [{"actor_id": actor["id"], "initiative": 10}],
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            },
        )
        storage = server_module.SagaSmithStorage(config)
        campaigns = server_module.CampaignService(storage.database)
        before = campaigns.get(campaign["id"])
        stale_request = {
            "campaign_id": campaign["id"],
            "actor_id": actor["id"],
            "action": "dodge",
            "expected_revision": started["campaign_revision"] - 1,
            "idempotency_key": "stale-dodge",
        }
        with pytest.raises(Exception, match="campaign revision conflict"):
            await _call(server, "combat_common_action", stale_request)
        unchanged = campaigns.get(campaign["id"])
        assert unchanged.revision == before.revision
        assert unchanged.state == before.state

        request = {
            **stale_request,
            "expected_revision": started["campaign_revision"],
            "idempotency_key": "dodge",
        }
        dodged = await _call(server, "combat_common_action", request)
        replayed = await _call(server, "combat_common_action", request)

        assert replayed == dodged
        assert dodged["campaign_revision"] == started["campaign_revision"] + 1
        combatant = dodged["combat"]["combatants"][0]
        assert "dodging" not in combatant["turn_flags"]
        assert combatant["turn_flags"]["dodge_ended"] == {
            "mechanic_id": "dnd5e.core.action.dodge",
            "reason": "speed_zero",
        }
        assert dodged["combat"]["log"][-1]["type"] == "dodge_ended"
        committed = campaigns.get(campaign["id"])
        assert committed.revision == started["campaign_revision"] + 1

    asyncio.run(exercise())


@pytest.mark.parametrize("legacy_zero_kind", ["speed_multiplier", "grappled"])
def test_legacy_invalid_dodge_ends_before_projection_restores_speed(
    tmp_path: Path,
    legacy_zero_kind: str,
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Legacy Dodge upgrade", "edition": "2014", "idempotency_key": "campaign"},
        )
        sheet = default_character_sheet()
        sheet["combat"]["hp"] = {"value": 1, "max": 5, "temp": 0}
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Legacy dodger",
                    "sheet": sheet,
                },
                "principal_id": "system:local",
                "idempotency_key": "actor",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        await _call(
            server,
            "combat_start",
            {
                "positioning_mode": "agent",
                "campaign_id": campaign["id"],
                "participant_ids": [actor["id"]],
                "participant_config": [{"actor_id": actor["id"], "initiative": 10}],
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            },
        )
        storage = server_module.SagaSmithStorage(config)
        campaigns = server_module.CampaignService(storage.database)
        stored = campaigns.get(campaign["id"])
        legacy_state = deepcopy(stored.state)
        legacy_combatant = legacy_state["combat"]["combatants"][0]
        legacy_combatant.setdefault("turn_flags", {})["dodging"] = True
        if legacy_zero_kind == "speed_multiplier":
            legacy_combatant["speed_multiplier"] = 0.0
        else:
            legacy_combatant["conditions"] = ["grappled"]
        upgraded = campaigns.update(
            campaign["id"],
            state=legacy_state,
            expected_revision=stored.revision,
        )

        request = {
            "campaign_id": campaign["id"],
            "target_id": actor["id"],
            "action": "heal",
            "payload": {"amount": 1},
            "expected_revision": upgraded.revision,
            "idempotency_key": f"upgrade-{legacy_zero_kind}",
        }
        healed = await _call_raw(server, "combat_hp_change", request)
        replayed = await _call_raw(server, "combat_hp_change", request)

        assert replayed == healed
        combatant = healed["combat"]["combatants"][0]
        assert combatant["speed_multiplier"] == 1.0
        assert "grappled" not in combatant["conditions"]
        assert "dodging" not in combatant["turn_flags"]
        assert combatant["turn_flags"]["dodge_ended"]["reason"] == "speed_zero"
        ended = [
            item
            for item in healed["combat"]["log"]
            if item.get("type") == "dodge_ended" and item.get("actor_id") == actor["id"]
        ]
        assert len(ended) == 1
        assert ended[0]["reason"] == "speed_zero"
        committed = campaigns.get(campaign["id"])
        assert committed.revision == upgraded.revision + 1

    asyncio.run(exercise())


def test_mid_turn_slow_caps_public_movement_without_failed_writes(tmp_path: Path) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Mid-turn slow", "edition": "2014", "idempotency_key": "campaign"},
        )
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Slowed mover"},
                "principal_id": "system:local",
                "idempotency_key": "actor",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        started = await _call(
            server,
            "combat_start",
            {
                "positioning_mode": "grid",
                "battle_map": {"width_cells": 12, "height_cells": 4},
                "campaign_id": campaign["id"],
                "participant_ids": [actor["id"]],
                "participant_config": [
                    {
                        "actor_id": actor["id"],
                        "initiative": 10,
                        "position": {"x": 0, "y": 0},
                    }
                ],
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            },
        )
        storage = server_module.SagaSmithStorage(config)
        campaigns = server_module.CampaignService(storage.database)
        stored = campaigns.get(campaign["id"])
        state_with_effect = deepcopy(stored.state)
        affected = state_with_effect["combat"]["combatants"][0]
        affected["speed_multiplier"] = 0.5
        effect_applied = campaigns.update(
            campaign["id"], state=state_with_effect, expected_revision=stored.revision
        )
        before = campaigns.get(campaign["id"])

        stale = {
            "campaign_id": campaign["id"],
            "actor_id": actor["id"],
            "action": "move",
            "payload": {"distance": 20, "destination": {"x": 4, "y": 0}},
            "expected_revision": started["campaign_revision"],
            "idempotency_key": "stale-move",
        }
        with pytest.raises(Exception, match="campaign revision conflict"):
            await _call_raw(server, "combat_movement", stale)
        oversized = {
            **stale,
            "expected_revision": effect_applied.revision,
            "idempotency_key": "oversized-move",
        }
        for _attempt in range(2):
            with pytest.raises(Exception, match="remaining speed"):
                await _call_raw(server, "combat_movement", oversized)
        after_failed = campaigns.get(campaign["id"])
        assert after_failed.revision == before.revision
        assert after_failed.state == before.state

        valid = {
            **oversized,
            "payload": {"distance": 15, "destination": {"x": 3, "y": 0}},
            "idempotency_key": "valid-move",
        }
        moved = await _call_raw(server, "combat_movement", valid)
        replayed = await _call_raw(server, "combat_movement", valid)
        assert replayed == moved
        assert moved["campaign_revision"] == effect_applied.revision + 1
        budget = moved["combat"]["combatants"][0]["turn_budget"]
        assert budget["movement"] == 0
        assert budget["movement_spent"] == 15
        assert budget["extra_movement_granted"] == 0

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("speed_multiplier", "condition", "expected_movement"),
    [
        (0.0, None, 0),
        (0.5, None, 30),
        (1.0, "grappled", 0),
        (1.0, "restrained", 0),
    ],
)
def test_dash_grants_only_current_effective_speed(
    tmp_path: Path,
    speed_multiplier: float,
    condition: str | None,
    expected_movement: int,
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "Effective-speed Dash",
                "edition": "2014",
                "idempotency_key": "campaign",
            },
        )
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Dasher",
                    "sheet": default_character_sheet(),
                },
                "principal_id": "system:local",
                "idempotency_key": "actor",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        await _call(
            server,
            "combat_start",
            {
                "positioning_mode": "grid",
                "battle_map": {"width_cells": 12, "height_cells": 4},
                "campaign_id": campaign["id"],
                "participant_ids": [actor["id"]],
                "participant_config": [
                    {
                        "actor_id": actor["id"],
                        "initiative": 10,
                        "position": {"x": 0, "y": 0},
                    }
                ],
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            },
        )

        storage = server_module.SagaSmithStorage(config)
        campaigns = server_module.CampaignService(storage.database)
        stored = campaigns.get(campaign["id"])
        state_with_effect = deepcopy(stored.state)
        affected = state_with_effect["combat"]["combatants"][0]
        assert affected["turn_budget"]["movement"] == 30
        affected["speed_multiplier"] = speed_multiplier
        if condition is not None:
            affected["conditions"].append(condition)
        effect_applied = campaigns.update(
            campaign["id"],
            state=state_with_effect,
            expected_revision=stored.revision,
        )

        dash_request = {
            "campaign_id": campaign["id"],
            "actor_id": actor["id"],
            "action": "dash",
            "expected_revision": effect_applied.revision,
            "idempotency_key": "dash",
        }
        dashed = await _call(server, "combat_common_action", dash_request)
        replayed = await _call(server, "combat_common_action", dash_request)

        assert replayed == dashed
        assert dashed["status"] == "committed"
        assert dashed["campaign_revision"] == effect_applied.revision + 1
        combatant = dashed["combat"]["combatants"][0]
        assert combatant["turn_budget"]["movement"] == expected_movement
        assert combatant["turn_budget"]["main_action"] == 0
        assert dashed["combat"]["log"][-1]["action"] == "dash"
        after = campaigns.get(campaign["id"])
        assert after.revision == effect_applied.revision + 1
        assert after.state["combat"]["combatants"][0]["turn_budget"][
            "movement"
        ] == expected_movement
        if speed_multiplier == 0.5 and condition is None:
            state_at_zero_speed = deepcopy(after.state)
            state_at_zero_speed["combat"]["combatants"][0]["speed_multiplier"] = 0.0
            zero_speed = campaigns.update(
                campaign["id"],
                state=state_at_zero_speed,
                expected_revision=after.revision,
            )
            server = create_server(config)
            before_blocked = campaigns.get(campaign["id"])
            blocked = {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "action": "move",
                "payload": {"distance": 5, "destination": {"x": 1, "y": 0}},
                "expected_revision": zero_speed.revision,
                "idempotency_key": "blocked-after-dash",
            }
            with pytest.raises(Exception, match="effective speed is zero"):
                await _call_raw(server, "combat_movement", blocked)
            after_blocked = campaigns.get(campaign["id"])
            assert after_blocked.revision == before_blocked.revision
            assert after_blocked.state == before_blocked.state

    asyncio.run(exercise())


def test_invalid_branch_is_rejected_before_noncombat_check_rolls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rolled = False

    def forbidden_roll(*args, **kwargs):
        nonlocal rolled
        rolled = True
        raise AssertionError("the check must not roll")

    monkeypatch.setattr(server_module, "resolve_actor_check", forbidden_roll)

    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Branch guard", "edition": "2014", "idempotency_key": "campaign"},
        )
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Checker"},
                "principal_id": "system:local",
                "idempotency_key": "actor",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        await _call(
            server,
            "game_phase",
            {
                "campaign_id": campaign["id"],
                "action": "set",
                "tool_profile": "play",
                "expected_revision": campaign["revision"],
                "idempotency_key": "enter-play",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )

        with pytest.raises(Exception, match="checked-out branch"):
            await _call(
                server,
                "character_check",
                {
                    "campaign_id": campaign["id"],
                    "action": "check",
                    "payload": {
                        "actor_id": actor["id"],
                        "kind": "check",
                        "ability": "wisdom",
                        "dc": 10,
                    },
                    "branch_id": "not-the-current-branch",
                    "expected_revision": campaign["revision"],
                    "idempotency_key": "invalid-branch-check",
                },
            )

    asyncio.run(exercise())
    assert rolled is False


def test_jack_of_all_trades_is_applied_and_receipted_by_public_tools(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Jack audit", "edition": "2014", "idempotency_key": "campaign"},
        )
        sheet = default_character_sheet()
        sheet["progression"] = {
            "level": 2,
            "classes": [{"name": "Bard", "level": 2, "hit_die": 8}],
        }
        sheet["abilities"]["charisma"]["score"] = 16
        sheet["abilities"]["dexterity"]["score"] = 14
        sheet["content"]["features"] = [
            {
                "id": "dnd5e.content.srd2014.feature.bard-jack-of-all-trades",
                "name": "Jack of All Trades",
                "source_key": "Bard",
                "mechanic_refs": ["dnd5e.core.check.jack_of_all_trades"],
            }
        ]
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Bard", "sheet": sheet},
                "principal_id": "system:local",
                "idempotency_key": "actor",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        await _call(
            server,
            "game_phase",
            {
                "campaign_id": campaign["id"],
                "action": "set",
                "tool_profile": "play",
                "expected_revision": campaign["revision"],
                "idempotency_key": "enter-play",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        checked = await _call(
            server,
            "character_check",
            {
                "campaign_id": campaign["id"],
                "action": "check",
                "payload": {
                    "actor_id": actor["id"],
                    "kind": "check",
                    "ability": "intimidation",
                    "dc": 0,
                },
                "expected_revision": campaign["revision"],
                "idempotency_key": "untrained-check",
            },
        )
        assert checked["ability_modifier"] == 3
        assert checked["bonus"] == 1
        assert [item["mechanic_id"] for item in checked["rule_receipts"]] == [
            "dnd5e.core.check.jack_of_all_trades"
        ]

        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        started = await _call(
            server,
            "combat_start",
            {
                "positioning_mode": "agent",
                "campaign_id": campaign["id"],
                "participant_ids": [actor["id"]],
                "expected_revision": campaign["revision"],
                "idempotency_key": "combat-start",
            },
        )
        combatant = started["combat"]["combatants"][0]
        assert combatant["initiative_bonus"] == 3
        assert started["combat"]["rule_boundary_ids"] == ["dnd5e.core.check.jack_of_all_trades"]

        receipts = await _call(
            server,
            "campaign_rules",
            {
                "campaign_id": campaign["id"],
                "action": "receipts",
                "payload": {},
                "principal_id": "system:local",
            },
        )
        jack_receipts = [
            item
            for item in receipts
            if item["mechanic_id"] == "dnd5e.core.check.jack_of_all_trades"
        ]
        assert {item["receipt"]["event"] for item in jack_receipts} == {
            "check.resolve",
            "combat.start",
        }

    asyncio.run(exercise())


def test_action_surge_is_settled_without_a_manual_ruling(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Action Surge", "edition": "2014", "idempotency_key": "campaign"},
        )
        sheet = default_character_sheet()
        sheet["content"]["features"] = [
            {
                "id": "dnd5e.content.srd2014.feature.fighter-action-surge",
                "name": "Action Surge",
                "source_key": "Fighter",
                "description": "Take one additional action on your turn.",
                "uses": {
                    "label": "Action Surge",
                    "value": 1,
                    "max": 1,
                    "recovers_on": "short_rest",
                },
                "resource_key": "",
                "activation": {"type": "special", "cost": 0, "trigger": ""},
                "scaling": [],
                "choices": {"outcome": "take one additional action on this turn"},
            }
        ]
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Fighter", "sheet": sheet},
                "principal_id": "system:local",
                "idempotency_key": "actor",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        started = await _call_raw(
            server,
            "combat_start",
            {
                "positioning_mode": "agent",
                "campaign_id": campaign["id"],
                "participant_ids": [actor["id"]],
                "participant_config": [{"actor_id": actor["id"], "initiative": 10}],
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            },
        )
        surged = await _call_raw(
            server,
            "combat_use_activity",
            {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "activity_id": "dnd5e.content.srd2014.feature.fighter-action-surge",
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "surge",
            },
        )

        assert surged["status"] == "committed"
        assert surged["result"]["requires_ruling"] is False
        assert surged["result"]["core_effect"]["extra_actions_granted"] == 1
        current = surged["combat"]["combatants"][surged["combat"]["turn_index"]]
        assert current["turn_budget"]["extra_action"] == 1
        assert any(
            item["mechanic_id"] == "dnd5e.core.activity.action_surge"
            for item in surged["result"]["rule_receipts"]
        )
        actor_after = await _call(
            server,
            "character_query",
            {
                "view": "get",
                "payload": {"character_id": actor["id"]},
                "principal_id": "system:local",
            },
        )
        assert actor_after["sheet"]["content"]["features"][0]["uses"]["value"] == 0

    asyncio.run(exercise())


def test_recharge_weapon_use_is_committed_on_attack_declaration(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "Recharge weapon transaction",
                "edition": "2014",
                "idempotency_key": "campaign",
            },
        )
        sheet = default_character_sheet()
        sheet["inventory"]["items"] = [
            {
                "id": "breath-recharge-5-6",
                "name": "Breath (Recharge 5-6)",
                "kind": "weapon",
                "mechanics": {
                    "attack_type": "ranged",
                    "attack_ability": "dexterity",
                    "damage_formula": "1d6",
                    "damage_type": "fire",
                    "attack_bonus_override": 20,
                    "always_available": True,
                    "normal_range_ft": 30,
                    "long_range_ft": 30,
                    "recharge": {
                        "kind": "d6_turn_start",
                        "minimum": 5,
                        "maximum": 6,
                        "source_marker": "(Recharge 5-6)",
                    },
                },
                "uses": {
                    "label": "Breath (Recharge 5-6)",
                    "value": 1,
                    "max": 1,
                    "recovers_on": "manual",
                    "source_key": "test:recharge",
                },
            }
        ]
        attacker = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Recharge attacker",
                    "sheet": sheet,
                },
                "principal_id": "system:local",
                "idempotency_key": "attacker",
            },
        )
        target = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Target"},
                "principal_id": "system:local",
                "idempotency_key": "target",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        started = await _call_raw(
            server,
            "combat_start",
            {
                "positioning_mode": "grid",
                "battle_map": {"width_cells": 12, "height_cells": 12},
                "campaign_id": campaign["id"],
                "participant_ids": [attacker["id"], target["id"]],
                "participant_config": [
                    {
                        "actor_id": attacker["id"],
                        "initiative": 20,
                        "position": {"x": 0, "y": 0},
                    },
                    {
                        "actor_id": target["id"],
                        "initiative": 10,
                        "position": {"x": 1, "y": 0},
                    },
                ],
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            },
        )

        attacked = await _call_raw(
            server,
            "combat_resolve_attack",
            {
                "campaign_id": campaign["id"],
                "actor_id": attacker["id"],
                "target_id": target["id"],
                "action": {
                    "weapon_id": "breath-recharge-5-6",
                    "attack_mode": "ranged",
                },
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "attack",
            },
        )
        after = await _call(
            server,
            "character_query",
            {
                "view": "get",
                "payload": {"character_id": attacker["id"]},
                "principal_id": "system:local",
            },
        )

        assert attacked["result"]["limited_use"]["remaining"] == 0
        assert after["sheet"]["inventory"]["items"][0]["uses"]["value"] == 0

    asyncio.run(exercise())


def test_locked_dragonborn_breath_uses_generic_area_and_save_primitives(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "Dragonborn Breath Weapon",
                "edition": "2014",
                "idempotency_key": "campaign",
            },
        )
        activity_id = "dnd5e.content.standard2014.species.dragonborn.activity.breath-weapon"
        source_sheet = default_character_sheet()
        source_sheet["progression"]["level"] = 1
        source_sheet["content"]["activities"] = [
            {
                "id": activity_id,
                "name": "Breath Weapon",
                "source_key": "Dragonborn",
                "description": "Reviewed standard Breath Weapon settlement.",
                "uses": {
                    "label": "Breath Weapon",
                    "value": 1,
                    "max": 1,
                    "recovers_on": "short_rest",
                    "source_key": "Dragonborn",
                },
                "activation": {"type": "action", "cost": 1},
                "choices": {
                    "standard_resolution": {
                        "kind": "area_save_damage",
                        "origin": {"kind": "self"},
                        "area": {"shape": "cone", "length_ft": 15},
                        "targets": "each_creature",
                        "save_ability": "dexterity",
                        "save_dc_formula": {
                            "base": 8,
                            "ability": "constitution",
                            "include_proficiency": True,
                        },
                        "damage_formula_by_level": {
                            "1": "2d6",
                            "6": "3d6",
                            "11": "4d6",
                            "16": "5d6",
                        },
                        "damage_type": "fire",
                        "half_on_success": True,
                        "save_source_kind": "nonmagical_effect",
                        "source_excerpt": "Reviewed standard Breath Weapon settlement.",
                    }
                },
                "pack_id": "dnd5e.content.standard2014",
                "pack_version": "1.4.0",
                "rule_refs": ["book:players-handbook-2014:p34"],
                "mechanic_refs": ["dnd5e.core.activity.dragonborn_breath_weapon"],
            }
        ]
        target_sheet = default_character_sheet()
        target_sheet["combat"]["hp"] = {"value": 30, "max": 30, "temp": 0}
        source = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Dragonborn",
                    "sheet": source_sheet,
                },
                "principal_id": "system:local",
                "idempotency_key": "source",
            },
        )
        target = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Target", "sheet": target_sheet},
                "principal_id": "system:local",
                "idempotency_key": "target",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        started = await _call_raw(
            server,
            "combat_start",
            {
                "positioning_mode": "grid",
                "battle_map": {"width_cells": 12, "height_cells": 12},
                "campaign_id": campaign["id"],
                "participant_ids": [source["id"], target["id"]],
                "participant_config": [
                    {
                        "actor_id": source["id"],
                        "initiative": 10,
                        "position": {"x": 0, "y": 0},
                    },
                    {
                        "actor_id": target["id"],
                        "initiative": 20,
                        "position": {"x": 2, "y": 0},
                    },
                ],
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            },
        )
        dodged = await _call_raw(
            server,
            "combat_common_action",
            {
                "campaign_id": campaign["id"],
                "actor_id": target["id"],
                "action": "dodge",
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "target-dodge",
            },
        )
        advanced = await _call_raw(
            server,
            "combat_end_turn",
            {
                "campaign_id": campaign["id"],
                "actor_id": target["id"],
                "expected_revision": dodged["campaign_revision"],
                "idempotency_key": "target-end",
            },
        )

        used = await _call_raw(
            server,
            "combat_use_activity",
            {
                "campaign_id": campaign["id"],
                "actor_id": source["id"],
                "activity_id": activity_id,
                "declaration": {
                    "origin": {"x": 1, "y": 0},
                    "target_contexts": [{"target_id": target["id"], "cover": "none"}],
                },
                "expected_revision": advanced["campaign_revision"],
                "idempotency_key": "breath",
            },
        )
        source_after = await _call(
            server,
            "character_query",
            {
                "view": "get",
                "payload": {"character_id": source["id"]},
                "principal_id": "system:local",
            },
        )
        target_after = await _call(
            server,
            "character_query",
            {
                "view": "get",
                "payload": {"character_id": target["id"]},
                "principal_id": "system:local",
            },
        )

        effect = used["result"]["core_effect"]
        assert used["status"] == "committed"
        assert effect["kind"] == "dragonborn_breath_weapon"
        assert effect["damage_expression"] == "2d6"
        assert effect["target_contexts"] == [
            {"target_id": target["id"], "distance_ft": 10.0, "cover": "none"}
        ]
        assert len(effect["targets"][0]["save"]["rolls"]) == 2
        assert [
            receipt["mechanic_id"]
            for receipt in effect["targets"][0]["save"]["rule_receipts"]
        ] == ["dnd5e.core.action.dodge"]
        assert source_after["sheet"]["content"]["activities"][0]["uses"]["value"] == 0
        assert target_after["sheet"]["combat"]["hp"]["value"] < 30

    asyncio.run(exercise())


@pytest.mark.parametrize("edition", ["2014", "2024"])
def test_second_wind_heals_and_pays_bonus_action_atomically(
    tmp_path: Path,
    edition: str,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Second Wind", "edition": edition, "idempotency_key": "campaign"},
        )
        sheet = default_character_sheet()
        sheet["edition"] = edition
        sheet["progression"]["level"] = 2
        sheet["progression"]["classes"] = [
            {"name": "Fighter", "level": 2, "subclass": "", "hit_die": 10}
        ]
        sheet["combat"]["hp"] = {"value": 1, "max": 20, "temp": 0}
        second_wind_id = f"dnd5e.content.srd{edition}.feature.fighter-second-wind"
        if edition == "2024":
            sheet["resources"]["second_wind"] = {
                "label": "Second Wind",
                "value": 2,
                "max": 2,
                "recovers_on": "long_rest",
                "recovery_amounts": {"short_rest": 1, "long_rest": "all"},
                "source_key": "Fighter",
            }
        feature = {
            "id": second_wind_id,
            "name": "Second Wind",
            "source_key": "Fighter",
            "description": "Regain 1d10 + Fighter level hit points.",
            **(
                {
                    "uses": {
                        "label": "Second Wind",
                        "value": 1,
                        "max": 1,
                        "recovers_on": "short_rest",
                    }
                }
                if edition == "2014"
                else {}
            ),
            "resource_key": "second_wind" if edition == "2024" else "",
            "activation": {"type": "bonus_action", "cost": 1, "trigger": ""},
            "scaling": [],
            "choices": {"outcome": "roll 1d10 + fighter level"},
            "mechanic_refs": ["dnd5e.core.activity.second_wind"],
        }
        sheet["content"]["features"] = [feature]
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Fighter", "sheet": sheet},
                "principal_id": "system:local",
                "idempotency_key": "actor",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        started = await _call_raw(
            server,
            "combat_start",
            {
                "positioning_mode": "agent",
                "campaign_id": campaign["id"],
                "participant_ids": [actor["id"]],
                "participant_config": [{"actor_id": actor["id"], "initiative": 10}],
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            },
        )

        result = await _call_raw(
            server,
            "combat_use_activity",
            {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "activity_id": second_wind_id,
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "second-wind",
            },
        )

        assert result["status"] == "committed"
        assert result["result"]["requires_ruling"] is False
        effect = result["result"]["core_effect"]
        assert effect["kind"] == "second_wind"
        assert effect["fighter_level"] == 2
        assert 4 <= effect["after_hp"] <= 13
        current = result["combat"]["combatants"][result["combat"]["turn_index"]]
        assert current["turn_budget"]["bonus_action"] == 0
        actor_after = await _call(
            server,
            "character_query",
            {
                "view": "get",
                "payload": {"character_id": actor["id"]},
                "principal_id": "system:local",
            },
        )
        assert actor_after["sheet"]["combat"]["hp"]["value"] == effect["after_hp"]
        if edition == "2014":
            assert actor_after["sheet"]["content"]["features"][0]["uses"]["value"] == 0
        else:
            assert actor_after["sheet"]["resources"]["second_wind"]["value"] == 1
        assert any(
            item["mechanic_id"] == "dnd5e.core.activity.second_wind"
            for item in result["result"]["rule_receipts"]
        )

    asyncio.run(exercise())


def test_second_wind_heals_and_advances_random_stream_outside_combat(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "Noncombat Second Wind",
                "edition": "2014",
                "random_seed": "noncombat-second-wind",
                "idempotency_key": "campaign",
            },
        )
        sheet = default_character_sheet()
        sheet["progression"]["level"] = 1
        sheet["progression"]["classes"] = [
            {"name": "Fighter", "level": 1, "subclass": "", "hit_die": 10}
        ]
        sheet["combat"]["hp"] = {"value": 2, "max": 12, "temp": 0}
        sheet["content"]["features"] = [
            {
                "id": "dnd5e.content.srd2014.feature.fighter-second-wind",
                "name": "Second Wind",
                "source_key": "Fighter",
                "description": "Regain 1d10 + Fighter level hit points.",
                "uses": {
                    "label": "Second Wind",
                    "value": 1,
                    "max": 1,
                    "recovers_on": "short_rest",
                },
                "resource_key": "",
                "activation": {"type": "bonus_action", "cost": 1, "trigger": ""},
                "scaling": [],
                "choices": {"outcome": "roll 1d10 + fighter level"},
            }
        ]
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Fighter", "sheet": sheet},
                "principal_id": "system:local",
                "idempotency_key": "actor",
            },
        )
        arguments = {
            "character_id": actor["id"],
            "activity_id": "dnd5e.content.srd2014.feature.fighter-second-wind",
            "expected_revision": actor["revision"],
            "idempotency_key": "second-wind",
        }

        before = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        stream = server_module.CampaignRandomStream.from_campaign_state(
            campaign["id"],
            before["state"],
            operation="character_action",
            idempotency_key="second-wind",
        )
        with server_module.use_random_stream(stream):
            result = await _call_raw(
                server,
                "character_action",
                {
                    "character_id": arguments["character_id"],
                    "action": "use_activity",
                    "payload": {"activity_id": arguments["activity_id"]},
                    "expected_revision": arguments["expected_revision"],
                    "idempotency_key": arguments["idempotency_key"],
                },
            )
        assert stream.has_unpersisted_draws is False
        assert (
            await _call_raw(
                server,
                "character_action",
                {
                    "character_id": actor["id"],
                    "action": "use_activity",
                    "payload": {"activity_id": "dnd5e.content.srd2014.feature.fighter-second-wind"},
                    "expected_revision": actor["revision"],
                    "idempotency_key": "second-wind",
                },
            )
            == result
        )
        assert result["status"] == "committed"
        effect = result["result"]["core_effect"]
        assert effect["kind"] == "second_wind"
        assert effect["fighter_level"] == 1
        assert 4 <= effect["after_hp"] <= 12
        assert effect["after_hp"] == result["character"]["sheet"]["combat"]["hp"]["value"]
        assert result["character"]["sheet"]["content"]["features"][0]["uses"]["value"] == 0
        assert any(
            item["mechanic_id"] == "dnd5e.core.activity.second_wind"
            for item in result["result"]["rule_receipts"]
        )
        current = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        assert current["state"]["random_stream"]["position"] == 1

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("edition", "speed_multiplier", "expected_movement"),
    [
        ("2014", 0.5, 30),
        ("2014", 0.0, 0),
        ("2024", 0.5, 30),
        ("2024", 0.0, 0),
    ],
)
def test_cunning_action_dash_uses_bonus_action_and_current_effective_speed(
    tmp_path: Path,
    edition: str,
    speed_multiplier: float,
    expected_movement: int,
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        activity_id = f"dnd5e.content.srd{edition}.feature.rogue-cunning-action"
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Cunning Action", "edition": edition, "idempotency_key": "campaign"},
        )
        sheet = default_character_sheet()
        sheet["progression"]["level"] = 2
        sheet["progression"]["classes"] = [
            {"name": "Rogue", "level": 2, "subclass": "", "hit_die": 8}
        ]
        sheet["content"]["features"] = [
            {
                "id": activity_id,
                "name": "Cunning Action",
                "source_key": "Rogue",
                "description": "Dash, Disengage, or Hide as a bonus action.",
                "uses": {
                    "label": "",
                    "value": 0,
                    "max": 0,
                    "unlimited": True,
                    "recovers_on": "none",
                },
                "resource_key": "",
                "activation": {"type": "bonus_action", "cost": 1, "trigger": ""},
                "scaling": [],
                "choices": {"options": ["Dash", "Disengage", "Hide"]},
            }
        ]
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Rogue", "sheet": sheet},
                "principal_id": "system:local",
                "idempotency_key": "actor",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        await _call_raw(
            server,
            "combat_start",
            {
                "positioning_mode": "grid",
                "battle_map": {"width_cells": 12, "height_cells": 4},
                "campaign_id": campaign["id"],
                "participant_ids": [actor["id"]],
                "participant_config": [
                    {
                        "actor_id": actor["id"],
                        "initiative": 10,
                        "position": {"x": 0, "y": 0},
                    }
                ],
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            },
        )

        storage = server_module.SagaSmithStorage(config)
        campaigns = server_module.CampaignService(storage.database)
        stored = campaigns.get(campaign["id"])
        state_with_effect = deepcopy(stored.state)
        affected = state_with_effect["combat"]["combatants"][0]
        assert affected["turn_budget"]["movement"] == 30
        affected["speed_multiplier"] = speed_multiplier
        effect_applied = campaigns.update(
            campaign["id"],
            state=state_with_effect,
            expected_revision=stored.revision,
        )

        result = await _call_raw(
            server,
            "combat_use_activity",
            {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "activity_id": activity_id,
                "declaration": {"action": "dash"},
                "expected_revision": effect_applied.revision,
                "idempotency_key": "cunning-dash",
            },
        )

        assert result["status"] == "committed"
        assert result["result"]["requires_ruling"] is False
        current = result["combat"]["combatants"][result["combat"]["turn_index"]]
        assert current["turn_budget"]["movement"] == expected_movement
        assert current["turn_budget"]["bonus_action"] == 0
        assert current["turn_budget"]["main_action"] == 1
        assert any(
            item["mechanic_id"] == "dnd5e.core.activity.cunning_action"
            for item in result["result"]["rule_receipts"]
        )
        if speed_multiplier == 0.5:
            after_dash = campaigns.get(campaign["id"])
            state_at_zero_speed = deepcopy(after_dash.state)
            state_at_zero_speed["combat"]["combatants"][0]["speed_multiplier"] = 0.0
            zero_speed = campaigns.update(
                campaign["id"],
                state=state_at_zero_speed,
                expected_revision=after_dash.revision,
            )
            server = create_server(config)
            before_blocked = campaigns.get(campaign["id"])
            blocked = {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "action": "move",
                "payload": {"distance": 5, "destination": {"x": 1, "y": 0}},
                "expected_revision": zero_speed.revision,
                "idempotency_key": "blocked-after-cunning-dash",
            }
            with pytest.raises(Exception, match="effective speed is zero"):
                await _call_raw(server, "combat_movement", blocked)
            after_blocked = campaigns.get(campaign["id"])
            assert after_blocked.revision == before_blocked.revision
            assert after_blocked.state == before_blocked.state

    asyncio.run(exercise())


def test_srd_orc_preset_aggressive_settles_and_spends_a_separate_grant(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        config = McpConfig(
            home=tmp_path / "home",
            database_url=None,
            chroma_url=None,
            chroma_path_override=None,
            dnd_skills_dir=Path(__file__).resolve().parents[3] / "skills",
            modulegen_skills_dir=tmp_path / "modulegen",
            auto_seed_rules=False,
        )
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Orc Aggressive", "edition": "2014", "idempotency_key": "campaign"},
        )
        orc_card = next(
            item
            for item in build_srd2014_preset_actors(config.dnd_skills_dir)
            if item["id"] == "dnd5e.presets.srd2014.actor.orc"
        )
        orc = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": orc_card["name"],
                    "character_type": orc_card["actor_type"],
                    "sheet": orc_card["sheet"],
                    "notes": orc_card["notes"],
                },
                "principal_id": "system:local",
                "idempotency_key": "orc",
            },
        )
        aggressive = next(
            item for item in orc["sheet"]["content"]["activities"] if item["name"] == "Aggressive"
        )
        assert aggressive["activation"]["type"] == "bonus_action"
        hostile = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Hostile"},
                "principal_id": "system:local",
                "idempotency_key": "hostile",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        await _call(
            server,
            "game_phase",
            {
                "campaign_id": campaign["id"],
                "action": "set",
                "tool_profile": "play",
                "expected_revision": campaign["revision"],
                "idempotency_key": "phase",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        await _call_raw(
            server,
            "combat_start",
            {
                "positioning_mode": "grid",
                "battle_map": {"width_cells": 12, "height_cells": 12},
                "campaign_id": campaign["id"],
                "participant_ids": [orc["id"], hostile["id"]],
                "participant_config": [
                    {
                        "actor_id": orc["id"],
                        "initiative": 20,
                        "position": {"x": 2, "y": 2},
                        "disposition": "friendly",
                    },
                    {
                        "actor_id": hostile["id"],
                        "initiative": 10,
                        "position": {"x": 8, "y": 2},
                        "disposition": "hostile",
                    },
                ],
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            },
        )
        storage = server_module.SagaSmithStorage(config)
        campaigns = server_module.CampaignService(storage.database)
        stored = campaigns.get(campaign["id"])
        state_with_effect = deepcopy(stored.state)
        affected = next(
            item
            for item in state_with_effect["combat"]["combatants"]
            if item["actor_id"] == orc["id"]
        )
        affected["speed_multiplier"] = 0.5
        effect_applied = campaigns.update(
            campaign["id"],
            state=state_with_effect,
            expected_revision=stored.revision,
        )
        activated = await _call_raw(
            server,
            "combat_use_activity",
            {
                "campaign_id": campaign["id"],
                "actor_id": orc["id"],
                "activity_id": aggressive["id"],
                "declaration": {"target_id": hostile["id"]},
                "expected_revision": effect_applied.revision,
                "idempotency_key": "aggressive",
            },
        )
        assert activated["status"] == "committed"
        assert activated["result"]["core_effect"] == {
            "kind": "orc_aggressive",
            "target_id": hostile["id"],
            "movement_granted": 15,
            "movement_remaining": 15,
            "requires_ruling": False,
        }
        assert any(
            item["mechanic_id"] == "dnd5e.core.activity.orc_aggressive"
            for item in activated["result"]["rule_receipts"]
        )

        with pytest.raises(Exception, match="every Aggressive movement segment"):
            await _call_raw(
                server,
                "combat_movement",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": orc["id"],
                    "action": "move",
                    "payload": {
                        "distance": 5,
                        "destination": {"x": 1, "y": 2},
                        "path": [{"x": 2, "y": 2}, {"x": 1, "y": 2}],
                        "movement_mode": "aggressive",
                    },
                    "expected_revision": activated["campaign_revision"],
                    "idempotency_key": "away",
                },
            )

        moved = await _call_raw(
            server,
            "combat_movement",
            {
                "campaign_id": campaign["id"],
                "actor_id": orc["id"],
                "action": "move",
                "payload": {
                    "distance": 10,
                    "destination": {"x": 4, "y": 2},
                    "path": [
                        {"x": 2, "y": 2},
                        {"x": 3, "y": 2},
                        {"x": 4, "y": 2},
                    ],
                    "movement_mode": "aggressive",
                },
                "expected_revision": activated["campaign_revision"],
                "idempotency_key": "toward",
            },
        )
        current = moved["combat"]["combatants"][moved["combat"]["turn_index"]]
        assert current["position"] == {"x": 4, "y": 2}
        assert current["turn_budget"]["movement"] == 30
        assert current["turn_flags"]["aggressive_movement"]["remaining"] == 5
        assert any(
            item["mechanic_id"] == "dnd5e.core.activity.orc_aggressive"
            for item in moved["rule_receipts"]
        )

    asyncio.run(exercise())


def test_combat_move_charges_reviewed_difficult_cells_and_records_core_receipt(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Difficult terrain", "edition": "2014", "idempotency_key": "campaign"},
        )
        mover = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Mover"},
                "principal_id": "system:local",
                "idempotency_key": "mover",
            },
        )
        other = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Other"},
                "principal_id": "system:local",
                "idempotency_key": "other",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        started = await _call_raw(
            server,
            "combat_start",
            {
                "positioning_mode": "grid",
                "campaign_id": campaign["id"],
                "participant_ids": [mover["id"], other["id"]],
                "participant_config": [
                    {
                        "actor_id": mover["id"],
                        "initiative": 20,
                        "position": {"x": 0, "y": 0},
                        "hidden": True,
                        "visible_to_actor_ids": [mover["id"]],
                    },
                    {
                        "actor_id": other["id"],
                        "initiative": 10,
                        "position": {"x": 4, "y": 0},
                    },
                ],
                "battle_map": {
                    "width_cells": 6,
                    "height_cells": 4,
                    "difficult_cells": [{"x": 1, "y": 0}],
                },
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            },
        )

        pending = await _call(
            server,
            "combat_movement",
            {
                "campaign_id": campaign["id"],
                "actor_id": mover["id"],
                "action": "move",
                "payload": {"distance": 10, "destination": {"x": 2, "y": 0}},
                "principal_id": "system:local",
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "move-without-path",
            },
        )

        assert pending["status"] == "pending_ruling"
        assert pending["default_resolver"] == "agent"
        assert pending["committed"] is False
        assert pending["missing"] == ["movement_path_for_difficult_terrain"]

        moved = await _call_raw(
            server,
            "combat_movement",
            {
                "campaign_id": campaign["id"],
                "actor_id": mover["id"],
                "action": "move",
                "payload": {
                    "distance": 10,
                    "destination": {"x": 2, "y": 0},
                    "path": [{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 2, "y": 0}],
                },
                "principal_id": "system:local",
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "move",
            },
        )

        current = moved["combat"]["combatants"][moved["combat"]["turn_index"]]
        assert current["turn_budget"]["movement"] == 15
        receipts = await _call(
            server,
            "campaign_rules",
            {
                "campaign_id": campaign["id"],
                "action": "receipts",
                "payload": {},
                "principal_id": "system:local",
            },
        )
        assert any(
            item["mechanic_id"] == "dnd5e.core.movement.difficult_terrain" for item in receipts
        )
        revealed = await _call_raw(
            server,
            "combat_map_patch",
            {
                "campaign_id": campaign["id"],
                "patches": [
                    {
                        "key": "combatant_visibility",
                        "value": {
                            "actor_id": mover["id"],
                            "hidden": False,
                            "visible_to_actor_ids": None,
                            "reason": "The hidden actor shouted from its new position.",
                        },
                    }
                ],
                "expected_revision": moved["campaign_revision"],
                "idempotency_key": "reveal",
            },
        )
        mover_after = next(
            item for item in revealed["combat"]["combatants"] if item["actor_id"] == mover["id"]
        )
        assert mover_after["hidden"] is False
        assert mover_after["visible_to_actor_ids"] is None
        assert revealed["campaign_revision"] == moved["campaign_revision"] + 1
        departed = await _call_raw(
            server,
            "combat_map_patch",
            {
                "campaign_id": campaign["id"],
                "patches": [
                    {
                        "key": "combatant_departure",
                        "value": {
                            "actor_id": other["id"],
                            "reason": "The source says one guard flees to warn the leader.",
                            "destination_location_key": "8-klarg-s-cave",
                        },
                    }
                ],
                "expected_revision": revealed["campaign_revision"],
                "idempotency_key": "source-departure",
            },
        )
        other_after = next(
            item for item in departed["combat"]["combatants"] if item["actor_id"] == other["id"]
        )
        assert other_after["departed"] == {
            "reason": "The source says one guard flees to warn the leader.",
            "destination_location_key": "8-klarg-s-cave",
        }
        assert other_after["hidden"] is True
        assert departed["world_patches"] == [
            {
                "key": "combatant_departure",
                "value": {
                    "actor_id": other["id"],
                    "reason": "The source says one guard flees to warn the leader.",
                    "destination_location_key": "8-klarg-s-cave",
                },
            }
        ]

    asyncio.run(exercise())


def test_forced_and_teleport_movement_are_off_turn_effect_position_changes(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Effect movement", "edition": "2014", "idempotency_key": "campaign"},
        )
        controller = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Controller"},
                "principal_id": "system:local",
                "idempotency_key": "controller",
            },
        )
        target_sheet = default_character_sheet()
        target_sheet["conditions"] = ["restrained", "prone"]
        target = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Target",
                    "sheet": target_sheet,
                },
                "principal_id": "system:local",
                "idempotency_key": "target",
            },
        )
        campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        started = await _call_raw(
            server,
            "combat_start",
            {
                "positioning_mode": "grid",
                "campaign_id": campaign["id"],
                "participant_ids": [controller["id"], target["id"]],
                "participant_config": [
                    {
                        "actor_id": controller["id"],
                        "initiative": 20,
                        "position": {"x": 0, "y": 0},
                        "disposition": "hostile",
                    },
                    {
                        "actor_id": target["id"],
                        "initiative": 10,
                        "position": {"x": 1, "y": 0},
                        "disposition": "friendly",
                    },
                ],
                "battle_map": {"width_cells": 12, "height_cells": 4},
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            },
        )
        target_before = next(
            item for item in started["combat"]["combatants"] if item["actor_id"] == target["id"]
        )
        movement_before = target_before["turn_budget"]["movement"]

        forced = await _call_raw(
            server,
            "combat_movement",
            {
                "campaign_id": campaign["id"],
                "actor_id": target["id"],
                "action": "move",
                "payload": {
                    "distance": 5,
                    "destination": {"x": 2, "y": 0},
                    "movement_mode": "forced",
                },
                "principal_id": "system:local",
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "forced",
            },
        )
        forced_target = next(
            item for item in forced["combat"]["combatants"] if item["actor_id"] == target["id"]
        )
        assert forced_target["position"] == {"x": 2, "y": 0}
        assert forced_target["turn_budget"]["movement"] == movement_before
        assert forced["combat"]["pending"] == []

        teleported = await _call_raw(
            server,
            "combat_movement",
            {
                "campaign_id": campaign["id"],
                "actor_id": target["id"],
                "action": "move",
                "payload": {
                    "distance": 40,
                    "destination": {"x": 10, "y": 0},
                    "movement_mode": "teleport",
                },
                "principal_id": "system:local",
                "expected_revision": forced["campaign_revision"],
                "idempotency_key": "teleport",
            },
        )
        teleported_target = next(
            item for item in teleported["combat"]["combatants"] if item["actor_id"] == target["id"]
        )
        assert teleported_target["position"] == {"x": 10, "y": 0}
        assert teleported_target["turn_budget"]["movement"] == movement_before
        assert teleported["combat"]["pending"] == []
        receipts = await _call(
            server,
            "campaign_rules",
            {
                "campaign_id": campaign["id"],
                "action": "receipts",
                "payload": {},
                "principal_id": "system:local",
            },
        )
        assert any(
            item["mechanic_id"] == "dnd5e.core.movement.forced_and_teleport" for item in receipts
        )

    asyncio.run(exercise())
