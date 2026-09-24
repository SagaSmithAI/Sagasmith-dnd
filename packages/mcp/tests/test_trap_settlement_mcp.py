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
    rolling_sphere_profile = {"profile_id": "srd5.1.rolling_sphere"}
    rolling_sphere_marker = json.dumps(
        rolling_sphere_profile, sort_keys=True, separators=(",", ":")
    )
    rolling_sphere_excerpt = (
        "When 20 or more pounds of pressure are placed on this trap's pressure plate, "
        "a hidden trapdoor in the ceiling opens, releasing a 10-foot diameter rolling "
        "sphere of solid stone.\n\n"
        "With a successful DC 15 Wisdom (Perception) check, a character can spot the "
        "trapdoor and pressure plate. A search of the floor accompanied by a successful "
        "DC 15 Intelligence (Investigation) check reveals variations in the mortar and "
        "stone that betray the pressure plate's presence. The same check made while "
        "inspecting the ceiling notes variations in the stonework that reveal the "
        "trapdoor. Wedging an iron spike or other object under the pressure plate "
        "prevents the trap from activating.\n\n"
        "Activation of the sphere requires all creatures present to roll initiative. "
        "The sphere rolls initiative with a +8 bonus. On its turn, it moves 60 feet in "
        "a straight line. The sphere can move through creatures' spaces, and creatures "
        "can move through its space, treating it as difficult terrain. Whenever the "
        "sphere enters a creature's space or a creature enters its space while it's "
        "rolling, that creature must succeed on a DC 15 Dexterity saving throw or take "
        "55 (10d10) bludgeoning damage and be knocked prone.\n\n"
        "The sphere stops when it hits a wall or similar barrier. It can't go around "
        "corners, but smart dungeon builders incorporate gentle, curving turns into "
        "nearby passages that allow the sphere to keep moving.\n\n"
        "As an action, a creature within 5 feet of the sphere can attempt to slow it "
        "down with a DC 20 Strength check. On a successful check, the sphere's speed "
        "is reduced by 15 feet. If the sphere's speed drops to 0, it stops moving and "
        "is no longer a threat.\n"
        f"trap_profile: {rolling_sphere_marker}"
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
    net_excerpt = (
        "A net covers a ten-foot square. Creatures in the area are restrained; "
        "each makes a DC 10 Strength save and those that fail are also knocked "
        f"prone.\ntrap_profile: {net_marker}"
    )
    needle_profile = {"profile_id": "srd5.1.poison_needle"}
    needle_marker = json.dumps(needle_profile, sort_keys=True, separators=(",", ":"))
    needle_excerpt = (
        f"Opening this lock fires a poisoned needle at the opener.\ntrap_profile: {needle_marker}"
    )
    source = tmp_path / "traps.md"
    source.write_text(
        f"# Traps\n\n## Collapsing roof\n\n{excerpt}\n\n"
        f"## Fire-Breathing Statue\n\n{fire_excerpt}\n\n"
        f"## Poisoned Spiked Hidden Pit\n\n{pit_excerpt}\n\n"
        f"## Locking Pit\n\n{locking_pit_excerpt}\n\n"
        f"## Spiked Locking Pit\n\n{spiked_locking_pit_excerpt}\n\n"
        f"## Rolling Sphere\n\n{rolling_sphere_excerpt}\n\n"
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
            captive_sheet["abilities"]["dexterity"]["score"] = 1
            captive_sheet["conditions"] = ["poisoned"]
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
            locksmith_sheet["abilities"]["intelligence"]["score"] = 30
            locksmith_sheet["abilities"]["wisdom"]["score"] = 20
            locksmith_sheet["skills"]["arcana"]["proficiency"] = "expertise"
            locksmith_sheet["skills"]["perception"]["proficiency"] = "expertise"
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
            fire_hits = await _call(
                server,
                "module_search",
                {"campaign_id": campaign_id, "query": "Fire-Breathing Statue", "top_k": 3},
            )
            fire_chunk = await _call(server, "module_expand", {"chunk_id": fire_hits[0]["id"]})
            fire_source_ref = json.dumps(
                fire_chunk["source_ref"], sort_keys=True, separators=(",", ":")
            )
            before_fire_detect = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            fire_detect_args = {
                **detect_args,
                "trap_id": "statue-detected",
                "action": "detect",
                "source_ref": fire_source_ref,
                "source_excerpt": fire_excerpt,
                "profile": fire_profile,
                "actor_id": locksmith["id"],
                "method": "perception",
                "expected_revision": before_fire_detect["revision"],
                "idempotency_key": "statue-detect-active",
            }
            with pytest.raises(ToolError, match="requires selecting one source-defined ability"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **fire_detect_args,
                        "method": None,
                        "idempotency_key": "statue-detect-method-required",
                    },
                )
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert unchanged["revision"] == before_fire_detect["revision"]
            fire_detect = await _call(server, "trap_state_transition", fire_detect_args)
            assert fire_detect["check"]["ability"] == "perception"
            assert fire_detect["check"]["success"] is True
            assert fire_detect["revealed_facts"] == [
                "hidden_pressure_plate",
                "faint_scorch_marks_on_floor_and_walls",
            ]
            assert "random_stream_receipt" in fire_detect
            assert await _call(server, "trap_state_transition", fire_detect_args) == fire_detect
            before_arcana_detect = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            fire_arcana_args = {
                **fire_detect_args,
                "trap_id": "statue-detect-arcana",
                "method": "arcana",
                "expected_revision": before_arcana_detect["revision"],
                "idempotency_key": "statue-detect-arcana",
            }
            fire_arcana = await _call(server, "trap_state_transition", fire_arcana_args)
            assert fire_arcana["check"]["ability"] == "arcana"
            assert fire_arcana["check"]["dc"] == 15
            assert len(fire_arcana["checks"]) == 1
            assert fire_arcana["revealed_facts"] == ["magic_trap"]
            assert await _call(server, "trap_state_transition", fire_arcana_args) == fire_arcana
            before_spell_disable = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            fire_wrong_disable_method = {
                **fire_detect_args,
                "trap_id": "statue-arcana-disable",
                "action": "disable",
                "method": "perception",
                "expected_revision": before_spell_disable["revision"],
                "idempotency_key": "statue-disable-wrong-method",
            }
            with pytest.raises(ToolError, match="disable method must match"):
                await _call(server, "trap_state_transition", fire_wrong_disable_method)
            after_wrong_disable_method = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert after_wrong_disable_method["revision"] == before_spell_disable["revision"]
            fire_arcana_disable_args = {
                **fire_wrong_disable_method,
                "method": "arcana",
                "expected_revision": after_wrong_disable_method["revision"],
                "idempotency_key": "statue-disable-arcana",
            }
            fire_arcana_disable = await _call(
                server, "trap_state_transition", fire_arcana_disable_args
            )
            assert fire_arcana_disable["check"]["ability"] == "arcana"
            assert fire_arcana_disable["check"]["dc"] == 15
            assert fire_arcana_disable["trap"]["status"] == "disabled"
            assert (
                await _call(server, "trap_state_transition", fire_arcana_disable_args)
                == fire_arcana_disable
            )
            after_fire_arcana_disable = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            with pytest.raises(ToolError, match="only an armed Fire-Breathing Statue"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **fire_arcana_disable_args,
                        "expected_revision": after_fire_arcana_disable["revision"],
                        "idempotency_key": "statue-disable-already-disabled",
                    },
                )
            after_rejected_repeat = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert after_rejected_repeat["revision"] == after_fire_arcana_disable["revision"]
            encounter_revision = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign_id,
                    "positioning_mode": "agent",
                    "participant_ids": [actor["id"], captive["id"], locksmith["id"]],
                    "participant_config": [
                        {"actor_id": actor["id"], "initiative": 20},
                        {"actor_id": captive["id"], "initiative": 10},
                        {"actor_id": locksmith["id"], "initiative": 5},
                    ],
                    "expected_revision": encounter_revision["revision"],
                    "idempotency_key": "trap-area-encounter",
                },
            )
            active_encounter = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            encounter_id = active_encounter["state"]["combat"]["id"]

            def reviewed_area_facts(
                *,
                scene_id: str,
                trap_id: str,
                source_ref: str,
                campaign_revision: int,
                in_area_ids: set[str],
            ) -> dict:
                participants = [actor, captive, locksmith]
                return {
                    "decision_id": f"review-{trap_id}-{campaign_revision}",
                    "reason": "Reviewed every active encounter combatant against the trap area.",
                    "scene_id": scene_id,
                    "trap_id": trap_id,
                    "encounter_id": encounter_id,
                    "source_ref": source_ref,
                    "campaign_revision": campaign_revision,
                    "reviewed_by": "system:local",
                    "actor_facts": [
                        {"actor_id": item["id"], "in_area": item["id"] in in_area_ids}
                        for item in participants
                    ],
                }

            disable_args = {
                **detect_args,
                "action": "disable",
                "method": "thieves_tools",
                "area_confirmed": True,
                "expected_revision": active_encounter["revision"],
                "idempotency_key": "disable-wire",
            }
            with pytest.raises(ToolError, match="reviewed spatial_facts"):
                await _call(server, "trap_state_transition", disable_args)
            after_reject = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign_id},
                },
            )
            assert after_reject["revision"] == active_encounter["revision"]

            roof_unconfirmed_args = {
                **detect_args,
                "trap_id": "roof-unconfirmed-failure",
                "action": "disable",
                "method": "edged_tool",
                "expected_revision": after_reject["revision"],
                "idempotency_key": "roof-unconfirmed-failure",
            }
            with pytest.raises(ToolError, match="area spatial_facts require"):
                await _call(server, "trap_state_transition", roof_unconfirmed_args)
            roof_after_unconfirmed = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert roof_after_unconfirmed["revision"] == after_reject["revision"]
            roof_no_roll_args = {
                **detect_args,
                "trap_id": "roof-inspected-beams",
                "action": "detect",
                "method": "inspect_support_beams",
                "source_ref": source_ref,
                "source_excerpt": excerpt,
                "profile": profile,
                "actor_id": locksmith["id"],
                "expected_revision": roof_after_unconfirmed["revision"],
                "idempotency_key": "roof-inspect-support-beams",
            }
            roof_no_roll = await _call(server, "trap_state_transition", roof_no_roll_args)
            assert roof_no_roll["check"] is None
            assert roof_no_roll["checks"] == []
            assert roof_no_roll["revealed_facts"] == ["wedged_support_beams"]
            assert roof_no_roll["trap"]["detected"] is True
            assert "random_stream_receipt" not in roof_no_roll
            assert await _call(server, "trap_state_transition", roof_no_roll_args) == roof_no_roll
            roof_after_unconfirmed = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )

            roof_disable_success_args = {
                **detect_args,
                "trap_id": "roof-disabled",
                "action": "disable",
                "method": "thieves_tools",
                "actor_id": locksmith["id"],
                "area_confirmed": None,
                "target_ids": None,
                "spatial_facts": reviewed_area_facts(
                    scene_id=expanded["scene"]["id"],
                    trap_id="roof-disabled",
                    source_ref=source_ref,
                    campaign_revision=roof_after_unconfirmed["revision"],
                    in_area_ids={actor["id"]},
                ),
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
                "area_confirmed": None,
                "target_ids": None,
                "spatial_facts": reviewed_area_facts(
                    scene_id=expanded["scene"]["id"],
                    trap_id="roof-failed-disable-triggered",
                    source_ref=source_ref,
                    campaign_revision=roof_current["revision"],
                    in_area_ids={actor["id"], captive["id"]},
                ),
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
            assert roof_disable_failure["trap"]["affected_actor_ids"] == [
                actor["id"],
                captive["id"],
            ]
            assert (
                await _call(server, "trap_state_transition", roof_disable_failure_args)
                == roof_disable_failure
            )
            roof_empty_revision = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            roof_empty_args = {
                **detect_args,
                "trap_id": "roof-empty-area",
                "action": "trigger",
                "method": None,
                "source_ref": source_ref,
                "source_excerpt": excerpt,
                "profile": profile,
                "actor_id": actor["id"],
                "target_ids": None,
                "area_confirmed": None,
                "spatial_facts": reviewed_area_facts(
                    scene_id=expanded["scene"]["id"],
                    trap_id="roof-empty-area",
                    source_ref=source_ref,
                    campaign_revision=roof_empty_revision["revision"],
                    in_area_ids=set(),
                ),
                "trigger_fact": {
                    "kind": "knock_wedged_beam",
                    "scene_id": expanded["scene"]["id"],
                    "beam_id": "roof-empty-area",
                    "action_spent": True,
                },
                "expected_revision": roof_empty_revision["revision"],
                "idempotency_key": "roof-empty-area",
            }
            roof_empty = await _call(server, "trap_state_transition", roof_empty_args)
            assert roof_empty["trap"]["status"] == "spent"
            assert roof_empty["affected_actor_ids"] == []
            assert roof_empty["targets"] == []
            assert roof_empty["damage_roll"] is None
            assert roof_empty["terrain_effect"]["effect"] == "rubble"
            assert await _call(server, "trap_state_transition", roof_empty_args) == roof_empty

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
            sphere_source_ref = json.dumps(
                sphere_chunk["source_ref"], sort_keys=True, separators=(",", ":")
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
                        "source_ref": sphere_source_ref,
                        "source_excerpt": sphere_excerpt,
                        "profile": sphere_profile,
                        "expected_revision": current["revision"],
                        "idempotency_key": "sphere-passive",
                    },
                )
            sphere_detect_args = {
                **detect_args,
                "trap_id": "sphere-1",
                "action": "detect",
                "source_ref": sphere_source_ref,
                "source_excerpt": sphere_excerpt,
                "profile": sphere_profile,
                "actor_id": locksmith["id"],
                "expected_revision": current["revision"],
                "idempotency_key": "sphere-arcana-detect",
            }
            sphere_detect = await _call(server, "trap_state_transition", sphere_detect_args)
            assert sphere_detect["check"]["ability"] == "arcana"
            assert sphere_detect["check"]["success"] is True
            assert sphere_detect["revealed_facts"] == [
                "sphere_of_annihilation_in_stone_mouth",
                "sphere_cannot_be_controlled_or_moved",
            ]
            assert "random_stream_receipt" in sphere_detect
            assert await _call(server, "trap_state_transition", sphere_detect_args) == sphere_detect
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            with pytest.raises(ToolError, match="has no disable check"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **detect_args,
                        "trap_id": "sphere-1",
                        "action": "disable",
                        "source_ref": sphere_source_ref,
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
            darts_source_ref = json.dumps(
                darts_chunk["source_ref"], sort_keys=True, separators=(",", ":")
            )
            stuffed = await _call(
                server,
                "trap_state_transition",
                {
                    **detect_args,
                    "trap_id": "darts-stuffed",
                    "action": "bypass",
                    "method": "stuff_dart_holes",
                    "source_ref": darts_source_ref,
                    "source_excerpt": darts_excerpt,
                    "profile": darts_profile,
                    "expected_revision": current["revision"],
                    "idempotency_key": "darts-stuff-holes",
                },
            )
            blocked_revision = stuffed["campaign_revision"]
            with pytest.raises(ToolError, match="stuffing the dart holes"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **detect_args,
                        "trap_id": "darts-stuffed",
                        "action": "trigger",
                        "source_ref": darts_source_ref,
                        "source_excerpt": darts_excerpt,
                        "profile": darts_profile,
                        "trigger_fact": {
                            "kind": "pressure_plate_weight",
                            "scene_id": darts_chunk["scene"]["id"],
                            "plate_id": "darts-stuffed",
                            "weight_lb": 21,
                        },
                        "spatial_facts": reviewed_area_facts(
                            scene_id=darts_chunk["scene"]["id"],
                            trap_id="darts-stuffed",
                            source_ref=darts_source_ref,
                            campaign_revision=blocked_revision,
                            in_area_ids={actor["id"]},
                        ),
                        "expected_revision": blocked_revision,
                        "idempotency_key": "darts-stuffed-trigger",
                    },
                )
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert unchanged["revision"] == blocked_revision
            current = unchanged
            darts_args = {
                **detect_args,
                "trap_id": "darts-1",
                "action": "trigger",
                "source_ref": darts_source_ref,
                "source_excerpt": darts_excerpt,
                "profile": darts_profile,
                "target_ids": None,
                "area_confirmed": None,
                "spatial_facts": reviewed_area_facts(
                    scene_id=darts_chunk["scene"]["id"],
                    trap_id="darts-1",
                    source_ref=darts_source_ref,
                    campaign_revision=current["revision"],
                    in_area_ids={actor["id"], captive["id"]},
                ),
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
            assert darts_result["eligible_target_ids"] == sorted([actor["id"], captive["id"]])
            assert locksmith["id"] not in darts_result["eligible_target_ids"]
            assert darts_result["random_stream_receipt"]["draw_count"] >= 8
            assert await _call(server, "trap_state_transition", darts_args) == darts_result

            empty_darts_current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            empty_darts_args = {
                **darts_args,
                "trap_id": "darts-empty-area",
                "trigger_fact": {
                    **darts_args["trigger_fact"],
                    "plate_id": "darts-empty-area",
                },
                "spatial_facts": reviewed_area_facts(
                    scene_id=darts_chunk["scene"]["id"],
                    trap_id="darts-empty-area",
                    source_ref=darts_source_ref,
                    campaign_revision=empty_darts_current["revision"],
                    in_area_ids=set(),
                ),
                "expected_revision": empty_darts_current["revision"],
                "idempotency_key": "darts-empty-area-trigger",
            }
            empty_darts = await _call(server, "trap_state_transition", empty_darts_args)
            assert empty_darts["trap"]["status"] == "spent"
            assert empty_darts["eligible_target_ids"] == []
            assert empty_darts["affected_actor_ids"] == []
            assert empty_darts["darts"] == [
                {
                    "dart": index,
                    "target_id": None,
                    "attack": {"hit": False, "reason": "no eligible creature in area"},
                }
                for index in range(1, 5)
            ]
            assert "random_stream_receipt" not in empty_darts
            assert await _call(server, "trap_state_transition", empty_darts_args) == empty_darts

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
            net_scene_progress = await _call(
                server,
                "module_set_progress",
                {
                    "campaign_id": campaign_id,
                    "scene_id": net_chunk["scene"]["id"],
                    "status": "current",
                    "expected_state_version": 0,
                    "idempotency_key": "make-net-scene-current",
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
            net_args = {
                **detect_args,
                "trap_id": "net-1",
                "action": "trigger",
                "source_ref": json.dumps(
                    net_chunk["source_ref"], sort_keys=True, separators=(",", ":")
                ),
                "source_excerpt": net_excerpt,
                "profile": net_profile,
                "target_ids": None,
                "area_confirmed": None,
                "spatial_facts": reviewed_area_facts(
                    scene_id=net_chunk["scene"]["id"],
                    trap_id="net-1",
                    source_ref=json.dumps(
                        net_chunk["source_ref"], sort_keys=True, separators=(",", ":")
                    ),
                    campaign_revision=current["revision"],
                    in_area_ids={actor["id"], captive["id"]},
                ),
                "expected_revision": current["revision"],
                "idempotency_key": "net-trigger",
            }
            net_result = await _call(server, "trap_state_transition", net_args)
            assert net_result["trap"]["status"] == "triggered"
            assert net_result["check"]["ability"] == "strength"
            assert net_result["check"]["success"] is True
            assert actor["id"] in net_result["trap"]["restrained_actor_ids"]
            assert net_result["targets"][1]["target_id"] == captive["id"]
            for target_result in net_result["targets"]:
                assert target_result["restrained"] is True
                assert target_result["prone"] is (not target_result["check"]["success"])
            assert locksmith["id"] not in net_result["affected_actor_ids"]
            assert "random_stream_receipt" in net_result
            current = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign_id},
                },
            )
            rescue_args = {
                **net_args,
                "action": "rescue",
                "actor_id": actor["id"],
                "spatial_facts": None,
                "rescue_target_id": captive["id"],
                "rescue_facts": {
                    "decision_id": "net-rescue-review",
                    "reason": "The rescuer can reach the captive.",
                    "scene_id": net_chunk["scene"]["id"],
                    "scene_revision": net_scene_progress["state_version"],
                    "trap_id": "net-1",
                    "source_ref": net_args["source_ref"],
                    "campaign_revision": current["revision"],
                    "reviewed_by": "system:local",
                    "rescuer_id": actor["id"],
                    "target_id": captive["id"],
                    "within_reach": True,
                },
                "expected_revision": current["revision"],
                "idempotency_key": "net-rescue",
            }
            with pytest.raises(ToolError, match="within reach"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **rescue_args,
                        "rescue_facts": {**rescue_args["rescue_facts"], "within_reach": False},
                        "idempotency_key": "net-rescue-not-reach",
                    },
                )
            with pytest.raises(ToolError, match="rescuer_id"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **rescue_args,
                        "rescue_facts": {
                            **rescue_args["rescue_facts"],
                            "rescuer_id": locksmith["id"],
                        },
                        "idempotency_key": "net-rescue-wrong-actor",
                    },
                )
            unchanged = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign_id},
                },
            )
            assert unchanged["revision"] == current["revision"]
            with pytest.raises(ToolError, match="revision conflict"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **rescue_args,
                        "expected_revision": current["revision"] - 1,
                        "idempotency_key": "net-rescue-stale-cas",
                    },
                )
            rescued = await _call(server, "trap_state_transition", rescue_args)
            assert rescued["action"] == "rescue"
            assert rescued["check"]["success"] is True
            assert rescued["target_id"] == captive["id"]
            assert rescued["action_cost"] == "action" and rescued["action_paid"] is True
            assert captive["id"] not in rescued["trap"]["restrained_actor_ids"]
            assert actor["id"] in rescued["trap"]["restrained_actor_ids"]
            assert await _call(server, "trap_state_transition", rescue_args) == rescued

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
                "spatial_facts": None,
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
                "area_confirmed": None,
                "target_ids": None,
                "spatial_facts": reviewed_area_facts(
                    scene_id=net_chunk["scene"]["id"],
                    trap_id="net-disabled",
                    source_ref=json.dumps(
                        net_chunk["source_ref"], sort_keys=True, separators=(",", ":")
                    ),
                    campaign_revision=current["revision"],
                    in_area_ids={actor["id"], captive["id"]},
                ),
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
                "area_confirmed": None,
                "target_ids": None,
                "spatial_facts": reviewed_area_facts(
                    scene_id=net_chunk["scene"]["id"],
                    trap_id="net-triggered-by-failure",
                    source_ref=json.dumps(
                        net_chunk["source_ref"], sort_keys=True, separators=(",", ":")
                    ),
                    campaign_revision=current["revision"],
                    in_area_ids={actor["id"], captive["id"]},
                ),
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
            needle_chunk = await _call(server, "module_expand", {"chunk_id": needle_hits[0]["id"]})
            needle_source_ref = json.dumps(
                needle_chunk["source_ref"], sort_keys=True, separators=(",", ":")
            )
            needle_scene_progress = await _call(
                server,
                "module_set_progress",
                {
                    "campaign_id": campaign_id,
                    "scene_id": needle_chunk["scene"]["id"],
                    "status": "current",
                    "expected_state_version": 0,
                    "idempotency_key": "make-needle-scene-current",
                },
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
                "spatial_facts": {
                    "decision_id": "needle-disable-range-review",
                    "reason": (
                        "The DM reviewed the locksmith at the lock within the needle's reach."
                    ),
                    "scene_id": needle_chunk["scene"]["id"],
                    "scene_revision": needle_scene_progress["state_version"],
                    "trap_id": "needle-safe",
                    "target_actor_id": locksmith["id"],
                    "source_ref": needle_source_ref,
                    "campaign_revision": current["revision"],
                    "reviewed_by": "system:local",
                    "distance_inches": 3,
                },
                "expected_revision": current["revision"],
                "idempotency_key": "needle-disable-success",
            }
            needle_success = await _call(server, "trap_state_transition", needle_success_args)
            assert needle_success["disable_check"]["success"] is True
            assert needle_success["disable_check"]["tool_proficient"] is True
            assert needle_success["trap"]["status"] == "disabled"
            assert needle_success["trap"]["source_ref"] == needle_source_ref
            assert "piercing" not in needle_success and "poison" not in needle_success
            assert (
                await _call(server, "trap_state_transition", needle_success_args) == needle_success
            )

            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            needle_failure_args = {
                **needle_success_args,
                "trap_id": "needle-triggered-by-failure",
                "actor_id": captive["id"],
                "spatial_facts": {
                    **needle_success_args["spatial_facts"],
                    "decision_id": "needle-disable-failure-range-review",
                    "trap_id": "needle-triggered-by-failure",
                    "target_actor_id": captive["id"],
                    "campaign_revision": current["revision"],
                },
                "expected_revision": current["revision"],
                "idempotency_key": "needle-disable-failure",
            }
            needle_failure = await _call(server, "trap_state_transition", needle_failure_args)
            assert needle_failure["disable_check"]["success"] is False
            assert needle_failure["trap"]["status"] == "spent"
            assert needle_failure["trap"]["source_ref"] == needle_source_ref
            assert needle_failure["target_id"] == captive["id"]
            assert needle_failure["piercing"]["hp_damage"] == 1
            assert needle_failure["poison"]["damage_expression"] == "2d10"
            if needle_failure["poison"]["success"] is False:
                assert needle_failure["condition_effect"]["source"].startswith(
                    "trap-poison-needle:"
                )
                assert (
                    needle_failure["condition_effect"]["metadata"]["trap_state"]["source_ref"]
                    == needle_source_ref
                )
            else:
                assert needle_failure["condition_effect"] is None
            assert "random_stream_receipt" in needle_failure
            assert (
                await _call(server, "trap_state_transition", needle_failure_args) == needle_failure
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
            with pytest.raises(ToolError, match="reviewed spatial_facts"):
                await _call(server, "trap_state_transition", fire_rejected_args)
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert unchanged["revision"] == current["revision"]
            caller_selected_targets_args = {
                **fire_rejected_args,
                "target_ids": [actor["id"]],
                "area_confirmed": None,
                "spatial_facts": None,
                "idempotency_key": "statue-caller-targets-only",
            }
            with pytest.raises(ToolError, match="derive all affected actors"):
                await _call(server, "trap_state_transition", caller_selected_targets_args)
            empty_area_args = {
                **fire_rejected_args,
                "target_ids": None,
                "area_confirmed": None,
                "spatial_facts": reviewed_area_facts(
                    scene_id=fire_chunk["scene"]["id"],
                    trap_id="statue-1",
                    source_ref=fire_source_ref,
                    campaign_revision=unchanged["revision"],
                    in_area_ids=set(),
                ),
                "trigger_fact": {
                    "kind": "pressure_plate_weight",
                    "scene_id": fire_chunk["scene"]["id"],
                    "plate_id": "statue-1",
                    "weight_lb": 21,
                },
                "idempotency_key": "statue-empty-area",
            }
            empty_area_args["trap_id"] = "statue-empty-area"
            empty_area_args["spatial_facts"]["trap_id"] = "statue-empty-area"
            empty_area_args["trigger_fact"]["plate_id"] = "statue-empty-area"
            empty_area_settled = await _call(server, "trap_state_transition", empty_area_args)
            assert empty_area_settled["trap"]["status"] == "spent"
            assert empty_area_settled["affected_actor_ids"] == []
            assert empty_area_settled["targets"] == []
            assert empty_area_settled["damage_roll"] is None
            assert (
                await _call(server, "trap_state_transition", empty_area_args) == empty_area_settled
            )
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            current = unchanged
            empty_area_args["expected_revision"] = unchanged["revision"]
            empty_area_args["spatial_facts"]["campaign_revision"] = unchanged["revision"]
            incomplete_area_args = {
                **empty_area_args,
                "spatial_facts": {
                    **empty_area_args["spatial_facts"],
                    "actor_facts": empty_area_args["spatial_facts"]["actor_facts"][:-1],
                },
                "idempotency_key": "statue-incomplete-area-review",
            }
            with pytest.raises(ToolError, match="cover every active encounter combatant"):
                await _call(server, "trap_state_transition", incomplete_area_args)
            stale_area_revision_args = {
                **empty_area_args,
                "spatial_facts": {
                    **empty_area_args["spatial_facts"],
                    "campaign_revision": unchanged["revision"] - 1,
                    "actor_facts": [
                        {"actor_id": actor["id"], "in_area": True},
                        {"actor_id": captive["id"], "in_area": False},
                        {"actor_id": locksmith["id"], "in_area": False},
                    ],
                },
                "expected_revision": unchanged["revision"],
                "idempotency_key": "statue-stale-spatial-review",
            }
            with pytest.raises(ToolError, match="stale for the current campaign revision"):
                await _call(server, "trap_state_transition", stale_area_revision_args)
            wrong_reviewer_args = {
                **empty_area_args,
                "spatial_facts": {
                    **empty_area_args["spatial_facts"],
                    "reviewed_by": "untrusted:caller",
                    "actor_facts": [
                        {"actor_id": actor["id"], "in_area": True},
                        {"actor_id": captive["id"], "in_area": False},
                        {"actor_id": locksmith["id"], "in_area": False},
                    ],
                },
                "idempotency_key": "statue-untrusted-reviewer",
            }
            with pytest.raises(ToolError, match="authorized DM principal"):
                await _call(server, "trap_state_transition", wrong_reviewer_args)
            stale_encounter_args = {
                **empty_area_args,
                "spatial_facts": {
                    **empty_area_args["spatial_facts"],
                    "encounter_id": "encounter-from-another-scene",
                    "actor_facts": [
                        {"actor_id": actor["id"], "in_area": True},
                        {"actor_id": captive["id"], "in_area": False},
                        {"actor_id": locksmith["id"], "in_area": False},
                    ],
                },
                "idempotency_key": "statue-stale-encounter",
            }
            with pytest.raises(ToolError, match="active encounter"):
                await _call(server, "trap_state_transition", stale_encounter_args)
            stale_revision_args = {
                **fire_rejected_args,
                "target_ids": None,
                "area_confirmed": None,
                "spatial_facts": reviewed_area_facts(
                    scene_id=fire_chunk["scene"]["id"],
                    trap_id="statue-1",
                    source_ref=fire_source_ref,
                    campaign_revision=unchanged["revision"],
                    in_area_ids={actor["id"]},
                ),
                "trigger_fact": {
                    "kind": "pressure_plate_weight",
                    "scene_id": fire_chunk["scene"]["id"],
                    "plate_id": "statue-1",
                    "weight_lb": 21,
                },
                "expected_revision": unchanged["revision"] - 1,
                "idempotency_key": "statue-cas-conflict",
            }
            with pytest.raises(ToolError, match="campaign revision conflict"):
                await _call(server, "trap_state_transition", stale_revision_args)
            after_area_rejections = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert after_area_rejections["revision"] == unchanged["revision"]
            fire_args = {
                **fire_rejected_args,
                "target_ids": None,
                "area_confirmed": None,
                "spatial_facts": reviewed_area_facts(
                    scene_id=fire_chunk["scene"]["id"],
                    trap_id="statue-1",
                    source_ref=fire_source_ref,
                    campaign_revision=unchanged["revision"],
                    in_area_ids={actor["id"], captive["id"]},
                ),
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
            assert fire_result["affected_actor_ids"] == [actor["id"], captive["id"]]
            assert locksmith["id"] not in fire_result["affected_actor_ids"]
            assert isinstance(fire_result["targets"][0]["save"]["success"], bool)
            assert fire_result["random_stream_receipt"]["draw_count"] >= 2
            assert await _call(server, "trap_state_transition", fire_args) == fire_result

            before_wedge = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            wedge_args = {
                **fire_args,
                "trap_id": "statue-wedged-1",
                "action": "bypass",
                "method": "wedge_pressure_plate",
                "target_ids": None,
                "area_confirmed": None,
                "spatial_facts": None,
                "trigger_fact": None,
                "trap_depth_ft": None,
                "expected_revision": before_wedge["revision"],
                "idempotency_key": "statue-wedge-pressure-plate",
            }
            wedged = await _call(server, "trap_state_transition", wedge_args)
            assert wedged["trap"]["bypassed"] is True
            assert wedged["trap"]["status"] == "armed"
            assert wedged["trap"]["bypass_method"] == "wedge_pressure_plate"
            assert "random_stream_receipt" not in wedged
            assert await _call(server, "trap_state_transition", wedge_args) == wedged

            before_blocked_trigger = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            blocked_trigger_args = {
                **fire_args,
                "trap_id": "statue-wedged-1",
                "method": None,
                "expected_revision": before_blocked_trigger["revision"],
                "spatial_facts": reviewed_area_facts(
                    scene_id=fire_chunk["scene"]["id"],
                    trap_id="statue-wedged-1",
                    source_ref=fire_source_ref,
                    campaign_revision=before_blocked_trigger["revision"],
                    in_area_ids={actor["id"], captive["id"]},
                ),
                "trigger_fact": {
                    "kind": "pressure_plate_weight",
                    "scene_id": fire_chunk["scene"]["id"],
                    "plate_id": "statue-wedged-1",
                    "weight_lb": 21,
                },
                "idempotency_key": "statue-wedged-trigger-rejected",
            }
            with pytest.raises(ToolError, match="pressure-plate wedge prevents"):
                await _call(server, "trap_state_transition", blocked_trigger_args)
            after_blocked_trigger = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert after_blocked_trigger["revision"] == before_blocked_trigger["revision"]
            assert (
                after_blocked_trigger["state"]["random_stream"]
                == before_blocked_trigger["state"]["random_stream"]
            )
            wedged_state = after_blocked_trigger["state"]["trap_state"]["traps"]["statue-wedged-1"]
            assert wedged_state["bypassed"] is True
            assert wedged_state["status"] == "armed"

            rolling_hits = await _call(
                server,
                "module_search",
                {"campaign_id": campaign_id, "query": "Rolling Sphere", "top_k": 3},
            )
            rolling_chunk = await _call(
                server, "module_expand", {"chunk_id": rolling_hits[0]["id"]}
            )
            rolling_source_ref = json.dumps(
                rolling_chunk["source_ref"], sort_keys=True, separators=(",", ":")
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            rolling_detect_args = {
                **detect_args,
                "trap_id": "rolling-sphere-investigation",
                "action": "detect",
                "method": "investigation",
                "source_ref": rolling_source_ref,
                "source_excerpt": rolling_sphere_excerpt,
                "profile": rolling_sphere_profile,
                "actor_id": locksmith["id"],
                "expected_revision": current["revision"],
                "idempotency_key": "rolling-sphere-investigation-detect",
            }
            with pytest.raises(ToolError, match="active ability or source-defined no-roll"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **rolling_detect_args,
                        "method": "arcana",
                        "idempotency_key": "rolling-sphere-invalid-detection-method",
                    },
                )
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert unchanged["revision"] == current["revision"]
            rolling_detect = await _call(server, "trap_state_transition", rolling_detect_args)
            assert rolling_detect["check"]["ability"] == "investigation"
            assert rolling_detect["check"]["dc"] == 15
            assert len(rolling_detect["checks"]) == 1
            assert rolling_detect["trap"]["detected"] is True
            assert (
                await _call(server, "trap_state_transition", rolling_detect_args) == rolling_detect
            )
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            rolling_args = {
                **detect_args,
                "trap_id": "rolling-sphere-plate",
                "action": "trigger",
                "source_ref": rolling_source_ref,
                "source_excerpt": rolling_sphere_excerpt,
                "profile": rolling_sphere_profile,
                "actor_id": actor["id"],
                "target_ids": None,
                "area_confirmed": None,
                "trap_depth_ft": None,
                "trigger_fact": {
                    "kind": "pressure_plate_weight",
                    "scene_id": rolling_chunk["scene"]["id"],
                    "plate_id": "rolling-sphere-plate",
                    "weight_lb": 20,
                },
                "expected_revision": current["revision"],
                "idempotency_key": "rolling-sphere-source-trigger",
            }
            with pytest.raises(ToolError, match="20 lb or greater"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **rolling_args,
                        "trigger_fact": {**rolling_args["trigger_fact"], "weight_lb": 19},
                        "idempotency_key": "rolling-sphere-below-threshold",
                    },
                )
            with pytest.raises(ToolError, match="source-defined scene"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **rolling_args,
                        "trigger_fact": {
                            **rolling_args["trigger_fact"],
                            "scene_id": "other-scene",
                        },
                        "idempotency_key": "rolling-sphere-wrong-scene",
                    },
                )
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert unchanged["revision"] == current["revision"]
            with pytest.raises(ToolError, match="revision conflict"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **rolling_args,
                        "expected_revision": current["revision"] - 1,
                        "idempotency_key": "rolling-sphere-stale-revision",
                    },
                )
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert unchanged["revision"] == current["revision"]
            with pytest.raises(ToolError, match="does not accept caller-confirmed area facts"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **rolling_args,
                        "area_confirmed": True,
                        "idempotency_key": "rolling-sphere-caller-area",
                    },
                )
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert unchanged["revision"] == current["revision"]
            with pytest.raises(ToolError, match="unresolved authoritative path"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **rolling_args,
                        "target_ids": [actor["id"]],
                        "idempotency_key": "rolling-sphere-caller-target",
                    },
                )
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert unchanged["revision"] == current["revision"]
            rolling_args["expected_revision"] = unchanged["revision"]
            with pytest.raises(ToolError, match="active Grid encounter"):
                await _call(server, "trap_state_transition", rolling_args)
            with pytest.raises(ToolError, match="active Grid encounter"):
                await _call(server, "trap_state_transition", rolling_args)
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert unchanged["revision"] == current["revision"]

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
                "target_ids": None,
                "area_confirmed": None,
                "spatial_facts": reviewed_area_facts(
                    scene_id=pit_chunk["scene"]["id"],
                    trap_id="pit-1",
                    source_ref=pit_source_ref,
                    campaign_revision=current["revision"],
                    in_area_ids={actor["id"]},
                ),
                "trap_depth_ft": 15,
                "trigger_fact": {
                    "kind": "step_on_cover",
                    "scene_id": pit_chunk["scene"]["id"],
                    "cover_id": "pit-1",
                },
                "expected_revision": current["revision"],
                "idempotency_key": "pit-invalid-depth",
            }
            pit_wedge_args = {
                **pit_rejected_args,
                "trap_id": "pit-wedged-cover",
                "action": "bypass",
                "method": "wedge_cover",
                "spatial_facts": None,
                "trap_depth_ft": None,
                "trigger_fact": None,
                "idempotency_key": "pit-wedge-cover",
            }
            wedged_pit = await _call(server, "trap_state_transition", pit_wedge_args)
            assert wedged_pit["trap"]["bypassed"] is True
            assert wedged_pit["trap"]["bypass_method"] == "wedge_cover"
            blocked_pit_trigger_args = {
                **pit_wedge_args,
                "action": "trigger",
                "method": None,
                "trap_depth_ft": 20,
                "trigger_fact": {
                    "kind": "step_on_cover",
                    "scene_id": pit_chunk["scene"]["id"],
                    "cover_id": "pit-wedged-cover",
                },
                "expected_revision": wedged_pit["campaign_revision"],
                "idempotency_key": "pit-wedged-cover-trigger",
            }
            with pytest.raises(ToolError, match="wedging the pit cover"):
                await _call(server, "trap_state_transition", blocked_pit_trigger_args)
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert unchanged["revision"] == wedged_pit["campaign_revision"]
            assert (
                unchanged["state"]["trap_state"]["traps"]["pit-wedged-cover"]["status"] == "armed"
            )
            current = unchanged
            pit_rejected_args["spatial_facts"] = reviewed_area_facts(
                scene_id=pit_chunk["scene"]["id"],
                trap_id="pit-1",
                source_ref=pit_source_ref,
                campaign_revision=current["revision"],
                in_area_ids={actor["id"]},
            )
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
                "target_ids": None,
                "area_confirmed": None,
                "spatial_facts": reviewed_area_facts(
                    scene_id=locking_chunk["scene"]["id"],
                    trap_id="locking-pit-invalid-depth",
                    source_ref=locking_source_ref,
                    campaign_revision=current["revision"],
                    in_area_ids={captive["id"]},
                ),
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
                "target_ids": None,
                "area_confirmed": None,
                "spatial_facts": reviewed_area_facts(
                    scene_id=locking_chunk["scene"]["id"],
                    trap_id="locking-pit-10ft",
                    source_ref=locking_source_ref,
                    campaign_revision=unchanged["revision"],
                    in_area_ids={actor["id"], captive["id"], locksmith["id"]},
                ),
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
                locksmith["id"],
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
                "spatial_facts": None,
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
                strong_escape = await _call(server, "trap_state_transition", strong_escape_args)
                assert (
                    await _call(server, "trap_state_transition", strong_escape_args)
                    == strong_escape
                )
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
            assert locksmith["id"] in strong_escape["trap"]["contained_actor_ids"]
            assert "position" not in strong_escape["trap"]

            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            locking_disable_facts = {
                "inside_pit": True,
                "mechanism_reachable": True,
                "can_see": True,
                "scene_id": locking_chunk["scene"]["id"],
                "trap_id": "locking-pit-10ft",
                "actor_id": locksmith["id"],
                "decision_id": "locking-pit-disable-ruling",
                "reason": "The locksmith is inside the pit and can see and reach its spring.",
            }
            locking_disable_args = {
                **locking_args,
                "action": "disable",
                "actor_id": locksmith["id"],
                "method": "thieves_tools",
                "target_ids": None,
                "area_confirmed": None,
                "trap_depth_ft": None,
                "trigger_fact": None,
                "scene_facts": locking_disable_facts,
                "spatial_facts": None,
                "expected_revision": current["revision"],
                "idempotency_key": "locking-pit-disable-success",
            }
            with pytest.raises(ToolError, match="exact reviewed scene facts"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **locking_disable_args,
                        "scene_facts": None,
                        "idempotency_key": "locking-pit-disable-missing-facts",
                    },
                )
            with pytest.raises(ToolError, match="source-defined scene"):
                await _call(
                    server,
                    "trap_state_transition",
                    {
                        **locking_disable_args,
                        "scene_facts": {**locking_disable_facts, "scene_id": "other-scene"},
                        "idempotency_key": "locking-pit-disable-wrong-scene",
                    },
                )
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert unchanged["revision"] == current["revision"]
            locking_disable_args["expected_revision"] = unchanged["revision"]
            locking_disable_success = await _call(
                server, "trap_state_transition", locking_disable_args
            )
            assert locking_disable_success["disable_check"]["ability"] == "dexterity"
            assert locking_disable_success["disable_check"]["dc"] == 15
            assert locking_disable_success["disable_check"]["tool"] == "thieves_tools"
            assert locking_disable_success["disable_check"]["tool_proficient"] is True
            assert locking_disable_success["disable_check"]["success"] is True
            assert locking_disable_success["trap"]["spring_disabled"] is True
            assert locking_disable_success["trap"]["status"] == "triggered"
            assert set(locking_disable_success["trap"]["contained_actor_ids"]) == {
                captive["id"],
                locksmith["id"],
            }
            assert locking_disable_success["scene_facts"] == locking_disable_facts
            assert "random_stream_receipt" in locking_disable_success
            assert (
                await _call(server, "trap_state_transition", locking_disable_args)
                == locking_disable_success
            )

            spiked_hits = await _call(
                server,
                "module_search",
                {"campaign_id": campaign_id, "query": "Spiked Locking Pit", "top_k": 5},
            )
            spiked_chunk = await _call(server, "module_expand", {"chunk_id": spiked_hits[0]["id"]})
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
                "target_ids": None,
                "area_confirmed": None,
                "spatial_facts": reviewed_area_facts(
                    scene_id=spiked_chunk["scene"]["id"],
                    trap_id="spiked-locking-pit-20ft",
                    source_ref=spiked_source_ref,
                    campaign_revision=current["revision"],
                    in_area_ids={captive["id"]},
                ),
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
                "spatial_facts": None,
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

            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            spiked_disable_args = {
                **spiked_escape_args,
                "action": "disable",
                "method": "thieves_tools",
                "trigger_fact": None,
                "scene_facts": {
                    "inside_pit": True,
                    "mechanism_reachable": True,
                    "can_see": True,
                    "scene_id": spiked_chunk["scene"]["id"],
                    "trap_id": "spiked-locking-pit-20ft",
                    "actor_id": captive["id"],
                    "decision_id": "spiked-locking-pit-disable-ruling",
                    "reason": "The captive can reach and see the spring mechanism.",
                },
                "expected_revision": current["revision"],
                "idempotency_key": "spiked-locking-pit-disable-failure",
            }
            spiked_disable_failure = await _call(
                server, "trap_state_transition", spiked_disable_args
            )
            assert spiked_disable_failure["disable_check"]["ability"] == "dexterity"
            assert spiked_disable_failure["disable_check"]["dc"] == 15
            assert spiked_disable_failure["disable_check"]["tool_proficient"] is False
            assert spiked_disable_failure["disable_check"]["success"] is False
            assert spiked_disable_failure["trap"]["spring_disabled"] is False
            assert spiked_disable_failure["trap"]["status"] == "triggered"
            assert captive["id"] in spiked_disable_failure["trap"]["contained_actor_ids"]
            assert "random_stream_receipt" in spiked_disable_failure
            assert (
                await _call(server, "trap_state_transition", spiked_disable_args)
                == spiked_disable_failure
            )
        finally:
            close_server(server)

        restarted = create_server(config)
        try:
            assert await _call(restarted, "trap_state_transition", detect_args) == detected
            assert (
                await _call(restarted, "trap_state_transition", roof_no_roll_args) == roof_no_roll
            )
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
            assert await _call(restarted, "trap_state_transition", rescue_args) == rescued
            assert (
                await _call(restarted, "trap_state_transition", locking_disable_args)
                == locking_disable_success
            )
            assert await _call(restarted, "trap_state_transition", weak_escape_args) == weak_escape
            for replay_args, replay_result in strong_escape_attempts:
                assert await _call(restarted, "trap_state_transition", replay_args) == replay_result
            assert await _call(restarted, "trap_state_transition", spiked_args) == spiked_result
            assert (
                await _call(restarted, "trap_state_transition", spiked_escape_args) == spiked_escape
            )
            assert (
                await _call(restarted, "trap_state_transition", spiked_disable_args)
                == spiked_disable_failure
            )
        finally:
            close_server(restarted)

    asyncio.run(exercise())
