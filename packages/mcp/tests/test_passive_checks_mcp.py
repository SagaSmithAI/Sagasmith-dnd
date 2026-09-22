"""Public scene passive checks preserve authority, secrecy and retry receipts."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_core.idempotency import IdempotencyService
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd_runtime.application_support import StateMutationService

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import finalize_and_activate_module


async def call(server, name, arguments):
    _, result = await server.call_tool(name, arguments)
    # Keep write envelopes intact: their result is the check, not the operation.
    return result


async def read(server, name, arguments):
    result = await call(server, name, arguments)
    return result.get("result", result)


def test_passive_scene_checks_are_atomic_secret_and_replay_after_restart(tmp_path, monkeypatch):
    source = tmp_path / "passive-tower.md"
    excerpt = "The concealed needle trap can be noticed with a DC 14 Wisdom (Perception) check."
    source.write_text(f"# Tower\n\n## Needle Trap\n\n{excerpt}\n", encoding="utf-8")
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=Path(__file__).resolve().parents[3] / "skills",
        modulegen_skills_dir=tmp_path / "modulegen",
        module_import_roots=(tmp_path,),
        auto_seed_rules=False,
    )

    async def exercise():
        server = create_server(config)
        try:
            campaign = await read(
                server,
                "campaign_create",
                {
                    "name": "Passive tower",
                    "edition": "2014",
                    "idempotency_key": "create",
                },
            )
            campaign_id = campaign["id"]
            staged = await read(
                server,
                "module_draft",
                {
                    "campaign_id": campaign_id,
                    "action": "start",
                    "idempotency_key": "draft",
                    "payload": {
                        "source_path": str(source),
                        "source_key": "passive-tower",
                        "title": "Tower",
                    },
                },
            )

            async def helper_call(server, name, arguments):
                value = await read(server, name, arguments)
                if isinstance(value, dict) and "action" in value and "result" in value:
                    return value["result"]
                return value

            await finalize_and_activate_module(
                helper_call,
                server,
                campaign_id,
                staged,
                source_key="passive-tower",
                title="Tower",
                portable_id="dnd5e.module.passive-tower",
            )
            hits = await read(
                server,
                "module_search",
                {
                    "campaign_id": campaign_id,
                    "query": "concealed needle trap",
                    "top_k": 3,
                },
            )
            expanded = await read(server, "module_expand", {"chunk_id": hits[0]["id"]})
            sheet = default_character_sheet()
            sheet["abilities"]["wisdom"]["score"] = 14
            sheet["abilities"]["intelligence"]["score"] = 18
            sheet["skills"]["perception"]["proficiency"] = "expertise"
            actor = await read(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign_id, "name": "Scout", "sheet": sheet},
                    "idempotency_key": "scout",
                },
            )
            other = await read(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign_id, "name": "Other", "sheet": sheet},
                    "idempotency_key": "other",
                },
            )
            blind_sheet = deepcopy(sheet)
            blind_sheet["conditions"] = ["blinded"]
            blind = await read(server, "character_create_from", {
                "mode": "direct", "payload": {"campaign_id": campaign_id, "name": "Blinded",
                                               "sheet": blind_sheet}, "idempotency_key": "blind",
            })
            for scope, payload in (
                ("campaign", {"role": "player"}),
                ("actor", {"actor_id": actor["id"], "can_control": True, "can_view_private": True}),
            ):
                await call(
                    server,
                    "access_grant",
                    {
                        "scope": scope,
                        "campaign_id": campaign_id,
                        "principal_id": "user:player",
                        "by_principal_id": "system:local",
                        "payload": payload,
                    },
                )

            async def snapshot(principal="system:local"):
                return await read(
                    server,
                    "campaign_query",
                    {
                        "view": "get",
                        "payload": {"campaign_id": campaign_id},
                        "principal_id": principal,
                    },
                )

            async def actor_snapshot():
                return await read(
                    server,
                    "character_query",
                    {
                        "view": "get",
                        "payload": {"character_id": actor["id"]},
                    },
                )

            current = await snapshot()
            await call(
                server,
                "game_phase",
                {
                    "campaign_id": campaign_id,
                    "action": "set",
                    "tool_profile": "play",
                    "expected_revision": current["revision"],
                    "idempotency_key": "play",
                },
            )
            before = await snapshot()
            before_actor = await actor_snapshot()
            task = {
                "mode": "trap_detection",
                "source_ref": expanded["source_ref"],
                "source_excerpt": excerpt,
                "reason": "Notice the trap while exploring.",
                "dc": 14,
            }
            args = {
                "campaign_id": campaign_id,
                "action": "passive",
                "payload": {
                    "actor_id": actor["id"],
                    "ability": "perception",
                    "task": task,
                },
                "expected_revision": before["revision"],
                "idempotency_key": "notice",
            }
            # Reject client totals, flags, skill grants, stale writes and forged sources.
            for index, patch in enumerate(
                (
                    {"total": 99},
                    {"proficient": True},
                    {"bonus": 8},
                    {"advantage": True},
                    {"task": {**task, "source_excerpt": "This invented trap does not exist."}},
                )
            ):
                with pytest.raises(ToolError):
                    await call(
                        server,
                        "character_check",
                        {
                            **args,
                            "payload": {**args["payload"], **patch},
                            "idempotency_key": f"invalid-{index}",
                        },
                    )
            with pytest.raises(ToolError, match="revision conflict"):
                await call(server, "character_check", {**args, "expected_revision": 0})
            with pytest.raises(ToolError):
                await call(server, "character_check", {**args, "principal_id": "user:player"})
            assert await snapshot() == before

            remember = IdempotencyService.remember_write_in_session
            injected = False

            def fail_receipt(self, session, **kwargs):
                nonlocal injected
                if kwargs["key"] == "notice":
                    injected = True
                    raise RuntimeError("passive receipt failure")
                return remember(self, session, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(IdempotencyService, "remember_write_in_session", fail_receipt)
                with pytest.raises(ToolError):
                    await call(server, "character_check", args)
            assert injected
            assert await snapshot() == before
            assert await actor_snapshot() == before_actor

            original_commit = StateMutationService.replace

            def stale_actor_read(self, *positional, **kwargs):
                kwargs["character_updates"] = [
                    replace(update, expected_revision=update.expected_revision - 1)
                    for update in kwargs["character_updates"]
                ]
                return original_commit(self, *positional, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(StateMutationService, "replace", stale_actor_read)
                with pytest.raises(ToolError, match="revision conflict"):
                    await call(server, "character_check", args)
            assert await snapshot() == before
            assert await actor_snapshot() == before_actor

            result = await call(server, "character_check", args)
            assert result["status"] == "committed"
            assert result["result"]["total"] == 16 and result["result"]["success"] is True
            assert result["result"]["rolls"] == []
            assert "random_stream_receipt" not in result
            assert result["audience"]["scope"] == "dm"
            after = await snapshot()
            assert after["state"]["random_stream"] == before["state"]["random_stream"]
            assert after["revision"] == before["revision"] + 1
            assert await call(server, "character_check", args) == result
            with pytest.raises(ToolError, match="different request"):
                await call(
                    server,
                    "character_check",
                    {
                        **args,
                        "payload": {**args["payload"], "secret": False},
                    },
                )
            presentation_args = {
                "campaign_id": campaign_id,
                "resolution_id": result["resolution_id"],
            }
            presentation = await read(server, "resolution_presentation", presentation_args)
            assert presentation["audience"]["scope"] == "dm"
            with pytest.raises(ToolError, match="not found"):
                await call(
                    server,
                    "resolution_presentation",
                    {
                        **presentation_args,
                        "principal_id": "user:player",
                    },
                )
            player_view = json.dumps(await snapshot("user:player"))
            assert result["resolution_id"] not in player_view
            assert "needle" not in player_view
            assert "source_excerpt" not in player_view

            close_server(server)
            server = create_server(config)
            assert await call(server, "character_check", args) == result
            assert await snapshot() == after
            sensory = deepcopy(args)
            sensory.update(expected_revision=after["revision"], idempotency_key="sensory")
            sensory["payload"]["actor_id"] = blind["id"]
            sensory["payload"]["task"]["relies_on_sight"] = True
            automatic = await call(server, "character_check", sensory)
            assert automatic["result"]["automatic_failure"] is True
            assert automatic["result"]["total"] is None
            assert automatic["result"]["success"] is False
            after = await snapshot()
            # A different-ability repeated task still uses the actor's Perception expertise.
            variant = deepcopy(args)
            variant.update(expected_revision=after["revision"], idempotency_key="variant")
            variant["payload"].update(skill_ability="intelligence", secret=False)
            variant["payload"]["task"].update(
                mode="repeated_task", reason="DM approves repeated Intelligence Perception."
            )
            resolved = await call(server, "character_check", variant)
            assert resolved["result"]["total"] == 18
            public = await read(
                server,
                "resolution_presentation",
                {
                    "campaign_id": campaign_id,
                    "resolution_id": resolved["resolution_id"],
                    "principal_id": "user:player",
                },
            )
            assert public["audience"]["scope"] == "actors"
            # Both sides come from current cards. Equal scores retain the prior situation.
            opposed = deepcopy(args)
            opposed.update(
                expected_revision=resolved["campaign_revision"], idempotency_key="opposed"
            )
            opposed["payload"]["task"].pop("dc")
            opposed["payload"]["task"].update(
                mode="opposed",
                opponent={"actor_id": other["id"], "ability": "perception"},
                reason="Compare the two observers without rolling.",
            )
            compared = await call(server, "character_check", opposed)
            assert compared["result"]["tie"] is True
            assert compared["result"]["outcome"] == "unchanged"
            opposed["expected_revision"] = compared["campaign_revision"]
            opposed["idempotency_key"] = "opposed-sensory"
            opposed["payload"]["task"]["opponent"]["actor_id"] = blind["id"]
            opposed["payload"]["task"]["relies_on_sight"] = True
            compared = await call(server, "character_check", opposed)
            assert compared["result"]["outcome"] == "actor"
            assert compared["result"]["opponent"]["automatic_failure"] is True
            assert (await snapshot())["state"]["random_stream"] == before["state"]["random_stream"]
        finally:
            close_server(server)

    asyncio.run(exercise())
