from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_core import CharacterService, Database
from sagasmith_core.database import sqlite_database_url
from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    equip_inventory_item,
)
from sagasmith_dnd.standard_spell_ids import CORE_INVISIBILITY_SPELL_ID
from test_ground_actor_creation_mcp import _creation_rows
from test_ground_items_mcp import _shield, _snapshot, _weapon
from test_official_expansions_mcp import _call, _config
from test_structured_spell_mcp import _invisibility, _slot

from sagasmith_dnd_mcp.random_state import RandomStateMutationService
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import finalize_and_activate_module


@pytest.mark.fresh_database
@pytest.mark.parametrize("entry", ["create", "replace"])
@pytest.mark.parametrize("holding", [False, True])
@pytest.mark.parametrize("prone_immune", [False, True])
def test_unconscious_prone_is_independent_of_held_items(
    tmp_path: Path, entry: str, holding: bool, prone_immune: bool
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Unconscious posture", "edition": "2014", "idempotency_key": "campaign"},
            )
            sheet, shield_id = add_inventory_item(default_character_sheet(), _shield())
            sheet = equip_inventory_item(sheet, shield_id, "shield")
            if holding:
                sheet, sword_id = add_inventory_item(sheet, _weapon())
                sheet = equip_inventory_item(sheet, sword_id, "main_hand")
            if prone_immune:
                sheet["traits"]["condition_immunities"] = ["prone"]
            if entry == "create":
                sheet["conditions"] = ["unconscious"]
            create_arguments = {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Actor", "sheet": sheet},
                "idempotency_key": "create",
            }
            created = await server.call_tool("character_create_from", create_arguments)
            actor = created[1]["result"]
            tool, arguments, result = "character_create_from", create_arguments, created
            before = await _snapshot(server, campaign["id"], [actor["id"]])
            if entry == "replace":
                replacement = deepcopy(actor["sheet"])
                replacement["conditions"] = ["unconscious"]
                tool = "character_sheet_replace"
                arguments = {
                    "character_id": actor["id"],
                    "sheet": replacement,
                    "expected_revision": actor["revision"],
                    "idempotency_key": "fall-unconscious",
                }
                result = await server.call_tool(tool, arguments)
                actor = result[1]
            after = await _snapshot(server, campaign["id"], [actor["id"]])
            assert "unconscious" in actor["sheet"]["conditions"]
            assert ("prone" in actor["sheet"]["conditions"]) is not prone_immune
            assert after[1][0]["sheet"] == actor["sheet"]
            assert actor["sheet"]["inventory"]["equipment_slots"]["shield"] == shield_id
            assert actor["sheet"]["inventory"]["equipment_slots"]["main_hand"] is None
            ground = after[0]["state"].get("ground_items", [])
            assert len(ground) == int(holding)
            if holding:
                assert ground[0]["root_item_id"] == sword_id
                assert ground[0]["location"] == {"mode": "agent", "anchor_actor_id": actor["id"]}
            elif entry == "replace":
                # Pure posture changes do not invent ground state or campaign revisions.
                assert after[0] == before[0]
            assert await server.call_tool(tool, arguments) == result
            close_server(server)
            server = create_server(config)
            assert await server.call_tool(tool, arguments) == result
            assert await _snapshot(server, campaign["id"], [actor["id"]]) == after
            if entry == "replace":
                history = await _call(
                    server, "state_revision", {"campaign_id": campaign["id"], "action": "history"}
                )
                changed = [
                    item for item in history if item["idempotency_key"] == "fall-unconscious"
                ]
                assert len({item["mutation_group_id"] for item in changed}) == 1
                assert {item["entity_type"] for item in changed} == (
                    {"character", "campaign"} if holding else {"character"}
                )
                await _call(
                    server,
                    "state_revision",
                    {
                        "campaign_id": campaign["id"],
                        "action": "undo",
                        "payload": {"expected_history_sequence": history[0]["sequence"]},
                        "idempotency_key": "undo-fall",
                    },
                )
                undone = await _snapshot(server, campaign["id"], [actor["id"]])
                assert undone[1][0]["sheet"] == before[1][0]["sheet"]
                assert undone[0]["state"] == before[0]["state"]
                history = await _call(
                    server, "state_revision", {"campaign_id": campaign["id"], "action": "history"}
                )
                await _call(
                    server,
                    "state_revision",
                    {
                        "campaign_id": campaign["id"],
                        "action": "redo",
                        "payload": {
                            "expected_history_sequence": max(
                                (item["sequence"] for item in history if item["applied"]), default=0
                            )
                        },
                        "idempotency_key": "redo-fall",
                    },
                )
                redone = await _snapshot(server, campaign["id"], [actor["id"]])
                assert redone[1][0]["sheet"] == after[1][0]["sheet"]
                assert redone[0]["state"] == after[0]["state"]
                after = redone
            awake = deepcopy(after[1][0]["sheet"])
            awake["conditions"].remove("unconscious")
            await server.call_tool(
                "character_sheet_replace",
                {
                    "character_id": actor["id"],
                    "sheet": awake,
                    "expected_revision": after[1][0]["revision"],
                    "idempotency_key": "wake",
                },
            )
            woken = await _snapshot(server, campaign["id"], [actor["id"]])
            assert "unconscious" not in woken[1][0]["sheet"]["conditions"]
            assert ("prone" in woken[1][0]["sheet"]["conditions"]) is not prone_immune
            assert woken[0] == after[0]
            assert woken[1][0]["sheet"]["inventory"] == after[1][0]["sheet"]["inventory"]
        finally:
            close_server(server)

    asyncio.run(exercise())


@pytest.mark.fresh_database
@pytest.mark.parametrize("entry", ["start", "join"])
@pytest.mark.parametrize("condition_origin", ["source", "legacy", "legacy_prone"])
@pytest.mark.parametrize("positioning_mode", ["agent", "grid"])
def test_source_unconscious_combat_entry_settles_posture_and_inventory(
    tmp_path: Path,
    entry: str,
    condition_origin: str,
    positioning_mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        source_root = tmp_path / "modules"
        source_root.mkdir()
        source = source_root / "arrival.md"
        excerpt = "The arriving guard is unconscious throughout this encounter."
        source.write_text(f"# Watch Room\n\n## Guard\n\n{excerpt}\n", encoding="utf-8")
        config = replace(_config(tmp_path), module_import_roots=(source_root,))
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Source unconscious", "edition": "2014", "idempotency_key": "campaign"},
            )
            staged = await _call(
                server,
                "module_draft",
                {
                    "campaign_id": campaign["id"],
                    "action": "start",
                    "payload": {
                        "source_path": str(source),
                        "source_key": "arrival",
                        "title": "Arrival",
                    },
                    "idempotency_key": "stage",
                },
            )
            await finalize_and_activate_module(
                _call,
                server,
                campaign["id"],
                staged,
                source_key="arrival",
                title="Arrival",
                portable_id="dnd5e.module.unconscious-arrival-test",
            )
            found = await _call(
                server,
                "module_search",
                {"campaign_id": campaign["id"], "query": "arriving guard unconscious", "top_k": 1},
            )
            expanded = await _call(server, "module_expand", {"chunk_id": found[0]["id"]})
            source_condition = {
                "condition": "unconscious",
                "duration": "encounter",
                "source_ref": expanded["source_ref"],
                "source_excerpt": excerpt,
            }
            held_sheet, sword_id = add_inventory_item(default_character_sheet(), _weapon())
            held_sheet = equip_inventory_item(held_sheet, sword_id, "main_hand")
            held_sheet["spellcasting"].update(ability="intelligence", spell_slots=_slot(2))
            held_sheet["content"]["spells"] = [_invisibility()]
            actors = []
            for name, sheet in (("Hero", default_character_sheet()), ("Guard", held_sheet)):
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
            hero, guard = actors
            cast = await _call(
                server,
                "character_action",
                {
                    "character_id": guard["id"],
                    "action": "cast_spell",
                    "payload": {
                        "spell_id": CORE_INVISIBILITY_SPELL_ID,
                        "cast_level": 2,
                        "target_character_ids": [hero["id"]],
                    },
                    "expected_revision": guard["revision"],
                    "idempotency_key": "invisibility",
                },
            )
            assert cast["result"]["automatic_effect"] == "invisibility"
            _, cast_actors = await _snapshot(server, campaign["id"], [hero["id"], guard["id"]])
            assert "invisible" in cast_actors[0]["sheet"]["conditions"]
            if condition_origin != "source":
                # Simulate a pre-upgrade persisted card, not a current public ingress.
                legacy_sheet = deepcopy(cast_actors[1]["sheet"])
                legacy_sheet["conditions"] = ["unconscious"]
                if condition_origin == "legacy_prone":
                    # The sheet normalization itself is already a no-op: only
                    # held-item custody and dependent targets still need repair.
                    legacy_sheet["conditions"] = ["prone", "unconscious"]
                    for effect in legacy_sheet["effects"]:
                        if effect["concentration"]:
                            effect["active"] = False
                            effect["ended_reason"] = "incapacitated"
                database = Database(sqlite_database_url(config.database_path))
                try:
                    CharacterService(database).update(
                        guard["id"],
                        sheet=legacy_sheet,
                        expected_revision=cast_actors[1]["revision"],
                    )
                finally:
                    database.dispose()
            current, _ = await _snapshot(server, campaign["id"], [])
            play = await _call(
                server,
                "game_phase",
                {
                    "campaign_id": campaign["id"],
                    "action": "set",
                    "tool_profile": "play",
                    "expected_revision": current["revision"],
                    "idempotency_key": "play",
                },
            )
            guard_config = {
                "actor_id": guard["id"],
                "initiative": 5,
                **(
                    {"source_conditions": [source_condition]}
                    if condition_origin == "source"
                    else {}
                ),
                **({"position": {"x": 3, "y": 2}} if positioning_mode == "grid" else {}),
            }
            arguments = {
                "campaign_id": campaign["id"],
                "positioning_mode": positioning_mode,
                **(
                    {
                        "battle_map": {"width_cells": 10, "height_cells": 10},
                        "battle_map_override_reason": "The DM establishes this open test room.",
                    }
                    if positioning_mode == "grid"
                    else {}
                ),
                "participant_ids": [actor["id"] for actor in actors]
                if entry == "start"
                else [hero["id"]],
                "participant_config": [
                    {
                        "actor_id": hero["id"],
                        "initiative": 20,
                        **({"position": {"x": 0, "y": 0}} if positioning_mode == "grid" else {}),
                    },
                    *([guard_config] if entry == "start" else []),
                ],
                "scene_id": expanded["scene"]["id"],
                "ruleset": "2014",
                "expected_revision": play["campaign_revision"],
                "idempotency_key": "start",
            }
            tool = "combat_start"
            if entry == "join":
                _, before_start_actors = await _snapshot(
                    server, campaign["id"], [hero["id"], guard["id"]]
                )
                started = await server.call_tool(tool, arguments)
                _, after_start_actors = await _snapshot(
                    server, campaign["id"], [hero["id"], guard["id"]]
                )
                assert after_start_actors[1] == before_start_actors[1]
                if condition_origin != "legacy_prone":
                    assert after_start_actors[0] == before_start_actors[0]
                tool = "combat_join"
                arguments = {
                    "campaign_id": campaign["id"],
                    "actor_id": guard["id"],
                    "participant_config": {
                        key: value for key, value in guard_config.items() if key != "actor_id"
                    },
                    "expected_revision": started[1]["campaign_revision"],
                    "idempotency_key": "join",
                }
            before_rows = _creation_rows(config.database_path)
            original_replace = RandomStateMutationService.replace
            attempts = []

            def stale_target(service, campaign_id, **kwargs):
                updates = kwargs["character_updates"]
                assert {update.character_id for update in updates} == {hero["id"], guard["id"]}
                attempts.append(True)
                kwargs["character_updates"] = [
                    replace(update, expected_revision=update.expected_revision - 1)
                    if update.character_id == hero["id"]
                    else update
                    for update in updates
                ]
                return original_replace(service, campaign_id, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(RandomStateMutationService, "replace", stale_target)
                with pytest.raises(ToolError, match="revision conflict"):
                    await server.call_tool(tool, arguments)
            assert attempts == [True]
            assert _creation_rows(config.database_path) == before_rows
            result = await server.call_tool(tool, arguments)
            after = await _snapshot(server, campaign["id"], [guard["id"]])
            assert set(after[1][0]["sheet"]["conditions"]) == {"unconscious", "prone"}
            assert after[1][0]["sheet"]["inventory"]["equipment_slots"]["main_hand"] is None
            ground = after[0]["state"]["ground_items"]
            assert len(ground) == 1 and ground[0]["root_item_id"] == sword_id
            assert ground[0]["location"] == (
                {"mode": "agent", "anchor_actor_id": guard["id"]}
                if positioning_mode == "agent"
                else {"mode": "grid", "position": {"x": 3, "y": 2}}
            )
            roster = "combatants" if entry == "start" else "reinforcements"
            combatant = next(
                item
                for item in after[0]["state"]["combat"][roster]
                if item["actor_id"] == guard["id"]
            )
            assert set(combatant["conditions"]) == {"unconscious", "prone"}
            response_combatant = next(
                item for item in result[1]["combat"][roster] if item["actor_id"] == guard["id"]
            )
            assert response_combatant["conditions"] == combatant["conditions"]
            if entry == "join":
                assert result[1]["queued"]["conditions"] == combatant["conditions"]
            _, settled_actors = await _snapshot(server, campaign["id"], [hero["id"], guard["id"]])
            assert "invisible" not in settled_actors[0]["sheet"]["conditions"]
            assert not any(
                effect["active"] and effect["concentration"]
                for effect in settled_actors[1]["sheet"]["effects"]
            )
            target = next(
                item for item in result[1]["combat"]["combatants"] if item["actor_id"] == hero["id"]
            )
            assert "invisible" not in target["conditions"]
            stored_target = next(
                item
                for item in after[0]["state"]["combat"]["combatants"]
                if item["actor_id"] == hero["id"]
            )
            assert target["conditions"] == stored_target["conditions"]
            assert await server.call_tool(tool, arguments) == result
            close_server(server)
            server = create_server(config)
            assert await server.call_tool(tool, arguments) == result
            assert await _snapshot(server, campaign["id"], [guard["id"]]) == after
            end_arguments = {
                "campaign_id": campaign["id"],
                "outcome": {"status": "interrupted", "summary": "End the source duration."},
                "expected_revision": after[0]["revision"],
                "idempotency_key": "end",
            }
            ended = await server.call_tool("combat_end", end_arguments)
            awake = await _snapshot(server, campaign["id"], [hero["id"], guard["id"]])
            assert set(awake[1][1]["sheet"]["conditions"]) == (
                {"prone"} if condition_origin == "source" else {"prone", "unconscious"}
            )
            assert awake[0]["state"]["ground_items"] == ground
            assert "invisible" not in awake[1][0]["sheet"]["conditions"]
            assert await server.call_tool("combat_end", end_arguments) == ended
            assert await _snapshot(server, campaign["id"], [hero["id"], guard["id"]]) == awake
        finally:
            close_server(server)

    asyncio.run(exercise())
