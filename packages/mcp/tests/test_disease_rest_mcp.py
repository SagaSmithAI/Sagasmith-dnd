from __future__ import annotations

import asyncio
from pathlib import Path

from sagasmith_dnd.character_schema import default_character_sheet

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
            sheet["progression"]["species"] = "human"
            sheet["abilities"]["constitution"]["score"] = 1
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
            disease_rest = None
            rest_request = None
            for day in range(4):
                current_campaign = await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
                elapsed = int(current_campaign["state"]["game_time"]["elapsed_ticks"])
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
            assert symptomatic["sheet"]["combat"]["exhaustion"] >= 1
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
