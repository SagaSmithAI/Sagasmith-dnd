"""Atomic Block and Tackle capacity checks bound to a reviewed scene load."""

from __future__ import annotations

import asyncio

import pytest

from tests.test_adventuring_gear_strength_mcp import (
    DOOR_EXCERPT,
    World,
    review_object,
    setup,
)


def test_block_and_tackle_derives_capacity_and_commits_reviewed_result(tmp_path):
    async def run():
        world: World = await setup(tmp_path)
        try:
            await review_object(
                world,
                world.door,
                door=True,
                leverage=False,
                excerpt=DOOR_EXCERPT,
            )

            def make_request(campaign, actor, *, action_id: str, weight_lb: int):
                return {
                    "campaign_id": world.cid,
                    "action_id": action_id,
                    "item_id": "block-and-tackle-1",
                    "intent": "hoist",
                    "source_ref": "bundled:srd2014/04_Equipment/Adventuring_Gear.md",
                    "actor_id": world.aid,
                    "expected_actor_revision": actor["revision"],
                    "expected_revision": campaign["revision"],
                    "idempotency_key": action_id,
                    "action_context": {
                        "load_object": {
                            "id": world.door["id"],
                            "scene_id": world.door["scene_id"],
                        },
                        "source_ref": world.source,
                        "load_weight_lb": weight_lb,
                        "review_reason": "The DM reviewed the exact object's weight.",
                    },
                }

            campaign, actor = await world.snapshot()
            before = (campaign, actor)
            forged = make_request(campaign, actor, action_id="hoist-forged", weight_lb=1)
            forged["action_context"]["normal_lift_capacity_lb"] = 9999
            with pytest.raises(Exception, match="DM-reviewed load weight bound"):
                await world.call("adventuring_gear_action", forged)
            assert await world.snapshot() == before

            wrong_scene = make_request(campaign, actor, action_id="hoist-wrong-scene", weight_lb=1)
            wrong_scene["action_context"]["load_object"]["scene_id"] = "another-scene"
            with pytest.raises(Exception, match="scene_id does not match"):
                await world.call("adventuring_gear_action", wrong_scene)
            assert await world.snapshot() == before

            overweight_request = make_request(
                campaign, actor, action_id="hoist-over-capacity", weight_lb=361
            )
            overweight = await world.call("adventuring_gear_action", overweight_request)
            assert overweight["hoist"]["status"] == "over_capacity"
            assert overweight["hoist"]["within_capacity"] is False
            assert overweight["hoist"]["normal_lift_capacity_lb"] == 90
            assert overweight["hoist"]["block_and_tackle_capacity_lb"] == 360
            assert overweight["hoist"]["destination"] is None
            assert overweight["hoist"]["weight_review"]["reviewed_by"] == "system:local"
            assert overweight["hoist"]["weight_review"]["reviewed_campaign_revision"] == (
                campaign["revision"]
            )
            after_overweight = await world.snapshot()
            assert after_overweight[0]["revision"] == campaign["revision"] + 1
            assert await world.call("adventuring_gear_action", overweight_request) == overweight
            assert await world.snapshot() == after_overweight

            stale = make_request(campaign, actor, action_id="hoist-stale", weight_lb=1)
            with pytest.raises(Exception, match="revision conflict"):
                await world.call("adventuring_gear_action", stale)
            assert await world.snapshot() == after_overweight

            actor = after_overweight[1]
            await world.call(
                "character_state_change",
                {
                    "character_id": world.aid,
                    "action": "effect_add",
                    "payload": {
                        "effect": {
                            "id": "bulls-strength",
                            "name": "Bull's Strength",
                            "kind": "spell",
                            "source": "spell.cast",
                            "source_spell_id": "dnd5e.content.srd2014.spell.enhance-ability",
                            "active": True,
                            "concentration": True,
                            "duration": {"period": "minute", "remaining": 1},
                            "changes": [
                                {
                                    "path": "carrying_capacity.multiplier",
                                    "mode": "multiply",
                                    "value": 2,
                                }
                            ],
                        }
                    },
                    "expected_revision": actor["revision"],
                    "idempotency_key": "buff",
                },
            )
            campaign, actor = await world.snapshot()
            within_request = make_request(
                campaign, actor, action_id="hoist-with-buff", weight_lb=500
            )
            within = await world.call("adventuring_gear_action", within_request)
            assert within["hoist"]["status"] == "within_capacity"
            assert within["hoist"]["normal_lift_capacity_lb"] == 180
            assert within["hoist"]["block_and_tackle_capacity_lb"] == 720
            assert within["hoist"]["carrying_capacity_modifiers"] == [
                {
                    "source_spell_id": "dnd5e.content.srd2014.spell.enhance-ability",
                    "effect": "bulls_strength",
                    "multiplier": 2,
                }
            ]
            after_within = await world.snapshot()
            assert after_within[0]["revision"] == campaign["revision"] + 1
            assert await world.call("adventuring_gear_action", within_request) == within
            assert await world.snapshot() == after_within

            await world.call(
                "character_state_change",
                {
                    "character_id": world.aid,
                    "action": "effect_add",
                    "payload": {
                        "effect": {
                            "id": "unknown-capacity-rule",
                            "name": "Unknown capacity modifier",
                            "kind": "custom",
                            "source": "unreviewed",
                            "active": True,
                            "duration": {"period": "minute", "remaining": 1},
                            "changes": [
                                {
                                    "path": "carrying_capacity.override",
                                    "mode": "set",
                                    "value": 5,
                                }
                            ],
                        }
                    },
                    "expected_revision": after_within[1]["revision"],
                    "idempotency_key": "unknown-capacity-rule",
                },
            )
            campaign, actor = await world.snapshot()
            unknown_request = make_request(
                campaign, actor, action_id="hoist-unknown-capacity", weight_lb=1
            )
            before_unknown = await world.snapshot()
            with pytest.raises(Exception, match="unrecognized carrying-capacity modifier"):
                await world.call("adventuring_gear_action", unknown_request)
            assert await world.snapshot() == before_unknown
        finally:
            world.close()

    asyncio.run(run())
