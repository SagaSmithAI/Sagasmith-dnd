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
    fire_profile = {"profile_id": "srd5.1.fire_breathing_statue"}
    fire_marker = json.dumps(fire_profile, sort_keys=True, separators=(",", ":"))
    fire_excerpt = f"A statue releases a thirty-foot cone of fire.\ntrap_profile: {fire_marker}"
    pit_profile = {"profile_id": "srd5.1.poisoned_spiked_hidden_pit"}
    pit_marker = json.dumps(pit_profile, sort_keys=True, separators=(",", ":"))
    pit_excerpt = f"A covered pit has poisoned spikes.\ntrap_profile: {pit_marker}"
    locking_pit_profile = {"profile_id": "srd5.1.locking_pit"}
    locking_pit_marker = json.dumps(locking_pit_profile, sort_keys=True, separators=(",", ":"))
    locking_pit_excerpt = f"A locking pit traps its fallers.\ntrap_profile: {locking_pit_marker}"
    spiked_locking_pit_profile = {"profile_id": "srd5.1.spiked_locking_pit"}
    spiked_locking_pit_marker = json.dumps(
        spiked_locking_pit_profile, sort_keys=True, separators=(",", ":")
    )
    spiked_locking_pit_excerpt = (
        f"A spiked locking pit traps creatures below.\ntrap_profile: {spiked_locking_pit_marker}"
    )
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
    needle_profile = {"profile_id": "srd5.1.poison_needle"}
    needle_marker = json.dumps(needle_profile, sort_keys=True, separators=(",", ":"))
    needle_excerpt = (
        "Opening this lock fires a poisoned needle at the opener.\n"
        f"trap_profile: {needle_marker}"
    )
    source = tmp_path / "traps.md"
    source.write_text(
        f"# Traps\n\n## Collapsing roof\n\n{excerpt}\n\n"
        f"## Fire-Breathing Statue\n\n{fire_excerpt}\n\n"
        f"## Poisoned Spiked Hidden Pit\n\n{pit_excerpt}\n\n"
        f"## Locking Pit\n\n{locking_pit_excerpt}\n\n"
        f"## Spiked Locking Pit\n\n{spiked_locking_pit_excerpt}\n\n"
        f"## Sphere of annihilation\n\n{sphere_excerpt}\n\n"
        f"## Poison darts\n\n{darts_excerpt}\n\n"
        f"## Falling net\n\n{net_excerpt}\n\n"
        f"## Poison Needle\n\n{needle_excerpt}\n",
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
            actor_sheet["abilities"]["constitution"]["score"] = 3
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign_id, "name": "Scout", "sheet": actor_sheet},
                    "idempotency_key": "scout",
                },
            )
            captive_sheet = default_character_sheet()
            captive_sheet["edition"] = "2014"
            captive_sheet["combat"]["hp"] = {"value": 200, "max": 200, "temp": 0}
            captive_sheet["abilities"]["strength"]["score"] = 3
            captive = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign_id,
                        "name": "Captive",
                        "sheet": captive_sheet,
                    },
                    "idempotency_key": "captive",
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
            with pytest.raises(ToolError, match="distinct explicitly confirmed targets"):
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

            roof_unconfirmed_args = {
                **detect_args,
                "trap_id": "roof-unconfirmed-failure",
                "action": "disable",
                "method": "edged_tool",
                "expected_revision": after_reject["revision"],
                "idempotency_key": "roof-unconfirmed-failure",
            }
            with pytest.raises(ToolError, match="explicit confirmed source-defined area fact"):
                await _call(server, "trap_state_transition", roof_unconfirmed_args)
            roof_after_unconfirmed = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert roof_after_unconfirmed["revision"] == after_reject["revision"]

            roof_disable_success_args = {
                **detect_args,
                "trap_id": "roof-disabled",
                "action": "disable",
                "method": "thieves_tools",
                "actor_id": locksmith["id"],
                "area_confirmed": True,
                "target_ids": [actor["id"]],
                "expected_revision": roof_after_unconfirmed["revision"],
                "idempotency_key": "roof-disable-success",
            }
            roof_disable_success = await _call(
                server, "trap_state_transition", roof_disable_success_args
            )
            assert roof_disable_success["disable_check"]["success"] is True
            assert roof_disable_success["trap"]["status"] == "disabled"
            assert "targets" not in roof_disable_success
            assert "affected_actor_ids" not in roof_disable_success
            assert roof_disable_success["trap"]["source_ref"] == source_ref
            assert (
                await _call(server, "trap_state_transition", roof_disable_success_args)
                == roof_disable_success
            )

            roof_current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            roof_disable_failure_args = {
                **detect_args,
                "trap_id": "roof-failed-disable-triggered",
                "action": "disable",
                "method": "edged_tool",
                "area_confirmed": True,
                "target_ids": [actor["id"]],
                "expected_revision": roof_current["revision"],
                "idempotency_key": "roof-disable-failure",
            }
            roof_disable_failure = await _call(
                server, "trap_state_transition", roof_disable_failure_args
            )
            assert roof_disable_failure["disable_check"]["success"] is False
            assert roof_disable_failure["trap"]["status"] == "spent"
            assert roof_disable_failure["trap"]["source_ref"] == source_ref
            assert roof_disable_failure["check"]["ability"] == "dexterity"
            assert roof_disable_failure["targets"][0]["save"]["dc"] == 15
            assert isinstance(roof_disable_failure["targets"][0]["save"]["success"], bool)
            assert roof_disable_failure["damage_roll"]["expression"] == "4d10"
            assert roof_disable_failure["terrain_effect"]["effect"] == "rubble"
            assert roof_disable_failure["terrain_effect"]["source_ref"] == source_ref
            assert roof_disable_failure["trap"]["affected_actor_ids"] == [actor["id"]]
            assert (
                await _call(server, "trap_state_transition", roof_disable_failure_args)
                == roof_disable_failure
            )

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
                "trigger_fact": {
                    "kind": "pressure_plate_weight",
                    "scene_id": darts_chunk["scene"]["id"],
                    "plate_id": "darts-1",
                    "weight_lb": 21,
                },
                "expected_revision": current["revision"],
                "idempotency_key": "darts-trigger",
            }
            darts_result = await _call(server, "trap_state_transition", darts_args)
            assert darts_result["trap"]["status"] == "spent"
            assert darts_result["trap"]["trigger_fact"] == darts_args["trigger_fact"]
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

            needle_hits = await _call(
                server,
                "module_search",
                {"campaign_id": campaign_id, "query": "Poison Needle", "top_k": 3},
            )
            needle_chunk = await _call(
                server, "module_expand", {"chunk_id": needle_hits[0]["id"]}
            )
            needle_source_ref = json.dumps(
                needle_chunk["source_ref"], sort_keys=True, separators=(",", ":")
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            needle_success_args = {
                **detect_args,
                "trap_id": "needle-safe",
                "action": "disable",
                "source_ref": needle_source_ref,
                "source_excerpt": needle_excerpt,
                "profile": needle_profile,
                "actor_id": locksmith["id"],
                "method": "thieves_tools",
                "area_confirmed": True,
                "expected_revision": current["revision"],
                "idempotency_key": "needle-disable-success",
            }
            needle_success = await _call(
                server, "trap_state_transition", needle_success_args
            )
            assert needle_success["disable_check"]["success"] is True
            assert needle_success["disable_check"]["tool_proficient"] is True
            assert needle_success["trap"]["status"] == "disabled"
            assert needle_success["trap"]["source_ref"] == needle_source_ref
            assert "piercing" not in needle_success and "poison" not in needle_success
            assert (
                await _call(server, "trap_state_transition", needle_success_args)
                == needle_success
            )

            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            needle_failure_args = {
                **needle_success_args,
                "trap_id": "needle-triggered-by-failure",
                "actor_id": actor["id"],
                "expected_revision": current["revision"],
                "idempotency_key": "needle-disable-failure",
            }
            needle_failure = await _call(
                server, "trap_state_transition", needle_failure_args
            )
            assert needle_failure["disable_check"]["success"] is False
            assert needle_failure["trap"]["status"] == "spent"
            assert needle_failure["trap"]["source_ref"] == needle_source_ref
            assert needle_failure["target_id"] == actor["id"]
            assert needle_failure["piercing"]["hp_damage"] == 1
            assert needle_failure["poison"]["damage_expression"] == "2d10"
            if needle_failure["poison"]["success"] is False:
                assert needle_failure["condition_effect"]["source"].startswith(
                    "trap-poison-needle:"
                )
                assert needle_failure["condition_effect"]["metadata"]["trap_state"][
                    "source_ref"
                ] == needle_source_ref
            else:
                assert needle_failure["condition_effect"] is None
            assert "random_stream_receipt" in needle_failure
            assert (
                await _call(server, "trap_state_transition", needle_failure_args)
                == needle_failure
            )

            fire_hits = await _call(
                server,
                "module_search",
                {"campaign_id": campaign_id, "query": "Fire-Breathing Statue", "top_k": 3},
            )
            fire_chunk = await _call(server, "module_expand", {"chunk_id": fire_hits[0]["id"]})
            fire_source_ref = json.dumps(
                fire_chunk["source_ref"], sort_keys=True, separators=(",", ":")
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            fire_rejected_args = {
                **detect_args,
                "trap_id": "statue-1",
                "action": "trigger",
                "source_ref": fire_source_ref,
                "source_excerpt": fire_excerpt,
                "profile": fire_profile,
                "actor_id": actor["id"],
                "target_ids": [actor["id"]],
                "area_confirmed": False,
                "expected_revision": current["revision"],
                "idempotency_key": "statue-unconfirmed",
            }
            with pytest.raises(ToolError, match="area fact"):
                await _call(server, "trap_state_transition", fire_rejected_args)
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert unchanged["revision"] == current["revision"]
            fire_args = {
                **fire_rejected_args,
                "area_confirmed": True,
                "trigger_fact": {
                    "kind": "pressure_plate_weight",
                    "scene_id": fire_chunk["scene"]["id"],
                    "plate_id": "statue-1",
                    "weight_lb": 21,
                },
                "expected_revision": unchanged["revision"],
                "idempotency_key": "statue-trigger",
            }
            with pytest.raises(ToolError, match="greater than 20 lb"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **fire_args,
                        "trigger_fact": {**fire_args["trigger_fact"], "weight_lb": 20},
                        "idempotency_key": "statue-weight-20",
                    },
                )
            with pytest.raises(ToolError, match="does not match the source-defined scene"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **fire_args,
                        "trigger_fact": {
                            **fire_args["trigger_fact"],
                            "scene_id": "unrelated-scene",
                        },
                        "idempotency_key": "statue-wrong-scene",
                    },
                )
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert unchanged["revision"] == current["revision"]
            fire_result = await _call(server, "trap_state_transition", fire_args)
            assert fire_result["trap"]["status"] == "spent"
            assert fire_result["trap"]["trigger_fact"] == fire_args["trigger_fact"]
            assert fire_result["damage_roll"]["expression"] == "4d10"
            assert isinstance(fire_result["targets"][0]["save"]["success"], bool)
            assert fire_result["random_stream_receipt"]["draw_count"] >= 2
            assert await _call(server, "trap_state_transition", fire_args) == fire_result

            pit_hits = await _call(
                server,
                "module_search",
                {
                    "campaign_id": campaign_id,
                    "query": "Poisoned Spiked Hidden Pit",
                    "top_k": 3,
                },
            )
            pit_chunk = await _call(server, "module_expand", {"chunk_id": pit_hits[0]["id"]})
            pit_source_ref = json.dumps(
                pit_chunk["source_ref"], sort_keys=True, separators=(",", ":")
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            pit_rejected_args = {
                **detect_args,
                "trap_id": "pit-1",
                "action": "trigger",
                "source_ref": pit_source_ref,
                "source_excerpt": pit_excerpt,
                "profile": pit_profile,
                "actor_id": actor["id"],
                "target_ids": [actor["id"]],
                "area_confirmed": True,
                "trap_depth_ft": 15,
                "trigger_fact": {
                    "kind": "step_on_cover",
                    "scene_id": pit_chunk["scene"]["id"],
                    "cover_id": "pit-1",
                },
                "expected_revision": current["revision"],
                "idempotency_key": "pit-invalid-depth",
            }
            with pytest.raises(ToolError, match="outside the fixed source profile"):
                await _call(server, "trap_state_transition", pit_rejected_args)
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert unchanged["revision"] == current["revision"]
            with pytest.raises(ToolError, match="identify this trap component"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **pit_rejected_args,
                        "trap_depth_ft": 20,
                        "trigger_fact": {
                            **pit_rejected_args["trigger_fact"],
                            "cover_id": "other-cover",
                        },
                        "idempotency_key": "pit-wrong-cover",
                    },
                )
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert unchanged["revision"] == current["revision"]
            pit_args = {
                **pit_rejected_args,
                "trap_depth_ft": 20,
                "expected_revision": unchanged["revision"],
                "idempotency_key": "pit-trigger",
            }
            pit_result = await _call(server, "trap_state_transition", pit_args)
            assert pit_result["trap"]["status"] == "spent"
            assert pit_result["trap"]["trigger_fact"] == pit_args["trigger_fact"]
            assert pit_result["trap_depth_ft"] == 20
            assert pit_result["targets"][0]["fall"]["dice_count"] == 2
            assert pit_result["targets"][0]["spikes"]["expression"] == "2d10"
            assert isinstance(pit_result["targets"][0]["poison"]["success"], bool)
            assert pit_result["random_stream_receipt"]["draw_count"] >= 4
            assert await _call(server, "trap_state_transition", pit_args) == pit_result

            locking_hits = await _call(
                server,
                "module_search",
                {"campaign_id": campaign_id, "query": "Locking Pit", "top_k": 5},
            )
            locking_chunk = await _call(
                server, "module_expand", {"chunk_id": locking_hits[0]["id"]}
            )
            locking_source_ref = json.dumps(
                locking_chunk["source_ref"], sort_keys=True, separators=(",", ":")
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            locking_invalid_depth_args = {
                **detect_args,
                "trap_id": "locking-pit-invalid-depth",
                "action": "trigger",
                "source_ref": locking_source_ref,
                "source_excerpt": locking_pit_excerpt,
                "profile": locking_pit_profile,
                "target_ids": [captive["id"]],
                "area_confirmed": True,
                "trap_depth_ft": 15,
                "trigger_fact": {
                    "kind": "step_on_cover",
                    "scene_id": locking_chunk["scene"]["id"],
                    "cover_id": "locking-pit-invalid-depth",
                },
                "expected_revision": current["revision"],
                "idempotency_key": "locking-pit-invalid-depth",
            }
            with pytest.raises(ToolError, match="outside the fixed source profile"):
                await _call(server, "trap_state_transition", locking_invalid_depth_args)
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert unchanged["revision"] == current["revision"]
            locking_args = {
                **detect_args,
                "trap_id": "locking-pit-10ft",
                "action": "trigger",
                "source_ref": locking_source_ref,
                "source_excerpt": locking_pit_excerpt,
                "profile": locking_pit_profile,
                "target_ids": [actor["id"], captive["id"]],
                "area_confirmed": True,
                "trap_depth_ft": 10,
                "trigger_fact": {
                    "kind": "step_on_cover",
                    "scene_id": locking_chunk["scene"]["id"],
                    "cover_id": "locking-pit-10ft",
                },
                "expected_revision": unchanged["revision"],
                "idempotency_key": "locking-pit-trigger",
            }
            locking_result = await _call(server, "trap_state_transition", locking_args)
            assert locking_result["trap"]["status"] == "triggered"
            assert locking_result["trap"]["source_ref"] == locking_source_ref
            assert locking_result["trap_depth_ft"] == 10
            assert locking_result["trap"]["trigger_fact"] == locking_args["trigger_fact"]
            assert set(locking_result["trap"]["contained_actor_ids"]) == {
                actor["id"],
                captive["id"],
            }
            assert "position" not in locking_result["trap"]
            assert await _call(server, "trap_state_transition", locking_args) == locking_result

            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            weak_escape_args = {
                **locking_args,
                "action": "escape",
                "target_ids": None,
                "area_confirmed": None,
                "trap_depth_ft": None,
                "actor_id": captive["id"],
                "expected_revision": current["revision"],
                "idempotency_key": "locking-pit-weak-escape",
            }
            weak_escape = await _call(server, "trap_state_transition", weak_escape_args)
            assert weak_escape["check"]["ability"] == "strength"
            assert weak_escape["check"]["dc"] == 20
            assert weak_escape["check"]["success"] is False
            assert weak_escape["trap"]["status"] == "triggered"
            assert captive["id"] in weak_escape["trap"]["contained_actor_ids"]
            assert await _call(server, "trap_state_transition", weak_escape_args) == weak_escape

            strong_escape_attempts = []
            for attempt in range(1, 21):
                current = await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign_id}},
                )
                strong_escape_args = {
                    **weak_escape_args,
                    "actor_id": actor["id"],
                    "expected_revision": current["revision"],
                    "idempotency_key": f"locking-pit-strong-escape-{attempt}",
                }
                strong_escape = await _call(
                    server, "trap_state_transition", strong_escape_args
                )
                assert await _call(
                    server, "trap_state_transition", strong_escape_args
                ) == strong_escape
                strong_escape_attempts.append((strong_escape_args, strong_escape))
                if strong_escape["check"]["success"]:
                    break
            else:
                pytest.fail("20 engine Strength checks did not produce an escape success")
            assert strong_escape["check"]["ability"] == "strength"
            assert strong_escape["check"]["dc"] == 20
            assert strong_escape["check"]["success"] is True
            assert strong_escape["trap"]["status"] == "triggered"
            assert actor["id"] not in strong_escape["trap"]["contained_actor_ids"]
            assert captive["id"] in strong_escape["trap"]["contained_actor_ids"]
            assert "position" not in strong_escape["trap"]

            spiked_hits = await _call(
                server,
                "module_search",
                {"campaign_id": campaign_id, "query": "Spiked Locking Pit", "top_k": 5},
            )
            spiked_chunk = await _call(
                server, "module_expand", {"chunk_id": spiked_hits[0]["id"]}
            )
            spiked_source_ref = json.dumps(
                spiked_chunk["source_ref"], sort_keys=True, separators=(",", ":")
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            spiked_args = {
                **locking_args,
                "trap_id": "spiked-locking-pit-20ft",
                "source_ref": spiked_source_ref,
                "source_excerpt": spiked_locking_pit_excerpt,
                "profile": spiked_locking_pit_profile,
                "target_ids": [captive["id"]],
                "trap_depth_ft": 20,
                "trigger_fact": {
                    "kind": "step_on_cover",
                    "scene_id": spiked_chunk["scene"]["id"],
                    "cover_id": "spiked-locking-pit-20ft",
                },
                "expected_revision": current["revision"],
                "idempotency_key": "spiked-locking-pit-trigger",
            }
            spiked_result = await _call(server, "trap_state_transition", spiked_args)
            assert spiked_result["trap"]["status"] == "triggered"
            assert spiked_result["trap"]["source_ref"] == spiked_source_ref
            assert spiked_result["trap_depth_ft"] == 20
            assert spiked_result["trap"]["trigger_fact"] == spiked_args["trigger_fact"]
            assert captive["id"] in spiked_result["trap"]["contained_actor_ids"]
            assert spiked_result["targets"][0]["spikes"]["expression"] == "2d10"
            assert await _call(server, "trap_state_transition", spiked_args) == spiked_result

            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            spiked_escape_args = {
                **spiked_args,
                "action": "escape",
                "target_ids": None,
                "area_confirmed": None,
                "trap_depth_ft": None,
                "actor_id": captive["id"],
                "expected_revision": current["revision"],
                "idempotency_key": "spiked-locking-pit-escape",
            }
            spiked_escape = await _call(server, "trap_state_transition", spiked_escape_args)
            assert spiked_escape["check"]["ability"] == "strength"
            assert spiked_escape["check"]["dc"] == 20
            assert spiked_escape["check"]["success"] is False
            assert captive["id"] in spiked_escape["trap"]["contained_actor_ids"]
            assert await _call(server, "trap_state_transition", spiked_escape_args) == spiked_escape
        finally:
            close_server(server)

        restarted = create_server(config)
        try:
            assert await _call(restarted, "trap_state_transition", detect_args) == detected
            assert (
                await _call(restarted, "trap_state_transition", roof_disable_success_args)
                == roof_disable_success
            )
            assert (
                await _call(restarted, "trap_state_transition", roof_disable_failure_args)
                == roof_disable_failure
            )
            assert await _call(restarted, "trap_state_transition", darts_args) == darts_result
            assert (
                await _call(restarted, "trap_state_transition", disable_success_args)
                == disable_success
            )
            assert (
                await _call(restarted, "trap_state_transition", disable_failure_args)
                == disable_failure
            )
            assert (
                await _call(restarted, "trap_state_transition", needle_success_args)
                == needle_success
            )
            assert (
                await _call(restarted, "trap_state_transition", needle_failure_args)
                == needle_failure
            )
            assert await _call(restarted, "trap_state_transition", fire_args) == fire_result
            assert await _call(restarted, "trap_state_transition", pit_args) == pit_result
            assert await _call(restarted, "trap_state_transition", locking_args) == locking_result
            assert await _call(restarted, "trap_state_transition", weak_escape_args) == weak_escape
            for replay_args, replay_result in strong_escape_attempts:
                assert (
                    await _call(restarted, "trap_state_transition", replay_args)
                    == replay_result
                )
            assert await _call(restarted, "trap_state_transition", spiked_args) == spiked_result
            assert (
                await _call(restarted, "trap_state_transition", spiked_escape_args)
                == spiked_escape
            )
        finally:
            close_server(restarted)

    asyncio.run(exercise())
