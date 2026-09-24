from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.diseases import DISEASE_SOURCE_REF, infection_state

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server

DISEASE_SOURCE = "bundled:srd2014/08_Gamemastering/Diseases.md"


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


def test_sewer_plague_incubation_long_rest_save_and_idempotent_replay(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(
            McpConfig(
                home=tmp_path / "home",
                database_url=None,
                chroma_url=None,
                chroma_path_override=None,
                dnd_skills_dir=tmp_path / "dnd",
                modulegen_skills_dir=tmp_path / "modulegen",
                auto_seed_rules=False,
            )
        )
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Disease rest integration",
                    "edition": "2014",
                    "random_seed": "sewer-rest-regression",
                    "idempotency_key": "campaign",
                },
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
            sheet["progression"]["species"] = "human"
            sheet["abilities"]["constitution"]["score"] = 1
            sheet["combat"]["hit_dice"] = {
                "fighter:d8": {
                    "label": "Fighter d8",
                    "value": 1,
                    "max": 1,
                    "recovers_on": "long_rest",
                    "source_key": "fighter",
                }
            }
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Sewer plague target",
                        "sheet": sheet,
                    },
                    "idempotency_key": "actor",
                },
            )
            exposure = None
            for attempt in range(20):
                current_campaign = await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
                current_actor = await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actor["id"]}},
                )
                exposure = await _call(
                    server,
                    "character_disease_exposure",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": actor["id"],
                        "disease_id": "sewer_plague",
                        "exposure_kind": "contaminated_filth",
                        "exposure_source_id": "scene:sewer-filth-1",
                        "exposure_source_ref": DISEASE_SOURCE,
                        "expected_revision": current_campaign["revision"],
                        "expected_actor_revision": current_actor["revision"],
                        "idempotency_key": f"exposure-{attempt}",
                    },
                )
                if exposure["status"] == "infected":
                    break
            assert exposure is not None and exposure["status"] == "infected"

            disease_effect_id = exposure["disease_effect"]["id"]
            state_at_infection = dict(
                exposure["disease_effect"]["metadata"]["disease_state"]
            )
            current_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            elapsed_at_infection = int(
                current_campaign["state"]["game_time"]["elapsed_ticks"]
            )
            days_to_symptoms = max(
                1,
                (
                    int(state_at_infection["symptoms_due_elapsed_ticks"])
                    - elapsed_at_infection
                    + 14_399
                )
                // 14_400,
            )
            disease_rest = None
            rest_request = None
            hp_before_symptomatic_rest = None
            for day in range(days_to_symptoms):
                current_campaign = await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
                elapsed = int(current_campaign["state"]["game_time"]["elapsed_ticks"])
                current_actor = await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actor["id"]}},
                )
                if day == days_to_symptoms - 1:
                    await _call(
                        server,
                        "character_state_change",
                        {
                            "character_id": actor["id"],
                            "action": "damage",
                            "payload": {
                                "parts": [{"amount": 2, "damage_type": "force"}]
                            },
                            "expected_revision": current_actor["revision"],
                            "idempotency_key": "sewer-before-symptom-rest-damage",
                        },
                    )
                    current_actor = await _call(
                        server,
                        "character_query",
                        {"view": "get", "payload": {"character_id": actor["id"]}},
                    )
                    hp_before_symptomatic_rest = int(
                        current_actor["sheet"]["combat"]["hp"]["value"]
                    )
                await _call(
                    server,
                    "campaign_change",
                    {
                        "campaign_id": campaign["id"],
                        "action": "clock_advance",
                        "payload": {
                            "period": "hour",
                            "count": 16,
                            "expected_elapsed_ticks": elapsed + 16 * 600,
                        },
                        "expected_revision": current_campaign["revision"],
                        "idempotency_key": f"incubation-{day}",
                    },
                )
                current_campaign = await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
                current_actor = await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actor["id"]}},
                )
                rest_request = {
                    "campaign_id": campaign["id"],
                    "action": "party_rest",
                    "payload": {
                        "rest_type": "long_rest",
                        "duration_minutes": 480,
                        "members": [
                            {
                                "character_id": actor["id"],
                                "expected_revision": current_actor["revision"],
                                "survival_intake": {
                                    "food_lb": 1,
                                    "water_gallons": 1,
                                    "hot_weather": False,
                                },
                            }
                        ],
                    },
                    "expected_revision": current_campaign["revision"],
                    "idempotency_key": f"sewer-long-rest-{day}",
                }
                rested = await _call(server, "campaign_change", rest_request)
                disease_rest_list = (
                    rested.get("recovered", {}).get(actor["id"], {}).get("disease_rest", [])
                )
                if disease_rest_list:
                    disease_rest = disease_rest_list[0]
            assert disease_rest is not None
            symptomatic = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            state = next(
                item["metadata"]["disease_state"]
                for item in symptomatic["sheet"]["effects"]
                if item["id"] == disease_effect_id
            )
            assert state["symptomatic"] is True
            assert hp_before_symptomatic_rest is not None
            assert (
                symptomatic["sheet"]["combat"]["hp"]["value"]
                == hp_before_symptomatic_rest
            )
            assert disease_rest["disease_id"] == "sewer_plague"
            assert disease_rest["save"]["dc"] == 11
            assert any(
                event["kind"] == "long_rest_hp_recovery_blocked"
                for event in disease_rest["events"]
            )
            replay = await _call(server, "campaign_change", rest_request)
            assert replay == rested
            after_replay = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_replay["revision"] == rested["campaign_revision"]

        finally:
            close_server(server)

    asyncio.run(exercise())


def test_cackle_long_rest_can_skip_without_disease_rng_or_failure_and_uses_current_dc(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(
            McpConfig(
                home=tmp_path / "home",
                database_url=None,
                chroma_url=None,
                chroma_path_override=None,
                dnd_skills_dir=tmp_path / "dnd",
                modulegen_skills_dir=tmp_path / "modulegen",
                auto_seed_rules=False,
            )
        )
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Cackle optional recovery save",
                    "edition": "2014",
                    "random_seed": "cackle-optional-recovery",
                    "idempotency_key": "campaign",
                },
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["progression"]["species"] = "human"
            disease_state = infection_state(
                "cackle_fever",
                actor_id="pending",
                elapsed_ticks=0,
                save_succeeded=False,
                incubation_roll=1,
            )
            disease_state.update(
                {
                    "actor_id": "pending",
                    "symptomatic": True,
                    "recovery_dc": 7,
                    "laughter_dc": 9,
                    "failed_recovery_saves": 1,
                }
            )
            sheet["combat"]["exhaustion"] = 1
            sheet["effects"].append(
                {
                    "id": "rest-cackle-disease",
                    "name": "Cackle Fever",
                    "kind": "disease_state",
                    "source": DISEASE_SOURCE_REF,
                    "active": True,
                    "duration": {"period": "manual", "remaining": 0},
                    "changes": [],
                    "metadata": {"disease_state": disease_state},
                }
            )
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Cackle recovery target",
                        "sheet": sheet,
                    },
                    "idempotency_key": "actor",
                },
            )
            current_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            current_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            before_random_stream = current_campaign["state"].get("random_stream")
            skip_request = {
                "campaign_id": campaign["id"],
                "action": "party_rest",
                "payload": {
                    "rest_type": "long_rest",
                    "duration_minutes": 480,
                    "members": [
                        {
                            "character_id": actor["id"],
                            "expected_revision": current_actor["revision"],
                            "survival_intake": {
                                "food_lb": 1,
                                "water_gallons": 1,
                                "hot_weather": False,
                            },
                            "cackle_fever_recovery": "skip",
                        }
                    ],
                },
                "expected_revision": current_campaign["revision"],
                "idempotency_key": "cackle-skip-recovery",
            }
            skipped = await _call(server, "campaign_change", skip_request)
            skipped_rest = skipped["recovered"][actor["id"]]["disease_rest"][0]
            assert skipped_rest["save"] is None
            assert {event["kind"] for event in skipped_rest["events"]} == {
                "recovery_skipped"
            }
            after_skip_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            after_skip_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            after_skip_state = next(
                item["metadata"]["disease_state"]
                for item in after_skip_actor["sheet"]["effects"]
                if item["id"] == "rest-cackle-disease"
            )
            assert after_skip_state["failed_recovery_saves"] == 1
            assert after_skip_state["recovery_dc"] == 7
            assert after_skip_campaign["state"].get("random_stream") == before_random_stream

            second_actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Cackle recovery attempt target",
                        "sheet": deepcopy(sheet),
                    },
                    "idempotency_key": "attempt-actor",
                },
            )
            attempt_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            attempt_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": second_actor["id"]}},
            )
            attempted = await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "party_rest",
                    "payload": {
                        "rest_type": "long_rest",
                        "duration_minutes": 480,
                        "members": [
                            {
                                "character_id": second_actor["id"],
                                "expected_revision": attempt_actor["revision"],
                                "survival_intake": {
                                    "food_lb": 1,
                                    "water_gallons": 1,
                                    "hot_weather": False,
                                },
                                "cackle_fever_recovery": "attempt",
                            }
                        ],
                    },
                    "expected_revision": attempt_campaign["revision"],
                    "idempotency_key": "cackle-attempt-recovery",
                },
            )
            attempted_rest = attempted["recovered"][second_actor["id"]]["disease_rest"][0]
            assert attempted_rest["save"]["dc"] == 7
        finally:
            close_server(server)

    asyncio.run(exercise())
