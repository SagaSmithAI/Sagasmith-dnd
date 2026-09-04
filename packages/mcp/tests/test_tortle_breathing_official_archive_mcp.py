import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.breathing import BREATHING_EFFECT_ID
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.standard_feature_ids import (
    TORTLE_HOLD_BREATH_ARTIFACT_ID,
    TORTLE_HOLD_BREATH_FEATURE_ID,
    TORTLE_HOLD_BREATH_LEGACY_PACK_ID,
    TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
    TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
)

import sagasmith_dnd_mcp.server as server_module
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
    monkeypatch: pytest.MonkeyPatch,
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
            active_snapshot = await _call(
                server,
                "snapshot_create",
                {
                    "campaign_id": campaign["id"],
                    "label": "Tortle active hold",
                    "expected_revision": underwater["campaign_revision"],
                    "expected_head_snapshot_id": "",
                    "idempotency_key": "active-snapshot",
                },
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

            await _call(
                server,
                "snapshot_create",
                {
                    "campaign_id": campaign["id"],
                    "label": "Tortle near expiry",
                    "expected_revision": before_boundary["campaign_revision"],
                    "expected_head_snapshot_id": active_snapshot["id"],
                    "idempotency_key": "near-snapshot",
                },
            )
            branches = await _call(
                server,
                "branch_query",
                {"campaign_id": campaign["id"], "view": "list", "payload": {}},
            )
            main_branch = next(item for item in branches if item["is_current"])
            branch_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            await _call(
                server,
                "branch_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "create",
                    "payload": {
                        "name": "tortle-active-hold",
                        "from_snapshot_id": active_snapshot["id"],
                        "checkout": True,
                    },
                    "expected_revision": branch_campaign["revision"],
                    "expected_branch_id": main_branch["id"],
                    "idempotency_key": "active-fork",
                },
            )
            forked = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            forked_effect = next(
                item for item in forked["sheet"]["effects"] if item["id"] == BREATHING_EFFECT_ID
            )
            assert forked_effect["metadata"]["hold_remaining_rounds"] == 600
            assert forked["sheet"]["content"] == applied["sheet"]["content"]

            close_server(server)
            server = create_server(config)
            restarted_fork = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            restarted_effect = next(
                item
                for item in restarted_fork["sheet"]["effects"]
                if item["id"] == BREATHING_EFFECT_ID
            )
            assert restarted_effect["metadata"]["hold_remaining_rounds"] == 600
            stale_revision = restarted_fork["revision"]
            replacement = deepcopy(restarted_fork["sheet"])
            mutated = await _call(
                server,
                "character_sheet_replace",
                {
                    "character_id": actor["id"],
                    "sheet": replacement,
                    "expected_revision": stale_revision,
                    "idempotency_key": "tortle-cas-mutation",
                },
            )
            before_stale = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            with pytest.raises(ToolError, match="revision"):
                await _call(
                    server,
                    "character_state_change",
                    {
                        "character_id": actor["id"],
                        "action": "breathing_transition",
                        "payload": {"can_breathe": True},
                        "expected_revision": stale_revision,
                        "idempotency_key": "tortle-stale-air",
                    },
                )
            after_stale = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert mutated["revision"] == before_stale["revision"]
            assert after_stale == before_stale
            assert (
                next(
                    item
                    for item in after_stale["sheet"]["effects"]
                    if item["id"] == BREATHING_EFFECT_ID
                )["metadata"]["hold_remaining_rounds"]
                == 600
            )
            current_after_fork = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            replay_to_near = await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "clock_advance",
                    "payload": {"period": "round", "count": 599},
                    "expected_revision": current_after_fork["revision"],
                    "idempotency_key": "fork-near-hour",
                },
            )
            await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "clock_advance",
                    "payload": {"period": "round", "count": 1},
                    "expected_revision": replay_to_near["campaign_revision"],
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

            observer = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Breathing observer",
                        "sheet": default_character_sheet(),
                    },
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
                    "positioning_mode": "agent",
                    "participant_ids": [actor["id"], observer["id"]],
                    "participant_config": [
                        {"actor_id": actor["id"], "initiative": 20},
                        {"actor_id": observer["id"], "initiative": 10},
                    ],
                    "expected_revision": phase["campaign_revision"],
                    "idempotency_key": "combat-start",
                },
            )
            after_tortle_turn = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "expected_revision": started["campaign_revision"],
                    "idempotency_key": "tortle-turn",
                },
            )
            still_alive = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert still_alive["sheet"]["combat"]["hp"]["value"] == 10
            after_observer_turn = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": observer["id"],
                    "expected_revision": after_tortle_turn["campaign_revision"],
                    "idempotency_key": "observer-turn",
                },
            )
            dropped = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert dropped["sheet"]["combat"]["hp"]["value"] == 0
            assert "suffocating" in dropped["sheet"]["conditions"]

            ended = await _call(
                server,
                "combat_end",
                {
                    "campaign_id": campaign["id"],
                    "outcome": {"status": "withdrawal", "summary": "The observer leaves."},
                    "expected_revision": after_observer_turn["campaign_revision"],
                    "idempotency_key": "combat-end",
                },
            )
            assert ended["combat"]["active"] is False
            # Ending combat legitimately refreshes the actor's combat state.
            # Recovery rejection must be compared with that committed baseline.
            dropped = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert dropped["sheet"]["combat"]["hp"]["value"] == 0
            dropped_revision = dropped["revision"]
            dropped_sheet = dropped["sheet"]
            for action, payload, key, message in (
                (
                    "heal",
                    {"amount": 1},
                    "heal-suffocating",
                    "cannot regain hit points until it can breathe",
                ),
                (
                    "stabilize",
                    {"source_actor_id": observer["id"], "reason": "Immediate aid"},
                    "stabilize-suffocating",
                    "cannot become stable until it can breathe",
                ),
            ):
                with pytest.raises(ToolError, match=message):
                    await _call(
                        server,
                        "character_state_change",
                        {
                            "character_id": actor["id"],
                            "action": action,
                            "payload": payload,
                            "expected_revision": dropped_revision,
                            "idempotency_key": key,
                        },
                    )
            unchanged = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert unchanged["revision"] == dropped_revision
            assert unchanged["sheet"] == dropped_sheet

            restored = await _call(
                server,
                "character_state_change",
                {
                    "character_id": actor["id"],
                    "action": "breathing_transition",
                    "payload": {"can_breathe": True},
                    "expected_revision": unchanged["revision"],
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
                        "expected_revision": unchanged["revision"],
                        "idempotency_key": "restore-air",
                    },
                )
                == restored
            )
            original_death_save = server_module.resolve_death_save_to_sheet

            class _NaturalTwenty:
                def randint(self, _lower: int, _upper: int) -> int:
                    return 20

            def deterministic_death_save(sheet, **kwargs):
                kwargs["rng"] = _NaturalTwenty()
                return original_death_save(sheet, **kwargs)

            monkeypatch.setattr(
                server_module,
                "resolve_death_save_to_sheet",
                deterministic_death_save,
            )
            after_air = await _call(
                server,
                "character_state_change",
                {
                    "character_id": actor["id"],
                    "action": "death_save",
                    "payload": {},
                    "expected_revision": restored["character"]["revision"],
                    "idempotency_key": "death-save-after-air",
                },
            )
            assert after_air["result"]["natural"] == 20
            assert after_air["result"]["outcome"] == "revived"
            assert after_air["character"]["sheet"]["combat"]["hp"]["value"] == 1
        finally:
            close_server(server)

    asyncio.run(exercise())
