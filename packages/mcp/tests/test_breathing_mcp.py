import asyncio
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    value = result.get("result", result) if isinstance(result, dict) else result
    if isinstance(value, dict) and "action" in value and "result" in value:
        return value["result"]
    return value


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


def test_breathing_transition_persists_and_clock_settles_once(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Breathing", "edition": "2014", "idempotency_key": "campaign"},
        )
        sheet = default_character_sheet()
        sheet["edition"] = "2014"
        sheet["abilities"]["constitution"]["score"] = 10
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Actor", "sheet": sheet},
                "idempotency_key": "actor",
            },
        )
        started = await _call(
            server,
            "character_state_change",
            {
                "character_id": actor["id"],
                "action": "breathing_transition",
                "payload": {"can_breathe": False},
                "expected_revision": actor["revision"],
                "idempotency_key": "underwater",
            },
        )
        assert (
            await _call(
                server,
                "character_state_change",
                {
                    "character_id": actor["id"],
                    "action": "breathing_transition",
                    "payload": {"can_breathe": False},
                    "expected_revision": actor["revision"],
                    "idempotency_key": "underwater",
                },
            )
        ) == started
        clock = await _call(
            server, "campaign_query", {"view": "get", "payload": {"campaign_id": campaign["id"]}}
        )
        first = await _call(
            server,
            "campaign_change",
            {
                "campaign_id": campaign["id"],
                "action": "clock_advance",
                "payload": {"period": "round", "count": 1},
                "expected_revision": clock["revision"],
                "idempotency_key": "one",
            },
        )
        after = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": actor["id"]}},
        )
        timer = next(item for item in after["sheet"]["effects"] if item["active"])
        assert timer["metadata"]["hold_remaining_rounds"] == 9
        second = await _call(
            server,
            "campaign_change",
            {
                "campaign_id": campaign["id"],
                "action": "clock_advance",
                "payload": {"period": "round", "count": 9},
                "expected_revision": first["campaign_revision"],
                "idempotency_key": "nine",
            },
        )
        after = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": actor["id"]}},
        )
        assert "suffocating" in after["sheet"]["conditions"]
        final = await _call(
            server,
            "campaign_change",
            {
                "campaign_id": campaign["id"],
                "action": "clock_advance",
                "payload": {"period": "round", "count": 1},
                "expected_revision": second["campaign_revision"],
                "idempotency_key": "final",
            },
        )
        assert final["campaign_revision"] == second["campaign_revision"] + 1
        after = await _call(
            server, "character_query", {"view": "get", "payload": {"character_id": actor["id"]}}
        )
        assert after["sheet"]["combat"]["hp"]["value"] == 0

    asyncio.run(exercise())


@pytest.mark.parametrize("affected_index", (0, 1))
@pytest.mark.parametrize("constitution", (10, 14))
@pytest.mark.parametrize("choking", (False, True))
def test_combat_choking_expires_at_affected_actor_turn_start(
    tmp_path: Path, affected_index: int, constitution: int, choking: bool
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Combat breathing", "edition": "2014", "idempotency_key": "campaign"},
        )
        actors = []
        for index in range(2):
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["abilities"]["constitution"]["score"] = constitution
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": f"Actor {index}",
                        "sheet": sheet,
                    },
                    "idempotency_key": f"actor-{index}",
                },
            )
            actors.append(actor)
        await _call(
            server,
            "character_state_change",
            {
                "character_id": actors[affected_index]["id"],
                "action": "breathing_transition",
                "payload": {"can_breathe": False, "choking": choking},
                "expected_revision": actors[affected_index]["revision"],
                "idempotency_key": "underwater",
            },
        )
        combat = await _call(
            server,
            "combat_start",
            {
                "positioning_mode": "agent",
                "campaign_id": campaign["id"],
                "participant_ids": [actor["id"] for actor in actors],
                "participant_config": [
                    {"actor_id": actors[0]["id"], "initiative": 20},
                    {"actor_id": actors[1]["id"], "initiative": 10},
                ],
                "expected_revision": (
                    await _call(
                        server,
                        "campaign_query",
                        {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                    )
                )["revision"],
                "idempotency_key": "combat-start",
            },
        )
        modifier = (constitution - 10) // 2
        hold_rounds = 0 if choking else (1 + modifier) * 10
        grace_rounds = max(1, modifier)
        deadline = hold_rounds + grace_rounds
        revision = combat["campaign_revision"]
        dropped = False
        for turn in range(2 * deadline + 1):
            ending_index = turn % 2
            settled = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": actors[ending_index]["id"],
                    "expected_revision": revision,
                    "idempotency_key": f"turn-{turn}",
                },
            )
            revision = settled["campaign_revision"]
            elapsed = (turn + 1) // 2
            assert settled["game_time"]["elapsed_ticks"] == elapsed
            current = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actors[affected_index]["id"]}},
            )
            own_turn_start = 1 - ending_index == affected_index
            dropped = elapsed >= deadline and own_turn_start
            assert (current["sheet"]["combat"]["hp"]["value"] == 0) is dropped
            if dropped:
                break
            timer = next(item for item in current["sheet"]["effects"] if item["active"])
            assert timer["metadata"]["hold_remaining_rounds"] == max(0, hold_rounds - elapsed)
            assert timer["metadata"]["suffocation_remaining_rounds"] == max(
                0, grace_rounds - max(0, elapsed - hold_rounds)
            )
        assert dropped
        other = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": actors[1 - affected_index]["id"]}},
        )
        assert other["sheet"]["combat"]["hp"]["value"] > 0

    asyncio.run(exercise())
