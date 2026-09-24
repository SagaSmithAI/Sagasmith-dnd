"""Magnifying Glass checks receive source-bound advantage on reviewed small items."""

from __future__ import annotations

import asyncio
import sys
import types
from copy import deepcopy
from pathlib import Path

import pytest

_test_package = types.ModuleType("tests")
_test_package.__path__ = [str(Path(__file__).parent)]
sys.modules.setdefault("tests", _test_package)

from tests.test_adventuring_gear_strength_mcp import (  # noqa: E402
    DOOR_EXCERPT,
    World,
    review_object,
    setup,
)


def test_magnifying_glass_advantage_is_engine_resolved_and_replayed(tmp_path):
    async def run():
        world: World = await setup(tmp_path)
        try:
            campaign, actor = await world.snapshot()
            added = await world.call(
                "inventory_change",
                {
                    "owner": "character", "action": "add", "owner_id": world.aid,
                    "payload": {"item": {
                        "id": "magnifying-glass-1", "name": "Magnifying Glass",
                        "source_key": "dnd5e.content.srd2014.item.magnifying-glass",
                        "quantity": 1,
                    }},
                    "expected_revision": actor["revision"],
                    "idempotency_key": "add-magnifying-glass",
                },
            )
            actor = added["character"]
            tiny = deepcopy(world.door)
            tiny.update(id="tiny-inscribed-token", name="Inscribed token", size="tiny")
            await review_object(world, tiny, door=True, leverage=False, excerpt=DOOR_EXCERPT)

            campaign, actor = await world.snapshot()
            target = {"id": tiny["id"], "scene_id": tiny["scene_id"]}

            def request(action_id: str):
                return {
                    "campaign_id": world.cid, "action_id": action_id,
                    "item_id": "magnifying-glass-1", "intent": "inspect",
                    "source_ref": "bundled:srd2014/04_Equipment/Adventuring_Gear.md",
                    "actor_id": world.aid, "expected_actor_revision": actor["revision"],
                    "expected_revision": campaign["revision"], "idempotency_key": action_id,
                    "target_object": deepcopy(target),
                    "action_context": {"purpose": "inspect", "ability": "intelligence", "dc": 15},
                }

            before = await world.snapshot()
            forged = request("magnifier-forged-advantage")
            forged["action_context"]["advantage"] = False
            with pytest.raises(Exception):
                await world.call("adventuring_gear_action", forged)
            assert await world.snapshot() == before

            wrong_scene = request("magnifier-wrong-scene")
            wrong_scene["target_object"]["scene_id"] = "unrelated-scene"
            with pytest.raises(Exception):
                await world.call("adventuring_gear_action", wrong_scene)
            assert await world.snapshot() == before

            stale = request("magnifier-stale")
            stale["expected_revision"] -= 1
            with pytest.raises(Exception, match="revision conflict"):
                await world.call("adventuring_gear_action", stale)
            assert await world.snapshot() == before

            action = request("magnifier-inspect")
            result = await world.call("adventuring_gear_action", action)
            assert result["status"] == "committed"
            assert result["check"]["source_item_modifier"]["advantage"] is True
            assert len(result["check"]["rolls"]) == 2
            assert result["receipt"]["profile_approval_digest"]
            after = await world.snapshot()
            assert after[0]["revision"] == campaign["revision"] + 1
            assert await world.call("adventuring_gear_action", action) == result
            assert await world.snapshot() == after

            medium = deepcopy(tiny)
            medium.update(
                id="medium-detailed-unknown", name="Detailed-looking object", size="medium"
            )
            await review_object(world, medium, door=True, leverage=False, excerpt=DOOR_EXCERPT)
            campaign, actor = await world.snapshot()
            blocked = {
                **request("magnifier-unmodeled-detail"),
                "expected_revision": campaign["revision"],
                "expected_actor_revision": actor["revision"],
                "target_object": {"id": medium["id"], "scene_id": medium["scene_id"]},
                "idempotency_key": "magnifier-unmodeled-detail",
                "action_id": "magnifier-unmodeled-detail",
            }
            before_blocked = await world.snapshot()
            with pytest.raises(Exception, match="highly detailed status is not represented"):
                await world.call("adventuring_gear_action", blocked)
            assert await world.snapshot() == before_blocked
        finally:
            world.close()

    asyncio.run(run())
