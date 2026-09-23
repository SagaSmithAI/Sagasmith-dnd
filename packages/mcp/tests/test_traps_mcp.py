"""Trap writes require an active, exact module source and durable replay."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import finalize_and_activate_module


async def _read(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


def test_trap_lifecycle_is_source_bound_persisted_and_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "traps.md"
    profile = {"profile_id": "srd5.1.fire_breathing_statue"}
    profile_marker = json.dumps(profile, sort_keys=True, separators=(",", ":"))
    excerpt = f"The fire-breathing statue can be detected.\ntrap_profile: {profile_marker}"
    source.write_text(f"# Traps\n\n## Trip wire\n\n{excerpt}\n", encoding="utf-8")
    config = McpConfig(
        home=tmp_path / "home", database_url=None, chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=Path(__file__).resolve().parents[3] / "skills",
        modulegen_skills_dir=tmp_path / "modulegen", auto_seed_rules=False,
        module_import_roots=(tmp_path,),
    )

    async def exercise() -> None:
        server = create_server(config)
        try:
            campaign = await _read(server, "campaign_create", {
                "name": "Trap source", "edition": "2014", "idempotency_key": "campaign",
            })
            campaign_id = campaign["id"]
            staged = await _read(server, "module_draft", {
                "campaign_id": campaign_id, "action": "start", "idempotency_key": "draft",
                "payload": {"source_path": str(source), "source_key": "trap-source",
                            "title": "Trap source"},
            })
            async def helper_call(target, name, arguments):
                value = await _read(target, name, arguments)
                if isinstance(value, dict) and "action" in value and "result" in value:
                    return value["result"]
                return value

            await finalize_and_activate_module(
                helper_call, server, campaign_id, staged, source_key="trap-source",
                title="Trap source", portable_id="dnd5e.module.trap-source",
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            actor = await _read(server, "character_create_from", {
                "mode": "direct", "idempotency_key": "actor",
                "payload": {"campaign_id": campaign_id, "name": "Scout", "sheet": sheet},
            })
            hits = await _read(server, "module_search", {
                "campaign_id": campaign_id, "query": "fire-breathing statue", "top_k": 3,
            })
            expanded = await _read(server, "module_expand", {"chunk_id": hits[0]["id"]})
            before = await _read(server, "campaign_query", {
                "view": "get", "payload": {"campaign_id": campaign_id},
            })
            args = {
                "campaign_id": campaign_id, "trap_id": "wire-1", "action": "detect",
                "source_ref": json.dumps(
                    expanded["source_ref"], sort_keys=True, separators=(",", ":")
                ),
                "source_excerpt": excerpt, "profile": profile, "actor_id": actor["id"],
                "expected_revision": before["revision"], "idempotency_key": "detect-wire",
            }
            detected = await _read(server, "trap_state_transition", args)
            assert detected["action"] == "detect"
            assert detected["check"]["ability"] == "perception"
            assert type(detected["check"]["success"]) is bool
            assert await _read(server, "trap_state_transition", args) == detected
            with pytest.raises(ToolError, match="source_ref must be a JSON object"):
                await _read(server, "trap_state_transition", {
                    **args, "source_ref": "forged:other-source", "idempotency_key": "forged",
                    "expected_revision": detected["campaign_revision"],
                })

            current = await _read(server, "campaign_query", {
                "view": "get", "payload": {"campaign_id": campaign_id},
            })
            bypass_args = {
                **args,
                "action": "bypass",
                "method": "wedge_pressure_plate",
                "expected_revision": current["revision"],
                "idempotency_key": "bypass-wire",
            }
            bypassed = await _read(server, "trap_state_transition", bypass_args)
            assert bypassed["trap"]["bypassed"] is True
            assert bypassed["trap"]["bypass_method"] == "wedge_pressure_plate"
            assert "random_stream_receipt" not in bypassed
            assert await _read(server, "trap_state_transition", bypass_args) == bypassed
            with pytest.raises(ToolError, match="not authorized"):
                await _read(server, "trap_state_transition", {
                    **bypass_args, "method": "invented_method", "idempotency_key": "bad-bypass",
                    "expected_revision": bypassed["campaign_revision"],
                })
        finally:
            close_server(server)

    asyncio.run(exercise())
