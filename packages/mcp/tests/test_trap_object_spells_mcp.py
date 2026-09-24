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


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


def _spell_card(spell_id: str, name: str, level: int) -> dict:
    if name == "Detect Magic":
        spell_range = {"kind": "self", "normal_ft": 0, "long_ft": 0}
        duration = {"kind": "timed", "value": 10, "unit": "minute", "concentration": True}
    else:
        spell_range = {"kind": "distance", "normal_ft": 120, "long_ft": 120}
        duration = {"kind": "instantaneous", "concentration": False}
    return {
        "id": spell_id,
        "name": name,
        "level": level,
        "grant": {"source_type": "class", "source_key": "fixture", "method": "known"},
        "access": {"known": True, "prepared": True},
        "definition": {
            "casting_time": "1 action",
            "range": spell_range,
            "duration": duration,
            "components": {"verbal": True, "somatic": True, "material": False},
        },
    }


def test_source_trap_object_spells_commit_detect_and_dispel_across_spell_routes(
    tmp_path: Path,
) -> None:
    fire_profile = {"profile_id": "srd5.1.fire_breathing_statue"}
    fire_marker = json.dumps(fire_profile, sort_keys=True, separators=(",", ":"))
    fire_excerpt = (
        "A statue breathes fire in a thirty-foot cone.\n"
        f"trap_profile: {fire_marker}"
    )
    sphere_profile = {"profile_id": "srd5.1.sphere_of_annihilation"}
    sphere_marker = json.dumps(sphere_profile, sort_keys=True, separators=(",", ":"))
    sphere_excerpt = (
        "A sphere of annihilation rests in a stone mouth with an optional face enchantment.\n"
        f"trap_profile: {sphere_marker}"
    )
    source = tmp_path / "trap-spells.md"
    source.write_text(
        "# Source-bound trap spell fixtures\n\n"
        "## Trap Chamber\n\n"
        f"{fire_excerpt}\n\n{sphere_excerpt}\n",
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
                    "name": "Trap object spell settlement",
                    "edition": "2014",
                    "random_seed": "trap-object-spell-fixture",
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
                        "source_key": "trap-spell-source",
                        "title": "Trap spell source",
                    },
                },
            )

            async def helper_call(target, name, arguments):
                return await _call(target, name, arguments)

            await finalize_and_activate_module(
                helper_call,
                server,
                campaign_id,
                staged,
                source_key="trap-spell-source",
                title="Trap spell source",
                portable_id="dnd5e.module.trap-spell-source",
                edition="2014",
            )
            hits = await _call(
                server,
                "module_search",
                {"campaign_id": campaign_id, "query": "Trap Chamber Fire statue", "top_k": 5},
            )
            expanded = await _call(server, "module_expand", {"chunk_id": hits[0]["id"]})
            source_ref = json.dumps(expanded["source_ref"], sort_keys=True, separators=(",", ":"))
            scene_id = str(expanded["scene"]["id"])
            scene_progress = await _call(
                server,
                "module_set_progress",
                {
                    "campaign_id": campaign_id,
                    "scene_id": scene_id,
                    "status": "current",
                    "expected_state_version": 0,
                    "idempotency_key": "make-trap-scene-current",
                },
            )
            current_scene = await _call(
                server,
                "module_query",
                {"campaign_id": campaign_id, "view": "current"},
            )
            assert current_scene["scene_id"] == scene_id

            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["abilities"]["intelligence"]["score"] = 30
            sheet["spellcasting"].update(
                ability="intelligence",
                preparation={"mode": "known", "selected_spell_ids": []},
                spell_slots={
                    "1": {
                        "label": "1st",
                        "value": 1,
                        "max": 1,
                        "recovers_on": "long_rest",
                        "source_key": "fixture",
                    },
                    "3": {
                        "label": "3rd",
                        "value": 3,
                        "max": 3,
                        "recovers_on": "long_rest",
                        "source_key": "fixture",
                    },
                },
            )
            detect_card = _spell_card(
                "dnd5e.content.srd2014.spell.detect-magic", "Detect Magic", 1
            )
            dispel_card = _spell_card(
                "dnd5e.content.srd2014.spell.dispel-magic", "Dispel Magic", 3
            )
            sheet["content"]["spells"] = [detect_card, dispel_card]
            caster = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign_id, "name": "Aster", "sheet": sheet},
                    "idempotency_key": "caster",
                },
            )
            opponent_sheet = default_character_sheet()
            opponent_sheet["edition"] = "2014"
            opponent = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign_id,
                        "name": "Guard",
                        "sheet": opponent_sheet,
                    },
                    "idempotency_key": "opponent",
                },
            )
            refreshed = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            await _call(
                server,
                "game_phase",
                {
                    "campaign_id": campaign_id,
                    "action": "set",
                    "tool_profile": "play",
                    "expected_revision": refreshed["revision"],
                    "idempotency_key": "play",
                },
            )

            async def campaign_state():
                return await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign_id}},
                )

            def trap_facts(
                *, profile_id: str, excerpt: str, trap_id: str, component: str,
                revision: int, decision_id: str, distance_ft: int = 30,
                face_enchantment_present: bool | None = None,
            ) -> dict:
                values = {
                    "decision_id": decision_id,
                    "reason": (
                        "DM reviewed the source object, current scene, visibility, and distance."
                    ),
                    "source_ref": source_ref,
                    "source_excerpt": excerpt,
                    "scene_id": scene_id,
                    "scene_revision": scene_progress["state_version"],
                    "trap_id": trap_id,
                    "profile_id": profile_id,
                    "component": component,
                    "actor_id": caster["id"],
                    "campaign_revision": revision,
                    "distance_ft": distance_ft,
                    "visible": True,
                    "targetable": True,
                }
                if face_enchantment_present is not None:
                    values["face_enchantment_present"] = face_enchantment_present
                return {"trap_object_facts": values}

            async def ooc_cast(arguments: dict) -> dict:
                payload = {
                    key: arguments[key]
                    for key in ("spell_id", "cast_level", "declaration")
                    if key in arguments
                }
                call = {
                    "character_id": arguments["character_id"],
                    "action": "cast_spell",
                    "payload": payload,
                    "expected_revision": arguments["expected_revision"],
                    "idempotency_key": arguments["idempotency_key"],
                }
                if "principal_id" in arguments:
                    call["principal_id"] = arguments["principal_id"]
                return await _call(server, "character_action", call)

            before_detect = await campaign_state()
            base_detect_args = {
                "character_id": caster["id"],
                "spell_id": detect_card["id"],
                "cast_level": 1,
                "expected_revision": caster["revision"],
                "idempotency_key": "detect-fire-aura",
            }
            player_facts = trap_facts(
                profile_id=fire_profile["profile_id"], excerpt=fire_excerpt,
                trap_id="fire-statue-1", component="statue",
                revision=before_detect["revision"], decision_id="player-review",
            )
            with pytest.raises(ToolError):
                await ooc_cast(
                    {
                        **base_detect_args,
                        "declaration": player_facts,
                        "principal_id": "player:untrusted",
                    }
                )
            too_far = trap_facts(
                profile_id=fire_profile["profile_id"], excerpt=fire_excerpt,
                trap_id="fire-statue-1", component="statue",
                revision=before_detect["revision"], decision_id="too-far", distance_ft=31,
            )
            with pytest.raises(ToolError, match="outside the 30-foot"):
                await ooc_cast({**base_detect_args, "declaration": too_far})
            wrong_target = trap_facts(
                profile_id=sphere_profile["profile_id"], excerpt=sphere_excerpt,
                trap_id="sphere-1", component="face_enchantment",
                revision=before_detect["revision"], decision_id="wrong-detect-target",
                face_enchantment_present=True,
            )
            with pytest.raises(ToolError, match="Detect Magic currently reveals only"):
                await ooc_cast({**base_detect_args, "declaration": wrong_target})
            unchanged = await campaign_state()
            assert unchanged["revision"] == before_detect["revision"]
            assert unchanged["state"].get("trap_state") is None

            detect_args = {
                **base_detect_args,
                "declaration": trap_facts(
                    profile_id=fire_profile["profile_id"], excerpt=fire_excerpt,
                    trap_id="fire-statue-1", component="statue",
                    revision=before_detect["revision"], decision_id="fire-aura-review",
                ),
            }
            detected = await ooc_cast(detect_args)
            assert detected["status"] == "committed"
            assert detected["result"]["automatic_effect"] == "source_trap_object_spell"
            assert detected["result"]["trap_object_spell"]["aura"] == {
                "target": "statue",
                "school": "evocation",
            }
            assert (
                detected["result"]["trap_object_spell"]["source_review_receipt"]["reviewed_by"]
                == "system:local"
            )
            after_detect = await campaign_state()
            caster_after_detect = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": caster["id"]}},
            )
            assert caster_after_detect["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] == 0
            fire_state = after_detect["state"]["trap_state"]["traps"]["fire-statue-1"]
            assert fire_state.get("status", "armed") == "armed"
            assert fire_state["magic_aura_revealed"] == "evocation"
            assert (
                after_detect["state"]["trap_state"]["attempts"][-1]["reviewed_by"]
                == "system:local"
            )
            assert await ooc_cast(detect_args) == detected

            before_ooc_dispel = await campaign_state()
            dispel_ooc_args = {
                "character_id": caster["id"],
                "spell_id": dispel_card["id"],
                "cast_level": 3,
                "declaration": trap_facts(
                    profile_id=fire_profile["profile_id"], excerpt=fire_excerpt,
                    trap_id="fire-statue-1", component="statue",
                    revision=before_ooc_dispel["revision"], decision_id="fire-dispel-ooc",
                    distance_ft=120,
                ),
                "expected_revision": detected["character"]["revision"],
                "idempotency_key": "dispel-fire-ooc",
            }
            dispelled_ooc = await ooc_cast(dispel_ooc_args)
            assert dispelled_ooc["status"] == "committed"
            assert (
                dispelled_ooc["result"]["trap_object_spell"]["check"]["ability"]
                == "intelligence"
            )
            after_ooc_dispel = await campaign_state()
            assert after_ooc_dispel["state"]["trap_state"]["traps"]["fire-statue-1"].get(
                "status", "armed"
            ) == (
                "disabled"
                if dispelled_ooc["result"]["trap_object_spell"]["check"]["success"]
                else "armed"
            )
            caster_after_ooc_dispel = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": caster["id"]}},
            )
            assert (
                caster_after_ooc_dispel["sheet"]["spellcasting"]["spell_slots"]["3"]["value"]
                == 2
            )
            assert await ooc_cast(dispel_ooc_args) == dispelled_ooc

            latest = await campaign_state()
            started = await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign_id,
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 20, "height_cells": 20},
                    "participant_ids": [caster["id"], opponent["id"]],
                    "participant_config": [
                        {
                            "actor_id": caster["id"],
                            "initiative": 20,
                            "position": {"x": 1, "y": 1},
                            "disposition": "friendly",
                        },
                        {
                            "actor_id": opponent["id"],
                            "initiative": 10,
                            "position": {"x": 5, "y": 1},
                            "disposition": "hostile",
                        },
                    ],
                    "expected_revision": latest["revision"],
                    "idempotency_key": "start-trap-spell-combat",
                },
            )
            sphere_facts = trap_facts(
                profile_id=sphere_profile["profile_id"], excerpt=sphere_excerpt,
                trap_id="sphere-1", component="face_enchantment",
                revision=started["campaign_revision"], decision_id="sphere-face-dispel",
                distance_ft=120, face_enchantment_present=True,
            )
            cast_args = {
                "campaign_id": campaign_id,
                "actor_id": caster["id"],
                "spell_id": dispel_card["id"],
                "cast_level": 3,
                "declaration": sphere_facts,
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "dispel-sphere-face-combat",
            }
            _, dispelled_combat = await server.call_tool("combat_cast_spell", cast_args)
            assert dispelled_combat["status"] == "committed"
            spell_result = dispelled_combat["result"]
            assert spell_result["check"]["dc"] == 18
            assert spell_result["effect"]["effect"] in {"remove_enchantment_only", "no_effect"}
            assert spell_result["effect"]["sphere_removed"] is False
            assert spell_result["source_review_receipt"]["reviewed_by"] == "system:local"
            assert "random_stream_receipt" in dispelled_combat
            combatant = next(
                item for item in dispelled_combat["combat"]["combatants"]
                if item["actor_id"] == caster["id"]
            )
            assert combatant["turn_budget"]["main_action"] == 0
            _, replayed_combat = await server.call_tool("combat_cast_spell", cast_args)
            assert replayed_combat == dispelled_combat
            final_state = await campaign_state()
            sphere_state = final_state["state"]["trap_state"]["traps"]["sphere-1"]
            assert sphere_state["sphere_object_removed"] is False
            if spell_result["check"]["success"]:
                assert sphere_state["optional_sympathy_active"] is False
            final_caster = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": caster["id"]}},
            )
            assert final_caster["sheet"]["spellcasting"]["spell_slots"]["3"]["value"] == 1
            assert final_state["revision"] == dispelled_combat["campaign_revision"]
        finally:
            close_server(server)

    asyncio.run(exercise())
