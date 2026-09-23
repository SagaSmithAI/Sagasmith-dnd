from __future__ import annotations

import asyncio
from pathlib import Path

from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.diseases import DISEASE_SOURCE_REF, infection_state

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server

DISEASE_SOURCE = "bundled:srd2014/08_Gamemastering/Diseases.md"


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


async def _response(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result


def _config(path: Path) -> McpConfig:
    return McpConfig(
        home=path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=path / "dnd",
        modulegen_skills_dir=path / "modulegen",
        auto_seed_rules=False,
    )


async def _cackle_actor(server, campaign_id: str) -> dict:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["progression"]["species"] = "human"
    sheet["abilities"]["constitution"]["score"] = 1
    sheet["combat"]["hp"]["max"] = 100
    sheet["combat"]["hp"]["value"] = 100
    disease_state = infection_state(
        "cackle_fever",
        actor_id="pending",
        elapsed_ticks=0,
        save_succeeded=False,
        incubation_roll=1,
    )
    disease_state["actor_id"] = "pending"
    sheet["effects"].append(
        {
            "id": "test-cackle-disease",
            "name": "Cackle Fever",
            "kind": "disease_state",
            "source": DISEASE_SOURCE_REF,
            "active": True,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [],
            "metadata": {"disease_state": disease_state},
        }
    )
    sheet["effects"].append(
        {
            "id": "test-unrelated-incapacitation",
            "name": "Independent incapacitation",
            "kind": "timed_conditions",
            "source": "test:independent-condition",
            "active": True,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [{"path": "conditions", "mode": "add", "value": "incapacitated"}],
            "metadata": {},
        }
    )
    return await _call(
        server,
        "character_create_from",
        {
            "mode": "direct",
            "payload": {"campaign_id": campaign_id, "name": "Cackle target", "sheet": sheet},
            "idempotency_key": "cackle-actor",
        },
    )


async def _advance_to_symptoms(server, campaign_id: str, actor_id: str) -> str:
    effect_id = "test-cackle-disease"
    symptoms_due = 600
    campaign = await _call(
        server, "campaign_query", {"view": "get", "payload": {"campaign_id": campaign_id}}
    )
    elapsed = int(campaign["state"]["game_time"]["elapsed_ticks"])
    if symptoms_due > elapsed:
        await _call(
            server,
            "campaign_change",
            {
                "campaign_id": campaign_id,
                "action": "clock_advance",
                "payload": {
                    "period": "hour",
                    "count": (symptoms_due - elapsed + 599) // 600,
                    "expected_elapsed_ticks": symptoms_due,
                },
                "expected_revision": campaign["revision"],
                "idempotency_key": "cackle-incubation-hour-1",
            },
        )
    return effect_id


async def _begin_single_actor_combat(server, campaign_id: str, actor_id: str) -> dict:
    campaign = await _call(
        server, "campaign_query", {"view": "get", "payload": {"campaign_id": campaign_id}}
    )
    return await _call(
        server,
        "combat_start",
        {
            "campaign_id": campaign_id,
            "positioning_mode": "grid",
            "battle_map": {"width_cells": 4, "height_cells": 4},
            "participant_ids": [actor_id],
            "participant_config": [
                {"actor_id": actor_id, "initiative": 20, "position": {"x": 1, "y": 1}}
            ],
            "expected_revision": campaign["revision"],
            "idempotency_key": "cackle-combat-start",
        },
    )


def test_cackle_stress_validates_source_and_replays_after_restart(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Cackle stress source and replay",
                    "edition": "2014",
                    "random_seed": "cackle-stress-source-replay",
                    "idempotency_key": "cackle-campaign",
                },
            )
            actor = await _cackle_actor(server, campaign["id"])
            disease_effect_id = await _advance_to_symptoms(server, campaign["id"], actor["id"])
            started = await _begin_single_actor_combat(server, campaign["id"], actor["id"])
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            target = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            before_rng = current["state"].get("random_stream")
            invalid = {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "trigger": "nightmare",
                "source_event_id": "not-a-logged-nightmare",
                "expected_revision": current["revision"],
                "expected_actor_revision": target["revision"],
                "idempotency_key": "reject-forged-nightmare",
            }
            try:
                await _response(server, "character_disease_stress", invalid)
            except Exception as error:
                assert "nightmare" in str(error).casefold()
            else:
                raise AssertionError("unlogged nightmare stress source was accepted")
            unchanged = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert unchanged["revision"] == current["revision"]
            assert unchanged["state"].get("random_stream") == before_rng

            request = {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "trigger": "entering_combat",
                "expected_revision": started["campaign_revision"],
                "expected_actor_revision": target["revision"],
                "idempotency_key": "cackle-enter-combat-stress",
            }
            response = await _response(server, "character_disease_stress", request)
            result = response["result"]
            assert result["disease_id"] == "cackle_fever"
            assert result["save"]["dc"] == 13
            assert response["random_stream_receipt"]
            assert any(
                "cackle_fever.stress.2014" in str(item) for item in response["rule_receipts"]
            )
            assert result["disease_state"]["resolved_stress_sources"]
            assert response["campaign_revision"] == started["campaign_revision"] + 1
            committed_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            committed_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert committed_campaign["state"]["random_stream"] != before_rng
            assert committed_actor["revision"] == target["revision"] + 1
            if result["events"][0]["kind"] == "psychic_damage":
                assert result["damage"]["damage_type"] == "psychic"
                assert result["damage_roll"]["total"] == result["events"][0]["rolled"]
            close_server(server)
            server = create_server(_config(tmp_path))
            assert await _response(server, "character_disease_stress", request) == response
            after_replay = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_replay["revision"] == response["campaign_revision"]
            persisted = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            disease = next(
                item for item in persisted["sheet"]["effects"] if item["id"] == disease_effect_id
            )
            assert disease["metadata"]["disease_state"]["resolved_stress_sources"]
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_cackle_laughter_end_turn_requires_actor_and_cleans_only_owned_condition(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Cackle end-turn recovery",
                    "edition": "2014",
                    "random_seed": "cackle-end-turn-recovery",
                    "idempotency_key": "cackle-campaign",
                },
            )
            actor = await _cackle_actor(server, campaign["id"])
            disease_effect_id = await _advance_to_symptoms(server, campaign["id"], actor["id"])
            started = await _begin_single_actor_combat(server, campaign["id"], actor["id"])
            current_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            request = {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "trigger": "entering_combat",
                "expected_revision": started["campaign_revision"],
                "expected_actor_revision": current_actor["revision"],
                "idempotency_key": "cackle-start-laughter",
            }
            stress = await _response(server, "character_disease_stress", request)
            assert stress["result"]["events"][0]["kind"] == "psychic_damage"
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            laughing_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            disease = next(
                item
                for item in laughing_actor["sheet"]["effects"]
                if item["id"] == disease_effect_id
            )
            sidecar = next(
                item
                for item in laughing_actor["sheet"]["effects"]
                if item.get("metadata", {}).get("disease_condition_owner") == disease_effect_id
            )
            assert sidecar["active"] is True
            assert disease["active"] is True
            assert "incapacitated" in sidecar["changes"][0]["value"]

            wrong_actor_request = {
                "campaign_id": campaign["id"],
                "actor_id": "not-the-active-turn-actor",
                "expected_revision": current["revision"],
                "expected_actor_revision": 0,
                "idempotency_key": "reject-non-active-laugher",
            }
            try:
                await _response(server, "character_disease_end_turn", wrong_actor_request)
            except Exception as error:
                assert "active turn" in str(error).casefold()
            else:
                raise AssertionError("end-turn recovery was accepted for a non-active actor")

            failed_request = {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "expected_revision": current["revision"],
                "expected_actor_revision": laughing_actor["revision"],
                "idempotency_key": "cackle-end-turn-failed-save",
            }
            failed = await _response(server, "character_disease_end_turn", failed_request)
            assert failed["result"]["save"]["dc"] == 13
            close_server(server)
            server = create_server(_config(tmp_path))
            assert await _response(server, "character_disease_end_turn", failed_request) == failed
            after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            sidecar_after = next(
                item for item in after["sheet"]["effects"] if item["id"] == sidecar["id"]
            )
            assert sidecar_after["active"] is True

            saved = False
            current_result = failed
            for attempt in range(1, 101):
                current = await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
                current_actor = await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actor["id"]}},
                )
                next_request = {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "expected_revision": current["revision"],
                    "expected_actor_revision": current_actor["revision"],
                    "idempotency_key": f"cackle-end-turn-repeat-{attempt}",
                }
                current_result = await _response(server, "character_disease_end_turn", next_request)
                if current_result["result"]["save"]["success"] is True:
                    saved = True
                    break
            assert saved, (
                "seeded campaign did not produce a successful end-turn save within 100 tries"
            )
            cleaned = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            owned_sidecar = next(
                item for item in cleaned["sheet"]["effects"] if item["id"] == sidecar["id"]
            )
            assert owned_sidecar["active"] is False
            disease_after = next(
                item for item in cleaned["sheet"]["effects"] if item["id"] == disease_effect_id
            )
            assert disease_after["active"] is True
            assert disease_after["metadata"]["disease_state"]["laughing"] is False
            unrelated_sidecar = next(
                item
                for item in cleaned["sheet"]["effects"]
                if item["id"] == "test-unrelated-incapacitation"
            )
            assert unrelated_sidecar["active"] is True
        finally:
            close_server(server)

    asyncio.run(exercise())
