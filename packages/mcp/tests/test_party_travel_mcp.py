import asyncio

import pytest
import sagasmith_dnd_runtime.application_support as runtime_support
from mcp.server.mcpserver.exceptions import ToolError
from test_combat_transaction_boundaries_mcp import _config
from test_food_water_survival_mcp import _create_survival_actor
from test_opportunity_sneak_attack_mcp import _call

from sagasmith_dnd_mcp.server import close_server, create_server


def _trip(actor, *, travel_id, pace, distance, end_trip=True):
    return {
        "travel_id": travel_id,
        "pace": pace,
        "distance_miles": distance,
        "route_fact": {
            "decision_id": f"{travel_id}-route",
            "reason": "The party follows the surveyed route.",
            "source_ref": "scene:route/north-road",
            "source_excerpt": "The road runs north through open country.",
        },
        "end_trip": end_trip,
        "participants": [{
            "character_id": actor["id"],
            "expected_revision": actor["revision"],
            "survival_intake": {"food_lb": 1, "water_gallons": 1},
        }],
    }


def test_party_travel_forced_march_is_atomic_and_replayable_after_restart(
    tmp_path, monkeypatch,
):
    def failed_save(actor, **kwargs):
        natural = kwargs["rng"].randint(1, 20)
        return {
            "kind": "save", "ability": "constitution", "dc": kwargs["dc"],
            "natural": natural, "total": natural, "success": False,
        }

    monkeypatch.setattr(runtime_support, "resolve_actor_check", failed_save)

    async def exercise():
        server = create_server(_config(tmp_path))
        try:
            campaign, actor = await _create_survival_actor(server)
            campaign = await _call(server, "campaign_query", {
                "view": "get", "payload": {"campaign_id": campaign["id"]},
            })
            request = {
                "campaign_id": campaign["id"],
                "action": "party_travel",
                "payload": {"trip": _trip(
                    actor, travel_id="forced-march-1", pace="fast", distance=50,
                )},
                "expected_revision": campaign["revision"],
                "idempotency_key": "travel-forced-march",
            }
            result = await _call(server, "campaign_change", request)
            assert result["duration_minutes"] == 780
            assert [item["dc"] for item in result["forced_march_saves"]] == [10, 11, 12, 13, 14]
            assert all(item["exhaustion_added"] == 1 for item in result["forced_march_saves"])
            assert result["random_stream_receipt"]["draw_count"] == 5
            actor_after = await _call(server, "character_query", {
                "view": "get", "payload": {"character_id": actor["id"]},
            })
            assert actor_after["sheet"]["combat"]["exhaustion"] == 5
            state_after = await _call(server, "campaign_query", {
                "view": "get", "payload": {"campaign_id": campaign["id"]},
            })
            assert state_after["state"]["travel"]["ledger"][-1]["duration_minutes"] == 780

            close_server(server)
            server = create_server(_config(tmp_path))
            assert await _call(server, "campaign_change", request) == result
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_party_travel_crosses_day_boundary_with_intake_and_receipts(tmp_path, monkeypatch):
    def successful_save(actor, **kwargs):
        natural = kwargs["rng"].randint(1, 20)
        return {
            "kind": "save", "ability": "constitution", "dc": kwargs["dc"],
            "natural": natural, "total": natural, "success": True,
        }

    monkeypatch.setattr(runtime_support, "resolve_actor_check", successful_save)

    async def exercise():
        server = create_server(_config(tmp_path))
        try:
            campaign, actor = await _create_survival_actor(server)
            campaign = await _call(server, "campaign_query", {
                "view": "get", "payload": {"campaign_id": campaign["id"]},
            })
            first_leg = await _call(server, "campaign_change", {
                "campaign_id": campaign["id"],
                "action": "party_travel",
                "payload": {"trip": _trip(
                    actor, travel_id="midnight-road", pace="normal", distance=69,
                    end_trip=False,
                )},
                "expected_revision": campaign["revision"],
                "idempotency_key": "travel-before-midnight",
            })
            actor = await _call(server, "character_query", {
                "view": "get", "payload": {"character_id": actor["id"]},
            })
            result = await _call(server, "campaign_change", {
                "campaign_id": campaign["id"],
                "action": "party_travel",
                "payload": {"trip": _trip(
                    actor, travel_id="midnight-road", pace="normal", distance=3,
                )},
                "expected_revision": first_leg["campaign_revision"],
                "idempotency_key": "travel-midnight",
            })
            assert result["duration_minutes"] == 60
            assert result["survival_settlements"][0]["fully_fed"] is True
            assert result["survival_rule_receipts"]
            assert result["rule_receipts"]
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_fast_travel_rejects_a_traveling_stealth_check(tmp_path):
    async def exercise():
        server = create_server(_config(tmp_path))
        try:
            campaign, actor = await _create_survival_actor(server)
            campaign = await _call(server, "campaign_query", {
                "view": "get", "payload": {"campaign_id": campaign["id"]},
            })
            phase = await _call(server, "game_phase", {
                "campaign_id": campaign["id"], "action": "set",
                "tool_profile": "play", "expected_revision": campaign["revision"],
                "idempotency_key": "fast-stealth-play-phase",
            })
            travel = await _call(server, "campaign_change", {
                "campaign_id": campaign["id"],
                "action": "party_travel",
                "payload": {"trip": _trip(
                    actor, travel_id="fast-scouting", pace="fast", distance=3,
                    end_trip=False,
                )},
                "expected_revision": phase["campaign_revision"],
                "idempotency_key": "fast-scouting",
            })
            with pytest.raises(ToolError, match="only at slow pace"):
                await _call(server, "character_check", {
                    "campaign_id": campaign["id"],
                    "action": "check",
                    "payload": {
                        "actor_id": actor["id"], "kind": "ability",
                        "ability": "stealth", "dc": 12,
                    },
                    "expected_revision": travel["campaign_revision"],
                    "idempotency_key": "fast-stealth-attempt",
                })
        finally:
            close_server(server)

    asyncio.run(exercise())
