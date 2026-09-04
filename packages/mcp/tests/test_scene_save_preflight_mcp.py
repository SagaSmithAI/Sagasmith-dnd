from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server
from tests.authoring_helpers import finalize_and_activate_module


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    value = result.get("result", result) if isinstance(result, dict) else result
    if isinstance(value, dict) and "action" in value and "result" in value:
        return value["result"]
    return value


async def _raw(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result


def test_scene_save_commitment_is_source_bound_before_improvise_payment(tmp_path: Path) -> None:
    source = tmp_path / "tower.md"
    source.write_text(
        "# Tower\n\n## Poison Breath\n\n"
        "The dragon catches the hero in its cone.\n\n"
        "Each creature must make a DC 18 Dexterity saving throw, taking 16d6 poison "
        "damage on a failed save, or half as much damage on a successful one.\n",
        encoding="utf-8",
    )
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "dnd",
        modulegen_skills_dir=tmp_path / "modulegen",
        module_import_roots=(tmp_path,),
        auto_seed_rules=False,
    )

    async def exercise() -> None:
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Save preflight", "edition": "2014", "idempotency_key": "campaign"},
        )
        staged = await _call(
            server,
            "module_draft",
            {
                "campaign_id": campaign["id"],
                "action": "start",
                "payload": {"source_path": str(source), "source_key": "tower", "title": "Tower"},
                "idempotency_key": "stage",
            },
        )
        await finalize_and_activate_module(
            _call,
            server,
            campaign["id"],
            staged,
            source_key="tower",
            title="Tower",
            portable_id="dnd5e.module.tower",
        )
        search = await _call(
            server,
            "module_search",
            {"campaign_id": campaign["id"], "query": "dragon catches hero cone", "top_k": 3},
        )
        expanded = await _call(server, "module_expand", {"chunk_id": search[0]["id"]})
        actors = []
        for name, key in (("Dragon", "dragon"), ("Hero", "hero")):
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": name,
                        "character_type": "monster" if name == "Dragon" else "pc",
                    },
                    "principal_id": "system:local",
                    "idempotency_key": key,
                },
            )
            sheet = default_character_sheet()
            sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
            actor = await _call(
                server,
                "character_sheet_replace",
                {
                    "character_id": actor["id"],
                    "sheet": sheet,
                    "expected_revision": actor["revision"],
                    "idempotency_key": f"{key}-sheet",
                    "principal_id": "system:local",
                },
            )
            actors.append(actor)
        current = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        phase = await _call(
            server,
            "game_phase",
            {
                "campaign_id": campaign["id"],
                "action": "set",
                "tool_profile": "play",
                "expected_revision": current["revision"],
                "idempotency_key": "phase",
            },
        )
        started = await _call(
            server,
            "combat_start",
            {
                "positioning_mode": "agent",
                "campaign_id": campaign["id"],
                "participant_ids": [actor["id"] for actor in actors],
                "participant_config": [
                    {"actor_id": actors[0]["id"], "initiative": 20, "disposition": "hostile"},
                    {"actor_id": actors[1]["id"], "initiative": 10, "disposition": "friendly"},
                ],
                "scene_id": expanded["scene"]["id"],
                "ruleset": "2014",
                "expected_revision": phase["campaign_revision"],
                "idempotency_key": "combat",
            },
        )
        commitment = {
            "application_id": "poison-1",
            "source_card_id": "scene-poison-breath",
            "source_card_kind": "scene_procedure",
            "target_ids": [actors[1]["id"]],
            "save_ability": "dexterity",
            "save_dc": 18,
            "save_advantage": False,
            "save_disadvantage": False,
            "damage_expression": "16d6",
            "damage_type": "poison",
            "half_on_success": True,
            "mechanic_source_excerpt": (
                "Each creature must make a DC 18 Dexterity saving throw, taking 16d6 poison "
                "damage on a failed save, or half as much damage on a successful one."
            ),
            "agent_ruling": {
                "application_id": "poison-1",
                "default_resolver": "agent",
                "ruling_kind": "agent_dm_adjudication",
                "decision": "The hero is in the reviewed cone.",
                "reason": "The active scene places the hero in the cone.",
                "source_ref": deepcopy(expanded["source_ref"]),
                "source_excerpt": "The dragon catches the hero in its cone.",
            },
        }
        before = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        actor_snapshots = [
            await _call(
                server,
                "character_query",
                {
                    "view": "get",
                    "payload": {"character_id": actor["id"]},
                    "principal_id": "system:local",
                },
            )
            for actor in actors
        ]
        for index, (field, value) in enumerate(
            (
                (
                    "mechanic_source_excerpt",
                    "Fabricated DC 19 psychic effect; no such clause exists.",
                ),
                ("save_dc", 19),
                ("damage_expression", "6d6"),
                ("damage_type", "psychic"),
                ("half_on_success", False),
            )
        ):
            forged = {**commitment, field: value}
            with pytest.raises(ToolError, match="canonical|source|contract|mechanic|reviewed"):
                await _raw(
                    server,
                    "combat_common_action",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": actors[0]["id"],
                        "action": "improvise",
                        "payload": {
                            "procedure_id": "scene-poison-breath",
                            "agent_ruling_commitment": forged,
                        },
                        "expected_revision": started["campaign_revision"],
                        "idempotency_key": f"preflight-invalid-{index}",
                    },
                )
        after = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        assert after == before
        assert [
            await _call(
                server,
                "character_query",
                {
                    "view": "get",
                    "payload": {"character_id": actor["id"]},
                    "principal_id": "system:local",
                },
            )
            for actor in actors
        ] == actor_snapshots

        paid = await _raw(
            server,
            "combat_common_action",
            {
                "campaign_id": campaign["id"],
                "actor_id": actors[0]["id"],
                "action": "improvise",
                "payload": {
                    "procedure_id": "scene-poison-breath",
                    "agent_ruling_commitment": commitment,
                },
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "preflight-valid",
            },
        )
        assert paid["status"] == "committed"
        settled_payload = {
            **commitment,
            "source_actor_id": actors[0]["id"],
            "spatial_facts": {
                "decision_id": "spatial:poison-breath",
                "reason": "The hero is inside the reviewed cone with clear line of effect.",
                "affected_target_ids": [actors[1]["id"]],
                "excluded_actor_ids": [actors[0]["id"]],
                "line_of_effect_clear": True,
                "friendly_fire_included": False,
            },
        }
        settled = await _call(
            server,
            "combat_hp_change",
            {
                "campaign_id": campaign["id"],
                "target_id": actors[1]["id"],
                "action": "save_damage",
                "payload": settled_payload,
                "expected_revision": paid["campaign_revision"],
                "idempotency_key": "preflight-settle",
            },
        )
        assert settled["status"] == "committed"
        replay = await _call(
            server,
            "combat_hp_change",
            {
                "campaign_id": campaign["id"],
                "target_id": actors[1]["id"],
                "action": "save_damage",
                "payload": settled_payload,
                "expected_revision": paid["campaign_revision"],
                "idempotency_key": "preflight-settle",
            },
        )
        assert replay == settled

    asyncio.run(exercise())
