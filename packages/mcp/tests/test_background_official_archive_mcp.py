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
def test_scag_103_background_choices_are_source_correct_across_restart(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        workspace = Path(__file__).resolve().parents[3]
        archive_root = workspace.parent / "SagaSmith-dnd-content-library" / "content-library"
        if not (archive_root / "index.json").is_file():
            pytest.skip("requires the sibling finalized content library")
        archives = list(
            archive_root.glob("packages/*sword-coast-adventurer-s-guide*1.0.3.sagasmith-pack")
        )
        assert len(archives) == 1
        with zipfile.ZipFile(archives[0]) as archive:
            package = json.loads(archive.read("package.sagasmith.json"))
        definition_id = package["content"]["rule_definitions"][0]["id"]
        config = replace(
            _config(tmp_path),
            official_content_library=archive_root,
            auto_seed_rules=True,
            dnd_skills_dir=workspace / "skills",
        )
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Official SCAG backgrounds", "edition": "2014", "idempotency_key": "campaign"},
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
                "idempotency_key": "activate-scag-103",
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
                    "query": definition_id,
                },
            },
        )
        city_watch = next(item for item in catalog if item["name"] == "City Watch")
        city_watch_id = city_watch["id"]

        sheet = default_character_sheet()
        sheet["skills"]["athletics"]["proficiency"] = "expertise"
        sheet["traits"]["languages"] = ["Common"]
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Athletics Watch",
                    "sheet": sheet,
                },
                "idempotency_key": "create-athletics-watch",
            },
        )
        assert actor["sheet"]["skills"]["athletics"]["proficiency"] == "expertise"
        pending = await _call(
            server,
            "character_content_apply",
            {
                "character_id": actor["id"],
                "artifact_id": city_watch_id,
                "selection": {"languages": ["Elvish", "Goblin"]},
                "expected_revision": actor["revision"],
                "idempotency_key": "watch-pending",
            },
        )
        assert pending.get("status") == "pending_choice"
        close_server(server)
        server = create_server(config)
        after_pending = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": actor["id"]}},
        )
        assert after_pending["revision"] == actor["revision"]
        assert after_pending["sheet"] == actor["sheet"]
        applied_args = {
            "character_id": actor["id"],
            "artifact_id": city_watch_id,
            "selection": {
                "languages": ["Elvish", "Goblin"],
                "skill_replacements": {"athletics": "arcana"},
            },
            "expected_revision": actor["revision"],
            "idempotency_key": "watch-apply",
        }
        applied = await _call(server, "character_content_apply", applied_args)
        assert applied["sheet"]["skills"]["athletics"]["proficiency"] == "expertise"
        assert applied["sheet"]["skills"]["arcana"]["proficiency"] == "proficient"
        assert applied["sheet"]["skills"]["insight"]["proficiency"] == "proficient"

        async def assert_rejected(name: str, languages: list[str], key: str, error: str) -> None:
            rejected_sheet = default_character_sheet()
            rejected_sheet["traits"]["languages"] = ["Common"]
            rejected = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": name,
                        "sheet": rejected_sheet,
                    },
                    "idempotency_key": f"create-{key}",
                },
            )
            before = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": rejected["id"]}},
            )
            with pytest.raises(ToolError, match=error):
                await _call(
                    server,
                    "character_content_apply",
                    {
                        "character_id": rejected["id"],
                        "artifact_id": city_watch_id,
                        "selection": {"languages": languages},
                        "expected_revision": before["revision"],
                        "idempotency_key": f"reject-{key}",
                    },
                )
            after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": rejected["id"]}},
            )
            assert after["revision"] == before["revision"]
            assert after["sheet"] == before["sheet"]

        await assert_rejected("Known Common", ["common", "Elvish"], "known-common", "must be new")
        await assert_rejected(
            "Duplicate language", ["Elvish", "ELVISH"], "duplicate-case", "must be distinct"
        )
        await assert_rejected(
            "Fabricated language",
            ["Elvish", "Definitely Not A Language"],
            "fabricated",
            "language_authorization",
        )

        authorized_sheet = default_character_sheet()
        authorized = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "DM language",
                    "sheet": authorized_sheet,
                },
                "idempotency_key": "create-authorized",
            },
        )
        authorized_result = await _call(
            server,
            "character_content_apply",
            {
                "character_id": authorized["id"],
                "artifact_id": city_watch_id,
                "selection": {
                    "languages": ["Elvish", "High Netherese"],
                    "language_authorization": {
                        "languages": ["High Netherese"],
                        "reason": "DM approved this campaign language.",
                    },
                },
                "expected_revision": authorized["revision"],
                "idempotency_key": "authorized-language",
            },
        )
        assert authorized_result["sheet"]["traits"]["languages"][-1] == "High Netherese"

        close_server(server)
        restarted = create_server(config)
        assert await _call(restarted, "character_content_apply", applied_args) == applied
        for before in (applied, authorized_result):
            after = await _call(
                restarted,
                "character_query",
                {"view": "get", "payload": {"character_id": before["id"]}},
            )
            assert after["revision"] == before["revision"]
            assert after["sheet"] == before["sheet"]
        close_server(restarted)

    asyncio.run(exercise())
