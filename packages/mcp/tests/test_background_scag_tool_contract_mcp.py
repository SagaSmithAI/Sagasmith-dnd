from __future__ import annotations

import asyncio
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from test_official_expansions_mcp import _call, _config

from sagasmith_dnd_mcp.server import close_server, create_server


@pytest.mark.fresh_database
def test_scag_103_clan_crafter_tool_duplicate_replacement_and_custom_contract(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        workspace = Path(__file__).resolve().parents[3]
        library = workspace.parent / "SagaSmith-dnd-content-library" / "content-library"
        if not (library / "index.json").is_file():
            pytest.skip("requires the sibling finalized content library")
        archives = list(
            library.glob("packages/*sword-coast-adventurer-s-guide*1.0.3.sagasmith-pack")
        )
        assert len(archives) == 1
        with zipfile.ZipFile(archives[0]) as archive:
            package = json.loads(archive.read("package.sagasmith.json"))
        config = replace(
            _config(tmp_path),
            official_content_library=library,
            auto_seed_rules=True,
            dnd_skills_dir=workspace / "skills",
        )
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "SCAG tool contract",
                "edition": "2014",
                "idempotency_key": "campaign",
            },
        )
        profile = await _call(
            server, "campaign_rules", {"campaign_id": campaign["id"], "action": "get_profile"}
        )
        await _call(
            server,
            "content_pack",
            {
                "action": "activate",
                "payload": {
                    "campaign_id": campaign["id"],
                    "kind": "addon",
                    "addon_id": package["id"],
                    "version": package["version"],
                },
                "expected_revision": profile["campaign_revision"],
                "idempotency_key": "activate-scag",
            },
        )
        catalog = await _call(
            server,
            "character_query",
            {
                "view": "catalog",
                "payload": {
                    "campaign_id": campaign["id"],
                    "kind": "background",
                    "query": "Clan Crafter",
                },
            },
        )
        clan = next(item for item in catalog if item["name"] == "Clan Crafter")
        sheet = default_character_sheet()
        sheet["traits"]["proficiencies"]["tools"] = ["Alchemist's Supplies"]
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Crafter", "sheet": sheet},
                "idempotency_key": "create-crafter",
            },
        )
        selection = {"languages": ["Dwarvish"], "tools": ["Alchemist's Supplies"]}
        pending = await _call(
            server,
            "character_content_apply",
            {
                "character_id": actor["id"],
                "artifact_id": clan["id"],
                "selection": selection,
                "expected_revision": actor["revision"],
                "idempotency_key": "clan-pending",
            },
        )
        assert pending["status"] == "pending_choice"
        applied = await _call(
            server,
            "character_content_apply",
            {
                "character_id": actor["id"],
                "artifact_id": clan["id"],
                "selection": {
                    **selection,
                    "tool_replacements": {"alchemist's supplies": "Brewer's Supplies"},
                },
                "expected_revision": actor["revision"],
                "idempotency_key": "clan-apply",
            },
        )
        assert applied["sheet"]["traits"]["proficiencies"]["tools"] == [
            "Alchemist's Supplies",
            "Brewer's Supplies",
        ]
        assert (
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": actor["id"],
                    "artifact_id": clan["id"],
                    "selection": {
                        **selection,
                        "tool_replacements": {"alchemist's supplies": "Brewer's Supplies"},
                    },
                    "expected_revision": actor["revision"],
                    "idempotency_key": "clan-apply",
                },
            )
            == applied
        )
        bad_sheet = default_character_sheet()
        bad_sheet["traits"]["proficiencies"]["tools"] = ["Alchemist's Supplies"]
        bad = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Bad tool", "sheet": bad_sheet},
                "idempotency_key": "create-bad",
            },
        )
        with pytest.raises(ToolError):
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": bad["id"],
                    "artifact_id": clan["id"],
                    "selection": {
                        "languages": ["Dwarvish"],
                        "tools": ["Alchemist's Supplies"],
                        "tool_replacements": {"alchemist's supplies": "Not A Tool"},
                    },
                    "expected_revision": bad["revision"],
                    "idempotency_key": "bad-replacement",
                },
            )
        backgrounds = await _call(
            server,
            "character_query",
            {
                "view": "catalog",
                "payload": {
                    "campaign_id": campaign["id"],
                    "kind": "background",
                    "query": "City Watch",
                },
            },
        )
        city_watch = next(item for item in backgrounds if item["name"] == "City Watch")
        investigator_catalog = await _call(
            server,
            "character_query",
            {
                "view": "catalog",
                "payload": {
                    "campaign_id": campaign["id"],
                    "kind": "background",
                    "query": "Investigator",
                },
            },
        )
        investigator = next(item for item in investigator_catalog if item["name"] == "Investigator")
        classes = await _call(
            server,
            "character_query",
            {
                "view": "catalog",
                "payload": {"campaign_id": campaign["id"], "kind": "class", "query": "Fighter"},
            },
        )
        fighter = next(item for item in classes if item["name"] == "Fighter")

        async def make(name: str) -> dict:
            return await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": name,
                        "sheet": default_character_sheet(),
                    },
                    "idempotency_key": f"create-{name}",
                },
            )

        background_first = await make("Background first")
        background_done = await _call(
            server,
            "character_content_apply",
            {
                "character_id": background_first["id"],
                "artifact_id": city_watch["id"],
                "selection": {"languages": ["Elvish", "Goblin"]},
                "expected_revision": background_first["revision"],
                "idempotency_key": "background-first",
            },
        )
        class_pending = await _call(
            server,
            "character_content_apply",
            {
                "character_id": background_first["id"],
                "artifact_id": fighter["id"],
                "selection": {"skills": ["athletics", "perception"]},
                "expected_revision": background_done["revision"],
                "idempotency_key": "background-first-class-pending",
            },
        )
        assert class_pending["status"] == "pending_choice"
        class_done = await _call(
            server,
            "character_content_apply",
            {
                "character_id": background_first["id"],
                "artifact_id": fighter["id"],
                "selection": {
                    "skills": ["athletics", "perception"],
                    "skill_replacements": {"athletics": "arcana"},
                },
                "expected_revision": background_done["revision"],
                "idempotency_key": "background-first-class",
            },
        )
        assert class_done["sheet"]["skills"]["athletics"]["proficiency"] == "proficient"

        class_first = await make("Class first")
        class_done = await _call(
            server,
            "character_content_apply",
            {
                "character_id": class_first["id"],
                "artifact_id": fighter["id"],
                "selection": {"skills": ["athletics", "perception"]},
                "expected_revision": class_first["revision"],
                "idempotency_key": "class-first",
            },
        )
        pending_background = await _call(
            server,
            "character_content_apply",
            {
                "character_id": class_first["id"],
                "artifact_id": city_watch["id"],
                "selection": {"languages": ["Elvish", "Goblin"]},
                "expected_revision": class_done["revision"],
                "idempotency_key": "class-first-background-pending",
            },
        )
        assert pending_background["status"] == "pending_choice"
        class_background = await _call(
            server,
            "character_content_apply",
            {
                "character_id": class_first["id"],
                "artifact_id": city_watch["id"],
                "selection": {
                    "languages": ["Elvish", "Goblin"],
                    "skill_replacements": {"athletics": "arcana"},
                },
                "expected_revision": class_done["revision"],
                "idempotency_key": "class-first-background",
            },
        )
        assert class_background["sheet"]["skills"]["arcana"]["proficiency"] == "proficient"
        assert class_background["sheet"]["skills"]["athletics"]["proficiency"] == "proficient"
        assert class_background["sheet"]["skills"]["insight"]["proficiency"] == "proficient"
        assert {
            key
            for key, value in class_background["sheet"]["skills"].items()
            if value["proficiency"] != "none"
        } == {"athletics", "arcana", "insight", "perception"}

        custom = {
            "custom_name": "Custom Watch",
            "custom_feature_artifact_id": investigator["id"],
            "skills": ["medicine", "religion"],
            "tools": [],
            "languages": ["Elvish", "Goblin"],
            "equipment_mode": "source",
        }
        custom_actor = await make("Custom source")
        custom_result = await _call(
            server,
            "character_content_apply",
            {
                "character_id": custom_actor["id"],
                "artifact_id": city_watch["id"],
                "selection": custom,
                "expected_revision": custom_actor["revision"],
                "idempotency_key": "custom-source",
            },
        )
        grants = custom_result["sheet"]["progression"]["background_grants"]
        assert grants["feature"] == "Watcher's Eye"
        assert grants["choices"]["equipment_mode"] == "source"
        assert all(
            custom_result["sheet"]["skills"][skill]["proficiency"] == "proficient"
            for skill in ("medicine", "religion")
        )
        assert len(custom_result["sheet"]["traits"]["languages"]) == 2
        coin_actor = await make("Custom coin")
        coin = await _call(
            server,
            "character_content_apply",
            {
                "character_id": coin_actor["id"],
                "artifact_id": city_watch["id"],
                "selection": {**custom, "equipment_mode": "starting_coin"},
                "expected_revision": coin_actor["revision"],
                "idempotency_key": "custom-coin",
            },
        )
        assert (
            coin["sheet"]["progression"]["background_grants"]["choices"]["equipment_mode"]
            == "starting_coin"
        )
        assert coin["sheet"]["inventory"]["items"] == []

        close_server(server)

    asyncio.run(exercise())
