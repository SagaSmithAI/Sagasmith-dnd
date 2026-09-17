"""Application file adapters extracted from Domain public_library."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from sagasmith_core.content_pack import (
    loads_content_archive,
    validate_content_package,
)
from sagasmith_core.integrity import canonical_json
from sagasmith_dnd.public_library import (
    BROWSER_ASSET_KINDS,
    LIBRARY_SCHEMA,
    _package_summary,
    validate_public_package,
)


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
