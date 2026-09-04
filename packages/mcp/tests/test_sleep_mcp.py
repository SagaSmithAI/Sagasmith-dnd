from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.standard_spell_ids import CORE_SLEEP_SPELL_ID
from test_conditional_species_saves_mcp import _selection
from test_official_expansions_mcp import _call, _config

from sagasmith_dnd_mcp.server import close_server, create_server


@pytest.mark.fresh_database
@pytest.mark.parametrize("cast_level", [1, 2])
def test_real_2014_sleep_area_pool_and_replay(tmp_path: Path, cast_level: int) -> None:
    async def exercise() -> None:
        workspace = Path(__file__).resolve().parents[3]
        config = replace(
            _config(tmp_path / "seed"),
            auto_seed_rules=True,
            dnd_skills_dir=workspace / "skills",
        )
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Sleep seed", "edition": "2014", "idempotency_key": "campaign"},
            )
            spell_catalog = await _call(
                server,
                "character_query",
                {
                    "view": "catalog",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "kind": "spell",
                        "query": "Sleep",
                    },
                },
            )
            sleep = next(item for item in spell_catalog if item["id"] == CORE_SLEEP_SPELL_ID)
            species_catalog = await _call(
                server,
                "character_query",
                {
                    "view": "catalog",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "kind": "species",
                        "query": "Elf",
                    },
                },
            )
            elf = next(item for item in species_catalog if item["name"] == "High Elf")
            half_catalog = await _call(
                server,
                "character_query",
                {
                    "view": "catalog",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "kind": "species",
                        "query": "Half-Elf",
                    },
                },
            )
            half_elf = next(item for item in half_catalog if item["name"] == "Half-Elf")

            async def new_character(name: str, sheet: dict | None = None) -> dict:
                return await _call(
                    server,
                    "character_create_from",
                    {
                        "mode": "direct",
                        "payload": {
                            "campaign_id": campaign["id"],
                            "name": name,
                            "sheet": sheet or default_character_sheet(),
                        },
                        "idempotency_key": name,
                    },
                )

            caster_sheet = default_character_sheet()
            caster_sheet["progression"]["level"] = 3
            caster_sheet["progression"]["classes"] = [
                {"name": "Bard", "level": 3, "subclass": "", "hit_die": 8}
            ]
            caster_sheet["spellcasting"].update(
                ability="charisma",
                spell_slots={
                    "1": {"label": "Level 1", "value": 1, "max": 1, "recovers_on": "long_rest"},
                    "2": {"label": "Level 2", "value": 1, "max": 1, "recovers_on": "long_rest"},
                },
            )
            caster = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Sleep caster",
                        "sheet": caster_sheet,
                    },
                    "idempotency_key": "Sleep caster",
                },
            )
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": caster["id"],
                    "artifact_id": sleep["id"],
                    "selection": {"source_class": "Bard", "method": "known"},
                    "expected_revision": caster["revision"],
                    "idempotency_key": "sleep-spell",
                },
            )
            low = default_character_sheet()
            low["combat"]["hp"] = {"value": 1, "max": 1, "temp": 0}
            low2 = default_character_sheet()
            low2["combat"]["hp"] = {"value": 2, "max": 2, "temp": 0}
            high = default_character_sheet()
            high["combat"]["hp"] = {"value": 100, "max": 100, "temp": 0}
            elf_character = await new_character("High Elf")
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": elf_character["id"],
                    "artifact_id": elf["id"],
                    "selection": {
                        **_selection(elf["selection_requirements"]),
                        "cantrip_artifact_id": "dnd5e.content.srd2014.spell.fire-bolt",
                    },
                    "expected_revision": elf_character["revision"],
                    "idempotency_key": "elf-species",
                },
            )
            half_character = await new_character("Half-Elf")
            half_selection = _selection(half_elf["selection_requirements"])
            half_selection["abilities"] = ["strength", "dexterity"]
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": half_character["id"],
                    "artifact_id": half_elf["id"],
                    "selection": half_selection,
                    "expected_revision": half_character["revision"],
                    "idempotency_key": "half-species",
                },
            )
            undead = default_character_sheet()
            undead["progression"]["species"] = "undead"
            undead["combat"]["hp"] = {"value": 1, "max": 1, "temp": 0}
            immune = default_character_sheet()
            immune["traits"]["condition_immunities"] = ["charmed"]
            immune["combat"]["hp"] = {"value": 1, "max": 1, "temp": 0}
            low_actor = await new_character("Low", low)
            low2_actor = await new_character("Low Two", low2)
            high_actor = await new_character("High", high)
            undead_actor = await new_character("Undead", undead)
            immune_actor = await new_character("Immune", immune)
            campaign_id = campaign["id"]
            current_campaign = await _call(
                server, "campaign_query", {"view": "get", "payload": {"campaign_id": campaign_id}}
            )
            phase = await _call(
                server,
                "game_phase",
                {
                    "campaign_id": campaign_id,
                    "action": "set",
                    "tool_profile": "play",
                    "expected_revision": current_campaign["revision"],
                    "idempotency_key": "play",
                },
            )
            actors = [
                caster,
                low_actor,
                low2_actor,
                high_actor,
                elf_character,
                half_character,
                undead_actor,
                immune_actor,
            ]
            started = await _call(
                server,
                "combat_start",
                {
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 40, "height_cells": 40},
                    "campaign_id": campaign_id,
                    "participant_ids": [actor["id"] for actor in actors],
                    "participant_config": [
                        {
                            "actor_id": actor["id"],
                            "initiative": [20, 18, 17, 19, 16, 15, 14, 13][index],
                            "position": {"x": position[0], "y": position[1]},
                            "disposition": "friendly" if index == 0 else "hostile",
                        }
                        for index, (actor, position) in enumerate(
                            zip(
                                actors,
                                [
                                    (0, 0),
                                    (10, 0),
                                    (11, 0),
                                    (10, 0),
                                    (13, 0),
                                    (14, 0),
                                    (10, 1),
                                    (11, 1),
                                ],
                                strict=True,
                            )
                        )
                    ],
                    "expected_revision": phase["campaign_revision"],
                    "idempotency_key": "start",
                },
            )
            revision = started["campaign_revision"]
            try:
                declaration = {
                    "origin": {"x": 10, "y": 0},
                    "target_contexts": [
                        {"target_id": actor["id"], "cover": "none"} for actor in actors[1:]
                    ],
                }
                arguments = {
                    "campaign_id": campaign_id,
                    "actor_id": actors[0]["id"],
                    "spell_id": sleep["id"],
                    "cast_level": cast_level,
                    "declaration": declaration,
                    "expected_revision": revision,
                    "idempotency_key": f"sleep-{cast_level}",
                }
                raw = await server.call_tool("combat_cast_spell", arguments)
                result = raw[1]
                assert result["status"] == "committed"
                assert result["result"]["spell_id"] == sleep["id"]
                assert (
                    result["result"]["pool_roll"]["expression"] == f"{5 + 2 * (cast_level - 1)}d8"
                )
                targets = {item["target_id"]: item for item in result["result"]["targets"]}
                assert targets[actors[1]["id"]]["affected"] is True
                assert targets[actors[4]["id"]]["skip_reason"] == "immune_to_magical_sleep"
                assert targets[actors[5]["id"]]["skip_reason"] == "immune_to_magical_sleep"
                assert targets[actors[6]["id"]]["skip_reason"] == "undead"
                assert targets[actors[7]["id"]]["skip_reason"] == "immune_to_charmed"
                assert targets[actors[2]["id"]]["affected"] is True
                assert targets[actors[3]["id"]]["affected"] is False
                assert (
                    targets[actors[3]["id"]]["skip_reason"] == "insufficient_remaining_hit_points"
                )
                assert (
                    result["result"]["pool_remaining"] == result["result"]["pool_roll"]["total"] - 3
                )
                close_server(server)
                server = create_server(config)
                assert await server.call_tool("combat_cast_spell", arguments) == raw
                cast_revision = result["campaign_revision"]
                ended = await _call(
                    server,
                    "combat_end_turn",
                    {
                        "campaign_id": campaign_id,
                        "actor_id": actors[0]["id"],
                        "expected_revision": cast_revision,
                        "idempotency_key": "end-caster-turn",
                    },
                )
                cast_revision = ended["campaign_revision"]
                with pytest.raises(Exception, match="another target"):
                    await server.call_tool(
                        "combat_common_action",
                        {
                            "campaign_id": campaign_id,
                            "actor_id": actors[3]["id"],
                            "action": "shake_sleep",
                            "target_id": actors[3]["id"],
                            "expected_revision": cast_revision,
                            "idempotency_key": "shake-self",
                        },
                    )
                with pytest.raises(Exception, match="revision"):
                    await server.call_tool(
                        "combat_common_action",
                        {
                            "campaign_id": campaign_id,
                            "actor_id": actors[3]["id"],
                            "action": "shake_sleep",
                            "target_id": actors[1]["id"],
                            "expected_revision": cast_revision - 1,
                            "idempotency_key": "shake-stale",
                        },
                    )
                shaken = await server.call_tool(
                    "combat_common_action",
                    {
                        "campaign_id": campaign_id,
                        "actor_id": actors[3]["id"],
                        "action": "shake_sleep",
                        "target_id": actors[1]["id"],
                        "expected_revision": cast_revision,
                        "idempotency_key": "shake-sleep",
                    },
                )
                assert shaken[1]["status"] == "committed"
                awakened = await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actors[1]["id"]}},
                )
                assert "unconscious" not in awakened["sheet"]["conditions"]
                zero = await server.call_tool(
                    "combat_hp_change",
                    {
                        "campaign_id": campaign_id,
                        "target_id": actors[2]["id"],
                        "action": "damage",
                        "payload": {"parts": [{"amount": 0, "damage_type": "bludgeoning"}]},
                        "expected_revision": shaken[1]["campaign_revision"],
                        "idempotency_key": "zero-sleep-damage",
                    },
                )
                assert zero[1]["status"] == "committed"
                still_sleeping = await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actors[2]["id"]}},
                )
                assert "unconscious" in still_sleeping["sheet"]["conditions"]
                before_no_action = {
                    "campaign": await _call(
                        server,
                        "campaign_query",
                        {"view": "get", "payload": {"campaign_id": campaign_id}},
                    ),
                    "target": still_sleeping,
                }
                with pytest.raises(Exception, match="legal action payment"):
                    await server.call_tool(
                        "combat_common_action",
                        {
                            "campaign_id": campaign_id,
                            "actor_id": actors[3]["id"],
                            "action": "shake_sleep",
                            "target_id": actors[2]["id"],
                            "expected_revision": before_no_action["campaign"]["revision"],
                            "idempotency_key": "shake-no-action",
                        },
                    )
                after_no_action = {
                    "campaign": await _call(
                        server,
                        "campaign_query",
                        {"view": "get", "payload": {"campaign_id": campaign_id}},
                    ),
                    "target": await _call(
                        server,
                        "character_query",
                        {"view": "get", "payload": {"character_id": actors[2]["id"]}},
                    ),
                }
                assert after_no_action == before_no_action
                damage = await server.call_tool(
                    "combat_hp_change",
                    {
                        "campaign_id": campaign_id,
                        "target_id": actors[2]["id"],
                        "action": "damage",
                        "payload": {"parts": [{"amount": 1, "damage_type": "bludgeoning"}]},
                        "expected_revision": after_no_action["campaign"]["revision"],
                        "idempotency_key": "sleep-damage",
                    },
                )
                assert damage[1]["status"] == "committed"
                damaged = await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actors[2]["id"]}},
                )
                assert "unconscious" not in damaged["sheet"]["conditions"]
                assert (
                    await server.call_tool(
                        "combat_hp_change",
                        {
                            "campaign_id": campaign_id,
                            "target_id": actors[2]["id"],
                            "action": "damage",
                            "payload": {"parts": [{"amount": 1, "damage_type": "bludgeoning"}]},
                            "expected_revision": (
                                await _call(
                                    server,
                                    "campaign_query",
                                    {"view": "get", "payload": {"campaign_id": campaign_id}},
                                )
                            )["revision"],
                            "idempotency_key": "sleep-damage",
                        },
                    )
                    == damage
                )
            finally:
                close_server(server)
        finally:
            close_server(server)

    asyncio.run(exercise())
