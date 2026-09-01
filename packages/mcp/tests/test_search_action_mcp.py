import asyncio
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp import server as server_module
from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


async def _raw_call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result


async def _start_combat(tmp_path: Path, edition: str):
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
    campaign = await _call(
        server,
        "campaign_create",
        {"name": "Search contract", "edition": edition, "idempotency_key": "campaign"},
    )
    searcher_sheet = default_character_sheet()
    searcher_sheet["edition"] = edition
    searcher_sheet["abilities"]["wisdom"]["score"] = 14
    searcher_sheet["abilities"]["intelligence"]["score"] = 12
    searcher_sheet["abilities"]["strength"]["score"] = 18
    searcher_sheet["skills"]["perception"] = {
        "proficiency": "proficient",
        "bonus": 1,
    }
    searcher_sheet["skills"]["investigation"] = {
        "proficiency": "expertise",
        "bonus": 2,
    }
    searcher_sheet["skills"]["survival"] = {
        "proficiency": "half",
        "bonus": 1,
    }
    searcher = await _call(
        server,
        "character_create_from",
        {
            "mode": "direct",
            "payload": {
                "campaign_id": campaign["id"],
                "name": "Searcher",
                "sheet": searcher_sheet,
            },
            "principal_id": "system:local",
            "idempotency_key": "searcher",
        },
    )
    observer_sheet = default_character_sheet()
    observer_sheet["edition"] = edition
    observer = await _call(
        server,
        "character_create_from",
        {
            "mode": "direct",
            "payload": {
                "campaign_id": campaign["id"],
                "name": "Observer",
                "sheet": observer_sheet,
            },
            "principal_id": "system:local",
            "idempotency_key": "observer",
        },
    )
    campaign = await _call(
        server,
        "campaign_query",
        {
            "view": "get",
            "payload": {"campaign_id": campaign["id"]},
            "principal_id": "system:local",
        },
    )
    phase = await _call(
        server,
        "game_phase",
        {
            "campaign_id": campaign["id"],
            "action": "set",
            "tool_profile": "play",
            "expected_revision": campaign["revision"],
            "idempotency_key": "play",
        },
    )
    started = await _call(
        server,
        "combat_start",
        {
            "positioning_mode": "agent",
            "campaign_id": campaign["id"],
            "participant_ids": [searcher["id"], observer["id"]],
            "participant_config": [
                {"actor_id": searcher["id"], "initiative": 20, "tie_breaker": 0},
                {"actor_id": observer["id"], "initiative": 10, "tie_breaker": 1},
            ],
            "expected_revision": phase["campaign_revision"],
            "idempotency_key": "start",
        },
    )
    return server, campaign["id"], searcher, started


@pytest.mark.parametrize("ability", ["perception", "investigation"])
def test_2014_search_uses_only_source_allowed_actor_card_skills(
    tmp_path: Path, ability: str
) -> None:
    async def exercise() -> None:
        server, campaign_id, searcher, started = await _start_combat(tmp_path, "2014")
        settled = await _raw_call(
            server,
            "combat_check",
            {
                "campaign_id": campaign_id,
                "actor_id": searcher["id"],
                "kind": "check",
                "ability": ability,
                "action": "search",
                "dc": 15,
                "expected_revision": started["campaign_revision"],
                "idempotency_key": f"search-{ability}",
            },
        )

        result = settled["result"]
        assert result["action"] == "search"
        assert result["skill"] == ability
        assert (
            result["ability_modifier"] + result["proficiency_bonus"] + result["bonus"]
            == searcher["derived"]["skills"][ability]
        )
        acting = next(
            item
            for item in settled["combat"]["combatants"]
            if item["actor_id"] == searcher["id"]
        )
        assert acting["turn_budget"]["main_action"] == 0

    asyncio.run(exercise())


@pytest.mark.parametrize("ability", ["wisdom", "survival"])
def test_2024_search_keeps_its_distinct_wisdom_contract(
    tmp_path: Path, ability: str
) -> None:
    async def exercise() -> None:
        server, campaign_id, searcher, started = await _start_combat(tmp_path, "2024")
        settled = await _raw_call(
            server,
            "combat_check",
            {
                "campaign_id": campaign_id,
                "actor_id": searcher["id"],
                "kind": "ability",
                "ability": ability,
                "action": "search",
                "dc": 15,
                "expected_revision": started["campaign_revision"],
                "idempotency_key": f"search-{ability}",
            },
        )

        result = settled["result"]
        assert result["action"] == "search"
        if ability == "survival":
            assert result["skill"] == "survival"
            expected_modifier = searcher["derived"]["skills"]["survival"]
        else:
            assert "skill" not in result
            expected_modifier = searcher["derived"]["ability_modifiers"]["wisdom"]
        assert (
            result["ability_modifier"] + result["proficiency_bonus"] + result["bonus"]
            == expected_modifier
        )

    asyncio.run(exercise())


def test_invalid_2014_searches_do_not_roll_pay_or_write_and_retry_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_resolve_actor_check = server_module.resolve_actor_check
    roll_calls = 0

    def tracked_resolve_actor_check(*args, **kwargs):
        nonlocal roll_calls
        roll_calls += 1
        return original_resolve_actor_check(*args, **kwargs)

    monkeypatch.setattr(server_module, "resolve_actor_check", tracked_resolve_actor_check)

    async def exercise() -> None:
        server, campaign_id, searcher, started = await _start_combat(tmp_path, "2014")
        before = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign_id},
                "principal_id": "system:local",
            },
        )
        invalid_requests = [
            {"ability": "strength"},
            {"ability": "athletics"},
            {"ability": "deception"},
            {"ability": "wisdom"},
            {"ability": "intelligence"},
            {"ability": "perception", "proficient": True},
            {"ability": "perception", "bonus": 99},
        ]
        for index, request in enumerate(invalid_requests):
            with pytest.raises(Exception, match="Search"):
                await _raw_call(
                    server,
                    "combat_check",
                    {
                        "campaign_id": campaign_id,
                        "actor_id": searcher["id"],
                        "kind": "check",
                        "action": "search",
                        "dc": 15,
                        "expected_revision": started["campaign_revision"],
                        "idempotency_key": f"invalid-search-{index}",
                        **request,
                    },
                )
        after_invalid = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign_id},
                "principal_id": "system:local",
            },
        )
        assert roll_calls == 0
        assert after_invalid["revision"] == before["revision"]
        assert after_invalid["state"] == before["state"]

        valid_request = {
            "campaign_id": campaign_id,
            "actor_id": searcher["id"],
            "kind": "check",
            "ability": "perception",
            "action": "search",
            "dc": 15,
            "expected_revision": started["campaign_revision"],
            "idempotency_key": "valid-search",
        }
        with pytest.raises(Exception, match="revision conflict"):
            await _raw_call(
                server,
                "combat_check",
                {**valid_request, "expected_revision": started["campaign_revision"] - 1},
            )
        assert roll_calls == 0

        settled = await _raw_call(server, "combat_check", valid_request)
        assert roll_calls == 1
        replayed = await _raw_call(server, "combat_check", valid_request)
        assert roll_calls == 1
        assert replayed["result"] == settled["result"]
        assert replayed["campaign_revision"] == settled["campaign_revision"]

        after = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign_id},
                "principal_id": "system:local",
            },
        )
        assert after["revision"] == before["revision"] + 1
        acting = next(
            item
            for item in after["state"]["combat"]["combatants"]
            if item["actor_id"] == searcher["id"]
        )
        assert acting["turn_budget"]["main_action"] == 0

    asyncio.run(exercise())
