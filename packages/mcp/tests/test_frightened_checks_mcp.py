from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.random_stream import CampaignRandomStream, use_random_stream
from sagasmith_dnd.statblocks import parse_2014_statblock
from test_official_expansions_mcp import _call, _config

from sagasmith_dnd_mcp.server import close_server, create_server


async def raw(server, name: str, arguments: dict) -> dict:
    _, result = await server.call_tool(name, arguments)
    return result


@pytest.mark.parametrize(("check_action", "visibility", "advantage"), [
    *[(action, visibility, False)
      for action in ("search", "stabilize", "legendary")
      for visibility in ("visible", "unseen", "missing")],
    ("search", "visible", True),
])
def test_frightened_checks_use_recorded_sources_and_atomic_receipts(
    tmp_path: Path, visibility: str, check_action: str, advantage: bool
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        try:
            campaign = await _call(server, "campaign_create", {
                "name": "Frightened checks", "edition": "2014", "idempotency_key": "create",
            })
            campaign_id = campaign["id"]

            async def snapshot() -> dict:
                return await _call(server, "campaign_query", {
                    "view": "get", "payload": {"campaign_id": campaign_id},
                })

            source_sheet = default_character_sheet()
            source_sheet["edition"] = "2014"
            source_sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
            source = await _call(server, "character_create_from", {
                "mode": "direct", "payload": {
                    "campaign_id": campaign_id, "name": "Fear source", "sheet": source_sheet,
                }, "idempotency_key": "source",
            })
            actor_sheet = default_character_sheet()
            legendary_activity_id = None
            if check_action == "legendary":
                source_path = (
                    Path(__file__).resolve().parents[3] / "skills/full/skills/dnd-dm/srd"
                    / "references-2014-en/10_Monsters/Monsters_Each/Aboleth.md"
                )
                actor_sheet = parse_2014_statblock(
                    source_path.read_text(encoding="utf-8"), source_key="srd2014:aboleth",
                ).sheet
                legendary_activity_id = next(
                    item["id"] for item in actor_sheet["content"]["activities"]
                    if item["name"] == "Detect"
                )
            actor_sheet["edition"] = "2014"
            actor_sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
            actor_sheet["conditions"] = ["frightened"]
            actor_sheet["effects"] = [{
                "id": "source-bound-fear", "name": "Fear", "kind": "timed_conditions",
                "source": source["id"], "active": True,
                "duration": {"period": "source_turn_start", "remaining": 1},
                "changes": [{"path": "conditions", "mode": "add", "value": "frightened"}],
            }]
            actor = await _call(server, "character_create_from", {
                "mode": "direct", "payload": {
                    "campaign_id": campaign_id, "name": "Frightened", "sheet": actor_sheet,
                }, "idempotency_key": "actor",
            })
            patient = None
            witness = None
            if check_action == "stabilize":
                patient_sheet = default_character_sheet()
                patient_sheet["edition"] = "2014"
                patient_sheet["combat"]["hp"] = {"value": 0, "max": 10, "temp": 0}
                patient_sheet["conditions"] = ["unconscious", "prone"]
                patient = await _call(server, "character_create_from", {
                    "mode": "direct", "payload": {
                        "campaign_id": campaign_id, "name": "Patient", "sheet": patient_sheet,
                    }, "idempotency_key": "patient",
                })
            if check_action == "legendary":
                witness = await _call(server, "character_create_from", {
                    "mode": "direct", "payload": {
                        "campaign_id": campaign_id, "name": "Other turn", "sheet": source_sheet,
                    }, "idempotency_key": "witness",
                })
            current = await snapshot()
            phase = await _call(server, "game_phase", {
                "campaign_id": campaign_id, "action": "set", "tool_profile": "play",
                "expected_revision": current["revision"], "idempotency_key": "play",
            })
            participants = [{"actor_id": actor["id"], "initiative": 20, "tie_breaker": 0}]
            if visibility != "missing":
                participants.append({
                    "actor_id": source["id"], "initiative": 10, "tie_breaker": 1,
                    "hidden": visibility == "unseen",
                    "visible_to_actor_ids": [] if visibility == "unseen" else None,
                })
            if witness is not None:
                participants.append({
                    "actor_id": witness["id"], "initiative": 30, "tie_breaker": 2,
                })
            spatial_config = {"positioning_mode": "agent"}
            if patient is not None:
                participants[0]["position"] = {"x": 0, "y": 0}
                if visibility != "missing":
                    participants[1]["position"] = {"x": 0, "y": 2}
                participants.append({
                    "actor_id": patient["id"], "initiative": 5, "tie_breaker": 2,
                    "position": {"x": 1, "y": 0},
                })
                spatial_config = {
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 12, "height_cells": 12},
                }
            await raw(server, "combat_start", {
                **spatial_config, "campaign_id": campaign_id,
                "participant_ids": [entry["actor_id"] for entry in participants],
                "participant_config": participants,
                "expected_revision": phase["campaign_revision"], "idempotency_key": "start",
            })
            before = await snapshot()
            tool = "combat_check"
            arguments = {
                "campaign_id": campaign_id, "actor_id": actor["id"], "kind": "check",
                "ability": "perception", "action": "search", "dc": 12,
                "advantage": advantage,
                "rule_facts": {"frightened_source_visibility": False},
                "expected_revision": before["revision"], "idempotency_key": "fear-search",
            }
            if patient is not None:
                arguments.update(kind="stabilize", ability="wisdom", target_id=patient["id"])
                arguments.pop("action")
                arguments.pop("dc")
            if legendary_activity_id is not None:
                tool = "combat_use_activity"
                arguments = {
                    "campaign_id": campaign_id, "actor_id": actor["id"],
                    "activity_id": legendary_activity_id,
                    "expected_revision": before["revision"], "idempotency_key": "fear-search",
                }
            with pytest.raises(ToolError, match="revision conflict"):
                await raw(server, tool, {
                    **arguments, "expected_revision": before["revision"] - 1,
                })
            assert await snapshot() == before
            stream = CampaignRandomStream.from_campaign_state(
                campaign_id, before["state"], operation=tool,
                idempotency_key="fear-search", campaign_revision=before["revision"],
            )
            with use_random_stream(stream):
                settled = await raw(server, tool, arguments)
            if visibility == "missing":
                assert settled["status"] == "pending_ruling"
                assert settled["committed"] is False
                assert "frightened_source_visibility" in settled["missing"]
                assert stream.draw_count == 0
                assert await snapshot() == before
            else:
                expected_draws = 2 if visibility == "visible" and not advantage else 1
                assert settled["status"] == "committed"
                check = settled["result"]
                if check_action == "legendary":
                    check = check["core_effect"]["check"]
                assert len(check["rolls"]) == expected_draws
                assert settled["random_stream_receipt"]["draw_count"] == expected_draws
                assert "dnd5e.core.check.frightened" in {
                    receipt["mechanic_id"] for receipt in check["rule_receipts"]
                }
                after = await snapshot()
                assert after["revision"] == before["revision"] + 1
                acting = next(item for item in after["state"]["combat"]["combatants"]
                              if item["actor_id"] == actor["id"])
                if check_action == "legendary":
                    assert acting["legendary_actions"]["remaining"] == 2
                else:
                    assert acting["turn_budget"]["main_action"] == 0
                if check_action != "stabilize":
                    assert all(item.get("position") is None
                               for item in after["state"]["combat"]["combatants"])
                assert await raw(server, tool, arguments) == settled
                assert await snapshot() == after
            final_snapshot = await snapshot()
            close_server(server)
            server = create_server(config)
            assert await snapshot() == final_snapshot
            repeated = await raw(server, tool, arguments)
            assert repeated == settled
            assert await snapshot() == final_snapshot
        finally:
            close_server(server)

    asyncio.run(exercise())


@pytest.mark.parametrize("mode", ["grid", "agent"])
def test_missing_source_check_resumes_after_authoritative_source_entry(
    tmp_path: Path, mode: str
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        try:
            campaign = await _call(server, "campaign_create", {
                "name": "Fear source enters", "edition": "2014", "idempotency_key": "campaign",
            })
            campaign_id = campaign["id"]

            async def snapshot() -> dict:
                return await _call(server, "campaign_query", {
                    "view": "get", "payload": {"campaign_id": campaign_id},
                })

            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
            source = await _call(server, "character_create_from", {
                "mode": "direct", "payload": {
                    "campaign_id": campaign_id, "name": "Source", "sheet": sheet,
                }, "idempotency_key": "source",
            })
            sheet["conditions"] = ["frightened"]
            sheet["effects"] = [{
                "id": "fear", "name": "Fear", "kind": "timed_conditions", "active": True,
                "source": source["id"], "duration": {"period": "round", "remaining": 10},
                "changes": [{"path": "conditions", "mode": "add", "value": "frightened"}],
            }]
            actor = await _call(server, "character_create_from", {
                "mode": "direct", "payload": {
                    "campaign_id": campaign_id, "name": "Frightened", "sheet": sheet,
                }, "idempotency_key": "actor",
            })
            await _call(server, "access_grant", {
                "scope": "campaign", "campaign_id": campaign_id,
                "principal_id": "player:observer", "payload": {"role": "player"},
            })
            current = await snapshot()
            phase = await _call(server, "game_phase", {
                "campaign_id": campaign_id, "action": "set", "tool_profile": "play",
                "expected_revision": current["revision"], "idempotency_key": "play",
            })
            participant = {"actor_id": actor["id"], "initiative": 20}
            spatial = {"positioning_mode": mode}
            if mode == "grid":
                participant["position"] = {"x": 0, "y": 0}
                spatial["battle_map"] = {"width_cells": 6, "height_cells": 6}
            await raw(server, "combat_start", {
                "campaign_id": campaign_id, **spatial, "participant_ids": [actor["id"]],
                "participant_config": [participant],
                "expected_revision": phase["campaign_revision"],
                "idempotency_key": "start",
            })
            before = await snapshot()
            check_args = {
                "campaign_id": campaign_id, "actor_id": actor["id"], "kind": "check",
                "ability": "perception", "dc": 10, "action": "search",
                "expected_revision": before["revision"], "idempotency_key": "search-once",
            }
            pending = await raw(server, "combat_check", check_args)
            assert pending["status"] == "pending_ruling"
            assert pending["committed"] is False
            assert await snapshot() == before
            joining = {
                "initiative": 10, "hidden": mode == "grid",
                "visible_to_actor_ids": [] if mode == "grid" else [actor["id"]],
            }
            if mode == "grid":
                joining["position"] = {"x": 2, "y": 0}
            await raw(server, "combat_join", {
                "campaign_id": campaign_id, "actor_id": source["id"],
                "participant_config": joining, "expected_revision": before["revision"],
                "idempotency_key": "source-joins",
            })
            queued = await snapshot()
            # A queued future source is not a present source for an ability check.
            repeated_pending = await raw(server, "combat_check", {
                **check_args, "expected_revision": queued["revision"],
            })
            assert repeated_pending["status"] == "pending_ruling"
            assert await snapshot() == queued
            await raw(server, "combat_end_turn", {
                "campaign_id": campaign_id, "actor_id": actor["id"],
                "expected_revision": queued["revision"], "idempotency_key": "next-round",
            })
            entered = await snapshot()
            assert any(item["actor_id"] == source["id"]
                       for item in entered["state"]["combat"]["combatants"])
            patch_args = {
                "campaign_id": campaign_id, "patches": [{
                    "key": "combatant_visibility", "value": {
                        "actor_id": source["id"], "hidden": False,
                        "visible_to_actor_ids": [actor["id"]],
                        "reason": "The DM confirms the entering source is now in sight.",
                    },
                }], "expected_revision": entered["revision"], "idempotency_key": "reveal",
            }
            with pytest.raises(ToolError, match="role|cannot access|permission"):
                await raw(server, "combat_map_patch", {
                    **patch_args, "principal_id": "player:observer",
                })
            assert await snapshot() == entered
            with pytest.raises(ToolError, match="revision conflict"):
                await raw(server, "combat_map_patch", {
                    **patch_args, "expected_revision": entered["revision"] - 1,
                })
            assert await snapshot() == entered
            if mode == "grid":
                with pytest.raises(ToolError, match="unique encounter participant"):
                    await raw(server, "combat_map_patch", {
                        **patch_args, "idempotency_key": "wrong-source", "patches": [{
                            "key": "combatant_visibility", "value": {
                                **patch_args["patches"][0]["value"], "actor_id": "outside-roster",
                            },
                        }],
                    })
                assert await snapshot() == entered
                revealed = await raw(server, "combat_map_patch", patch_args)
                now = await snapshot()
                assert now["revision"] == entered["revision"] + 1
                assert await raw(server, "combat_map_patch", patch_args) == revealed
                assert await snapshot() == now
                with pytest.raises(ToolError, match="idempotency|different payload"):
                    await raw(server, "combat_map_patch", {
                        **patch_args, "patches": [{
                            "key": "combatant_visibility", "value": {
                                **patch_args["patches"][0]["value"], "visible_to_actor_ids": [],
                            },
                        }],
                    })
                assert await snapshot() == now
            else:
                # Do not invent a map to use a grid-only update API. The current
                # visible source fact was explicitly supplied in combat_join.
                with pytest.raises(ToolError, match="no temporary battle map"):
                    await raw(server, "combat_map_patch", patch_args)
                now = await snapshot()
                assert now == entered
                assert all(item.get("position") is None
                           for item in now["state"]["combat"]["combatants"])
            with pytest.raises(ToolError, match="revision conflict"):
                await raw(server, "combat_check", check_args)
            assert await snapshot() == now
            resumed_args = {**check_args, "expected_revision": now["revision"]}
            stream = CampaignRandomStream.from_campaign_state(
                campaign_id, now["state"], operation="combat_check", idempotency_key="search-once",
                campaign_revision=now["revision"],
            )
            with use_random_stream(stream):
                settled = await raw(server, "combat_check", resumed_args)
            assert settled["status"] == "committed"
            assert stream.draw_count == 2
            assert len(settled["result"]["rolls"]) == 2
            after = await snapshot()
            assert after["revision"] == now["revision"] + 1
            assert await raw(server, "combat_check", resumed_args) == settled
            assert await snapshot() == after
            close_server(server)
            server = create_server(config)
            assert await snapshot() == after
            assert await raw(server, "combat_check", resumed_args) == settled
            assert await snapshot() == after
        finally:
            close_server(server)

    asyncio.run(exercise())
