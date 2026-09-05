"""Revival must respect source-bound events resolved during its delay."""

import asyncio
from pathlib import Path

import pytest
from mcp import Client

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from scripts.regression_official_expansions import _ProtocolTools
from tests.authoring_helpers import import_and_activate_addon_fixture
from tests.test_official_expansions_mcp import _call
from tests.test_steel_defender_lifecycle_mcp import _create_bound_defender


@pytest.mark.parametrize("owner_death", ["independent", "perish"])
def test_source_event_owner_death_during_revival_follows_source_policy(
    tmp_path: Path, owner_death: str,
) -> None:
    config = McpConfig(
        home=tmp_path / "home", database_url=None, chroma_url=None,
        chroma_path_override=None, dnd_skills_dir=tmp_path / "skills",
        modulegen_skills_dir=tmp_path / "modulegen", auto_seed_rules=False,
    )

    async def exercise():
        runtime = create_server(config)
        try:
            async with Client(runtime, mode="2026-07-28") as client:
                server = _ProtocolTools(client)
                campaign, owner, defender = await _create_bound_defender(
                    server, config, owner_death=owner_death,
                )

                async def snapshot():
                    current = await _call(server, "campaign_query", {
                        "view": "get", "payload": {"campaign_id": campaign["id"]},
                    })
                    actors = [await _call(server, "character_query", {
                        "view": "get", "payload": {"character_id": actor["id"]},
                    }) for actor in (owner, defender)]
                    return current, *actors

                await _call(server, "character_state_change", {
                    "character_id": defender["id"], "action": "damage",
                    "payload": {"parts": [{"amount": 100, "damage_type": "force"}]},
                    "expected_revision": defender["revision"], "idempotency_key": "kill",
                })
                before = await _call(server, "campaign_query", {
                    "view": "get", "payload": {"campaign_id": campaign["id"]},
                })
                # Synthetic authored hazard, not an assertion that this is a
                # published spell: it exercises the supported duration event.
                await import_and_activate_addon_fixture(
                    _call, server, campaign["id"], config.home,
                    manifest={
                        "id": "dnd5e.addon.revival-hazard", "version": "1.0.0",
                        "title": "Revival hazard", "namespace": "dnd5e.addon.revival-hazard",
                        "system_id": "dnd5e", "editions": ["2014"],
                        "capabilities": ["duration.advance"],
                        "tests": [{
                            "name": "owner death event", "event": "duration.advance",
                            "facts": {"actor_id": owner["id"]}, "sheet": {"conditions": []},
                            "expect": [{"path": "conditions", "equals": ["dead"]}],
                        }],
                    },
                    artifacts=[],
                    mechanics=[{
                        "id": "dnd5e.addon.revival-hazard.death",
                        "event": "duration.advance",
                        "predicates": [{"kind": "fact_equals", "key": "actor_id",
                                        "value": owner["id"]}],
                        "operations": [{"op": "condition.add", "id": "dead"}],
                    }],
                    expected_revision=before["revision"], request_key="hazard",
                    source_chunks_override=[
                        "When elapsed time advances, the designated owner dies.",
                    ],
                )
                ready = await snapshot()
                current_owner = ready[1]
                assert "dead" not in current_owner["sheet"]["conditions"]
                relation_before = ready[0]["state"]["dependent_actor_relations"][0]
                death_tick = relation_before["death_elapsed_ticks"]
                assert death_tick is not None
                request = {
                    "character_id": owner["id"], "action": "revive_steel_defender",
                    "payload": {
                        "dependent_actor_id": defender["id"], "slot_level": 1,
                        "spatial_facts": {
                            "distance_ft": 5, "default_resolver": "agent",
                            "ruling_kind": "agent_dm_adjudication",
                            "reason": "The owner begins beside the destroyed defender.",
                        },
                    },
                    "expected_revision": current_owner["revision"], "idempotency_key": "revive",
                }
                response = await server.call_tool("character_action", request)
                after = await snapshot()
                after_campaign, after_owner, after_defender = after
                assert "dead" in after_owner["sheet"]["conditions"]
                perishes = owner_death == "perish"
                assert ("dead" in after_defender["sheet"]["conditions"]) is perishes
                hp = after_defender["sheet"]["combat"]["hp"]
                assert hp["value"] == (0 if perishes else hp["max"])
                assert after_owner["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] == (
                    current_owner["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] - 1
                )
                assert after_campaign["state"]["game_time"]["elapsed_ticks"] == (
                    ready[0]["state"]["game_time"]["elapsed_ticks"] + 10
                )
                relation = after_campaign["state"]["dependent_actor_relations"][0]
                assert relation["status"] == ("dead" if perishes else "active")
                assert relation["death_elapsed_ticks"] == (death_tick if perishes else None)
                assert relation["revival_started_elapsed_ticks"] is None
                assert relation["revival_completes_elapsed_ticks"] is None
                result = response[1]["result"]
                assert result["action_paid"] is True and result["elapsed_ticks"] == 10
                assert result["payment"]["kind"] == "spell_slot"
                assert result["payment"]["level"] == 1
                assert result["owner"] == after_owner
                assert await server.call_tool("character_action", request) == response
                assert await snapshot() == after
        finally:
            close_server(runtime)

        restarted = create_server(config)
        try:
            async with Client(restarted, mode="2026-07-28") as client:
                server = _ProtocolTools(client)
                assert await server.call_tool("character_action", request) == response
                assert await snapshot() == after
        finally:
            close_server(restarted)

    asyncio.run(exercise())
