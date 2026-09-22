"""Application file adapters extracted from Domain official_expansions."""
from __future__ import annotations

import hashlib
import json
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from sagasmith_dnd.content_packages import validate_dnd_content_package
from sagasmith_dnd.official_expansions import (
    _DESCRIPTOR_NAME,
    CONTENT_LIBRARY_INDEX_SCHEMA,
    OFFICIAL_EXPANSION_CLASSIFICATIONS,
    OFFICIAL_EXPANSION_LOCK_SCHEMA,
    OfficialExpansionArchive,
    _summarize_package,
    load_official_expansion_lock,
)


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


def verify_official_expansion_library(
    path: Path,
    *,
    lock: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify every locked expansion and core dependency in a local library.

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

    # Mounting already checks support archives independently. The standalone
    # verifier must make the same check before claiming the library is valid.
    support_archives = (
        resolve_official_expansion_support_archives(root, lock=expected)
        if expected.get("support_packages")
        else ()
    )
    support_ids = {archive.id for archive in support_archives}

    reports: list[dict[str, Any]] = []
    discovered_official: set[str] = set()
    for package_id, item in indexed_by_id.items():
        if package_id in support_ids:
            continue
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
        "source_commit_role": expected.get("source_commit_role"),
        "library_root": str(root),
        "packages": sorted(reports, key=lambda item: item["publication_id"]),
        "support_packages": [
            {
                "id": archive.id,
                "version": archive.version,
                "checksum": archive.checksum,
                "archive_sha256": archive.archive_sha256,
                "role": archive.role,
            }
            for archive in support_archives
        ],
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


def resolve_official_expansion_archives(
    path: Path,
    *,
    lock: Mapping[str, Any] | None = None,
) -> tuple[OfficialExpansionArchive, ...]:
    """Resolve every verified registry entry to an exact authorized local archive.

    Verification is deliberately completed before any paths are returned.  The
    consumer must still re-check ``archive_sha256`` immediately before import to
    close the local time-of-check/time-of-use boundary.
    """

    expected = dict(lock or load_official_expansion_lock())
    report = verify_official_expansion_library(path, lock=expected)
    root = _content_library_root(Path(path))
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    indexed = {
        str(item.get("id") or ""): dict(item)
        for item in index.get("packages", [])
        if isinstance(item, dict)
    }
    locked = {str(item["id"]): dict(item) for item in expected["packages"]}
    resolved = []
    for item in report["packages"]:
        package_id = str(item["id"])
        registry_item = locked[package_id]
        index_item = indexed[package_id]
        resolved.append(
            OfficialExpansionArchive(
                id=package_id,
                version=str(registry_item["version"]),
                checksum=str(registry_item["checksum"]),
                archive_sha256=str(registry_item["archive_sha256"]),
                publication_id=str(registry_item["publication_id"]),
                title=str(registry_item["title"]),
                classification=str(registry_item["classification"]),
                editions=tuple(str(value) for value in registry_item["editions"]),
                path=_portable_archive_path(root, index_item.get("path")),
            )
        )
    return tuple(sorted(resolved, key=lambda item: item.publication_id))


def resolve_official_expansion_support_archives(
    path: Path,
    *,
    lock: Mapping[str, Any] | None = None,
) -> tuple[OfficialExpansionArchive, ...]:
    """Resolve and fully validate rights-gated core dependencies before mounting."""

    expected = dict(lock or load_official_expansion_lock())
    root = _content_library_root(Path(path))
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    if not isinstance(index, dict) or index.get("schema") != CONTENT_LIBRARY_INDEX_SCHEMA:
        raise ValueError("content library uses an unsupported index schema")
    indexed = {
        str(item.get("id") or ""): dict(item)
        for item in index.get("packages", [])
        if isinstance(item, dict)
    }
    resolved = []
    for support in expected["support_packages"]:
        package_id = str(support["id"])
        item = indexed.get(package_id)
        if item is None:
            raise ValueError(f"content library is missing official support: {package_id}")
        archive_path = _portable_archive_path(root, item.get("path"))
        descriptor, archive_sha256, archive_size = _read_descriptor(archive_path)
        normalized = validate_dnd_content_package(descriptor)
        for field in ("id", "version", "checksum"):
            if normalized.get(field) != support[field] or item.get(field) != support[field]:
                raise ValueError(f"official support {package_id} has a mismatched {field}")
        if archive_sha256 != support["archive_sha256"] or item.get(
            "archive_sha256"
        ) != support["archive_sha256"]:
            raise ValueError(f"official support {package_id} archive checksum is stale")
        if archive_size != item.get("archive_size"):
            raise ValueError(f"official support {package_id} archive size is stale")
        manifest = dict(normalized.get("manifest") or {})
        if (
            normalized.get("system_id") != "dnd5e"
            or normalized.get("kind") != "addon"
            or manifest.get("classification") != support["classification"]
            or list(manifest.get("editions") or []) != support["editions"]
        ):
            raise ValueError(f"official support {package_id} semantics are stale")
        definitions = {
            (str(item["id"]), str(item["version"])): str(item["definition_checksum"])
            for item in dict(normalized.get("content") or {}).get("rule_definitions") or []
        }
        expected_definitions = {
            (str(item["id"]), str(item["version"])): str(item["checksum"])
            for item in support["provided_rule_definitions"]
        }
        if definitions != expected_definitions:
            raise ValueError(f"official support {package_id} definitions are stale")
        resolved.append(
            OfficialExpansionArchive(
                id=package_id,
                version=str(support["version"]),
                checksum=str(support["checksum"]),
                archive_sha256=str(support["archive_sha256"]),
                publication_id="phb2014",
                title=str(support["title"]),
                classification=str(support["classification"]),
                editions=tuple(str(value) for value in support["editions"]),
                path=archive_path,
                role=str(support["role"]),
            )
        )
    return tuple(resolved)
