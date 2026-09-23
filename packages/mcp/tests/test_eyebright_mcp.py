from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server

DISEASE_SOURCE = "bundled:srd2014/08_Gamemastering/Diseases.md"
OINTMENT_SOURCE_KEY = "dnd5e.srd2014.disease.sight_rot.eyebright_ointment"


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


def test_eyebright_crafting_application_and_replay_are_one_authoritative_lifecycle(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Eyebright lifecycle",
                    "edition": "2014",
                    "random_seed": "eyebright-authority",
                    "idempotency_key": "eyebright-campaign",
                },
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["progression"]["species"] = "human"
            sheet["abilities"]["constitution"]["score"] = 1
            sheet["traits"]["proficiencies"]["tools"] = ["herbalism kit"]
            for index in range(3):
                sheet["inventory"]["items"].append(
                    {
                        "id": f"eyebright-flower-{index}",
                        "name": "Eyebright flower",
                        "kind": "equipment",
                        "quantity": 1,
                    }
                )
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Herbalist", "sheet": sheet},
                    "idempotency_key": "eyebright-herbalist",
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
                        "disease_id": "sight_rot",
                        "exposure_kind": "tainted_water",
                        "exposure_source_id": "scene:eyebright-sight-rot-water",
                        "exposure_source_ref": DISEASE_SOURCE,
                        "expected_revision": current_campaign["revision"],
                        "expected_actor_revision": current_actor["revision"],
                        "idempotency_key": f"eyebright-exposure-{attempt}",
                    },
                )
                if exposure["status"] == "infected":
                    break
            assert exposure is not None and exposure["status"] == "infected"
            disease_effect_id = exposure["disease_effect"]["id"]

            craft_request = None
            first_craft = None
            for index in range(3):
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
                craft_request = {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "flower_item_id": f"eyebright-flower-{index}",
                    "expected_revision": current_campaign["revision"],
                    "expected_actor_revision": current_actor["revision"],
                    "expected_elapsed_ticks": current_campaign["state"]["game_time"][
                        "elapsed_ticks"
                    ]
                    + 600,
                    "idempotency_key": f"eyebright-craft-{index}",
                }
                crafted = await _response(
                    server, "character_disease_eyebright_craft", craft_request
                )
                assert crafted["status"] == "committed"
                assert crafted["eyebright_crafting"]["doses_created"] == 1
                assert crafted["game_time"]["elapsed_ticks"] == craft_request[
                    "expected_elapsed_ticks"
                ]
                assert crafted["rule_receipts"]
                if index == 0:
                    first_craft = crafted
                    stale_actor_revision = current_actor["revision"]
                    current_after_craft = await _call(
                        server,
                        "campaign_query",
                        {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                    )
                    actor_after_craft = await _call(
                        server,
                        "character_query",
                        {"view": "get", "payload": {"character_id": actor["id"]}},
                    )
                    before_random_stream = deepcopy(
                        current_after_craft["state"].get("random_stream")
                    )
                    stale_request = {
                        **craft_request,
                        "flower_item_id": "eyebright-flower-1",
                        "expected_revision": current_after_craft["revision"],
                        "expected_actor_revision": stale_actor_revision,
                        "expected_elapsed_ticks": current_after_craft["state"]["game_time"][
                            "elapsed_ticks"
                        ]
                        + 600,
                        "idempotency_key": "eyebright-stale-craft",
                    }
                    try:
                        await _response(
                            server, "character_disease_eyebright_craft", stale_request
                        )
                    except Exception as error:
                        assert "revision conflict" in str(error).casefold()
                    else:
                        raise AssertionError("stale crafter revision was accepted")
                    after_stale_campaign = await _call(
                        server,
                        "campaign_query",
                        {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                    )
                    after_stale_actor = await _call(
                        server,
                        "character_query",
                        {"view": "get", "payload": {"character_id": actor["id"]}},
                    )
                    assert after_stale_campaign["revision"] == current_after_craft["revision"]
                    assert (
                        after_stale_campaign["state"].get("random_stream")
                        == before_random_stream
                    )
                    assert after_stale_actor["revision"] == actor_after_craft["revision"]

                    close_server(server)
                    server = create_server(_config(tmp_path))
                    assert (
                        await _response(
                            server, "character_disease_eyebright_craft", craft_request
                        )
                        == first_craft
                    )

            actor_after_craft = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            doses = [
                item
                for item in actor_after_craft["sheet"]["inventory"]["items"]
                if item.get("source_key") == OINTMENT_SOURCE_KEY
            ]
            assert len(doses) == 3
            assert all(
                item["name"] == "Eyebright ointment" and item["quantity"] == 1
                for item in doses
            )
            assert not any(
                item["name"] == "Eyebright flower"
                for item in actor_after_craft["sheet"]["inventory"]["items"]
            )

            apply_request = None
            first_application = None
            for index, dose in enumerate(doses):
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
                apply_request = {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "disease_effect_id": disease_effect_id,
                    "ointment_item_id": dose["id"],
                    "expected_revision": current_campaign["revision"],
                    "expected_actor_revision": current_actor["revision"],
                    "idempotency_key": f"eyebright-apply-{index}",
                }
                applied = await _response(
                    server, "character_disease_eyebright_apply", apply_request
                )
                assert applied["status"] == "committed"
                assert applied["result"]["disease_state"]["ointment_doses_applied"] == index + 1
                assert applied["rule_receipts"]
                if index == 0:
                    first_application = applied
                    close_server(server)
                    server = create_server(_config(tmp_path))
                    assert (
                        await _response(
                            server, "character_disease_eyebright_apply", apply_request
                        )
                        == first_application
                    )

            cured_actor = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            cured_effect = next(
                item
                for item in cured_actor["sheet"]["effects"]
                if item["id"] == disease_effect_id
            )
            assert cured_effect["active"] is False
            assert cured_effect["metadata"]["disease_state"]["active"] is False
            assert cured_effect["metadata"]["disease_state"]["ointment_doses_applied"] == 3
            assert not any(
                item["source_key"] == OINTMENT_SOURCE_KEY
                for item in cured_actor["sheet"]["inventory"]["items"]
            )
        finally:
            close_server(server)

    asyncio.run(exercise())
