import asyncio

import pytest
from sagasmith_dnd.statblocks import parse_2014_statblock
from test_combat_transaction_boundaries_mcp import _call, _config
from test_statblock_import_mcp import COMMONER

from sagasmith_dnd_mcp.server import close_server, create_server


@pytest.mark.parametrize("distance", [None, False, True])
def test_optional_distance_does_not_invent_an_out_of_reach_fact(tmp_path, distance):
    async def exercise():
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Optional spatial fact",
                    "edition": "2014",
                    "idempotency_key": "campaign",
                },
            )
            actors = []
            for index in range(2):
                actors.append(
                    await _call(
                        server,
                        "character_create_from",
                        {
                            "mode": "direct",
                            "payload": {
                                "campaign_id": campaign["id"],
                                "character_type": "monster",
                                "name": f"Guard {index}",
                                "sheet": parse_2014_statblock(
                                    COMMONER, source_key="fixture:commoner"
                                ).sheet,
                            },
                            "idempotency_key": f"actor-{index}",
                        },
                    )
                )
            campaign = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign["id"]},
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
                    "idempotency_key": "phase",
                },
            )
            await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "agent",
                    "participant_ids": [a["id"] for a in actors],
                    "participant_config": [
                        {"actor_id": a["id"], "initiative": 20 - i * 10}
                        for i, a in enumerate(actors)
                    ],
                    "expected_revision": phase["campaign_revision"],
                    "idempotency_key": "start",
                },
            )
            before = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign["id"]},
                },
            )
            facts = {
                "decision_id": "reach",
                "reason": "The selected club can reach the target.",
                "targetable": True,
                "in_range": True,
                "cover_degree": "none",
                "attacker_can_see_target": True,
                "target_can_see_attacker": True,
            }
            if distance is not None:
                facts["target_within_5_ft"] = distance
            arguments = {
                "campaign_id": campaign["id"],
                "actor_id": actors[0]["id"],
                "target_id": actors[1]["id"],
                "action": {"weapon_id": "club", "context": {"spatial_facts": facts}},
                "expected_revision": before["revision"],
                "idempotency_key": "attack",
            }
            if distance is False:
                with pytest.raises(Exception, match="contradict weapon reach"):
                    await _call(server, "combat_resolve_attack", arguments)
                after = await _call(
                    server,
                    "campaign_query",
                    {
                        "view": "get",
                        "payload": {"campaign_id": campaign["id"]},
                    },
                )
                assert after == before
            else:
                result = await _call(server, "combat_resolve_attack", arguments)
                assert result["roll_mode"] == "normal"
                assert await _call(server, "combat_resolve_attack", arguments) == result
        finally:
            close_server(server)

    asyncio.run(exercise())
