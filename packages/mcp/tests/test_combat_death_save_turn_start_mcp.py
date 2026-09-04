import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.random_stream import CampaignRandomStream, use_random_stream
from test_ground_items_mcp import _raw, _snapshot
from test_official_expansions_mcp import _call, _config

from sagasmith_dnd_mcp.server import close_server, create_server


@pytest.mark.parametrize("initial_state", ["healthy", "stable", "healed"])
def test_mid_turn_damage_waits_for_next_start_and_replay_is_exact(tmp_path, initial_state):
    async def exercise():
        config = replace(
            _config(tmp_path),
            auto_seed_rules=False,
            dnd_skills_dir=Path(__file__).resolve().parents[3] / "skills",
        )
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Death save start boundary",
                    "edition": "2014",
                    "random_seed": "death-save-start",
                    "idempotency_key": "campaign",
                },
            )
            sheet = default_character_sheet()
            sheet["combat"]["hp"] = {
                "value": 10 if initial_state == "healthy" else 0,
                "max": 10,
                "temp": 0,
            }
            if initial_state == "stable":
                sheet["conditions"] = ["unconscious", "stable"]
            elif initial_state == "healed":
                sheet["conditions"] = ["unconscious"]
            actors = []
            for name, actor_sheet in [("Falling", sheet), ("Other", default_character_sheet())]:
                actors.append(
                    await _call(
                        server,
                        "character_create_from",
                        {
                            "mode": "direct",
                            "payload": {
                                "campaign_id": campaign["id"],
                                "name": name,
                                "sheet": actor_sheet,
                            },
                            "idempotency_key": name,
                        },
                    )
                )
            actor_ids = [actor["id"] for actor in actors]
            before = await _snapshot(server, campaign["id"], actor_ids)
            await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "agent",
                    "participant_ids": actor_ids,
                    "participant_config": [
                        {"actor_id": actor_ids[0], "initiative": 20, "death_saves": True},
                        {"actor_id": actor_ids[1], "initiative": 10},
                    ],
                    "expected_revision": before[0]["revision"],
                    "idempotency_key": "start",
                },
            )
            started = await _snapshot(server, campaign["id"], actor_ids)
            assert started[0]["state"]["combat"]["combatants"][0]["turn_flags"][
                "death_save_due"
            ] is (initial_state == "healed")
            if initial_state == "healed":
                await _call(
                    server,
                    "combat_hp_change",
                    {
                        "campaign_id": campaign["id"],
                        "target_id": actor_ids[0],
                        "action": "heal",
                        "payload": {"amount": 1},
                        "expected_revision": started[0]["revision"],
                        "idempotency_key": "heal-before-save",
                    },
                )
                started = await _snapshot(server, campaign["id"], actor_ids)
                assert (
                    started[0]["state"]["combat"]["combatants"][0]["turn_flags"]["death_save_due"]
                    is False
                )
            await _call(
                server,
                "combat_hp_change",
                {
                    "campaign_id": campaign["id"],
                    "target_id": actor_ids[0],
                    "action": "damage",
                    "payload": {
                        "parts": [
                            {"amount": 10 if initial_state == "healthy" else 1, "type": "force"}
                        ]
                    },
                    "expected_revision": started[0]["revision"],
                    "idempotency_key": "mid-turn-damage",
                },
            )
            fallen = await _snapshot(server, campaign["id"], actor_ids)
            assert fallen[1][0]["sheet"]["combat"]["hp"]["value"] == 0
            assert fallen[1][0]["sheet"]["combat"]["death_saves"] == {
                "successes": 0,
                "failures": 1 if initial_state == "stable" else 0,
            }
            close_server(server)
            server = create_server(config)
            assert await _snapshot(server, campaign["id"], actor_ids) == fallen
            available = await _call(
                server,
                "combat_query",
                {
                    "campaign_id": campaign["id"],
                    "view": "available_actions",
                    "actor_id": actor_ids[0],
                },
            )
            assert available["actions"] == []
            premature = {
                "campaign_id": campaign["id"],
                "actor_id": actor_ids[0],
                "kind": "death_save",
                "expected_revision": fallen[0]["revision"],
                "idempotency_key": "premature-save",
            }
            with pytest.raises(ToolError, match="no death save is due"):
                await _raw(server, "combat_check", premature)
            assert await _snapshot(server, campaign["id"], actor_ids) == fallen
            end_request = {
                "campaign_id": campaign["id"],
                "actor_id": actor_ids[0],
                "expected_revision": fallen[0]["revision"],
                "idempotency_key": "end-falling",
            }
            ended = await _raw(server, "combat_end_turn", end_request)
            after_end = await _snapshot(server, campaign["id"], actor_ids)
            assert after_end[1][0] == fallen[1][0]
            assert await _raw(server, "combat_end_turn", end_request) == ended
            await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": actor_ids[1],
                    "expected_revision": after_end[0]["revision"],
                    "idempotency_key": "end-other",
                },
            )
            due = await _snapshot(server, campaign["id"], actor_ids)
            available = await _call(
                server,
                "combat_query",
                {
                    "campaign_id": campaign["id"],
                    "view": "available_actions",
                    "actor_id": actor_ids[0],
                },
            )
            assert available["actions"] == ["death_save"]
            with pytest.raises(ToolError, match="required death save"):
                await _raw(
                    server,
                    "combat_end_turn",
                    {
                        **end_request,
                        "expected_revision": due[0]["revision"],
                        "idempotency_key": "skip-due",
                    },
                )
            assert await _snapshot(server, campaign["id"], actor_ids) == due
            save_request = {
                **premature,
                "expected_revision": due[0]["revision"],
                "idempotency_key": "due-save",
            }
            expected_stream = CampaignRandomStream.from_campaign_state(
                campaign["id"],
                due[0]["state"],
                operation="combat_check",
                campaign_revision=due[0]["revision"],
            )
            expected_natural = expected_stream.randint(1, 20)
            if initial_state == "healthy":
                for mismatch in ("campaign", "revision", "seed", "position"):
                    stale_state = deepcopy(due[0]["state"])
                    if mismatch == "seed":
                        stale_state["random_stream"]["seed"] = "f" * 64
                    if mismatch == "position":
                        stale_state["random_stream"]["position"] += 1
                    stale_stream = CampaignRandomStream.from_campaign_state(
                        "wrong-campaign" if mismatch == "campaign" else campaign["id"],
                        stale_state,
                        operation="combat_check",
                        campaign_revision=due[0]["revision"] - (mismatch == "revision"),
                    )
                    with (
                        use_random_stream(stale_stream),
                        pytest.raises(ToolError, match="current campaign random snapshot"),
                    ):
                        await _raw(server, "combat_check", save_request)
                    assert stale_stream.draw_count == 0
                    assert await _snapshot(server, campaign["id"], actor_ids) == due
            saved = await _raw(server, "combat_check", save_request)
            after_save = await _snapshot(server, campaign["id"], actor_ids)
            receipt = saved["random_stream_receipt"]
            assert receipt["position_after"] == receipt["position_before"] + 1
            assert after_save[0]["state"]["random_stream"]["position"] == receipt["position_after"]
            assert after_save[0]["state"]["random_stream"]["last_receipt"] == receipt
            assert (
                after_save[0]["state"]["combat"]["combatants"][0]["turn_flags"]["death_save_used"]
                is True
            )
            assert saved["result"]["kind"] == "death_save"
            assert saved["result"]["natural"] == expected_natural
            duplicate = deepcopy(save_request)
            duplicate.update(
                expected_revision=after_save[0]["revision"], idempotency_key="duplicate-save"
            )
            with pytest.raises(ToolError, match="already made a death save"):
                await _raw(server, "combat_check", duplicate)
            assert await _snapshot(server, campaign["id"], actor_ids) == after_save
            close_server(server)
            server = create_server(config)
            assert await _raw(server, "combat_check", save_request) == saved
            with use_random_stream(expected_stream):
                assert await _raw(server, "combat_check", save_request) == saved
            assert expected_stream.draw_count == 1
            assert await _raw(server, "combat_end_turn", end_request) == ended
            assert await _snapshot(server, campaign["id"], actor_ids) == after_save
        finally:
            close_server(server)

    asyncio.run(exercise())
