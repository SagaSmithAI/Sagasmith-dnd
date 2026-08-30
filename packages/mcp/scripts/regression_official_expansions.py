"""Exercise the built-in official-expansion mount through public MCP tools.

The commercial archives stay in an authorized local content library.  Output
contains only package identities, aggregate counts, and behavioral assertions;
it never exports source prose, blobs, credentials, or local filesystem paths.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.core_content import PACK_ID as CORE_CONTENT_PACK_ID
from sagasmith_dnd.core_content import PACK_VERSION as CORE_CONTENT_PACK_VERSION
from sagasmith_dnd.official_expansions import official_expansion_catalog

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server

_ADVANCEMENT_SOURCE = "bundled:srd2014/03_Characterization/Beyond_1st_Level.md"
_ARTIFICER = (
    "dnd5e.addon.rulebook.d-d-5e-eberron-rising-from-the-last-war."
    "31293633134f.class.artificer"
)
_BATTLE_SMITH = (
    "dnd5e.addon.rulebook.d-d-5e-eberron-rising-from-the-last-war."
    "31293633134f.subclass.battle-smith"
)
_TORTLE = (
    "dnd5e.addon.rulebook.d-d-5e-the-tortle-package."
    "e3234de670da.species.tortle"
)
_CITY_WATCH = (
    "dnd5e.addon.rulebook.d-d-5e-sword-coast-adventurer-s-guide."
    "16e6a243ef0a.background.city-watch"
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content-library", type=Path, required=True)
    parser.add_argument("--home", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


async def _call(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    _, response = await server.call_tool(name, arguments)
    return response.get("result", response) if isinstance(response, dict) else response


async def _apply(
    server: Any,
    character: dict[str, Any],
    artifact_id: str,
    key: str,
    selection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = await _call(
        server,
        "character_content_apply",
        {
            "character_id": character["id"],
            "artifact_id": artifact_id,
            "selection": selection or {},
            "expected_revision": character["revision"],
            "idempotency_key": key,
        },
    )
    if "revision" not in result:
        diagnostic = {
            field: result.get(field)
            for field in ("status", "ruling_kind", "reason", "errors")
            if result.get(field) is not None
        }
        raise RuntimeError(f"official artifact did not apply: {artifact_id}: {diagnostic}")
    return result


async def _catalog_selection(
    server: Any,
    campaign_id: str,
    artifact_id: str,
    *,
    target_class_name: str | None = None,
) -> dict[str, Any]:
    """Choose deterministic valid inputs from the public reviewed catalog contract."""

    entries = await _call(
        server,
        "character_query",
        {
            "view": "catalog",
            "payload": {"campaign_id": campaign_id, "query": artifact_id},
        },
    )
    entry = next((item for item in entries if item.get("id") == artifact_id), None)
    if entry is None:
        raise RuntimeError(f"official artifact is missing from the active catalog: {artifact_id}")
    requirements = dict(entry.get("selection_requirements") or {})
    selection: dict[str, Any] = {}
    skill_count = int(requirements.get("skill_choice_count", 0) or 0)
    tool_count = int(requirements.get("tool_choice_count", 0) or 0)
    language_count = int(requirements.get("language_count", 0) or 0)
    if skill_count:
        selection["skills"] = list(requirements.get("skill_options") or [])[:skill_count]
    if tool_count:
        selection["tools"] = list(requirements.get("tool_options") or [])[:tool_count]
    if language_count:
        language_options = list(requirements.get("language_options") or [])
        if len(language_options) < language_count and requirements.get("allow_any_language"):
            language_options.extend(
                item
                for item in ("Draconic", "Dwarvish", "Elvish")
                if item not in language_options
            )
        selection["languages"] = language_options[:language_count]
    equipment_options = list(requirements.get("equipment_package_options") or [])
    if equipment_options:
        selection["equipment_package"] = equipment_options[0]
    if target_class_name is not None:
        selection["target_class_name"] = target_class_name
    return selection


async def _all_catalog_entries(
    server: Any,
    campaign_id: str,
    kind: str,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = await _call(
            server,
            "character_query",
            {
                "view": "catalog",
                "payload": {
                    "campaign_id": campaign_id,
                    "kind": kind,
                    "limit": 100,
                    "offset": offset,
                },
            },
        )
        if not page:
            return entries
        entries.extend(page)
        if len(page) < 100:
            return entries
        offset += len(page)


async def _run(content_library: Path, home: Path) -> dict[str, Any]:
    base = McpConfig.from_environment()
    config = McpConfig(
        home=home,
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=base.dnd_skills_dir,
        modulegen_skills_dir=base.modulegen_skills_dir,
        auto_seed_rules=True,
        rule_import_roots=(),
        module_import_roots=(),
        official_content_library=content_library.expanduser().resolve(),
    )
    server = create_server(config)
    campaign = await _call(
        server,
        "campaign_create",
        {
            "name": "Official expansion 2014 regression",
            "edition": "2014",
            "idempotency_key": "official-expansion-campaign",
        },
    )
    profile = await _call(
        server,
        "campaign_rules",
        {"campaign_id": campaign["id"], "action": "get_profile"},
    )
    expected = {item["id"]: item for item in official_expansion_catalog("2014")}
    if set(item["id"] for item in profile["available_official_expansions"]) != set(expected):
        raise RuntimeError("campaign profile does not expose the complete 2014 registry")
    if profile["official_expansion_mount"] != {
        "configured": True,
        "installed": len(expected),
        "available": len(expected),
        "support_installed": 1,
        "support_available": 1,
    }:
        raise RuntimeError("official expansion mount is incomplete")

    listed = await _call(
        server,
        "content_pack",
        {
            "action": "list",
            "payload": {"campaign_id": campaign["id"], "kind": "addon"},
        },
    )
    official = [item for item in listed if item.get("built_in_official_expansion")]
    if {item["addon_id"] for item in official} != set(expected):
        raise RuntimeError("stored official expansion inventory differs from the registry")
    if any(item.get("activation") is not None for item in official):
        raise RuntimeError("official expansions must be inactive by default")
    if any(item.get("editions") != ["2014"] for item in official):
        raise RuntimeError("official expansion inventory leaked outside the 2014 edition")

    campaign_2024 = await _call(
        server,
        "campaign_create",
        {
            "name": "Official expansion 2024 isolation regression",
            "edition": "2024",
            "idempotency_key": "official-expansion-campaign-2024",
        },
    )
    profile_2024 = await _call(
        server,
        "campaign_rules",
        {"campaign_id": campaign_2024["id"], "action": "get_profile"},
    )
    if profile_2024["available_official_expansions"]:
        raise RuntimeError("2014 official expansions were advertised to a 2024 campaign")
    try:
        await _call(
            server,
            "content_pack",
            {
                "action": "activate",
                "payload": {
                    "campaign_id": campaign_2024["id"],
                    "kind": "addon",
                    "addon_id": official[0]["addon_id"],
                    "version": official[0]["version"],
                },
                "expected_revision": profile_2024["campaign_revision"],
                "idempotency_key": "reject-official-expansion-in-2024",
            },
        )
    except Exception as error:
        if "edition" not in str(error).casefold() and "2014" not in str(error):
            raise RuntimeError("2024 activation failed for an unrelated reason") from error
    else:
        raise RuntimeError("a 2014 official expansion activated in a 2024 campaign")

    revision = int(profile["campaign_revision"])
    core_activated = await _call(
        server,
        "content_pack",
        {
            "action": "activate",
            "payload": {
                "campaign_id": campaign["id"],
                "kind": "core_rules",
                "pack_id": CORE_CONTENT_PACK_ID,
                "version": CORE_CONTENT_PACK_VERSION,
            },
            "expected_revision": revision,
            "idempotency_key": "activate-official-core-content",
        },
    )
    if core_activated["activation"]["enabled"] is not True:
        raise RuntimeError("the exact 2014 core content Pack did not activate")
    revision = int(core_activated["campaign_revision"])
    support = [item for item in listed if item.get("built_in_official_core_support")]
    if len(support) != 1 or support[0].get("activation") is not None:
        raise RuntimeError("the exact 2014 core support Pack was not mounted inactive")
    activation_order = [*support, *sorted(official, key=lambda value: value["addon_id"])]
    for index, item in enumerate(activation_order):
        activated = await _call(
            server,
            "content_pack",
            {
                "action": "activate",
                "payload": {
                    "campaign_id": campaign["id"],
                    "kind": "addon",
                    "addon_id": item["addon_id"],
                    "version": item["version"],
                },
                "expected_revision": revision,
                "idempotency_key": f"activate-official-{index}",
            },
        )
        if activated["activation"]["enabled"] is not True:
            raise RuntimeError(f"official expansion did not activate: {item['addon_id']}")
        current = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        revision = int(current["revision"])

    catalog_counts: Counter[str] = Counter()
    official_pack_ids = {
        str(component["id"])
        for item in official
        for component in item.get("components", [])
        if component.get("kind") == "rule_pack"
    }
    for kind in (
        "background",
        "class",
        "feat",
        "feature",
        "item",
        "species",
        "spell",
        "statblock",
        "subclass",
    ):
        entries = await _all_catalog_entries(server, campaign["id"], kind)
        catalog_counts[kind] = sum(
            1 for item in entries if str(item.get("pack_id") or "") in official_pack_ids
        )
    declared_catalog_counts: Counter[str] = Counter()
    declared_selection_counts: Counter[str] = Counter()
    for item in expected.values():
        declared_catalog_counts.update(item["content_summary"])
        declared_selection_counts.update(item["selection_ready"])
    if catalog_counts != declared_catalog_counts:
        raise RuntimeError(
            f"active catalog coverage mismatch: {dict(catalog_counts)} != "
            f"{dict(declared_catalog_counts)}"
        )

    sheet = default_character_sheet()
    character = await _call(
        server,
        "character_create_from",
        {
            "mode": "direct",
            "payload": {
                "campaign_id": campaign["id"],
                "name": "Official expansion tester",
                "sheet": sheet,
            },
            "idempotency_key": "official-expansion-character",
        },
    )
    selections = {
        artifact_id: await _catalog_selection(server, campaign["id"], artifact_id)
        for artifact_id in (_ARTIFICER, _TORTLE, _CITY_WATCH)
    }
    applied_ids = []
    for index, artifact_id in enumerate((_ARTIFICER, _TORTLE, _CITY_WATCH)):
        applied = await _apply(
            server,
            character,
            artifact_id,
            f"apply-official-{index}",
            selections[artifact_id],
        )
        character = {
            "id": character["id"],
            "revision": applied["revision"],
            "sheet": applied["sheet"],
        }
        applied_ids.append(artifact_id)

    for level in (2, 3):
        advanced = await _call(
            server,
            "character_state_change",
            {
                "character_id": character["id"],
                "action": "level_advance",
                "payload": {
                    "class_name": "Artificer",
                    "hp_method": "fixed",
                    "reason": "official expansion regression",
                    "source_ref": _ADVANCEMENT_SOURCE,
                },
                "expected_revision": character["revision"],
                "idempotency_key": f"official-expansion-level-{level}",
            },
        )
        character = advanced["character"]

    subclass = await _apply(
        server,
        character,
        _BATTLE_SMITH,
        "apply-official-subclass",
        await _catalog_selection(
            server,
            campaign["id"],
            _BATTLE_SMITH,
            target_class_name="Artificer",
        ),
    )
    character = {
        "id": character["id"],
        "revision": subclass["revision"],
        "sheet": subclass["sheet"],
    }
    applied_ids.append(_BATTLE_SMITH)
    classes = character["sheet"]["progression"]["classes"]
    if not any(
        item.get("name") == "Artificer"
        and item.get("level") == 3
        and item.get("subclass") == "Battle Smith"
        for item in classes
    ):
        raise RuntimeError("official Artificer/Battle Smith character build did not settle")

    current = await _call(
        server,
        "campaign_query",
        {"view": "get", "payload": {"campaign_id": campaign["id"]}},
    )
    await _call(
        server,
        "game_phase",
        {
            "campaign_id": campaign["id"],
            "action": "set",
            "tool_profile": "play",
            "expected_revision": current["revision"],
            "idempotency_key": "official-expansion-enter-play",
        },
    )
    current = await _call(
        server,
        "campaign_query",
        {"view": "get", "payload": {"campaign_id": campaign["id"]}},
    )
    settled = await _call(
        server,
        "character_check",
        {
            "campaign_id": campaign["id"],
            "action": "check",
            "payload": {
                "actor_id": character["id"],
                "kind": "check",
                "ability": "intelligence",
                "dc": 10,
            },
            "expected_revision": current["revision"],
            "idempotency_key": "official-expansion-intelligence-check",
        },
    )
    if not isinstance(settled.get("total"), int):
        raise RuntimeError("the expansion-built character did not produce a settled check")

    return {
        "schema": "sagasmith.dnd-official-expansion-regression.v1",
        "edition": "2014",
        "packages": len(official),
        "activated": len(official),
        "catalog_entries": sum(catalog_counts.values()),
        "catalog_entries_by_kind": dict(sorted(catalog_counts.items())),
        "selection_ready": sum(declared_selection_counts.values()),
        "selection_ready_by_kind": dict(sorted(declared_selection_counts.items())),
        "character": {
            "class": "Artificer",
            "level": 3,
            "subclass": "Battle Smith",
            "species": "Tortle",
            "background": "City Watch",
            "applied_artifacts": len(applied_ids),
            "level_advancements": 2,
        },
        "settlement": {
            "kind": "intelligence_check",
            "committed": True,
            "has_rule_receipts": bool(settled.get("rule_receipts")),
        },
        "content_exported": False,
        "passed": True,
    }


def main() -> int:
    args = _arguments()
    if args.home is not None:
        report = asyncio.run(
            _run(
                args.content_library,
                args.home.expanduser().resolve(),
            )
        )
    else:
        with tempfile.TemporaryDirectory(prefix="sagasmith-official-expansions-") as value:
            report = asyncio.run(_run(args.content_library, Path(value)))
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.expanduser().resolve().write_text(output, encoding="utf-8")
    print(output)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
