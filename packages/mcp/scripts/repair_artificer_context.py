"""Repair exact source-reviewed context boundaries in private 2014 archives.

This is an offline QA transformation, not a publication or campaign migration.
Book text comes only from the supplied hash-locked archives. Species/subclass
mechanics are preserved; retaining them is not proof of complete implementation.
"""

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
from sagasmith_dnd.content_packages import content_definition_checksum, validate_dnd_content_package
from sagasmith_dnd.content_validation import build_catalog_review, build_selection_contract

_RECIPES = {
    "925256497eaf1a4f8ec26b3be1cee0d14de081b8804eb96410ea08d2b7ea3069": {
        "prefix": "dnd5e.addon.rulebook.d-d-5e-eberron-rising-from-the-last-war.31293633134f",
        "source_key": "user.rulebook.d-d-5e-eberron-rising-from-the-last-war.3129363313",
        "version": "1.0.3-local.artificer-context.1",
        "sections": {361: "415f03ca39ceb6e098e280c2bedbdd5ca36dedf540977e84e457ea69da15b350"},
        "directories": {"artificer-specialists": 361},
    },
    "36417e8ab72d838b64459bc50b4ffb4e92a12ac3b90242187a2a90989d549a99": {
        "prefix": "dnd5e.addon.rulebook.d-d-5e-tasha-s-cauldron-of-everything.89a729b37a4b",
        "source_key": "user.rulebook.d-d-5e-tasha-s-cauldron-of-everything.89a729b37a",
        "version": "1.0.1-local.artificer-context.1",
        "sections": {
            56: "a4c0913ae689dc00e89d501c88aebe61a730aa288a1ba9c376363b7d3f5124ed",
            57: "50956567b6916fe93f1ba6597cfa851324c2bc2dc6c339e36e6681f6f4bbf6fb",
            74: "16446bbf628553a838ba3c4c4f51be9d081699c1550518e6b7a036c40ef2c8ed",
            87: "415f03ca39ceb6e098e280c2bedbdd5ca36dedf540977e84e457ea69da15b350",
        },
        "directories": {"artificer-specialists-805d88a09ca0": 87},
        "sidebar": "the-magic-of-artifice",
        "sidebar_end": 659,
        "spellcasting": "spellcasting-26b317b14251",
        "specialist": "artificer-specialists-c13c52caa87c",
    },
    "41a60a99be6cb93259b0cfdbed584137f5484d6ab276f5fd26ecbb6de85a5d4f": {
        "prefix": "dnd5e.addon.rulebook.d-d-5e-wayfinders-guide-to-eberron.38e71ffb60c7",
        "source_key": "user.rulebook.d-d-5e-wayfinders-guide-to-eberron.38e71ffb60",
        "version": "1.0.1-local.artificer-context.1",
        "sections": {
            377: "f6691ba67dd8ca64227c66f1a51a8b0466b4632819258bc9e71605c457b1d59a",
            380: "27898c1d8cdd8d56de7c0e14ac228c6d7f1ec32677e5b92066e1f93ef9abcba2",
        },
        "directories": {},
        "mixed_context": "chapter-4-the-mark-of-storm",
    },
}


def _one(values, label):
    values = list(values)
    if len(values) != 1:
        raise ValueError(f"expected exactly one {label}")
    return values[0]


def _sections(package, blobs, recipe):
    source = _one(
        (s for s in package["sources"] if s["source_key"] == recipe["source_key"]), "source"
    )
    asset = _one(
        (a for a in package["assets"] if a["asset_key"] == source["normalized_document_asset_key"]),
        "source asset",
    )
    raw = blobs[asset["checksum"]]
    if hashlib.sha256(raw).hexdigest() != asset["checksum"]:
        raise ValueError("source asset checksum mismatch")
    document = raw.decode("utf-8")
    result = {}
    for ordinal, expected_hash in recipe["sections"].items():
        section = _one((s for s in source["sections"] if s["ordinal"] == ordinal), "source section")
        text = document[section["start_offset"] : section["end_offset"]]
        if (
            section["content_hash"] != expected_hash
            or hashlib.sha256(text.encode()).hexdigest() != expected_hash
        ):
            raise ValueError("reviewed context section checksum mismatch")
        if not section["chunks"]:
            raise ValueError("reviewed context section has no chunks")
        result[ordinal] = (
            text,
            [
                {
                    "source_key": source["source_key"],
                    "chunk_key": c["key"],
                    "page": c["page_start"],
                    "note": "Reviewed context boundary",
                }
                for c in section["chunks"]
            ],
        )
    return result


def _refs(source_refs):
    return [f"rule-source:{r['source_key']}#chunk:{r['chunk_key']}" for r in source_refs]


def _review(artifact):
    artifact["selection_contract"] = build_selection_contract(
        artifact,
        status=(
            "not_applicable"
            if artifact.get("selection_applicability") == "not_applicable"
            else "ready"
        ),
        references=artifact["rule_refs"],
    )
    artifact["catalog_review"] = build_catalog_review(
        artifact,
        decisions=[
            {
                "role": "primary",
                "reviewer": "source-reviewed context repair",
                "method": "agent",
                "checks": {
                    "identity": True,
                    "classification": True,
                    "entry_boundary": True,
                    "references": True,
                },
                "notes": (
                    "Exact directory/sidebar boundary reviewed. "
                    "Local QA, not redistribution approval."
                ),
            }
        ],
    )


def _as_context(artifact, *, description=None, source_refs=None):
    artifact["application_state"] = "catalog_only"
    artifact["selection_applicability"] = "not_applicable"
    if description is not None:
        # Pure directory/sidebar: do not retain swallowed subclass rule clauses.
        artifact["card"] = {
            "name": artifact["card"]["name"],
            "description": description,
            "source_fragment": True,
        }
        artifact["source_refs"] = deepcopy(source_refs)
        artifact["rule_refs"] = _refs(source_refs)
        artifact["mechanical_scope"] = "descriptive"
        artifact["execution_state"] = "descriptive_ready"
        artifact["semantic_resolution"] = {
            "status": "resolved",
            "mode": "descriptive",
            "first_use_compilation_required": False,
        }
        artifact.pop("rule_clauses", None)
    else:
        # Mixed racial context remains searchable, but is not a class grant.
        for field in ("class_name", "subclass_name", "minimum_level"):
            artifact["card"].pop(field, None)
        artifact["card"]["source_fragment"] = True
    _review(artifact)


def _repair_artifacts(artifacts, recipe, sections):
    result = deepcopy(artifacts)
    changed = []

    def feature(slug):
        identifier = f"{recipe['prefix']}.feature.{slug}"
        artifact = _one((a for a in result if a["id"] == identifier), "context feature")
        if artifact["kind"] != "feature":
            raise ValueError("context repair target must be a feature")
        changed.append(identifier)
        return artifact

    for slug, ordinal in recipe["directories"].items():
        description, refs = sections[ordinal]
        _as_context(feature(slug), description=description, source_refs=refs)
    if recipe.get("mixed_context"):
        _as_context(feature(recipe["mixed_context"]))
    if recipe.get("sidebar"):
        body, sidebar_refs = sections[57]
        split = recipe["sidebar_end"]
        if not 0 < split < len(body) or len(sidebar_refs) != 1:
            raise ValueError("invalid reviewed sidebar boundary")
        _as_context(feature(recipe["sidebar"]), description=body[:split], source_refs=sidebar_refs)
        # Preserve the interrupted Tools Required sentence and the focus rule.
        tools_text, tools_refs = sections[56]
        if len(tools_refs) != 1:
            raise ValueError("reviewed tools clause must have one source chunk")
        focus = tools_text + " " + body[split:].strip()
        spellcasting = feature(recipe["spellcasting"])
        spellcasting["card"]["description"] += "\n\n" + focus
        spellcasting["source_refs"].extend(deepcopy(tools_refs + sidebar_refs))
        spellcasting["rule_refs"] = list(
            dict.fromkeys(spellcasting["rule_refs"] + _refs(tools_refs + sidebar_refs))
        )
        clause_id = (
            "source-resolution-"
            + hashlib.sha256((spellcasting["id"] + ":focus").encode()).hexdigest()[:16]
        )
        spellcasting["rule_clauses"].append(
            {
                "id": clause_id,
                "schema_version": 1,
                "scope": "mechanical",
                "title": "Tools Required",
                "source_citations": [
                    {
                        "source": f"rule-source:{refs[0]['source_key']}",
                        "source_ref": {"chunk_key": refs[0]["chunk_key"]},
                        "source_excerpt": text,
                    }
                    for text, refs in [
                        (tools_text, tools_refs),
                        (body[split:].strip(), sidebar_refs),
                    ]
                ],
                "settlement": {
                    "mode": "agent_ruling",
                    "default_resolver": "agent",
                    "ruling_kind": "agent_dm_adjudication",
                    "reason": "Apply the source-specific Artificer tools and infusion focus rules.",
                },
            }
        )
        spellcasting["semantic_resolution"]["clause_ids"].append(clause_id)
        _review(spellcasting)
        # This singular class feature is real; it must not be demoted with the directory.
        specialist = feature(recipe["specialist"])
        specialist["card"]["name"] = "Artificer Specialist"
        for clause in specialist.get("rule_clauses", []):
            clause["title"] = "Artificer Specialist"
        _review(specialist)
    return result, changed


def repair_archive(data):
    source_sha = hashlib.sha256(data).hexdigest()
    recipe = _RECIPES.get(source_sha)
    if recipe is None:
        raise ValueError("archive is not an exact reviewed context repair input")
    package, blobs = loads_content_archive(data)
    validate_dnd_content_package(package)
    if (
        package["id"] != recipe["prefix"] + ".addon"
        or package["metadata"].get("distribution") != "private"
    ):
        raise ValueError("context repair input identity/distribution mismatch")
    sections = _sections(package, blobs, recipe)
    corrected = deepcopy(package)
    artifacts, changed = _repair_artifacts(package["content"]["artifacts"], recipe, sections)
    corrected["content"]["artifacts"] = artifacts
    runtime = [
        {
            k: v
            for k, v in a.items()
            if k not in ("catalog_review", "selection_contract", "rule_definition_id")
        }
        for a in artifacts
        if a["id"] in changed
    ]
    errors = validate_selection_ready_artifacts(runtime)
    if errors:
        raise ValueError("repaired context runtime contract is invalid: " + "; ".join(errors))
    definition = _one(
        (d for d in corrected["content"]["rule_definitions"] if d["id"] == recipe["prefix"]),
        "rule definition",
    )
    definition["version"] = "1.0.1-local.artificer-context.1"
    manifest = definition["manifest"]
    manifest["version"] = definition["version"]
    members = [a for a in artifacts if a["rule_definition_id"] == definition["id"]]
    manifest["resolution_readiness"]["modes"] = dict(
        Counter(a["semantic_resolution"]["mode"] for a in members)
    )
    definition["definition_checksum"] = content_definition_checksum(
        manifest=manifest,
        artifacts=members,
        mechanics=[
            m
            for m in corrected["content"]["mechanics"]
            if m.get("rule_definition_id") == definition["id"]
        ],
    )
    corrected["metadata"]["local_artificer_context_repair"] = {
        "source_archive_sha256": source_sha,
        "provisional": True,
        "changed_artifacts": changed,
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
    output = dumps_content_archive(rebuilt, blobs)
    checked, checked_blobs = loads_content_archive(output)
    if checked != rebuilt or checked_blobs != blobs:
        raise ValueError("context repair did not preserve archive roundtrip/source assets")
    return output, {
        "id": rebuilt["id"],
        "version": rebuilt["version"],
        "checksum": rebuilt["checksum"],
        "archive_sha256": hashlib.sha256(output).hexdigest(),
        "source_archive_sha256": source_sha,
        "changed_artifacts": changed,
        "published": False,
        "source_assets_changed": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("output must be a new archive path")
    data, report = repair_archive(args.archive.read_bytes())
    with args.output.open("xb") as stream:
        stream.write(data)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
