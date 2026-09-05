"""Bind Steel Defender ownership to the reviewed feature entitlement.

This is an exact-hash-bound, source-only repair.  It adds no runtime behavior;
the dependent actor template is bound to the uniquely reviewed feature card.
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
_PREFIX = "dnd5e.addon.rulebook.d-d-5e-eberron-rising-from-the-last-war.31293633134f"
_SOURCE_SHA = "6c045a44eba3e231d4e65897c1617f6543df7f85f5ceaa16e008c69dd01d2f09"
_ASSET_SHA = "38daf35316b56176b2a516aa09174c76b67bd0d905b831c12e8868d04781782c"
_CHUNK_KEY = _SOURCE_KEY + "/section-390/chunk-417-e7d3b85f277baaaa"
_CHUNK_SHA = "e7d3b85f277baaaa66a0f39eba6b3dd51c0adef804ee5fb4e1bc897bdb6257fa"
_PACKAGE_VERSION = "1.0.5-local.steel-defender-owner-binding.1"
_DEFINITION_VERSION = "1.0.3-local.steel-defender-owner-binding.1"


def _one(values, label):
    values = list(values)
    if len(values) != 1:
        raise ValueError(f"expected exactly one {label}")
    return values[0]


def repair_archive(data: bytes) -> tuple[bytes, dict]:
    source_sha = hashlib.sha256(data).hexdigest()
    if source_sha != _SOURCE_SHA:
        raise ValueError("archive is not the exact reviewed owner-binding input")
    package, blobs = loads_content_archive(data)
    validate_dnd_content_package(package)
    if package["id"] != _PREFIX + ".addon" or package["metadata"].get("distribution") != "private":
        raise ValueError("owner-binding input identity/distribution mismatch")
    source = _one((s for s in package["sources"] if s["source_key"] == _SOURCE_KEY), "source")
    asset = _one(
        (a for a in package["assets"] if a["asset_key"] == source["normalized_document_asset_key"]),
        "normalized asset",
    )
    if asset["checksum"] != _ASSET_SHA:
        raise ValueError("normalized source asset checksum mismatch")
    document = blobs[_ASSET_SHA].decode("utf-8")
    chunks = [
        c for section in source["sections"] for c in section["chunks"] if c["key"] == _CHUNK_KEY
    ]
    chunk = _one(chunks, "reviewed feature chunk")
    excerpt = document[chunk["start_offset"] : chunk["end_offset"]]
    if (
        chunk["content_hash"] != _CHUNK_SHA
        or hashlib.sha256(excerpt.encode()).hexdigest() != _CHUNK_SHA
    ):
        raise ValueError("reviewed feature chunk checksum mismatch")
    folded = excerpt.casefold()
    if not all(marker in folded for marker in ("faithful companion", "bonus action", "long rest")):
        raise ValueError("reviewed feature chunk lacks owner-binding evidence")
    corrected = deepcopy(package)
    feature_id = _PREFIX + ".feature.steel-defender"
    feature = _one(
        (a for a in corrected["content"]["artifacts"] if a.get("id") == feature_id),
        "Steel Defender feature artifact",
    )
    feature_citations = [
        c
        for clause in feature.get("rule_clauses", [])
        for c in clause.get("source_citations", [])
        if c.get("source_ref", {}).get("chunk_key") == _CHUNK_KEY
    ]
    _one(feature_citations, "Steel Defender feature citation")
    statblock = _one(
        (
            a
            for a in corrected["content"]["artifacts"]
            if a.get("id") == _PREFIX + ".statblock.steel-defender"
        ),
        "Steel Defender statblock",
    )
    template = statblock["card"].get("dependent_actor_template")
    if not isinstance(template, dict):
        raise ValueError("Steel Defender dependent actor template is missing")
    template["owner_binding"] = {
        "schema_version": 1,
        "kind": "feature_entitlement",
        "feature_artifact_id": feature_id,
        "relation_key": "steel_defender",
    }
    statblock["selection_contract"] = build_selection_contract(
        statblock,
        status=statblock["selection_contract"]["status"],
        references=statblock["selection_contract"].get("references", []),
    )
    statblock["catalog_review"] = build_catalog_review(
        statblock, decisions=statblock["catalog_review"].get("decisions", [])
    )
    definition = _one(
        (d for d in corrected["content"]["rule_definitions"] if d["id"] == _PREFIX),
        "rule definition",
    )
    definition["version"] = _DEFINITION_VERSION
    definition["manifest"]["version"] = _DEFINITION_VERSION
    members = [
        a for a in corrected["content"]["artifacts"] if a["rule_definition_id"] == definition["id"]
    ]
    definition["definition_checksum"] = content_definition_checksum(
        manifest=definition["manifest"],
        artifacts=members,
        mechanics=[
            m
            for m in corrected["content"]["mechanics"]
            if m.get("rule_definition_id") == definition["id"]
        ],
    )
    corrected["metadata"]["local_steel_defender_owner_binding_repair"] = {
        "source_archive_sha256": source_sha,
        "normalized_asset_sha256": _ASSET_SHA,
        "source_chunk_key": _CHUNK_KEY,
        "source_chunk_sha256": _CHUNK_SHA,
        "feature_artifact_id": feature_id,
        "published": False,
    }
    rebuilt = build_content_package(
        kind=corrected["kind"],
        package_id=corrected["id"],
        version=_PACKAGE_VERSION,
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
        raise ValueError("owner-binding repair did not preserve archive roundtrip")
    return output, {
        "id": rebuilt["id"],
        "version": rebuilt["version"],
        "checksum": rebuilt["checksum"],
        "archive_sha256": hashlib.sha256(output).hexdigest(),
        "source_archive_sha256": source_sha,
        "feature_artifact_id": feature_id,
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
