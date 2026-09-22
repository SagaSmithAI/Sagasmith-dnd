"""Rebuild exact Tasha Battle Smith contracts from a private hash-locked archive.

The source assets remain byte-identical. This offline transform neither publishes
commercial text nor replaces campaign locks. It produces a new immutable archive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from copy import deepcopy
from pathlib import Path

from sagasmith_core.content_pack import (
    build_content_package,
    dumps_content_archive,
    loads_content_archive,
)
from sagasmith_dnd.content_packages import content_definition_checksum, validate_dnd_content_package
from sagasmith_dnd.content_validation import build_catalog_review, build_selection_contract

PREFIX = "dnd5e.addon.rulebook.d-d-5e-tasha-s-cauldron-of-everything.89a729b37a4b"
SOURCE_KEY = "user.rulebook.d-d-5e-tasha-s-cauldron-of-everything.89a729b37a"
SOURCE_SHA = "c220eb1abe4125a48faf2b7b18078d3295c68c1053e4d6333577581226baf50b"
ASSET_SHA = "00d38ea48582b9f6c8fae91c04e7cd68c0c9b74ed4dbd5a488d63f78d629a358"
VERSION = "1.0.2-local.battle-smith.1"
ARTISAN_TOOLS = [
    "Alchemist's Supplies",
    "Brewer's Supplies",
    "Calligrapher's Supplies",
    "Carpenter's Tools",
    "Cartographer's Tools",
    "Cobbler's Tools",
    "Cook's Utensils",
    "Glassblower's Tools",
    "Jeweler's Tools",
    "Leatherworker's Tools",
    "Mason's Tools",
    "Painter's Supplies",
    "Potter's Tools",
    "Tinker's Tools",
    "Weaver's Tools",
    "Woodcarver's Tools",
]
SPELLS = {
    3: ("Heroism", "Shield"),
    5: ("Branding Smite", "Warding Bond"),
    9: ("Aura of Vitality", "Conjure Barrage"),
    13: ("Aura of Purity", "Fire Shield"),
    17: ("Banishing Smite", "Mass Cure Wounds"),
}


def _one(values, label):
    values = list(values)
    if len(values) != 1:
        raise ValueError(f"expected exactly one {label}")
    return values[0]


def repair_archive(data: bytes) -> tuple[bytes, dict]:
    if hashlib.sha256(data).hexdigest() != SOURCE_SHA:
        raise ValueError("archive is not the exact reviewed Tasha Battle Smith input")
    package, blobs = loads_content_archive(data)
    validate_dnd_content_package(package)
    if package["id"] != PREFIX + ".addon" or package["metadata"].get("distribution") != "private":
        raise ValueError("Battle Smith input identity/distribution mismatch")
    source = _one((s for s in package["sources"] if s["source_key"] == SOURCE_KEY), "source")
    asset = _one(
        (a for a in package["assets"] if a["asset_key"] == source["normalized_document_asset_key"]),
        "source asset",
    )
    if asset["checksum"] != ASSET_SHA or hashlib.sha256(blobs[ASSET_SHA]).hexdigest() != ASSET_SHA:
        raise ValueError("normalized source asset checksum mismatch")
    document = blobs[ASSET_SHA].decode("utf-8")
    sections = {s["ordinal"]: s for s in source["sections"]}
    evidence = {}

    def section(ordinal):
        s = sections[ordinal]
        body = document[s["start_offset"] : s["end_offset"]]
        if hashlib.sha256(body.encode()).hexdigest() != s["content_hash"]:
            raise ValueError("reviewed section checksum mismatch")
        if not s["chunks"]:
            raise ValueError("reviewed source section has no chunks")
        evidence[str(ordinal)] = s["content_hash"]
        return body

    corrected = deepcopy(package)
    artifacts = corrected["content"]["artifacts"]
    changed = []

    def artifact(slug):
        return _one((a for a in artifacts if a["id"] == PREFIX + "." + slug), slug)

    def bind(a, ordinals):
        bodies = [section(n) for n in ordinals]
        refs, citations = [], []
        for ordinal in ordinals:
            for c in sections[ordinal]["chunks"]:
                text = document[c["start_offset"] : c["end_offset"]]
                if hashlib.sha256(text.encode()).hexdigest() != c["content_hash"]:
                    raise ValueError("reviewed source chunk checksum mismatch")
                refs.append(
                    {
                        "source_key": source["source_key"],
                        "chunk_key": c["key"],
                        "page": c["page_start"],
                        "note": "Exact Battle Smith source review",
                    }
                )
                if text.strip():
                    citations.append(
                        {
                            "source": "rule-source:" + source["source_key"],
                            "source_ref": {"chunk_key": c["key"]},
                            "source_excerpt": text,
                        }
                    )
        a["source_refs"] = refs
        a["rule_refs"] = [f"rule-source:{r['source_key']}#chunk:{r['chunk_key']}" for r in refs]
        clause_id = "source-resolution-" + hashlib.sha256(a["id"].encode()).hexdigest()[:16]
        reason = (
            "Apply the exact reviewed source through its declared grants and Runtime mechanics."
        )
        a["rule_clauses"] = [
            {
                "id": clause_id,
                "schema_version": 1,
                "scope": "mechanical",
                "title": a["card"]["name"],
                "source_citations": citations,
                "settlement": {
                    "mode": "agent_ruling",
                    "default_resolver": "agent",
                    "ruling_kind": "agent_dm_adjudication",
                    "reason": reason,
                },
            }
        ]
        a["semantic_resolution"] = {
            "status": "resolved",
            "mode": "agent_ruling",
            "first_use_compilation_required": False,
            "clause_ids": [clause_id],
        }
        if a["kind"] != "statblock":
            a["card"]["description"] = "\n\n".join(bodies)
            a["card"]["ruling_requirements"] = [
                {
                    "kind": "source_bound_import_resolution",
                    "policy_ref": "rule_clause.v1",
                    "default_resolver": "agent",
                    "ruling_kind": "agent_dm_adjudication",
                    "reason": reason,
                    "requires_external_input_only_for": [],
                    "source_excerpt": "\n\n".join(bodies),
                }
            ]
        changed.append(a["id"])

    subclass = artifact("subclass.battle-smith")
    subclass["card"]["spell_grants"] = [
        {"name": name, "minimum_level": level, "method": "always_prepared"}
        for level, names in SPELLS.items()
        for name in names
    ]
    bind(subclass, [123, 125, 126])

    # The importer merged identically named Tool Proficiency entries. Restore
    # separate source identities, without granting the Battle Smith tools to an Artillerist.
    old_tool = artifact("feature.tool-proficiency-6864fb17eb96")
    tools = deepcopy(old_tool)
    tools["id"] = PREFIX + ".feature.tool-proficiency-battle-smith"
    tools["card"] = {
        "name": "Tool Proficiency (Battle Smith)",
        "class_name": "Artificer",
        "subclass_name": "Battle Smith",
        "minimum_level": 3,
        "mechanical_grants": {
            "tool_proficiencies": ["Smith's Tools"],
            "tool_proficiency_replacement_options": {"Smith's Tools": ARTISAN_TOOLS},
        },
    }
    bind(tools, [124])
    artifacts.append(tools)
    bind(old_tool, [111])
    for slug, ordinal, level in [
        ("battle-smith-spells", 125, 3),
        ("battle-ready", 127, 3),
        ("steel-defender", 128, 3),
        ("extra-attack-21cf6f37cbf9", 138, 5),
        ("arcane-jolt", 139, 9),
        ("improved-defender", 140, 15),
    ]:
        a = artifact("feature." + slug)
        a["card"]["minimum_level"] = level
        bind(a, [ordinal, 126] if slug == "battle-smith-spells" else [ordinal])
    artifact("feature.battle-ready")["card"]["mechanical_grants"] = {
        "weapon_proficiencies": ["martial weapons"],
    }
    artifact("feature.extra-attack-21cf6f37cbf9")["card"]["attack_scaling"] = {
        "class_name": "Artificer",
        "attacks_per_action_by_level": {"5": 2},
    }

    defender = artifact("statblock.steel-defender")
    bind(defender, [129, 135, 136, 137, 128])
    text = defender["card"]["normalized_content"]
    text = text.replace("**Armor Class** 1 5", "**Armor Class** 15")
    text = text.replace("***Challenge Vigilant***", "***Vigilant***")
    text = text.replace("su rprised", "surprised").replace("o n the attack", "on the attack")
    text = text.replace("attack rol l", "attack roll")
    text = text.replace(" ***Repair (3/Day)***", "\n\n***Repair (3/Day)***")
    match = re.search(r" \*\*\*Deflect Attack\*\*\*\.(.*?)(?=\n\n## Actions)", text, re.S)
    if match is None:
        raise ValueError("reviewed Deflect Attack boundary is missing")
    reaction = "***Deflect Attack***." + match[1]
    text = text[: match.start()] + text[match.end() :]
    text += "\n\n## Reactions\n\n" + reaction
    defender["card"]["normalized_content"] = text
    template = defender["card"]["dependent_actor_template"]
    template["owner_binding"] = {
        "schema_version": 1,
        "kind": "feature_entitlement",
        "feature_artifact_id": PREFIX + ".feature.steel-defender",
        "relation_key": "steel_defender",
    }
    template["lifecycle_policy"] = {"schema_version": 1, "owner_death": "perish"}

    for a in artifacts:
        if a["id"] not in changed:
            continue
        old = a["selection_contract"]
        a["selection_contract"] = build_selection_contract(
            a,
            status=old["status"],
            references=a["rule_refs"],
            blockers=old.get("blockers", []),
        )
        a["catalog_review"] = build_catalog_review(
            a,
            decisions=[
                {
                    "role": "primary",
                    "reviewer": "exact Tasha Battle Smith source review",
                    "method": "agent",
                    "checks": {
                        "identity": True,
                        "classification": True,
                        "entry_boundary": True,
                        "references": True,
                    },
                    "notes": (
                        "Checked source hashes, spell table, feature levels and statblock "
                        "boundaries; local QA only."
                    ),
                }
            ],
        )
    definition = _one(
        (d for d in corrected["content"]["rule_definitions"] if d["id"] == PREFIX),
        "rule definition",
    )
    definition["version"] = definition["manifest"]["version"] = VERSION
    definition["definition_checksum"] = content_definition_checksum(
        manifest=definition["manifest"],
        artifacts=[a for a in artifacts if a["rule_definition_id"] == PREFIX],
        mechanics=[
            m for m in corrected["content"]["mechanics"] if m.get("rule_definition_id") == PREFIX
        ],
    )
    corrected["metadata"]["local_tasha_battle_smith_repair"] = {
        "source_archive_sha256": SOURCE_SHA,
        "normalized_asset_sha256": ASSET_SHA,
        "sections": evidence,
        "changed_artifacts": changed,
        "published": False,
    }
    corrected["manifest"]["content_summary"] = dict(
        sorted(Counter(a["kind"] for a in artifacts).items())
    )
    rebuilt = build_content_package(
        kind=corrected["kind"],
        package_id=corrected["id"],
        version=VERSION,
        system_id=corrected["system_id"],
        manifest=corrected["manifest"],
        dependencies=corrected["dependencies"],
        sources=corrected["sources"],
        assets=corrected["assets"],
        content_reviews=corrected["content_reviews"],
        actors=corrected["actors"],
        content=corrected["content"],
        metadata=corrected["metadata"],
    )
    validate_dnd_content_package(rebuilt)
    result = dumps_content_archive(rebuilt, blobs)
    checked, checked_blobs = loads_content_archive(result)
    if checked != rebuilt or checked_blobs != blobs:
        raise ValueError("Battle Smith repair did not preserve archive roundtrip/source assets")
    return result, {
        "id": rebuilt["id"],
        "version": VERSION,
        "checksum": rebuilt["checksum"],
        "archive_sha256": hashlib.sha256(result).hexdigest(),
        "source_archive_sha256": SOURCE_SHA,
        "changed_artifacts": changed,
        "published": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("output must be a new archive path")
    result, report = repair_archive(args.archive.read_bytes())
    with args.output.open("xb") as stream:
        stream.write(result)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
