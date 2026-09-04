import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    equip_inventory_item,
)
from sagasmith_dnd.standard_feature_ids import (
    TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
    TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
    TORTLE_SHELL_DEFENSE_ARTIFACT_ID,
    TORTLE_SHELL_DEFENSE_LEGACY_PACK_ID,
)

from sagasmith_dnd_mcp import server as server_module
from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


async def _raw_call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result


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


@pytest.mark.fresh_database
@pytest.mark.parametrize("official_armored", [False, True])
def test_tortle_shell_defense_materializes_and_settles_atomically_across_restart(
    tmp_path: Path,
    official_armored: bool,
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        library = (
            Path(__file__).resolve().parents[4]
            / "SagaSmith-dnd-content-library"
            / "content-library"
        )
        if not (library / "index.json").is_file():
            pytest.skip("requires the sibling finalized content library")
        config = replace(config, official_content_library=library)
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "Tortle Shell Defense",
                "edition": "2014",
                "random_seed": "tortle-shell-defense-seed",
                "idempotency_key": "campaign",
            },
        )
        await _call(
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
                "expected_revision": campaign["revision"],
                "idempotency_key": "activate-official-tortle",
            },
        )
        sheet = default_character_sheet()
        if official_armored:
            sheet, armor_id = add_inventory_item(
                sheet,
                {
                    "id": "plate-plus-three",
                    "name": "+3 Plate",
                    "kind": "armor",
                    "mechanics": {
                        "base_ac": 18,
                        "category": "heavy",
                        "dexterity_mode": "none",
                        "magic_bonus": 3,
                    },
                },
            )
            sheet = equip_inventory_item(sheet, armor_id, "armor")
        tortle = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Source-bound Tortle",
                    "sheet": sheet,
                },
                "principal_id": "system:local",
                "idempotency_key": "tortle",
            },
        )
        applied = await _call(
            server,
            "character_content_apply",
            {
                "character_id": tortle["id"],
                "artifact_id": TORTLE_SHELL_DEFENSE_ARTIFACT_ID,
                "selection": {},
                "expected_revision": tortle["revision"],
                "idempotency_key": "apply-tortle",
            },
        )
        applied_selection = applied["sheet"]["content"]["selections"][0]
        assert applied_selection["artifact_id"] == TORTLE_SHELL_DEFENSE_ARTIFACT_ID
        assert applied_selection["pack_id"] == TORTLE_SHELL_DEFENSE_LEGACY_PACK_ID
        assert applied_selection["pack_version"] == "1.0.0"
        applied_feature = next(
            item
            for item in applied["sheet"]["content"]["features"]
            if item["name"] == "Shell Defense"
        )
        assert applied_feature["id"] == f"{TORTLE_SHELL_DEFENSE_ARTIFACT_ID}.feature.shell-defense"
        assert applied_feature["pack_id"] == TORTLE_SHELL_DEFENSE_LEGACY_PACK_ID
        assert applied_feature["pack_version"] == "1.0.0"
        assert applied_feature["source_key"] == "Tortle"
        observer = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Observer",
                    "sheet": default_character_sheet(),
                },
                "principal_id": "system:local",
                "idempotency_key": "observer",
            },
        )
        current_campaign = await _call(
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
                "expected_revision": current_campaign["revision"],
                "idempotency_key": "play",
            },
        )
        started = await _call(
            server,
            "combat_start",
            {
                "campaign_id": campaign["id"],
                "participant_ids": [tortle["id"], observer["id"]],
                "participant_config": [
                    {"actor_id": tortle["id"], "initiative": 20, "tie_breaker": 0},
                    {"actor_id": observer["id"], "initiative": 10, "tie_breaker": 1},
                ],
                "positioning_mode": "agent",
                "expected_revision": phase["campaign_revision"],
                "idempotency_key": "start",
            },
        )
        actions = await _call(
            server,
            "combat_query",
            {
                "campaign_id": campaign["id"],
                "view": "available_actions",
                "actor_id": tortle["id"],
            },
        )
        assert "shell_defense" in actions["actions"]

        storage = server_module.SagaSmithStorage(config)
        campaigns = server_module.CampaignService(storage.database)
        before_stale = campaigns.get(campaign["id"])
        with pytest.raises(Exception, match="campaign revision conflict"):
            await _call(
                server,
                "combat_common_action",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": tortle["id"],
                    "action": "shell_defense",
                    "expected_revision": started["campaign_revision"] - 1,
                    "idempotency_key": "stale-withdraw",
                },
            )
        assert campaigns.get(campaign["id"]).state == before_stale.state

        withdraw_request = {
            "campaign_id": campaign["id"],
            "actor_id": tortle["id"],
            "action": "shell_defense",
            "expected_revision": started["campaign_revision"],
            "idempotency_key": "withdraw",
        }
        withdrawn = await _call(server, "combat_common_action", withdraw_request)
        assert await _call(server, "combat_common_action", withdraw_request) == withdrawn
        assert withdrawn["campaign_revision"] == started["campaign_revision"] + 1
        assert withdrawn["condition_resolution"] == {
            "kind": "tortle_shell_defense",
            "withdrawn": True,
            "armor_class": 21,
            "speed_multiplier": 0.0,
            "conditions": ["prone"],
        }
        acting = next(
            item for item in withdrawn["combat"]["combatants"] if item["actor_id"] == tortle["id"]
        )
        assert acting["turn_budget"]["main_action"] == 0
        assert acting["turn_budget"]["reaction"] == 0
        assert acting["turn_budget"]["movement"] == 0

        with pytest.raises(Exception, match="only the bonus action to emerge"):
            await _call(
                server,
                "combat_common_action",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": tortle["id"],
                    "action": "dodge",
                    "expected_revision": withdrawn["campaign_revision"],
                    "idempotency_key": "blocked-dodge",
                },
            )
        save_revision = withdrawn["campaign_revision"]
        expected_save_modes = (
            {
                "strength": "normal",
                "constitution": "advantage",
                "dexterity": "disadvantage",
            }
            if official_armored
            else {
                "strength": "advantage",
                "constitution": "advantage",
                "dexterity": "disadvantage",
            }
        )
        for ability, roll_mode in expected_save_modes.items():
            saved = await _raw_call(
                server,
                "combat_check",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": tortle["id"],
                    "kind": "save",
                    "ability": ability,
                    "dc": 15,
                    "expected_revision": save_revision,
                    "idempotency_key": f"{ability}-save",
                },
            )
            assert saved["result"]["roll_mode"] == roll_mode
            assert len(saved["result"]["rolls"]) == (1 if roll_mode == "normal" else 2)
            assert any(
                item["mechanic_id"] == "dnd5e.core.activity.tortle_shell_defense"
                for item in saved["result"]["rule_receipts"]
            )
            save_revision = saved["campaign_revision"]

        restarted = create_server(config)
        persisted = await _call(
            restarted,
            "character_query",
            {
                "view": "get",
                "payload": {"character_id": tortle["id"]},
                "principal_id": "system:local",
            },
        )
        assert persisted["derived"]["armor_class"] == 21
        assert persisted["sheet"]["conditions"] == ["prone"]
        assert any(
            item["id"] == "dnd5e.effect.tortle_shell_defense"
            for item in persisted["sheet"]["effects"]
        )
        restarted_actions = await _call(
            restarted,
            "combat_query",
            {
                "campaign_id": campaign["id"],
                "view": "available_actions",
                "actor_id": tortle["id"],
            },
        )
        assert restarted_actions["actions"] == ["emerge_shell"]

        emerge_request = {
            "campaign_id": campaign["id"],
            "actor_id": tortle["id"],
            "action": "emerge_shell",
            "expected_revision": save_revision,
            "idempotency_key": "emerge",
        }
        emerged = await _call(restarted, "combat_common_action", emerge_request)
        assert await _call(restarted, "combat_common_action", emerge_request) == emerged
        assert emerged["condition_resolution"]["withdrawn"] is False
        assert emerged["condition_resolution"]["armor_class"] == 17
        assert emerged["condition_resolution"]["speed_multiplier"] == 1.0
        assert emerged["condition_resolution"]["conditions"] == []
        final_character = await _call(
            restarted,
            "character_query",
            {
                "view": "get",
                "payload": {"character_id": tortle["id"]},
                "principal_id": "system:local",
            },
        )
        assert final_character["derived"]["armor_class"] == 17
        assert final_character["sheet"]["conditions"] == []
        assert not any(
            item["id"] == "dnd5e.effect.tortle_shell_defense"
            for item in final_character["sheet"]["effects"]
        )
        assert applied["sheet"]["progression"]["species"] == "Tortle"

    asyncio.run(exercise())
