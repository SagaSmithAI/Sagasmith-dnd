import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_core import ActorKnowledgeService, BranchService

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


def _config(tmp_path: Path) -> McpConfig:
    dnd = tmp_path / "dnd"
    modulegen = tmp_path / "modulegen"
    (dnd / "full").mkdir(parents=True)
    modulegen.mkdir(parents=True)
    (dnd / "full" / "SKILL.md").write_text("# D&D Full\n", encoding="utf-8")
    (modulegen / "SKILL.md").write_text("# Module Generator\n", encoding="utf-8")
    return McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=dnd,
        modulegen_skills_dir=modulegen,
        auto_seed_rules=False,
    )


def test_character_state_facade_has_no_second_actor_memory_authority(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        tool = next(
            item for item in await server.list_tools() if item.name == "character_state_change"
        )
        actions = tool.input_schema["properties"]["action"]["enum"]
        assert "memory_add" not in actions
        assert "memory_resolve" not in actions
        assert {"actor_knowledge_query", "actor_knowledge_change"}.issubset(
            {item.name for item in await server.list_tools()}
        )
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "One knowledge authority", "idempotency_key": "campaign"},
        )
        with pytest.raises(Exception, match="unsupported fields: memories"):
            await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Invalid witness",
                        "notes": {
                            "memories": [
                                {
                                    "id": "invalid-memory",
                                    "summary": "Must use ActorKnowledge instead.",
                                },
                            ],
                        },
                    },
                    "idempotency_key": "invalid-actor",
                },
            )

    asyncio.run(exercise())


def test_memory_facade_supports_stable_upsert_revision_and_supersede(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Stable facts", "idempotency_key": "campaign"},
        )
        created = await _call(
            server,
            "memory_change",
            {
                "campaign_id": campaign["id"],
                "action": "upsert",
                "payload": {
                    "fact_key": "location:cellar:door-state",
                    "subject": "Cellar door",
                    "subject_ref": "location:cellar",
                    "predicate": "door-state",
                    "content": "The cellar door is locked.",
                    "importance": 4,
                    "disclosure_scope": "party",
                },
                "idempotency_key": "fact-create",
            },
        )
        assert created["fact_key"] == "location:cellar:door-state"

        with pytest.raises(Exception, match="expected_revision_id"):
            await _call(
                server,
                "memory_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "upsert",
                    "payload": {
                        "fact_key": "location:cellar:door-state",
                        "content": "An unsafe overwrite.",
                    },
                    "idempotency_key": "unsafe-upsert",
                },
            )

        revised = await _call(
            server,
            "memory_change",
            {
                "campaign_id": campaign["id"],
                "action": "upsert",
                "payload": {
                    "fact_key": "location:cellar:door-state",
                    "content": "The cellar door is open.",
                    "expected_revision_id": created["revision_id"],
                    "source_event_ids": ["event:door-opened"],
                },
                "idempotency_key": "fact-revise",
            },
        )
        assert revised["id"] == created["id"]
        assert revised["content"] == "The cellar door is open."
        assert revised["importance"] == 4
        assert revised["disclosure_scope"] == "party"

        with pytest.raises(Exception, match="fact_key identity conflict.*subject"):
            await _call(
                server,
                "memory_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "upsert",
                    "payload": {
                        "fact_key": "location:cellar:door-state",
                        "subject": "A different fact identity",
                        "content": "This write must be rejected.",
                        "expected_revision_id": revised["revision_id"],
                    },
                    "idempotency_key": "fact-identity-conflict",
                },
            )

        superseded = await _call(
            server,
            "memory_change",
            {
                "campaign_id": campaign["id"],
                "action": "supersede",
                "payload": {
                    "memory_id": created["id"],
                    "expected_revision_id": revised["revision_id"],
                },
                "idempotency_key": "fact-supersede",
            },
        )
        assert superseded["status"] == "superseded"
        assert (
            await _call(
                server,
                "memory_query",
                {"campaign_id": campaign["id"], "view": "list"},
            )
            == []
        )
        history = await _call(
            server,
            "memory_query",
            {
                "campaign_id": campaign["id"],
                "view": "list",
                "payload": {"include_inactive": True},
            },
        )
        assert [item["id"] for item in history] == [created["id"]]

    asyncio.run(exercise())


def test_memory_facade_retract_forget_preserve_history_and_guard_revision(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server, "campaign_create", {"name": "Retractions", "idempotency_key": "campaign"}
        )
        created = await _call(
            server,
            "memory_change",
            {
                "campaign_id": campaign["id"],
                "action": "add",
                "content": "The bell rings at dusk.",
                "kind": "fact",
                "subject": "bell",
                "expected_revision": campaign["revision"],
                "idempotency_key": "fact-add",
            },
        )
        with pytest.raises(ToolError, match="idempotency"):
            await _call(
                server,
                "memory_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "add",
                    "content": "The bell rings at dusk.",
                    "kind": "fact",
                    "subject": "bell",
                    "expected_revision": 999,
                    "idempotency_key": "fact-add",
                },
            )
        with pytest.raises(ToolError, match="campaign revision conflict"):
            await _call(
                server,
                "memory_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "retract",
                    "payload": {
                        "memory_id": created["id"],
                        "expected_revision_id": created["revision_id"],
                    },
                    "expected_revision": 0,
                    "idempotency_key": "fact-retract-stale",
                },
            )
        current = await _call(
            server,
            "memory_query",
            {"campaign_id": campaign["id"], "view": "list", "payload": {"include_inactive": True}},
        )
        retracted = await _call(
            server,
            "memory_change",
            {
                "campaign_id": campaign["id"],
                "action": "retract",
                "payload": {
                    "memory_id": created["id"],
                    "expected_revision_id": current[0]["revision_id"],
                },
                "idempotency_key": "fact-retract",
            },
        )
        assert retracted["status"] == "retracted"
        assert await _call(
            server, "memory_query", {"campaign_id": campaign["id"], "view": "list"}
        ) == []
        forgotten = await _call(
            server,
            "memory_change",
            {
                "campaign_id": campaign["id"],
                "action": "forget",
                "payload": {
                    "memory_id": created["id"],
                    "expected_revision_id": retracted["revision_id"],
                },
                "idempotency_key": "fact-forget",
            },
        )
        assert forgotten["status"] == "forgotten"
        history = await _call(
            server,
            "memory_query",
            {
                "campaign_id": campaign["id"],
                "view": "list",
                "payload": {"include_inactive": True},
            },
        )
        assert len(history) == 1 and history[0]["status"] == "forgotten"

    asyncio.run(exercise())


def test_actor_knowledge_revise_preserves_omitted_fields_and_can_clear_source(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Knowledge revisions", "idempotency_key": "campaign"},
        )
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Witness",
                    "character_type": "npc",
                },
                "idempotency_key": "actor",
            },
        )
        event = await _call(
            server,
            "campaign_event",
            {
                "campaign_id": campaign["id"],
                "action": "add",
                "payload": {"summary": "The witness studies the sigil."},
                "idempotency_key": "source-event",
            },
        )
        original = await _call(
            server,
            "actor_knowledge_change",
            {
                "action": "add",
                "payload": {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "knowledge_key": "sigil-color",
                    "proposition": "The sigil is blue.",
                    "epistemic_status": "belief",
                    "confidence": 5,
                    "source_event_id": event["id"],
                    "cause": "inferred",
                    "disclosure_scope": "owner",
                },
                "idempotency_key": "knowledge-add",
            },
        )
        revised = await _call(
            server,
            "actor_knowledge_change",
            {
                "action": "revise",
                "payload": {
                    "knowledge_id": original["id"],
                    "proposition": "The sigil is azure.",
                    "expected_revision_id": original["revision_id"],
                },
                "idempotency_key": "knowledge-revise-preserve",
            },
        )
        assert (
            revised["epistemic_status"],
            revised["confidence"],
            revised["source_event_id"],
            revised["cause"],
            revised["disclosure_scope"],
        ) == ("belief", 5, event["id"], "inferred", "owner")

        cleared = await _call(
            server,
            "actor_knowledge_change",
            {
                "action": "revise",
                "payload": {
                    "knowledge_id": original["id"],
                    "proposition": "The source is no longer remembered.",
                    "source_event_id": None,
                    "expected_revision_id": revised["revision_id"],
                },
                "idempotency_key": "knowledge-revise-clear",
            },
        )
        assert cleared["source_event_id"] is None
        assert cleared["disclosure_scope"] == "owner"
        retracted = await _call(
            server,
            "actor_knowledge_change",
            {
                "action": "retract",
                "payload": {
                    "knowledge_id": original["id"],
                    "expected_revision_id": cleared["revision_id"],
                },
                "idempotency_key": "knowledge-retract",
            },
        )
        assert retracted["epistemic_status"] == "superseded"
        assert await _call(
            server,
            "actor_knowledge_query",
            {"campaign_id": campaign["id"], "actor_id": actor["id"], "view": "list"},
        ) == []
        forgotten = await _call(
            server,
            "actor_knowledge_change",
            {
                "action": "forget",
                "payload": {
                    "knowledge_id": original["id"],
                    "expected_revision_id": retracted["revision_id"],
                },
                "idempotency_key": "knowledge-forget",
            },
        )
        assert forgotten["epistemic_status"] == "forgotten"
        history = await _call(
            server,
            "actor_knowledge_query",
            {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "view": "list",
                "payload": {"include_inactive": True},
            },
        )
        assert len(history) == 1 and history[0]["epistemic_status"] == "forgotten"

        later = await _call(
            server,
            "actor_knowledge_change",
            {
                "action": "revise",
                "payload": {
                    "knowledge_id": original["id"],
                    "proposition": "The sigil was later described as violet.",
                    "expected_revision_id": forgotten["revision_id"],
                },
                "idempotency_key": "knowledge-later-revision",
            },
        )
        replayed_forget = await _call(
            server,
            "actor_knowledge_change",
            {
                "action": "forget",
                "payload": {
                    "knowledge_id": original["id"],
                    "expected_revision_id": retracted["revision_id"],
                },
                "idempotency_key": "knowledge-forget",
            },
        )
        assert replayed_forget == forgotten
        assert later["proposition"] == "The sigil was later described as violet."

        await _call(
            server,
            "access_grant",
            {
                "scope": "campaign",
                "campaign_id": campaign["id"],
                "principal_id": "player:witness",
                "payload": {"role": "player"},
            },
        )
        await _call(
            server,
            "access_grant",
            {
                "scope": "actor",
                "campaign_id": campaign["id"],
                "principal_id": "player:witness",
                "payload": {"actor_id": actor["id"], "can_view_private": True},
            },
        )
        for view in ("list", "search"):
            with pytest.raises(ToolError, match="restricted to DM roles"):
                await _call(
                    server,
                    "actor_knowledge_query",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": actor["id"],
                        "view": view,
                        "query": "sigil",
                        "payload": {"include_inactive": True},
                        "principal_id": "player:witness",
                    },
                )
        for tool_name, arguments in (
            (
                "memory_query",
                {
                    "campaign_id": campaign["id"],
                    "view": "list",
                    "payload": {"include_inactive": "false"},
                },
            ),
            (
                "actor_knowledge_query",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "view": "list",
                    "payload": {"include_inactive": "false"},
                },
            ),
        ):
            with pytest.raises(ToolError, match="include_inactive must be a boolean"):
                await _call(server, tool_name, arguments)

    asyncio.run(exercise())


def test_actor_knowledge_change_rejects_stale_revision_and_checkout_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Knowledge CAS", "idempotency_key": "campaign"},
        )
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Witness",
                    "character_type": "npc",
                },
                "idempotency_key": "actor",
            },
        )
        current = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        branches = await _call(
            server,
            "branch_query",
            {"campaign_id": campaign["id"], "view": "list", "payload": {}},
        )
        main = next(item for item in branches if item["is_current"])
        base = await _call(
            server,
            "snapshot_create",
            {
                "campaign_id": campaign["id"],
                "label": "Knowledge baseline",
                "expected_revision": current["revision"],
                "expected_head_snapshot_id": "",
                "idempotency_key": "snapshot",
            },
        )
        current = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        alternate = await _call(
            server,
            "branch_change",
            {
                "campaign_id": campaign["id"],
                "action": "create",
                "payload": {
                    "name": "alternate",
                    "from_snapshot_id": base["id"],
                    "checkout": False,
                },
                "expected_revision": current["revision"],
                "expected_branch_id": main["id"],
                "idempotency_key": "branch",
            },
        )
        current = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        request = {
            "action": "add",
            "payload": {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "knowledge_key": "door-state",
                "proposition": "The door is closed.",
            },
        }
        with pytest.raises(ToolError, match="campaign revision conflict"):
            await _call(
                server,
                "actor_knowledge_change",
                {
                    **request,
                    "expected_revision": current["revision"] - 1,
                    "idempotency_key": "stale-revision",
                },
            )

        original_add = ActorKnowledgeService.add

        def checkout_before_add(service, campaign_id, **kwargs):
            BranchService(service.database).checkout(campaign_id, alternate["id"])
            return original_add(service, campaign_id, **kwargs)

        monkeypatch.setattr(ActorKnowledgeService, "add", checkout_before_add)
        with pytest.raises(ToolError, match="branch conflict"):
            await _call(
                server,
                "actor_knowledge_change",
                {
                    **request,
                    "expected_revision": current["revision"],
                    "idempotency_key": "checkout-race",
                },
            )

        for branch_id in (main["id"], alternate["id"]):
            assert await _call(
                server,
                "actor_knowledge_query",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "view": "list",
                    "payload": {"branch_id": branch_id, "include_inactive": True},
                },
            ) == []

        monkeypatch.setattr(ActorKnowledgeService, "add", original_add)
        replay_request = {
            "action": "add",
            "payload": {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "knowledge_key": "stable-replay",
                "proposition": "This write is replayable.",
            },
            "idempotency_key": "stable-replay",
        }
        created = await _call(server, "actor_knowledge_change", replay_request)
        current = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        await _call(
            server,
            "campaign_change",
            {
                "campaign_id": campaign["id"],
                "payload": {"description": "Campaign revision advances."},
                "expected_revision": current["revision"],
                "idempotency_key": "advance-campaign",
            },
        )
        assert await _call(server, "actor_knowledge_change", replay_request) == created

    asyncio.run(exercise())


def test_memory_commit_is_atomic_idempotent_and_pins_skill_manifest(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Atomic scene", "idempotency_key": "campaign"},
        )
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Witness"},
                "idempotency_key": "actor",
            },
        )
        arguments = {
            "campaign_id": campaign["id"],
            "action": "commit",
            "payload": {
                "event": {
                    "summary": "The witness hears the midnight bell.",
                    "audience_scope": "actor",
                },
                "facts": [
                    {
                        "fact_key": "world:midnight-bell:heard",
                        "content": "The midnight bell rang.",
                        "disclosure_scope": "party",
                    }
                ],
                "actor_knowledge": [
                    {
                        "actor_id": actor["id"],
                        "knowledge_key": "midnight-bell",
                        "proposition": "I heard the midnight bell.",
                        "disclosure_scope": "owner",
                    }
                ],
                "snapshot": {"label": "Midnight bell"},
            },
            "expected_revision": campaign["revision"],
            "idempotency_key": "scene-commit",
        }
        committed = await _call(server, "memory_change", arguments)
        current_after_commit = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        replayed = await _call(
            server,
            "memory_change",
            {
                **arguments,
                "expected_revision": current_after_commit["revision"],
            },
        )

        assert replayed["event"]["id"] == committed["event"]["id"]
        assert committed["snapshot"] is not None
        assert len(committed["skill_manifest"]) == 2
        assert all(len(item["checksum"]) == 64 for item in committed["skill_manifest"])
        assert (
            committed["event"]["payload"]["_sagasmith_skill_manifest"]
            == (committed["skill_manifest"])
        )
        assert committed["facts"][0]["source_event_ids"] == [committed["event"]["id"]]
        assert committed["actor_knowledge"][0]["source_event_id"] == (committed["event"]["id"])
        context = await _call(
            server,
            "continuity_context",
            {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "query": "midnight bell",
                "budget_chars": 1_000,
            },
        )
        assert context["retrieval"]["budget_chars"] == 1_000
        assert context["retrieval"]["strategy"] == ("lexical_structured_shared_budget_v2")
        diagnostics = await _call(
            server,
            "memory_query",
            {"campaign_id": campaign["id"], "view": "diagnostics"},
        )
        assert diagnostics["facts"]["active"] == 1
        assert diagnostics["actor_knowledge"]["active"] == 1
        assert diagnostics["skill_manifest"]["drift"] is False
        assert diagnostics["recap"]["source"] == "deterministic"

        before = await _call(
            server,
            "campaign_event",
            {"campaign_id": campaign["id"], "action": "list"},
        )
        with pytest.raises(Exception, match="live character"):
            await _call(
                server,
                "memory_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "commit",
                    "payload": {
                        "event": {"summary": "This unit must roll back."},
                        "facts": [{"fact_key": "rollback:test", "content": "Must roll back."}],
                        "actor_knowledge": [
                            {
                                "actor_id": "missing",
                                "knowledge_key": "invalid",
                                "proposition": "Must fail.",
                            }
                        ],
                    },
                    "idempotency_key": "rollback",
                },
            )
        after = await _call(
            server,
            "campaign_event",
            {"campaign_id": campaign["id"], "action": "list"},
        )
        assert [item["id"] for item in after] == [item["id"] for item in before]

    asyncio.run(exercise())
