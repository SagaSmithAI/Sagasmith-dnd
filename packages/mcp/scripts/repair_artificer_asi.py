"""Repair two exact, locally supplied 2014 Artificer ASI archives.

No book text is bundled here. This offline maintenance script does not activate
packs, edit a campaign lock, or upload content. Eberron input is the separately
reviewed local subclass-spell repair, not the legacy published archive. Output
is a new private QA version; promotion needs a separately reviewed library lock.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path

from sagasmith_core.content_pack import (
    build_content_package,
    dumps_content_archive,
    loads_content_archive,
)
from sagasmith_dnd.content_import import validate_selection_ready_artifacts
from sagasmith_dnd.content_packages import (
    content_definition_checksum,
    validate_dnd_content_package,
)
from sagasmith_dnd.content_validation import build_catalog_review, build_selection_contract

_RECIPES = {
    "2289aa6affedfbd4fd9cc8da13845cd468b86df0172c5c235c5b69763200c3e0": {
        "prefix": "dnd5e.addon.rulebook.d-d-5e-eberron-rising-from-the-last-war.31293633134f",
        "source_key": "user.rulebook.d-d-5e-eberron-rising-from-the-last-war.3129363313",
        "version": "1.0.3-local.artificer-asi.2",
        "slug": "ability-score-improvement",
        "existing": False,
        "sections": {
            326: "2cc9ee294934e7cd456250d10f9b2ac38e21abbdeaa41980de08ec2d7c1b3bd2",
            352: "8b0165c06fc53513963d3237c2c4ab1948d9a4e366a0f53ff8ee8672ba8b516c",
        },
        "prose_section": 352,
    },
    "127dbe5841a24feaa27a3efbbe0dc239adba958a79ced3c63292d1f32dd6dac7": {
        "prefix": "dnd5e.addon.rulebook.d-d-5e-tasha-s-cauldron-of-everything.89a729b37a4b",
        "source_key": "user.rulebook.d-d-5e-tasha-s-cauldron-of-everything.89a729b37a",
        "version": "1.0.1-local.artificer-asi.2",
        "slug": "ability-score-improvement-1164ca577fe2",
        "existing": True,
        "sections": {
            76: "3a4ec908824f823913f30ac6902b5932fb814e3fc65e0332436d5aa0ce45b502",
        },
        "prose_section": 76,
    },
}


def _one(values, label):
    values = list(values)
    if len(values) != 1:
        raise ValueError(f"expected exactly one {label}")
    return values[0]


def _source_evidence(package, blobs, recipe):
    source = _one(
        (s for s in package["sources"] if s["source_key"] == recipe["source_key"]),
        "source",
    )
    asset = _one(
        (a for a in package["assets"] if a["asset_key"] == source["normalized_document_asset_key"]),
        "normalized source asset",
    )
    raw = blobs[asset["checksum"]]
    if hashlib.sha256(raw).hexdigest() != asset["checksum"]:
        raise ValueError("normalized source asset checksum mismatch")
    document = raw.decode("utf-8")
    refs, sections = [], {}
    # The final reference is the prose clause used by the runtime citation.
    for ordinal in sorted(recipe["sections"], key=lambda value: value == recipe["prose_section"]):
        expected_hash = recipe["sections"][ordinal]
        section = _one((s for s in source["sections"] if s["ordinal"] == ordinal), "section")
        text = document[section["start_offset"] : section["end_offset"]]
        if (
            section["content_hash"] != expected_hash
            or hashlib.sha256(text.encode("utf-8")).hexdigest() != expected_hash
        ):
            raise ValueError("reviewed source section checksum mismatch")
        if not section["chunks"]:
            raise ValueError("reviewed source section has no chunks")
        if ordinal == recipe["prose_section"] and len(section["chunks"]) != 1:
            raise ValueError("reviewed ASI prose must have exactly one source chunk")
        sections[ordinal] = text
        for chunk in section["chunks"]:
            refs.append(
                {
                    "source_key": source["source_key"],
                    "chunk_key": chunk["key"],
                    "page": chunk["page_start"],
                    "note": "Source-reviewed Artificer ASI clause/table",
                }
            )
    return sections[recipe["prose_section"]], refs


def _asi_artifact(recipe, description, source_refs):
    """Materialize the already reviewed rules using the existing feature contract."""
    identifier = f"{recipe['prefix']}.feature.{recipe['slug']}"
    rule_refs = [f"rule-source:{ref['source_key']}#chunk:{ref['chunk_key']}" for ref in source_refs]
    # Keep source adjudication explicit; this does not invent a kernel mechanic.
    clause_id = "source-resolution-" + hashlib.sha256(identifier.encode()).hexdigest()[:16]
    artifact = {
        "id": identifier,
        "kind": "feature",
        "rule_definition_id": recipe["prefix"],
        "application_state": "selection_ready",
        "mechanical_scope": "mechanical",
        "execution_state": "ruling_ready",
        "semantic_resolution": {
            "status": "resolved",
            "mode": "agent_ruling",
            "first_use_compilation_required": False,
            "clause_ids": [clause_id],
        },
        "card": {
            "name": "Ability Score Improvement",
            "class_name": "Artificer",
            "minimum_level": 4,
            "repeatable_selection_levels": [4, 8, 12, 16, 19],
            "description": description,
            "selection_requirements": {
                "field": "ability_score_increases",
                "kind": "ability_score_increase",
                "allowed_distributions": [[2], [1, 1]],
                "maximum_score": 20,
            },
        },
        "source_refs": deepcopy(source_refs),
        "rule_refs": rule_refs,
        "rule_clauses": [
            {
                "id": clause_id,
                "schema_version": 1,
                "scope": "mechanical",
                "title": "Ability Score Improvement",
                "source_citations": [
                    {
                        "source": f"rule-source:{source_refs[-1]['source_key']}",
                        "source_ref": {"chunk_key": source_refs[-1]["chunk_key"]},
                        "source_excerpt": description,
                    }
                ],
                "settlement": {
                    "mode": "agent_ruling",
                    "default_resolver": "agent",
                    "ruling_kind": "agent_dm_adjudication",
                    "reason": "Use the source-bound feature selection contract for ASI settlement.",
                },
            }
        ],
    }
    artifact["selection_contract"] = build_selection_contract(
        artifact,
        status="ready",
        references=rule_refs,
    )
    artifact["catalog_review"] = build_catalog_review(
        artifact,
        decisions=[
            {
                "role": "primary",
                "reviewer": "source-reviewed Artificer ASI repair",
                "method": "agent",
                "checks": {
                    "identity": True,
                    "classification": True,
                    "entry_boundary": True,
                    "references": True,
                },
                "notes": "Exact source sections reviewed; local QA only, not publication approval.",
            }
        ],
    )
    return artifact


def repair_archive(data: bytes) -> tuple[bytes, dict]:
    source_sha = hashlib.sha256(data).hexdigest()
    recipe = _RECIPES.get(source_sha)
    if recipe is None:
        raise ValueError("archive is not an exact reviewed ASI repair input")
    package, blobs = loads_content_archive(data)
    validate_dnd_content_package(package)
    if package["id"] != recipe["prefix"] + ".addon":
        raise ValueError("reviewed archive package identity mismatch")
    if package["metadata"].get("distribution") != "private":
        raise ValueError("ASI repair requires a private input package")
    description, refs = _source_evidence(package, blobs, recipe)
    corrected = deepcopy(package)
    artifacts = corrected["content"]["artifacts"]
    matches = [
        a
        for a in artifacts
        if a["kind"] == "feature"
        and a["card"].get("class_name", "").casefold() == "artificer"
        and not a["card"].get("subclass_name")
        and a["card"]["name"].casefold() == "ability score improvement"
    ]
    expected_id = f"{recipe['prefix']}.feature.{recipe['slug']}"
    if [a["id"] for a in matches] != ([expected_id] if recipe["existing"] else []):
        raise ValueError("unexpected existing Artificer ASI artifacts")
    artifact = _asi_artifact(recipe, description, refs)
    runtime_errors = validate_selection_ready_artifacts(
        [
            {
                key: value
                for key, value in artifact.items()
                if key not in {"catalog_review", "selection_contract", "rule_definition_id"}
            }
        ]
    )
    if runtime_errors:
        raise ValueError("repaired ASI runtime contract is invalid: " + "; ".join(runtime_errors))
    if matches:
        artifacts[artifacts.index(matches[0])] = artifact
    else:
        if any(a["id"] == expected_id for a in artifacts):
            raise ValueError("ASI artifact identity collision")
        artifacts.append(artifact)
    corrected["manifest"]["content_summary"] = dict(Counter(a["kind"] for a in artifacts))
    _one(
        (d for d in corrected["content"]["rule_definitions"] if d["id"] == recipe["prefix"]),
        "target rule definition",
    )
    for definition in corrected["content"]["rule_definitions"]:
        if definition["id"] != recipe["prefix"]:
            continue
        members = [a for a in artifacts if a["rule_definition_id"] == definition["id"]]
        definition["version"] = "1.0.1-local.artificer-asi.2"
        definition["manifest"]["version"] = definition["version"]
        readiness = definition["manifest"]["resolution_readiness"]
        readiness["artifact_count"] = len(members)
        readiness["resolved_count"] = sum(
            a["semantic_resolution"]["status"] == "resolved" for a in members
        )
        readiness["modes"] = dict(Counter(a["semantic_resolution"]["mode"] for a in members))
        definition["definition_checksum"] = content_definition_checksum(
            manifest=definition["manifest"],
            artifacts=members,
            mechanics=[
                m
                for m in corrected["content"]["mechanics"]
                if m.get("rule_definition_id") == definition["id"]
            ],
        )
    corrected["metadata"]["local_artificer_asi_repair"] = {
        "source_archive_sha256": source_sha,
        "source_version": package["version"],
        "source_checksum": package["checksum"],
        "provisional": True,
        "sections": {str(k): v for k, v in recipe["sections"].items()},
    }
    rebuilt = build_content_package(
        kind=corrected["kind"],
        package_id=corrected["id"],
        version=recipe["version"],
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
        raise ValueError("repaired archive did not round-trip without source changes")
    return result, {
        "id": rebuilt["id"],
        "version": rebuilt["version"],
        "checksum": rebuilt["checksum"],
        "archive_sha256": hashlib.sha256(result).hexdigest(),
        "source_archive_sha256": source_sha,
        "published": False,
        "source_assets_changed": False,
        "artifact_id": expected_id,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    # Exclusive creation refuses existing files, symlinks and the source itself.
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("output must be a new archive path")
    result, report = repair_archive(args.archive.read_bytes())
    with args.output.open("xb") as stream:
        stream.write(result)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
