import asyncio

import pytest
from sagasmith_dnd.statblocks import parse_2014_statblock
from test_combat_transaction_boundaries_mcp import _call, _config
from test_statblock_import_mcp import COMMONER

from sagasmith_dnd_mcp.server import close_server, create_server


@pytest.mark.parametrize("provokes", [False, True])
def test_movement_requires_explicit_reaction_assessment(tmp_path, provokes):
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
                        {
                            "actor_id": a["id"],
                            "initiative": 20 - i * 10,
                            "disposition": "friendly" if i == 0 else "hostile",
                        }
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
                "decision_id": "leave-reach",
                "reason": "The actor moves away; reaction eligibility is explicitly assessed.",
                "destination_legal": True,
                "distance_ft": 5,
            }
            arguments = {
                "campaign_id": campaign["id"],
                "actor_id": actors[0]["id"],
                "action": "move",
                "payload": {"distance": 5, "spatial_facts": facts},
                "expected_revision": before["revision"],
                "idempotency_key": "move",
            }
            missing = await _call(server, "combat_movement", arguments)
            assert missing["status"] == "pending_ruling"
            assert "movement.spatial_facts.opportunity_attack_actor_ids" in missing["missing"]
            after = await _call(
                server,
                "campaign_query",
                {
                    "view": "get",
                    "payload": {"campaign_id": campaign["id"]},
                },
            )
            assert after == before
            facts["opportunity_attack_actor_ids"] = [actors[1]["id"]] if provokes else []
            moved = await _call(server, "combat_movement", arguments)
            assert moved["status"] == "committed"
            assert await _call(server, "combat_movement", arguments) == moved
            encounter = moved["combat"]
            reactions = [
                p for p in encounter.get("pending", []) if p.get("trigger") == "opportunity_attack"
            ]
            assert len(reactions) == int(provokes)
            if provokes:
                assert reactions[0]["actor_id"] == actors[1]["id"]
                assert reactions[0]["target_id"] == actors[0]["id"]
        finally:
            close_server(server)

    asyncio.run(exercise())
