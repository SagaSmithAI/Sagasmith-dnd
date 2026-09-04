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
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterable, Mapping

from sagasmith_dnd.content_packages import validate_dnd_content_package
from sagasmith_dnd.content_validation import DND_SELECTION_MATERIALIZERS

OFFICIAL_EXPANSION_LOCK_SCHEMA = "sagasmith.dnd-official-expansions-lock.v1"
CONTENT_LIBRARY_INDEX_SCHEMA = "sagasmith.current-content-packs.v1"
OFFICIAL_EXPANSION_CLASSIFICATIONS = frozenset(
    {"official_supplement", "official_legacy"}
)
_DESCRIPTOR_NAME = "package.sagasmith.json"


@dataclass(frozen=True)
class OfficialExpansionArchive:
    """One rights-gated archive resolved from the built-in expansion registry."""

    id: str
    version: str
    checksum: str
    archive_sha256: str
    publication_id: str
    title: str
    classification: str
    editions: tuple[str, ...]
    path: Path
    role: str = "expansion"


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
    builtin_definitions = value.get("builtin_rule_definitions")
    if not isinstance(builtin_definitions, list) or not builtin_definitions:
        raise ValueError("official expansion lock builtin_rule_definitions must be non-empty")
    support_definitions: dict[tuple[str, str], str] = {}
    for definition in builtin_definitions:
        if not isinstance(definition, dict) or set(definition) != {
            "id",
            "version",
            "checksum",
        }:
            raise ValueError("official expansion built-in rule definition is invalid")
        support_definitions[(str(definition["id"]), str(definition["version"]))] = str(
            definition["checksum"]
        )
    support_packages = value.get("support_packages")
    if not isinstance(support_packages, list):
        raise ValueError("official expansion lock support_packages must be an array")
    for index, raw in enumerate(support_packages):
        if not isinstance(raw, dict):
            raise ValueError(f"official expansion support_packages[{index}] must be an object")
        required = {
            "role",
            "id",
            "version",
            "checksum",
            "archive_sha256",
            "title",
            "classification",
            "editions",
            "provided_rule_definitions",
        }
        missing = sorted(required - set(raw))
        if missing:
            raise ValueError(
                f"official expansion support_packages[{index}] is missing: "
                + ", ".join(missing)
            )
        if raw["role"] != "official_core_dependency":
            raise ValueError("unsupported official expansion support package role")
        if raw["classification"] != "official_core":
            raise ValueError("official expansion support package must be official_core")
        for field in ("checksum", "archive_sha256"):
            digest = str(raw[field])
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError(f"official expansion support {field} must be lowercase SHA-256")
        definitions = raw["provided_rule_definitions"]
        if not isinstance(definitions, list) or not definitions:
            raise ValueError("official expansion support must provide rule definitions")
        for definition in definitions:
            if not isinstance(definition, dict) or set(definition) != {
                "id",
                "version",
                "checksum",
            }:
                raise ValueError("official expansion support definition is invalid")
            support_definitions[(str(definition["id"]), str(definition["version"]))] = str(
                definition["checksum"]
            )
    rebinds = value.get("dependency_rebinds")
    if not isinstance(rebinds, list):
        raise ValueError("official expansion lock dependency_rebinds must be an array")
    rebind_keys: set[tuple[str, str, str, str, str]] = set()
    for index, raw in enumerate(rebinds):
        if not isinstance(raw, dict) or set(raw) != {
            "package_id",
            "definition_id",
            "dependency_id",
            "dependency_version",
            "runtime_version",
            "source_checksum",
            "runtime_checksum",
            "basis",
        }:
            raise ValueError(f"official expansion dependency_rebinds[{index}] is invalid")
        known_packages = {
            *{item[0] for item in identities},
            *{str(item["id"]) for item in support_packages},
        }
        package_id = str(raw["package_id"])
        definition_id = str(raw["definition_id"])
        if package_id == "*" or definition_id == "*":
            raise ValueError("official expansion dependency rebinds must be explicitly scoped")
        if package_id not in known_packages:
            raise ValueError("official expansion dependency rebind references an unknown package")
        if not definition_id:
            raise ValueError("official expansion dependency rebind definition id must not be empty")
        rebind_key = (
            package_id,
            definition_id,
            str(raw["dependency_id"]),
            str(raw["dependency_version"]),
            str(raw["source_checksum"]),
        )
        if rebind_key in rebind_keys:
            raise ValueError("official expansion dependency rebind is duplicated")
        rebind_keys.add(rebind_key)
        runtime = support_definitions.get(
            (str(raw["dependency_id"]), str(raw["runtime_version"]))
        )
        if runtime != raw["runtime_checksum"]:
            raise ValueError("official expansion dependency rebind is not bound to support")
        if (
            raw["source_checksum"] == raw["runtime_checksum"]
            and raw["dependency_version"] == raw["runtime_version"]
        ) or not str(raw["basis"]).strip():
            raise ValueError("official expansion dependency rebind requires a reasoned change")
    return value


def _content_library_root(path: Path) -> Path:
    root = Path(path).expanduser().resolve()
    if (root / "index.json").is_file():
        return root
    nested = root / "content-library"
    if (nested / "index.json").is_file():
        return nested
    raise ValueError("content library path must contain index.json or content-library/index.json")


def official_expansion_catalog(
    edition: str | None = None,
    *,
    lock: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Return the built-in metadata registry without exposing commercial content."""

    value = dict(lock or load_official_expansion_lock())
    if value.get("schema") != OFFICIAL_EXPANSION_LOCK_SCHEMA:
        raise ValueError("official expansion lock uses an unsupported schema")
    normalized_edition = str(edition or "").strip()
    return tuple(
        {
            key: item[key]
            for key in (
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
            )
        }
        for item in value["packages"]
        if not normalized_edition or normalized_edition in item["editions"]
    )


def official_expansion_support_catalog(
    *,
    lock: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], ...]:
    """Return metadata for non-optional core dependencies of the expansion set."""

    value = dict(lock or load_official_expansion_lock())
    return tuple(dict(item) for item in value["support_packages"])


def official_expansion_dependency_rebinds(
    *,
    lock: Mapping[str, Any] | None = None,
) -> tuple[dict[str, str], ...]:
    """Return audited stale-to-current dependency bindings for immutable source Packs."""

    value = dict(lock or load_official_expansion_lock())
    return tuple(dict(item) for item in value["dependency_rebinds"])


def matching_official_expansion_dependency_rebinds(
    rebinds: Iterable[Mapping[str, str]],
    *,
    package_id: str,
    definition_id: str,
) -> tuple[dict[str, str], ...]:
    """Select only rebinds explicitly locked to one package component."""

    return tuple(
        dict(item)
        for item in rebinds
        if item.get("package_id") == package_id
        and item.get("definition_id") == definition_id
    )


def installed_official_definition_matches(
    *,
    source_checksum: str,
    runtime_checksum: str,
    recorded_checksum: str,
    recorded_source_checksum: str,
) -> bool:
    """Return whether an installed definition proves the expected effective identity."""

    if runtime_checksum != source_checksum:
        return (
            recorded_checksum == runtime_checksum
            and recorded_source_checksum == source_checksum
        )
    return recorded_checksum == source_checksum or recorded_source_checksum == source_checksum


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


__all__ = [
    "CONTENT_LIBRARY_INDEX_SCHEMA",
    "OfficialExpansionArchive",
    "OFFICIAL_EXPANSION_CLASSIFICATIONS",
    "OFFICIAL_EXPANSION_LOCK_SCHEMA",
    "load_official_expansion_lock",
    "official_expansion_catalog",
    "official_expansion_dependency_rebinds",
    "official_expansion_support_catalog",
    "resolve_official_expansion_archives",
    "resolve_official_expansion_support_archives",
    "verify_official_expansion_library",
]
