"""Build static private or license-gated public unified-content catalogs."""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from sagasmith_core.content_pack import (
    PACKAGE_KINDS,
    validate_content_package,
)

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


# Compatibility only: new application code imports Runtime directly.
_RUNTIME_EXPORTS = (
    'build_content_library',
    'build_public_library',
    'main',
)

def __getattr__(name: str):
    if name not in _RUNTIME_EXPORTS:
        raise AttributeError(name)
    from sagasmith_dnd._runtime_compat import runtime_attribute

    return runtime_attribute('public_library', name)

if __name__ == "__main__":
    __getattr__("main")()
