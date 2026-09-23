"""Engine-owned checks and failed-disable settlement for source-bound traps."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from test_official_expansions_mcp import _call

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import finalize_and_activate_module


def test_trap_passive_detection_is_source_bound_fail_closed_and_replayable(tmp_path: Path) -> None:
    profile = {"profile_id": "srd5.1.collapsing_roof"}
    marker = json.dumps(profile, sort_keys=True, separators=(",", ":"))
    excerpt = f"The collapsing roof trap has this source profile.\ntrap_profile: {marker}"
    sphere_profile = {"profile_id": "srd5.1.sphere_of_annihilation"}
    sphere_marker = json.dumps(sphere_profile, sort_keys=True, separators=(",", ":"))
    sphere_excerpt = (
        f"The sphere of annihilation is a complex magical hazard.\ntrap_profile: {sphere_marker}"
    )
    darts_profile = {"profile_id": "srd5.1.poison_darts"}
    darts_marker = json.dumps(darts_profile, sort_keys=True, separators=(",", ":"))
    darts_excerpt = (
        f"Four poisoned darts target creatures near the plate.\ntrap_profile: {darts_marker}"
    )
    net_profile = {"profile_id": "srd5.1.falling_net"}
    net_marker = json.dumps(net_profile, sort_keys=True, separators=(",", ":"))
    net_excerpt = f"A net covers a ten-foot square.\ntrap_profile: {net_marker}"
    source = tmp_path / "traps.md"
    source.write_text(
        f"# Traps\n\n## Collapsing roof\n\n{excerpt}\n\n"
        f"## Sphere of annihilation\n\n{sphere_excerpt}\n\n"
        f"## Poison darts\n\n{darts_excerpt}\n\n"
        f"## Falling net\n\n{net_excerpt}\n",
        encoding="utf-8",
    )
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=Path(__file__).resolve().parents[3] / "skills",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
        module_import_roots=(tmp_path,),
    )

    async def exercise() -> None:
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Trap settlement",
                    "edition": "2014",
                    "random_seed": "trap-settlement",
                    "idempotency_key": "campaign",
                },
            )
            campaign_id = campaign["id"]
            staged = await _call(
                server,
                "module_draft",
                {
                    "campaign_id": campaign_id,
                    "action": "start",
                    "idempotency_key": "draft",
                    "payload": {
                        "source_path": str(source),
                        "source_key": "trap-source",
                        "title": "Trap source",
                    },
                },
            )

            async def helper_call(target, name, arguments):
                value = await _call(target, name, arguments)
                if isinstance(value, dict) and "action" in value and "result" in value:
                    return value["result"]
                return value

            await finalize_and_activate_module(
                helper_call,
                server,
                campaign_id,
                staged,
                source_key="trap-source",
                title="Trap source",
                portable_id="dnd5e.module.trap-source",
            )
            actor_sheet = default_character_sheet()
            actor_sheet["edition"] = "2014"
            actor_sheet["combat"]["hp"] = {"value": 200, "max": 200, "temp": 0}
            actor_sheet["abilities"]["dexterity"]["score"] = 3
            actor_sheet["abilities"]["strength"]["score"] = 30
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign_id, "name": "Scout", "sheet": actor_sheet},
                    "idempotency_key": "scout",
                },
            )
            locksmith_sheet = default_character_sheet()
            locksmith_sheet["edition"] = "2014"
            locksmith_sheet["combat"]["hp"] = {"value": 200, "max": 200, "temp": 0}
            locksmith_sheet["abilities"]["dexterity"]["score"] = 30
            # A level-20 proficient locksmith makes the successful disable
            # branch deterministic even when the seeded d20 is low.
            locksmith_sheet["progression"]["level"] = 20
            locksmith_sheet["traits"]["proficiencies"]["tools"].append("thieves' tools")
            locksmith = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign_id,
                        "name": "Locksmith",
                        "sheet": locksmith_sheet,
                    },
                    "idempotency_key": "locksmith",
                },
            )
            hits = await _call(
                server,
                "module_search",
                {
                    "campaign_id": campaign_id,
                    "query": "collapsing roof",
                    "top_k": 3,
                },
            )
            expanded = await _call(server, "module_expand", {"chunk_id": hits[0]["id"]})
            source_ref = json.dumps(expanded["source_ref"], sort_keys=True, separators=(",", ":"))
            before = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign_id},
                },
            )
            detect_args = {
                "campaign_id": campaign_id,
                "trap_id": "wire-1",
                "action": "passive_detect",
                "source_ref": source_ref,
                "source_excerpt": excerpt,
                "profile": profile,
                "actor_id": actor["id"],
                "expected_revision": before["revision"],
                "idempotency_key": "detect-wire",
            }
            with pytest.raises(ToolError, match="marker"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **detect_args,
                        "profile": {"profile_id": "srd5.1.falling_net"},
                        "idempotency_key": "mismatched-profile",
                    },
                )
            detected = await _call(server, "trap_state_transition", detect_args)
            assert detected["check"]["success"] is True
            assert detected["check"]["passive"] is True
            assert detected["trap"]["detected"] is True
            assert "random_stream_receipt" not in detected
            assert await _call(server, "trap_state_transition", detect_args) == detected
            disable_args = {
                **detect_args,
                "action": "disable",
                "method": "thieves_tools",
                "area_confirmed": True,
                "expected_revision": detected["campaign_revision"],
                "idempotency_key": "disable-wire",
            }
            with pytest.raises(ToolError, match="disable settlement is unsupported"):
                await _call(server, "trap_state_transition", disable_args)
            after_reject = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign_id},
                },
            )
            assert after_reject["revision"] == detected["campaign_revision"]

            sphere_hits = await _call(
                server,
                "module_search",
                {
                    "campaign_id": campaign_id,
                    "query": "sphere of annihilation",
                    "top_k": 3,
                },
            )
            sphere_chunk = await _call(
                server,
                "module_expand",
                {
                    "chunk_id": sphere_hits[0]["id"],
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign_id},
                },
            )
            with pytest.raises(ToolError, match="no passive detection rule"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **detect_args,
                        "trap_id": "sphere-1",
                        "action": "passive_detect",
                        "source_ref": json.dumps(
                            sphere_chunk["source_ref"], sort_keys=True, separators=(",", ":")
                        ),
                        "source_excerpt": sphere_excerpt,
                        "profile": sphere_profile,
                        "expected_revision": current["revision"],
                        "idempotency_key": "sphere-passive",
                    },
                )
            with pytest.raises(ToolError, match="has no disable check"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **detect_args,
                        "trap_id": "sphere-1",
                        "action": "disable",
                        "source_ref": json.dumps(
                            sphere_chunk["source_ref"], sort_keys=True, separators=(",", ":")
                        ),
                        "source_excerpt": sphere_excerpt,
                        "profile": sphere_profile,
                        "idempotency_key": "sphere-disable",
                    },
                )

            darts_hits = await _call(
                server,
                "module_search",
                {
                    "campaign_id": campaign_id,
                    "query": "poison darts",
                    "top_k": 3,
                },
            )
            darts_chunk = await _call(
                server,
                "module_expand",
                {
                    "chunk_id": darts_hits[0]["id"],
                },
            )
            current = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign_id},
                },
            )
            with pytest.raises(ToolError, match="has no disable check"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **detect_args,
                        "action": "disable",
                        "source_ref": json.dumps(
                            darts_chunk["source_ref"], sort_keys=True, separators=(",", ":")
                        ),
                        "source_excerpt": darts_excerpt,
                        "profile": darts_profile,
                        "expected_revision": current["revision"],
                        "idempotency_key": "darts-disable-unsupported",
                    },
                )
            darts_args = {
                **detect_args,
                "trap_id": "darts-1",
                "action": "trigger",
                "source_ref": json.dumps(
                    darts_chunk["source_ref"], sort_keys=True, separators=(",", ":")
                ),
                "source_excerpt": darts_excerpt,
                "profile": darts_profile,
                "target_ids": [actor["id"]],
                "area_confirmed": True,
                "expected_revision": current["revision"],
                "idempotency_key": "darts-trigger",
            }
            darts_result = await _call(server, "trap_state_transition", darts_args)
            assert darts_result["trap"]["status"] == "spent"
            assert len(darts_result["darts"]) == 4
            assert darts_result["random_stream_receipt"]["draw_count"] >= 8
            assert await _call(server, "trap_state_transition", darts_args) == darts_result

            net_hits = await _call(
                server,
                "module_search",
                {
                    "campaign_id": campaign_id,
                    "query": "falling net",
                    "top_k": 3,
                },
            )
            net_chunk = await _call(server, "module_expand", {"chunk_id": net_hits[0]["id"]})
            current = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign_id},
                },
            )
            net_args = {
                **detect_args,
                "trap_id": "net-1",
                "action": "trigger",
                "source_ref": json.dumps(
                    net_chunk["source_ref"], sort_keys=True, separators=(",", ":")
                ),
                "source_excerpt": net_excerpt,
                "profile": net_profile,
                "area_confirmed": True,
                "expected_revision": current["revision"],
                "idempotency_key": "net-trigger",
            }
            net_result = await _call(server, "trap_state_transition", net_args)
            assert net_result["trap"]["status"] == "triggered"
            assert net_result["check"]["ability"] == "dexterity"
            assert "random_stream_receipt" in net_result
            if net_result["check"]["success"] is False:
                assert actor["id"] in net_result["trap"]["restrained_actor_ids"]
                current = await _call(
                    server,
                    "campaign_query",
                    {
                        "view": "get",
                        "payload": {"campaign_id": campaign_id},
                    },
                )
                escape_args = {
                    **net_args,
                    "action": "escape",
                    "area_confirmed": None,
                    "expected_revision": current["revision"],
                    "idempotency_key": "net-escape",
                }
                escaped = await _call(server, "trap_state_transition", escape_args)
                assert escaped["check"]["success"] is True
                assert actor["id"] not in escaped["trap"]["restrained_actor_ids"]

            current = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign_id},
                },
            )
            disable_success_args = {
                **net_args,
                "trap_id": "net-disabled",
                "action": "disable",
                "actor_id": locksmith["id"],
                "method": "thieves_tools",
                "expected_revision": current["revision"],
                "idempotency_key": "net-disable-success",
            }
            disable_success = await _call(server, "trap_state_transition", disable_success_args)
            assert disable_success["disable_check"]["success"] is True, disable_success[
                "disable_check"
            ]
            assert disable_success["trap"]["status"] == "disabled"
            assert (
                await _call(server, "trap_state_transition", disable_success_args)
                == disable_success
            )

            current = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign_id},
                },
            )
            disable_failure_args = {
                **net_args,
                "trap_id": "net-triggered-by-failure",
                "action": "disable",
                "method": "edged_tool",
                "expected_revision": current["revision"],
                "idempotency_key": "net-disable-failure",
            }
            disable_failure = await _call(server, "trap_state_transition", disable_failure_args)
            assert disable_failure["disable_check"]["success"] is False
            assert disable_failure["trap"]["status"] == "triggered"
            assert disable_failure["check"]["kind"] == "save"
            assert disable_failure["random_stream_receipt"]["draw_count"] >= 3
            assert (
                await _call(server, "trap_state_transition", disable_failure_args)
                == disable_failure
            )
        finally:
            close_server(server)

        restarted = create_server(config)
        try:
            assert await _call(restarted, "trap_state_transition", detect_args) == detected
            assert await _call(restarted, "trap_state_transition", darts_args) == darts_result
            assert (
                await _call(restarted, "trap_state_transition", disable_success_args)
                == disable_success
            )
            assert (
                await _call(restarted, "trap_state_transition", disable_failure_args)
                == disable_failure
            )
        finally:
            close_server(restarted)

    asyncio.run(exercise())
