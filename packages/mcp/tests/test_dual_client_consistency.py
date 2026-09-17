from copy import deepcopy

import pytest
from aiohttp.test_utils import TestClient, TestServer
from mcp.types import CallToolResult

from sagasmith_dnd_mcp.gateway import GatewayConfig, create_app
from sagasmith_dnd_mcp.server import close_server
from tests import test_semantic_plan_mcp as fixture


def test_ui_interleave_refreshes_read_and_preserves_paid_agent_application(tmp_path, monkeypatch):
    class CompleteError(Exception):
        pass

    servers = []
    original_factory = fixture.create_server

    def factory(config):
        server = original_factory(config)
        servers.append(server)
        call = server.call_tool

        async def intercepted(name, arguments, *args, **kwargs):
            if name != "combat_choice" or arguments.get("idempotency_key") != "settle":
                return await call(name, arguments, *args, **kwargs)

            class Bridge:
                moved = None

                async def start(self):
                    pass

                async def stop(self):
                    pass

                async def call_tool(self, tool, data):
                    content, structured = await call(tool, data)
                    if tool == "combat_query" and self.moved is None:
                        _, self.moved = await call(
                            "combat_movement",
                            {
                                "campaign_id": arguments["campaign_id"],
                                "actor_id": arguments["actor_id"],
                                "action": "move",
                                "payload": {"distance": 5, "destination": {"x": 1, "y": 0}},
                                "expected_revision": arguments["expected_revision"],
                                "idempotency_key": "ui-interleave",
                            },
                        )
                    return CallToolResult(content=content, structuredContent=structured)

            bridge = Bridge()
            async with TestClient(TestServer(create_app(GatewayConfig(), bridge, config))) as http:
                response = await http.get(f"/api/campaigns/{arguments['campaign_id']}/combat")
                assert response.status == 200
                view = await response.json()
            revision = bridge.moved["result"]["campaign_revision"]
            actor = next(
                a for a in view["data"]["combatants"] if a["actor_id"] == arguments["actor_id"]
            )
            assert actor["position"] == {"x": 1, "y": 0}
            assert (
                view["data"]["campaign_revision"] == view["meta"]["campaign_revision"] == revision
            )
            with pytest.raises(Exception, match="revision conflict"):
                await call(name, arguments)
            refreshed = deepcopy(arguments)
            refreshed["expected_revision"] = revision
            refreshed["idempotency_key"] = "fresh-settlement"
            with pytest.raises(Exception, match="target_facts"):
                await call(name, refreshed)
            commitment = refreshed["payload"]["commitment"]
            commitment.pop("bound_plan_fingerprint", None)
            commitment["agent_ruling"]["target_facts"]["campaign_revision"] = revision
            changed = deepcopy(refreshed)
            changed["payload"]["commitment"]["agent_ruling"]["decision"] = "Different decision"
            with pytest.raises(Exception):
                await call(name, changed)
            _, settled = await call(name, refreshed)
            assert settled["result"]["status"] == "committed"
            _, replay = await call(name, refreshed)
            assert replay == settled
            raise CompleteError()

        server.call_tool = intercepted
        return server

    monkeypatch.setattr(fixture, "create_server", factory)
    try:
        with pytest.raises(CompleteError):
            fixture.test_custom_monster_plan_pays_executes_replays_and_rejects_mutation(
                tmp_path,
                None,
                "grid",
            )
    finally:
        for server in servers:
            close_server(server)
