"""Repair the exact source citation of Eberron's Steel Defender statblock.

This is a hash-bound, source-only QA transformation.  It does not publish or
interpret the statblock; it only replaces the citation selected from the
preceding spell-table chunk with the reviewed, split statblock chunks from
the same normalized source asset.
"""

from __future__ import annotations

import argparse
import hashlib
from copy import deepcopy
from pathlib import Path

from sagasmith_core.content_pack import (
    build_content_package,
    dumps_content_archive,
    loads_content_archive,
)
from sagasmith_dnd.content_packages import content_definition_checksum, validate_dnd_content_package
from sagasmith_dnd.content_validation import build_catalog_review, build_selection_contract

_SOURCE_KEY = "user.rulebook.d-d-5e-eberron-rising-from-the-last-war.3129363313"
_RECIPES = {
    "17d673579776c31f58ac412753676d8a9c811f950d763ed4230cebfc36ca25b3": {
        "prefix": "dnd5e.addon.rulebook.d-d-5e-eberron-rising-from-the-last-war.31293633134f",
        "source_key": _SOURCE_KEY,
        "normalized_asset_sha256": (
            "38daf35316b56176b2a516aa09174c76b67bd0d905b831c12e8868d04781782c"
        ),
        "evidence": (
            {
                "chunk_key": _SOURCE_KEY
                + "/section-393/chunk-420-2acecb9960fe16cd",
                "chunk_sha256": "2acecb9960fe16cd0e4fc095cbd37524262fe4c5c67ebd532c5d730dea8e5acd",
                "markers": ("Armor Class", "Hit Points"),
            },
            {
                "chunk_key": _SOURCE_KEY
                + "/section-398/chunk-425-da46e35df46c6c61",
                "chunk_sha256": "da46e35df46c6c61633641978e8b86c5123cca21bec922dc8d28b5e14e2130cb",
                "markers": ("Might of the Master", "Saving Throws", "Skills"),
            },
            {
                "chunk_key": _SOURCE_KEY
                + "/section-399/chunk-426-237e1dc69dd604ad",
                "chunk_sha256": "237e1dc69dd604ad3f1b1de02646b535819eb9c211bb74614ce58171a63543bf",
                "markers": ("Force-Empowered Rend", "Repair"),
            },
            {
                "chunk_key": _SOURCE_KEY
                + "/section-400/chunk-427-d973aba290d33472",
                "chunk_sha256": "d973aba290d33472e9ce3705847673c94d92ccf40436e4809ecfac5789886f4f",
                # The normalized OCR renders "Deflect" as "De.fleet".  The
                # exact asset, section, chunk key and hash are authoritative.
                "markers": ("Attack",),
            },
        ),
        "version": "1.0.4-local.steel-defender-citation.1",
        "definition_version": "1.0.2-local.steel-defender-citation.1",
    }
}


def _one(values, label):
    values = list(values)
    if len(values) != 1:
        raise ValueError(f"expected exactly one {label}")
    return values[0]


def _normalized_chunk_map(
    package,
    blobs,
    source_key,
    *,
    expected_asset_sha256=None,
    expected_chunk_sha256=None,
):
    source = _one(
        (item for item in package["sources"] if item["source_key"] == source_key), "source"
    )
    asset = _one(
        (
            item
            for item in package["assets"]
            if item["asset_key"] == source["normalized_document_asset_key"]
        ),
        "normalized source asset",
    )
    if expected_asset_sha256 is not None and asset["checksum"] != expected_asset_sha256:
        raise ValueError("normalized source asset checksum mismatch")
    document = blobs[asset["checksum"]].decode("utf-8")
    chunks = {}
    expected_chunk_sha256 = expected_chunk_sha256 or {}
    for section in source["sections"]:
        for chunk in section["chunks"]:
            text = document[chunk["start_offset"] : chunk["end_offset"]]
            if hashlib.sha256(text.encode()).hexdigest() != chunk["content_hash"]:
                raise ValueError("source chunk checksum mismatch")
            expected_hash = expected_chunk_sha256.get(chunk["key"])
            if expected_hash is not None and chunk["content_hash"] != expected_hash:
                raise ValueError("reviewed Steel Defender source chunk checksum mismatch")
            chunks[chunk["key"]] = " ".join(text.split())
    if not set(expected_chunk_sha256).issubset(chunks):
        raise ValueError("reviewed Steel Defender source chunk is missing")
    return chunks


def repair_archive(data: bytes) -> tuple[bytes, dict]:
    source_sha = hashlib.sha256(data).hexdigest()
    recipe = _RECIPES.get(source_sha)
    if recipe is None:
        raise ValueError("archive is not an exact reviewed Steel Defender repair input")
    package, blobs = loads_content_archive(data)
    validate_dnd_content_package(package)
    if (
        package["id"] != recipe["prefix"] + ".addon"
        or package["metadata"].get("distribution") != "private"
    ):
        raise ValueError("Steel Defender repair input identity/distribution mismatch")
    chunks = _normalized_chunk_map(
        package,
        blobs,
        recipe["source_key"],
        expected_asset_sha256=recipe["normalized_asset_sha256"],
        expected_chunk_sha256={
            item["chunk_key"]: item["chunk_sha256"] for item in recipe["evidence"]
        },
    )
    reviewed_evidence = []
    for evidence in recipe["evidence"]:
        chunk_key = evidence["chunk_key"]
        excerpt = chunks.get(chunk_key)
        if excerpt is None:
            raise ValueError("reviewed Steel Defender source chunk is missing")
        if any(marker not in excerpt for marker in evidence["markers"]):
            raise ValueError("reviewed Steel Defender source chunk lacks required evidence")
        reviewed_evidence.append((chunk_key, excerpt))
    corrected = deepcopy(package)
    statblocks = [
        artifact
        for artifact in corrected["content"]["artifacts"]
        if artifact.get("id") == recipe["prefix"] + ".statblock.steel-defender"
    ]
    statblock = _one(statblocks, "Steel Defender statblock")
    clauses = statblock.get("rule_clauses") or []
    clause = _one(clauses, "Steel Defender statblock clause")
    _one(clause.get("source_citations") or [], "Steel Defender statblock citation")
    clause["source_citations"] = [
        {
            "source": "rule-source:" + recipe["source_key"],
            "source_ref": {"chunk_key": chunk_key},
            "source_excerpt": excerpt[:4000],
        }
        for chunk_key, excerpt in reviewed_evidence
    ]
    statblock["selection_contract"] = build_selection_contract(
        statblock,
        status=statblock["selection_contract"]["status"],
        references=statblock["selection_contract"].get("references", []),
    )
    statblock["catalog_review"] = build_catalog_review(
        statblock,
        decisions=statblock["catalog_review"].get("decisions", []),
    )
    definition = _one(
        (
            item
            for item in corrected["content"]["rule_definitions"]
            if item["id"] == recipe["prefix"]
        ),
        "rule definition",
    )
    definition["version"] = recipe["definition_version"]
    definition["manifest"]["version"] = recipe["definition_version"]
    members = [
        item
        for item in corrected["content"]["artifacts"]
        if item["rule_definition_id"] == definition["id"]
    ]
    definition["definition_checksum"] = content_definition_checksum(
        manifest=definition["manifest"],
        artifacts=members,
        mechanics=[
            item
            for item in corrected["content"]["mechanics"]
            if item.get("rule_definition_id") == definition["id"]
        ],
    )
    corrected["metadata"]["local_steel_defender_citation_repair"] = {
        "source_archive_sha256": source_sha,
        "source_chunk_keys": [item[0] for item in reviewed_evidence],
        "provisional": True,
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
        raise ValueError("Steel Defender repair did not preserve archive roundtrip/source assets")
    return output, {
        "id": rebuilt["id"],
        "version": rebuilt["version"],
        "checksum": rebuilt["checksum"],
        "archive_sha256": hashlib.sha256(output).hexdigest(),
        "source_archive_sha256": source_sha,
        "source_chunk_keys": [item[0] for item in reviewed_evidence],
        "published": False,
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
    print(report)


if __name__ == "__main__":
    raise SystemExit(main())
