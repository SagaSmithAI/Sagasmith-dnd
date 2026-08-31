from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_core.content_pack import dumps_content_archive
from sagasmith_dnd.character_schema import default_character_notes, default_character_sheet
from sagasmith_dnd.content_actors import build_dnd_content_actor
from sagasmith_dnd.content_packages import (
    build_preset_content_package,
    compose_addon_content_package,
)
from sagasmith_dnd.core_content import PACK_ID as CORE_CONTENT_PACK_ID
from sagasmith_dnd.core_content import PACK_VERSION as CORE_CONTENT_PACK_VERSION
from sagasmith_dnd.official_expansions import load_official_expansion_lock

import sagasmith_dnd_mcp.server as server_module
from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server


async def _call(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    _, response = await server.call_tool(name, arguments)
    return response.get("result", response) if isinstance(response, dict) else response


def _config(
    tmp_path: Path,
    *,
    rule_import_roots: tuple[Path, ...] = (),
) -> McpConfig:
    return McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "dnd-skills",
        modulegen_skills_dir=tmp_path / "modulegen-skills",
        auto_seed_rules=False,
        rule_import_roots=rule_import_roots,
    )


def _official_2014_addon_archive(tmp_path: Path) -> tuple[dict[str, Any], Path, str]:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    notes = default_character_notes()
    notes["profile"]["summary"] = "A source-backed official 2014 test fixture."
    card = build_dnd_content_actor(
        actor_id="dnd5e.test.official-2014.actor",
        version="1.0.0",
        actor_type="monster",
        name="Official 2014 fixture",
        sheet=sheet,
        notes=notes,
    )
    preset, preset_blobs = build_preset_content_package(
        package_id="dnd5e.test.official-2014.preset",
        version="1.0.0",
        system_id="dnd5e",
        title="Official 2014 fixture presets",
        cards=[card],
        metadata={
            "distribution": "private",
            "license": "user-supplied",
            "attribution": "Test fixture",
            "edition": "2014",
        },
    )
    package, blobs = compose_addon_content_package(
        package_id="dnd5e.test.official-2014.addon",
        version="1.0.0",
        system_id="dnd5e",
        manifest={
            "title": "Official 2014 fixture",
            "classification": "official_supplement",
            "editions": ["2014"],
            "activation": {
                "rule_policy": "branch",
                "preset_policy": "library",
                "module_policy": "none",
            },
        },
        components=[(preset, preset_blobs)],
        metadata={
            "distribution": "private",
            "license": "user-supplied",
            "attribution": "Test fixture",
        },
    )
    archive = dumps_content_archive(package, blobs)
    archive_dir = tmp_path / "imports"
    archive_dir.mkdir()
    archive_path = archive_dir / "official-2014.sagasmith-pack"
    archive_path.write_bytes(archive)
    return package, archive_path, hashlib.sha256(archive).hexdigest()


def test_official_expansion_registry_is_core_visible_but_unmounted_by_default(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign_2014 = await _call(
            server,
            "campaign_create",
            {
                "name": "2014 registry",
                "edition": "2014",
                "idempotency_key": "official-registry-2014",
            },
        )
        profile_2014 = await _call(
            server,
            "campaign_rules",
            {"campaign_id": campaign_2014["id"], "action": "get_profile"},
        )
        campaign_2024 = await _call(
            server,
            "campaign_create",
            {
                "name": "2024 registry",
                "edition": "2024",
                "idempotency_key": "official-registry-2024",
            },
        )
        profile_2024 = await _call(
            server,
            "campaign_rules",
            {"campaign_id": campaign_2024["id"], "action": "get_profile"},
        )

        assert len(profile_2014["available_official_expansions"]) == 10
        assert {
            tuple(item["editions"])
            for item in profile_2014["available_official_expansions"]
        } == {
            ("2014",)
        }
        assert profile_2014["official_expansion_mount"] == {
            "configured": False,
            "installed": 0,
            "available": 10,
            "support_installed": 0,
            "support_available": 1,
        }
        assert profile_2024["available_official_expansions"] == []

    asyncio.run(exercise())


def test_official_2014_addon_inventory_is_hidden_from_2024_campaigns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package, archive_path, archive_sha256 = _official_2014_addon_archive(tmp_path)
    catalog_entry = {
        "id": package["id"],
        "version": package["version"],
        "checksum": package["checksum"],
        "archive_sha256": archive_sha256,
        "publication_id": "test-official-2014",
        "title": "Official 2014 fixture",
        "classification": "official_supplement",
        "editions": ["2014"],
        "content_summary": {"statblock": 1},
        "selection_ready": {},
        "catalog_only": {"statblock": 1},
    }

    def official_catalog(edition: str | None = None) -> tuple[dict[str, Any], ...]:
        return (catalog_entry,) if edition in {None, "", "2014"} else ()

    monkeypatch.setattr(server_module, "official_expansion_catalog", official_catalog)

    async def exercise() -> None:
        server = create_server(
            _config(tmp_path, rule_import_roots=(archive_path.parent,))
        )
        campaign_2014 = await _call(
            server,
            "campaign_create",
            {
                "name": "2014 official inventory",
                "edition": "2014",
                "idempotency_key": "campaign-2014",
            },
        )
        campaign_2024 = await _call(
            server,
            "campaign_create",
            {
                "name": "2024 official inventory",
                "edition": "2024",
                "idempotency_key": "campaign-2024",
            },
        )
        await _call(
            server,
            "content_pack",
            {
                "action": "import",
                "payload": {
                    "campaign_id": campaign_2014["id"],
                    "kind": "addon",
                    "source_path": str(archive_path),
                },
                "idempotency_key": "import-official-2014",
            },
        )

        listed_2014 = await _call(
            server,
            "content_pack",
            {
                "action": "list",
                "payload": {
                    "campaign_id": campaign_2014["id"],
                    "kind": "addon",
                },
            },
        )
        assert [item["addon_id"] for item in listed_2014] == [package["id"]]
        assert listed_2014[0]["built_in_official_expansion"] is True
        assert listed_2014[0]["editions"] == ["2014"]

        detail_2014 = await _call(
            server,
            "content_pack",
            {
                "action": "get",
                "payload": {
                    "campaign_id": campaign_2014["id"],
                    "kind": "addon",
                    "addon_id": package["id"],
                    "version": package["version"],
                    "include_package": True,
                },
            },
        )
        assert detail_2014["package"]["checksum"] == package["checksum"]
        exported_2014 = await _call(
            server,
            "content_pack",
            {
                "action": "export",
                "payload": {
                    "campaign_id": campaign_2014["id"],
                    "kind": "addon",
                    "addon_id": package["id"],
                    "version": package["version"],
                },
            },
        )
        artifact = exported_2014["artifact"]["artifact"]

        listed_2024 = await _call(
            server,
            "content_pack",
            {
                "action": "list",
                "payload": {
                    "campaign_id": campaign_2024["id"],
                    "kind": "addon",
                },
            },
        )
        assert listed_2024 == []

        forbidden = (
            {
                "action": "get",
                "payload": {
                    "campaign_id": campaign_2024["id"],
                    "kind": "addon",
                    "addon_id": package["id"],
                    "version": package["version"],
                },
            },
            {
                "action": "get",
                "payload": {
                    "campaign_id": campaign_2024["id"],
                    "kind": "addon",
                    "artifact": artifact,
                },
            },
            {
                "action": "export",
                "payload": {
                    "campaign_id": campaign_2024["id"],
                    "kind": "addon",
                    "addon_id": package["id"],
                    "version": package["version"],
                },
            },
        )
        for arguments in forbidden:
            with pytest.raises(ToolError):
                await _call(server, "content_pack", arguments)

        profile_2024 = await _call(
            server,
            "campaign_rules",
            {"campaign_id": campaign_2024["id"], "action": "get_profile"},
        )
        with pytest.raises(ToolError):
            await _call(
                server,
                "content_pack",
                {
                    "action": "activate",
                    "payload": {
                        "campaign_id": campaign_2024["id"],
                        "kind": "addon",
                        "addon_id": package["id"],
                        "version": package["version"],
                    },
                    "expected_revision": profile_2024["campaign_revision"],
                    "idempotency_key": "reject-official-2014-in-2024",
                },
            )

        profile_2014 = await _call(
            server,
            "campaign_rules",
            {"campaign_id": campaign_2014["id"], "action": "get_profile"},
        )
        activated = await _call(
            server,
            "content_pack",
            {
                "action": "activate",
                "payload": {
                    "campaign_id": campaign_2014["id"],
                    "kind": "addon",
                    "addon_id": package["id"],
                    "version": package["version"],
                },
                "expected_revision": profile_2014["campaign_revision"],
                "idempotency_key": "activate-official-2014",
            },
        )
        assert activated["activation"]["enabled"] is True

    asyncio.run(exercise())


def test_official_expansion_lock_matches_seeded_core_content(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[3]
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=repository_root / "skills",
        modulegen_skills_dir=tmp_path / "modulegen-skills",
        auto_seed_rules=True,
    )

    create_server(config)

    with sqlite3.connect(config.home / "data" / "ttrpgbase.db") as connection:
        row = connection.execute(
            "SELECT checksum FROM rule_pack_versions WHERE pack_id = ? AND version = ?",
            (CORE_CONTENT_PACK_ID, CORE_CONTENT_PACK_VERSION),
        ).fetchone()
    assert row is not None
    installed_checksum = str(row[0])
    lock = load_official_expansion_lock()
    builtin = next(
        definition
        for definition in lock["builtin_rule_definitions"]
        if definition["id"] == CORE_CONTENT_PACK_ID
        and definition["version"] == CORE_CONTENT_PACK_VERSION
    )
    assert builtin["checksum"] == installed_checksum
    assert {
        rebind["runtime_checksum"]
        for rebind in lock["dependency_rebinds"]
        if rebind["dependency_id"] == CORE_CONTENT_PACK_ID
        and rebind["runtime_version"] == CORE_CONTENT_PACK_VERSION
    } == {installed_checksum}
