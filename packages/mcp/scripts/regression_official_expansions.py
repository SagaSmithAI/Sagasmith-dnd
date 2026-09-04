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
from sagasmith_dnd_mcp.server import close_server, create_server

_ADVANCEMENT_SOURCE = "bundled:srd2014/03_Characterization/Beyond_1st_Level.md"
_ARTIFICER = (
    "dnd5e.addon.rulebook.d-d-5e-eberron-rising-from-the-last-war.31293633134f.class.artificer"
)
_BATTLE_SMITH = (
    "dnd5e.addon.rulebook.d-d-5e-eberron-rising-from-the-last-war."
    "31293633134f.subclass.battle-smith"
)
_TORTLE = "dnd5e.addon.rulebook.d-d-5e-the-tortle-package.e3234de670da.species.tortle"
_CITY_WATCH = (
    "dnd5e.addon.rulebook.d-d-5e-sword-coast-adventurer-s-guide.16e6a243ef0a.background.city-watch"
)
_ABERRANT_DRAGONMARK = (
    "dnd5e.addon.rulebook.d-d-5e-eberron-rising-from-the-last-war."
    "31293633134f.feat.aberrant-dragonmark"
)
_BATTLE_READY = (
    "dnd5e.addon.rulebook.d-d-5e-eberron-rising-from-the-last-war.31293633134f.feature.battle-ready"
)
_ARMOR_OF_GLEAMING = (
    "dnd5e.addon.rulebook.d-d-5e-xanathar-s-guide-to-everything.72d56f4f8dae.item.armor-of-gleaming"
)
_BOOMING_BLADE = (
    "dnd5e.addon.rulebook.d-d-5e-tasha-s-cauldron-of-everything.89a729b37a4b.spell.booming-blade"
)
_GREEN_FLAME_BLADE = (
    "dnd5e.addon.rulebook.d-d-5e-tasha-s-cauldron-of-everything."
    "89a729b37a4b.spell.green-flame-blade"
)
_CURE_WOUNDS = "dnd5e.content.srd2014.spell.cure-wounds"
_ARTIFICER_INFUSIONS = (
    "Enhanced Arcane Focus",
    "Enhanced Defense",
    "Enhanced Weapon",
    "Repeating Shot",
)
_LIGHT = "dnd5e.content.srd2014.spell.light"
_BURNING_HANDS = "dnd5e.content.srd2014.spell.burning-hands"
_ARTIFICER_PREFIX = _ARTIFICER.rsplit(".class.", 1)[0]
_ARTIFICER_FEATURE_ORDER = tuple(
    f"{_ARTIFICER_PREFIX}.feature.{slug}"
    for slug in (
        "magical-tinkering",
        "spellcasting",
        "infuse-item",
        "artificer-specialist",
        "the-right-tool-for-the-job",
        "tool-proficiency-battle-smith",
        "battle-smith-spells",
        "battle-ready",
        "steel-defender",
    )
)
_REQUIRED_ARTIFICER_FEATURES = set(_ARTIFICER_FEATURE_ORDER) | {
    f"{_ARTIFICER_PREFIX}.feature.{name.lower().replace(' ', '-')}" for name in _ARTIFICER_INFUSIONS
}


def _build_failures(sheet: dict[str, Any], follow_ups: list[dict[str, Any]]) -> list[str]:
    """Reject incomplete level-three builds, even when their receipts persist."""
    failures = []
    content = dict(sheet.get("content") or {})
    features = {str(item.get("id") or "") for item in content.get("features", [])}
    required = _REQUIRED_ARTIFICER_FEATURES | {
        str(item["artifact_id"])
        for follow_up in follow_ups
        for item in follow_up.get("feature_artifacts", [])
    }
    if required - features:
        failures.append("missing_required_features")
    spells = list(content.get("spells") or [])
    cantrips = [
        spell
        for spell in spells
        if spell.get("level") == 0
        and dict(spell.get("grant") or {}).get("source_type") == "class"
        and str(dict(spell.get("grant") or {}).get("source_key") or "").casefold() == "artificer"
        and dict(spell.get("access") or {}).get("known") is True
    ]
    if len(cantrips) != 2 or len({spell.get("id") for spell in cantrips}) != 2:
        failures.append("initial_artificer_cantrips_incomplete")
    preparation = dict(dict(sheet.get("spellcasting") or {}).get("preparation") or {})
    selected = list(preparation.get("selected_spell_ids") or [])
    # This deterministic scenario starts at INT 10; neither Tortle nor the feat changes it.
    if preparation.get("max_prepared") != 1 or len(selected) != 1:
        failures.append("prepared_spell_selection_incomplete")
    elif not any(
        spell.get("id") == selected[0]
        and spell.get("level") == 1
        and dict(spell.get("grant") or {}).get("source_type") == "class"
        and dict(spell.get("access") or {}).get("prepared") is True
        and dict(spell.get("access") or {}).get("always_prepared") is not True
        for spell in spells
    ):
        failures.append("prepared_spell_not_materialized")
    for name in ("Heroism", "Shield"):
        if not any(
            str(spell.get("name") or "").casefold() == name.casefold()
            and dict(spell.get("grant") or {}).get("source_type") == "subclass"
            and dict(spell.get("grant") or {}).get("source_key") == "Battle Smith"
            and dict(spell.get("access") or {}).get("prepared") is True
            and dict(spell.get("access") or {}).get("always_prepared") is True
            and spell.get("id") not in selected
            for spell in spells
        ):
            failures.append(f"missing_battle_smith_spell_{name.casefold()}")
    # Unknown or newly introduced follow-up requirements require deliberate support.
    if any(
        follow_up.get("prepared_spell_event")
        or any(
            int(value)
            for key, value in dict(follow_up.get("spell_choices") or {}).items()
            if key != "cantrips_to_add"
        )
        for follow_up in follow_ups
    ):
        failures.append("unhandled_follow_up")
    return failures


_EXPECTED_MATERIALIZERS = {
    "background": "dnd5e.character.background.v1",
    "class": "dnd5e.character.base_class.v1",
    "feat": "dnd5e.character.feat.v1",
    "feature": "dnd5e.character.feature.v1",
    "item": "dnd5e.character.inventory_item.v1",
    "species": "dnd5e.character.species.v1",
    "spell": "dnd5e.character.spell.v1",
    "subclass": "dnd5e.character.subclass.v1",
}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--content-library", type=Path, required=True)
    parser.add_argument("--home", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


async def _call(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    response = await _call_raw(server, name, arguments)
    return response.get("result", response) if isinstance(response, dict) else response


async def _call_raw(server: Any, name: str, arguments: dict[str, Any]) -> Any:
    _, response = await server.call_tool(name, arguments)
    return response


async def _apply(
    server: Any,
    character: dict[str, Any],
    entry: dict[str, Any],
    key: str,
    *,
    ruleset_fingerprint: str,
    selection: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    artifact_id = str(entry["id"])
    selected = selection or {}
    result = await _call(
        server,
        "character_content_apply",
        {
            "character_id": character["id"],
            "artifact_id": artifact_id,
            "selection": selected,
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
    runtime_context = dict(entry.get("runtime_context") or {})
    contract = dict(runtime_context.get("selection_contract") or {})
    receipts = list(result.get("rule_receipts") or [])
    if len(receipts) != 1:
        raise RuntimeError(
            f"official artifact application returned {len(receipts)} content receipts: "
            f"{artifact_id}"
        )
    receipt = dict(receipts[0])
    receipt_selection = selected
    if entry.get("kind") in {"class", "species", "background", "subclass"}:
        selections = [
            item
            for item in dict(result["sheet"].get("content") or {}).get("selections", [])
            if item.get("artifact_id") == artifact_id
        ]
        if len(selections) != 1 or any(
            selections[0].get(field) != entry.get(catalog_field)
            for field, catalog_field in (
                ("kind", "kind"),
                ("pack_id", "pack_id"),
                ("pack_version", "pack_version"),
            )
        ):
            raise RuntimeError(f"official selection provenance mismatch: {artifact_id}")
        # Materializers normalize choices and attach server-signed source authority.
        # Compare the whole receipt with the resulting record, and independently
        # ensure the requested choices were retained (not replaced by other choices).
        receipt_selection = dict(selections[0].get("selection") or {})
        for field, requested in selected.items():
            observed = receipt_selection.get(field)
            if field in {"skills", "tools", "languages"} and isinstance(requested, list):
                matches = isinstance(observed, list) and [
                    str(value).strip().casefold() for value in observed
                ] == [str(value).strip().casefold() for value in requested]
            else:
                matches = observed == requested
            if not matches:
                raise RuntimeError(f"official selection changed requested {field}: {artifact_id}")
    expected_receipt = {
        "ruleset_fingerprint": ruleset_fingerprint,
        "mechanic_id": str(contract.get("materializer") or ""),
        "event": "character.content.apply",
        "artifact_id": artifact_id,
        "character_id": character["id"],
        "pack_id": str(entry.get("pack_id") or ""),
        "pack_version": str(entry.get("pack_version") or ""),
        "artifact_content_hash": str(contract.get("reviewed_content_hash") or ""),
        "reviewed_content_hash": str(contract.get("reviewed_content_hash") or ""),
        "selection": receipt_selection,
        "rule_refs": list(entry.get("rule_refs") or []),
    }
    if artifact_id == _TORTLE:
        authority_id = receipt.get("content_authority_id")
        if (
            not isinstance(authority_id, str)
            or len(authority_id) != 32
            or any(value not in "0123456789abcdef" for value in authority_id)
        ):
            raise RuntimeError("official Tortle receipt lacks its server-issued authority id")
        expected_receipt["content_authority_id"] = authority_id
    if receipt != expected_receipt:
        mismatches = {
            key: {"expected": expected_receipt.get(key), "actual": receipt.get(key)}
            for key in sorted(set(expected_receipt) | set(receipt))
            if expected_receipt.get(key) != receipt.get(key)
        }
        raise RuntimeError(
            f"official artifact content receipt mismatch: {artifact_id}: {mismatches}"
        )
    return result, receipt


async def _catalog_selection(
    server: Any,
    campaign_id: str,
    artifact_id: str,
    *,
    expected_kind: str,
    official_pack_ids: set[str],
    target_class_name: str | None = None,
    selection_overrides: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Choose deterministic valid inputs from the public reviewed catalog contract."""

    entries = await _call(
        server,
        "character_query",
        {
            "view": "catalog",
            "payload": {
                "campaign_id": campaign_id,
                "query": artifact_id,
                "include_context": True,
            },
        },
    )
    entry = next((item for item in entries if item.get("id") == artifact_id), None)
    if entry is None:
        raise RuntimeError(f"official artifact is missing from the active catalog: {artifact_id}")
    if entry.get("kind") != expected_kind:
        raise RuntimeError(f"official artifact has the wrong catalog kind: {artifact_id}")
    if str(entry.get("pack_id") or "") not in official_pack_ids:
        raise RuntimeError(
            f"official artifact resolved outside the official pack set: {artifact_id}"
        )
    if entry.get("application_state") != "selection_ready":
        raise RuntimeError(f"official artifact is not selection-ready: {artifact_id}")
    runtime_context = dict(entry.get("runtime_context") or {})
    contract = dict(runtime_context.get("selection_contract") or {})
    if contract.get("status") != "ready":
        raise RuntimeError(f"official artifact has no ready selection contract: {artifact_id}")
    if contract.get("materializer") != _EXPECTED_MATERIALIZERS[expected_kind]:
        raise RuntimeError(f"official artifact uses an unexpected materializer: {artifact_id}")
    reviewed_hash = str(contract.get("reviewed_content_hash") or "")
    if len(reviewed_hash) != 64 or any(value not in "0123456789abcdef" for value in reviewed_hash):
        raise RuntimeError(f"official artifact has an invalid reviewed content hash: {artifact_id}")
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
            # This build exercises ordinary choices, not DM authorization of
            # exotic languages such as Draconic.
            language_options.extend(
                item for item in ("Dwarvish", "Elvish", "Giant") if item not in language_options
            )
        selection["languages"] = language_options[:language_count]
    equipment_options = list(requirements.get("equipment_package_options") or [])
    if equipment_options:
        selection["equipment_package"] = equipment_options[0]
    if target_class_name is not None:
        selection["target_class_name"] = target_class_name
    selection.update(selection_overrides or {})
    return entry, selection


async def _finish_artificer_build(
    server: Any,
    campaign_id: str,
    character: dict[str, Any],
    *,
    official_pack_ids: set[str],
    ruleset_fingerprint: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Consume explicit feature/preparation work; do not synthesize sheet grants."""
    receipts = {}
    for artifact_id in _ARTIFICER_FEATURE_ORDER:
        if any(
            item.get("id") == artifact_id
            for item in character["sheet"].get("content", {}).get("features", [])
        ):
            continue
        entry, selection = await _catalog_selection(
            server,
            campaign_id,
            artifact_id,
            expected_kind="feature",
            official_pack_ids=official_pack_ids,
            selection_overrides=(
                {"infusions": list(_ARTIFICER_INFUSIONS)}
                if artifact_id == f"{_ARTIFICER_PREFIX}.feature.infuse-item"
                else {}
            ),
        )
        result, receipt = await _apply(
            server,
            character,
            entry,
            f"apply-official-feature-{artifact_id.rsplit('.', 1)[-1]}",
            ruleset_fingerprint=ruleset_fingerprint,
            selection=selection,
        )
        character = {
            "id": character["id"],
            "revision": result["revision"],
            "sheet": result["sheet"],
        }
        receipts[artifact_id] = receipt
    prepared = await _call(
        server,
        "character_spell_prepare",
        {
            "character_id": character["id"],
            "mode": "replace_all",
            "payload": {"spell_ids": [_CURE_WOUNDS], "event": "setup"},
            "expected_revision": character["revision"],
            "idempotency_key": "official-expansion-prepare-spells",
        },
    )
    updated = dict(prepared.get("character") or {})
    if (
        updated.get("id") != character["id"]
        or updated.get("revision") != character["revision"] + 1
        or not isinstance(updated.get("sheet"), dict)
    ):
        raise RuntimeError("ordinary Artificer spell preparation did not settle")
    return updated, receipts


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


def _create_regression_server(content_library: Path, home: Path) -> Any:
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
    return create_server(config)


async def _run(server: Any) -> tuple[dict[str, Any], dict[str, Any]]:
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
    selection_counts: Counter[str] = Counter()
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
        selection_counts[kind] = sum(
            1
            for item in entries
            if str(item.get("pack_id") or "") in official_pack_ids
            and item.get("application_state") == "selection_ready"
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
    if selection_counts != declared_selection_counts:
        raise RuntimeError(
            f"active selection-ready coverage mismatch: {dict(selection_counts)} != "
            f"{dict(declared_selection_counts)}"
        )

    explained = await _call(
        server,
        "campaign_rules",
        {"campaign_id": campaign["id"], "action": "explain", "payload": {}},
    )
    ruleset_fingerprint = str(explained.get("fingerprint") or "")
    if not ruleset_fingerprint:
        raise RuntimeError("the activated official ruleset has no fingerprint")

    sheet = default_character_sheet()
    sheet["abilities"]["constitution"]["score"] = 11
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
    initial_cases = (
        ("class", _ARTIFICER, {}),
        ("species", _TORTLE, {}),
        ("background", _CITY_WATCH, {}),
        (
            "feat",
            _ABERRANT_DRAGONMARK,
            {
                "spell_choices": {
                    "cantrip": [_LIGHT],
                    "level_1_spell": [_BURNING_HANDS],
                }
            },
        ),
        ("item", _ARMOR_OF_GLEAMING, {}),
        ("spell", _BOOMING_BLADE, {"source_class": "Artificer", "method": "known"}),
        ("spell", _GREEN_FLAME_BLADE, {"source_class": "Artificer", "method": "known"}),
    )
    content_receipts: dict[str, dict[str, Any]] = {}
    applied_ids: list[str] = []
    materializers: set[str] = set()
    for index, (kind, artifact_id, overrides) in enumerate(initial_cases):
        entry, selection = await _catalog_selection(
            server,
            campaign["id"],
            artifact_id,
            expected_kind=kind,
            official_pack_ids=official_pack_ids,
            selection_overrides=overrides,
        )
        applied, receipt = await _apply(
            server,
            character,
            entry,
            f"apply-official-{index}",
            ruleset_fingerprint=ruleset_fingerprint,
            selection=selection,
        )
        character = {
            "id": character["id"],
            "revision": applied["revision"],
            "sheet": applied["sheet"],
        }
        applied_ids.append(artifact_id)
        content_receipts[artifact_id] = receipt
        materializers.add(str(receipt["mechanic_id"]))
        if kind == "class":
            initial_choices = dict(
                dict(applied.get("class_materialization") or {}).get("spellcasting") or {}
            ).get("spell_choices")
            if initial_choices != {"cantrips_to_add": 2, "leveled_spells_to_add": 0}:
                raise RuntimeError(
                    "Artificer initial spell-choice obligations changed or are absent"
                )

    if character["sheet"]["abilities"]["constitution"]["score"] != 12:
        raise RuntimeError("the official feat ability increase did not materialize")

    follow_ups = []
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
        follow_ups.append(dict(advanced["advancement"]["follow_up"]))

    subclass_entry, subclass_selection = await _catalog_selection(
        server,
        campaign["id"],
        _BATTLE_SMITH,
        expected_kind="subclass",
        official_pack_ids=official_pack_ids,
        target_class_name="Artificer",
    )
    subclass, subclass_receipt = await _apply(
        server,
        character,
        subclass_entry,
        "apply-official-subclass",
        ruleset_fingerprint=ruleset_fingerprint,
        selection=subclass_selection,
    )
    character = {
        "id": character["id"],
        "revision": subclass["revision"],
        "sheet": subclass["sheet"],
    }
    applied_ids.append(_BATTLE_SMITH)
    content_receipts[_BATTLE_SMITH] = subclass_receipt
    materializers.add(str(subclass_receipt["mechanic_id"]))

    character, feature_receipts = await _finish_artificer_build(
        server,
        campaign["id"],
        character,
        official_pack_ids=official_pack_ids,
        ruleset_fingerprint=ruleset_fingerprint,
    )
    applied_ids.extend(feature_receipts)
    content_receipts.update(feature_receipts)
    materializers.update(str(receipt["mechanic_id"]) for receipt in feature_receipts.values())
    if materializers != set(_EXPECTED_MATERIALIZERS.values()):
        raise RuntimeError("the regression did not exercise every selection materializer kind")
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
    settlement = await _call_raw(
        server,
        "character_check",
        {
            "campaign_id": campaign["id"],
            "action": "check",
            "payload": {
                "actor_id": character["id"],
                "kind": "save",
                "ability": "constitution",
                "dc": 10,
            },
            "expected_revision": current["revision"],
            "idempotency_key": "official-expansion-constitution-save",
        },
    )
    if settlement.get("status") != "committed":
        raise RuntimeError("the expansion-built character save did not commit")
    settled = dict(settlement.get("result") or {})
    if not isinstance(settled.get("total"), int) or not isinstance(settled.get("natural"), int):
        raise RuntimeError("the expansion-built character did not produce a settled save")
    if settled.get("ability_modifier") != 1 or settled.get("proficiency_bonus") != 2:
        raise RuntimeError("the applied feat and class did not affect the settled save")
    if settled["total"] != settled["natural"] + 3:
        raise RuntimeError("the settled save total did not use the materialized character state")
    if settlement.get("campaign_revision") != current["revision"] + 1:
        raise RuntimeError("the settled save did not advance the campaign revision exactly once")
    resolution_id = str(settlement.get("resolution_id") or "")
    if not resolution_id:
        raise RuntimeError("the settled save has no resolution id")
    after_settlement = await _call(
        server,
        "campaign_query",
        {"view": "get", "payload": {"campaign_id": campaign["id"]}},
    )
    resolution_log = list(after_settlement.get("state", {}).get("resolution_log") or [])
    resolution = resolution_log[-1] if resolution_log else {}
    if (
        after_settlement.get("revision") != settlement["campaign_revision"]
        or resolution.get("id") != resolution_id
        or resolution.get("actor_id") != character["id"]
        or resolution.get("type") != "save"
        or dict(resolution.get("result") or {}).get("total") != settled["total"]
    ):
        raise RuntimeError("the committed save was not recorded in the campaign resolution log")

    report = {
        "schema": "sagasmith.dnd-official-expansion-regression.v2",
        "edition": "2014",
        "packages": len(official),
        "activated": len(official),
        "catalog_entries": sum(catalog_counts.values()),
        "catalog_entries_by_kind": dict(sorted(catalog_counts.items())),
        "selection_ready": sum(selection_counts.values()),
        "selection_ready_by_kind": dict(sorted(selection_counts.items())),
        "materializers_exercised": len(materializers),
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
            "kind": "constitution_save",
            "status": settlement["status"],
            "committed": settlement["status"] == "committed",
            "campaign_revision": settlement["campaign_revision"],
            "mechanic_rule_receipts": len(settled.get("rule_receipts") or []),
        },
        "receipts": {
            "content_application": len(content_receipts),
            "mechanic_settlement": len(settled.get("rule_receipts") or []),
            "restart_persisted": 0,
        },
        "persistence": {"restart_verified": False},
        "build": {"failures": _build_failures(character["sheet"], follow_ups)},
        "content_exported": False,
    }
    checkpoint = {
        "campaign_id": campaign["id"],
        "campaign_revision": after_settlement["revision"],
        "character_id": character["id"],
        "character_revision": character["revision"],
        "character_sheet": character["sheet"],
        "resolution_id": resolution_id,
        "resolution_total": settled["total"],
        "content_receipts": content_receipts,
        "follow_ups": follow_ups,
        "official_addons": {
            (str(item["addon_id"]), str(item["version"])) for item in activation_order
        },
    }
    return report, checkpoint


async def _verify_restart(server: Any, checkpoint: dict[str, Any]) -> int:
    campaign_id = str(checkpoint["campaign_id"])
    campaign = await _call(
        server,
        "campaign_query",
        {"view": "get", "payload": {"campaign_id": campaign_id}},
    )
    resolution_log = list(campaign.get("state", {}).get("resolution_log") or [])
    resolution = resolution_log[-1] if resolution_log else {}
    if (
        campaign.get("revision") != checkpoint["campaign_revision"]
        or resolution.get("id") != checkpoint["resolution_id"]
        or dict(resolution.get("result") or {}).get("total") != checkpoint["resolution_total"]
    ):
        raise RuntimeError("campaign settlement state did not survive an MCP restart")

    character = await _call(
        server,
        "character_query",
        {"view": "get", "payload": {"character_id": checkpoint["character_id"]}},
    )
    if (
        character.get("revision") != checkpoint["character_revision"]
        or character.get("sheet") != checkpoint["character_sheet"]
    ):
        raise RuntimeError("the official-expansion character did not survive an MCP restart")

    listed = await _call(
        server,
        "content_pack",
        {"action": "list", "payload": {"campaign_id": campaign_id, "kind": "addon"}},
    )
    active_official = {
        (str(item["addon_id"]), str(item["version"]))
        for item in listed
        if (item.get("built_in_official_expansion") or item.get("built_in_official_core_support"))
        and dict(item.get("activation") or {}).get("enabled") is True
    }
    if active_official != checkpoint["official_addons"]:
        raise RuntimeError("official addon activation state did not survive an MCP restart")

    stored = await _call(
        server,
        "campaign_rules",
        {"campaign_id": campaign_id, "action": "receipts", "payload": {"limit": 1000}},
    )
    expected_receipts = dict(checkpoint["content_receipts"])
    persisted: dict[str, dict[str, Any]] = {}
    for item in stored:
        receipt = dict(item.get("receipt") or {})
        artifact_id = str(receipt.get("artifact_id") or "")
        if (
            receipt.get("event") != "character.content.apply"
            or artifact_id not in expected_receipts
        ):
            continue
        if (
            item.get("operation") != "character.content.apply"
            or item.get("event") != "character.content.apply"
            or item.get("mechanic_id") != receipt.get("mechanic_id")
            or item.get("ruleset_fingerprint") != receipt.get("ruleset_fingerprint")
            or not item.get("mutation_group_id")
            or item.get("applied") is not True
            or receipt != expected_receipts[artifact_id]
        ):
            raise RuntimeError(f"persisted content receipt is incomplete: {artifact_id}")
        if artifact_id in persisted:
            raise RuntimeError(f"persisted content receipt is duplicated: {artifact_id}")
        persisted[artifact_id] = receipt
    if set(persisted) != set(expected_receipts):
        raise RuntimeError("content application receipts did not survive an MCP restart")
    return len(persisted)


def _execute(content_library: Path, home: Path) -> dict[str, Any]:
    server = _create_regression_server(content_library, home)
    try:
        report, checkpoint = asyncio.run(_run(server))
    finally:
        close_server(server)
    restarted = _create_regression_server(content_library, home)
    try:
        persisted_receipts = asyncio.run(_verify_restart(restarted, checkpoint))
    finally:
        close_server(restarted)
    report["receipts"]["restart_persisted"] = persisted_receipts
    report["persistence"]["restart_verified"] = True
    # Persisting an incomplete build is not a successful official-rules regression.
    report["passed"] = not report["build"]["failures"]
    return report


def main() -> int:
    args = _arguments()
    if args.home is not None:
        report = _execute(
            args.content_library,
            args.home.expanduser().resolve(),
        )
    else:
        with tempfile.TemporaryDirectory(prefix="sagasmith-official-expansions-") as value:
            report = _execute(args.content_library, Path(value))
    output = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.expanduser().resolve().write_text(output, encoding="utf-8")
    print(output)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
