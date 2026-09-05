import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    equip_inventory_item,
)
from test_ground_items_mcp import _raw, _snapshot, _weapon
from test_official_expansions_mcp import _call, _config
from test_rest_hit_dice_mcp import _ensure_rest_clock

from sagasmith_dnd_mcp.server import close_server, create_server


@pytest.mark.parametrize("rest_mode", ["party_rest", "stable_recovery"])
def test_another_creatures_completed_short_rest_ends_the_old_attunement(tmp_path, rest_mode):
    async def exercise():
        config = replace(
            _config(tmp_path),
            auto_seed_rules=False,
            dnd_skills_dir=Path(__file__).resolve().parents[3] / "skills",
        )
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Physical item attunement",
                    "edition": "2014",
                    "idempotency_key": "campaign",
                },
            )
            sheet = default_character_sheet()
            sheet, item_id = add_inventory_item(sheet, {**_weapon(), "attunement": "attuned"})
            sheet = equip_inventory_item(sheet, item_id, "main_hand")
            patient_sheet = default_character_sheet()
            patient_sheet["combat"]["hp"] = {"value": 0, "max": 10, "temp": 0}
            patient_sheet["conditions"] = ["unconscious", "stable"]
            actors = []
            for name, actor_sheet in [
                ("Old owner", sheet),
                ("New carrier", default_character_sheet()),
                ("Patient", patient_sheet),
            ]:
                actors.append(
                    await _call(
                        server,
                        "character_create_from",
                        {
                            "mode": "direct",
                            "payload": {
                                "campaign_id": campaign["id"],
                                "name": name,
                                "sheet": actor_sheet,
                            },
                            "idempotency_key": name,
                        },
                    )
                )
            actor_ids = [actor["id"] for actor in actors]
            old_id, carrier_id, patient_id = actor_ids
            before = await _snapshot(server, campaign["id"], actor_ids)
            await _call(
                server,
                "inventory_transfer",
                {
                    "mode": "character_to_ground",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "character_id": old_id,
                        "expected_campaign_revision": before[0]["revision"],
                        "expected_character_revision": before[1][0]["revision"],
                    },
                    "idempotency_key": "drop",
                },
            )
            dropped = await _snapshot(server, campaign["id"], actor_ids)
            await _call(
                server,
                "inventory_transfer",
                {
                    "mode": "ground_to_character",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "character_id": carrier_id,
                        "ground_id": dropped[0]["state"]["ground_items"][0]["id"],
                        "slot": "main_hand",
                        "expected_campaign_revision": dropped[0]["revision"],
                        "expected_character_revision": dropped[1][1]["revision"],
                        "spatial_facts": {
                            "decision_id": "reach",
                            "reason": "The new carrier can reach the dropped item.",
                            "campaign_revision": dropped[0]["revision"],
                            "can_reach_ground_item": True,
                        },
                    },
                    "idempotency_key": "pickup",
                },
            )
            await _ensure_rest_clock(server, campaign["id"], "bond")
            picked = await _snapshot(server, campaign["id"], actor_ids)
            assert (
                picked[1][0]["sheet"]["inventory"]["external_items"][0]["attunement"] == "attuned"
            )
            assert picked[1][1]["sheet"]["inventory"]["items"][0]["attunement"] == "required"
            request = {
                "campaign_id": campaign["id"],
                "action": "party_rest",
                "payload": {
                    "rest_type": "short_rest",
                    "duration_minutes": 60,
                    "members": [
                        {
                            "character_id": carrier_id,
                            "expected_revision": picked[1][1]["revision"],
                            "attune_item_id": item_id,
                        }
                    ],
                },
                "expected_revision": picked[0]["revision"],
                "idempotency_key": "rest-no-confirmation",
            }
            rest_members_key = "members"
            if rest_mode == "stable_recovery":
                request["action"] = "stable_recovery"
                request["payload"] = {
                    "members": [
                        {"character_id": patient_id, "expected_revision": picked[1][2]["revision"]}
                    ],
                    "resting_members": request["payload"]["members"],
                }
                rest_members_key = "resting_members"
            pending = await _call(server, "campaign_change", request)
            assert pending["status"] == "pending_ruling"
            assert await _snapshot(server, campaign["id"], actor_ids) == picked
            request = deepcopy(request)
            request["payload"][rest_members_key][0]["attunement_prerequisite_confirmed"] = True
            request["idempotency_key"] = "rest-attune"
            result = await _raw(server, "campaign_change", request)
            after = await _snapshot(server, campaign["id"], actor_ids)
            old_ref = after[1][0]["sheet"]["inventory"]["external_items"][0]
            assert old_ref["attunement"] == "required"
            assert old_ref["location"] == {
                "kind": "actor",
                "actor_id": carrier_id,
                "item_id": item_id,
            }
            assert after[1][1]["sheet"]["inventory"]["items"][0]["attunement"] == "attuned"
            assert after[1][0]["revision"] == picked[1][0]["revision"] + 1
            assert after[1][1]["revision"] == picked[1][1]["revision"] + 1
            if rest_mode == "stable_recovery":
                assert after[1][2]["sheet"]["combat"]["hp"]["value"] == 1
            assert await _raw(server, "campaign_change", request) == result
            close_server(server)
            server = create_server(config)
            assert await _raw(server, "campaign_change", request) == result
            assert await _snapshot(server, campaign["id"], actor_ids) == after
        finally:
            close_server(server)

    asyncio.run(exercise())
