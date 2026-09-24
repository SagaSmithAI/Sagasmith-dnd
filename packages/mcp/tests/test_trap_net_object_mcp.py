"""Falling Net destruction releases restrained actors in the object attack CAS."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from test_official_expansions_mcp import _call

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import finalize_and_activate_module


def test_destroyed_source_bound_falling_net_releases_actors_atomically(tmp_path: Path) -> None:
    profile = {"profile_id": "srd5.1.falling_net"}
    marker = '{"profile_id":"srd5.1.falling_net"}'
    excerpt = (
        "When the trap is triggered, the net covers a 10-foot square. Creatures in "
        "the area are restrained; each makes a DC 10 Strength save and those that "
        "fail are also knocked prone. The net has AC 10 and 20 hit points; 5 "
        "slashing damage destroys a 5-foot square section.\n"
        f"trap_profile: {marker}"
    )
    source = tmp_path / "net.md"
    source.write_text(f"# Traps\n\n## Falling Net\n\n{excerpt}\n", encoding="utf-8")
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
                    "name": "Falling Net Object",
                    "edition": "2014",
                    "random_seed": "falling-net-object",
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
                        "source_key": "net-source",
                        "title": "Net source",
                    },
                },
            )

            async def helper_call(target, name, arguments):
                value = await _call(target, name, arguments)
                if isinstance(value, dict) and "action" in value and "result" in value:
                    return value["result"]
                return value

            activation = await finalize_and_activate_module(
                helper_call,
                server,
                campaign_id,
                staged,
                source_key="net-source",
                title="Net source",
                portable_id="dnd5e.module.net-source",
            )
            hits = await _call(
                server,
                "module_search",
                {"campaign_id": campaign_id, "query": "Falling Net AC 10 hit points", "top_k": 3},
            )
            expanded = await _call(server, "module_expand", {"chunk_id": hits[0]["id"]})
            source_ref = {
                "module_id": activation["activated"]["activation"]["module_id"],
                "scene_id": expanded["scene"]["id"],
                "chunk_id": expanded["chunk_id"],
                "page_start": expanded["page_start"],
                "page_end": expanded["page_end"],
                "heading_path": expanded["heading_path"],
                "content_sha256": hashlib.sha256(expanded["content"].encode("utf-8")).hexdigest(),
            }
            source_ref_json = json.dumps(source_ref, sort_keys=True, separators=(",", ":"))
            scene_progress = await _call(
                server,
                "module_set_progress",
                {
                    "campaign_id": campaign_id,
                    "scene_id": expanded["scene"]["id"],
                    "status": "current",
                    "expected_state_version": 0,
                    "idempotency_key": "make-net-scene-current",
                },
            )

            trapped_sheet = default_character_sheet()
            trapped_sheet["edition"] = "2014"
            trapped_sheet["abilities"]["strength"]["score"] = 3
            trapped_sheet["combat"]["hp"] = {"value": 100, "max": 100, "temp": 0}
            trapped = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign_id,
                        "name": "Trapped Scout",
                        "sheet": trapped_sheet,
                    },
                    "idempotency_key": "trapped-scout",
                },
            )
            strong_sheet = default_character_sheet()
            strong_sheet["edition"] = "2014"
            strong_sheet["abilities"]["strength"]["score"] = 30
            strong_sheet["conditions"] = ["restrained"]
            strong_sheet["combat"]["hp"] = {"value": 100, "max": 100, "temp": 0}
            strong = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign_id,
                        "name": "Strong Scout",
                        "sheet": strong_sheet,
                    },
                    "idempotency_key": "strong-scout",
                },
            )
            breaker_sheet = default_character_sheet()
            breaker_sheet["edition"] = "2014"
            breaker_sheet["abilities"]["strength"]["score"] = 30
            breaker_sheet["inventory"]["items"] = [
                {
                    "id": "longsword",
                    "name": "Longsword",
                    "kind": "weapon",
                    "equipped": True,
                    "equipped_slot": "main_hand",
                    "mechanics": {
                        "attack_type": "melee",
                        "attack_ability": "strength",
                        "damage_formula": "1d8",
                        "damage_type": "slashing",
                        "properties": [],
                        "proficient": True,
                    },
                }
            ]
            breaker_sheet["inventory"]["equipment_slots"]["main_hand"] = "longsword"
            breaker = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign_id,
                        "name": "Net Breaker",
                        "sheet": breaker_sheet,
                    },
                    "idempotency_key": "net-breaker",
                },
            )
            campaign_state = await _call(
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
                    "participant_ids": [trapped["id"], strong["id"]],
                    "participant_config": [
                        {
                            "actor_id": trapped["id"],
                            "initiative": 20,
                        },
                        {
                            "actor_id": strong["id"],
                            "initiative": 10,
                        },
                    ],
                    "expected_revision": campaign_state["revision"],
                    "idempotency_key": "net-encounter",
                },
            )
            campaign_state = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            active_encounter = campaign_state["state"]["combat"]
            trigger_args = {
                "campaign_id": campaign_id,
                "trap_id": "net-object-1",
                "action": "trigger",
                "source_ref": source_ref_json,
                "source_excerpt": excerpt,
                "profile": profile,
                "actor_id": trapped["id"],
                "spatial_facts": {
                    "decision_id": "falling-net-area-1",
                    "reason": "DM reviewed both creatures inside the 10-foot net area.",
                    "scene_id": expanded["scene"]["id"],
                    "trap_id": "net-object-1",
                    "encounter_id": active_encounter["id"],
                    "source_ref": source_ref_json,
                    "campaign_revision": campaign_state["revision"],
                    "reviewed_by": "system:local",
                    "actor_facts": [
                        {"actor_id": trapped["id"], "in_area": True},
                        {"actor_id": strong["id"], "in_area": True},
                    ],
                },
                "expected_revision": campaign_state["revision"],
                "idempotency_key": "trigger-net",
            }
            trigger = await _call(server, "trap_state_transition", trigger_args)
            assert await _call(server, "trap_state_transition", trigger_args) == trigger
            with pytest.raises(ToolError, match="campaign revision conflict"):
                await _call(
                    server,
                    "trap_state_transition",
                    {**trigger_args, "idempotency_key": "stale-net-trigger"},
                )
            after_stale = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert after_stale["revision"] == trigger["campaign_revision"]
            assert trigger["check"]["ability"] == "strength"
            assert len(trigger["checks"]) == 2
            assert trigger["trap"]["status"] == "triggered"
            assert trigger["trap"]["restrained_actor_ids"] == [trapped["id"], strong["id"]]
            assert trigger["trap"]["object_id"] == "net-object-1"
            assert trigger["trap"]["scene_id"] == expanded["scene"]["id"]
            assert trigger["trap"]["source_ref"] == source_ref_json
            target_results = {item["target_id"]: item for item in trigger["targets"]}
            assert target_results[trapped["id"]]["check"]["success"] is False
            assert target_results[trapped["id"]]["restrained"] is True
            assert target_results[trapped["id"]]["prone"] is True
            assert target_results[strong["id"]]["check"]["success"] is True
            assert target_results[strong["id"]]["restrained"] is True
            assert target_results[strong["id"]]["prone"] is False
            trapped_state = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": trapped["id"]}},
            )
            assert "restrained" in trapped_state["sheet"]["conditions"]
            strong_state = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": strong["id"]}},
            )
            assert "restrained" in strong_state["sheet"]["conditions"]
            await _call(
                server,
                "combat_end",
                {
                    "campaign_id": campaign_id,
                    "outcome": {
                        "status": "victory",
                        "summary": "The encounter ends after the net trap is resolved.",
                    },
                    "expected_revision": trigger["campaign_revision"],
                    "idempotency_key": "net-encounter-end",
                },
            )

            def section_spatial_facts(
                campaign_revision: int,
                restrained_actor_ids: list[str],
                target_section_id: str,
            ) -> dict[str, object]:
                section_for_actor = {
                    trapped["id"]: "northwest",
                    strong["id"]: "southeast",
                }
                return {
                    "decision_id": f"net-sections-{campaign_revision}",
                    "reason": "The DM reviewed each remaining trapped actor's net square.",
                    "scene_id": expanded["scene"]["id"],
                    "scene_revision": scene_progress["state_version"],
                    "trap_id": "net-object-1",
                    "object_id": "net-object-1",
                    "source_ref": source_ref_json,
                    "campaign_revision": campaign_revision,
                    "reviewed_by": "system:local",
                    "target_section_id": target_section_id,
                    "actor_sections": [
                        {"actor_id": actor_id, "section_id": section_for_actor[actor_id]}
                        for actor_id in restrained_actor_ids
                    ],
                }

            object_profile = {
                "id": "net-object-1",
                "name": "Falling Net",
                "scene_id": expanded["scene"]["id"],
                "material": "rope",
                "size": "large",
                "resilience": "fragile",
            }
            campaign_state = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            breaker_state = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": breaker["id"]}},
            )
            wrong_net_profile = {
                **object_profile,
                "armor_class": 1,
                "hit_points": 200,
                "damage_filter": {"allowed_damage_types": ["slashing"]},
            }
            with pytest.raises(ToolError, match="armor class is fixed"):
                await _call(
                    server,
                    "character_action",
                    {
                        "character_id": breaker["id"],
                        "action": "attack_source_object",
                        "payload": {
                            "object": wrong_net_profile,
                            "section_spatial_facts": section_spatial_facts(
                                campaign_state["revision"],
                                [trapped["id"], strong["id"]],
                                "northwest",
                            ),
                            "object_ruling": {
                                "reason": "The DM reviews only the net material and size.",
                                "source_excerpt": excerpt,
                            },
                            "weapon_id": "longsword",
                            "source_ref": source_ref,
                            "reason": "The breaker confirms the net is within melee reach.",
                            "expected_campaign_revision": campaign_state["revision"],
                        },
                        "expected_revision": breaker_state["revision"],
                        "idempotency_key": "attack-net-wrong-source-ac",
                    },
                )
            with pytest.raises(ToolError, match="section_spatial_facts"):
                await _call(
                    server,
                    "character_action",
                    {
                        "character_id": breaker["id"],
                        "action": "attack_source_object",
                        "payload": {
                            "object": object_profile,
                            "object_ruling": {
                                "reason": "The DM reviews the net material and size.",
                                "source_excerpt": excerpt,
                            },
                            "weapon_id": "longsword",
                            "source_ref": source_ref,
                            "reason": "The breaker confirms the net is in range.",
                            "expected_campaign_revision": campaign_state["revision"],
                        },
                        "expected_revision": breaker_state["revision"],
                        "idempotency_key": "attack-net-no-section-facts",
                    },
                )
            outside_facts = section_spatial_facts(
                campaign_state["revision"], [trapped["id"], strong["id"]], "northwest"
            )
            outside_facts["actor_sections"] = [
                {"actor_id": trapped["id"], "section_id": "northwest"},
                {"actor_id": "outside-creature", "section_id": "southeast"},
            ]
            with pytest.raises(ToolError, match="cover every currently restrained actor"):
                await _call(
                    server,
                    "character_action",
                    {
                        "character_id": breaker["id"],
                        "action": "attack_source_object",
                        "payload": {
                            "object": object_profile,
                            "object_ruling": {
                                "reason": "The DM reviews the net material and size.",
                                "source_excerpt": excerpt,
                            },
                            "weapon_id": "longsword",
                            "source_ref": source_ref,
                            "reason": "The breaker confirms the net is in range.",
                            "section_spatial_facts": outside_facts,
                            "expected_campaign_revision": campaign_state["revision"],
                        },
                        "expected_revision": breaker_state["revision"],
                        "idempotency_key": "attack-net-outside-actor",
                    },
                )
            unchanged_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            unchanged_breaker = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": breaker["id"]}},
            )
            assert unchanged_campaign["revision"] == campaign_state["revision"]
            assert (
                unchanged_campaign["state"]["random_stream"]
                == campaign_state["state"]["random_stream"]
            )
            assert unchanged_breaker["revision"] == breaker_state["revision"]
            final_attack_args = None
            final_attack = None
            partial_release_seen = False
            for attempt in range(1, 21):
                campaign_state = await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign_id}},
                )
                breaker_state = await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": breaker["id"]}},
                )
                current_net = campaign_state["state"]["trap_state"]["traps"][
                    "net-object-1"
                ]
                severed = set(current_net.get("severed_section_ids") or [])
                target_section_id = next(
                    section_id
                    for section_id in ("northwest", "northeast", "southwest", "southeast")
                    if section_id not in severed
                )
                final_attack_args = {
                    "character_id": breaker["id"],
                    "action": "attack_source_object",
                    "payload": {
                        "object": (
                            object_profile
                            if attempt == 1
                            else {
                                "id": object_profile["id"],
                                "scene_id": object_profile["scene_id"],
                            }
                        ),
                        "object_ruling": {
                            "reason": (
                                "DM confirms the netting profile for this source-defined trap."
                            ),
                            "source_excerpt": excerpt,
                        },
                        "weapon_id": "longsword",
                        "source_ref": source_ref,
                        "reason": "The breaker confirms the net is within melee reach.",
                        "section_spatial_facts": section_spatial_facts(
                            campaign_state["revision"],
                            list(current_net.get("restrained_actor_ids") or []),
                            target_section_id,
                        ),
                        "expected_campaign_revision": campaign_state["revision"],
                    },
                    "expected_revision": breaker_state["revision"],
                    "idempotency_key": f"attack-net-{attempt}",
                }
                final_attack = await _call(server, "character_action", final_attack_args)
                assert final_attack["status"] == "committed"
                if (
                    "northwest" not in severed
                    and "northwest" in final_attack["object"].get("severed_section_ids", [])
                ):
                    assert final_attack["object"]["destroyed"] is False
                    assert final_attack["released_actors"] == [trapped["id"]]
                    assert final_attack["trap"]["restrained_actor_ids"] == [strong["id"]]
                    assert final_attack["trap"]["severed_section_ids"] == ["northwest"]
                    trapped_state = await _call(
                        server,
                        "character_query",
                        {"view": "get", "payload": {"character_id": trapped["id"]}},
                    )
                    strong_state = await _call(
                        server,
                        "character_query",
                        {"view": "get", "payload": {"character_id": strong["id"]}},
                    )
                    assert "restrained" not in trapped_state["sheet"]["conditions"]
                    assert "restrained" in strong_state["sheet"]["conditions"]
                    assert (
                        await _call(server, "character_action", final_attack_args)
                        == final_attack
                    )
                    stale_campaign = await _call(
                        server,
                        "campaign_query",
                        {"view": "get", "payload": {"campaign_id": campaign_id}},
                    )
                    stale_breaker = await _call(
                        server,
                        "character_query",
                        {"view": "get", "payload": {"character_id": breaker["id"]}},
                    )
                    with pytest.raises(ToolError, match="stale for the current campaign revision"):
                        await _call(
                            server,
                            "character_action",
                            {
                                **final_attack_args,
                                "payload": {
                                    **final_attack_args["payload"],
                                    "expected_campaign_revision": stale_campaign["revision"],
                                },
                                "expected_revision": stale_breaker["revision"],
                                "idempotency_key": "attack-net-stale-section-facts",
                            },
                        )
                    partial_release_seen = True
                if final_attack["object"]["destroyed"]:
                    break
                assert final_attack["object"]["hit_points"] > 0
                if partial_release_seen:
                    still_trapped = await _call(
                        server,
                        "character_query",
                        {"view": "get", "payload": {"character_id": strong["id"]}},
                    )
                    assert "restrained" in still_trapped["sheet"]["conditions"]
                else:
                    assert "released_actors" not in final_attack
                    still_trapped = await _call(
                        server,
                        "character_query",
                        {"view": "get", "payload": {"character_id": trapped["id"]}},
                    )
                    assert "restrained" in still_trapped["sheet"]["conditions"]
            else:
                raise AssertionError("20 real object attacks did not destroy the Falling Net")

            assert partial_release_seen is True

            assert final_attack["object"]["armor_class"] == 10
            assert final_attack["object"]["hit_point_maximum"] == 20
            assert final_attack["object"]["hit_points"] == 0
            assert final_attack["object"]["destroyed"] is True
            assert final_attack["object"]["source_ref"] == source_ref
            assert final_attack["object"]["last_attack"]["attack"]["hit"] is True
            assert final_attack["object"]["last_attack"]["damage"]["applied_amount"] > 0
            assert final_attack["object"]["last_attack"]["damage"]["parts"][0][
                "damage_type"
            ] == "slashing"
            assert final_attack["trap"]["status"] == "spent"
            assert final_attack["trap"]["object_destroyed"] is True
            assert final_attack["trap"]["object_hit_points"] == 0
            assert final_attack["trap"]["released_actor_ids"] == [trapped["id"], strong["id"]]
            assert final_attack["released_actors"] == [strong["id"]]
            assert final_attack["campaign_revision"] > trigger["campaign_revision"]
            assert "random_stream_receipt" in final_attack
            replay = await _call(server, "character_action", final_attack_args)
            assert replay == final_attack
            with pytest.raises(ToolError, match="character revision conflict"):
                await _call(
                    server,
                    "character_action",
                    {**final_attack_args, "idempotency_key": "stale-net-attack"},
                )
            trapped_state = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": trapped["id"]}},
            )
            assert "restrained" not in trapped_state["sheet"]["conditions"]
            assert "prone" in trapped_state["sheet"]["conditions"]
            strong_state = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": strong["id"]}},
            )
            assert "restrained" in strong_state["sheet"]["conditions"]
            assert "prone" not in strong_state["sheet"]["conditions"]
        finally:
            close_server(server)

        restarted = create_server(config)
        try:
            assert await _call(restarted, "character_action", final_attack_args) == final_attack
            trapped_state = await _call(
                restarted,
                "character_query",
                {"view": "get", "payload": {"character_id": trapped["id"]}},
            )
            assert "restrained" not in trapped_state["sheet"]["conditions"]
            assert "prone" in trapped_state["sheet"]["conditions"]
            strong_state = await _call(
                restarted,
                "character_query",
                {"view": "get", "payload": {"character_id": strong["id"]}},
            )
            assert "restrained" in strong_state["sheet"]["conditions"]
            assert "prone" not in strong_state["sheet"]["conditions"]
        finally:
            close_server(restarted)

    asyncio.run(exercise())
