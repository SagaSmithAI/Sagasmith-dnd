"""Real modern transport regressions at the shared Runtime boundary."""
import asyncio

from mcp import Client
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd_runtime.operations import RequestIdentity

from sagasmith_dnd_mcp.server import close_server
from tests.test_mcp_2026_contract import _meta, _server


def test_signed_nested_actor_scope_and_phase_transition_replay(tmp_path):
    async def exercise():
        server = _server(tmp_path)
        runtime = server.runtime
        principal = "user:owner"

        async def execute(name, args, campaign=None, who=principal):
            result = await runtime.execute(name, args, context=RequestIdentity(who, campaign))
            return result.get("result", result)

        def meta(name, campaign, revision, nonce):
            return _meta(operation=name, nonce=nonce, campaign_id=campaign,
                         base_revision=revision, requester_principal=principal,
                         resource_owner_principal=principal)

        try:
            a = await execute("campaign_create", {"name": "A", "idempotency_key": "a"})
            b = await execute("campaign_create", {"name": "B", "idempotency_key": "b"})
            actors = []
            for cid, label in ((a["id"], "a1"), (a["id"], "a2"), (b["id"], "b")):
                actors.append(await execute("character_create_from", {
                    "mode": "direct", "payload": {"campaign_id": cid, "name": label,
                                                   "sheet": default_character_sheet()},
                    "idempotency_key": label,
                }, cid))
            query = {"view": "get", "payload": {"character_id": actors[2]["id"]}}
            for who in (principal, "user:outsider"):
                try:
                    await execute("character_query", query, a["id"], who)
                except (ValueError, PermissionError):
                    pass
                else:
                    raise AssertionError("cross-campaign actor escaped the Runtime scope")
            state = await execute("campaign_query", {
                "view": "get", "payload": {"campaign_id": a["id"]},
            }, a["id"])
            phase = await execute("game_phase", {
                "campaign_id": a["id"], "action": "set", "tool_profile": "play",
                "expected_revision": state["revision"], "idempotency_key": "play",
            }, a["id"])
            start = {"campaign_id": a["id"], "positioning_mode": "agent",
                     "participant_ids": [x["id"] for x in actors[:2]],
                     "participant_config": [
                         {"actor_id": x["id"], "initiative": 20-i, "tie_breaker": i}
                         for i, x in enumerate(actors[:2])],
                     "expected_revision": phase["campaign_revision"], "idempotency_key": "start"}
            async with Client(server, mode="2026-07-28") as client:
                denied = await client.call_tool("character_query", query,
                    meta=meta("character_query", a["id"], phase["campaign_revision"], "scope"))
                assert denied.is_error
                started = await client.call_tool("combat_start", start,
                    meta=meta("combat_start", a["id"], start["expected_revision"], "start-1"))
                assert not started.is_error, started
                receipt = started.content[0].meta["sagasmith_auth_context_receipt"]
                assert (receipt["campaign_revision"]
                        == started.structured_content["campaign_revision"])
                replay = await client.call_tool("combat_start", start,
                    meta=meta("combat_start", a["id"], start["expected_revision"], "start-2"))
                assert not replay.is_error, replay
                assert replay.structured_content == started.structured_content
                revision = started.structured_content["campaign_revision"]
                end = {"campaign_id": a["id"], "expected_revision": revision,
                       "idempotency_key": "end"}
                remember = runtime.ports["remember_transition"]

                def fail_receipt(*args):
                    raise ValueError("simulated receipt persistence failure")

                runtime.ports["remember_transition"] = fail_receipt
                failed = await client.call_tool("combat_end", end,
                    meta=meta("combat_end", a["id"], revision, "end-rollback"))
                assert failed.is_error
                runtime.ports["remember_transition"] = remember
                unchanged = await execute("campaign_query", {
                    "view": "get", "payload": {"campaign_id": a["id"]},
                }, a["id"])
                assert unchanged["revision"] == revision
                assert unchanged["effective_game_phase"] == "combat"
                ended = await client.call_tool("combat_end", end,
                    meta=meta("combat_end", a["id"], revision, "end-1"))
                assert not ended.is_error, ended
                replay = await client.call_tool("combat_end", end,
                    meta=meta("combat_end", a["id"], revision, "end-2"))
                assert not replay.is_error, replay
                assert replay.structured_content == ended.structured_content
                changed = await client.call_tool("combat_end", {**end, "outcome": {
                    "status": "victory", "summary": "different request"}},
                    meta=meta("combat_end", a["id"], revision, "end-changed"))
                assert changed.is_error
                fresh = await client.call_tool("combat_end", {**end, "idempotency_key": "new"},
                    meta=meta("combat_end", a["id"], revision, "end-new"))
                assert fresh.is_error
            close_server(server)
            server = _server(tmp_path)
            runtime = server.runtime
            async with Client(server, mode="2026-07-28") as client:
                restarted = await client.call_tool("combat_end", end,
                    meta=meta("combat_end", a["id"], revision, "end-restart"))
                assert not restarted.is_error, restarted
                assert restarted.structured_content == ended.structured_content
            assert runtime.operations["state_revision"].annotations.read_only_hint is False
        finally:
            close_server(server)

    asyncio.run(exercise())
