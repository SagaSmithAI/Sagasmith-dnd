import asyncio
from copy import deepcopy

import pytest
from sagasmith_dnd.character_schema import derive_character_sheet
from sagasmith_dnd.statblocks import parse_2014_statblock
from test_combat_transaction_boundaries_mcp import _call, _config
from test_statblock_import_mcp import COMMONER

from sagasmith_dnd_mcp.server import create_server


def test_legacy_armor_sync_preserves_live_combat_and_replays(tmp_path):
    async def exercise():
        server = create_server(_config(tmp_path))
        campaign = await _call(server, "campaign_create", {
            "name": "Armor migration", "edition": "2014", "idempotency_key": "campaign",
        })
        sheet = parse_2014_statblock(
            COMMONER.replace("**Armor Class** 10", "**Armor Class** 16 (chain shirt, shield)"),
            source_key="rule-source:test-commoner",
        ).sheet
        sheet["traits"]["proficiencies"]["armor"] = []
        sheet["combat"]["hp"]["value"] = 2
        assert not derive_character_sheet(sheet)["armor_proficiency"]["proficient"]
        actors = []
        for index in range(2):
            actors.append(await _call(server, "character_create_from", {
                "mode": "direct", "payload": {
                    "campaign_id": campaign["id"], "character_type": "monster",
                    "name": f"Guard {index}", "sheet": sheet,
                }, "idempotency_key": f"actor-{index}",
            }))
        campaign = await _call(server, "campaign_query", {
            "view": "get", "payload": {"campaign_id": campaign["id"]},
        })
        phase = await _call(server, "game_phase", {
            "campaign_id": campaign["id"], "action": "set", "tool_profile": "play",
            "expected_revision": campaign["revision"], "idempotency_key": "phase",
        })
        await _call(server, "combat_start", {
            "campaign_id": campaign["id"], "positioning_mode": "agent",
            "participant_ids": [actor["id"] for actor in actors],
            "participant_config": [{"actor_id": actor["id"], "initiative": 20 - index * 10}
                                   for index, actor in enumerate(actors)],
            "expected_revision": phase["campaign_revision"], "idempotency_key": "start",
        })
        before = await _call(server, "campaign_query", {
            "view": "get", "payload": {"campaign_id": campaign["id"]},
        })
        current = await _call(server, "character_query", {
            "view": "get", "payload": {"character_id": actors[0]["id"]},
        })
        args = {"character_id": current["id"], "action": "statblock_proficiency_sync",
                "payload": {"reason": "Repair omitted printed equipment proficiency"},
                "expected_revision": current["revision"], "idempotency_key": "sync"}
        for invalid in ({"reason": "test", "armor": ["all armor"]}, {}):
            with pytest.raises(Exception):
                await _call(server, "character_state_change", {**args, "payload": invalid})
        with pytest.raises(Exception, match="supports only statblock_proficiency_sync"):
            await _call(server, "character_state_change", {
                **args, "action": "heal", "payload": {"amount": 4},
            })
        repaired = await _call(server, "character_state_change", args)
        assert await _call(server, "character_state_change", args) == repaired
        result = repaired["character"]["sheet"]
        expected = deepcopy(current["sheet"])
        expected["traits"]["proficiencies"]["armor"] = ["Chain Shirt", "Shield"]
        assert result == expected
        assert derive_character_sheet(result)["armor_proficiency"]["proficient"]
        after = await _call(server, "campaign_query", {
            "view": "get", "payload": {"campaign_id": campaign["id"]},
        })
        assert after["state"]["combat"] == before["state"]["combat"]
        assert after["state"]["random_stream"] == before["state"]["random_stream"]
        attack = await _call(server, "combat_resolve_attack", {
            "campaign_id": campaign["id"], "actor_id": current["id"],
            "target_id": actors[1]["id"],
            "action": {"weapon_id": "club", "context": {"spatial_facts": {
                "decision_id": "adjacent-guards", "reason": "The guards stand adjacent.",
                "targetable": True, "in_range": True, "cover_degree": "none",
                "attacker_vision": {
                    "distance_ft": 5, "illumination": "bright", "obscuration": "none",
                    "magical_darkness": False, "opaque_boundary": False,
                    "scene_ref": "fixture:adjacent-guards",
                    "scene_excerpt": "The guards see each other in bright light.",
                },
                "target_vision": {
                    "distance_ft": 5, "illumination": "bright", "obscuration": "none",
                    "magical_darkness": False, "opaque_boundary": False,
                    "scene_ref": "fixture:adjacent-guards",
                    "scene_excerpt": "The guards see each other in bright light.",
                },
                "target_within_5_ft": True,
            }}},
            "expected_revision": after["revision"], "idempotency_key": "attack-after-sync",
        })
        assert attack["roll_mode"] == "normal"

    asyncio.run(exercise())
