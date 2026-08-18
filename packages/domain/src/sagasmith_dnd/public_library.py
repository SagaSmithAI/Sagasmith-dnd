"""Build static private or license-gated public unified-content catalogs."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from sagasmith_core.content_pack import (
    PACKAGE_KINDS,
    loads_content_archive,
    validate_content_package,
)
from sagasmith_core.integrity import canonical_json

LIBRARY_SCHEMA = "sagasmith.content-library.v1"
PUBLIC_DISTRIBUTIONS = frozenset({"public", "shareable"})
NON_PUBLIC_LICENSES = frozenset({"", "private", "proprietary", "user-supplied"})
PUBLIC_LICENSES = frozenset(
    {
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "CC-BY-4.0",
        "CC0-1.0",
        "MIT",
        "OGL-1.0a",
    }
)
BROWSER_ASSET_KINDS = frozenset(
    {"actor_image", "normalized_document", "map", "player_reference"}
)


def _package_summary(
    package: Mapping[str, Any],
    path: str,
    download_path: str,
    *,
    archive_checksum: str,
    archive_size: int,
) -> dict[str, Any]:
    actors = list(package["actors"])
    actor_counts = Counter(str(actor["actor_type"]) for actor in actors)
    content = dict(package["content"])
    component_counts: dict[str, int] = {
        "source": len(package["sources"]),
        "asset": len(package["assets"]),
        "actor_card": len(actors),
        **dict(sorted(actor_counts.items())),
    }
    if package["kind"] in {"addon", "core_rules"}:
        component_counts.update(
            {
                "artifact": len(content.get("artifacts") or []),
                "mechanic": len(content.get("mechanics") or []),
            }
        )
    elif package["kind"] == "module":
        component_counts["scene"] = len(content.get("scene_atlas") or [])
        component_counts["ending"] = len(dict(content.get("narrative") or {}).get("endings") or [])
    metadata = dict(package.get("metadata") or {})
    manifest = dict(package["manifest"])
    return {
        "kind": package["kind"],
        "id": package["id"],
        "version": package["version"],
        "checksum": package["checksum"],
        "title": manifest.get("title") or metadata.get("title") or package["id"],
        "editions": content.get("editions") or manifest.get("editions") or [],
        "classification": content.get("classification") or manifest.get("classification"),
        "license": metadata.get("license"),
        "attribution": metadata.get("attribution"),
        "distribution": metadata.get("distribution"),
        "component_counts": component_counts,
        "image_count": sum(actor.get("image") is not None for actor in actors),
        "path": path,
        "download_path": download_path,
        "archive_checksum": archive_checksum,
        "archive_size": archive_size,
    }


def validate_public_package(package: Mapping[str, Any]) -> dict[str, Any]:
    """Refuse a package or asset without explicit redistribution rights."""

    value = validate_content_package(package)
    if value["system_id"] != "dnd5e" or value["kind"] not in PACKAGE_KINDS:
        raise ValueError("public D&D library received an incompatible package")
    metadata = dict(value.get("metadata") or {})
    distribution = str(metadata.get("distribution") or "").casefold()
    license_name = str(metadata.get("license") or "").strip()
    if distribution not in PUBLIC_DISTRIBUTIONS:
        raise ValueError(f"{value['id']} is not marked public/shareable")
    if license_name.casefold() in NON_PUBLIC_LICENSES:
        raise ValueError(f"{value['id']} has no redistributable license")
    if license_name not in PUBLIC_LICENSES:
        raise ValueError(f"{value['id']} uses an unverified public license identifier")
    license_evidence = metadata.get("license_evidence")
    if not isinstance(license_evidence, dict) or set(license_evidence) != {
        "type",
        "license_url",
        "source_url",
    }:
        raise ValueError(f"{value['id']} has no exact license evidence")
    if license_evidence["type"] not in {"open_license", "rights_holder_grant"}:
        raise ValueError(f"{value['id']} has unsupported license evidence")
    if not all(
        isinstance(license_evidence[field], str)
        and license_evidence[field].startswith("https://")
        for field in ("license_url", "source_url")
    ):
        raise ValueError(f"{value['id']} license evidence must use HTTPS URLs")
    for asset in value["assets"]:
        if asset["license"] != license_name:
            raise ValueError(
                f"{value['id']} asset {asset['asset_key']} uses a different license"
            )
        if str(asset["license"]).strip().casefold() in NON_PUBLIC_LICENSES:
            raise ValueError(
                f"{value['id']} asset {asset['asset_key']} has no redistributable license"
            )
        if not str(asset["attribution"]).strip():
            raise ValueError(f"{value['id']} asset {asset['asset_key']} has no attribution")
    return value


def build_content_library(
    output_dir: Path,
    packages: Iterable[Mapping[str, Any]],
    *,
    archives: Mapping[str, bytes],
    visibility: str = "private",
) -> dict[str, Any]:
    """Write descriptors, verified archives, browsable blobs, and one index."""

    if visibility not in {"private", "public"}:
        raise ValueError("library visibility must be private or public")

    output_dir.mkdir(parents=True, exist_ok=True)
    package_dir = output_dir / "packages"
    package_dir.mkdir(parents=True, exist_ok=True)
    blob_dir = output_dir / "blobs" / "sha256"
    blob_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    seen: set[tuple[str, str, str]] = set()
    expected_files: set[Path] = set()
    expected_blobs: set[Path] = set()
    archive_map = {str(checksum): bytes(content) for checksum, content in archives.items()}
    for raw in packages:
        package = (
            validate_public_package(raw)
            if visibility == "public"
            else validate_content_package(raw)
        )
        identity = (package["kind"], package["id"], package["version"])
        if identity in seen:
            raise ValueError(f"duplicate public package: {identity}")
        seen.add(identity)
        archive_content = archive_map.get(package["checksum"])
        if archive_content is None:
            raise ValueError(f"{package['id']} has no verified content archive")
        archived_package, archived_blobs = loads_content_archive(archive_content)
        if archived_package != package:
            raise ValueError(f"{package['id']} archive descriptor differs from index package")
        if not archived_blobs:
            raise ValueError(f"{package['id']} archive contains no source or asset blobs")
        for asset in package["assets"]:
            if asset["kind"] not in BROWSER_ASSET_KINDS:
                continue
            checksum = str(asset["checksum"])
            blob_path = blob_dir / checksum
            data = archived_blobs[checksum]
            if not blob_path.exists() or blob_path.read_bytes() != data:
                blob_path.write_bytes(data)
            expected_blobs.add(blob_path.resolve())
        stem = f"{package['id'].replace('/', '-')}-{package['version']}"
        descriptor_name = f"{stem}.json"
        archive_name = f"{stem}.sagasmith-pack"
        descriptor_path = package_dir / descriptor_name
        archive_path = package_dir / archive_name
        descriptor_path.write_text(canonical_json(package) + "\n", encoding="utf-8")
        archive_path.write_bytes(archive_content)
        expected_files.update({descriptor_path.resolve(), archive_path.resolve()})
        entries.append(
            _package_summary(
                package,
                f"packages/{descriptor_name}",
                f"packages/{archive_name}",
                archive_checksum=hashlib.sha256(archive_content).hexdigest(),
                archive_size=len(archive_content),
            )
        )
    for stale in package_dir.iterdir():
        if stale.is_file() and stale.resolve() not in expected_files:
            stale.unlink()
    for stale in blob_dir.iterdir():
        if stale.is_file() and stale.resolve() not in expected_blobs:
            stale.unlink()
    entries.sort(key=lambda item: (item["kind"], item["id"], item["version"]))
    index = {
        "schema": LIBRARY_SCHEMA,
        "visibility": visibility,
        "system_id": "dnd5e",
        "package_format": "sagasmith.content-package",
        "blob_base_path": "blobs/sha256",
        "browser_asset_kinds": sorted(BROWSER_ASSET_KINDS),
        "packages": entries,
    }
    (output_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return index


def build_public_library(
    output_dir: Path,
    packages: Iterable[Mapping[str, Any]],
    *,
    archives: Mapping[str, bytes],
) -> dict[str, Any]:
    """Build a public library after enforcing every package and asset license."""

    return build_content_library(
        output_dir,
        packages,
        archives=archives,
        visibility="public",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--package", action="append", type=Path, default=[])
    args = parser.parse_args()
    packages = []
    archives = {}
    for path in args.package:
        content = path.read_bytes()
        package, _blobs = loads_content_archive(content)
        packages.append(package)
        archives[package["checksum"]] = content
    index = build_public_library(args.output, packages, archives=archives)
    print(json.dumps(index, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
