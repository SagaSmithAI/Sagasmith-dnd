"""Numeric dice helpers must never substitute for actor-aware combat settlement."""

import asyncio
import json

from jsonschema import Draft202012Validator

from sagasmith_dnd_mcp.server import close_server
from tests.test_search_action_mcp import _start_combat


def test_numeric_check_preserves_action_and_actor_search_spends_it(tmp_path) -> None:
    async def exercise() -> None:
        server, campaign_id, actor, started = await _start_combat(tmp_path, "2024")

        async def call(name, arguments):
            _, result = await server.call_tool(name, arguments)
            tool = server._tool_manager.get_tool(name)
            schema = tool.fn_metadata.output_schema
            Draft202012Validator(schema).validate(json.loads(json.dumps(result)))
            return result

        def budget(state):
            return next(item for item in state["combatants"]
                        if item["actor_id"] == actor["id"])["turn_budget"]["main_action"]

        try:
            numeric = await call("dnd_check", {
                "campaign_id": campaign_id, "dc": 10, "ability_score": 10,
                "expected_campaign_revision": started["campaign_revision"],
                "idempotency_key": "numeric-calculator",
            })
            status = await call("combat_query", {"campaign_id": campaign_id})
            assert budget(status["result"]) == 1
            request = {
                "campaign_id": campaign_id, "actor_id": actor["id"], "kind": "ability",
                "ability": "perception", "action": "search", "dc": 10,
                "expected_revision": numeric["campaign_revision"],
                "idempotency_key": "actor-search",
            }
            resolved = await call("combat_check", request)
            assert resolved["result"]["action"] == "search"
            assert budget(resolved["combat"]) == 0
            assert await call("combat_check", request) == resolved
            dice = await call("dnd_dice_roll", {
                "campaign_id": campaign_id, "expression": "1d6",
                "expected_campaign_revision": resolved["campaign_revision"],
                "idempotency_key": "raw-dice",
            })
            assert dice["resolution_id"]
        finally:
            close_server(server)

    asyncio.run(exercise())
