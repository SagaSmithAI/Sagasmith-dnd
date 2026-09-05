from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    equip_inventory_item,
)
from sagasmith_dnd.lifecycle import (
    apply_raise_dead_to_sheet,
    reduce_revival_ordeal_after_long_rest,
)
from sagasmith_dnd.random_stream import CampaignRandomStream, use_random_stream
from test_chase_mcp import _call, _config

from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import finalize_and_activate_module


def _sheet(case: str) -> dict:
    value = default_character_sheet()
    value["edition"] = "2014"
    value["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
    if case == "poisoned":
        value["conditions"] = ["poisoned"]
    elif case == "armor":
        value, item_id = add_inventory_item(value, {
            "id": "chain-mail", "name": "Chain mail", "kind": "armor", "weight_oz": 880,
            "mechanics": {"base_ac": 16, "category": "heavy", "dexterity_mode": "none",
                          "strength_requirement": 13, "stealth_disadvantage": True},
        })
        value = equip_inventory_item(value, item_id, "armor")
    elif case == "heavy_variant":
        value["inventory"]["encumbrance"]["mode"] = "variant"
        value, _ = add_inventory_item(value, {
            "id": "load", "name": "Load", "kind": "equipment", "weight_oz": 1800,
        })
    elif case.startswith("revival_"):
        value["conditions"] = ["dead"]
        value["combat"]["hp"]["value"] = 0
        value = apply_raise_dead_to_sheet(
            value, elapsed_days=1, soul_willing=True, body_intact=True,
            source_ref="dnd5e.content.srd2014.spell.raise-dead", source_actor_id="cleric",
        )["sheet"]
        for _ in range(int(case.rsplit("_", 1)[1])):
            value = reduce_revival_ordeal_after_long_rest(value)["sheet"]
    return value


async def _raw(server, name: str, arguments: dict) -> dict:
    _, result = await server.call_tool(name, arguments)
    return result


async def _snapshot(server, campaign_id: str) -> dict:
    return await _call(server, "campaign_query", {
        "view": "get", "payload": {"campaign_id": campaign_id},
    })


async def _create_actor(server, campaign_id: str, name: str, sheet: dict) -> dict:
    return await _call(server, "character_create_from", {
        "mode": "direct", "payload": {
            "campaign_id": campaign_id, "name": name, "sheet": sheet,
        }, "idempotency_key": name,
    })


def _assert_persisted_state(stored: dict, case: str) -> None:
    sheet = stored["sheet"]
    if case == "poisoned":
        assert "poisoned" in sheet["conditions"]
    elif case in {"armor", "heavy_variant"}:
        assert "dexterity" in stored["derived"]["equipment_penalties"][
            "check_disadvantage_abilities"
        ]
    elif case.startswith("revival_"):
        effect = next(
            item for item in sheet["effects"] if item["kind"] == "revival_ordeal"
        )
        change = next(
            item for item in effect["changes"] if item["path"] == "rolls.ability_check.bonus"
        )
        assert change["value"] == (-4 if case == "revival_0" else 0)


@pytest.mark.parametrize(
    "case", ["ordinary", "poisoned", "armor", "heavy_variant", "revival_0", "revival_4"]
)
@pytest.mark.parametrize("joining", [False, True])
def test_public_2014_initiative_applies_persisted_shared_modifiers(
    tmp_path: Path, case: str, joining: bool
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(server, "campaign_create", {
                "name": "Initiative shared modifiers", "edition": "2014",
                "idempotency_key": "campaign",
            })
            campaign_id = campaign["id"]
            observer = await _create_actor(server, campaign_id, "observer", _sheet("ordinary"))
            actor = await _create_actor(server, campaign_id, "subject", _sheet(case))
            stored = await _call(server, "character_query", {
                "view": "get", "payload": {"character_id": actor["id"]},
            })
            _assert_persisted_state(stored, case)
            current = await _snapshot(server, campaign_id)
            phase = await _call(server, "game_phase", {
                "campaign_id": campaign_id, "action": "set", "tool_profile": "play",
                "expected_revision": current["revision"], "idempotency_key": "play",
            })
            start_args = {
                "campaign_id": campaign_id, "positioning_mode": "agent",
                "participant_ids": [observer["id"]],
                "participant_config": [{
                    "actor_id": observer["id"], "initiative": 25, "tie_breaker": 0,
                }],
                "expected_revision": phase["campaign_revision"], "idempotency_key": "start",
            }
            if joining:
                await _call(server, "combat_start", start_args)
                tool = "combat_join"
                arguments = {
                    "campaign_id": campaign_id, "actor_id": actor["id"],
                    "participant_config": {"tie_breaker": 1}, "idempotency_key": "join",
                }
            else:
                tool = "combat_start"
                arguments = {
                    **start_args,
                    "participant_ids": [observer["id"], actor["id"]],
                    "participant_config": [
                        {"actor_id": observer["id"], "initiative": 25, "tie_breaker": 0},
                        {"actor_id": actor["id"], "tie_breaker": 1},
                    ],
                }
            before = await _snapshot(server, campaign_id)
            arguments["expected_revision"] = before["revision"]
            with pytest.raises(ToolError, match="revision conflict"):
                await _raw(server, tool, {**arguments, "expected_revision": before["revision"] - 1})
            assert await _snapshot(server, campaign_id) == before
            stream = CampaignRandomStream.from_campaign_state(
                campaign_id, before["state"], operation=tool,
                idempotency_key=arguments["idempotency_key"], campaign_revision=before["revision"],
            )
            with use_random_stream(stream):
                result = await _raw(server, tool, arguments)
            after = await _snapshot(server, campaign_id)
            roster = after["state"]["combat"]["reinforcements" if joining else "combatants"]
            rolled = next(item for item in roster if item["actor_id"] == actor["id"])
            expected_disadvantage = case in {"poisoned", "armor", "heavy_variant"}
            expected_bonus = -4 if case == "revival_0" else 0
            assert len(rolled["initiative_roll"]["rolls"]) == (2 if expected_disadvantage else 1)
            assert rolled["initiative_bonus"] == expected_bonus
            expected_natural = (
                min(rolled["initiative_roll"]["rolls"])
                if expected_disadvantage
                else rolled["initiative_roll"]["natural"]
            )
            assert rolled["initiative"] == expected_natural + expected_bonus
            assert rolled["initiative_roll"]["roll_mode"] == (
                "disadvantage" if expected_disadvantage else "normal"
            )
            assert stream.draw_count == (2 if expected_disadvantage else 1)
            assert all(item.get("position") is None for item in roster)
            assert await _raw(server, tool, arguments) == result
            assert await _snapshot(server, campaign_id) == after
            close_server(server)
            server = create_server(_config(tmp_path))
            assert await _snapshot(server, campaign_id) == after
            assert await _raw(server, tool, arguments) == result
        finally:
            close_server(server)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "case", ["ordinary", "poisoned", "armor", "heavy_variant", "revival_0", "revival_4"]
)
def test_public_2014_chase_start_applies_the_same_initiative_contract(
    tmp_path: Path, case: str
) -> None:
    async def exercise() -> None:
        source = tmp_path / "initiative.md"
        source.write_text(
            "# Chapter Four\n\n## Street Chase\n\n"
            "The runner is 60 feet ahead when the pursuer begins the chase.\n",
            encoding="utf-8",
        )
        server = create_server(_config(tmp_path, tmp_path))
        try:
            campaign = await _call(server, "campaign_create", {
                "name": "Chase initiative", "edition": "2014", "idempotency_key": "campaign",
            })
            campaign_id = campaign["id"]
            staged = await _call(server, "module_draft", {
                "campaign_id": campaign_id, "action": "start", "payload": {
                    "source_path": str(source), "source_key": "initiative", "title": "Initiative",
                }, "idempotency_key": "stage",
            })
            await finalize_and_activate_module(
                _call, server, campaign_id, staged, source_key="initiative",
                title="Initiative", portable_id="dnd5e.module.initiative",
            )
            hit = (await _call(server, "module_search", {
                "campaign_id": campaign_id, "query": "runner pursuer chase",
            }))[0]
            expanded = await _call(server, "module_expand", {"chunk_id": hit["id"]})
            source_ref = {
                "module_id": expanded["module"]["id"], "scene_id": expanded["scene"]["id"],
                "chunk_id": expanded["chunk_id"], "page_start": expanded["page_start"],
                "page_end": expanded["page_end"], "heading_path": expanded["heading_path"],
                "content_sha256": hashlib.sha256(expanded["content"].encode()).hexdigest(),
            }
            runner = await _create_actor(server, campaign_id, "runner", _sheet("ordinary"))
            pursuer = await _create_actor(server, campaign_id, "pursuer", _sheet(case))
            stored = await _call(server, "character_query", {
                "view": "get", "payload": {"character_id": pursuer["id"]},
            })
            _assert_persisted_state(stored, case)
            current = await _snapshot(server, campaign_id)
            phase = await _call(server, "game_phase", {
                "campaign_id": campaign_id, "action": "set", "tool_profile": "play",
                "expected_revision": current["revision"], "idempotency_key": "play",
            })
            arguments = {
                "campaign_id": campaign_id, "action": "start", "payload": {
                    "participant_ids": [runner["id"], pursuer["id"]], "quarry_ids": [runner["id"]],
                    "initial_distance_ft": 60, "scene_id": expanded["scene"]["id"],
                    "source_ref": source_ref,
                    "source_excerpt": (
                        "The runner is 60 feet ahead when the pursuer begins the chase."
                    ),
                    "participant_config": [
                        {"actor_id": runner["id"], "initiative": 25, "tie_breaker": 0},
                        {"actor_id": pursuer["id"], "tie_breaker": 1},
                    ],
                },
                "expected_revision": phase["campaign_revision"], "idempotency_key": "chase",
            }
            before = await _snapshot(server, campaign_id)
            with pytest.raises(ToolError, match="revision conflict"):
                await _raw(
                    server, "chase",
                    {**arguments, "expected_revision": before["revision"] - 1},
                )
            assert await _snapshot(server, campaign_id) == before
            arguments["expected_revision"] = before["revision"]
            stream = CampaignRandomStream.from_campaign_state(
                campaign_id, before["state"], operation="chase", idempotency_key="chase",
                campaign_revision=before["revision"],
            )
            with use_random_stream(stream):
                result = await _raw(server, "chase", arguments)
            after = await _snapshot(server, campaign_id)
            rolled = next(
                item for item in after["state"]["chase"]["participants"]
                if item["actor_id"] == pursuer["id"]
            )
            expected_disadvantage = case in {"poisoned", "armor", "heavy_variant"}
            expected_bonus = -4 if case == "revival_0" else 0
            assert len(rolled["initiative_roll"]["rolls"]) == (2 if expected_disadvantage else 1)
            expected_natural = (
                min(rolled["initiative_roll"]["rolls"])
                if expected_disadvantage
                else rolled["initiative_roll"]["natural"]
            )
            assert rolled["initiative"] == expected_natural + expected_bonus
            assert rolled["initiative_bonus"] == expected_bonus
            assert rolled["initiative_roll"]["roll_mode"] == (
                "disadvantage" if expected_disadvantage else "normal"
            )
            assert stream.draw_count == (2 if expected_disadvantage else 1)
            assert all(
                item.get("position") is None
                for item in after["state"]["chase"]["participants"]
            )
            assert await _raw(server, "chase", arguments) == result
            assert await _snapshot(server, campaign_id) == after
            close_server(server)
            server = create_server(_config(tmp_path, tmp_path))
            assert await _snapshot(server, campaign_id) == after
            assert await _raw(server, "chase", arguments) == result
        finally:
            close_server(server)

    asyncio.run(exercise())
