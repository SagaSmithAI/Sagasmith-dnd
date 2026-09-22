import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import add_inventory_item, default_character_sheet
from test_character_batch_query_mcp import _call, _config

from sagasmith_dnd_mcp.server import create_server


def test_sheet_replacement_cannot_bypass_hand_capacity(tmp_path: Path):
    async def exercise():
        server = create_server(_config(tmp_path))
        campaign = await _call(server, "campaign_create", {
            "name": "Hand capacity", "edition": "2014", "idempotency_key": "campaign",
        })
        sheet = default_character_sheet()
        for item_id, kind in (("sword", "weapon"), ("spear", "weapon"), ("shield", "shield")):
            sheet, _ = add_inventory_item(sheet, {
                "id": item_id, "name": item_id, "kind": kind,
                "mechanics": {"ac_bonus": 2} if kind == "shield" else {},
            })
        actor = await _call(server, "character_create_from", {
            "mode": "direct", "payload": {
                "campaign_id": campaign["id"], "name": "Fighter", "sheet": sheet,
            }, "idempotency_key": "actor",
        })
        invalid = deepcopy(actor["sheet"])
        for item, slot in zip(invalid["inventory"]["items"], ("main_hand", "off_hand", "shield")):
            item.update(equipped=True, equipped_slot=slot)
            invalid["inventory"]["equipment_slots"][slot] = item["id"]
        with pytest.raises(Exception, match="exceed functional hands"):
            await _call(server, "character_sheet_replace", {
                "character_id": actor["id"], "sheet": invalid,
                "expected_revision": actor["revision"], "idempotency_key": "invalid-replacement",
            })
        after = await _call(server, "character_query", {
            "view": "get", "payload": {"character_id": actor["id"]},
        })
        assert after["revision"] == actor["revision"]
        assert after["sheet"] == actor["sheet"]
        equipped = await _call(server, "inventory_change", {
            "owner": "character", "owner_id": actor["id"], "action": "equip",
            "payload": {"item_id": "sword", "slot": "main_hand"},
            "expected_revision": after["revision"], "idempotency_key": "equip",
        })
        with pytest.raises(Exception, match="payload.slot is required"):
            await _call(server, "inventory_change", {
                "owner": "character", "owner_id": actor["id"], "action": "equip",
                "payload": {"item_id": "sword"},
                "expected_revision": equipped["revision"], "idempotency_key": "missing-slot",
            })
        stowed = await _call(server, "inventory_change", {
            "owner": "character", "owner_id": actor["id"], "action": "equip",
            "payload": {"item_id": "sword", "slot": None},
            "expected_revision": equipped["revision"], "idempotency_key": "stow",
        })
        assert stowed["sheet"]["inventory"]["equipment_slots"]["main_hand"] is None
        sword = next(i for i in stowed["sheet"]["inventory"]["items"] if i["id"] == "sword")
        assert not sword["equipped"]

    asyncio.run(exercise())
