"""Build a static, license-gated catalog of portable D&D content packages."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from sagasmith_core.portable import (
    canonical_json,
    loads_module_archive,
    validate_addon_pack,
    validate_module_pack,
    validate_preset_pack,
)

from sagasmith_dnd.portable_cards import (
    build_srd2014_preset_pack,
    build_srd2024_preset_pack,
)

LIBRARY_SCHEMA = "sagasmith.public-content-library.v2"
PUBLIC_DISTRIBUTIONS = frozenset({"public", "shareable"})
NON_PUBLIC_LICENSES = frozenset({"", "private", "proprietary", "user-supplied"})


def _package_summary(
    package: Mapping[str, Any], path: str, download_path: str | None = None
) -> dict[str, Any]:
    payload = dict(package["payload"])
    if package["kind"] == "preset_pack":
        cards = list(payload["cards"])
        counts: dict[str, int] = {}
        image_count = 0
        for card in cards:
            actor_type = str(card["payload"]["actor_type"])
            counts[actor_type] = counts.get(actor_type, 0) + 1
            image_count += int(card["payload"].get("image") is not None)
        component_counts = {"actor_card": len(cards), **counts}
    elif package["kind"] == "addon_pack":
        components = list(payload["components"])
        component_counts = {}
        image_count = 0
        for component in components:
            kind = str(component["kind"])
            component_counts[kind] = component_counts.get(kind, 0) + 1
            if kind == "preset_pack":
                for card in component["payload"]["cards"]:
                    image_count += int(card["payload"].get("image") is not None)
    else:
        actors = list(payload["actors"])
        component_counts = {
            "scene": len(payload["scene_atlas"]),
            "asset": len(payload["assets"]),
            "actor_card": len(actors),
        }
        image_count = sum(
            card["payload"].get("image") is not None for card in actors
        )
    metadata = dict(package.get("metadata") or {})
    result = {
        "kind": package["kind"],
        "id": package["id"],
        "version": package["version"],
        "checksum": package["checksum"],
        "title": metadata.get("title") or package["id"],
        "edition": metadata.get("edition"),
        "license": metadata.get("license"),
        "attribution": metadata.get("attribution"),
        "component_counts": component_counts,
        "image_count": image_count,
        "path": path,
    }
    if package["kind"] == "module_pack":
        manifest = payload["manifest"]
        result.update(
            {
                "classification": manifest["classification"],
                "play_profile": manifest["play_profile"],
                "continuity": manifest["continuity"],
                "readiness": payload["readiness"],
                "download_path": download_path,
            }
        )
    return result


def validate_public_package(package: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a package and refuse content without explicit redistribution rights."""

    kind = package.get("kind")
    if kind == "preset_pack":
        value = validate_preset_pack(package, expected_system_id="dnd5e")
    elif kind == "addon_pack":
        value = validate_addon_pack(package, expected_system_id="dnd5e")
    elif kind == "module_pack":
        value = validate_module_pack(package, expected_system_id="dnd5e")
    else:
        raise ValueError(
            "public library accepts only preset_pack, addon_pack, and module_pack"
        )
    metadata = dict(value.get("metadata") or {})
    distribution = str(metadata.get("distribution") or "").casefold()
    license_name = str(metadata.get("license") or "").strip()
    if distribution not in PUBLIC_DISTRIBUTIONS:
        raise ValueError(f"{value['id']} is not marked public/shareable")
    if license_name.casefold() in NON_PUBLIC_LICENSES:
        raise ValueError(f"{value['id']} has no redistributable license")
    components = (
        list(value["payload"]["components"])
        if value["kind"] == "addon_pack"
        else [value]
    )
    for component in components:
        component_metadata = dict(component.get("metadata") or {})
        component_license = str(component_metadata.get("license") or license_name).strip()
        if component_license.casefold() in NON_PUBLIC_LICENSES:
            raise ValueError(f"{component['id']} has no redistributable license")
        cards = list(component["payload"].get("cards") or [])
        if component["kind"] == "module_pack":
            cards = list(component["payload"].get("actors") or [])
        for card in cards:
            card_metadata = dict(card.get("metadata") or {})
            card_distribution = str(card_metadata.get("distribution") or distribution).casefold()
            card_license = str(card_metadata.get("license") or component_license).strip()
            if card_distribution not in PUBLIC_DISTRIBUTIONS:
                raise ValueError(f"{card['id']} is not marked public/shareable")
            if card_license.casefold() in NON_PUBLIC_LICENSES:
                raise ValueError(f"{card['id']} has no redistributable license")
            image = card["payload"].get("image")
            if image and str(image["license"]).strip().casefold() in NON_PUBLIC_LICENSES:
                raise ValueError(f"{card['id']} image has no redistributable license")
    return value


def build_public_library(
    output_dir: Path,
    packages: Iterable[Mapping[str, Any]],
    *,
    module_archives: Mapping[str, bytes] | None = None,
) -> dict[str, Any]:
    """Write validated packages and a compact index for static web clients."""

    output_dir.mkdir(parents=True, exist_ok=True)
    package_dir = output_dir / "packages"
    package_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    seen: set[tuple[str, str, str]] = set()
    for raw in packages:
        package = validate_public_package(raw)
        identity = (package["kind"], package["id"], package["version"])
        if identity in seen:
            raise ValueError(f"duplicate public package: {identity}")
        seen.add(identity)
        stem = f"{package['id'].replace('/', '-')}-{package['version']}"
        filename = f"{stem}.json"
        relative_path = f"packages/{filename}"
        (package_dir / filename).write_text(
            canonical_json(package) + "\n", encoding="utf-8"
        )
        download_path = None
        if package["kind"] == "module_pack":
            archive_content = dict(module_archives or {}).get(package["checksum"])
            if archive_content is None:
                raise ValueError(f"{package['id']} has no verified module archive")
            archived_package, _blobs = loads_module_archive(archive_content)
            if archived_package != package:
                raise ValueError(f"{package['id']} archive descriptor differs from index package")
            archive_name = f"{stem}.sagasmith-module"
            (package_dir / archive_name).write_bytes(archive_content)
            download_path = f"packages/{archive_name}"
        entries.append(_package_summary(package, relative_path, download_path))
    entries.sort(key=lambda item: (item["kind"], item["id"], item["version"]))
    index = {
        "schema": LIBRARY_SCHEMA,
        "system_id": "dnd5e",
        "packages": entries,
    }
    (output_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return index


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--addon", action="append", type=Path, default=[])
    parser.add_argument("--module", action="append", type=Path, default=[])
    args = parser.parse_args()
    packages: list[dict[str, Any]] = [
        build_srd2014_preset_pack(args.skill_root),
        build_srd2024_preset_pack(args.skill_root),
    ]
    module_archives: dict[str, bytes] = {}
    for path in args.addon:
        packages.append(json.loads(path.read_text(encoding="utf-8")))
    for path in args.module:
        content = path.read_bytes()
        package, _blobs = loads_module_archive(content)
        packages.append(package)
        module_archives[package["checksum"]] = content
    index = build_public_library(
        args.output, packages, module_archives=module_archives
    )
    print(json.dumps(index, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
