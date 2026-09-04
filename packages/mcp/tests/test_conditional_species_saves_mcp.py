from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import default_character_sheet
from test_official_expansions_mcp import _call, _config
from test_structured_spell_mcp import (
    _campaign_with_combat,
    _deterministic_rolls,
    _hypnotic_pattern,
    _slot,
)

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
def test_real_2014_gnome_cunning_enters_native_wisdom_save(tmp_path: Path, monkeypatch) -> None:
    """The source-bound Hypnotic Pattern path must consume the applied Gnome trait."""

    _deterministic_rolls(monkeypatch)

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
            assert await server.call_tool("combat_cast_spell", arguments) == result
        finally:
            close_server(server)

    asyncio.run(exercise())
