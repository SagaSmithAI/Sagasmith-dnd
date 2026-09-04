import asyncio
import hashlib
from pathlib import Path

import pytest
from sagasmith_core.content_pack import dumps_content_archive
from sagasmith_core.indexed_source import rule_chunk_key
from sagasmith_dnd.character_schema import default_character_notes, default_character_sheet
from sagasmith_dnd.content_actors import build_dnd_content_actor
from sagasmith_dnd.content_packages import build_rule_content_package

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import finalize_and_activate_module


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


def _config(tmp_path: Path, archive_dir: Path) -> McpConfig:
    return McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "dnd",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
        rule_import_roots=(archive_dir,),
    )


def _archive(tmp_path: Path, ignore_currency_weight: bool | None) -> tuple[Path, dict]:
    text = "# Currency Rule\nA source-backed currency actor."
    digest = hashlib.sha256(text.encode()).hexdigest()
    source_key = "example.currency-source"
    chunk_key = rule_chunk_key(source_key, 0, 0, text)
    source = {
        "source_key": source_key,
        "title": "Currency Source",
        "edition": "2014",
        "locale": "en",
        "version": "1.0.0",
        "publication_id": source_key,
        "authority": "supplement",
        "canonical_source_key": None,
        "checksum": digest,
        "metadata": {},
        "sections": [
            {
                "ordinal": 0,
                "parent_ordinal": None,
                "level": 1,
                "title": "Currency Rule",
                "path": ["Currency Rule"],
                "content": text,
                "content_hash": digest,
                "start_offset": 0,
                "end_offset": len(text),
                "chunks": [
                    {
                        "key": chunk_key,
                        "ordinal": 0,
                        "heading_path": ["Currency Rule"],
                        "content": text,
                        "content_hash": digest,
                        "token_count": len(text.split()),
                        "metadata": {
                            "start_offset": 0,
                            "end_offset": len(text),
                            "page_start": 1,
                            "page_end": 1,
                        },
                    }
                ],
            }
        ],
    }
    component = {
        "id": "dnd5e.example.currency-rules",
        "version": "1.0.0",
        "manifest": {
            "id": "dnd5e.example.currency-rules",
            "version": "1.0.0",
            "title": "Currency Rules",
            "namespace": "dnd5e.example.currency-rules",
            "system_id": "dnd5e",
            "editions": ["2014"],
            "dependencies": [],
            "conflicts": [],
            "capabilities": [],
        },
        "artifacts": [],
        "mechanics": [],
        "sources": [source],
        "metadata": {"distribution": "private"},
        "dependencies": [],
    }
    sheet = default_character_sheet()
    sheet["inventory"]["wallet"]["gp"] = 10
    sheet["inventory"]["encumbrance"].pop("ignore_currency_weight")
    if ignore_currency_weight is not None:
        sheet["inventory"]["encumbrance"]["ignore_currency_weight"] = ignore_currency_weight
    notes = default_character_notes()
    notes["profile"]["summary"] = "A source-backed currency actor."
    actor = build_dnd_content_actor(
        actor_id="dnd5e.example.currency-actor",
        version="1.0.0",
        actor_type="monster",
        name="Currency Actor",
        sheet=sheet,
        notes=notes,
    )
    package, blobs = build_rule_content_package(
        package_id="dnd5e.example.currency-addon",
        version="1.0.0",
        system_id="dnd5e",
        manifest={
            "id": "dnd5e.example.currency-addon",
            "version": "1.0.0",
            "system_id": "dnd5e",
            "title": "Currency Addon",
            "classification": "third_party",
            "editions": ["2014"],
            "activation": {
                "rule_policy": "branch",
                "preset_policy": "library",
                "module_policy": "none",
            },
        },
        rule_descriptors=[component],
        preset_actors=[actor],
        metadata={
            "distribution": "private",
            "license": "user-supplied",
            "attribution": "Test source",
        },
    )
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()
    path = archive_dir / "currency-addon.sagasmith-pack"
    path.write_bytes(dumps_content_archive(package, blobs))
    return path, package


@pytest.mark.parametrize("ignore_currency_weight", (None, True))
def test_currency_weight_content_actor_addon_replay_restart_and_explicit_opt_out(
    tmp_path: Path,
    ignore_currency_weight: bool | None,
) -> None:
    archive_path, package = _archive(tmp_path, ignore_currency_weight)
    expected_weight = 0 if ignore_currency_weight else 3.2

    async def exercise() -> None:
        server = create_server(_config(tmp_path, archive_path.parent))
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Currency ingress", "edition": "2014", "idempotency_key": "campaign"},
        )
        imported = await _call(
            server,
            "content_pack",
            {
                "action": "import",
                "payload": {
                    "kind": "addon",
                    "campaign_id": campaign["id"],
                    "source_path": str(archive_path),
                },
                "idempotency_key": "import",
            },
        )
        assert imported["stored"] is True
        detail = await _call(
            server,
            "content_pack",
            {
                "action": "get",
                "payload": {
                    "kind": "addon",
                    "campaign_id": campaign["id"],
                    "addon_id": package["id"],
                    "version": package["version"],
                    "include_package": True,
                },
            },
        )
        created = await _call(
            server,
            "character_create_from",
            {
                "mode": "content_actor",
                "payload": {
                    "campaign_id": campaign["id"],
                    "artifact": detail["artifact"]["artifact"],
                    "artifact_id": package["actors"][0]["id"],
                },
                "idempotency_key": "create",
            },
        )
        assert created["character"]["sheet"]["inventory"]["encumbrance"][
            "ignore_currency_weight"
        ] is bool(ignore_currency_weight)
        assert created["character"]["derived"]["inventory"]["total_weight_oz"] == pytest.approx(
            expected_weight
        )
        replay = await _call(
            server,
            "character_create_from",
            {
                "mode": "content_actor",
                "payload": {
                    "campaign_id": campaign["id"],
                    "artifact": detail["artifact"]["artifact"],
                    "artifact_id": package["actors"][0]["id"],
                },
                "idempotency_key": "create",
            },
        )
        assert replay == created
        close_server(server)
        server = create_server(_config(tmp_path, archive_path.parent))
        persisted = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": created["character"]["id"]}},
        )
        assert persisted["sheet"]["inventory"]["encumbrance"]["ignore_currency_weight"] is bool(
            ignore_currency_weight
        )
        assert persisted["derived"]["inventory"]["total_weight_oz"] == pytest.approx(
            expected_weight
        )
        opted_out = await _call(
            server,
            "character_sheet_replace",
            {
                "character_id": created["character"]["id"],
                "sheet": {
                    "inventory": {
                        "wallet": {"gp": 10},
                        "encumbrance": {"ignore_currency_weight": True},
                    }
                },
                "expected_revision": persisted["revision"],
                "idempotency_key": "opt-out",
            },
        )
        assert opted_out["derived"]["inventory"]["total_weight_oz"] == 0
        close_server(server)

    asyncio.run(exercise())


@pytest.mark.parametrize("ignore_currency_weight", (None, True))
def test_currency_weight_finalized_module_actor_ingress_restart_and_opt_out(
    tmp_path: Path,
    ignore_currency_weight: bool | None,
) -> None:
    expected_weight = 0 if ignore_currency_weight else 3.2

    async def exercise() -> None:
        config = _config(tmp_path, tmp_path / "archives")
        config.content_packages_dir.mkdir(parents=True, exist_ok=True)
        server = create_server(config)
        source = await _call(
            server,
            "campaign_create",
            {"name": "Module source", "edition": "2014", "idempotency_key": "source"},
        )
        staged = await _call(
            server,
            "module_draft",
            {
                "campaign_id": source["id"],
                "action": "start",
                "payload": {
                    "name": "currency.md",
                    "content": "# Currency\nA reviewed source-backed actor.",
                    "source_key": "example.currency-module",
                    "title": "Currency Module",
                },
                "idempotency_key": "stage",
            },
        )
        source_sheet = {"inventory": {"wallet": {"gp": 10}}}
        if ignore_currency_weight is not None:
            source_sheet["inventory"]["encumbrance"] = {
                "ignore_currency_weight": ignore_currency_weight,
            }
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": source["id"],
                    "name": "Module Currency Actor",
                    "character_type": "npc",
                    "sheet": source_sheet,
                },
                "idempotency_key": "actor",
            },
        )
        index = await _call(
            server,
            "module_query",
            {
                "campaign_id": source["id"],
                "view": "index",
                "payload": {"module_id": staged["module_id"]},
            },
        )
        await _call(
            server,
            "module_draft",
            {
                "campaign_id": source["id"],
                "action": "edit",
                "payload": {
                    "operation": "actor",
                    "module_id": staged["module_id"],
                    "scene_id": index[0]["scene_id"],
                    "character_id": actor["id"],
                    "actor_card_id": "example.currency-module.actor",
                    "binding_kind": "cast",
                    "role": "currency actor",
                },
                "idempotency_key": "bind",
            },
        )
        finalized = await finalize_and_activate_module(
            _call,
            server,
            source["id"],
            staged,
            source_key="example.currency-module",
            title="Currency Module",
            portable_id="example.currency-module",
            activate=False,
        )
        target = await _call(
            server,
            "campaign_create",
            {"name": "Module target", "edition": "2014", "idempotency_key": "target"},
        )
        artifact = finalized["finalized"]["artifact"]
        imported = await _call(
            server,
            "content_pack",
            {
                "action": "import",
                "payload": {"kind": "module", "campaign_id": target["id"], "artifact": artifact},
                "idempotency_key": "import",
            },
        )
        assert imported["actor_map"]
        module_character_id = next(iter(imported["actor_map"].values()))
        created = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": module_character_id}},
        )
        assert created["sheet"]["inventory"]["encumbrance"]["ignore_currency_weight"] is bool(
            ignore_currency_weight
        )
        assert created["derived"]["inventory"]["total_weight_oz"] == pytest.approx(expected_weight)
        await _call(
            server,
            "content_pack",
            {
                "action": "activate",
                "payload": {
                    "kind": "module",
                    "campaign_id": target["id"],
                    "module_id": imported["module_id"],
                },
                "idempotency_key": "activate",
            },
        )
        close_server(server)
        server = create_server(config)
        persisted = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": module_character_id}},
        )
        assert persisted["derived"]["inventory"]["total_weight_oz"] == pytest.approx(
            expected_weight
        )
        opted_out = await _call(
            server,
            "character_sheet_replace",
            {
                "character_id": module_character_id,
                "sheet": {
                    "inventory": {
                        "wallet": {"gp": 10},
                        "encumbrance": {"ignore_currency_weight": True},
                    }
                },
                "expected_revision": persisted["revision"],
                "idempotency_key": "module-opt-out",
            },
        )
        assert opted_out["derived"]["inventory"]["total_weight_oz"] == 0
        close_server(server)

    asyncio.run(exercise())
