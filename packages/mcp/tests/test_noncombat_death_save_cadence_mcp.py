from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_core import CampaignService
from sagasmith_dnd.character_schema import default_character_sheet, validate_party_state
from sagasmith_dnd.core_rule_pack import get_core_rule_pack
from sagasmith_dnd.game_time import advance_game_time
from sagasmith_dnd.random_stream import CampaignRandomStream, use_random_stream

import sagasmith_dnd_mcp.server as server_module
from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server


async def _call(server, name: str, arguments: dict):
    _, structured = await server.call_tool(name, arguments)
    value = structured.get("result", structured) if isinstance(structured, dict) else structured
    if isinstance(value, dict) and "action" in value and "result" in value:
        return value["result"]
    return value


async def _call_raw(server, name: str, arguments: dict):
    _, structured = await server.call_tool(name, arguments)
    return structured


def _config(tmp_path: Path) -> McpConfig:
    workspace = Path(__file__).resolve().parents[3]
    return McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=workspace / "skills",
        modulegen_skills_dir=workspace / "skills" / "dnd-module-generator",
        auto_seed_rules=False,
    )


def _dying_sheet() -> dict:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 0, "max": 12, "temp": 0}
    sheet["conditions"] = ["prone", "unconscious"]
    return sheet


async def _campaign_and_actor(
    server,
    *,
    edition: str = "2014",
    suffix: str = "main",
    enter_play: bool = True,
):
    campaign = await _call(
        server,
        "campaign_create",
        {
            "name": f"Death-save cadence {suffix}",
            "edition": edition,
            "random_seed": "death-8",
            "idempotency_key": f"campaign-{suffix}",
        },
    )
    actor = await _call(
        server,
        "character_create_from",
        {
            "mode": "direct",
            "payload": {
                "campaign_id": campaign["id"],
                "name": "Dying hero",
                "sheet": _dying_sheet(),
            },
            "idempotency_key": f"actor-{suffix}",
        },
    )
    phase = None
    if enter_play:
        current = await _campaign(server, campaign["id"])
        phase = await _call(
            server,
            "game_phase",
            {
                "campaign_id": campaign["id"],
                "action": "set",
                "tool_profile": "play",
                "expected_revision": current["revision"],
                "idempotency_key": f"play-{suffix}",
            },
        )
    return campaign["id"], actor, phase


async def _campaign(server, campaign_id: str) -> dict:
    return await _call(
        server,
        "campaign_query",
        {"view": "get", "payload": {"campaign_id": campaign_id}},
    )


async def _actor(server, actor_id: str) -> dict:
    return await _call(
        server,
        "character_query",
        {"view": "get", "payload": {"character_id": actor_id}},
    )


async def _advance_round(server, campaign_id: str, key: str) -> dict:
    campaign = await _campaign(server, campaign_id)
    return await _call(
        server,
        "campaign_change",
        {
            "campaign_id": campaign_id,
            "action": "clock_advance",
            "payload": {"period": "round", "count": 1},
            "expected_revision": campaign["revision"],
            "idempotency_key": key,
        },
    )


async def _death_save(server, campaign_id: str, arguments: dict) -> dict:
    campaign = await _campaign(server, campaign_id)
    stream = CampaignRandomStream.from_campaign_state(
        campaign_id,
        campaign["state"],
        operation="character_state_change",
        idempotency_key=str(arguments.get("idempotency_key") or ""),
        campaign_revision=campaign["revision"],
    )
    with use_random_stream(stream):
        return await _call(server, "character_state_change", arguments)


def test_2014_noncombat_death_saves_require_distinct_rounds_and_survive_restart(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        campaign_id, actor, _phase = await _campaign_and_actor(server)
        initial_revision = actor["revision"]
        first_args = {
            "character_id": actor["id"],
            "action": "death_save",
            "payload": {},
            "expected_revision": initial_revision,
            "idempotency_key": "save-round-0",
        }
        first = await _death_save(server, campaign_id, first_args)

        assert first["result"]["natural"] == 14
        assert first["result"]["cadence"] == {
            "elapsed_tick": 0,
            "next_eligible_elapsed_tick": 1,
            "advance_contract": {
                "tool": "campaign_change",
                "action": "clock_advance",
                "payload": {"period": "round", "count": 1},
            },
        }
        assert first["character"]["sheet"]["combat"]["last_death_save_elapsed_tick"] == 0
        assert any(
            receipt["mechanic_id"] == "dnd5e.core.mcp.death_save_turn_cadence"
            and receipt["event"] == "death_save.turn_start"
            for receipt in first["result"]["rule_receipts"]
        )

        before_reject = await _campaign(server, campaign_id)
        actor_before_reject = await _actor(server, actor["id"])
        with pytest.raises(ToolError, match="already made a death save.*current game-time tick"):
            await _death_save(
                server,
                campaign_id,
                {
                    "character_id": actor["id"],
                    "action": "death_save",
                    "payload": {},
                    "expected_revision": actor_before_reject["revision"],
                    "idempotency_key": "same-tick-new-key",
                },
            )
        after_reject = await _campaign(server, campaign_id)
        actor_after_reject = await _actor(server, actor["id"])
        assert after_reject["revision"] == before_reject["revision"]
        assert after_reject["state"]["random_stream"] == before_reject["state"]["random_stream"]
        assert actor_after_reject == actor_before_reject

        assert await _death_save(server, campaign_id, first_args) == first
        await _advance_round(server, campaign_id, "advance-round-1")
        before_stale = await _campaign(server, campaign_id)
        with pytest.raises(ToolError, match="character revision conflict"):
            await _death_save(
                server,
                campaign_id,
                {
                    "character_id": actor["id"],
                    "action": "death_save",
                    "payload": {},
                    "expected_revision": initial_revision,
                    "idempotency_key": "stale-character-cas",
                },
            )
        assert (await _campaign(server, campaign_id))["state"]["random_stream"] == before_stale[
            "state"
        ]["random_stream"]

        second_args = {
            "character_id": actor["id"],
            "action": "death_save",
            "payload": {},
            "expected_revision": first["character"]["revision"],
            "idempotency_key": "save-round-1",
        }
        second = await _death_save(server, campaign_id, second_args)
        assert second["result"]["natural"] == 10
        assert second["result"]["cadence"]["elapsed_tick"] == 1

        restarted = create_server(config)
        assert await _death_save(restarted, campaign_id, second_args) == second
        restarted_actor = await _actor(restarted, actor["id"])
        before_restart_reject = await _campaign(restarted, campaign_id)
        with pytest.raises(ToolError, match="already made a death save.*current game-time tick"):
            await _death_save(
                restarted,
                campaign_id,
                {
                    "character_id": actor["id"],
                    "action": "death_save",
                    "payload": {},
                    "expected_revision": restarted_actor["revision"],
                    "idempotency_key": "restart-same-tick",
                },
            )
        assert await _campaign(restarted, campaign_id) == before_restart_reject

        await _advance_round(restarted, campaign_id, "advance-round-2")
        third = await _death_save(
            restarted,
            campaign_id,
            {
                "character_id": actor["id"],
                "action": "death_save",
                "payload": {},
                "expected_revision": restarted_actor["revision"],
                "idempotency_key": "save-round-2",
            },
        )
        assert third["result"]["natural"] == 15
        assert third["result"]["outcome"] == "stable"
        assert third["character"]["sheet"]["combat"]["death_saves"] == {
            "successes": 0,
            "failures": 0,
        }
        terminal_campaign = await _campaign(restarted, campaign_id)
        assert terminal_campaign["state"]["random_stream"]["position"] == 3
        with pytest.raises(ToolError, match="stable actors do not make additional death saves"):
            await _death_save(
                restarted,
                campaign_id,
                {
                    "character_id": actor["id"],
                    "action": "death_save",
                    "payload": {},
                    "expected_revision": third["character"]["revision"],
                    "idempotency_key": "stable-no-roll",
                },
            )
        assert await _campaign(restarted, campaign_id) == terminal_campaign

    asyncio.run(exercise())


def test_death_save_settled_boundaries_reject_without_random_or_writes(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign_id, recovering, _phase = await _campaign_and_actor(
            server, suffix="settled", enter_play=False
        )
        stable = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign_id,
                    "name": "Stable target",
                    "sheet": _dying_sheet(),
                },
                "idempotency_key": "stable-target",
            },
        )
        helper = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign_id,
                    "name": "Conscious helper",
                    "sheet": default_character_sheet(),
                },
                "idempotency_key": "conscious-helper",
            },
        )
        dead_sheet = _dying_sheet()
        dead_sheet["conditions"] = ["dead"]
        dead = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign_id,
                    "name": "Dead target",
                    "sheet": dead_sheet,
                },
                "idempotency_key": "dead-target",
            },
        )
        current = await _campaign(server, campaign_id)
        await _call(
            server,
            "game_phase",
            {
                "campaign_id": campaign_id,
                "action": "set",
                "tool_profile": "play",
                "expected_revision": current["revision"],
                "idempotency_key": "play-settled",
            },
        )
        first = await _death_save(
            server,
            campaign_id,
            {
                "character_id": recovering["id"],
                "action": "death_save",
                "payload": {},
                "expected_revision": recovering["revision"],
                "idempotency_key": "settled-first-save",
            },
        )
        healed = await _call(
            server,
            "character_state_change",
            {
                "character_id": recovering["id"],
                "action": "heal",
                "payload": {"amount": 1},
                "expected_revision": first["character"]["revision"],
                "idempotency_key": "heal-recovering",
            },
        )
        await _advance_round(server, campaign_id, "settled-advance-1")
        before_healed_reject = await _campaign(server, campaign_id)
        with pytest.raises(ToolError, match="only available at 0 hit points"):
            await _death_save(
                server,
                campaign_id,
                {
                    "character_id": recovering["id"],
                    "action": "death_save",
                    "payload": {},
                    "expected_revision": healed["character"]["revision"],
                    "idempotency_key": "healed-no-save",
                },
            )
        assert await _campaign(server, campaign_id) == before_healed_reject

        stabilized = await _call(
            server,
            "character_state_change",
            {
                "character_id": stable["id"],
                "action": "stabilize",
                "payload": {"source_actor_id": helper["id"], "reason": "DM adjudication"},
                "expected_revision": stable["revision"],
                "idempotency_key": "stabilize-target",
            },
        )
        settled_campaign = await _campaign(server, campaign_id)
        for actor_id, revision, message, key in (
            (
                stable["id"],
                stabilized["character"]["revision"],
                "stable actors do not make additional death saves",
                "stable-no-save",
            ),
            (dead["id"], dead["revision"], "dead actors cannot make death saves", "dead-no-save"),
        ):
            with pytest.raises(ToolError, match=message):
                await _death_save(
                    server,
                    campaign_id,
                    {
                        "character_id": actor_id,
                        "action": "death_save",
                        "payload": {},
                        "expected_revision": revision,
                        "idempotency_key": key,
                    },
                )
        assert await _campaign(server, campaign_id) == settled_campaign
        assert settled_campaign["state"]["random_stream"]["position"] == 1

    asyncio.run(exercise())


def test_death_save_cadence_restores_with_snapshot_branch(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign_id, actor, _phase = await _campaign_and_actor(server, suffix="branch")
        first = await _death_save(
            server,
            campaign_id,
            {
                "character_id": actor["id"],
                "action": "death_save",
                "payload": {},
                "expected_revision": actor["revision"],
                "idempotency_key": "branch-save-0",
            },
        )
        branch = next(
            item
            for item in await _call(
                server, "branch_query", {"campaign_id": campaign_id, "view": "list"}
            )
            if item["is_current"]
        )
        current = await _campaign(server, campaign_id)
        checkpoint = await _call(
            server,
            "snapshot_create",
            {
                "campaign_id": campaign_id,
                "label": "After first death save",
                "expected_revision": current["revision"],
                "expected_head_snapshot_id": branch.get("head_snapshot_id") or "",
                "idempotency_key": "death-save-checkpoint",
            },
        )
        await _advance_round(server, campaign_id, "branch-advance")
        second = await _death_save(
            server,
            campaign_id,
            {
                "character_id": actor["id"],
                "action": "death_save",
                "payload": {},
                "expected_revision": first["character"]["revision"],
                "idempotency_key": "branch-save-1",
            },
        )
        before_restore = await _campaign(server, campaign_id)
        await _call(
            server,
            "snapshot_restore",
            {
                "campaign_id": campaign_id,
                "slot": checkpoint["slot"],
                "expected_revision": before_restore["revision"],
                "expected_branch_id": branch["id"],
                "idempotency_key": "restore-death-save-checkpoint",
            },
        )
        restored_campaign = await _campaign(server, campaign_id)
        restored_actor = await _actor(server, actor["id"])
        assert restored_campaign["state"]["game_time"]["elapsed_ticks"] == 0
        assert restored_campaign["state"]["random_stream"]["position"] == 1
        assert restored_actor["sheet"]["combat"]["last_death_save_elapsed_tick"] == 0
        with pytest.raises(ToolError, match="already made a death save.*current game-time tick"):
            await _death_save(
                server,
                campaign_id,
                {
                    "character_id": actor["id"],
                    "action": "death_save",
                    "payload": {},
                    "expected_revision": restored_actor["revision"],
                    "idempotency_key": "restored-same-tick",
                },
            )
        await _advance_round(server, campaign_id, "restored-advance")
        replayed_second = await _death_save(
            server,
            campaign_id,
            {
                "character_id": actor["id"],
                "action": "death_save",
                "payload": {},
                "expected_revision": restored_actor["revision"],
                "idempotency_key": "restored-save-1",
            },
        )
        assert replayed_second["result"]["natural"] == second["result"]["natural"] == 10

    asyncio.run(exercise())


def test_whole_sheet_replace_cannot_remove_authoritative_cadence(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign_id, actor, _phase = await _campaign_and_actor(server, suffix="replace")
        first = await _death_save(
            server,
            campaign_id,
            {
                "character_id": actor["id"],
                "action": "death_save",
                "payload": {},
                "expected_revision": actor["revision"],
                "idempotency_key": "replace-save-0",
            },
        )
        forged_sheet = deepcopy(first["character"]["sheet"])
        forged_sheet["combat"].pop("last_death_save_elapsed_tick")
        replaced = await _call(
            server,
            "character_sheet_replace",
            {
                "character_id": actor["id"],
                "sheet": forged_sheet,
                "expected_revision": first["character"]["revision"],
                "idempotency_key": "remove-cadence-marker",
            },
        )
        assert replaced["sheet"]["combat"]["last_death_save_elapsed_tick"] == 0
        before_reject = await _campaign(server, campaign_id)
        with pytest.raises(ToolError, match="already made a death save.*current game-time tick"):
            await _death_save(
                server,
                campaign_id,
                {
                    "character_id": actor["id"],
                    "action": "death_save",
                    "payload": {},
                    "expected_revision": replaced["revision"],
                    "idempotency_key": "replace-cannot-reroll",
                },
            )
        assert await _campaign(server, campaign_id) == before_reject

    asyncio.run(exercise())


def test_whole_sheet_replace_replays_original_request_after_marker_advances(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign_id, actor, _phase = await _campaign_and_actor(server, suffix="replace-replay")
        first = await _death_save(
            server,
            campaign_id,
            {
                "character_id": actor["id"],
                "action": "death_save",
                "payload": {},
                "expected_revision": actor["revision"],
                "idempotency_key": "replace-replay-save-0",
            },
        )
        original_sheet = deepcopy(first["character"]["sheet"])
        original_sheet["combat"].pop("last_death_save_elapsed_tick")
        replace_arguments = {
            "character_id": actor["id"],
            "sheet": original_sheet,
            "expected_revision": first["character"]["revision"],
            "idempotency_key": "stable-replace-request",
        }
        replaced = await _call(server, "character_sheet_replace", replace_arguments)
        assert replaced["sheet"]["combat"]["last_death_save_elapsed_tick"] == 0

        await _advance_round(server, campaign_id, "replace-replay-advance")
        second = await _death_save(
            server,
            campaign_id,
            {
                "character_id": actor["id"],
                "action": "death_save",
                "payload": {},
                "expected_revision": replaced["revision"],
                "idempotency_key": "replace-replay-save-1",
            },
        )
        assert second["character"]["sheet"]["combat"]["last_death_save_elapsed_tick"] == 1

        replay = await _call(server, "character_sheet_replace", replace_arguments)
        assert replay == replaced
        authoritative = await _actor(server, actor["id"])
        assert authoritative["revision"] == second["character"]["revision"]
        assert authoritative["sheet"]["combat"]["last_death_save_elapsed_tick"] == 1

    asyncio.run(exercise())


def test_stale_outer_random_snapshot_rejects_before_draw_and_retries_from_new_position(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign_id, actor, _phase = await _campaign_and_actor(
            server, suffix="random-race", enter_play=False
        )
        other = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign_id,
                    "name": "Other dying hero",
                    "sheet": _dying_sheet(),
                },
                "idempotency_key": "random-race-other",
            },
        )
        current = await _campaign(server, campaign_id)
        await _call(
            server,
            "game_phase",
            {
                "campaign_id": campaign_id,
                "action": "set",
                "tool_profile": "play",
                "expected_revision": current["revision"],
                "idempotency_key": "random-race-play",
            },
        )

        outer_snapshot = await _campaign(server, campaign_id)
        stale_stream = CampaignRandomStream.from_campaign_state(
            campaign_id,
            outer_snapshot["state"],
            operation="character_state_change",
            idempotency_key="stale-outer-save",
            campaign_revision=outer_snapshot["revision"],
        )
        other_save = await _death_save(
            server,
            campaign_id,
            {
                "character_id": other["id"],
                "action": "death_save",
                "payload": {},
                "expected_revision": other["revision"],
                "idempotency_key": "interleaved-other-save",
            },
        )
        assert other_save["result"]["natural"] == 14
        after_other = await _campaign(server, campaign_id)
        assert after_other["state"]["random_stream"]["position"] == 1

        stale_arguments = {
            "character_id": actor["id"],
            "action": "death_save",
            "payload": {},
            "expected_revision": actor["revision"],
            "idempotency_key": "stale-outer-save",
        }
        with use_random_stream(stale_stream):
            with pytest.raises(ToolError, match="campaign random snapshot conflict"):
                await _call(server, "character_state_change", stale_arguments)
        assert stale_stream.draw_count == 0
        assert await _campaign(server, campaign_id) == after_other
        assert (
            "last_death_save_elapsed_tick"
            not in (await _actor(server, actor["id"]))["sheet"]["combat"]
        )

        retried = await _death_save(server, campaign_id, stale_arguments)
        assert retried["result"]["natural"] == 10
        assert retried["random_stream_receipt"]["position_before"] == 1
        assert retried["random_stream_receipt"]["position_after"] == 2
        assert (await _campaign(server, campaign_id))["state"]["random_stream"]["position"] == 2

    asyncio.run(exercise())


def test_combat_death_save_watermark_blocks_same_tick_after_combat_and_survives_restart(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        campaign_id, actor, _phase = await _campaign_and_actor(server, suffix="combat-watermark")
        current = await _campaign(server, campaign_id)
        started = await _call(
            server,
            "combat_start",
            {
                "campaign_id": campaign_id,
                "positioning_mode": "agent",
                "participant_ids": [actor["id"]],
                "participant_config": [
                    {"actor_id": actor["id"], "initiative": 10, "death_saves": True}
                ],
                "expected_revision": current["revision"],
                "idempotency_key": "combat-watermark-start",
            },
        )
        before_save = await _campaign(server, campaign_id)
        stream = CampaignRandomStream.from_campaign_state(
            campaign_id,
            before_save["state"],
            operation="combat_check",
            idempotency_key="combat-watermark-save",
            campaign_revision=before_save["revision"],
        )
        with use_random_stream(stream):
            combat_save = await _call_raw(
                server,
                "combat_check",
                {
                    "campaign_id": campaign_id,
                    "actor_id": actor["id"],
                    "kind": "death_save",
                    "expected_revision": started["campaign_revision"],
                    "idempotency_key": "combat-watermark-save",
                },
            )
        assert combat_save["result"]["natural"] == 14
        during = await _actor(server, actor["id"])
        assert during["sheet"]["combat"]["last_death_save_elapsed_tick"] == 0

        ended = await _call(
            server,
            "combat_end",
            {
                "campaign_id": campaign_id,
                "outcome": {"status": "withdrawal", "summary": "The threat withdraws."},
                "expected_revision": combat_save["campaign_revision"],
                "idempotency_key": "combat-watermark-end",
            },
        )
        assert ended["combat"]["active"] is False

        restarted = create_server(config)
        restored = await _actor(restarted, actor["id"])
        assert restored["sheet"]["combat"]["last_death_save_elapsed_tick"] == 0
        same_tick_campaign = await _campaign(restarted, campaign_id)
        with pytest.raises(ToolError, match="already made a death save.*current game-time tick"):
            await _death_save(
                restarted,
                campaign_id,
                {
                    "character_id": actor["id"],
                    "action": "death_save",
                    "payload": {},
                    "expected_revision": restored["revision"],
                    "idempotency_key": "combat-watermark-same-tick",
                },
            )
        assert await _campaign(restarted, campaign_id) == same_tick_campaign

        await _advance_round(restarted, campaign_id, "combat-watermark-advance")
        next_tick = await _death_save(
            restarted,
            campaign_id,
            {
                "character_id": actor["id"],
                "action": "death_save",
                "payload": {},
                "expected_revision": restored["revision"],
                "idempotency_key": "combat-watermark-next-tick",
            },
        )
        assert next_tick["result"]["natural"] == 10
        assert next_tick["result"]["cadence"]["elapsed_tick"] == 1
        assert next_tick["character"]["sheet"]["combat"]["last_death_save_elapsed_tick"] == 1

    asyncio.run(exercise())


def test_concurrent_clock_advance_fails_campaign_cas_without_consuming_random(
    tmp_path: Path,
    monkeypatch,
) -> None:
    original_replace = server_module.StateMutationService.replace
    raced = False

    def replace_after_clock_advance(service, campaign_id: str, **kwargs):
        nonlocal raced
        if kwargs.get("operation") == "character.death_save.resolve" and not raced:
            raced = True
            campaign_service = CampaignService(service.database)
            current = campaign_service.get(campaign_id)
            state = deepcopy(dict(current.state or {}))
            transition = advance_game_time(
                state["game_time"],
                world_time=state.get("world_time"),
                period="round",
            )
            state["game_time"] = transition["after"]
            if transition["world_time_after"] is not None:
                state["world_time"] = transition["world_time_after"]
            campaign_service.update(
                campaign_id,
                state=validate_party_state(state),
                expected_revision=current.revision,
            )
        return original_replace(service, campaign_id, **kwargs)

    monkeypatch.setattr(
        server_module.StateMutationService,
        "replace",
        replace_after_clock_advance,
    )

    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign_id, actor, _phase = await _campaign_and_actor(server, suffix="campaign-cas")
        arguments = {
            "character_id": actor["id"],
            "action": "death_save",
            "payload": {},
            "expected_revision": actor["revision"],
            "idempotency_key": "raced-death-save",
        }
        before = await _campaign(server, campaign_id)
        with pytest.raises(ToolError, match="campaign revision conflict"):
            await _death_save(server, campaign_id, arguments)

        after = await _campaign(server, campaign_id)
        unchanged_actor = await _actor(server, actor["id"])
        assert after["revision"] == before["revision"] + 1
        assert after["state"]["game_time"]["elapsed_ticks"] == 1
        assert after["state"]["random_stream"] == before["state"]["random_stream"]
        assert unchanged_actor["revision"] == actor["revision"]
        assert "last_death_save_elapsed_tick" not in unchanged_actor["sheet"]["combat"]

        committed = await _death_save(server, campaign_id, arguments)
        assert committed["result"]["natural"] == 14
        assert committed["result"]["cadence"]["elapsed_tick"] == 1
        assert (await _campaign(server, campaign_id))["state"]["random_stream"]["position"] == 1

    asyncio.run(exercise())


def test_2024_noncombat_death_save_contract_remains_separate(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign_id, actor, _phase = await _campaign_and_actor(
            server, edition="2024", suffix="2024"
        )
        first = await _death_save(
            server,
            campaign_id,
            {
                "character_id": actor["id"],
                "action": "death_save",
                "payload": {},
                "expected_revision": actor["revision"],
                "idempotency_key": "2024-save-1",
            },
        )
        second = await _death_save(
            server,
            campaign_id,
            {
                "character_id": actor["id"],
                "action": "death_save",
                "payload": {},
                "expected_revision": first["character"]["revision"],
                "idempotency_key": "2024-save-2",
            },
        )
        assert "cadence" not in first["result"]
        assert "last_death_save_elapsed_tick" not in second["character"]["sheet"]["combat"]
        assert (await _campaign(server, campaign_id))["state"]["random_stream"]["position"] == 2

    asyncio.run(exercise())


def test_2014_cadence_boundary_maps_to_exact_bundled_source() -> None:
    pack = get_core_rule_pack("2014")
    boundary = next(
        item for item in pack.boundaries if item.id == "dnd5e.core.mcp.death_save_turn_cadence"
    )
    assert boundary.editions == ("2014",)
    assert boundary.citation == (
        "bundled:srd2014/06_Gameplay/Order_of_Combat.md#death-saving-throws"
    )
    assert "Whenever you start your turn with 0 hit points" in (
        Path(__file__).resolve().parents[3]
        / "skills/full/skills/dnd-dm/srd/references-2014-en/06_Gameplay/Order_of_Combat.md"
    ).read_text(encoding="utf-8")
