from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.adventuring_gear import ADVENTURING_GEAR_SOURCE_REF
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.combat_engine import death_save_due

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


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


def _kit(item_id: str, *, uses: int | None = None) -> dict:
    item = {
        "id": item_id,
        "name": "Healer's Kit",
        "source_key": "dnd5e.content.srd2014.item.healer-s-kit",
        "quantity": 1,
    }
    if uses is not None:
        item["uses"] = {
            "label": "Healer's Kit uses",
            "value": uses,
            "max": 10,
            "unlimited": False,
            "recovers_on": "none",
            "source_key": item["source_key"],
        }
    return item


async def _snapshot(server, campaign_id: str, actor_ids: list[str]) -> tuple[dict, list[dict]]:
    campaign = await _call(
        server,
        "campaign_query",
        {"view": "get", "payload": {"campaign_id": campaign_id}},
    )
    actors = [
        await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": actor_id}},
        )
        for actor_id in actor_ids
    ]
    return campaign, actors


def test_healer_kit_stabilizes_in_combat_with_action_use_and_atomic_replay(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Healer kit action", "edition": "2014", "idempotency_key": "campaign"},
            )
            healer_sheet = default_character_sheet()
            healer_sheet["edition"] = "2014"
            healer_sheet["inventory"]["items"] = [_kit("kit-full"), _kit("kit-empty", uses=0)]
            healer = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Healer",
                        "sheet": healer_sheet,
                    },
                    "idempotency_key": "healer",
                },
            )

            def pc(*, hp: int, successes: int = 0, failures: int = 0) -> dict:
                sheet = default_character_sheet()
                sheet["edition"] = "2014"
                sheet["combat"]["hp"].update({"value": hp, "max": max(1, hp)})
                if hp == 0:
                    sheet["conditions"] = ["unconscious"]
                    sheet["combat"]["death_saves"] = {
                        "successes": successes,
                        "failures": failures,
                    }
                return sheet

            decoy = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Decoy",
                        "sheet": pc(hp=10),
                    },
                    "idempotency_key": "decoy",
                },
            )
            dying_one = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Dying one",
                        "sheet": pc(hp=0, successes=1, failures=1),
                    },
                    "idempotency_key": "dying-one",
                },
            )
            dying_two = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Dying two",
                        "sheet": pc(hp=0, failures=2),
                    },
                    "idempotency_key": "dying-two",
                },
            )
            actor_ids = [healer["id"], decoy["id"], dying_one["id"], dying_two["id"]]
            current, _ = await _snapshot(server, campaign["id"], actor_ids)
            await _call(
                server,
                "game_phase",
                {
                    "campaign_id": campaign["id"],
                    "action": "set",
                    "tool_profile": "play",
                    "expected_revision": current["revision"],
                    "idempotency_key": "phase-play",
                },
            )
            current, _ = await _snapshot(server, campaign["id"], actor_ids)
            started = await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "agent",
                    "participant_ids": actor_ids,
                    "participant_config": [
                        {"actor_id": healer["id"], "initiative": 10},
                        {"actor_id": decoy["id"], "initiative": 20},
                        {"actor_id": dying_one["id"], "initiative": 5, "death_saves": True},
                        {"actor_id": dying_two["id"], "initiative": 1, "death_saves": True},
                    ],
                    "expected_revision": current["revision"],
                    "idempotency_key": "start-combat",
                },
            )
            campaign_now, actors_now = await _snapshot(server, campaign["id"], actor_ids)
            actor_by_id = {actor["id"]: actor for actor in actors_now}

            def request(action_id: str, item_id: str, target_id: str, key: str) -> dict:
                return {
                    "campaign_id": campaign["id"],
                    "action_id": action_id,
                    "item_id": item_id,
                    "intent": "stabilize",
                    "source_ref": ADVENTURING_GEAR_SOURCE_REF,
                    "actor_id": healer["id"],
                    "target_actor_id": target_id,
                    "expected_actor_revision": actor_by_id[healer["id"]]["revision"],
                    "expected_target_revision": actor_by_id[target_id]["revision"],
                    "expected_revision": campaign_now["revision"],
                    "idempotency_key": key,
                }

            wrong_turn = request("wrong-turn", "kit-full", dying_one["id"], "wrong-turn")
            before, before_actors = await _snapshot(server, campaign["id"], actor_ids)
            with pytest.raises(ToolError, match="not this actor's turn"):
                await _call(server, "adventuring_gear_action", wrong_turn)
            assert await _snapshot(server, campaign["id"], actor_ids) == (before, before_actors)

            await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": decoy["id"],
                    "expected_revision": started["campaign_revision"],
                    "idempotency_key": "end-decoy-turn",
                },
            )
            campaign_now, actors_now = await _snapshot(server, campaign["id"], actor_ids)
            actor_by_id = {actor["id"]: actor for actor in actors_now}

            healthy_target = request("healthy-target", "kit-full", decoy["id"], "healthy-target")
            before, before_actors = await _snapshot(server, campaign["id"], actor_ids)
            with pytest.raises(ToolError, match="0 hit points"):
                await _call(server, "adventuring_gear_action", healthy_target)
            assert await _snapshot(server, campaign["id"], actor_ids) == (before, before_actors)

            empty_kit = request("empty-kit", "kit-empty", dying_two["id"], "empty-kit")
            with pytest.raises(ToolError, match="no uses remaining"):
                await _call(server, "adventuring_gear_action", empty_kit)
            assert await _snapshot(server, campaign["id"], actor_ids) == (before, before_actors)

            stabilize = request("stabilize-one", "kit-full", dying_one["id"], "stabilize-one")
            result = await _call(server, "adventuring_gear_action", stabilize)
            assert result["source_ref"] == ADVENTURING_GEAR_SOURCE_REF
            assert result["rule_plan"]["effect"]["medicine_check_required"] is False
            assert "check" not in result and "roll" not in result
            assert result["resource"] == {"key": "healer_s_kit", "spent": 1, "remaining": 9}
            assert result["stabilization"]["status"] == "stable"
            assert result["stabilization"]["before_death_saves"] == {
                "successes": 1,
                "failures": 1,
            }
            assert result["stabilization"]["after_death_saves"] == {
                "successes": 0,
                "failures": 0,
            }
            assert result["combat"]["active"] is True
            healer_combatant = next(
                value
                for value in result["combat"]["combatants"]
                if value["actor_id"] == healer["id"]
            )
            assert healer_combatant["turn_budget"]["main_action"] == 0
            stabilized_combatant = next(
                value
                for value in result["combat"]["combatants"]
                if value["actor_id"] == dying_one["id"]
            )
            assert {"stable", "unconscious"}.issubset(set(stabilized_combatant["conditions"]))

            after, after_actors = await _snapshot(server, campaign["id"], actor_ids)
            after_by_id = {actor["id"]: actor for actor in after_actors}
            campaign_now = after
            actor_by_id = after_by_id
            saved_target = after_by_id[dying_one["id"]]
            assert death_save_due(stabilized_combatant, saved_target["sheet"]) is False
            assert saved_target["sheet"]["combat"]["death_saves"] == {
                "successes": 0,
                "failures": 0,
            }
            assert {"stable", "unconscious"}.issubset(set(saved_target["sheet"]["conditions"]))
            saved_healer = after_by_id[healer["id"]]
            kit = next(
                item
                for item in saved_healer["sheet"]["inventory"]["items"]
                if item["id"] == "kit-full"
            )
            assert kit["uses"]["value"] == 9
            receipt = next(
                spend
                for spend in after["state"]["item_spends"]
                if spend.get("id") == "stabilize-one"
            )
            assert receipt["uses_spent"] == 1
            assert receipt["quantity"] == 0
            assert receipt["source_ref"] == ADVENTURING_GEAR_SOURCE_REF

            assert await _call(server, "adventuring_gear_action", stabilize) == result
            assert await _snapshot(server, campaign["id"], actor_ids) == (after, after_actors)

            stale = {
                **request("stale-cas", "kit-full", dying_two["id"], "stale-cas"),
                "expected_revision": before["revision"],
            }
            with pytest.raises(ToolError, match="campaign revision conflict"):
                await _call(server, "adventuring_gear_action", stale)
            assert await _snapshot(server, campaign["id"], actor_ids) == (after, after_actors)

            no_action = request("no-action", "kit-full", dying_two["id"], "no-action")
            with pytest.raises(ToolError, match="no legal action payment|no action payment"):
                await _call(server, "adventuring_gear_action", no_action)
            assert await _snapshot(server, campaign["id"], actor_ids) == (after, after_actors)
        finally:
            close_server(server)

    asyncio.run(exercise())
