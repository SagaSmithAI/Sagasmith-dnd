import asyncio
from copy import deepcopy
from pathlib import Path

import pytest

from sagasmith_dnd_mcp.actor_memory import MEMORY_TRACKS, select_actor_memory_context
from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server


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


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


async def _create_actor(
    server,
    campaign_id: str,
    *,
    name: str,
    character_type: str,
    key: str,
) -> dict:
    return await _call(
        server,
        "character_create_from",
        {
            "mode": "direct",
            "payload": {
                "campaign_id": campaign_id,
                "name": name,
                "character_type": character_type,
                "summary": f"{name} remembers promises and observed events.",
            },
            "principal_id": "system:local",
            "idempotency_key": key,
        },
    )


def test_four_tracks_ranking_deduplication_and_budget_diagnostics() -> None:
    actor_state = {
        "id": "actor-arden",
        "revision": 4,
        "name": "Arden",
        "character_type": "pc",
        "summary": "A cartographer who never abandons a companion.",
        "state_facts": [
            {
                "id": "fact-oath",
                "revision_id": "fact-oath-r1",
                "kind": "actor_state",
                "fact_key": "actor:arden:promise:senna",
                "predicate": "promise",
                "content": "Return Senna's star map.",
                "importance": 5,
            }
        ],
    }
    exact_ref_memory = {
        "id": "knowledge-exact",
        "revision_id": "knowledge-exact-r1",
        "actor_id": "actor-arden",
        "knowledge_key": "floodgate-rust",
        "proposition": "The floodgate chain is rusted.",
        "subject_ref": "scene:floodgate",
        "confidence": 1,
        "salience": 1,
        "source_event_id": "event-1",
    }
    queried_memory = {
        "id": "knowledge-query",
        "revision_id": "knowledge-query-r1",
        "actor_id": "actor-arden",
        "knowledge_key": "senna-map",
        "proposition": "Senna hid the star map beneath the blue astrolabe.",
        "confidence": 5,
        "salience": 5,
        "source_event_id": "event-2",
    }
    duplicate_old = {
        "id": "knowledge-duplicate",
        "revision_id": "knowledge-duplicate-r1",
        "actor_id": "actor-arden",
        "knowledge_key": "observatory-door",
        "proposition": "The observatory door is sealed.",
        "confidence": 5,
        "salience": 5,
        "source_event_id": "event-1",
    }
    duplicate_new = {
        **duplicate_old,
        "revision_id": "knowledge-duplicate-r2",
        "proposition": "The observatory door is open.",
        "source_event_id": "event-3",
    }
    events = [
        {
            "id": f"event-{index}",
            "sequence": index,
            "summary": summary,
            "retrieval_text": summary,
        }
        for index, summary in (
            (1, "Arden inspected the floodgate."),
            (2, "Senna pointed toward the blue astrolabe."),
            (3, "The observatory door opened."),
        )
    ]
    knowledge = [exact_ref_memory, queried_memory, duplicate_old, duplicate_new]

    complete = select_actor_memory_context(
        actor_state=actor_state,
        actor_knowledge=deepcopy(knowledge),
        events=deepcopy(events),
        current_refs=("scene:floodgate",),
        query="Senna star map blue astrolabe",
        budget_chars=100_000,
    )

    assert [item.source for item in complete.identity] == ["actor_state"]
    assert [item.record["predicate"] for item in complete.motivational] == ["promise"]
    assert {item.source for item in complete.semantic} == {"actor_knowledge"}
    assert {item.source for item in complete.episodic} == {"event"}
    assert complete.diagnostics["selection_order"][0]["basis_ref"].startswith(
        "knowledge:knowledge-exact"
    )
    assert complete.diagnostics["selection_order"][0]["signals"][
        "exact_ref_matches"
    ] == 1
    assert complete.diagnostics["selection_order"][1]["basis_ref"].startswith(
        "knowledge:knowledge-query"
    )
    selected_revisions = {
        item.record.get("revision_id") for item in complete.semantic
    }
    assert "knowledge-duplicate-r2" in selected_revisions
    assert "knowledge-duplicate-r1" not in selected_revisions
    assert complete.diagnostics["duplicates_dropped"] == 1

    first_two = complete.diagnostics["selection_order"][:2]
    exact_budget = sum(item["cost_chars"] for item in first_two)
    bounded = select_actor_memory_context(
        actor_state=actor_state,
        actor_knowledge=deepcopy(knowledge),
        events=deepcopy(events),
        current_refs=("scene:floodgate",),
        query="Senna star map blue astrolabe",
        budget_chars=exact_budget,
    )
    diagnostics = bounded.diagnostics
    assert diagnostics["used_chars"] == exact_budget
    assert diagnostics["remaining_chars"] == 0
    assert diagnostics["selected_count"] == 2
    assert diagnostics["omitted_for_budget"] == diagnostics["deduplicated_count"] - 2
    assert set(diagnostics["track_candidates"]) == set(MEMORY_TRACKS)
    assert set(diagnostics["track_selected"]) == set(MEMORY_TRACKS)
    assert diagnostics["candidate_count"] == 9
    assert diagnostics["deduplicated_count"] == 8
    assert diagnostics["query_terms"] == ["astrolabe", "blue", "map", "senna", "star"]


@pytest.mark.parametrize("character_type", ["pc", "npc"])
def test_pc_and_npc_use_the_same_four_track_memory_contract(
    tmp_path: Path,
    character_type: str,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": f"{character_type.upper()} memory",
                "idempotency_key": "campaign",
            },
        )
        actor = await _create_actor(
            server,
            campaign["id"],
            name="Arden",
            character_type=character_type,
            key="actor",
        )
        await _call(
            server,
            "memory_change",
            {
                "campaign_id": campaign["id"],
                "action": "upsert",
                "payload": {
                    "fact_key": f"actor:{actor['id']}:goal:star-map",
                    "kind": "actor_state",
                    "subject_ref": f"actor:{actor['id']}",
                    "predicate": "goal",
                    "content": "Recover Senna's star map.",
                    "importance": 5,
                    "disclosure_scope": "dm",
                },
                "idempotency_key": "goal",
            },
        )
        await _call(
            server,
            "campaign_event",
            {
                "campaign_id": campaign["id"],
                "action": "add",
                "payload": {
                    "summary": "Arden heard the astrolabe chime under moonlight.",
                    "event_type": "discovery",
                    "audience_scope": "actor",
                    "known_by_actor_ids": [actor["id"]],
                    "knowledge_key": "astrolabe-chime",
                    "knowledge_proposition": "The astrolabe chimes under moonlight.",
                    "knowledge_disclosure_scope": "owner",
                },
                "idempotency_key": "observed-event",
            },
        )

        context = await _call(
            server,
            "continuity_context",
            {
                "campaign_id": campaign["id"],
                "purpose": "actor_memory",
                "actor_id": actor["id"],
                "query": "star map astrolabe moonlight",
                "related_refs": [f"actor:{actor['id']}"],
                "budget_chars": 12_000,
            },
        )

        assert context["actor"]["character_type"] == character_type
        assert all(context["memory"][track] for track in MEMORY_TRACKS)
        assert context["memory"]["motivational"][0]["record"]["predicate"] == "goal"
        assert context["memory"]["semantic"][0]["record"]["knowledge_key"] == (
            "astrolabe-chime"
        )
        assert context["memory"]["episodic"][0]["record"]["event_type"] == (
            "discovery"
        )
        diagnostics = context["memory"]["diagnostics"]
        assert diagnostics["used_chars"] <= diagnostics["budget_chars"]
        assert diagnostics["selected_count"] == sum(
            diagnostics["track_selected"].values()
        )
        assert diagnostics["deduplicated_count"] == (
            diagnostics["selected_count"] + diagnostics["omitted_for_budget"]
        )
        assert context["context_receipt"]["signature"]

    asyncio.run(exercise())


def test_old_relevant_episode_is_recalled_after_more_than_two_hundred_actor_events(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Long history", "idempotency_key": "campaign"},
        )
        actor = await _create_actor(
            server,
            campaign["id"],
            name="Mira",
            character_type="pc",
            key="actor",
        )
        other = await _create_actor(
            server,
            campaign["id"],
            name="Unrelated witness",
            character_type="npc",
            key="other-actor",
        )
        old_event = await _call(
            server,
            "campaign_event",
            {
                "campaign_id": campaign["id"],
                "action": "add",
                "payload": {
                    "summary": "Mira learned the silver cicada passphrase at the old ferry.",
                    "event_type": "revelation",
                    "audience_scope": "actor",
                    "known_by_actor_ids": [actor["id"]],
                    "knowledge_key": "silver-cicada-passphrase",
                    "knowledge_proposition": "The old ferry passphrase is silver cicada.",
                },
                "idempotency_key": "old-event",
            },
        )
        other_event = await _call(
            server,
            "campaign_event",
            {
                "campaign_id": campaign["id"],
                "action": "add",
                "payload": {
                    "summary": "Only the unrelated witness heard the copper moth password.",
                    "event_type": "revelation",
                    "audience_scope": "actor",
                    "known_by_actor_ids": [other["id"]],
                    "knowledge_key": "copper-moth-password",
                    "knowledge_proposition": "The password is copper moth.",
                },
                "idempotency_key": "other-event",
            },
        )
        hidden_event = await _call(
            server,
            "campaign_event",
            {
                "campaign_id": campaign["id"],
                "action": "add",
                "payload": {
                    "summary": "A DM-only source underlies a private belief note for Mira.",
                    "event_type": "secret",
                    "audience_scope": "dm",
                    "known_by_actor_ids": [actor["id"]],
                    "knowledge_key": "hidden-source-belief",
                    "knowledge_proposition": "The DM tracks a belief Mira has not learned.",
                    "knowledge_disclosure_scope": "dm",
                },
                "idempotency_key": "hidden-event",
            },
        )
        for index in range(200):
            await _call(
                server,
                "campaign_event",
                {
                    "campaign_id": campaign["id"],
                    "action": "add",
                    "payload": {
                        "summary": f"Mira completed an unrelated watch shift {index:03d}.",
                        "event_type": "downtime",
                        "audience_scope": "actor",
                        "known_by_actor_ids": [actor["id"]],
                        "knowledge_key": f"watch-shift-{index:03d}",
                        "knowledge_proposition": f"Watch shift {index:03d} was uneventful.",
                    },
                    "idempotency_key": f"filler-{index:03d}",
                },
            )

        context = await _call(
            server,
            "continuity_context",
            {
                "campaign_id": campaign["id"],
                "purpose": "actor_memory",
                "actor_id": actor["id"],
                "query": "silver cicada passphrase old ferry",
                "budget_chars": 12_000,
                "limit": 8,
            },
        )

        assert context["memory"]["semantic"][0]["record"]["knowledge_key"] == (
            "silver-cicada-passphrase"
        )
        assert old_event["id"] in {
            item["record"]["id"] for item in context["memory"]["episodic"]
        }

        exact = await _call(
            server,
            "continuity_context",
            {
                "campaign_id": campaign["id"],
                "purpose": "actor_memory",
                "actor_id": actor["id"],
                "query": "",
                "related_refs": [
                    f"event:{old_event['id']}",
                    f"event:{other_event['id']}",
                ],
                "budget_chars": 12_000,
            },
        )
        exact_event_ids = [
            item["record"]["id"] for item in exact["memory"]["episodic"]
        ]
        assert exact_event_ids[0] == old_event["id"]
        assert other_event["id"] not in exact_event_ids

        await _call(
            server,
            "access_grant",
            {
                "scope": "campaign",
                "campaign_id": campaign["id"],
                "principal_id": "player:mira",
                "payload": {"role": "player"},
            },
        )
        await _call(
            server,
            "access_grant",
            {
                "scope": "actor",
                "campaign_id": campaign["id"],
                "principal_id": "player:mira",
                "payload": {
                    "actor_id": actor["id"],
                    "can_view_private": True,
                },
            },
        )
        player_exact = await _call(
            server,
            "continuity_context",
            {
                "campaign_id": campaign["id"],
                "purpose": "actor_memory",
                "actor_id": actor["id"],
                "query": "",
                "related_refs": [
                    f"event:{old_event['id']}",
                    f"event:{other_event['id']}",
                    f"event:{hidden_event['id']}",
                ],
                "principal_id": "player:mira",
                "budget_chars": 12_000,
            },
        )
        player_event_ids = {
            item["record"]["id"] for item in player_exact["memory"]["episodic"]
        }
        assert old_event["id"] in player_event_ids
        assert other_event["id"] not in player_event_ids
        assert hidden_event["id"] not in player_event_ids

    asyncio.run(exercise())


def test_player_actor_memory_filters_dm_only_knowledge_while_dm_keeps_it(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Private knowledge", "idempotency_key": "campaign"},
        )
        pc = await _create_actor(
            server,
            campaign["id"],
            name="Mira",
            character_type="pc",
            key="pc",
        )
        await _call(
            server,
            "access_grant",
            {
                "scope": "campaign",
                "campaign_id": campaign["id"],
                "principal_id": "player:mira",
                "payload": {"role": "player"},
            },
        )
        await _call(
            server,
            "access_grant",
            {
                "scope": "actor",
                "campaign_id": campaign["id"],
                "principal_id": "player:mira",
                "payload": {
                    "actor_id": pc["id"],
                    "can_view_private": True,
                },
            },
        )
        for knowledge_key, disclosure_scope in (
            ("owner-memory", "owner"),
            ("public-memory", "public"),
            ("dm-only-memory", "dm"),
        ):
            await _call(
                server,
                "actor_knowledge_change",
                {
                    "action": "add",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "actor_id": pc["id"],
                        "knowledge_key": knowledge_key,
                        "proposition": f"Privacy marker {knowledge_key}.",
                        "disclosure_scope": disclosure_scope,
                    },
                    "principal_id": "system:local",
                    "idempotency_key": knowledge_key,
                },
            )
        await _call(
            server,
            "memory_change",
            {
                "campaign_id": campaign["id"],
                "action": "add",
                "payload": {
                    "fact_key": f"actor:{pc['id']}:goal:dm-secret",
                    "kind": "actor_state",
                    "subject_ref": f"actor:{pc['id']}",
                    "predicate": "goal",
                    "content": "DM-only goal privacy marker.",
                    "disclosure_scope": "dm",
                },
                "idempotency_key": "dm-only-actor-state",
            },
        )
        dm_participant_event = await _call(
            server,
            "memory_change",
            {
                "campaign_id": campaign["id"],
                "action": "commit",
                "payload": {
                    "event": {
                        "summary": "DM participant privacy marker.",
                        "retrieval_text": "DM participant privacy marker memory.",
                        "audience_scope": "dm",
                        "participants": [
                            {"actor_id": pc["id"], "role": "witness"}
                        ],
                    }
                },
                "expected_revision": campaign["revision"],
                "idempotency_key": "dm-participant-event",
            },
        )

        arguments = {
            "campaign_id": campaign["id"],
            "purpose": "actor_memory",
            "actor_id": pc["id"],
            "query": "privacy marker memory",
            "budget_chars": 12_000,
        }
        player_context = await _call(
            server,
            "continuity_context",
            {**arguments, "principal_id": "player:mira"},
        )
        dm_context = await _call(server, "continuity_context", arguments)

        player_keys = {
            item["record"]["knowledge_key"]
            for item in player_context["memory"]["semantic"]
        }
        dm_keys = {
            item["record"]["knowledge_key"]
            for item in dm_context["memory"]["semantic"]
        }
        assert dm_keys == {"owner-memory", "public-memory", "dm-only-memory"}
        assert player_keys == {"owner-memory", "public-memory"}
        assert "dm-only-memory" not in str(player_context["memory"])
        assert dm_participant_event["event"]["id"] in {
            item["record"]["id"] for item in dm_context["memory"]["episodic"]
        }
        assert dm_participant_event["event"]["id"] not in {
            item["record"]["id"] for item in player_context["memory"]["episodic"]
        }
        assert "DM participant privacy marker" not in str(player_context["memory"])
        assert "DM-only goal privacy marker" in str(dm_context["memory"])
        assert "DM-only goal privacy marker" not in str(player_context["memory"])

    asyncio.run(exercise())


def test_actor_memory_isolated_by_branch_and_player_actor_authority(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Isolated memories", "idempotency_key": "campaign"},
        )
        pc = await _create_actor(
            server,
            campaign["id"],
            name="Mira",
            character_type="pc",
            key="pc",
        )
        npc = await _create_actor(
            server,
            campaign["id"],
            name="Keeper",
            character_type="npc",
            key="npc",
        )
        await _call(
            server,
            "access_grant",
            {
                "scope": "campaign",
                "campaign_id": campaign["id"],
                "principal_id": "player:mira",
                "payload": {"role": "player"},
            },
        )
        await _call(
            server,
            "access_grant",
            {
                "scope": "actor",
                "campaign_id": campaign["id"],
                "principal_id": "player:mira",
                "payload": {
                    "actor_id": pc["id"],
                    "can_view_private": True,
                },
            },
        )
        await _call(
            server,
            "actor_knowledge_change",
            {
                "action": "add",
                "payload": {
                    "campaign_id": campaign["id"],
                    "actor_id": npc["id"],
                    "knowledge_key": "keeper-only",
                    "proposition": "The Keeper knows which portrait is a hidden door.",
                },
                "principal_id": "system:local",
                "idempotency_key": "keeper-secret",
            },
        )
        player_context = await _call(
            server,
            "continuity_context",
            {
                "campaign_id": campaign["id"],
                "purpose": "actor_memory",
                "actor_id": pc["id"],
                "query": "portrait hidden door",
                "principal_id": "player:mira",
            },
        )
        assert player_context["memory"]["semantic"] == []
        with pytest.raises(Exception, match="cannot access actor"):
            await _call(
                server,
                "continuity_context",
                {
                    "campaign_id": campaign["id"],
                    "purpose": "actor_memory",
                    "actor_id": npc["id"],
                    "query": "portrait hidden door",
                    "principal_id": "player:mira",
                },
            )

        current = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        base = await _call(
            server,
            "snapshot_create",
            {
                "campaign_id": campaign["id"],
                "label": "Shared base",
                "expected_revision": current["revision"],
                "expected_head_snapshot_id": "",
                "idempotency_key": "base-snapshot",
            },
        )
        branches = await _call(
            server,
            "branch_query",
            {"campaign_id": campaign["id"], "view": "list", "payload": {}},
        )
        main_branch = next(item for item in branches if item["is_current"])
        await _call(
            server,
            "actor_knowledge_change",
            {
                "action": "add",
                "payload": {
                    "campaign_id": campaign["id"],
                    "actor_id": pc["id"],
                    "knowledge_key": "main-only",
                    "proposition": "On the main branch the portrait opens eastward.",
                },
                "idempotency_key": "main-only",
            },
        )
        current = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        await _call(
            server,
            "snapshot_create",
            {
                "campaign_id": campaign["id"],
                "label": "Main memory",
                "expected_revision": current["revision"],
                "expected_head_snapshot_id": base["id"],
                "idempotency_key": "main-snapshot",
            },
        )
        current = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        await _call(
            server,
            "branch_change",
            {
                "campaign_id": campaign["id"],
                "action": "create",
                "payload": {
                    "name": "westward-portrait",
                    "from_snapshot_id": base["id"],
                    "checkout": True,
                },
                "expected_revision": current["revision"],
                "expected_branch_id": main_branch["id"],
                "idempotency_key": "fork",
            },
        )
        branches = await _call(
            server,
            "branch_query",
            {"campaign_id": campaign["id"], "view": "list", "payload": {}},
        )
        fork_branch = next(item for item in branches if item["is_current"])
        await _call(
            server,
            "actor_knowledge_change",
            {
                "action": "add",
                "payload": {
                    "campaign_id": campaign["id"],
                    "actor_id": pc["id"],
                    "knowledge_key": "fork-only",
                    "proposition": "On the fork the portrait opens westward.",
                },
                "idempotency_key": "fork-only",
            },
        )

        fork_context = await _call(
            server,
            "continuity_context",
            {
                "campaign_id": campaign["id"],
                "purpose": "actor_memory",
                "actor_id": pc["id"],
                "query": "portrait opens direction",
                "branch_id": fork_branch["id"],
            },
        )
        main_context = await _call(
            server,
            "continuity_context",
            {
                "campaign_id": campaign["id"],
                "purpose": "actor_memory",
                "actor_id": pc["id"],
                "query": "portrait opens direction",
                "branch_id": main_branch["id"],
            },
        )
        assert {
            item["record"]["knowledge_key"] for item in fork_context["memory"]["semantic"]
        } == {"fork-only"}
        assert {
            item["record"]["knowledge_key"] for item in main_context["memory"]["semantic"]
        } == {"main-only"}

    asyncio.run(exercise())
