import asyncio
from pathlib import Path

import pytest
from sagasmith_dnd.breathing import BREATHING_EFFECT_ID
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.standard_feature_ids import (
    TORTLE_HOLD_BREATH_ARTIFACT_ID,
    TORTLE_HOLD_BREATH_FEATURE_ID,
    TORTLE_HOLD_BREATH_LEGACY_PACK_ID,
    TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
    TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
)

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    value = result.get("result", result) if isinstance(result, dict) else result
    if isinstance(value, dict) and "action" in value and "result" in value:
        return value["result"]
    return value


def _config(tmp_path: Path) -> McpConfig:
    repository_root = Path(__file__).resolve().parents[3]
    library = repository_root.parent / "SagaSmith-dnd-content-library" / "content-library"
    return McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=repository_root / "skills",
        modulegen_skills_dir=tmp_path / "modulegen-skills",
        auto_seed_rules=True,
        official_content_library=library,
    )


@pytest.mark.fresh_database
def test_finalized_tortle_hold_breath_is_one_hour_and_recovers_after_restart(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    if not (config.official_content_library / "index.json").is_file():
        pytest.skip("requires the sibling finalized content library")

    async def exercise() -> None:
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Official Tortle breathing",
                    "edition": "2014",
                    "idempotency_key": "campaign",
                },
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
                    "idempotency_key": "activate-tortle",
                },
            )
            assert activated["activation"]["enabled"] is True
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Archive Tortle",
                        "sheet": sheet,
                    },
                    "idempotency_key": "actor",
                },
            )
            applied = await _call(
                server,
                "character_content_apply",
                {
                    "character_id": actor["id"],
                    "artifact_id": TORTLE_HOLD_BREATH_ARTIFACT_ID,
                    "selection": {},
                    "expected_revision": actor["revision"],
                    "idempotency_key": "apply-tortle",
                },
            )
            selection = applied["sheet"]["content"]["selections"][0]
            assert selection["artifact_id"] == TORTLE_HOLD_BREATH_ARTIFACT_ID
            assert selection["pack_id"] == TORTLE_HOLD_BREATH_LEGACY_PACK_ID
            assert selection["pack_version"] == "1.0.0"
            feature = next(
                item
                for item in applied["sheet"]["content"]["features"]
                if item["id"] == TORTLE_HOLD_BREATH_FEATURE_ID
            )
            assert feature["pack_id"] == TORTLE_HOLD_BREATH_LEGACY_PACK_ID
            assert feature["pack_version"] == "1.0.0"

            underwater = await _call(
                server,
                "character_state_change",
                {
                    "character_id": actor["id"],
                    "action": "breathing_transition",
                    "payload": {"can_breathe": False},
                    "expected_revision": applied["revision"],
                    "idempotency_key": "underwater",
                },
            )
            assert underwater["result"]["effect"]["metadata"]["hold_remaining_rounds"] == 600
            assert (
                await _call(
                    server,
                    "character_state_change",
                    {
                        "character_id": actor["id"],
                        "action": "breathing_transition",
                        "payload": {"can_breathe": False},
                        "expected_revision": applied["revision"],
                        "idempotency_key": "underwater",
                    },
                )
                == underwater
            )
            clock = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            before_boundary = await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "clock_advance",
                    "payload": {
                        "period": "round",
                        "count": 599,
                    },
                    "expected_revision": clock["revision"],
                    "idempotency_key": "near-hour",
                },
            )
            near_boundary = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            effect = next(
                item
                for item in near_boundary["sheet"]["effects"]
                if item["id"] == BREATHING_EFFECT_ID
            )
            assert effect["metadata"]["hold_remaining_rounds"] == 1
            assert effect["metadata"]["phase"] == "holding_breath"
            assert near_boundary["sheet"]["combat"]["hp"]["value"] == 10

            close_server(server)
            server = create_server(config)
            await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "clock_advance",
                    "payload": {"period": "round", "count": 1},
                    "expected_revision": before_boundary["campaign_revision"],
                    "idempotency_key": "one-hour-boundary",
                },
            )
            at_boundary = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            effect = next(
                item
                for item in at_boundary["sheet"]["effects"]
                if item["id"] == BREATHING_EFFECT_ID
            )
            assert effect["metadata"]["hold_remaining_rounds"] == 0
            assert effect["metadata"]["phase"] == "suffocating"
            assert at_boundary["sheet"]["combat"]["hp"]["value"] == 10

            restored = await _call(
                server,
                "character_state_change",
                {
                    "character_id": actor["id"],
                    "action": "breathing_transition",
                    "payload": {"can_breathe": True},
                    "expected_revision": at_boundary["revision"],
                    "idempotency_key": "restore-air",
                },
            )
            assert restored["result"]["status"] == "breathing_restored"
            restored_sheet = restored["character"]["sheet"]
            assert not any(item["id"] == BREATHING_EFFECT_ID for item in restored_sheet["effects"])
            assert any(item["id"] != BREATHING_EFFECT_ID for item in restored_sheet["effects"])
            assert "suffocating" not in restored_sheet["conditions"]
            assert (
                await _call(
                    server,
                    "character_state_change",
                    {
                        "character_id": actor["id"],
                        "action": "breathing_transition",
                        "payload": {"can_breathe": True},
                        "expected_revision": at_boundary["revision"],
                        "idempotency_key": "restore-air",
                    },
                )
                == restored
            )
        finally:
            close_server(server)

    asyncio.run(exercise())
