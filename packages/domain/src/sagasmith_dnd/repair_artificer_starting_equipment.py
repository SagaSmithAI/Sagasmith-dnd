"""Private, source-bound repair for the Eberron Artificer class card.

This module edits an in-memory package mapping only.  It deliberately does not
read, write, or repack the source archive; callers remain responsible for
keeping the original blobs unchanged and for publishing the resulting pack.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

PACKAGE_ID = (
    "dnd5e.addon.rulebook.d-d-5e-eberron-rising-from-the-last-war.31293633134f.addon"
)
SOURCE_VERSION = "1.0.5-local.steel-defender-owner-binding.1"
TARGET_VERSION = "1.0.6-local.starting-equipment.1"
TARGET_DEFINITION_VERSION = "1.0.4-local.starting-equipment.1"
SOURCE_ARCHIVE_SHA256 = "bfd0d326c22abef2c3bb5770c6683a1d82ce522ea4aa4ddc2dba01750a14c122"
SOURCE_ASSET_SHA256 = "38daf35316b56176b2a516aa09174c76b67bd0d905b831c12e8868d04781782c"
SOURCE_KEY = "user.rulebook.d-d-5e-eberron-rising-from-the-last-war.3129363313"
SOURCE_CHUNK_SHA256 = "a6c7c7a1e1a8b94fa5fac3b5a9fb57092e09ce37ffca9a4fa56c95be8f05361a"
SOURCE_CHUNK_KEY = f"{SOURCE_KEY}/section-324/chunk-349-{SOURCE_CHUNK_SHA256}"
ARTIFICER_ID = f"{PACKAGE_ID[:-6]}.class.artificer"

SIMPLE_WEAPONS = [
    f"dnd5e.content.srd2014.item.{item}"
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
STUDDED_LEATHER = "dnd5e.content.srd2014.item.studded-leather"
SCALE_MAIL = "dnd5e.content.srd2014.item.scale-mail"


def artificer_starting_equipment_contract() -> dict[str, Any]:
    """Return the reviewed 2014 Eberron Artificer starting-equipment contract."""

    return {
        "items": [
            {"artifact_id": "dnd5e.content.srd2014.item.crossbow-light", "quantity": 1},
            {"artifact_id": "dnd5e.content.srd2014.item.crossbow-bolts", "quantity": 20},
            {"artifact_id": "dnd5e.content.srd2014.item.thieves-tools", "quantity": 1},
            {"artifact_id": "dnd5e.content.srd2014.item.dungeoneer-s-pack", "quantity": 1},
        ],
        "choices": [
            {
                "id": "simple_weapons",
                "count": 2,
                "options": SIMPLE_WEAPONS,
                "allow_duplicates": True,
            },
            {
                "id": "armor",
                "count": 1,
                "options": [STUDDED_LEATHER, SCALE_MAIL],
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


def _fingerprint(artifact: Mapping[str, Any]) -> str:
    value = copy.deepcopy(dict(artifact))
    value.pop("rule_definition_id", None)
    for field in ("catalog_review", "selection_contract", "runtime_contract"):
        value.pop(field, None)
    if isinstance(value.get("card"), Mapping):
        card = copy.deepcopy(dict(value["card"]))
        card.pop("image", None)
        value["card"] = card
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _definition_checksum(
    manifest: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]],
    mechanics: Sequence[Mapping[str, Any]],
) -> str:
    def records(values: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [
            {k: copy.deepcopy(v) for k, v in dict(x).items() if k != "rule_definition_id"}
            for x in values
        ]

    value = {
        "manifest": copy.deepcopy(dict(manifest)),
        "artifacts": records(artifacts),
        "mechanics": records(mechanics),
    }
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def repair_artificer_starting_equipment(
    package: Mapping[str, Any],
    *,
    source_archive_sha256: str = SOURCE_ARCHIVE_SHA256,
    source_asset_sha256: str = SOURCE_ASSET_SHA256,
    source_chunk_sha256: str = SOURCE_CHUNK_SHA256,
) -> dict[str, Any]:
    """Return a repaired package, rejecting any non-exact source binding."""

    if not isinstance(package, Mapping):
        raise TypeError("package must be an object")
    if source_archive_sha256 != SOURCE_ARCHIVE_SHA256 or source_asset_sha256 != SOURCE_ASSET_SHA256:
        raise ValueError("source archive or asset hash is not the approved exact input")
    if source_chunk_sha256 != SOURCE_CHUNK_SHA256:
        raise ValueError("source chunk hash is not the approved exact input")
    if package.get("id") != PACKAGE_ID or package.get("version") != SOURCE_VERSION:
        raise ValueError("package id/version is not the approved Eberron input")
    candidate = copy.deepcopy(dict(package))
    artifacts = candidate.get("content", {}).get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("package content artifacts must be a list")
    classes = [x for x in artifacts if isinstance(x, Mapping) and x.get("id") == ARTIFICER_ID]
    if len(classes) != 1:
        raise ValueError("package must contain exactly one Artificer class artifact")
    class_artifact = next(x for x in artifacts if x.get("id") == ARTIFICER_ID)
    card = class_artifact.get("card")
    if not isinstance(card, Mapping):
        raise ValueError("Artificer class card is missing")

    contract = artificer_starting_equipment_contract()
    card = copy.deepcopy(dict(card))
    card["starting_equipment"] = contract
    class_artifact["card"] = card
    chunk_ref = {
        "chunk_key": SOURCE_CHUNK_KEY,
        "note": "Starting equipment source evidence",
        "page": 55,
        "source_key": SOURCE_KEY,
    }
    source_refs = list(class_artifact.get("source_refs") or [])
    if not any(
        isinstance(ref, Mapping) and ref.get("chunk_key") == SOURCE_CHUNK_KEY
        for ref in source_refs
    ):
        source_refs.append(chunk_ref)
    class_artifact["source_refs"] = source_refs
    rule_ref = f"rule-source:{SOURCE_KEY}#chunk:{SOURCE_CHUNK_KEY}"
    class_artifact["rule_refs"] = list(
        dict.fromkeys([*class_artifact.get("rule_refs", []), rule_ref])
    )
    selection = copy.deepcopy(dict(class_artifact.get("selection_contract") or {}))
    refs = list(selection.get("references") or [])
    selection["references"] = list(dict.fromkeys([*refs, f"rule-source-chunk:{SOURCE_CHUNK_KEY}"]))
    selection["reviewed_content_hash"] = _fingerprint(class_artifact)
    class_artifact["selection_contract"] = selection
    review = copy.deepcopy(dict(class_artifact.get("catalog_review") or {}))
    review["reviewed_content_hash"] = _fingerprint(class_artifact)
    class_artifact["catalog_review"] = review
    metadata = copy.deepcopy(dict(candidate.get("metadata") or {}))
    metadata["local_artificer_starting_equipment_repair"] = {
        "provisional": True,
        "source_archive_sha256": SOURCE_ARCHIVE_SHA256,
        "source_asset_sha256": SOURCE_ASSET_SHA256,
        "source_chunk_key": SOURCE_CHUNK_KEY,
        "source_chunk_sha256": SOURCE_CHUNK_SHA256,
        "source_version": SOURCE_VERSION,
        "target_version": TARGET_VERSION,
        "target_definition_version": TARGET_DEFINITION_VERSION,
        "changed_artifacts": [ARTIFICER_ID],
    }
    candidate["metadata"] = metadata
    candidate["version"] = TARGET_VERSION
    for definition in candidate.get("content", {}).get("rule_definitions") or []:
        if isinstance(definition, dict):
            definition["version"] = TARGET_DEFINITION_VERSION
            definition["definition_checksum"] = _definition_checksum(
                definition.get("manifest", {}),
                [x for x in artifacts if x.get("rule_definition_id") == definition.get("id")],
                [
                    x
                    for x in candidate["content"].get("mechanics", [])
                    if x.get("rule_definition_id") == definition.get("id")
                ],
            )
    return candidate
