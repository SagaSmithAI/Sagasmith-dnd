from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.random_stream import CampaignRandomStream, use_random_stream
from test_frightened_checks_mcp import raw
from test_official_expansions_mcp import _call, _config

from sagasmith_dnd_mcp.server import close_server, create_server


@pytest.mark.parametrize("edition", ["2014", "2024"])
@pytest.mark.parametrize("joining", [False, True])
@pytest.mark.parametrize("visibility", ["visible", "unseen", "missing"])
def test_public_start_and_join_derive_frightened_initiative(
    tmp_path: Path, edition: str, joining: bool, visibility: str
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        try:
            campaign = await _call(server, "campaign_create", {
                "name": "Initiative fear", "edition": edition, "idempotency_key": "campaign",
            })
            campaign_id = campaign["id"]

            async def snapshot() -> dict:
                return await _call(server, "campaign_query", {
                    "view": "get", "payload": {"campaign_id": campaign_id},
                })

            async def create_actor(name: str, sheet: dict) -> dict:
                return await _call(server, "character_create_from", {
                    "mode": "direct", "payload": {
                        "campaign_id": campaign_id, "name": name, "sheet": sheet,
                    }, "idempotency_key": name,
                })

            sheet = default_character_sheet()
            sheet["edition"] = edition
            sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
            source = await create_actor("Source", sheet)
            observer = await create_actor("Observer", sheet)
            frightened = deepcopy(sheet)
            frightened["conditions"] = ["frightened"]
            frightened["effects"] = [{
                "id": "fear", "name": "Fear", "kind": "timed_conditions", "active": True,
                "source": source["id"], "duration": {"period": "round", "remaining": 10},
                "changes": [{"path": "conditions", "mode": "add", "value": "frightened"}],
            }]
            actor = await create_actor("Frightened", frightened)
            before_play = await snapshot()
            phase = await _call(server, "game_phase", {
                "campaign_id": campaign_id, "action": "set", "tool_profile": "play",
                "expected_revision": before_play["revision"], "idempotency_key": "play",
            })
            participants = [
                {"actor_id": observer["id"], "initiative": 25, "tie_breaker": 0},
            ]
            if visibility != "missing":
                participants.append({
                    "actor_id": source["id"], "initiative": 24, "tie_breaker": 1,
                    "hidden": visibility == "unseen",
                    "visible_to_actor_ids": [] if visibility == "unseen" else None,
                })
            start_args = {
                "campaign_id": campaign_id, "positioning_mode": "agent",
                "participant_ids": [item["actor_id"] for item in participants],
                "participant_config": participants, "expected_revision": phase["campaign_revision"],
                "idempotency_key": "start",
            }
            if joining:
                await raw(server, "combat_start", start_args)
                tool = "combat_join"
                arguments = {
                    "campaign_id": campaign_id, "actor_id": actor["id"],
                    "participant_config": {"tie_breaker": 2}, "idempotency_key": "join",
                }
            else:
                participants.append({"actor_id": actor["id"], "tie_breaker": 2})
                start_args["participant_ids"].append(actor["id"])
                tool = "combat_start"
                arguments = start_args
            before = await snapshot()
            arguments["expected_revision"] = before["revision"]
            with pytest.raises(ToolError, match="revision conflict"):
                await raw(server, tool, {**arguments, "expected_revision": before["revision"] - 1})
            assert await snapshot() == before
            stream = CampaignRandomStream.from_campaign_state(
                campaign_id, before["state"], operation=tool,
                idempotency_key=arguments["idempotency_key"], campaign_revision=before["revision"],
            )
            with use_random_stream(stream):
                result = await raw(server, tool, arguments)
            if visibility == "missing" and edition == "2014":
                assert result["status"] == "pending_ruling"
                assert result["committed"] is False
                assert "frightened_source_visibility" in result["missing"]
                assert stream.draw_count == 0
                assert await snapshot() == before
            else:
                after = await snapshot()
                combat = after["state"]["combat"]
                roster = combat["reinforcements"] if joining else combat["combatants"]
                rolled = next(item for item in roster if item["actor_id"] == actor["id"])
                dice = 2 if edition == "2014" and visibility == "visible" else 1
                assert len(rolled["initiative_roll"]["rolls"]) == dice
                assert stream.draw_count == dice
                assert after["revision"] == before["revision"] + 1
                assert all(item.get("position") is None for item in roster)
            final = await snapshot()
            assert await raw(server, tool, arguments) == result
            assert await snapshot() == final
            close_server(server)
            server = create_server(config)
            assert await snapshot() == final
            assert await raw(server, tool, arguments) == result
            assert await snapshot() == final
        finally:
            close_server(server)

    asyncio.run(exercise())
