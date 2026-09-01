from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.playthrough import new_playthrough_manifest

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server


def _server(tmp_path: Path):
    workspace = Path(__file__).resolve().parents[3]
    return create_server(
        McpConfig(
            home=tmp_path / "home",
            database_url=None,
            chroma_url=None,
            chroma_path_override=None,
            dnd_skills_dir=workspace / "skills",
            modulegen_skills_dir=workspace / "skills" / "dnd-module-generator",
            auto_seed_rules=False,
        )
    )


async def _call(server, name: str, arguments: dict):
    _, structured = await server.call_tool(name, arguments)
    value = structured.get("result", structured) if isinstance(structured, dict) else structured
    if isinstance(value, dict) and "action" in value and "result" in value:
        return value["result"]
    return value


def _source_ref() -> dict:
    return {
        "source_key": "the-lantern-below",
        "page": None,
        "chunk_hash": "a" * 64,
        "note": "Agent-reviewed source evidence: The Lantern Vault",
    }


def _manifest(source_ref: dict) -> dict:
    return {
        "title": "The Lantern Below",
        "classification": "adventure",
        "compatibility": {
            "editions": ["2014", "2024"],
            "required_capabilities": ["module_pack_v2"],
        },
        "play_profile": {
            "party_size": {
                "minimum": 2,
                "maximum": 4,
                "source_refs": [source_ref],
            },
            "starting_level": {"value": 1, "source_refs": [source_ref]},
            "expected_end_level": {"value": 2, "source_refs": [source_ref]},
            "advancement": {
                "modes": ["milestone"],
                "recommended": "milestone",
                "source_refs": [source_ref],
            },
            "pregenerated_characters": {
                "available": False,
                "applicability": "Reviewed; the module includes no pregenerated characters.",
                "source_refs": [source_ref],
            },
        },
        "continuity": {
            "series_id": None,
            "order": None,
            "continues_from": None,
            "state_policy": {},
        },
        "activation": {"mode": "campaign_attach", "default_active": False},
        "content_summary": {},
    }


def test_module_draft_tools_list_contract_discriminates_all_actions(tmp_path: Path) -> None:
    async def exercise() -> None:
        tools = await _server(tmp_path).list_tools()
        module_draft = next(tool for tool in tools if tool.name == "module_draft")
        schema = module_draft.input_schema
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)

        source_ref = _source_ref()
        valid_calls = [
            {
                "campaign_id": "campaign-1",
                "action": "start",
                "payload": {
                    "name": "lantern.md",
                    "content": "# The Lantern Below\n\n## Lantern Vault\n\nA sourced scene.",
                    "title": "The Lantern Below",
                    "source_key": "the-lantern-below",
                },
                "idempotency_key": "module-start-1",
            },
            {"campaign_id": "campaign-1", "action": "get", "payload": {"job_id": "job-1"}},
            {
                "campaign_id": "campaign-1",
                "action": "evidence",
                "payload": {"job_id": "job-1", "kind": "chunks", "limit": 1},
            },
            {
                "campaign_id": "campaign-1",
                "action": "edit",
                "payload": {
                    "job_id": "job-1",
                    "operation": "package",
                    "manifest": _manifest(source_ref),
                    "narrative": {"dossiers": [], "endings": []},
                    "dependencies": [],
                    "version": "1.0.0",
                },
                "expected_revision": 4,
                "idempotency_key": "module-package-1",
            },
            {
                "campaign_id": "campaign-1",
                "action": "finalize",
                "payload": {
                    "job_id": "job-1",
                    "pack_id": "dnd5e.module.the-lantern-below",
                    "confirmation": {
                        "confirmed": True,
                        "note": "Reviewed the Pack decisions and evidence receipts.",
                    },
                },
                "idempotency_key": "module-finalize-1",
            },
        ]
        for call in valid_calls:
            assert list(validator.iter_errors(call)) == [], call["action"]

        invalid_finalize = deepcopy(valid_calls[-1])
        del invalid_finalize["payload"]["pack_id"]
        assert list(validator.iter_errors(invalid_finalize))
        invalid_profile = deepcopy(valid_calls[3])
        invalid_profile["payload"]["manifest"]["play_profile"]["starting_level"] = {
            "value": 1
        }
        assert list(validator.iter_errors(invalid_profile))

        assert len(schema["allOf"]) == 5
        assert "Server-issued job_id/module_id" in schema["properties"]["payload"]["description"]
        assert "Import-job revision" in schema["properties"]["expected_revision"]["description"]
        assert "real module_draft(evidence)" in module_draft.description

    asyncio.run(exercise())


def test_luna_shaped_module_authoring_rejects_bad_pack_then_becomes_playable(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = _server(tmp_path)
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "Luna module contract",
                "edition": "2024",
                "idempotency_key": "campaign-create",
            },
        )
        campaign_id = campaign["id"]
        started = await _call(
            server,
            "module_draft",
            {
                "campaign_id": campaign_id,
                "action": "start",
                "payload": {
                    "name": "the-lantern-below.md",
                    "title": "The Lantern Below",
                    "source_key": "the-lantern-below",
                    "content": (
                        "# The Lantern Below\n\n"
                        "## Lantern Vault\n\n"
                        "Two to four level 1 heroes enter the vault. The final seal raises "
                        "them to level 2 by milestone. No pregenerated heroes are included.\n"
                    ),
                },
                "idempotency_key": "module-start",
            },
        )
        job_id = started["job_id"]
        inspected = await _call(
            server,
            "module_draft",
            {"campaign_id": campaign_id, "action": "get", "payload": {"job_id": job_id}},
        )
        evidence = await _call(
            server,
            "module_draft",
            {
                "campaign_id": campaign_id,
                "action": "evidence",
                "payload": {
                    "job_id": job_id,
                    "kind": "chunks",
                    "query": "Lantern Vault",
                    "limit": 1,
                },
            },
        )
        source_ref = deepcopy(evidence[0]["source_ref"])
        valid_manifest = _manifest(source_ref)
        invalid_manifest = deepcopy(valid_manifest)
        invalid_manifest["play_profile"]["starting_level"] = {"value": 1}

        before = inspected["job"]
        with pytest.raises(
            ToolError,
            match=r"play_profile\.starting_level requires exactly value and source_refs",
        ):
            await _call(
                server,
                "module_draft",
                {
                    "campaign_id": campaign_id,
                    "action": "edit",
                    "payload": {
                        "job_id": job_id,
                        "operation": "package",
                        "manifest": invalid_manifest,
                    },
                    "expected_revision": before["revision"],
                    "idempotency_key": "invalid-package-edit",
                },
            )
        unchanged = await _call(
            server,
            "module_draft",
            {"campaign_id": campaign_id, "action": "get", "payload": {"job_id": job_id}},
        )
        assert unchanged["job"] == before

        edit_arguments = {
            "campaign_id": campaign_id,
            "action": "edit",
            "payload": {
                "job_id": job_id,
                "operation": "package",
                "note": "Luna reviewed the complete Pack manifest and source receipts.",
                "manifest": valid_manifest,
                "narrative": {"dossiers": [], "endings": []},
                "dependencies": [],
                "version": "1.0.0",
            },
            "expected_revision": before["revision"],
            "idempotency_key": "valid-package-edit",
        }
        edited = await _call(server, "module_draft", edit_arguments)
        assert edited["job"]["revision"] == before["revision"] + 1
        reviewed = await _call(
            server,
            "module_draft",
            {
                "campaign_id": campaign_id,
                "action": "get",
                "payload": {"job_id": job_id, "view": "package"},
            },
        )
        assert reviewed["pack_draft"]["manifest"] == valid_manifest
        with pytest.raises(ToolError, match="revision"):
            await _call(
                server,
                "module_draft",
                {
                    "campaign_id": campaign_id,
                    "action": "edit",
                    "payload": {
                        "job_id": job_id,
                        "operation": "package",
                        "metadata": {"stale": True},
                    },
                    "expected_revision": before["revision"],
                    "idempotency_key": "stale-package-edit",
                },
            )
        after_stale = await _call(
            server,
            "module_draft",
            {
                "campaign_id": campaign_id,
                "action": "get",
                "payload": {"job_id": job_id, "view": "package"},
            },
        )
        assert after_stale == reviewed

        finalized = await _call(
            server,
            "module_draft",
            {
                "campaign_id": campaign_id,
                "action": "finalize",
                "payload": {
                    "job_id": job_id,
                    "pack_id": "dnd5e.module.the-lantern-below",
                    "confirmation": {
                        "confirmed": True,
                        "note": "Luna reviewed the current Pack decisions and final evidence.",
                    },
                },
                "idempotency_key": "module-finalize",
            },
        )
        imported = await _call(
            server,
            "content_pack",
            {
                "action": "import",
                "payload": {
                    "campaign_id": campaign_id,
                    "kind": "module",
                    "artifact": finalized["artifact"],
                },
                "idempotency_key": "module-import",
            },
        )
        current = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign_id}},
        )
        activated = await _call(
            server,
            "content_pack",
            {
                "action": "activate",
                "payload": {
                    "campaign_id": campaign_id,
                    "kind": "module",
                    "module_id": imported["module_id"],
                },
                "expected_revision": current["revision"],
                "idempotency_key": "module-activate",
            },
        )
        module_id = activated["activation"]["module_id"]
        current = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign_id}},
        )
        playable = await _call(
            server,
            "playthrough_manifest",
            {
                "campaign_id": campaign_id,
                "action": "initialize",
                "payload": {
                    "manifest": new_playthrough_manifest(
                        run_id="luna-run",
                        campaign_line_id="luna-line",
                        module_ids=[module_id],
                        recommended_party_minimum=None,
                        recommended_party_maximum=None,
                        selected_party_size=None,
                        source_refs=[],
                    )
                },
                "expected_revision": current["revision"],
                "idempotency_key": "playthrough-initialize",
            },
        )
        assert playable["manifest"]["module_ids"] == [module_id]
        scene_index = await _call(
            server,
            "module_query",
            {
                "campaign_id": campaign_id,
                "view": "index",
                "payload": {"module_id": module_id},
            },
        )
        assert scene_index and scene_index[0]["module_id"] == module_id

    asyncio.run(exercise())
