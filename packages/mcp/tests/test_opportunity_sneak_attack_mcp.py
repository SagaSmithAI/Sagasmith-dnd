from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet

import sagasmith_dnd_mcp.server as server_module
from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server


async def _call(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    _, response = await server.call_tool(name, arguments)
    return response.get("result", response) if isinstance(response, dict) else response


async def _raw(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    _, response = await server.call_tool(name, arguments)
    return response["result"] if isinstance(response, dict) and "action" in response else response


def test_opportunity_sneak_attack_uses_trigger_snapshot_and_reaction_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "dnd",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
    )
    real_roll = server_module.roll_attack_action

    def deterministic_roll(*args: Any, **kwargs: Any) -> Any:
        kwargs["rng"] = random.Random(5)
        return real_roll(*args, **kwargs)

    monkeypatch.setattr(server_module, "roll_attack_action", deterministic_roll)

    async def exercise() -> None:
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Opportunity Sneak Attack", "edition": "2014", "idempotency_key": "c"},
            )

            rogue_sheet = default_character_sheet()
            rogue_sheet["abilities"]["dexterity"]["score"] = 16
            rogue_sheet["progression"] = {
                "level": 1,
                "classes": [{"name": "Rogue", "level": 1, "hit_die": 8}],
            }
            rogue_sheet["content"]["features"] = [
                {
                    "id": "dnd5e.content.srd2014.feature.rogue-sneak-attack",
                    "name": "Sneak Attack",
                    "source_key": "Rogue",
                }
            ]
            rogue_sheet["inventory"]["items"] = [
                {
                    "id": "dagger",
                    "name": "Dagger",
                    "kind": "weapon",
                    "equipped": True,
                    "equipped_slot": "main_hand",
                    "mechanics": {
                        "category": "simple",
                        "attack_type": "melee",
                        "attack_ability": "dexterity",
                        "damage_formula": "1d4",
                        "damage_type": "piercing",
                        "properties": ["finesse", "light", "thrown"],
                    },
                }
            ]
            rogue_sheet["inventory"]["equipment_slots"]["main_hand"] = "dagger"

            rogue = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Rogue",
                        "sheet": rogue_sheet,
                    },
                    "idempotency_key": "rogue",
                },
            )
            ally = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Ally"},
                    "idempotency_key": "ally",
                },
            )
            mover = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Target"},
                    "idempotency_key": "target",
                },
            )
            started = await _raw(
                server,
                "combat_start",
                {
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 8, "height_cells": 8},
                    "campaign_id": campaign["id"],
                    "participant_ids": [mover["id"], rogue["id"], ally["id"]],
                    "participant_config": [
                        {
                            "actor_id": mover["id"],
                            "initiative": 30,
                            "position": {"x": 0, "y": 0},
                            "disposition": "hostile",
                        },
                        {
                            "actor_id": rogue["id"],
                            "initiative": 20,
                            "position": {"x": 1, "y": 0},
                            "disposition": "friendly",
                            "reach_ft": 5,
                        },
                        {
                            "actor_id": ally["id"],
                            "initiative": 10,
                            "position": {"x": 1, "y": 1},
                            "disposition": "friendly",
                        },
                    ],
                    "expected_revision": campaign["revision"],
                    "idempotency_key": "start",
                },
            )
            dodged = await _call(
                server,
                "combat_common_action",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": mover["id"],
                    "action": "dodge",
                    "expected_revision": started["campaign_revision"],
                    "idempotency_key": "dodge",
                },
            )
            moved = await _call(
                server,
                "combat_movement",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": mover["id"],
                    "action": "move",
                    "payload": {"distance": 15, "destination": {"x": 3, "y": 0}},
                    "expected_revision": dodged["campaign_revision"],
                    "idempotency_key": "move",
                },
            )
            reactions = await _call(
                server,
                "combat_query",
                {"campaign_id": campaign["id"], "view": "reactions", "actor_id": rogue["id"]},
            )
            assert reactions and reactions[0]["target_id"] == mover["id"]
            request = {
                "campaign_id": campaign["id"],
                "actor_id": rogue["id"],
                "choice_id": reactions[0]["id"],
                "target_id": mover["id"],
                "action": {
                    "weapon_id": "dagger",
                    "use_sneak_attack": True,
                    "context": {"advantage": True},
                },
                "expected_revision": moved["campaign_revision"],
                "idempotency_key": "oa-sneak",
            }
            receipts_before = await _call(
                server,
                "campaign_rules",
                {"campaign_id": campaign["id"], "action": "receipts", "payload": {}},
            )
            resolved = await _raw(server, "combat_reaction_attack", request)
            assert resolved["result"]["sneak_attack"]["used"] is True
            assert resolved["result"]["sneak_attack"]["turn_token"]
            assert resolved["result"]["disadvantage_applied"] is False
            replay = await _raw(server, "combat_reaction_attack", request)
            assert replay == resolved
            status = await _call(
                server,
                "combat_query",
                {
                    "campaign_id": campaign["id"],
                    "view": "status",
                    "principal_id": "system:local",
                },
            )
            rogue_state = next(
                item for item in status["combatants"] if item["actor_id"] == rogue["id"]
            )
            assert rogue_state["turn_budget"]["reaction"] == 0
            assert rogue_state["turn_budget"]["main_action"] == 1
            receipts_after = await _call(
                server,
                "campaign_rules",
                {"campaign_id": campaign["id"], "action": "receipts", "payload": {}},
            )
            assert len(receipts_after) > len(receipts_before)
            with pytest.raises(ToolError, match="not this actor's opportunity-attack window"):
                await _raw(
                    server,
                    "combat_reaction_attack",
                    {
                        **request,
                        "choice_id": reactions[0]["id"],
                        "expected_revision": request["expected_revision"] + 1,
                        "idempotency_key": "oa-sneak-second",
                    },
                )
        finally:
            close_server(server)

    asyncio.run(exercise())
