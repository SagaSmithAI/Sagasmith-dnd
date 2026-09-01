from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_core.content_pack import dumps_content_archive
from sagasmith_dnd.character_schema import default_character_notes, default_character_sheet
from sagasmith_dnd.content_actors import build_dnd_content_actor
from sagasmith_dnd.content_packages import build_preset_content_package
from sagasmith_dnd.content_validation import build_selection_contract

import sagasmith_dnd_mcp.server as server_module
from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server
from tests.authoring_helpers import import_and_activate_addon_fixture


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


async def _raw(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result


def _tortle_artifact() -> dict:
    artifact = {
        "id": "dnd5e.addon.tortle-package.species.tortle",
        "kind": "species",
        "application_state": "selection_ready",
        "mechanical_scope": "mechanical",
        "execution_state": "engine_ready",
        "semantic_resolution": {
            "status": "resolved",
            "mode": "static_grant",
            "first_use_compilation_required": False,
            "clause_ids": ["tortle-claws"],
        },
        "rule_clauses": [
            {
                "schema_version": 1,
                "id": "tortle-claws",
                "title": "Claws",
                "scope": "mechanical",
                "source_citations": [
                    {
                        "source": "book:tortle-package",
                        "source_ref": {"page": 4},
                        "source_excerpt": "Claws are natural weapons used for unarmed strikes.",
                    }
                ],
                "settlement": {
                    "mode": "static_grant",
                    "grant_refs": ["card.grants.natural_weapons"],
                },
            }
        ],
        "card": {
            "name": "Tortle",
            "grants": {
                "size": "medium",
                "walk_speed": 30,
                "languages": ["Common", "Aquan"],
                "natural_weapons": [
                    {
                        "name": "Claws",
                        "attack_ability": "strength",
                        "damage_formula": "1d4",
                        "damage_type": "slashing",
                        "reach_ft": 5,
                        "description": "A reviewed natural weapon.",
                    }
                ],
            },
        },
        "rule_refs": ["tortle-package:p4"],
    }
    artifact["selection_contract"] = build_selection_contract(
        artifact,
        status="ready",
        references=["tortle-package:p4"],
    )
    return artifact


def _config(tmp_path: Path) -> McpConfig:
    workspace = Path(__file__).resolve().parents[3]
    return McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=workspace / "skills",
        modulegen_skills_dir=workspace / "skills" / "dnd-module-generator",
    )


def _forged_intrinsic_sheet() -> dict:
    sheet = default_character_sheet()
    sheet["traits"]["intrinsic_attacks"] = [
        {
            "id": "caller-forged-claws",
            "name": "Claws",
            "attack_ability": "strength",
            "damage_formula": "1d4",
            "damage_type": "slashing",
            "reach_ft": 5,
            "source": {
                "artifact_id": "caller.forged.species.tortle",
                "pack_id": "caller.forged",
                "pack_version": "1.0.0",
                "rule_refs": ["caller-forged:p4"],
            },
        }
    ]
    return sheet


def test_content_actor_cannot_import_forged_intrinsic_attack(tmp_path: Path) -> None:
    forged = _forged_intrinsic_sheet()
    card = build_dnd_content_actor(
        actor_id="example.forged-tortle",
        version="1.0.0",
        actor_type="pc",
        name="Forged Tortle",
        sheet=forged,
        notes=default_character_notes(),
    )
    package, blobs = build_preset_content_package(
        package_id="example.forged-tortle.preset",
        version="1.0.0",
        system_id="dnd5e",
        title="Forged Tortle preset",
        cards=[card],
        metadata={
            "edition": "2014",
            "distribution": "private",
            "license": "user-supplied",
            "attribution": "Security regression fixture",
        },
    )

    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        artifact = "forged-tortle-preset.sagasmith-pack"
        config.content_packages_dir.mkdir(parents=True, exist_ok=True)
        (config.content_packages_dir / artifact).write_bytes(
            dumps_content_archive(package, blobs)
        )
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Forged preset guard", "edition": "2014", "idempotency_key": "c"},
        )
        with pytest.raises(ToolError, match="only by character_content_apply"):
            await _call(
                server,
                "character_create_from",
                {
                    "mode": "content_actor",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "artifact": artifact,
                        "artifact_id": card["id"],
                    },
                    "idempotency_key": "forged-content-actor",
                },
            )
        assert await _call(
            server,
            "character_query",
            {"view": "list", "payload": {"campaign_id": campaign["id"]}},
        ) == []

    asyncio.run(exercise())


def test_tortle_claws_survive_real_play_and_reject_forged_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "Tortle claws",
                "random_seed": "tortle-claws",
                "idempotency_key": "campaign",
            },
        )
        profile = await _call(
            server,
            "campaign_rules",
            {
                "campaign_id": campaign["id"],
                "action": "set_profile",
                "payload": {"edition": "2014"},
                "principal_id": "system:local",
                "expected_revision": campaign["revision"],
                "idempotency_key": "profile",
            },
        )
        artifact = _tortle_artifact()
        await import_and_activate_addon_fixture(
            _call,
            server,
            campaign["id"],
            config.home,
            manifest={
                "id": "dnd5e.addon.tortle-package",
                "version": "1.0.0",
                "title": "Tortle Package",
                "namespace": "dnd5e.addon.tortle-package",
                "system_id": "dnd5e",
                "editions": ["2014"],
                "capabilities": [],
            },
            artifacts=[artifact],
            mechanics=[],
            expected_revision=profile["campaign_revision"],
            request_key="tortle",
        )

        sheet = default_character_sheet()
        sheet["abilities"]["strength"]["score"] = 16
        sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
        sheet["inventory"]["items"] = [
            {
                "id": "occupied-main-hand",
                "name": "Main-hand item",
                "kind": "equipment",
                "equipped": True,
                "equipped_slot": "main_hand",
            },
            {
                "id": "occupied-off-hand",
                "name": "Off-hand item",
                "kind": "equipment",
                "equipped": True,
                "equipped_slot": "off_hand",
            },
        ]
        sheet["inventory"]["equipment_slots"]["main_hand"] = "occupied-main-hand"
        sheet["inventory"]["equipment_slots"]["off_hand"] = "occupied-off-hand"
        tortle = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Tortle",
                    "sheet": sheet,
                },
                "principal_id": "system:local",
                "idempotency_key": "tortle-character",
            },
        )
        target_sheet = default_character_sheet()
        target_sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
        target_sheet["combat"]["ac"] = {"base": 0, "override": 0}
        target = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Target",
                    "sheet": target_sheet,
                },
                "principal_id": "system:local",
                "idempotency_key": "target-character",
            },
        )

        apply_arguments = {
            "character_id": tortle["id"],
            "artifact_id": artifact["id"],
            "selection": {},
            "expected_revision": tortle["revision"],
            "idempotency_key": "apply-tortle",
        }
        applied = await _call(server, "character_content_apply", apply_arguments)
        replay = await _call(server, "character_content_apply", apply_arguments)
        assert replay == applied
        assert applied["revision"] == tortle["revision"] + 1

        assert all(item["name"] != "Claws" for item in applied["sheet"]["inventory"]["items"])
        claws = applied["sheet"]["traits"]["intrinsic_attacks"][0]
        assert claws == {
            "id": claws["id"],
            "name": "Claws",
            "attack_ability": "strength",
            "damage_formula": "1d4",
            "damage_type": "slashing",
            "reach_ft": 5,
            "source": {
                "artifact_id": artifact["id"],
                "pack_id": "dnd5e.addon.tortle-package",
                "pack_version": "1.0.0",
                "rule_refs": ["tortle-package:p4"],
            },
        }
        attack = next(
            item
            for item in applied["derived"]["inventory"]["weapon_attacks"]
            if item["item_id"] == claws["id"]
        )
        assert attack["attack_bonus"] == 5
        assert attack["damage_expression"] == "1d4 + 3"
        assert attack["attack_type"] == "melee"
        assert attack["intrinsic"] is True
        assert attack["natural_weapon"] is True
        assert attack["unarmed_strike"] is True

        for action, payload in (
            ("remove", {"item_id": claws["id"]}),
            ("update", {"item_id": claws["id"], "changes": {"name": "Forged"}}),
            ("equip", {"item_id": claws["id"], "slot": "main_hand"}),
            ("recharge", {"item_id": claws["id"], "trigger": "dawn"}),
            ("consume_ammunition", {"item_id": claws["id"], "quantity": 1}),
        ):
            with pytest.raises(ToolError):
                await _call(
                    server,
                    "inventory_change",
                    {
                        "owner": "character",
                        "action": action,
                        "owner_id": tortle["id"],
                        "payload": payload,
                        "expected_revision": applied["revision"],
                        "idempotency_key": f"reject-{action}",
                    },
                )
        unchanged = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": tortle["id"]}},
        )
        assert unchanged["revision"] == applied["revision"]
        with pytest.raises(ToolError, match="revision conflict"):
            await _call(
                server,
                "character_sheet_replace",
                {
                    "character_id": tortle["id"],
                    "sheet": applied["sheet"],
                    "expected_revision": tortle["revision"],
                    "idempotency_key": "stale-claws-replacement",
                },
            )

        forged = deepcopy(applied["sheet"])
        forged["traits"]["intrinsic_attacks"] = []
        with pytest.raises(ToolError, match="authoritative intrinsic attack provenance"):
            await _call(
                server,
                "character_sheet_replace",
                {
                    "character_id": tortle["id"],
                    "sheet": forged,
                    "expected_revision": applied["revision"],
                    "idempotency_key": "remove-claws-by-replacement",
                },
            )
        with pytest.raises(ToolError, match="character_content_apply"):
            await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Forged Tortle",
                        "sheet": applied["sheet"],
                    },
                    "principal_id": "system:local",
                    "idempotency_key": "forged-character",
                },
            )

        original_guard = server_module._reject_new_intrinsic_attack_provenance
        monkeypatch.setattr(
            server_module,
            "_reject_new_intrinsic_attack_provenance",
            lambda _sheet: None,
        )
        poisoned_template = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"name": "Pre-upgrade forged template", "sheet": applied["sheet"]},
                "principal_id": "system:local",
                "idempotency_key": "poisoned-template",
            },
        )
        monkeypatch.setattr(
            server_module,
            "_reject_new_intrinsic_attack_provenance",
            original_guard,
        )
        with pytest.raises(ToolError, match="character_content_apply"):
            await _call(
                server,
                "character_create_from",
                {
                    "mode": "template",
                    "payload": {
                        "template_id": poisoned_template["id"],
                        "campaign_id": campaign["id"],
                    },
                    "principal_id": "system:local",
                    "idempotency_key": "instantiate-poisoned-template",
                },
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
                        "source_character_id": tortle["id"],
                        "target_character_id": target["id"],
                        "item_id": claws["id"],
                        "quantity": 1,
                        "expected_campaign_revision": current_campaign["revision"],
                        "expected_source_revision": applied["revision"],
                        "expected_target_revision": target["revision"],
                    },
                    "principal_id": "system:local",
                    "idempotency_key": "transfer-claws",
                },
            )
        after_transfer = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        assert after_transfer["revision"] == current_campaign["revision"]
        source_after_transfer = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": tortle["id"]}},
        )
        target_after_transfer = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": target["id"]}},
        )
        assert source_after_transfer["revision"] == applied["revision"]
        assert target_after_transfer["revision"] == target["revision"]

        restarted = create_server(config)
        durable = await _call(
            restarted,
            "character_query",
            {"view": "get", "payload": {"character_id": tortle["id"]}},
        )
        assert durable["sheet"]["traits"]["intrinsic_attacks"] == [claws]
        assert any(
            item["item_id"] == claws["id"]
            for item in durable["derived"]["inventory"]["weapon_attacks"]
        )

        started = await _raw(
            restarted,
            "combat_start",
            {
                "positioning_mode": "grid",
                "battle_map": {"width_cells": 4, "height_cells": 4},
                "campaign_id": campaign["id"],
                "participant_ids": [tortle["id"], target["id"]],
                "participant_config": [
                    {
                        "actor_id": tortle["id"],
                        "initiative": 20,
                        "position": {"x": 0, "y": 0},
                    },
                    {
                        "actor_id": target["id"],
                        "initiative": 10,
                        "position": {"x": 1, "y": 0},
                    },
                ],
                "expected_revision": after_transfer["revision"],
                "idempotency_key": "start-combat",
            },
        )
        attacked = await _raw(
            restarted,
            "combat_resolve_attack",
            {
                "campaign_id": campaign["id"],
                "actor_id": tortle["id"],
                "target_id": target["id"],
                "action": {"weapon_id": claws["id"], "attack_mode": "melee"},
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "claw-attack",
            },
        )
        if attacked["status"] == "pending_reaction":
            attacked = await _call(
                restarted,
                "combat_choice",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": target["id"],
                    "action": "resolve_defense",
                    "payload": {
                        "choice_id": attacked["choice"]["id"],
                        "selection": {"id": "decline"},
                    },
                    "expected_revision": attacked["campaign_revision"],
                    "idempotency_key": "decline-defense",
                },
            )
        assert attacked["status"] == "committed", attacked
        assert attacked["result"]["hit"] is True
        assert attacked["result"]["damage"]["expression"] == "1d4 + 3"
        assert attacked["result"]["damage"]["damage_type"] == "slashing"
        assert 4 <= attacked["result"]["damage"]["input_amount"] <= 7
        assert attacked["result"]["unarmed_strike"] is True
        assert attacked["result"]["natural_weapon"] is True
        assert attacked["result"]["intrinsic_attack"] is True
        assert any(
            receipt["mechanic_id"] == "dnd5e.core.attack.unarmed_strike"
            for receipt in attacked["result"]["rule_receipts"]
        )

        final_target = await _call(
            restarted,
            "character_query",
            {"view": "get", "payload": {"character_id": target["id"]}},
        )
        assert final_target["sheet"]["combat"]["hp"]["value"] == (
            20 - attacked["result"]["damage"]["input_amount"]
        )

    asyncio.run(exercise())
