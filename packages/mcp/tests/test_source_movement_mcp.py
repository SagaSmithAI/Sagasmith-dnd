import asyncio
from copy import deepcopy

import pytest
from test_semantic_continuations_mcp import prepare
from test_semantic_plan_mcp import _call, _raw

from sagasmith_dnd_mcp.server import close_server, create_server


@pytest.mark.parametrize("payment", ["reaction", "action", "movement"])
def test_source_movement_is_paid_off_turn_and_resumes_after_restart(tmp_path, payment):
    async def exercise():
        server, config, cid, actors, paid = await prepare(
            tmp_path,
            reaction=False,
            movement_payment=payment,
        )
        source, mover = (a["id"] for a in actors)
        commitment = paid["result"]["declaration"]["agent_resolution_commitment"]
        request = {
            "campaign_id": cid,
            "actor_id": source,
            "action": "execute_plan",
            "payload": {"commitment": commitment},
            "expected_revision": paid["campaign_revision"],
            "idempotency_key": "execute",
        }
        try:
            before = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": cid},
                },
            )
            altered = deepcopy(request)
            altered["payload"]["commitment"]["plan_fingerprint"] = "forged"
            with pytest.raises(Exception, match="does not match the recorded plan"):
                await _raw(server, "combat_choice", altered)
            assert (
                await _call(
                    server,
                    "campaign_query",
                    {
                        "view": "get",
                        "payload": {"campaign_id": cid},
                    },
                )
                == before
            )
            with pytest.raises(Exception, match="revision conflict"):
                await _raw(server, "combat_choice", {**request, "expected_revision": 0})
            paused = await _call(server, "combat_choice", request)
            combat = paused["combat"]
            actor = next(a for a in combat["combatants"] if a["actor_id"] == mover)
            assert actor["position"] == {"x": 1, "y": 0}
            assert "prone" not in actor["conditions"]
            assert len(combat["pending"]) == 1
            if payment != "movement":
                assert (
                    actor["turn_budget"]["reaction" if payment == "reaction" else "main_action"]
                    == 0
                )
            source_payment = next(
                e for e in combat["log"] if e["type"] == "source_movement_payment"
            )
            assert source_payment["source"]["plan_fingerprint"] == commitment["plan_fingerprint"]
            close_server(server)
            server = create_server(config)
            assert await _call(server, "combat_choice", request) == paused
            # An unfinished route blocks the remaining plan, with no new spend/write.
            premature = await _raw(
                server,
                "combat_choice",
                {
                    **request,
                    "expected_revision": paused["campaign_revision"],
                    "idempotency_key": "premature",
                },
            )
            assert premature["status"] == "pending_ruling"
            window = combat["pending"][0]
            finished = await _call(
                server,
                "combat_choice",
                {
                    "campaign_id": cid,
                    "actor_id": source,
                    "action": "resolve",
                    "payload": {"choice_id": window["id"], "selection": {"id": "decline"}},
                    "expected_revision": paused["campaign_revision"],
                    "idempotency_key": "decline",
                },
            )
            actor = next(a for a in finished["combat"]["combatants"] if a["actor_id"] == mover)
            assert actor["position"] == {"x": 2, "y": 0}
            assert "prone" not in actor["conditions"]
            # The first waiting ID has gone; the second reach exit must still block the plan.
            premature = await _raw(
                server,
                "combat_choice",
                {
                    **request,
                    "expected_revision": finished["campaign_revision"],
                    "idempotency_key": "premature-later",
                },
            )
            assert premature["status"] == "pending_ruling"
            window = finished["combat"]["pending"][0]
            assert window["opportunity_attack_weapon_ids"] == ["whip"]
            finished = await _call(
                server,
                "combat_choice",
                {
                    "campaign_id": cid,
                    "actor_id": source,
                    "action": "resolve",
                    "payload": {"choice_id": window["id"], "selection": {"id": "decline"}},
                    "expected_revision": finished["campaign_revision"],
                    "idempotency_key": "decline-later",
                },
            )
            actor = next(a for a in finished["combat"]["combatants"] if a["actor_id"] == mover)
            assert actor["position"] == {"x": 4, "y": 0}
            assert actor["turn_budget"].get("movement_spent", 0) == (
                15 if payment == "movement" else 0
            )
            assert "prone" not in actor["conditions"]
            final = await _call(
                server,
                "combat_choice",
                {
                    **request,
                    "expected_revision": finished["campaign_revision"],
                    "idempotency_key": "resume",
                },
            )
            actor = next(a for a in final["combat"]["combatants"] if a["actor_id"] == mover)
            assert "prone" in actor["conditions"]
            assert not final["combat"]["semantic_state"]["continuations"]
            current = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": cid},
                },
            )
            assert (
                len(
                    [
                        e
                        for e in current["state"]["combat"]["log"]
                        if e["type"] == "source_movement_payment"
                    ]
                )
                == 1
            )
            assert final["result"]["results"]["move"]["position"] == {"x": 4, "y": 0}
            assert final["result"]["results"]["move"]["movement_status"] == "completed"
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_source_movement_without_eligible_reaction_leaves_state_unchanged(tmp_path):
    async def exercise():
        server, _config, cid, actors, paid = await prepare(
            tmp_path,
            reaction=False,
            movement_payment="reaction",
            mover_incapacitated=True,
        )
        try:
            query = {"view": "get", "payload": {"campaign_id": cid}}
            before = await _call(server, "campaign_query", query)
            request = {
                "campaign_id": cid,
                "actor_id": actors[0]["id"],
                "action": "execute_plan",
                "payload": {
                    "commitment": paid["result"]["declaration"]["agent_resolution_commitment"]
                },
                "expected_revision": paid["campaign_revision"],
                "idempotency_key": "execute",
            }
            with pytest.raises(Exception, match="cannot pay for source movement"):
                await _raw(server, "combat_choice", request)
            assert await _call(server, "campaign_query", query) == before
        finally:
            close_server(server)

    asyncio.run(exercise())
