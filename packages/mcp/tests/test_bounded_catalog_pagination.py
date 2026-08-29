from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import _bounded_page, create_server

COLLECTION_LIMIT_FIELDS = {
    "actor_knowledge_query": "limit",
    "branch_query": "limit",
    "campaign_event": "limit",
    "campaign_query": "limit",
    "character_query": "limit",
    "combat_query": "limit",
    "content_pack": "limit",
    "exposure": "limit",
    "memory_query": "limit",
    "module_draft": "limit",
    "module_query": "limit",
    "module_search": "top_k",
    "npc_conversation": "limit",
    "rule_search": "top_k",
    "rulebook_draft": "limit",
    "skill_query": "limit",
    "snapshot_query": "limit",
    "state_revision": "limit",
}


def _server(tmp_path: Path):
    return create_server(
        McpConfig(
            home=tmp_path / "home",
            database_url=None,
            chroma_url=None,
            chroma_path_override=None,
            dnd_skills_dir=tmp_path / "dnd",
            modulegen_skills_dir=tmp_path / "modulegen",
            auto_seed_rules=False,
        )
    )


async def _raw(server, name: str, arguments: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    content, structured = await server.call_tool(name, arguments)
    assert isinstance(structured, dict)
    return content, structured


def test_every_public_collection_facade_has_filter_limit_and_cursor_contract(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = _server(tmp_path)
        tools = {tool.name: tool for tool in await server.list_tools()}
        assert set(COLLECTION_LIMIT_FIELDS) <= set(tools)

        for tool_name, limit_field in COLLECTION_LIMIT_FIELDS.items():
            schema = tools[tool_name].input_schema
            properties = schema["properties"]
            assert "query" in properties, tool_name
            assert "cursor" in properties, tool_name
            assert properties["cursor"]["maxLength"] <= 1024, tool_name
            assert limit_field in properties, tool_name
            assert properties[limit_field]["maximum"] <= 100, tool_name
            assert properties[limit_field]["minimum"] >= 1, tool_name

        # This is a fixed one-record capability catalog, not an unbounded
        # collection, and therefore intentionally has no continuation cursor.
        _, systems = await _raw(server, "system_list", {})
        assert len(systems) == 1

    asyncio.run(exercise())


def test_campaign_catalog_filter_and_cursor_are_bounded_stable_and_compatible(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = _server(tmp_path)
        for name in ("Aster", "Birch", "Cedar", "Dogwood", "Elm"):
            await _raw(
                server,
                "campaign_create",
                {"name": name, "idempotency_key": f"pagination:{name}"},
            )

        content, first = await _raw(
            server,
            "campaign_query",
            {"view": "list", "limit": 2},
        )
        assert first["action"] == "list"
        assert [item["name"] for item in first["result"]] == ["Aster", "Birch"]
        assert first["page"] == {
            "limit": 2,
            "returned": 2,
            "has_more": True,
            "next_cursor": first["next_cursor"],
            "total_count": 5,
        }
        assert isinstance(first["next_cursor"], str)
        assert first["next_cursor"] not in {"2", "Aster", "Birch"}
        assert content and json.loads(content[0].text)["result"] == first["result"]

        _, second = await _raw(
            server,
            "campaign_query",
            {"view": "list", "limit": 2, "cursor": first["next_cursor"]},
        )
        assert [item["name"] for item in second["result"]] == ["Cedar", "Dogwood"]
        first_ids = {item["id"] for item in first["result"]}
        second_ids = {item["id"] for item in second["result"]}
        assert not (first_ids & second_ids)

        # Opaque payload-based continuation remains compatible with the
        # existing facade payload convention.
        _, last = await _raw(
            server,
            "campaign_query",
            {
                "view": "list",
                "limit": 2,
                "payload": {"cursor": second["next_cursor"]},
            },
        )
        assert [item["name"] for item in last["result"]] == ["Elm"]
        assert last["next_cursor"] is None

        _, filtered = await _raw(
            server,
            "campaign_query",
            {"view": "list", "query": "cedar", "limit": 2},
        )
        assert [item["name"] for item in filtered["result"]] == ["Cedar"]
        assert filtered["page"]["total_count"] == 1

    asyncio.run(exercise())


def test_cursor_is_bound_to_collection_and_filter_scope() -> None:
    _, page = _bounded_page([{"id": 1}, {"id": 2}], scope="campaigns", limit=1)
    cursor = page["next_cursor"]
    assert cursor is not None

    with pytest.raises(ValueError, match="invalid for this query"):
        _bounded_page([{"id": 1}, {"id": 2}], scope="characters", limit=1, cursor=cursor)
    with pytest.raises(ValueError, match="invalid for this query"):
        _bounded_page(
            [{"id": 1}, {"id": 2}],
            scope="campaigns",
            query="changed",
            limit=1,
            cursor=cursor,
        )
    with pytest.raises(ValueError, match="cursor and offset are mutually exclusive"):
        _bounded_page(
            [{"id": 1}, {"id": 2}], scope="campaigns", limit=1, cursor=cursor, offset=1
        )


def test_event_and_revision_cursors_reach_beyond_first_hundred_records(
    tmp_path: Path,
) -> None:
    async def collect(server, name: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            request = {**arguments, "limit": 100}
            if cursor is not None:
                request["cursor"] = cursor
            _, structured = await _raw(server, name, request)
            values.extend(structured["result"])
            cursor = structured["next_cursor"]
            if cursor is None:
                return values

    async def exercise() -> None:
        server = _server(tmp_path)
        _, created = await _raw(
            server,
            "campaign_create",
            {"name": "Long history", "idempotency_key": "long-history"},
        )
        campaign_id = created["id"]
        event_ids: set[str] = set()
        for ordinal in range(125):
            _, event = await _raw(
                server,
                "campaign_event",
                {
                    "campaign_id": campaign_id,
                    "action": "add",
                    "payload": {"summary": f"Checkpoint {ordinal:03d}"},
                    "idempotency_key": f"long-history:{ordinal}",
                },
            )
            event_ids.add(event["result"]["id"])

        events = await collect(
            server,
            "campaign_event",
            {"campaign_id": campaign_id, "action": "list"},
        )
        assert len(events) == len({item["id"] for item in events}) == 125
        assert {item["id"] for item in events} == event_ids

        current = created
        for ordinal in range(105):
            _, changed = await _raw(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign_id,
                    "action": "update",
                    "payload": {"description": f"Revision checkpoint {ordinal:03d}"},
                    "expected_revision": current["revision"],
                    "idempotency_key": f"revision-history:{ordinal}",
                },
            )
            current = changed["result"]

        revisions = await collect(
            server,
            "state_revision",
            {"campaign_id": campaign_id, "action": "history"},
        )
        assert len(revisions) > 100
        assert len(revisions) == len({item["sequence"] for item in revisions})

    asyncio.run(exercise())
