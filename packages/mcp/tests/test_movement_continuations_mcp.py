import asyncio

import pytest
from sagasmith_dnd.character_schema import default_character_sheet
from test_combat_transaction_boundaries_mcp import _config
from test_opportunity_sneak_attack_mcp import _call, _raw
from test_opportunity_turn_lifecycle_mcp import _FixedD20

import sagasmith_dnd_mcp.server as server_module
from sagasmith_dnd_mcp.server import close_server, create_server


@pytest.mark.parametrize("outcome", ["decline", "miss", "lethal"])
def test_movement_continuation_is_atomic_replayable_and_durable(tmp_path, monkeypatch, outcome):
    rolls = []
    real_roll = server_module.roll_attack_action

    def roll(*, plan):
        rolls.append(plan)
        return real_roll(plan=plan, rng=_FixedD20(1 if outcome == "miss" else 20))

    monkeypatch.setattr(server_module, "roll_attack_action", roll)

    async def exercise():
        config = _config(tmp_path)
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Interrupted movement",
                    "edition": "2014",
                    "idempotency_key": "campaign",
                },
            )
            actors = []
            for name in ("mover", "threat", "later"):
                sheet = default_character_sheet()
                sheet["combat"]["hp"] = {"value": 1, "max": 1, "temp": 0}
                actors.append(
                    await _call(
                        server,
                        "character_create_from",
                        {
                            "mode": "direct",
                            "payload": {
                                "campaign_id": campaign["id"],
                                "name": name,
                                "sheet": sheet,
                            },
                            "idempotency_key": name,
                        },
                    )
                )
            mover, threat, later = actors
            started = await _raw(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 10, "height_cells": 5},
                    "participant_ids": [a["id"] for a in actors],
                    "participant_config": [
                        {
                            "actor_id": actor["id"],
                            "initiative": 30 - index * 10,
                            "position": position,
                            "disposition": "friendly" if index == 0 else "hostile",
                        }
                        for index, (actor, position) in enumerate(
                            zip(
                                actors,
                                [
                                    {"x": 0, "y": 0},
                                    {"x": 1, "y": 1},
                                    {"x": 4, "y": 1},
                                ],
                            )
                        )
                    ],
                    "expected_revision": campaign["revision"],
                    "idempotency_key": "start",
                },
            )
            request = {
                "campaign_id": campaign["id"],
                "actor_id": mover["id"],
                "action": "move",
                "payload": {"distance": 30, "destination": {"x": 6, "y": 0}},
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "move",
            }
            paused = await _call(server, "combat_movement", request)
            assert paused["status"] == "pending_reaction"
            moved = next(a for a in paused["combat"]["combatants"] if a["actor_id"] == mover["id"])
            assert moved["position"] == {"x": 2, "y": 0}
            assert moved["turn_budget"]["movement_spent"] == 10
            assert len(paused["combat"]["pending"]) == 1
            window = paused["combat"]["pending"][0]
            assert window["actor_id"] == threat["id"]
            close_server(server)
            server = create_server(config)
            assert await _call(server, "combat_movement", request) == paused
            arguments = {
                "campaign_id": campaign["id"],
                "actor_id": threat["id"],
                "expected_revision": paused["campaign_revision"],
                "idempotency_key": "reaction",
            }
            if outcome == "decline":
                tool = "combat_choice"
                arguments.update(
                    action="resolve",
                    payload={
                        "choice_id": window["id"],
                        "selection": {"id": "decline"},
                    },
                )
            else:
                tool = "combat_reaction_attack"
                arguments.update(
                    choice_id=window["id"],
                    target_id=mover["id"],
                    action={"weapon_id": "unarmed-strike"},
                )
            with pytest.raises(Exception, match="revision conflict"):
                await _raw(
                    server, tool, {**arguments, "expected_revision": 0, "idempotency_key": "stale"}
                )
            assert not rolls
            settled = await _raw(server, tool, arguments)
            expected_rolls = 0 if outcome == "decline" else 1
            assert len(rolls) == expected_rolls
            assert await _raw(server, tool, arguments) == settled
            assert len(rolls) == expected_rolls
            current = next(
                a for a in settled["combat"]["combatants"] if a["actor_id"] == mover["id"]
            )
            if outcome == "lethal":
                assert current["position"] == {"x": 2, "y": 0}
                assert current["hit_points"] == 0
                assert current["turn_budget"]["movement_spent"] == 10
                assert not settled["combat"]["pending"]
                assert settled["movement_outcome"]["status"] == "cancelled"
            else:
                assert current["position"] == {"x": 5, "y": 0}
                assert current["turn_budget"]["movement_spent"] == 25
                later_window = settled["combat"]["pending"][0]
                assert later_window["actor_id"] == later["id"]
                final = await _call(
                    server,
                    "combat_choice",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": later["id"],
                        "action": "resolve",
                        "payload": {
                            "choice_id": later_window["id"],
                            "selection": {"id": "decline"},
                        },
                        "expected_revision": settled["campaign_revision"],
                        "idempotency_key": "decline-later",
                    },
                )
                current = next(
                    a for a in final["combat"]["combatants"] if a["actor_id"] == mover["id"]
                )
                assert current["position"] == {"x": 6, "y": 0}
                assert current["turn_budget"]["movement_spent"] == 30
                assert not final["combat"].get("movement_continuation")
        finally:
            close_server(server)

    asyncio.run(exercise())
