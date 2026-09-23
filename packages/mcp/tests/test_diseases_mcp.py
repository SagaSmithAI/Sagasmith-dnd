from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server

DISEASE_SOURCE = "bundled:srd2014/08_Gamemastering/Diseases.md"
LESSER_RESTORATION_SOURCE = "bundled:srd2014/07_Spells/Spells_Each/Lesser_Restoration.md"


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


async def _call_response(server, name: str, arguments: dict):
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


async def _actor(server, campaign_id: str) -> dict:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["progression"]["species"] = "human"
    sheet["abilities"]["constitution"]["score"] = 1
    return await _call(
        server,
        "character_create_from",
        {
            "mode": "direct",
            "payload": {"campaign_id": campaign_id, "name": "Disease target", "sheet": sheet},
            "idempotency_key": "disease-target",
        },
    )


def test_disease_exposure_is_campaign_random_cas_persisted_and_cure_is_source_bound(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Disease lifecycle",
                    "edition": "2014",
                    "random_seed": "disease-exposure-restart",
                    "idempotency_key": "disease-campaign",
                },
            )
            actor = await _actor(server, campaign["id"])
            result = None
            request = None
            for attempt in range(20):
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
                request = {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "disease_id": "sight_rot",
                    "exposure_kind": "tainted_water",
                    "exposure_source_id": "scene:tainted-water-1",
                    "exposure_source_ref": DISEASE_SOURCE,
                    "expected_revision": current["revision"],
                    "expected_actor_revision": target["revision"],
                    "idempotency_key": f"disease-water-sip-{attempt}",
                }
                response = await _call_response(server, "character_disease_exposure", request)
                result = response["result"]
                if result["status"] == "infected":
                    break
                assert result["status"] == "saved"
            else:
                raise AssertionError("seeded campaign did not produce a failed Sight Rot save")

            infected = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            effect = next(
                item
                for item in infected["sheet"]["effects"]
                if item["id"] == result["disease_effect"]["id"]
            )
            disease_state = effect["metadata"]["disease_state"]
            assert effect["active"] is True
            assert disease_state["source_ref"] == DISEASE_SOURCE
            assert disease_state.get("incubation_roll") is None
            assert disease_state["symptoms_due_elapsed_ticks"] == 14400
            assert result["save"]["dc"] == 15
            assert response["rule_receipts"]

            replacement = deepcopy(infected["sheet"])
            disease_effect_id = result["disease_effect"]["id"]
            replacement["effects"] = [
                item for item in replacement["effects"] if item["id"] != disease_effect_id
            ]
            with pytest.raises(ToolError, match="source-owned disease lifecycle"):
                await _call(
                    server,
                    "character_sheet_replace",
                    {
                        "character_id": actor["id"],
                        "sheet": replacement,
                        "expected_revision": infected["revision"],
                        "idempotency_key": "cannot-replace-disease-state",
                    },
                )

            with pytest.raises(ToolError, match="source-owned disease effects"):
                await _call(
                    server,
                    "character_state_change",
                    {
                        "character_id": actor["id"],
                        "action": "effect_remove",
                        "payload": {"effect_id": result["disease_effect"]["id"]},
                        "expected_revision": infected["revision"],
                        "idempotency_key": "cannot-remove-disease-effect",
                    },
                )
            with pytest.raises(ToolError, match="source-owned disease effects"):
                await _call(
                    server,
                    "character_state_change",
                    {
                        "character_id": actor["id"],
                        "action": "effect_add",
                        "payload": {
                            "effect": {
                                "id": "forged-disease",
                                "name": "Forged disease",
                                "kind": "disease_state",
                                "active": True,
                                "source": "caller:fake",
                            }
                        },
                        "expected_revision": infected["revision"],
                        "idempotency_key": "cannot-add-disease-effect",
                    },
                )

            close_server(server)
            server = create_server(_config(tmp_path))
            assert await _call_response(server, "character_disease_exposure", request) == response
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            infected = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            cure_request = {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "disease_effect_id": effect["id"],
                "cure_source_id": "lesser_restoration",
                "cure_source_ref": LESSER_RESTORATION_SOURCE,
                "expected_revision": current["revision"],
                "expected_actor_revision": infected["revision"],
                "idempotency_key": "cure-sight-rot",
            }
            cured = await _call_response(server, "character_disease_cure", cure_request)
            assert cured["status"] == "committed"
            close_server(server)
            server = create_server(_config(tmp_path))
            assert await _call_response(server, "character_disease_cure", cure_request) == cured
            cured_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            cured_effect = next(
                item for item in cured_actor["sheet"]["effects"] if item["id"] == effect["id"]
            )
            assert cured_effect["active"] is False
            assert cured_effect["metadata"]["disease_state"]["active"] is False
            assert cured_effect["metadata"]["disease_state"]["symptomatic"] is False
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_disease_exposure_rejects_unreviewed_source_before_campaign_or_actor_change(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Disease source guard", "edition": "2014", "idempotency_key": "c"},
            )
            actor = await _actor(server, campaign["id"])
            current = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            before_stream = current["state"].get("random_stream")
            try:
                await _call(
                    server,
                    "character_disease_exposure",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": actor["id"],
                        "disease_id": "sight_rot",
                        "exposure_kind": "tainted_water",
                        "exposure_source_id": "scene:unknown-water",
                        "exposure_source_ref": "caller:made-up-rules",
                        "expected_revision": current["revision"],
                        "expected_actor_revision": actor["revision"],
                        "idempotency_key": "reject-unreviewed-disease-source",
                    },
                )
            except Exception as error:
                assert "source" in str(error).casefold()
            else:
                raise AssertionError("unreviewed disease source was accepted")
            after = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after["revision"] == current["revision"]
            assert after["state"].get("random_stream") == before_stream
        finally:
            close_server(server)

    asyncio.run(exercise())
