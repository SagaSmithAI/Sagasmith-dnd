"""Bind the Eberron Artificer starting-equipment contract to exact source bytes."""

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
from sagasmith_dnd.content_packages import (
    content_definition_checksum,
    validate_dnd_content_package,
)
from sagasmith_dnd.content_validation import build_catalog_review, build_selection_contract

_SOURCE_KEY = "user.rulebook.d-d-5e-eberron-rising-from-the-last-war.3129363313"
_PREFIX = "dnd5e.addon.rulebook.d-d-5e-eberron-rising-from-the-last-war.31293633134f"
_SOURCE_SHA = "bfd0d326c22abef2c3bb5770c6683a1d82ce522ea4aa4ddc2dba01750a14c122"
_ASSET_SHA = "38daf35316b56176b2a516aa09174c76b67bd0d905b831c12e8868d04781782c"
_CHUNK_SHA = "a6c7c7a1e1a8b94fa5fac3b5a9fb57092e09ce37ffca9a4fa56c95be8f05361a"
_CHUNK_KEY = f"{_SOURCE_KEY}/section-324/chunk-349-{_CHUNK_SHA[:16]}"
_PACKAGE_VERSION = "1.0.6-local.starting-equipment.1"
_DEFINITION_VERSION = "1.0.4-local.starting-equipment.1"
_SRD = "dnd5e.content.srd2014.item."
_SIMPLE = [
    _SRD + item
    for item in (
        "club",
        "dagger",
        "greatclub",
        "handaxe",
        "javelin",
        "light-hammer",
        "mace",
        "quarterstaff",
        "sickle",
        "spear",
        "crossbow-light",
        "dart",
        "shortbow",
        "sling",
    )
]


def _one(values, label):
    values = list(values)
    if len(values) != 1:
        raise ValueError(f"expected exactly one {label}")
    return values[0]


def _contract():
    return {
        "items": [
            {"artifact_id": _SRD + "crossbow-light", "quantity": 1},
            {"artifact_id": _SRD + "crossbow-bolts", "quantity": 20},
            {"artifact_id": _SRD + "thieves-tools", "quantity": 1},
            {"artifact_id": _SRD + "dungeoneer-s-pack", "quantity": 1},
        ],
        "choices": [
            {"id": "simple_weapons", "count": 2, "options": _SIMPLE, "allow_duplicates": True},
            {
                "id": "armor",
                "count": 1,
                "options": [_SRD + "studded-leather", _SRD + "scale-mail"],
                "allow_duplicates": False,
            },
        ],
        "gold_alternative": {
            "dice": "5d4",
            "multiplier": 10,
            "denomination": "gp",
            "replaces_background_equipment": True,
        },
    }


def repair_archive(data: bytes) -> tuple[bytes, dict]:
    source_sha = hashlib.sha256(data).hexdigest()
    if source_sha != _SOURCE_SHA:
        raise ValueError("archive is not the exact reviewed starting-equipment input")
    package, blobs = loads_content_archive(data)
    validate_dnd_content_package(package)
    if (
        package["id"] != _PREFIX + ".addon"
        or package["version"] != "1.0.5-local.steel-defender-owner-binding.1"
    ):
        raise ValueError("starting-equipment input identity/version mismatch")
    source = _one((s for s in package["sources"] if s["source_key"] == _SOURCE_KEY), "source")
    asset = _one(
        (a for a in package["assets"] if a["asset_key"] == source["normalized_document_asset_key"]),
        "normalized asset",
    )
    if asset["checksum"] != _ASSET_SHA or _ASSET_SHA not in blobs:
        raise ValueError("normalized source asset checksum mismatch")
    document = blobs[_ASSET_SHA].decode("utf-8")
    chunk = _one(
        (c for section in source["sections"] for c in section["chunks"] if c["key"] == _CHUNK_KEY),
        "starting equipment chunk",
    )
    excerpt = document[chunk["start_offset"] : chunk["end_offset"]]
    if (
        chunk["content_hash"] != _CHUNK_SHA
        or hashlib.sha256(excerpt.encode()).hexdigest() != _CHUNK_SHA
    ):
        raise ValueError("starting equipment chunk checksum mismatch")
    corrected = deepcopy(package)
    artifact_id = _PREFIX + ".class.artificer"
    artifact = _one(
        (a for a in corrected["content"]["artifacts"] if a.get("id") == artifact_id),
        "Artificer class artifact",
    )
    class_definition = deepcopy(artifact["card"]["class_definition"])
    class_definition["starting_equipment"] = _contract()
    artifact["card"]["class_definition"] = class_definition
    ref = {
        "chunk_key": _CHUNK_KEY,
        "note": "Starting equipment source evidence",
        "source_key": _SOURCE_KEY,
    }
    artifact["source_refs"] = [*artifact.get("source_refs", []), ref]
    artifact["rule_refs"] = [
        *artifact.get("rule_refs", []),
        f"rule-source:{_SOURCE_KEY}#chunk:{_CHUNK_KEY}",
    ]
    selection = artifact["selection_contract"]
    artifact["selection_contract"] = build_selection_contract(
        artifact,
        status=selection["status"],
        references=[*selection.get("references", []), f"rule-source-chunk:{_CHUNK_KEY}"],
        blockers=selection.get("blockers", []),
    )
    artifact["catalog_review"] = build_catalog_review(
        artifact, decisions=artifact["catalog_review"]["decisions"]
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
    corrected["metadata"]["local_artificer_starting_equipment_repair"] = {
        "source_archive_sha256": source_sha,
        "normalized_asset_sha256": _ASSET_SHA,
        "source_chunk_key": _CHUNK_KEY,
        "source_chunk_sha256": _CHUNK_SHA,
        "changed_artifacts": [artifact_id],
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
        raise ValueError("starting-equipment repair did not preserve archive roundtrip")
    return output, {
        "id": rebuilt["id"],
        "version": rebuilt["version"],
        "checksum": rebuilt["checksum"],
        "archive_sha256": hashlib.sha256(output).hexdigest(),
        "source_archive_sha256": source_sha,
        "changed_artifacts": [artifact_id],
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
