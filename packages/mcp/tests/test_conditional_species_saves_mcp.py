from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
import sagasmith_dnd.character_schema as character_schema
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.random_stream import CampaignRandomStream, use_random_stream
from test_official_expansions_mcp import _call, _config
from test_structured_spell_mcp import (
    _campaign_actor_snapshot,
    _campaign_with_combat,
    _hypnotic_pattern,
    _slot,
)

import sagasmith_dnd_mcp.server as server_module
from sagasmith_dnd_mcp.server import close_server, create_server


def _selection(requirements: dict) -> dict:
    selection: dict[str, list[str]] = {}
    for field, count_key, options_key in (
        ("skills", "skill_count", "skill_options"),
        ("tools", "tool_count", "tool_options"),
        ("languages", "language_count", "language_options"),
        ("abilities", "ability_score_count", "ability_score_options"),
    ):
        count = int(requirements.get(count_key, 0) or 0)
        if count:
            options = list(requirements.get(options_key) or [])
            if not options and requirements.get(f"allow_any_{field[:-1]}") is True:
                options = ["Acrobatics", "Athletics"] if field == "skills" else ["Dwarvish"]
            if field == "abilities" and not options:
                options = ["strength", "dexterity", "constitution", "wisdom"]
            assert len(options) >= count, (field, requirements)
            selection[field] = options[:count]
    return selection


@pytest.mark.fresh_database
@pytest.mark.parametrize(
    "species_name",
    ["Hill Dwarf", "High Elf", "Half-Elf", "Rock Gnome", "Lightfoot"],
)
def test_real_2014_species_save_traits_apply_and_replay(tmp_path: Path, species_name: str) -> None:
    async def exercise() -> None:
        workspace = Path(__file__).resolve().parents[3]
        config = replace(
            _config(tmp_path), auto_seed_rules=True, dnd_skills_dir=workspace / "skills"
        )
        server = create_server(config)
        try:
            assert Path(__import__("sagasmith_dnd").__file__).resolve().is_relative_to(workspace)
            assert Path(character_schema.__file__).resolve().is_relative_to(workspace)
            assert Path(server_module.__file__).resolve().is_relative_to(workspace)
            campaign = await _call(
                server,
                "campaign_create",
                {"name": species_name, "edition": "2014", "idempotency_key": "campaign"},
            )
            catalog = await _call(
                server,
                "character_query",
                {
                    "view": "catalog",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "kind": "species",
                        "query": species_name,
                    },
                },
            )
            artifact = next(item for item in catalog if item["name"] == species_name)
            selection = _selection(artifact["selection_requirements"])
            if "abilities" in artifact["selection_requirements"]["fields"]:
                selection["abilities"] = ["strength", "dexterity"]
            if "cantrip_artifact_id" in artifact["selection_requirements"]["fields"]:
                spells = await _call(
                    server,
                    "character_query",
                    {
                        "view": "catalog",
                        "payload": {
                            "campaign_id": campaign["id"],
                            "kind": "spell",
                            "query": "Fire Bolt",
                        },
                    },
                )
                selection["cantrip_artifact_id"] = next(
                    item["id"] for item in spells if item["name"] == "Fire Bolt"
                )
            sheet = default_character_sheet()
            character = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": species_name,
                        "sheet": sheet,
                    },
                    "idempotency_key": "character",
                },
            )
            applied = await _call(
                server,
                "character_content_apply",
                {
                    "character_id": character["id"],
                    "artifact_id": artifact["id"],
                    "selection": selection,
                    "expected_revision": character["revision"],
                    "idempotency_key": "species",
                },
            )
            assert applied["sheet"]["progression"]["species"] == species_name
            requested_selection = selection
            selection = next(
                item
                for item in applied["sheet"]["content"]["selections"]
                if item["kind"] == "species"
            )
            assert selection["artifact_id"] == artifact["id"]
            assert selection["pack_id"] == "dnd5e.content.srd2014"
            assert selection["pack_version"]
            assert selection["rule_refs"]
            assert "mechanic_refs" in selection
            feature_names = {item["name"] for item in applied["sheet"]["content"]["features"]}
            expected_feature = {
                "Hill Dwarf": "Dwarven Resilience",
                "High Elf": "Fey Ancestry",
                "Half-Elf": "Fey Ancestry",
                "Rock Gnome": "Gnome Cunning",
                "Lightfoot": "Brave",
            }[species_name]
            assert expected_feature in feature_names
            close_server(server)
            server = create_server(config)
            reloaded = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": character["id"]}},
            )
            assert reloaded["sheet"] == applied["sheet"]
            replayed = await _call(
                server,
                "character_content_apply",
                {
                    "character_id": character["id"],
                    "artifact_id": artifact["id"],
                    "selection": requested_selection,
                    "expected_revision": reloaded["revision"],
                    "idempotency_key": "species",
                },
            )
            assert replayed == applied
        finally:
            close_server(server)

    asyncio.run(exercise())


@pytest.mark.fresh_database
@pytest.mark.parametrize("ancestry,damage_type", [("green", "poison"), ("white", "cold")])
def test_real_2014_dwarf_against_native_dragonborn_breath(
    tmp_path: Path, ancestry: str, damage_type: str
) -> None:
    """Real species grants; successful-save reduction precedes poison resistance."""

    async def exercise() -> None:
        workspace = Path(__file__).resolve().parents[3]
        config = replace(
            _config(tmp_path), auto_seed_rules=True, dnd_skills_dir=workspace / "skills"
        )
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Dwarven resilience", "edition": "2014", "idempotency_key": "campaign"},
            )
            actors = []
            for index, species_name in enumerate(["Dragonborn", "Hill Dwarf", "Hill Dwarf"]):
                catalog = await _call(
                    server,
                    "character_query",
                    {
                        "view": "catalog",
                        "payload": {
                            "campaign_id": campaign["id"],
                            "kind": "species",
                            "query": species_name,
                        },
                    },
                )
                species = next(
                    item
                    for item in catalog
                    if (
                        item["id"] == "dnd5e.content.standard2014.species.dragonborn"
                        if index == 0
                        else item["name"] == species_name
                    )
                )
                sheet = default_character_sheet()
                sheet["combat"]["hp"] = {"value": 40, "max": 40, "temp": 0}
                # Explicit fixture bonuses guarantee both save outcomes with
                # genuine campaign dice; species traits are applied publicly.
                sheet["abilities"]["constitution"]["bonus"] = [0, 20, -20][index]
                character = await _call(
                    server,
                    "character_create_from",
                    {
                        "mode": "direct",
                        "payload": {
                            "campaign_id": campaign["id"],
                            "name": f"{species_name}-{index}",
                            "sheet": sheet,
                        },
                        "idempotency_key": f"actor-{index}",
                    },
                )
                selection = _selection(species["selection_requirements"])
                if index == 0:
                    selection["damage_affinity"] = ancestry
                applied = await _call(
                    server,
                    "character_content_apply",
                    {
                        "character_id": character["id"],
                        "artifact_id": species["id"],
                        "selection": selection,
                        "expected_revision": character["revision"],
                        "idempotency_key": f"species-{index}",
                    },
                )
                if "sheet" not in applied:
                    raise AssertionError(f"{species['id']}: {applied}")
                assert applied["sheet"]["progression"]["species"] == species_name
                assert (damage_type if index == 0 else "poison") in applied["sheet"]["traits"][
                    "resistances"
                ]
                actors.append(applied)
            actor_ids = [actor["id"] for actor in actors]
            snapshot = await _campaign_actor_snapshot(server, campaign["id"], actor_ids)
            phase = await _call(
                server,
                "game_phase",
                {
                    "campaign_id": campaign["id"],
                    "action": "set",
                    "tool_profile": "play",
                    "expected_revision": snapshot["campaign"]["revision"],
                    "idempotency_key": "play",
                },
            )
            started = await _call(
                server,
                "combat_start",
                {
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 12, "height_cells": 12},
                    "campaign_id": campaign["id"],
                    "participant_ids": actor_ids,
                    "participant_config": [
                        {
                            "actor_id": actor_id,
                            "initiative": 20 - index,
                            "position": {"x": x, "y": y},
                        }
                        for index, (actor_id, (x, y)) in enumerate(
                            zip(actor_ids, [(0, 0), (2, 0), (2, 1)], strict=True)
                        )
                    ],
                    "expected_revision": phase["campaign_revision"],
                    "idempotency_key": "start",
                },
            )
            breath = next(
                item
                for item in actors[0]["sheet"]["content"]["activities"]
                if item["name"] == "Breath Weapon"
            )
            assert breath["pack_id"] == "dnd5e.content.standard2014"
            assert breath["pack_version"] and breath["rule_refs"]
            assert breath["choices"]["standard_resolution"]["save_ability"] == "constitution"
            arguments = {
                "campaign_id": campaign["id"],
                "actor_id": actor_ids[0],
                "activity_id": breath["id"],
                "declaration": {
                    "origin": {"x": 1, "y": 0},
                    "target_contexts": [
                        {"target_id": actor_id, "cover": "none"} for actor_id in actor_ids[1:]
                    ],
                },
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "breath",
            }
            before = await _campaign_actor_snapshot(server, campaign["id"], actor_ids)
            stream = CampaignRandomStream.from_campaign_state(
                campaign["id"],
                before["campaign"]["state"],
                operation="combat_use_activity",
                idempotency_key="breath",
                campaign_revision=started["campaign_revision"],
            )
            with use_random_stream(stream):
                result = await server.call_tool("combat_use_activity", arguments)
            payload = result[1]
            assert payload["status"] == "committed", payload
            effect = payload["result"]["core_effect"]
            assert effect["save_ability"] == "constitution"
            assert effect["save_dc"] == 10
            assert effect["damage_type"] == damage_type
            assert effect["damage_expression"] == "2d6"
            after = await _campaign_actor_snapshot(server, campaign["id"], actor_ids)
            assert effect["activation_payment"] == {"kind": "activity", "activation_type": "action"}
            for index, actor_id in enumerate(actor_ids):
                before_combatant = next(
                    item
                    for item in before["campaign"]["state"]["combat"]["combatants"]
                    if item["actor_id"] == actor_id
                )
                after_combatant = next(
                    item
                    for item in after["campaign"]["state"]["combat"]["combatants"]
                    if item["actor_id"] == actor_id
                )
                expected_budget = dict(before_combatant["turn_budget"])
                if index == 0:
                    expected_budget["main_action"] -= 1
                assert after_combatant["turn_budget"] == expected_budget
            assert {item["target_id"] for item in effect["targets"]} == set(actor_ids[1:])
            for index, actor_id in enumerate(actor_ids[1:], start=1):
                target = next(item for item in effect["targets"] if item["target_id"] == actor_id)
                saved = target["save"]
                assert saved["success"] is (index == 1)
                assert saved["roll_mode"] == ("advantage" if ancestry == "green" else "normal")
                assert len(saved["rolls"]) == (2 if ancestry == "green" else 1)
                receipts = [item["mechanic_id"] for item in saved.get("rule_receipts", [])]
                assert receipts == (
                    ["dnd5e.core.save.dwarven_resilience"] if ancestry == "green" else []
                )
                raw_damage = effect["damage_roll"]["total"]
                after_save = raw_damage // 2 if index == 1 else raw_damage
                expected_damage = after_save // 2 if ancestry == "green" else after_save
                assert target["damage_amount"] == after_save
                assert target["damage"]["applied_amount"] == expected_damage
                assert after["actors"][index]["sheet"]["combat"]["hp"]["value"] == (
                    before["actors"][index]["sheet"]["combat"]["hp"]["value"] - expected_damage
                )
            used_breath = next(
                item
                for item in after["actors"][0]["sheet"]["content"]["activities"]
                if item["id"] == breath["id"]
            )
            assert used_breath["uses"]["value"] == 0
            receipt = payload["random_stream_receipt"]
            assert receipt["position_after"] - receipt["position_before"] == (
                6 if ancestry == "green" else 4
            )
            assert after["campaign"]["state"]["random_stream"]["last_receipt"] == receipt
            assert (
                after["campaign"]["state"]["random_stream"]["position"] == receipt["position_after"]
            )
            assert await server.call_tool("combat_use_activity", arguments) == result
            close_server(server)
            server = create_server(config)
            assert await server.call_tool("combat_use_activity", arguments) == result
            assert await _campaign_actor_snapshot(server, campaign["id"], actor_ids) == after
        finally:
            close_server(server)

    asyncio.run(exercise())


@pytest.mark.fresh_database
def test_real_2014_gnome_cunning_enters_native_wisdom_save(tmp_path: Path) -> None:
    """The source-bound Hypnotic Pattern path must consume the applied Gnome trait."""

    async def exercise() -> None:
        workspace = Path(__file__).resolve().parents[3]
        config = replace(
            _config(tmp_path), auto_seed_rules=True, dnd_skills_dir=workspace / "skills"
        )
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Gnome save", "edition": "2014", "idempotency_key": "campaign"},
            )
            catalog = await _call(
                server,
                "character_query",
                {
                    "view": "catalog",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "kind": "species",
                        "query": "Rock Gnome",
                    },
                },
            )
            species = next(item for item in catalog if item["name"] == "Rock Gnome")
            character = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Gnome",
                        "sheet": default_character_sheet(),
                    },
                    "idempotency_key": "character",
                },
            )
            applied = await _call(
                server,
                "character_content_apply",
                {
                    "character_id": character["id"],
                    "artifact_id": species["id"],
                    "selection": {},
                    "expected_revision": character["revision"],
                    "idempotency_key": "species",
                },
            )
            gnome = applied["sheet"]
            gnome["abilities"]["wisdom"]["score"] = 1
            caster = default_character_sheet()
            caster["abilities"]["charisma"]["score"] = 30
            caster["spellcasting"].update(ability="charisma", spell_slots=_slot(3))
            hypnotic = _hypnotic_pattern()
            caster["content"]["spells"] = [hypnotic]
            close_server(server)
            combat_config = replace(
                _config(tmp_path / "combat"),
                auto_seed_rules=True,
                dnd_skills_dir=workspace / "skills",
            )
            server = create_server(combat_config)
            campaign_id, revision, actors = await _campaign_with_combat(
                server,
                [("Bard", caster), ("Rock Gnome", gnome)],
                positions=[(0, 0), (2, 1)],
            )
            arguments = {
                "campaign_id": campaign_id,
                "actor_id": actors[0]["id"],
                "spell_id": hypnotic["id"],
                "cast_level": 3,
                "declaration": {
                    "origin": {"x": 1, "y": 0},
                    "cube": {"min": {"x": 1, "y": 0}, "max": {"x": 6, "y": 5}},
                },
                "expected_revision": revision,
                "idempotency_key": "gnome-hypnotic",
            }
            actor_ids = [actor["id"] for actor in actors]
            before = await _campaign_actor_snapshot(server, campaign_id, actor_ids)
            stream = CampaignRandomStream.from_campaign_state(
                campaign_id,
                before["campaign"]["state"],
                operation="combat_cast_spell",
                idempotency_key=arguments["idempotency_key"],
                campaign_revision=revision,
            )
            # Exercise the real campaign stream normally supplied by the MCP
            # request wrapper, without substituting dice or settlement helpers.
            with use_random_stream(stream):
                result = await server.call_tool("combat_cast_spell", arguments)
            payload = result[1]
            assert payload["status"] == "committed"
            target = next(
                item
                for item in payload["result"]["targets"]
                if item["target_id"] == actors[1]["id"]
            )
            assert target["save"]["roll_mode"] == "advantage", target
            assert len(target["save"]["rolls"]) == 2
            assert any(
                receipt["mechanic_id"] == "dnd5e.core.save.gnome_cunning"
                for receipt in target["save"]["rule_receipts"]
            )
            after = await _campaign_actor_snapshot(server, campaign_id, actor_ids)
            assert target["save"]["success"] is False
            assert {"charmed", "incapacitated"} <= set(after["actors"][1]["sheet"]["conditions"])
            assert after["actors"][0]["sheet"]["spellcasting"]["spell_slots"]["3"]["value"] == 0
            receipt = payload["random_stream_receipt"]
            assert receipt["position_after"] == receipt["position_before"] + 2
            assert after["campaign"]["state"]["random_stream"]["last_receipt"] == receipt
            assert (
                after["campaign"]["state"]["random_stream"]["position"] == receipt["position_after"]
            )
            assert await server.call_tool("combat_cast_spell", arguments) == result
            close_server(server)
            server = create_server(combat_config)
            assert await server.call_tool("combat_cast_spell", arguments) == result
            assert await _campaign_actor_snapshot(server, campaign_id, actor_ids) == after
        finally:
            close_server(server)

    asyncio.run(exercise())
