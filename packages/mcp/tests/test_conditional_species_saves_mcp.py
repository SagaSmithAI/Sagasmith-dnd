from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import default_character_sheet
from test_official_expansions_mcp import _call, _config

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
