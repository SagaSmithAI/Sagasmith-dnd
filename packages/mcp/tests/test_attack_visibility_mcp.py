from __future__ import annotations

import asyncio
from pathlib import Path

from sagasmith_dnd.character_schema import default_character_sheet
from test_structured_spell_mcp import _campaign_with_combat, _raw

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


def test_hidden_attack_reveals_attacker_to_its_target(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))

        async def call(name: str, arguments: dict):
            _, result = await server.call_tool(name, arguments)
            return result.get("result", result) if isinstance(result, dict) else result

        async def raw(name: str, arguments: dict):
            _, result = await server.call_tool(name, arguments)
            return result

        campaign = await call(
            "campaign_create",
            {"name": "Reveal attack", "edition": "2014", "idempotency_key": "campaign"},
        )
        attacker = await call(
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Hidden attacker"},
                "principal_id": "system:local",
                "idempotency_key": "attacker",
            },
        )
        target = await call(
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Target"},
                "principal_id": "system:local",
                "idempotency_key": "target",
            },
        )
        for actor, key in ((attacker, "attacker-sheet"), (target, "target-sheet")):
            sheet = actor["sheet"]
            sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
            updated = await call(
                "character_sheet_replace",
                {
                    "character_id": actor["id"],
                    "sheet": sheet,
                    "expected_revision": actor["revision"],
                    "idempotency_key": key,
                },
            )
            if actor["id"] == attacker["id"]:
                attacker = updated
            else:
                target = updated
        campaign = await call(
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        started = await raw(
            "combat_start",
            {
                "positioning_mode": "grid",
                "battle_map": {"width_cells": 12, "height_cells": 12},
                "campaign_id": campaign["id"],
                "participant_ids": [attacker["id"], target["id"]],
                "participant_config": [
                    {
                        "actor_id": attacker["id"],
                        "initiative": 20,
                        "position": {"x": 0, "y": 0},
                        "hidden": True,
                        "visible_to_actor_ids": [attacker["id"]],
                    },
                    {
                        "actor_id": target["id"],
                        "initiative": 10,
                        "position": {"x": 1, "y": 0},
                    },
                ],
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            },
        )
        attacked = await raw(
            "combat_resolve_attack",
            {
                "campaign_id": campaign["id"],
                "actor_id": attacker["id"],
                "target_id": target["id"],
                "action": {"weapon_id": "unarmed-strike", "attack_mode": "melee"},
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "attack",
            },
        )
        assert attacked["result"]["reveals_attacker"] is True
        attacker_state = next(
            item for item in attacked["combat"]["combatants"] if item["actor_id"] == attacker["id"]
        )
        assert attacker_state["hidden"] is False
        assert attacker_state["visible_to_actor_ids"] is None

        ended = await raw(
            "combat_end_turn",
            {
                "campaign_id": campaign["id"],
                "actor_id": attacker["id"],
                "expected_revision": attacked["campaign_revision"],
                "idempotency_key": "end-attacker",
            },
        )
        counterattack = await call(
            "combat_preflight_attack",
            {
                "campaign_id": campaign["id"],
                "actor_id": target["id"],
                "target_id": attacker["id"],
                "action": {"weapon_id": "unarmed-strike", "attack_mode": "melee"},
            },
        )
        assert (
            ended["combat"]["combatants"][ended["combat"]["turn_index"]]["actor_id"] == target["id"]
        )
        assert counterattack["target_can_see_attacker"] is True
        assert counterattack["disadvantage"] is False

        closed = await raw(
            "combat_end",
            {
                "campaign_id": campaign["id"],
                "outcome": {
                    "status": "victory",
                    "summary": "The grid-mode visibility check is complete.",
                },
                "expected_revision": ended["campaign_revision"],
                "idempotency_key": "end-grid",
            },
        )
        agent_started = await raw(
            "combat_start",
            {
                "positioning_mode": "agent",
                "campaign_id": campaign["id"],
                "participant_ids": [attacker["id"], target["id"]],
                "participant_config": [
                    {"actor_id": attacker["id"], "initiative": 20},
                    {"actor_id": target["id"], "initiative": 10},
                ],
                "expected_revision": closed["campaign_revision"],
                "idempotency_key": "start-agent",
            },
        )
        pending = await call(
            "combat_preflight_attack",
            {
                "campaign_id": campaign["id"],
                "actor_id": attacker["id"],
                "target_id": target["id"],
                "action": {"weapon_id": "unarmed-strike", "attack_mode": "melee"},
            },
        )
        assert agent_started["combat"]["positioning_mode"] == "agent"
        assert pending["status"] == "pending_ruling"
        assert pending["missing"] == ["attack.spatial_facts"]

        spatial_facts = {
            "decision_id": "spatial:agent-preflight",
            "reason": "The target is beside the attacker; both are in the open chamber.",
            "targetable": True,
            "in_range": True,
            "cover_degree": "none",
            "attacker_vision": {
                "distance_ft": 5, "illumination": "dark", "obscuration": "none",
                "magical_darkness": False, "opaque_boundary": False,
                "scene_ref": "scene:crypt:attacker-sight",
                "scene_excerpt": "The target is in darkness beyond the torchlight.",
            },
            "target_vision": {
                "distance_ft": 5, "illumination": "bright", "obscuration": "none",
                "magical_darkness": False, "opaque_boundary": False,
                "scene_ref": "scene:crypt:target-sight",
                "scene_excerpt": "The attacker is in the target's torchlight.",
            },
        }
        agent_plan = await call(
            "combat_preflight_attack",
            {
                "campaign_id": campaign["id"],
                "actor_id": attacker["id"],
                "target_id": target["id"],
                "action": {
                    "weapon_id": "unarmed-strike",
                    "attack_mode": "melee",
                    "context": {"spatial_facts": spatial_facts},
                },
            },
        )
        assert agent_plan["status"] == "ready"
        assert agent_plan["spatial_ruling"]["decision_id"] == "spatial:agent-preflight"
        assert agent_plan["vision"]["attacker"]["visible"] is False
        assert agent_plan["disadvantage"] is True
        assert any(
            receipt["mechanic_id"] == "dnd5e.core.vision.light_obscuration_2014"
            for receipt in agent_plan["rule_receipts"]
        )

        moved = await call(
            "combat_movement",
            {
                "campaign_id": campaign["id"],
                "actor_id": attacker["id"],
                "action": "move",
                "payload": {
                    "distance": 5,
                    "spatial_facts": {
                        "decision_id": "spatial:agent-move",
                        "reason": "The attacker shifts through an open nearby space.",
                        "destination_legal": True,
                        "distance_ft": 5,
                        "opportunity_attack_actor_ids": [],
                        "space_segments": [{
                            "distance_ft": 5,
                            "occupant_ids": [],
                            "passage_width_ft": None,
                            "difficult_terrain": False,
                        }],
                    },
                },
                "expected_revision": agent_started["campaign_revision"],
                "idempotency_key": "agent-move",
            },
        )
        assert moved["combat"]["log"][-1]["decision"]["decision_id"] == (
            "spatial:agent-move"
        )

    asyncio.run(exercise())


def test_agent_2014_opportunity_attack_uses_boundary_vision_facts(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign, revision, actors = await _campaign_with_combat(
            server,
            [("Mover", default_character_sheet()), ("Threat", default_character_sheet())],
            positioning_mode="agent",
        )
        threat_id, mover_id = actors[1]["id"], actors[0]["id"]
        sight = {
            "distance_ft": 5,
            "illumination": "bright",
            "obscuration": "none",
            "magical_darkness": False,
            "opaque_boundary": False,
            "scene_ref": "scene:reach-crossing",
            "scene_excerpt": "The creatures can see one another across the reach boundary.",
        }
        moved = await _raw(server, "combat_movement", {
            "campaign_id": campaign,
            "actor_id": mover_id,
            "action": "move",
            "payload": {
                "distance": 10,
                "spatial_facts": {
                    "decision_id": "move:crossing",
                    "reason": "The mover crosses out of the adjacent threat's reach.",
                    "destination_legal": True,
                    "distance_ft": 10,
                    "opportunity_attack_actor_ids": [threat_id],
                    "space_segments": [
                        {"distance_ft": 5, "occupant_ids": [], "passage_width_ft": None,
                         "difficult_terrain": False},
                        {"distance_ft": 5, "occupant_ids": [], "passage_width_ft": None,
                         "difficult_terrain": False},
                    ],
                    "opportunity_attack_boundaries": [{
                        "actor_id": threat_id,
                        "distance_ft": 5,
                        "weapon_ids": ["unarmed-strike"],
                        "targetable": True,
                        "in_range": True,
                        "cover_degree": "none",
                        "attacker_vision": sight,
                        "target_vision": {**sight, "scene_ref": "scene:reciprocal-sight"},
                    }],
                },
            },
            "expected_revision": revision,
            "idempotency_key": "move-to-reach-boundary",
        })
        assert moved["status"] == "pending_reaction"
        window = next(
            item for item in moved["combat"]["pending"]
            if item.get("trigger") == "opportunity_attack"
        )
        result = await _raw(server, "combat_reaction_attack", {
            "campaign_id": campaign,
            "actor_id": threat_id,
            "target_id": mover_id,
            "choice_id": window["id"],
            "action": {"weapon_id": "unarmed-strike"},
            "expected_revision": moved["campaign_revision"],
            "idempotency_key": "resolve-agent-opportunity-attack",
        })
        assert result["status"] == "committed"
        assert any(
            receipt["mechanic_id"] == "dnd5e.core.vision.light_obscuration_2014"
            for receipt in result.get("rule_receipts", [])
        )

    asyncio.run(exercise())
