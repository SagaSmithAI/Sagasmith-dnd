import asyncio

import sagasmith_dnd_runtime.application_support as runtime_support
from sagasmith_dnd.character_schema import default_character_sheet
from test_combat_transaction_boundaries_mcp import _config
from test_opportunity_sneak_attack_mcp import _call

from sagasmith_dnd_mcp.server import close_server, create_server


async def _create_survival_actor(server, *, with_rations=False):
    campaign = await _call(server, "campaign_create", {
        "name": "Food and water", "edition": "2014", "idempotency_key": "campaign",
    })
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    if with_rations:
        sheet["inventory"]["items"].append({
            "id": "rations",
            "name": "Rations",
            "kind": "equipment",
            "quantity": 2,
            "source_key": (
                "dnd5e.content.srd2014@1.42.0:"
                "dnd5e.content.srd2014.item.rations"
            ),
        })
    actor = await _call(server, "character_create_from", {
        "mode": "direct",
        "payload": {"campaign_id": campaign["id"], "name": "Ranger", "sheet": sheet},
        "idempotency_key": "ranger",
    })
    return campaign, actor


async def _long_rest(server, campaign, actor, *, key, survival_intake):
    return await _call(server, "campaign_change", {
        "campaign_id": campaign["id"],
        "action": "party_rest",
        "payload": {
            "rest_type": "long_rest",
            "duration_minutes": 1440,
            "members": [{
                "character_id": actor["id"],
                "expected_revision": actor["revision"],
                "survival_intake": survival_intake,
            }],
        },
        "expected_revision": campaign["revision"],
        "idempotency_key": key,
    })


def test_daily_short_water_save_is_campaign_random_and_replayable(tmp_path, monkeypatch):
    def failed_constitution_save(*args, **kwargs):
        natural = kwargs["rng"].randint(1, 20)
        return {
            "kind": "save", "ability": "constitution", "dc": 15,
            "natural": natural, "total": natural, "success": False,
        }

    monkeypatch.setattr(runtime_support, "resolve_actor_check", failed_constitution_save)

    async def exercise():
        server = create_server(_config(tmp_path))
        try:
            campaign, actor = await _create_survival_actor(server)
            first = await _long_rest(
                server,
                campaign,
                actor,
                key="day-one",
                survival_intake={"food_lb": 1, "water_gallons": 0},
            )
            actor = await _call(server, "character_query", {
                "view": "get", "payload": {"character_id": actor["id"]},
            })
            campaign = await _call(server, "campaign_query", {
                "view": "get", "payload": {"campaign_id": campaign["id"]},
            })
            assert first["survival_settlements"][0]["exhaustion_added"] == 1
            second = await _long_rest(
                server,
                campaign,
                actor,
                key="day-two",
                survival_intake={"food_lb": 1, "water_gallons": 0.5},
            )
            assert second["survival_settlements"][0]["water_save"]["dc"] == 15
            assert second["survival_settlements"][0]["exhaustion_added"] == 2
            assert second["random_stream_receipt"]["draw_count"] == 1
            close_server(server)
            server = create_server(_config(tmp_path))
            replay = await _long_rest(
                server,
                campaign,
                actor,
                key="day-two",
                survival_intake={"food_lb": 1, "water_gallons": 0.5},
            )
            assert replay == second
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_survival_skipped_day_fails_without_committing(tmp_path):
    async def exercise():
        server = create_server(_config(tmp_path))
        try:
            campaign, actor = await _create_survival_actor(server)
            request = {
                "campaign_id": campaign["id"],
                "action": "party_rest",
                "payload": {
                    "rest_type": "long_rest",
                    "duration_minutes": 2880,
                    "members": [{
                        "character_id": actor["id"],
                        "expected_revision": actor["revision"],
                        "survival_intake": {"food_lb": 1, "water_gallons": 1},
                    }],
                },
                "expected_revision": campaign["revision"],
                "idempotency_key": "skip-survival-day",
            }
            result = await _call(server, "campaign_change", request)
            assert result["status"] == "pending_ruling"
            assert result["committed"] is False
            assert "skipped an unsettled survival day" in result["reason"]
            current_campaign = await _call(server, "campaign_query", {
                "view": "get", "payload": {"campaign_id": campaign["id"]},
            })
            current_actor = await _call(server, "character_query", {
                "view": "get", "payload": {"character_id": actor["id"]},
            })
            assert current_campaign["revision"] == campaign["revision"]
            assert current_actor["revision"] == actor["revision"]
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_ration_inventory_consumption_is_atomic_and_survives_replay(tmp_path):
    async def exercise():
        server = create_server(_config(tmp_path))
        try:
            campaign, actor = await _create_survival_actor(server, with_rations=True)
            request = {
                "campaign_id": campaign["id"],
                "action": "party_rest",
                "payload": {
                    "rest_type": "long_rest",
                    "duration_minutes": 1440,
                    "members": [{
                        "character_id": actor["id"],
                        "expected_revision": actor["revision"],
                        "survival_intake": {
                            "rations": [{"item_id": "rations", "quantity": 1}],
                            "water_gallons": 1,
                        },
                    }],
                },
                "expected_revision": campaign["revision"],
                "idempotency_key": "ration-day",
            }
            settled = await _call(server, "campaign_change", request)
            assert settled["survival_settlements"][0]["fully_fed"] is True
            persisted = await _call(server, "character_query", {
                "view": "get", "payload": {"character_id": actor["id"]},
            })
            ration = next(
                item for item in persisted["sheet"]["inventory"]["items"]
                if item["id"] == "rations"
            )
            assert ration["quantity"] == 1
            assert settled["survival_settlements"][0]["food_lb"] == 1
            assert await _call(server, "campaign_change", request) == settled
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_2014_rest_rejects_boolean_food_claims_and_missing_weather_source(tmp_path):
    async def exercise():
        server = create_server(_config(tmp_path))
        try:
            campaign, actor = await _create_survival_actor(server)
            request = {
                "campaign_id": campaign["id"],
                "action": "party_rest",
                "payload": {
                    "rest_type": "long_rest",
                    "duration_minutes": 480,
                    "members": [{
                        "character_id": actor["id"],
                        "expected_revision": actor["revision"],
                        "food_and_drink": True,
                    }],
                },
                "expected_revision": campaign["revision"],
                "idempotency_key": "boolean-food",
            }
            try:
                await _call(server, "campaign_change", request)
            except Exception as exc:
                assert "survival_intake" in str(exc)
            else:
                raise AssertionError("a boolean food claim was accepted as an outcome")

            request["payload"]["members"][0].pop("food_and_drink")
            request["payload"]["members"][0]["survival_intake"] = {
                "food_lb": 1, "water_gallons": 2, "hot_weather": True,
            }
            try:
                await _call(server, "campaign_change", request)
            except Exception as exc:
                assert "bounded source fact" in str(exc).lower()
            else:
                raise AssertionError("hot weather without a source fact was accepted")
        finally:
            close_server(server)

    asyncio.run(exercise())
