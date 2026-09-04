from __future__ import annotations

import asyncio
import hashlib
import os
import random
import sqlite3
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_core import Database, RulePackService
from sagasmith_core.content_pack import dumps_content_archive
from sagasmith_core.database import sqlite_database_url
from sagasmith_core.models import RulePackPayload, RulePackVersion
from sagasmith_core.state_document_storage import decode_state_document, encode_state_document
from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_notes,
    default_character_sheet,
    derive_character_sheet,
    equip_inventory_item,
)
from sagasmith_dnd.combat_engine import roll_attack_action as engine_roll_attack_action
from sagasmith_dnd.content_actors import (
    SRD2014_PRESET_PACK_ID,
    SRD2014_PRESET_PACK_VERSION,
    SRD2024_PRESET_PACK_ID,
    SRD2024_PRESET_PACK_VERSION,
    build_dnd_content_actor,
)
from sagasmith_dnd.content_packages import (
    build_preset_content_package,
    compose_addon_content_package,
)
from sagasmith_dnd.core_content import PACK_ID as CORE_CONTENT_PACK_ID
from sagasmith_dnd.core_content import PACK_VERSION as CORE_CONTENT_PACK_VERSION
from sagasmith_dnd.official_expansions import (
    load_official_expansion_lock,
    verify_official_expansion_library,
)
from sagasmith_dnd.spells import CORE_SHIELD_MECHANIC_ID, CORE_SHIELD_SPELL_ID
from sagasmith_dnd.standard_feature_ids import (
    TORTLE_NATURAL_ARMOR_ARTIFACT_ID,
    TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
    TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
    TORTLE_NATURAL_ARMOR_LEGACY_PACK_VERSIONS,
)

import sagasmith_dnd_mcp.server as server_module
from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import (
    _verified_content_authority_ids,
    close_server,
    create_server,
)

_SCAG_ADDON_ID = "dnd5e.addon.rulebook.d-d-5e-sword-coast-adventurer-s-guide.16e6a243ef0a.addon"
_SCAG_RULE_ID = _SCAG_ADDON_ID.removesuffix(".addon")
_CITY_WATCH_ID = f"{_SCAG_RULE_ID}.background.city-watch"
_TORTLE_ADDON_ID = "dnd5e.addon.rulebook.d-d-5e-the-tortle-package.e3234de670da.addon"
_TORTLE_RULE_ID = _TORTLE_ADDON_ID.removesuffix(".addon")
_TORTLE_ID = f"{_TORTLE_RULE_ID}.species.tortle"


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


def _locked_official_library() -> Path:
    raw = os.environ.get("SAGASMITH_DND_TEST_OFFICIAL_CONTENT_LIBRARY")
    if not raw:
        pytest.skip(
            "set SAGASMITH_DND_TEST_OFFICIAL_CONTENT_LIBRARY to the checkout at "
            "the official lock source commit"
        )
    library = Path(raw).expanduser().resolve()
    report = verify_official_expansion_library(library)
    assert report["verified"] is True
    return library


async def _selection_for(
    server: Any,
    campaign_id: str,
    artifact_id: str,
) -> dict[str, Any]:
    entries = await _call(
        server,
        "character_query",
        {
            "view": "catalog",
            "payload": {"campaign_id": campaign_id, "query": artifact_id},
        },
    )
    entry = next(item for item in entries if item.get("id") == artifact_id)
    requirements = dict(entry.get("selection_requirements") or {})
    selection: dict[str, Any] = {}
    for key, count_key, options_key in (
        ("skills", "skill_choice_count", "skill_options"),
        ("tools", "tool_choice_count", "tool_options"),
        ("languages", "language_count", "language_options"),
    ):
        count = int(requirements.get(count_key, 0) or 0)
        if count:
            options = list(requirements.get(options_key) or [])
            if key == "languages" and len(options) < count:
                # This fixture exercises ordinary choices, not DM-authorized
                # exotic languages such as Draconic.
                options.extend(
                    value for value in ("Dwarvish", "Elvish", "Giant") if value not in options
                )
            selection[key] = options[:count]
    equipment = list(requirements.get("equipment_package_options") or [])
    if equipment:
        selection["equipment_package"] = equipment[0]
    return selection


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


@pytest.mark.parametrize(
    ("initial_rank", "expected_rank"),
    [
        ("none", "proficient"),
        ("half", "proficient"),
        ("proficient", "proficient"),
        ("expertise", "expertise"),
    ],
)
def test_fixed_species_skill_proficiency_uses_rank_max(
    initial_rank: str,
    expected_rank: str,
) -> None:
    sheet = default_character_sheet()
    sheet["skills"]["survival"]["proficiency"] = initial_rank

    server_module._apply_fixed_skill_proficiency(sheet, "survival", source="species")

    assert sheet["skills"]["survival"]["proficiency"] == expected_rank


def test_fixed_species_skill_proficiency_survives_restart_and_actual_check(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)

    async def exercise() -> None:
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "Fixed species skill proficiency",
                "edition": "2014",
                "idempotency_key": "campaign",
            },
        )
        sheet = default_character_sheet()
        server_module._apply_fixed_skill_proficiency(sheet, "survival", source="species")
        assert sheet["skills"]["survival"]["proficiency"] == "proficient"
        sheet["skills"]["survival"]["proficiency"] = "expertise"
        server_module._apply_fixed_skill_proficiency(sheet, "survival", source="species")
        assert sheet["skills"]["survival"]["proficiency"] == "expertise"
        character = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Expert survivalist",
                    "sheet": sheet,
                },
                "idempotency_key": "character",
            },
        )
        close_server(server)

        restarted = create_server(config)
        restored = await _call(
            restarted,
            "character_query",
            {"view": "get", "payload": {"character_id": character["id"]}},
        )
        assert restored["sheet"]["skills"]["survival"]["proficiency"] == "expertise"
        assert restored["derived"]["skills"]["survival"] == 4
        campaign_state = await _call(
            restarted,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        phase = await _call(
            restarted,
            "game_phase",
            {
                "campaign_id": campaign["id"],
                "action": "set",
                "tool_profile": "play",
                "expected_revision": campaign_state["revision"],
                "idempotency_key": "enter-play",
            },
        )
        checked = await _call(
            restarted,
            "character_check",
            {
                "campaign_id": campaign["id"],
                "action": "check",
                "payload": {
                    "actor_id": character["id"],
                    "kind": "check",
                    "ability": "survival",
                    "dc": 10,
                },
                "expected_revision": phase["campaign_revision"],
                "idempotency_key": "survival-check",
            },
        )
        assert checked["ability_modifier"] == 0
        assert checked["proficiency_bonus"] == 2
        assert checked["bonus"] == 2
        assert checked["total"] - checked["natural"] == 4
        close_server(restarted)

    asyncio.run(exercise())


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
            tuple(item["editions"]) for item in profile_2014["available_official_expansions"]
        } == {("2014",)}
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
        server = create_server(_config(tmp_path, rule_import_roots=(archive_path.parent,)))
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


def test_finalized_tortle_archive_settles_natural_armor_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root = Path(__file__).resolve().parents[3]
    official_library = repository_root.parent / "SagaSmith-dnd-content-library" / "content-library"
    if not (official_library / "index.json").is_file():
        pytest.skip("requires the sibling finalized SagaSmith D&D content library")
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=repository_root / "skills",
        modulegen_skills_dir=tmp_path / "modulegen-skills",
        auto_seed_rules=True,
        official_content_library=official_library,
    )

    def deterministic_attack(*, plan: dict[str, Any]) -> Any:
        # Fixed legal roll hits AC 19 but is blocked by the Shield reaction's AC 24.
        return engine_roll_attack_action(plan=plan, rng=random.Random(0))

    monkeypatch.setattr(server_module, "roll_attack_action", deterministic_attack)

    async def exercise() -> None:
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "Finalized Tortle archive",
                "edition": "2014",
                "idempotency_key": "campaign",
            },
        )
        profile = await _call(
            server,
            "campaign_rules",
            {"campaign_id": campaign["id"], "action": "get_profile"},
        )
        activated = await _call(
            server,
            "content_pack",
            {
                "action": "activate",
                "payload": {
                    "campaign_id": campaign["id"],
                    "kind": "addon",
                    "addon_id": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
                    "version": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
                },
                "expected_revision": profile["campaign_revision"],
                "idempotency_key": "activate-tortle",
            },
        )
        assert activated["activation"]["enabled"] is True
        tortle_sheet = default_character_sheet()
        tortle_sheet["skills"]["survival"]["proficiency"] = "expertise"
        tortle_sheet["traits"]["proficiencies"]["armor"] = ["heavy armor", "shields"]
        tortle_sheet["spellcasting"]["spell_slots"] = {
            "1": {
                "label": "1st",
                "value": 1,
                "max": 1,
                "recovers_on": "long_rest",
                "source_key": "wizard",
            }
        }
        tortle_sheet["content"]["spells"] = [
            {
                "id": CORE_SHIELD_SPELL_ID,
                "name": "Shield",
                "level": 1,
                "grant": {"source_type": "class", "source_key": "wizard", "method": "known"},
                "access": {"known": True, "prepared": True},
                "definition": {
                    "casting_time": "1 reaction, which you take when hit by an attack",
                    "duration": {
                        "kind": "timed",
                        "value": 1,
                        "unit": "round",
                        "concentration": False,
                    },
                    "components": {"verbal": True, "somatic": True},
                },
                "mechanic_refs": [CORE_SHIELD_MECHANIC_ID],
            }
        ]
        character = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Tortle",
                    "sheet": tortle_sheet,
                },
                "idempotency_key": "character",
            },
        )
        race_character = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Tortle race target",
                    "sheet": default_character_sheet(),
                },
                "idempotency_key": "race-character",
            },
        )
        entered_authority_verification = Event()
        release_authority_verification = Event()
        original_authority_verifier = server_module._verified_tortle_natural_armor_authority

        def pause_after_candidate_lookup(**kwargs: Any) -> dict[str, str] | None:
            result = original_authority_verifier(**kwargs)
            entered_authority_verification.set()
            if not release_authority_verification.wait(timeout=10):
                raise RuntimeError("timed out waiting for concurrent addon disable")
            return result

        monkeypatch.setattr(
            server_module,
            "_verified_tortle_natural_armor_authority",
            pause_after_candidate_lookup,
        )
        raced_apply = asyncio.create_task(
            _call(
                server,
                "character_content_apply",
                {
                    "character_id": race_character["id"],
                    "artifact_id": TORTLE_NATURAL_ARMOR_ARTIFACT_ID,
                    "selection": {},
                    "expected_revision": race_character["revision"],
                    "idempotency_key": "raced-apply",
                },
            )
        )
        assert await asyncio.to_thread(entered_authority_verification.wait, 10)
        campaign_before_disable = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        await _call(
            server,
            "content_pack",
            {
                "action": "deactivate",
                "payload": {
                    "campaign_id": campaign["id"],
                    "kind": "addon",
                    "addon_id": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
                    "version": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
                },
                "expected_revision": campaign_before_disable["revision"],
                "idempotency_key": "disable-during-apply",
            },
        )
        release_authority_verification.set()
        with pytest.raises(ToolError, match="campaign revision conflict"):
            await raced_apply
        monkeypatch.setattr(
            server_module,
            "_verified_tortle_natural_armor_authority",
            original_authority_verifier,
        )
        race_after = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": race_character["id"]}},
        )
        assert race_after["revision"] == race_character["revision"]
        assert race_after["sheet"]["content"]["selections"] == []
        # Addon activation responses expose the activation/effective ruleset,
        # while the authoritative campaign revision is returned by the
        # campaign facade.  Read it after deactivation for the next guarded
        # mutation instead of assuming a deprecated top-level field.
        campaign_after_disable = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        reactivated = await _call(
            server,
            "content_pack",
            {
                "action": "activate",
                "payload": {
                    "campaign_id": campaign["id"],
                    "kind": "addon",
                    "addon_id": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
                    "version": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
                },
                "expected_revision": campaign_after_disable["revision"],
                "idempotency_key": "reactivate-after-race",
            },
        )
        assert reactivated["activation"]["enabled"] is True
        applied = await _call(
            server,
            "character_content_apply",
            {
                "character_id": character["id"],
                "artifact_id": TORTLE_NATURAL_ARMOR_ARTIFACT_ID,
                "selection": {},
                "expected_revision": character["revision"],
                "idempotency_key": "apply-tortle",
            },
        )
        assert applied["sheet"]["skills"]["survival"]["proficiency"] == "expertise"
        assert applied["derived"]["skills"]["survival"] == 4
        authority_secret = (config.home / "data" / ".content-authority-key").read_bytes()
        trusted_authorities = _verified_content_authority_ids(
            applied["sheet"],
            character_id=character["id"],
            secret=authority_secret,
        )
        assert trusted_authorities
        assert not _verified_content_authority_ids(
            applied["sheet"],
            character_id="different-character",
            secret=authority_secret,
        )
        apply_arguments = {
            "character_id": character["id"],
            "artifact_id": TORTLE_NATURAL_ARMOR_ARTIFACT_ID,
            "selection": {},
            "expected_revision": character["revision"],
            "idempotency_key": "apply-tortle",
        }
        receipts_after_apply = await _call(
            server,
            "campaign_rules",
            {"campaign_id": campaign["id"], "action": "receipts", "payload": {}},
        )
        assert await _call(server, "character_content_apply", apply_arguments) == applied
        assert (
            await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": character["id"]}},
            )
        )["revision"] == applied["revision"]
        assert (
            await _call(
                server,
                "campaign_rules",
                {"campaign_id": campaign["id"], "action": "receipts", "payload": {}},
            )
            == receipts_after_apply
        )
        close_server(server)
        server = create_server(config)
        assert await _call(server, "character_content_apply", apply_arguments) == applied
        assert (
            await _call(
                server,
                "campaign_rules",
                {"campaign_id": campaign["id"], "action": "receipts", "payload": {}},
            )
            == receipts_after_apply
        )
        sheet, armor_id = add_inventory_item(
            applied["sheet"],
            {
                "id": "plate-plus-three",
                "name": "+3 Plate",
                "kind": "armor",
                "mechanics": {
                    "base_ac": 18,
                    "category": "heavy",
                    "dexterity_mode": "none",
                    "magic_bonus": 3,
                    "stealth_disadvantage": True,
                },
            },
        )
        sheet, shield_id = add_inventory_item(
            sheet,
            {
                "id": "shield",
                "name": "Shield",
                "kind": "shield",
                "mechanics": {"ac_bonus": 2, "magic_bonus": 0},
            },
        )
        sheet = equip_inventory_item(sheet, armor_id, "armor")
        sheet = equip_inventory_item(sheet, shield_id, "shield")
        sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
        assert derive_character_sheet(sheet)["armor_class"] == 23
        persisted = await _call(
            server,
            "character_sheet_replace",
            {
                "character_id": character["id"],
                "sheet": sheet,
                "expected_revision": applied["revision"],
                "idempotency_key": "equip-tortle",
            },
        )
        assert persisted["derived"]["armor_class"] == 19
        attacker_sheet = default_character_sheet()
        attacker_sheet["abilities"]["strength"]["score"] = 18
        attacker_sheet["inventory"]["items"] = [
            {
                "id": "longsword",
                "name": "Longsword",
                "kind": "weapon",
                "equipped": True,
                "equipped_slot": "main_hand",
                "mechanics": {
                    "attack_type": "melee",
                    "attack_ability": "strength",
                    "damage_formula": "1d8",
                    "damage_type": "slashing",
                    "properties": ["versatile"],
                },
            }
        ]
        attacker_sheet["inventory"]["equipment_slots"]["main_hand"] = "longsword"
        attacker = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Attacker",
                    "sheet": attacker_sheet,
                },
                "idempotency_key": "attacker",
            },
        )
        close_server(server)
        restarted = create_server(config)
        restored = await _call(
            restarted,
            "character_query",
            {"view": "get", "payload": {"character_id": character["id"]}},
        )
        assert restored["derived"]["armor_class"] == 19
        assert restored["sheet"]["skills"]["survival"]["proficiency"] == "expertise"
        assert restored["derived"]["skills"]["survival"] == 4
        campaign_state = await _call(
            restarted,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        phase = await _call(
            restarted,
            "game_phase",
            {
                "campaign_id": campaign["id"],
                "action": "set",
                "tool_profile": "play",
                "expected_revision": campaign_state["revision"],
                "idempotency_key": "enter-play",
            },
        )
        checked = await _call(
            restarted,
            "character_check",
            {
                "campaign_id": campaign["id"],
                "action": "check",
                "payload": {
                    "actor_id": character["id"],
                    "kind": "check",
                    "ability": "survival",
                    "dc": 10,
                },
                "expected_revision": phase["campaign_revision"],
                "idempotency_key": "survival-check",
            },
        )
        assert checked["ability_modifier"] == 0
        assert checked["proficiency_bonus"] == 2
        assert checked["bonus"] == 2
        assert checked["total"] - checked["natural"] == 4
        campaign_after_check = await _call(
            restarted,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        started = await _call(
            restarted,
            "combat_start",
            {
                "positioning_mode": "grid",
                "battle_map": {"width_cells": 12, "height_cells": 12},
                "campaign_id": campaign["id"],
                "participant_ids": [attacker["id"], character["id"]],
                "participant_config": [
                    {
                        "actor_id": attacker["id"],
                        "initiative": 20,
                        "position": {"x": 0, "y": 0},
                        "disposition": "hostile",
                    },
                    {
                        "actor_id": character["id"],
                        "initiative": 10,
                        "position": {"x": 1, "y": 0},
                        "disposition": "friendly",
                    },
                ],
                "expected_revision": campaign_after_check["revision"],
                "idempotency_key": "combat-start",
            },
        )
        _, raw_attack = await restarted.call_tool(
            "combat_resolve_attack",
            {
                "campaign_id": campaign["id"],
                "actor_id": attacker["id"],
                "target_id": character["id"],
                "action": {"weapon_id": "longsword"},
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "attack",
            },
        )
        rolled = (
            raw_attack["result"]
            if isinstance(raw_attack, dict) and "action" in raw_attack
            else raw_attack
        )
        assert rolled["status"] == "pending_reaction"
        reactions = await _call(
            restarted,
            "combat_query",
            {
                "campaign_id": campaign["id"],
                "view": "reactions",
                "actor_id": character["id"],
            },
        )
        choice = reactions[0]
        resolved = await _call(
            restarted,
            "combat_choice",
            {
                "campaign_id": campaign["id"],
                "actor_id": character["id"],
                "action": "resolve_defense",
                "payload": {
                    "choice_id": choice["id"],
                    "selection": {"id": CORE_SHIELD_SPELL_ID, "cast_level": 1},
                },
                "expected_revision": rolled["campaign_revision"],
                "idempotency_key": "shield-reaction",
            },
        )
        assert resolved["result"]["hit"] is False
        after_rederive = await _call(
            restarted,
            "character_query",
            {"view": "get", "payload": {"character_id": character["id"]}},
        )
        assert after_rederive["derived"]["armor_class"] == 24
        assert after_rederive["derived"]["armor_class_breakdown"]["armor"]["ignored_for_ac"] is True
        assert TORTLE_NATURAL_ARMOR_ARTIFACT_ID in {
            effect["source"] for effect in after_rederive["sheet"]["effects"] if effect["active"]
        }
        assert "dnd5e.core.ac.tortle_natural_armor" in {
            receipt["mechanic_id"] for receipt in after_rederive["derived"]["rule_receipts"]
        }
        close_server(restarted)

    asyncio.run(exercise())


@pytest.mark.parametrize("tamper", ("artifact_game", "resolution_policy", "semantic_validation"))
def test_reserved_tortle_archive_rejects_installed_payload_tampering(
    tmp_path: Path, tamper: str
) -> None:
    repository_root = Path(__file__).resolve().parents[3]
    official_library = repository_root.parent / "SagaSmith-dnd-content-library" / "content-library"
    if not (official_library / "index.json").is_file():
        pytest.skip("requires the sibling finalized SagaSmith D&D content library")
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=repository_root / "skills",
        modulegen_skills_dir=tmp_path / "modulegen-skills",
        auto_seed_rules=True,
        official_content_library=official_library,
    )

    async def exercise() -> None:
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Tortle payload tamper", "edition": "2014", "idempotency_key": "campaign"},
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
                    "addon_id": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
                    "version": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
                },
                "expected_revision": profile["campaign_revision"],
                "idempotency_key": "activate",
            },
        )
        character = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Tortle",
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
                "artifact_id": TORTLE_NATURAL_ARMOR_ARTIFACT_ID,
                "selection": {},
                "expected_revision": character["revision"],
                "idempotency_key": "apply",
            },
        )
        before_campaign = await _call(
            server, "campaign_query", {"view": "get", "payload": {"campaign_id": campaign["id"]}}
        )
        before_character = await _call(
            server, "character_query", {"view": "get", "payload": {"character_id": character["id"]}}
        )
        database = Database(sqlite_database_url(config.database_path))
        try:
            with database.transaction() as session:
                row = session.get(
                    RulePackVersion,
                    {
                        "pack_id": _TORTLE_RULE_ID,
                        "version": next(iter(TORTLE_NATURAL_ARMOR_LEGACY_PACK_VERSIONS)),
                    },
                )
                assert row is not None
                payload = decode_state_document(
                    document_id=row.payload_document.id,
                    payload_codec=row.payload_document.payload_codec,
                    uncompressed_size=row.payload_document.uncompressed_size,
                    compressed_payload=bytes(row.payload_document.compressed_payload),
                )
                if tamper == "artifact_game":
                    artifact = next(
                        item
                        for item in payload["artifacts"]
                        if item.get("id") == TORTLE_NATURAL_ARMOR_ARTIFACT_ID
                    )
                    artifact.setdefault("game", {})["armor_class"] = 99
                elif tamper == "resolution_policy":
                    payload["manifest"]["resolution_policy"] = "tampered"
                else:
                    payload["manifest"]["semantic_validation"] = {
                        "complete": False,
                        "unresolved": [],
                    }
                encoded = encode_state_document(payload)
                document = RulePackPayload(
                    id=encoded.document_id,
                    payload_codec=encoded.payload_codec,
                    uncompressed_size=encoded.uncompressed_size,
                    compressed_payload=encoded.compressed_payload,
                )
                session.add(document)
                row.payload_document_id = document.id
        finally:
            database.dispose()
        with pytest.raises(ToolError, match="immutable official content archive"):
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": character["id"],
                    "artifact_id": TORTLE_NATURAL_ARMOR_ARTIFACT_ID,
                    "selection": {},
                    "expected_revision": applied["revision"],
                    "idempotency_key": f"tamper-{tamper}",
                },
            )
        after_campaign = await _call(
            server, "campaign_query", {"view": "get", "payload": {"campaign_id": campaign["id"]}}
        )
        after_character = await _call(
            server, "character_query", {"view": "get", "payload": {"character_id": character["id"]}}
        )
        assert after_campaign["revision"] == before_campaign["revision"]
        assert after_character["revision"] == before_character["revision"]
        assert (
            after_character["sheet"]["content"]["selections"]
            == before_character["sheet"]["content"]["selections"]
        )
        close_server(server)

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

    server = create_server(config)
    database = Database(sqlite_database_url(config.database_path))
    try:
        packs = RulePackService(database)
        for pack_id, version, edition in (
            (CORE_CONTENT_PACK_ID, CORE_CONTENT_PACK_VERSION, "2014"),
            (
                server_module.STANDARD_2014_CONTENT_PACK_ID,
                server_module.STANDARD_2014_CONTENT_PACK_VERSION,
                "2014",
            ),
            (
                server_module.CORE_2024_CONTENT_PACK_ID,
                server_module.CORE_2024_CONTENT_PACK_VERSION,
                "2024",
            ),
        ):
            installed = packs.get_version(pack_id, version)
            core = server_module.get_core_rule_pack(edition)
            assert installed.status == "installed"
            assert installed.manifest["native_provider_locks"] == [
                {
                    "id": core.id,
                    "version": core.version,
                    "edition": edition,
                    "fingerprint": core.fingerprint,
                    "mechanic_refs": installed.manifest["native_mechanic_refs"],
                }
            ]
        for pack_id, version, content_pack_id, content_version in (
            (
                SRD2014_PRESET_PACK_ID,
                SRD2014_PRESET_PACK_VERSION,
                CORE_CONTENT_PACK_ID,
                CORE_CONTENT_PACK_VERSION,
            ),
            (
                SRD2024_PRESET_PACK_ID,
                SRD2024_PRESET_PACK_VERSION,
                server_module.CORE_2024_CONTENT_PACK_ID,
                server_module.CORE_2024_CONTENT_PACK_VERSION,
            ),
        ):
            assert version == "2.1.0"
            installed = packs.get_version(pack_id, version)
            assert installed.status == "installed"
            assert installed.artifacts
            for artifact in installed.artifacts:
                actor = artifact["card"]["content_actor"]
                assert actor["version"] == version
                assert actor["sheet"]["inventory"]["external_items"] == []
                assert actor["provenance"]["pack"] == {
                    "id": content_pack_id,
                    "version": content_version,
                }
    finally:
        database.dispose()
        close_server(server)

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


@pytest.mark.fresh_database
def test_locked_scag_and_tortle_activate_exact_dependency_closure_apply_and_restart(
    tmp_path: Path,
) -> None:
    library = _locked_official_library()
    repository_root = Path(__file__).resolve().parents[3]
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=repository_root / "skills",
        modulegen_skills_dir=tmp_path / "modulegen-skills",
        auto_seed_rules=True,
        official_content_library=library,
    )
    locked = {item["id"]: item for item in load_official_expansion_lock()["packages"]}

    async def exercise() -> None:
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Official dependency closure",
                    "edition": "2014",
                    "idempotency_key": "official-closure-campaign",
                },
            )
            profile = await _call(
                server,
                "campaign_rules",
                {"campaign_id": campaign["id"], "action": "get_profile"},
            )
            scag = await _call(
                server,
                "content_pack",
                {
                    "action": "activate",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "kind": "addon",
                        "addon_id": _SCAG_ADDON_ID,
                        "version": locked[_SCAG_ADDON_ID]["version"],
                    },
                    "expected_revision": profile["campaign_revision"],
                    "idempotency_key": "activate-locked-scag",
                },
            )
            assert {item["pack_id"] for item in scag["effective_ruleset"]["lock"]} == {
                CORE_CONTENT_PACK_ID,
                _SCAG_RULE_ID,
            }

            current_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            tortle = await _call(
                server,
                "content_pack",
                {
                    "action": "activate",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "kind": "addon",
                        "addon_id": _TORTLE_ADDON_ID,
                        "version": locked[_TORTLE_ADDON_ID]["version"],
                    },
                    "expected_revision": current_campaign["revision"],
                    "idempotency_key": "activate-locked-tortle",
                },
            )
            expected_lock = {CORE_CONTENT_PACK_ID, _SCAG_RULE_ID, _TORTLE_RULE_ID}
            assert {
                item["pack_id"] for item in tortle["effective_ruleset"]["lock"]
            } == expected_lock

            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            character = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Locked expansion character",
                        "sheet": sheet,
                    },
                    "idempotency_key": "official-closure-character",
                },
            )
            for index, artifact_id in enumerate((_CITY_WATCH_ID, _TORTLE_ID)):
                applied = await _call(
                    server,
                    "character_content_apply",
                    {
                        "character_id": character["id"],
                        "artifact_id": artifact_id,
                        "selection": await _selection_for(
                            server,
                            campaign["id"],
                            artifact_id,
                        ),
                        "expected_revision": character["revision"],
                        "idempotency_key": f"apply-locked-official-{index}",
                    },
                )
                character = applied
            assert character["sheet"]["progression"]["background"] == "City Watch"
            assert character["sheet"]["progression"]["species"] == "Tortle"
            character_id = character["id"]
            campaign_id = campaign["id"]
        finally:
            close_server(server)

        restarted = create_server(config)
        try:
            profile = await _call(
                restarted,
                "campaign_rules",
                {"campaign_id": campaign_id, "action": "get_profile"},
            )
            assert {item["pack_id"] for item in profile["effective"]["lock"]} == expected_lock
            restored = await _call(
                restarted,
                "character_query",
                {"view": "get", "payload": {"character_id": character_id}},
            )
            assert restored["sheet"]["progression"]["background"] == "City Watch"
            assert restored["sheet"]["progression"]["species"] == "Tortle"
            assert {item["artifact_id"] for item in restored["sheet"]["content"]["selections"]} >= {
                _CITY_WATCH_ID,
                _TORTLE_ID,
            }
        finally:
            close_server(restarted)

    asyncio.run(exercise())
