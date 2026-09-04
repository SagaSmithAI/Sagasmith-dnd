from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_core import CampaignService, CharacterService, CharacterStateUpdate
from sagasmith_core.database import Database, sqlite_database_url
from sagasmith_core.idempotency import IdempotencyService, IdempotencyWrite
from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    equip_inventory_item,
)
from test_official_expansions_mcp import _call, _config

from sagasmith_dnd_mcp.random_state import RandomStateMutationService
from sagasmith_dnd_mcp.server import close_server, create_server


def _weapon() -> dict:
    return {
        "id": "custody-sword",
        "name": "Custody Sword",
        "kind": "weapon",
        "mechanics": {
            "category": "simple",
            "attack_type": "melee",
            "attack_ability": "strength",
            "damage_formula": "1d8",
            "damage_type": "slashing",
            "properties": [],
        },
    }


async def _snapshot(server, campaign_id: str, actor_ids: list[str]) -> tuple[dict, list[dict]]:
    campaign = await _call(
        server, "campaign_query", {"view": "get", "payload": {"campaign_id": campaign_id}}
    )
    actors = [
        await _call(
            server, "character_query", {"view": "get", "payload": {"character_id": actor_id}}
        )
        for actor_id in actor_ids
    ]
    return campaign, actors


@pytest.mark.fresh_database
def test_ground_custody_rejects_stale_owner_transfer_and_remove(tmp_path: Path) -> None:
    async def exercise() -> None:
        workspace = Path(__file__).resolve().parents[3]
        config = replace(
            _config(tmp_path / "seed"), auto_seed_rules=False, dnd_skills_dir=workspace / "skills"
        )
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Custody fail closed", "edition": "2014", "idempotency_key": "campaign"},
            )
            actors = {}
            for name in ("A", "B", "C"):
                sheet = default_character_sheet()
                if name == "A":
                    sheet, item_id = add_inventory_item(sheet, _weapon())
                    sheet = equip_inventory_item(sheet, item_id, "main_hand")
                actors[name] = await _call(
                    server,
                    "character_create_from",
                    {
                        "mode": "direct",
                        "payload": {"campaign_id": campaign["id"], "name": name, "sheet": sheet},
                        "idempotency_key": f"actor-{name}",
                    },
                )
            actor_a = actors["A"]
            campaign_now, actor_snapshots = await _snapshot(
                server, campaign["id"], [actors["A"]["id"], actors["B"]["id"], actors["C"]["id"]]
            )
            before_drop = (campaign_now, actor_snapshots)
            await _call(
                server,
                "inventory_transfer",
                {
                    "mode": "character_to_ground",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "character_id": actor_a["id"],
                        "expected_campaign_revision": campaign_now["revision"],
                        "expected_character_revision": actor_a["revision"],
                    },
                    "principal_id": "system:local",
                    "idempotency_key": "drop-sword",
                },
            )
            after_drop = await _snapshot(
                server, campaign["id"], [actors["A"]["id"], actors["B"]["id"], actors["C"]["id"]]
            )
            ground_id = after_drop[0]["state"]["ground_items"][0]["id"]
            await _call(
                server,
                "inventory_transfer",
                {
                    "mode": "ground_to_character",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "character_id": actors["B"]["id"],
                        "ground_id": ground_id,
                        "expected_campaign_revision": after_drop[0]["revision"],
                        "expected_character_revision": after_drop[1][1]["revision"],
                        "spatial_facts": {
                            "decision_id": "dm-reach",
                            "reason": "B can reach the dropped item in this scene.",
                            "campaign_revision": after_drop[0]["revision"],
                            "can_reach_ground_item": True,
                        },
                    },
                    "principal_id": "system:local",
                    "idempotency_key": "pickup-sword",
                },
            )
            state_before_bad = await _snapshot(
                server, campaign["id"], [actors["A"]["id"], actors["B"]["id"], actors["C"]["id"]]
            )
            b_item_id = state_before_bad[1][1]["sheet"]["inventory"]["items"][0]["id"]
            assert state_before_bad[1][0]["sheet"]["inventory"]["external_items"][0][
                "location"
            ] == {"kind": "actor", "actor_id": actors["B"]["id"], "item_id": b_item_id}
            payload = {
                "source_character_id": actors["B"]["id"],
                "target_character_id": actors["C"]["id"],
                "item_id": b_item_id,
                "expected_campaign_revision": state_before_bad[0]["revision"],
                "expected_source_revision": state_before_bad[1][1]["revision"],
                "expected_target_revision": state_before_bad[1][2]["revision"],
            }
            with pytest.raises(ToolError, match="missing physical item"):
                await _call(
                    server,
                    "inventory_transfer",
                    {
                        "mode": "character_to_character",
                        "payload": payload,
                        "idempotency_key": "bad-b-c",
                    },
                )
            assert (
                await _snapshot(
                    server,
                    campaign["id"],
                    [actors["A"]["id"], actors["B"]["id"], actors["C"]["id"]],
                )
                == state_before_bad
            )
            with pytest.raises(ToolError, match="missing physical item"):
                await _call(
                    server,
                    "inventory_change",
                    {
                        "owner": "character",
                        "action": "remove",
                        "owner_id": actors["B"]["id"],
                        "payload": {"item_id": b_item_id},
                        "expected_revision": state_before_bad[1][1]["revision"],
                        "idempotency_key": "bad-remove",
                    },
                )
            assert (
                await _snapshot(
                    server,
                    campaign["id"],
                    [actors["A"]["id"], actors["B"]["id"], actors["C"]["id"]],
                )
                == state_before_bad
            )
            assert before_drop != after_drop
            # Bypass the server preflight deliberately: even the raw adapter
            # must roll back documents, audit group and exact replay receipt.
            database = Database(sqlite_database_url(config.database_path))
            try:
                stored_campaign = CampaignService(database).get(campaign["id"])
                stored_b = CharacterService(database).get(actors["B"]["id"])
                broken_sheet = deepcopy(stored_b.sheet)
                broken_sheet["inventory"]["items"] = []
                changed_state = deepcopy(stored_campaign.state)
                changed_state["custody_rollback_probe"] = True
                receipt_attempts = []

                def attempted_response(revisions):
                    receipt_attempts.append(len(revisions))
                    return {"status": "should-not-commit"}

                with pytest.raises(ValueError, match="missing physical item"):
                    RandomStateMutationService(database).replace(
                        campaign["id"],
                        campaign_state=changed_state,
                        character_updates=[
                            CharacterStateUpdate(
                                stored_b.id, broken_sheet, stored_b.notes, stored_b.revision
                            )
                        ],
                        expected_campaign_revision=stored_campaign.revision,
                        operation="test.custody-rollback",
                        idempotency_key="raw-custody-reject",
                        idempotency_write=IdempotencyWrite(
                            scope="custody-test", payload={}, response=attempted_response
                        ),
                    )
                assert receipt_attempts and receipt_attempts[0] > 0
                assert (
                    IdempotencyService(database).lookup("custody-test", "raw-custody-reject", {})
                    is None
                )
                assert not IdempotencyService(database).mutation_committed(
                    campaign["id"], "raw-custody-reject"
                )
                assert (
                    await _snapshot(
                        server, campaign["id"], [actors[name]["id"] for name in ("A", "B", "C")]
                    )
                    == state_before_bad
                )
            finally:
                database.dispose()
        finally:
            close_server(server)

    asyncio.run(exercise())
