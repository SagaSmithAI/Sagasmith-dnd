import asyncio
from pathlib import Path

import pytest
from authoring_helpers import import_and_activate_addon_fixture
from sagasmith_core.indexed_source import rule_chunk_key
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.content_validation import build_selection_contract
from sagasmith_dnd.standard_feature_ids import (
    TORTLE_SHELL_DEFENSE_ARTIFACT_ID,
    TORTLE_SHELL_DEFENSE_LEGACY_PACK_ID,
    TORTLE_SHELL_DEFENSE_SOURCE_KEY,
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


def _tortle_artifact() -> dict:
    source_text = (
        "# Reviewed fixture\n\n## Tortle\n\n"
        "Mechanics and choices for Tortle were reviewed for this fixture."
    )
    source_ref = (
        f"rule-source:{TORTLE_SHELL_DEFENSE_SOURCE_KEY}#chunk:"
        f"{rule_chunk_key(TORTLE_SHELL_DEFENSE_SOURCE_KEY, 0, 0, source_text)}"
    )
    artifact = {
        "id": TORTLE_SHELL_DEFENSE_ARTIFACT_ID,
        "kind": "species",
        "application_state": "selection_ready",
        "mechanical_scope": "mechanical",
        "execution_state": "ruling_ready",
        "semantic_resolution": {
            "status": "resolved",
            "mode": "agent_ruling",
            "first_use_compilation_required": False,
            "clause_ids": ["tortle-shell-defense"],
        },
        "ruling_requirements": [
            {
                "kind": "source_bound_import_resolution",
                "policy_ref": "rule_clause.v1",
                "reason": "Apply only the exact source-bound Tortle traits.",
                "default_resolver": "agent",
                "ruling_kind": "agent_dm_adjudication",
                "source_excerpt": "The Tortle can withdraw and emerge under the cited rules.",
                "requires_external_input_only_for": [],
            }
        ],
        "rule_clauses": [
            {
                "schema_version": 1,
                "id": "tortle-shell-defense",
                "title": "Tortle Shell Defense",
                "scope": "mechanical",
                "source_citations": [
                    {
                        "source": f"rule-source:{TORTLE_SHELL_DEFENSE_SOURCE_KEY}",
                        "source_ref": {"page": 4},
                        "source_excerpt": (
                            "Withdraw as an action; gain the cited AC and save modifiers, "
                            "become prone and immobile, lose reactions, and emerge with a "
                            "bonus action."
                        ),
                    }
                ],
                "settlement": {
                    "mode": "agent_ruling",
                    "default_resolver": "agent",
                    "ruling_kind": "agent_dm_adjudication",
                    "reason": "Apply only the exact source-bound Tortle traits.",
                },
            }
        ],
        "card": {
            "name": "Tortle",
            "base_species": "Tortle",
            "grants": {
                "natural_armor_base": 17,
                "natural_armor_includes_dexterity": False,
                "walk_speed": 30,
                "features": [
                    {
                        "name": "Shell Defense",
                        "description": (
                            "Source-bound action: withdraw or emerge and apply the cited AC, "
                            "save, speed, prone, reaction, and action restrictions."
                        ),
                    }
                ],
                "unresolved": [],
            },
        },
        "rule_refs": [source_ref],
    }
    artifact["selection_contract"] = build_selection_contract(
        artifact,
        status="ready",
        references=[source_ref],
    )
    return artifact


@pytest.mark.fresh_database
def test_tortle_shell_defense_materializes_and_settles_atomically_across_restart(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
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
        artifact = _tortle_artifact()
        await import_and_activate_addon_fixture(
            _call,
            server,
            campaign["id"],
            config.home,
            manifest={
                "id": TORTLE_SHELL_DEFENSE_LEGACY_PACK_ID,
                "version": "1.0.1",
                "title": "Reviewed Tortle Package",
                "namespace": TORTLE_SHELL_DEFENSE_LEGACY_PACK_ID,
                "system_id": "dnd5e",
                "editions": ["2014"],
                "capabilities": [],
            },
            artifacts=[artifact],
            mechanics=[],
            expected_revision=campaign["revision"],
            request_key="tortle-shell-defense",
            source_key_override=TORTLE_SHELL_DEFENSE_SOURCE_KEY,
        )
        tortle = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Source-bound Tortle",
                    "sheet": default_character_sheet(),
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
        for ability, roll_mode in (
            ("strength", "advantage"),
            ("constitution", "advantage"),
            ("dexterity", "disadvantage"),
        ):
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
            assert len(saved["result"]["rolls"]) == 2
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
