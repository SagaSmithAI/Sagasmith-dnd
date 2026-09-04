from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.combat_engine import roll_attack_action as engine_roll_attack_action
from sagasmith_dnd.standard_feature_ids import (
    TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
    TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
    TORTLE_NATURAL_ARMOR_LEGACY_PACK_VERSIONS,
)

import sagasmith_dnd_mcp.server as server_module
from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.test_official_expansions_mcp import (
    _TORTLE_ID,
    _locked_official_library,
    _selection_for,
)


async def _call(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    _, response = await server.call_tool(name, arguments)
    return response.get("result", response) if isinstance(response, dict) else response


async def _raw(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    _, response = await server.call_tool(name, arguments)
    return response


def test_finalized_tortle_archive_claws_are_intrinsic_and_unarmed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = _locked_official_library()
    repository_root = Path(__file__).resolve().parents[3]
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=repository_root / "skills",
        modulegen_skills_dir=tmp_path / "modulegen-skills",
        auto_seed_rules=True,
        official_content_library=library,
    )

    def deterministic_attack(*, plan: dict[str, Any]) -> Any:
        return engine_roll_attack_action(plan=plan, rng=random.Random(0))

    monkeypatch.setattr(server_module, "roll_attack_action", deterministic_attack)

    async def exercise() -> None:
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Official Tortle Claws", "edition": "2014", "idempotency_key": "c"},
            )
            profile = await _call(
                server,
                "campaign_rules",
                {"campaign_id": campaign["id"], "action": "get_profile"},
            )
            activated = await _call(
                server,
                "content_pack",
                {
                    "action": "activate",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "kind": "addon",
                        "addon_id": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
                        "version": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
                    },
                    "expected_revision": profile["campaign_revision"],
                    "idempotency_key": "activate",
                },
            )
            assert activated["activation"]["enabled"] is True

            def sheet(*, occupied: bool = False) -> dict[str, Any]:
                value = default_character_sheet()
                value["abilities"]["strength"]["score"] = 16
                value["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
                if occupied:
                    value["inventory"]["items"] = [
                        {
                            "id": "main",
                            "name": "Main hand",
                            "kind": "equipment",
                            "equipped": True,
                            "equipped_slot": "main_hand",
                        },
                        {
                            "id": "off",
                            "name": "Off hand",
                            "kind": "equipment",
                            "equipped": True,
                            "equipped_slot": "off_hand",
                        },
                    ]
                    value["inventory"]["equipment_slots"].update(
                        {"main_hand": "main", "off_hand": "off"}
                    )
                return value

            empty = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Empty", "sheet": sheet()},
                    "idempotency_key": "empty",
                },
            )
            occupied = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Occupied",
                        "sheet": sheet(occupied=True),
                    },
                    "idempotency_key": "occupied",
                },
            )
            target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Target",
                        "sheet": sheet(),
                    },
                    "idempotency_key": "target",
                },
            )
            applied: dict[str, Any] = {}
            applied_by_id: dict[str, dict[str, Any]] = {}
            for actor, key in ((empty, "empty-apply"), (occupied, "occupied-apply")):
                applied = await _call(
                    server,
                    "character_content_apply",
                    {
                        "character_id": actor["id"],
                        "artifact_id": _TORTLE_ID,
                        "selection": await _selection_for(server, campaign["id"], _TORTLE_ID),
                        "expected_revision": actor["revision"],
                        "idempotency_key": key,
                    },
                )
                claws = applied["sheet"]["traits"]["intrinsic_attacks"]
                assert len(claws) == 1
                assert claws[0]["name"] == "Claws"
                assert claws[0]["damage_formula"] == "1d4"
                assert claws[0]["damage_type"] == "slashing"
                assert applied["sheet"]["abilities"]["strength"]["score"] == 18
                assert claws[0]["source"] == {
                    "artifact_id": _TORTLE_ID,
                    "pack_id": _TORTLE_ID.removesuffix(".species.tortle"),
                    "pack_version": next(iter(TORTLE_NATURAL_ARMOR_LEGACY_PACK_VERSIONS)),
                    "rule_refs": claws[0]["source"]["rule_refs"],
                }
                assert claws[0]["source"]["rule_refs"]
                assert all(
                    ref.startswith("rule-source:") for ref in claws[0]["source"]["rule_refs"]
                )
                applied_by_id[actor["id"]] = applied
                assert (
                    applied["sheet"]["inventory"]["items"] == actor["sheet"]["inventory"]["items"]
                )

            empty_after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": empty["id"]}},
            )
            replay = await _call(
                server,
                "character_content_apply",
                {
                    "character_id": empty["id"],
                    "artifact_id": _TORTLE_ID,
                    "selection": await _selection_for(server, campaign["id"], _TORTLE_ID),
                    "expected_revision": empty_after["revision"],
                    "idempotency_key": "empty-apply",
                },
            )
            assert replay["revision"] == empty_after["revision"]
            assert replay["sheet"] == empty_after["sheet"]
            occupied_after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": occupied["id"]}},
            )
            receipts_before = await _call(
                server,
                "campaign_rules",
                {"campaign_id": campaign["id"], "action": "receipts", "payload": {}},
            )
            current_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            with pytest.raises(ToolError):
                await _call(
                    server,
                    "inventory_transfer",
                    {
                        "mode": "character_to_character",
                        "payload": {
                            "source_character_id": empty["id"],
                            "target_character_id": target["id"],
                            "item_id": empty_after["sheet"]["traits"]["intrinsic_attacks"][0]["id"],
                            "quantity": 1,
                            "expected_campaign_revision": current_campaign["revision"],
                            "expected_source_revision": empty_after["revision"],
                            "expected_target_revision": target["revision"],
                        },
                        "idempotency_key": "reject-transfer",
                    },
                )
            for actor in (empty_after, occupied_after):
                claws_id = actor["sheet"]["traits"]["intrinsic_attacks"][0]["id"]
                before = actor
                for action, payload in (
                    ("remove", {"item_id": claws_id}),
                    ("update", {"item_id": claws_id, "patch": {"name": "forged"}}),
                    ("equip", {"item_id": claws_id, "slot": "main_hand"}),
                    ("recharge", {"item_id": claws_id, "trigger": "dawn"}),
                    ("consume_ammunition", {"weapon_id": claws_id, "quantity": 1}),
                ):
                    with pytest.raises(ToolError):
                        await _call(
                            server,
                            "inventory_change",
                            {
                                "owner": "character",
                                "action": action,
                                "owner_id": actor["id"],
                                "payload": payload,
                                "expected_revision": before["revision"],
                                "idempotency_key": f"reject-{actor['id']}-{action}",
                            },
                        )
                after = await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actor["id"]}},
                )
                assert after["revision"] == before["revision"]
                assert after["sheet"] == before["sheet"]
            receipts_after = await _call(
                server,
                "campaign_rules",
                {"campaign_id": campaign["id"], "action": "receipts", "payload": {}},
            )
            assert receipts_after == receipts_before

            current_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            started = await _raw(
                server,
                "combat_start",
                {
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 4, "height_cells": 4},
                    "campaign_id": campaign["id"],
                    "participant_ids": [empty["id"], occupied["id"], target["id"]],
                    "participant_config": [
                        {"actor_id": empty["id"], "initiative": 30, "position": {"x": 0, "y": 0}},
                        {
                            "actor_id": occupied["id"],
                            "initiative": 20,
                            "position": {"x": 1, "y": 1},
                        },
                        {"actor_id": target["id"], "initiative": 10, "position": {"x": 1, "y": 0}},
                    ],
                    "expected_revision": current_campaign["revision"],
                    "idempotency_key": "combat",
                },
            )
            last_attack_request: dict[str, Any] | None = None
            last_attack_response: dict[str, Any] | None = None
            receipts_after_last_attack: Any = None
            for actor, key in ((empty, "empty-attack"), (occupied, "occupied-attack")):
                attack_request = {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "target_id": target["id"],
                    "action": {
                        "weapon_id": applied_by_id[actor["id"]]["sheet"]["traits"][
                            "intrinsic_attacks"
                        ][0]["id"]
                    },
                    "expected_revision": started["campaign_revision"],
                    "idempotency_key": key,
                }
                receipts_before_attack = await _call(
                    server,
                    "campaign_rules",
                    {"campaign_id": campaign["id"], "action": "receipts", "payload": {}},
                )
                attacked = await _raw(
                    server,
                    "combat_resolve_attack",
                    attack_request,
                )
                assert attacked["status"] == "committed"
                assert attacked["result"]["damage"]["expression"] == "1d4 + 4"
                assert attacked["result"]["damage"]["damage_type"] == "slashing"
                assert attacked["result"]["unarmed_strike"] is True
                assert any(
                    receipt["mechanic_id"] == "dnd5e.core.attack.unarmed_strike"
                    for receipt in attacked["result"]["rule_receipts"]
                )
                receipts_after_attack = await _call(
                    server,
                    "campaign_rules",
                    {"campaign_id": campaign["id"], "action": "receipts", "payload": {}},
                )
                assert receipts_after_attack != receipts_before_attack
                replay_attack = await _raw(server, "combat_resolve_attack", attack_request)
                assert replay_attack == attacked
                assert (
                    await _call(
                        server,
                        "campaign_rules",
                        {"campaign_id": campaign["id"], "action": "receipts", "payload": {}},
                    )
                    == receipts_after_attack
                )
                last_attack_request = attack_request
                last_attack_response = attacked
                receipts_after_last_attack = receipts_after_attack
                started = attacked
                if actor is empty:
                    started = await _raw(
                        server,
                        "combat_end_turn",
                        {
                            "campaign_id": campaign["id"],
                            "actor_id": empty["id"],
                            "expected_revision": attacked["campaign_revision"],
                            "idempotency_key": "empty-end",
                        },
                    )
            await _raw(
                server,
                "combat_end",
                {
                    "campaign_id": campaign["id"],
                    "outcome": {"status": "withdrawal", "summary": "Test complete."},
                    "expected_revision": started["campaign_revision"],
                    "idempotency_key": "combat-end",
                },
            )
            close_server(server)
            server = create_server(config)
            durable = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": empty["id"]}},
            )
            durable_occupied = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": occupied["id"]}},
            )
            assert durable["sheet"] == empty_after["sheet"]
            assert durable_occupied["sheet"] == occupied_after["sheet"]
            assert last_attack_request is not None
            assert last_attack_response is not None
            assert (
                await _raw(server, "combat_resolve_attack", last_attack_request)
                == last_attack_response
            )
            assert (
                await _call(
                    server,
                    "campaign_rules",
                    {"campaign_id": campaign["id"], "action": "receipts", "payload": {}},
                )
                == receipts_after_last_attack
            )
        finally:
            close_server(server)

    asyncio.run(exercise())
