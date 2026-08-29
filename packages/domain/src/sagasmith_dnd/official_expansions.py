"""Verify the rights-aware official-expansion compatibility baseline.

The commercial source text and Pack archives deliberately remain outside the
Apache-2.0 runtime.  This module binds the runtime to immutable package
identities and validates an authorized local content-library checkout without
copying or downloading its content.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections import Counter
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping

from sagasmith_dnd.content_packages import validate_dnd_content_package
from sagasmith_dnd.content_validation import DND_SELECTION_MATERIALIZERS

OFFICIAL_EXPANSION_LOCK_SCHEMA = "sagasmith.dnd-official-expansions-lock.v1"
CONTENT_LIBRARY_INDEX_SCHEMA = "sagasmith.current-content-packs.v1"
OFFICIAL_EXPANSION_CLASSIFICATIONS = frozenset(
    {"official_supplement", "official_legacy"}
)
_DESCRIPTOR_NAME = "package.sagasmith.json"


def load_official_expansion_lock(path: Path | None = None) -> dict[str, Any]:
    """Load and structurally validate the shipped metadata-only compatibility lock."""

    if path is None:
        resource = files("sagasmith_dnd").joinpath("data/official-expansions.lock.json")
        value = json.loads(resource.read_text(encoding="utf-8"))
    else:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema") != OFFICIAL_EXPANSION_LOCK_SCHEMA:
        raise ValueError("official expansion lock uses an unsupported schema")
    packages = value.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ValueError("official expansion lock must contain packages")
    identities: set[tuple[str, str]] = set()
    publications: set[str] = set()
    for index, raw in enumerate(packages):
        if not isinstance(raw, dict):
            raise ValueError(f"official expansion lock packages[{index}] must be an object")
        required = {
            "id",
            "version",
            "checksum",
            "archive_sha256",
            "publication_id",
            "title",
            "classification",
            "editions",
            "content_summary",
            "selection_ready",
            "catalog_only",
        }
        missing = sorted(required - set(raw))
        if missing:
            raise ValueError(
                f"official expansion lock packages[{index}] is missing: {', '.join(missing)}"
            )
        identity = (str(raw["id"]), str(raw["version"]))
        if identity in identities:
            raise ValueError(f"official expansion lock repeats {identity[0]}@{identity[1]}")
        identities.add(identity)
        publication_id = str(raw["publication_id"])
        if not publication_id or publication_id in publications:
            raise ValueError("official expansion publication ids must be non-empty and unique")
        publications.add(publication_id)
        if raw["classification"] not in OFFICIAL_EXPANSION_CLASSIFICATIONS:
            raise ValueError(
                f"unsupported official expansion classification: {raw['classification']}"
            )
        for field in ("checksum", "archive_sha256"):
            digest = str(raw[field])
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError(f"official expansion lock {field} must be lowercase SHA-256")
        editions = raw["editions"]
        if not isinstance(editions, list) or not editions or any(
            not isinstance(edition, str) or not edition for edition in editions
        ):
            raise ValueError("official expansion editions must be a non-empty string array")
        for field in ("content_summary", "selection_ready", "catalog_only"):
            counts = raw[field]
            if not isinstance(counts, dict) or any(
                not isinstance(kind, str)
                or not kind
                or isinstance(count, bool)
                or not isinstance(count, int)
                or count < 0
                for kind, count in counts.items()
            ):
                raise ValueError(f"official expansion {field} must contain non-negative counts")
            if any(count == 0 for count in counts.values()):
                raise ValueError(f"official expansion {field} must omit zero counts")
        combined = Counter(raw["selection_ready"]) + Counter(raw["catalog_only"])
        if dict(sorted(combined.items())) != dict(sorted(raw["content_summary"].items())):
            raise ValueError("official expansion readiness counts do not match content_summary")
    return value


def _content_library_root(path: Path) -> Path:
    root = Path(path).expanduser().resolve()
    if (root / "index.json").is_file():
        return root
    nested = root / "content-library"
    if (nested / "index.json").is_file():
        return nested
    raise ValueError("content library path must contain index.json or content-library/index.json")


def _portable_archive_path(root: Path, value: object) -> Path:
    text = str(value or "")
    relative = Path(text)
    if not text or "\\" in text or relative.is_absolute():
        raise ValueError(f"official expansion archive path is not portable: {text}")
    resolved = (root / relative).resolve()
    package_root = (root / "packages").resolve()
    if resolved.parent != package_root:
        raise ValueError(f"official expansion archive escapes packages directory: {text}")
    return resolved


def _read_descriptor(path: Path) -> tuple[dict[str, Any], str, int]:
    archive_size = path.stat().st_size
    with path.open("rb") as stream:
        archive_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if names.count(_DESCRIPTOR_NAME) != 1:
                raise ValueError(f"{path.name} must contain one {_DESCRIPTOR_NAME}")
            descriptor_info = archive.getinfo(_DESCRIPTOR_NAME)
            if descriptor_info.file_size > 64 * 1024 * 1024:
                raise ValueError(f"{path.name} descriptor exceeds 64 MiB")
            value = json.loads(archive.read(_DESCRIPTOR_NAME))
    except (OSError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        raise ValueError(f"cannot read official expansion archive {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} descriptor must contain an object")
    return value, archive_sha256, archive_size


def _publication_id(package: Mapping[str, Any]) -> str:
    publications = {
        str(source.get("publication_id") or "")
        for source in package.get("sources", [])
        if isinstance(source, Mapping) and source.get("publication_id")
    }
    if len(publications) != 1:
        raise ValueError("official expansion must bind exactly one publication id")
    return publications.pop()


def _summarize_package(package: Mapping[str, Any]) -> dict[str, Any]:
    normalized = validate_dnd_content_package(package)
    manifest = dict(normalized.get("manifest") or {})
    classification = str(manifest.get("classification") or "")
    if normalized.get("system_id") != "dnd5e" or normalized.get("kind") != "addon":
        raise ValueError("official expansion must be a dnd5e addon")
    if classification not in OFFICIAL_EXPANSION_CLASSIFICATIONS:
        raise ValueError(
            f"package is not an official expansion: {classification or 'unclassified'}"
        )
    artifacts = list(dict(normalized.get("content") or {}).get("artifacts") or [])
    summary = Counter(str(artifact.get("kind") or "") for artifact in artifacts)
    if "" in summary:
        raise ValueError("official expansion contains an artifact without a kind")
    declared_summary = dict(manifest.get("content_summary") or {})
    if dict(sorted(summary.items())) != dict(sorted(declared_summary.items())):
        raise ValueError("official expansion manifest content_summary is stale")
    ready: Counter[str] = Counter()
    catalog_only: Counter[str] = Counter()
    for artifact in artifacts:
        state = str(artifact.get("application_state") or "")
        kind = str(artifact.get("kind") or "")
        if state == "selection_ready":
            if kind not in DND_SELECTION_MATERIALIZERS:
                raise ValueError(f"selection-ready official content has no materializer: {kind}")
            ready[kind] += 1
        elif state == "catalog_only":
            catalog_only[kind] += 1
        else:
            raise ValueError(f"official expansion artifact has unsupported state: {state}")
    return {
        "id": str(normalized["id"]),
        "version": str(normalized["version"]),
        "checksum": str(normalized["checksum"]),
        "publication_id": _publication_id(normalized),
        "title": str(
            dict(normalized.get("metadata") or {}).get("title")
            or manifest.get("title")
            or ""
        ),
        "classification": classification,
        "editions": list(manifest.get("editions") or []),
        "distribution": str(dict(normalized.get("metadata") or {}).get("distribution") or ""),
        "license": str(dict(normalized.get("metadata") or {}).get("license") or ""),
        "content_summary": dict(sorted(summary.items())),
        "selection_ready": dict(sorted(ready.items())),
        "catalog_only": dict(sorted(catalog_only.items())),
    }


def verify_official_expansion_library(
    path: Path,
    *,
    lock: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify every locked official expansion in an authorized local library.

    The function performs no network access and never copies Pack contents.  A
    successful result proves archive identity, D&D package semantics, complete
    artifact accounting, and selection-materializer coverage; it does not grant
    a license to use or redistribute the verified content.
    """

    expected = dict(lock or load_official_expansion_lock())
    if expected.get("schema") != OFFICIAL_EXPANSION_LOCK_SCHEMA:
        raise ValueError("official expansion lock uses an unsupported schema")
    root = _content_library_root(Path(path))
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    if not isinstance(index, dict) or index.get("schema") != CONTENT_LIBRARY_INDEX_SCHEMA:
        raise ValueError("content library uses an unsupported index schema")
    indexed = index.get("packages")
    if not isinstance(indexed, list):
        raise ValueError("content library index packages must be an array")
    expected_by_id = {str(item["id"]): dict(item) for item in expected["packages"]}
    indexed_by_id: dict[str, dict[str, Any]] = {}
    for item in indexed:
        if not isinstance(item, dict):
            raise ValueError("content library index package entries must be objects")
        package_id = str(item.get("id") or "")
        if not package_id:
            raise ValueError("content library index package id must not be empty")
        if package_id in indexed_by_id:
            raise ValueError(f"content library index repeats package id: {package_id}")
        indexed_by_id[package_id] = dict(item)
    missing = sorted(set(expected_by_id) - set(indexed_by_id))
    if missing:
        raise ValueError("content library is missing official expansions: " + ", ".join(missing))

    reports: list[dict[str, Any]] = []
    discovered_official: set[str] = set()
    for package_id, item in indexed_by_id.items():
        if item.get("system_id") != "dnd5e" or item.get("kind") != "addon":
            continue
        archive_path = _portable_archive_path(root, item.get("path"))
        descriptor, archive_sha256, archive_size = _read_descriptor(archive_path)
        classification = str(dict(descriptor.get("manifest") or {}).get("classification") or "")
        if classification not in OFFICIAL_EXPANSION_CLASSIFICATIONS:
            continue
        discovered_official.add(package_id)
        locked = expected_by_id.get(package_id)
        if locked is None:
            raise ValueError(
                f"official expansion is not represented in the runtime lock: {package_id}"
            )
        for field in ("id", "version", "checksum"):
            if descriptor.get(field) != item.get(field) or descriptor.get(field) != locked.get(
                field
            ):
                raise ValueError(f"official expansion {package_id} has a mismatched {field}")
        if archive_sha256 != item.get("archive_sha256") or archive_sha256 != locked.get(
            "archive_sha256"
        ):
            raise ValueError(f"official expansion {package_id} archive checksum is stale")
        if archive_size != item.get("archive_size"):
            raise ValueError(f"official expansion {package_id} archive size is stale")
        report = _summarize_package(descriptor)
        for field in (
            "publication_id",
            "title",
            "classification",
            "editions",
            "content_summary",
            "selection_ready",
            "catalog_only",
        ):
            if report[field] != locked[field]:
                raise ValueError(f"official expansion {package_id} lock mismatch: {field}")
        reports.append(report)

    unexpected_missing = sorted(set(expected_by_id) - discovered_official)
    if unexpected_missing:
        raise ValueError(
            "locked packages are no longer classified as official expansions: "
            + ", ".join(unexpected_missing)
        )
    content_summary: Counter[str] = Counter()
    selection_ready: Counter[str] = Counter()
    catalog_only: Counter[str] = Counter()
    for report in reports:
        content_summary.update(report["content_summary"])
        selection_ready.update(report["selection_ready"])
        catalog_only.update(report["catalog_only"])
    return {
        "schema": "sagasmith.dnd-official-expansions-verification.v1",
        "source_repository": expected.get("source_repository"),
        "source_commit": expected.get("source_commit"),
        "library_root": str(root),
        "packages": sorted(reports, key=lambda item: item["publication_id"]),
        "coverage": {
            "packages": len(reports),
            "artifacts": sum(content_summary.values()),
            "selection_ready": sum(selection_ready.values()),
            "catalog_only": sum(catalog_only.values()),
            "content_summary": dict(sorted(content_summary.items())),
            "selection_ready_by_kind": dict(sorted(selection_ready.items())),
            "catalog_only_by_kind": dict(sorted(catalog_only.items())),
        },
        "rights": {
            "content_copied": False,
            "license_granted": False,
            "note": "Verification does not authorize use or redistribution of Pack content.",
        },
        "verified": True,
    }


__all__ = [
    "CONTENT_LIBRARY_INDEX_SCHEMA",
    "OFFICIAL_EXPANSION_CLASSIFICATIONS",
    "OFFICIAL_EXPANSION_LOCK_SCHEMA",
    "load_official_expansion_lock",
    "verify_official_expansion_library",
]
