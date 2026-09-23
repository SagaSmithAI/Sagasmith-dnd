from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from test_structured_spell_mcp import _campaign_with_combat, _raw

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server


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


def test_grid_2014_light_sources_are_source_receipted_and_survive_restart(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)

        async def call(name: str, arguments: dict):
            _, result = await server.call_tool(name, arguments)
            return result.get("result", result) if isinstance(result, dict) else result

        try:
            campaign = await call(
                "campaign_create",
                {"name": "Torchlight", "edition": "2014", "idempotency_key": "campaign"},
            )
            async def create_actor(name: str, key: str):
                return await call(
                    "character_create_from",
                    {
                        "mode": "direct",
                        "payload": {"campaign_id": campaign["id"], "name": name},
                        "principal_id": "system:local",
                        "idempotency_key": key,
                    },
                )

            lit_attacker = await create_actor("Lit attacker", "lit-attacker")
            lit_target = await create_actor("Lit target", "lit-target")
            dark_attacker = await create_actor("Dark attacker", "dark-attacker")
            dark_target = await create_actor("Dark target", "dark-target")
            darkvision_attacker = await create_actor("Darkvision attacker", "darkvision-attacker")
            darkvision_target = await create_actor("Darkvision target", "darkvision-target")
            darkvision_sheet = darkvision_attacker["sheet"]
            darkvision_sheet["traits"]["senses"]["darkvision"] = 60
            darkvision_attacker = await call(
                "character_sheet_replace",
                {
                    "character_id": darkvision_attacker["id"],
                    "sheet": darkvision_sheet,
                    "expected_revision": darkvision_attacker["revision"],
                    "idempotency_key": "darkvision-sheet",
                },
            )
            campaign_state = await call(
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign["id"]},
                    "principal_id": "system:local",
                },
            )
            started = await call(
                "combat_start",
                {
                    "positioning_mode": "grid",
                    "battle_map": {
                        "width_cells": 14,
                        "height_cells": 2,
                        "ambient_illumination": "dark",
                        "light_sources": [
                            {
                                "id": "torch-1",
                                "position": {"x": 0, "y": 0},
                                "bright_radius_ft": 20,
                                "dim_radius_ft": 40,
                                "source_ref": (
                                    "bundled:srd2014/04_Equipment/Adventuring_Gear.md#Torch"
                                ),
                                "source_excerpt": (
                                    "A torch sheds bright light in a 20-foot radius and dim "
                                    "light for an additional 20 feet."
                                ),
                            }
                        ],
                    },
                    "battle_map_override_reason": (
                        "The dark chamber has one lit torch next to the attacker."
                    ),
                    "campaign_id": campaign["id"],
                    "participant_ids": [
                        lit_attacker["id"],
                        lit_target["id"],
                        dark_attacker["id"],
                        dark_target["id"],
                        darkvision_attacker["id"],
                        darkvision_target["id"],
                    ],
                    "participant_config": [
                        {
                            "actor_id": lit_attacker["id"],
                            "initiative": 30,
                            "position": {"x": 4, "y": 0},
                        },
                        {
                            "actor_id": lit_target["id"],
                            "initiative": 29,
                            "position": {"x": 5, "y": 0},
                        },
                        {
                            "actor_id": dark_attacker["id"],
                            "initiative": 28,
                            "position": {"x": 10, "y": 0},
                        },
                        {
                            "actor_id": dark_target["id"],
                            "initiative": 27,
                            "position": {"x": 11, "y": 0},
                        },
                        {
                            "actor_id": darkvision_attacker["id"],
                            "initiative": 26,
                            "position": {"x": 12, "y": 0},
                        },
                        {
                            "actor_id": darkvision_target["id"],
                            "initiative": 25,
                            "position": {"x": 13, "y": 0},
                        },
                    ],
                    "expected_revision": campaign_state["revision"],
                    "idempotency_key": "start-grid-vision",
                },
            )
            assert started["combat"]["battle_map"]["ambient_illumination"] == "dark"
            assert started["combat"]["battle_map"]["light_sources"][0]["id"] == "torch-1"

            close_server(server)
            server = create_server(config)
            async def preflight(actor: dict, target: dict):
                return await call(
                    "combat_preflight_attack",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": actor["id"],
                        "target_id": target["id"],
                        "action": {"weapon_id": "unarmed-strike", "attack_mode": "melee"},
                    },
                )

            async def advance_to_turn(actor: dict, key: str) -> None:
                for step in range(7):
                    state = await call(
                        "campaign_query",
                        {
                            "view": "get",
                            "payload": {"campaign_id": campaign["id"]},
                            "principal_id": "system:local",
                        },
                    )
                    combat = await call(
                        "combat_query",
                        {
                            "campaign_id": campaign["id"],
                            "view": "status",
                            "payload": {"detail": "summary"},
                        },
                    )
                    current = combat["combatants"][combat["turn_index"]]
                    if current["actor_id"] == actor["id"]:
                        return
                    await call(
                        "combat_end_turn",
                        {
                            "campaign_id": campaign["id"],
                            "actor_id": current["actor_id"],
                            "expected_revision": state["revision"],
                            "idempotency_key": f"{key}-{step}",
                        },
                    )
                raise AssertionError(f"encounter did not reach {actor['name']}'s turn")

            await advance_to_turn(lit_attacker, "advance-lit")
            lit_plan = await preflight(lit_attacker, lit_target)
            assert lit_plan["vision"]["attacker"]["visible"] is True
            assert lit_plan["vision"]["attacker"]["light_level"] == "dim"
            assert lit_plan["vision"]["attacker"]["perception_disadvantage"] is True
            assert lit_plan["vision"]["attacker"]["source_lights"][0]["source_ref"].endswith(
                "Adventuring_Gear.md#Torch"
            )
            assert any(
                receipt["mechanic_id"] == "dnd5e.core.vision.light_obscuration_2014"
                for receipt in lit_plan["rule_receipts"]
            )
            await advance_to_turn(dark_attacker, "advance-dark")
            dark_plan = await preflight(dark_attacker, dark_target)
            assert dark_plan["vision"]["attacker"]["visible"] is False
            assert dark_plan["vision"]["attacker"]["light_level"] == "dark"
            await advance_to_turn(darkvision_attacker, "advance-darkvision")
            darkvision_plan = await preflight(darkvision_attacker, darkvision_target)
            assert darkvision_plan["vision"]["attacker"]["visible"] is True
            assert darkvision_plan["vision"]["attacker"]["darkvision_used"] is True
            assert darkvision_plan["vision"]["attacker"]["color_detail"] == "grayscale"
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_madness_apply_uses_engine_rolls_and_replays_after_restart(tmp_path: Path) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)

        async def call(name: str, arguments: dict):
            _, result = await server.call_tool(name, arguments)
            return result.get("result", result) if isinstance(result, dict) else result

        try:
            campaign = await call(
                "campaign_create",
                {"name": "Madness", "edition": "2014", "idempotency_key": "campaign"},
            )
            actor = await call(
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Witness"},
                    "principal_id": "system:local",
                    "idempotency_key": "witness",
                },
            )
            arguments = {
                "character_id": actor["id"],
                "action": "madness_apply",
                "payload": {
                    "category": "short_term",
                    "trigger_reason": "The DM recorded a source-defined horror event.",
                },
                "principal_id": "system:local",
                "expected_revision": actor["revision"],
                "idempotency_key": "madness-apply",
            }
            result = await call("character_state_change", arguments)
            assert 1 <= result["madness"]["roll"] <= 100
            assert result["rolls"]["table"]["total"] == result["madness"]["roll"]
            assert result["rolls"]["duration"]["total"] == result["madness"]["duration"]["amount"]
            assert any(
                item["mechanic_id"] == "dnd5e.core.madness.2014"
                for item in result["rule_receipts"]
            )
            effect_id = result["effect_id"]
            assert any(
                effect["id"] == effect_id
                and effect["source"] == "bundled:srd2014/08_Gamemastering/Madness.md"
                for effect in result["character"]["sheet"]["effects"]
            )
            assert await call("character_state_change", arguments) == result

            close_server(server)
            server = create_server(config)
            replay = await call("character_state_change", arguments)
            assert replay == result
            restored = await call(
                "character_query",
                {
                    "view": "get",
                    "payload": {"character_id": actor["id"]},
                    "principal_id": "system:local",
                },
            )
            assert any(effect["id"] == effect_id for effect in restored["sheet"]["effects"])
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_madness_suppression_expires_on_campaign_clock_and_cure_tiers_are_enforced(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)

        async def call(name: str, arguments: dict):
            _, result = await server.call_tool(name, arguments)
            return result.get("result", result) if isinstance(result, dict) else result

        try:
            campaign = await call(
                "campaign_create",
                {"name": "Madness suppression", "edition": "2014", "idempotency_key": "campaign"},
            )
            actor = await call(
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Witness"},
                    "principal_id": "system:local",
                    "idempotency_key": "witness",
                },
            )
            applied = await call(
                "character_state_change",
                {
                    "character_id": actor["id"],
                    "action": "madness_apply",
                    "payload": {
                        "category": "short_term",
                        "trigger_reason": "The DM recorded a source-defined horror event.",
                    },
                    "principal_id": "system:local",
                    "expected_revision": actor["revision"],
                    "idempotency_key": "madness-apply",
                },
            )
            effect_id = applied["effect_id"]
            suppressed = await call(
                "character_state_change",
                {
                    "character_id": actor["id"],
                    "action": "madness_suppress",
                    "payload": {
                        "effect_id": effect_id,
                        "spell_id": "dnd5e.content.srd2014.spell.calm-emotions",
                    },
                    "principal_id": "system:local",
                    "expected_revision": applied["character"]["revision"],
                    "idempotency_key": "madness-suppress",
                },
            )
            assert suppressed["madness_transition"]["status"] == "suppressed"
            suppressed_effect = next(
                item
                for item in suppressed["character"]["sheet"]["effects"]
                if item["id"] == effect_id
            )
            assert suppressed_effect["changes"] == []

            advanced = await call(
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "clock_advance",
                    "payload": {
                        "period": "minute",
                        "count": 1,
                        "expected_elapsed_ticks": 10,
                    },
                    "principal_id": "system:local",
                    "expected_revision": suppressed["campaign_revision"],
                    "idempotency_key": "advance-suppression",
                },
            )
            assert advanced["madness_suppression_resumed"] == {actor["id"]: [effect_id]}
            restored = await call(
                "character_query",
                {
                    "view": "get",
                    "payload": {"character_id": actor["id"]},
                    "principal_id": "system:local",
                },
            )
            active_effect = next(
                item for item in restored["sheet"]["effects"] if item["id"] == effect_id
            )
            assert active_effect["changes"]

            with pytest.raises(ToolError, match="source rules do not allow"):
                await call(
                    "character_state_change",
                    {
                        "character_id": actor["id"],
                        "action": "madness_cure",
                        "payload": {
                            "effect_id": effect_id,
                            "spell_id": "dnd5e.content.srd2014.spell.remove-curse",
                        },
                        "principal_id": "system:local",
                        "expected_revision": restored["revision"],
                        "idempotency_key": "invalid-madness-cure",
                    },
                )
            cured = await call(
                "character_state_change",
                {
                    "character_id": actor["id"],
                    "action": "madness_cure",
                    "payload": {
                        "effect_id": effect_id,
                        "spell_id": "dnd5e.content.srd2014.spell.lesser-restoration",
                    },
                    "principal_id": "system:local",
                    "expected_revision": restored["revision"],
                    "idempotency_key": "valid-madness-cure",
                },
            )
            cured_effect = next(
                item for item in cured["character"]["sheet"]["effects"] if item["id"] == effect_id
            )
            assert cured_effect["active"] is False
            assert cured["madness_transition"]["status"] == "cured"
        finally:
            close_server(server)

    asyncio.run(exercise())
