"""Deterministic temporary battle-map compilation from reviewed scene evidence."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime
from typing import Any
from uuid import uuid4

from sagasmith_core.integrity import json_sha256


class BattleMapError(ValueError):
    pass


_PORTABLE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_TEMPLATE_FIELDS = {
    "schema_version",
    "id",
    "title",
    "location_key",
    "grid",
    "bounds",
    "blocked_cells",
    "difficult_cells",
    "deployment_zones",
    "map_asset_key",
    "party_public_map_asset",
    "source_refs",
}

_PARTY_PUBLIC_ASSET_FIELDS = {
    "asset_key",
    "checksum",
    "media_type",
    "width",
    "height",
    "alt_text",
    "license",
    "attribution",
    "grid_alignment",
    "review",
}
_PARTY_PUBLIC_ALIGNMENT_FIELDS = {
    "mode",
    "x",
    "y",
    "width_cells",
    "height_cells",
}
_PARTY_PUBLIC_REVIEW_FIELDS = {
    "status",
    "audience",
    "reviewer",
    "reviewed_at",
    "note",
}
_PARTY_PUBLIC_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def normalize_combat_grid_template(
    value: Mapping[str, Any],
    *,
    source_ref_key: str = "chunk_key",
) -> dict[str, Any]:
    """Return the exact portable v1 D&D combat-grid template form."""

    if not isinstance(value, Mapping):
        raise BattleMapError("combat-grid template must be an object")
    unknown = sorted(set(value) - _TEMPLATE_FIELDS)
    missing = sorted(
        (_TEMPLATE_FIELDS - {"map_asset_key", "party_public_map_asset"}) - set(value)
    )
    if unknown or missing:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if unknown:
            details.append("unsupported " + ", ".join(unknown))
        raise BattleMapError("combat-grid template fields are invalid: " + "; ".join(details))
    if value.get("schema_version") != 1:
        raise BattleMapError("combat-grid template schema_version must be 1")
    template_id = _portable_id(value.get("id"), "combat-grid template id")
    title = _bounded_text(value.get("title"), "combat-grid template title", 300)
    location_key = _bounded_text(value.get("location_key"), "location_key", 300)
    grid = value.get("grid")
    if not isinstance(grid, Mapping) or set(grid) != {"kind", "cell_ft"}:
        raise BattleMapError("combat-grid template grid must contain exactly kind and cell_ft")
    if grid.get("kind") != "square" or grid.get("cell_ft") != 5:
        raise BattleMapError("D&D combat-grid templates require square five-foot cells")
    bounds = value.get("bounds")
    if not isinstance(bounds, Mapping) or set(bounds) != {"width_cells", "height_cells"}:
        raise BattleMapError(
            "combat-grid template bounds must contain exactly width_cells and height_cells"
        )
    width = _bounded_int(bounds.get("width_cells"), "bounds.width_cells", 1, 200)
    height = _bounded_int(bounds.get("height_cells"), "bounds.height_cells", 1, 200)
    blocked = _portable_cells(value.get("blocked_cells"), width, height, "blocked_cells")
    difficult = _portable_cells(
        value.get("difficult_cells"), width, height, "difficult_cells"
    )
    blocked_keys = {(item["x"], item["y"]) for item in blocked}
    difficult_keys = {(item["x"], item["y"]) for item in difficult}
    if blocked_keys & difficult_keys:
        raise BattleMapError("blocked_cells and difficult_cells must not overlap")
    raw_zones = value.get("deployment_zones")
    if not isinstance(raw_zones, list):
        raise BattleMapError("deployment_zones must be an array")
    zones = []
    zone_ids: set[str] = set()
    for index, raw_zone in enumerate(raw_zones):
        field = f"deployment_zones[{index}]"
        if not isinstance(raw_zone, Mapping) or set(raw_zone) != {"id", "cells"}:
            raise BattleMapError(f"{field} must contain exactly id and cells")
        zone_id = _portable_id(raw_zone.get("id"), f"{field}.id")
        if zone_id in zone_ids:
            raise BattleMapError(f"duplicate deployment zone id: {zone_id}")
        zone_ids.add(zone_id)
        zone_cells = _portable_cells(raw_zone.get("cells"), width, height, f"{field}.cells")
        if not zone_cells:
            raise BattleMapError(f"{field}.cells must not be empty")
        if any((item["x"], item["y"]) in blocked_keys for item in zone_cells):
            raise BattleMapError(f"{field}.cells must not contain blocked cells")
        zones.append({"id": zone_id, "cells": zone_cells})
    refs = _source_refs(value.get("source_refs"), source_ref_key=source_ref_key)
    result = {
        "schema_version": 1,
        "id": template_id,
        "title": title,
        "location_key": location_key,
        "grid": {"kind": "square", "cell_ft": 5},
        "bounds": {"width_cells": width, "height_cells": height},
        "blocked_cells": blocked,
        "difficult_cells": difficult,
        "deployment_zones": sorted(zones, key=lambda item: item["id"]),
        "source_refs": refs,
    }
    if "map_asset_key" in value:
        result["map_asset_key"] = _bounded_text(
            value.get("map_asset_key"), "map_asset_key", 300
        )
    if "party_public_map_asset" in value:
        result["party_public_map_asset"] = normalize_party_public_map_asset(
            value.get("party_public_map_asset"),
            width_cells=width,
            height_cells=height,
        )
    return result


def normalize_party_public_map_asset(
    value: Any,
    *,
    width_cells: int,
    height_cells: int,
) -> dict[str, Any]:
    """Validate one explicitly reviewed, portable party-public map asset reference."""

    if not isinstance(value, Mapping) or set(value) != _PARTY_PUBLIC_ASSET_FIELDS:
        raise BattleMapError(
            "party_public_map_asset must contain exactly asset_key, checksum, media_type, "
            "width, height, alt_text, license, attribution, grid_alignment, and review"
        )
    checksum = str(value.get("checksum") or "").casefold()
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise BattleMapError("party_public_map_asset.checksum must be a SHA-256 checksum")
    media_type = str(value.get("media_type") or "").strip().casefold()
    if media_type not in _PARTY_PUBLIC_IMAGE_TYPES:
        raise BattleMapError(
            "party_public_map_asset.media_type must be image/png, image/jpeg, or image/webp"
        )
    width = _bounded_int(value.get("width"), "party_public_map_asset.width", 1, 16384)
    height = _bounded_int(value.get("height"), "party_public_map_asset.height", 1, 16384)
    if width * height > 32 * 1024 * 1024:
        raise BattleMapError("party_public_map_asset exceeds the 32-megapixel safety limit")

    alignment = value.get("grid_alignment")
    if not isinstance(alignment, Mapping) or set(alignment) != _PARTY_PUBLIC_ALIGNMENT_FIELDS:
        raise BattleMapError(
            "party_public_map_asset.grid_alignment must contain exactly mode, x, y, "
            "width_cells, and height_cells"
        )
    if alignment.get("mode") != "contain":
        raise BattleMapError("party-public map artwork must use contain (letterbox) alignment")
    x = _bounded_int(alignment.get("x"), "grid_alignment.x", 0, width_cells - 1)
    y = _bounded_int(alignment.get("y"), "grid_alignment.y", 0, height_cells - 1)
    aligned_width = _bounded_int(
        alignment.get("width_cells"),
        "grid_alignment.width_cells",
        1,
        width_cells,
    )
    aligned_height = _bounded_int(
        alignment.get("height_cells"),
        "grid_alignment.height_cells",
        1,
        height_cells,
    )
    if x + aligned_width > width_cells or y + aligned_height > height_cells:
        raise BattleMapError("party_public_map_asset.grid_alignment exceeds map bounds")

    review = value.get("review")
    if not isinstance(review, Mapping) or set(review) != _PARTY_PUBLIC_REVIEW_FIELDS:
        raise BattleMapError(
            "party_public_map_asset.review must contain exactly status, audience, reviewer, "
            "reviewed_at, and note"
        )
    if review.get("status") != "approved" or review.get("audience") != "party_public":
        raise BattleMapError(
            "party_public_map_asset requires an approved party_public publication review"
        )
    reviewed_at = _bounded_text(review.get("reviewed_at"), "review.reviewed_at", 100)
    try:
        reviewed_datetime = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BattleMapError("review.reviewed_at must be an RFC 3339 timestamp") from exc
    if reviewed_datetime.tzinfo is None:
        raise BattleMapError("review.reviewed_at must include a timezone")
    normalized_review = {
        "status": "approved",
        "audience": "party_public",
        "reviewer": _bounded_text(review.get("reviewer"), "review.reviewer", 300),
        "reviewed_at": reviewed_at,
        "note": _bounded_text(review.get("note"), "review.note", 2000),
    }
    return {
        "asset_key": _bounded_text(value.get("asset_key"), "party_public_map_asset.asset_key", 300),
        "checksum": checksum,
        "media_type": media_type,
        "width": width,
        "height": height,
        "alt_text": _bounded_text(value.get("alt_text"), "party_public_map_asset.alt_text", 1000),
        "license": _bounded_text(value.get("license"), "party_public_map_asset.license", 500),
        "attribution": _bounded_text(
            value.get("attribution"), "party_public_map_asset.attribution", 2000
        ),
        "grid_alignment": {
            "mode": "contain",
            "x": x,
            "y": y,
            "width_cells": aligned_width,
            "height_cells": aligned_height,
        },
        "review": normalized_review,
    }


def normalize_combat_grid_templates(
    values: Sequence[Mapping[str, Any]],
    *,
    source_ref_key: str = "chunk_key",
) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        raise BattleMapError("combat_grid_templates must be an array")
    normalized = [
        normalize_combat_grid_template(value, source_ref_key=source_ref_key) for value in values
    ]
    ids = [item["id"] for item in normalized]
    if len(ids) != len(set(ids)):
        raise BattleMapError("combat_grid_templates contains duplicate template ids")
    return sorted(normalized, key=lambda item: item["id"])


def normalize_combat_grid_source_refs(
    values: Any,
    *,
    source_ref_key: str = "chunk_key",
) -> list[dict[str, Any]]:
    """Normalize evidence refs used by template authoring and removal receipts."""

    return _source_refs(values, source_ref_key=source_ref_key)


def compile_battle_map_template(
    scene: dict[str, Any],
    template: Mapping[str, Any],
    *,
    authority_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Copy one finalized Pack template into a fresh encounter-local battle map."""

    normalized = normalize_combat_grid_template(template)
    location_keys = {
        str(item.get("key"))
        for item in dict(scene.get("spatial") or {}).get("locations", [])
        if isinstance(item, dict) and item.get("key")
    }
    if normalized["location_key"] not in location_keys:
        raise BattleMapError("combat-grid template location_key is not in scene spatial evidence")
    width = normalized["bounds"]["width_cells"]
    height = normalized["bounds"]["height_cells"]
    value = {
        "id": f"battle-map-{uuid4().hex}",
        "schema_version": 1,
        "map_revision": 1,
        "lifecycle": "temporary",
        "source": {
            "scene_id": scene["scene_id"],
            "encounter_scene_id": scene.get("encounter_scene_id", scene["scene_id"]),
            "module_id": scene.get("module_id"),
            "location_key": normalized["location_key"],
            "scene_spatial_schema": dict(scene.get("spatial") or {}).get("schema_version", 1),
            "battle_map_template_id": normalized["id"],
        },
        "grid": deepcopy(normalized["grid"]),
        "bounds": deepcopy(normalized["bounds"]),
        "blocked_cells": _cells(normalized["blocked_cells"], width, height, "blocked_cells"),
        "difficult_cells": _cells(
            normalized["difficult_cells"], width, height, "difficult_cells"
        ),
        "deployment_zones": [
            {
                "id": zone["id"],
                "cells": _cells(zone["cells"], width, height, "deployment_zones.cells"),
            }
            for zone in normalized["deployment_zones"]
        ],
        "dm_overrides": False,
        "authority_receipt": deepcopy(dict(authority_receipt or {})),
        "world_patches": [],
    }
    if normalized.get("map_asset_key"):
        value["map_asset_key"] = normalized["map_asset_key"]
    if normalized.get("party_public_map_asset"):
        value["party_public_map_asset"] = deepcopy(normalized["party_public_map_asset"])
    value["checksum"] = _checksum(value)
    return value


def compile_battle_map(
    scene: dict[str, Any], request: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Create a frozen encounter-local map; never infer walls or line of sight."""
    request = deepcopy(request or {})
    spatial = dict(scene.get("spatial") or {})
    locations = [item for item in spatial.get("locations", []) if isinstance(item, dict)]
    requested_key = request.get("location_key")
    location = next((item for item in locations if item.get("key") == requested_key), None)
    if requested_key and location is None:
        raise BattleMapError("battle-map location_key is not in scene spatial evidence")
    if not requested_key and len(locations) == 1:
        location = locations[0]
    dimensions = dict((location or {}).get("dimensions_ft") or {})
    grid = dict(spatial.get("grid") or {"kind": "square", "cell_ft": 5})
    if str(grid.get("kind") or "square") != "square":
        raise BattleMapError("D&D temporary battle maps require a square grid")
    cell_ft = _bounded_int(
        request["cell_ft"] if "cell_ft" in request else grid.get("cell_ft", 5),
        "battle-map cell_ft",
        1,
        200,
    )
    if cell_ft != 5:
        raise BattleMapError("D&D combat resolution requires five-foot grid cells")
    width_ft = int(dimensions.get("width", 0) or 0)
    height_ft = int(dimensions.get("height", 0) or 0)
    width = _bounded_int(
        request["width_cells"]
        if "width_cells" in request
        else (max(6, width_ft // cell_ft) if width_ft else 12),
        "battle-map width_cells",
        1,
        200,
    )
    height = _bounded_int(
        request["height_cells"]
        if "height_cells" in request
        else (max(6, height_ft // cell_ft) if height_ft else 12),
        "battle-map height_cells",
        1,
        200,
    )
    blocked = _cells(request.get("blocked_cells") or [], width, height, "blocked_cells")
    difficult = _cells(request.get("difficult_cells") or [], width, height, "difficult_cells")
    vision = _compile_vision_layers(request, width, height)
    source = {
        "scene_id": scene["scene_id"],
        "encounter_scene_id": scene.get("encounter_scene_id", scene["scene_id"]),
        "module_id": scene.get("module_id"),
        "location_key": (location or {}).get("key"),
        "scene_spatial_schema": spatial.get("schema_version", 1),
    }
    value = {
        "id": f"battle-map-{uuid4().hex}",
        "schema_version": 1,
        "map_revision": 1,
        "lifecycle": "temporary",
        "source": source,
        "grid": {"kind": "square", "cell_ft": cell_ft},
        "bounds": {"width_cells": width, "height_cells": height},
        "blocked_cells": blocked,
        "difficult_cells": difficult,
        "dm_overrides": bool(set(request) - {"location_key"}),
        "world_patches": [],
        **vision,
    }
    value["checksum"] = _checksum(value)
    return value


def _compile_vision_layers(request: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    """Compile bounded, source-cited environmental facts used by 2014 vision rules."""
    result: dict[str, Any] = {}
    if "ambient_illumination" in request:
        level = request.get("ambient_illumination")
        if level not in {"bright", "dim", "dark"}:
            raise BattleMapError("ambient_illumination must be bright, dim, or dark")
        result["ambient_illumination"] = level
    raw_sources = request.get("light_sources")
    if raw_sources is not None:
        if not isinstance(raw_sources, list) or len(raw_sources) > 100:
            raise BattleMapError("light_sources must be a bounded array")
        sources = []
        seen_ids: set[str] = set()
        for item in raw_sources:
            fields = {
                "id", "position", "bright_radius_ft", "dim_radius_ft",
                "source_ref", "source_excerpt",
            }
            if not isinstance(item, dict) or set(item) != fields:
                raise BattleMapError(
                    "light_sources require exact identity, position, radii, and source fields"
                )
            identifier = str(item.get("id") or "").strip()
            position = item.get("position")
            if not identifier or len(identifier) > 128 or identifier in seen_ids:
                raise BattleMapError("light source IDs must be unique non-empty strings")
            if not isinstance(position, dict) or set(position) != {"x", "y"}:
                raise BattleMapError("light source position must contain exactly x and y")
            if any(type(position[key]) is not int for key in ("x", "y")):
                raise BattleMapError("light source positions must use integer grid cells")
            if _cells([position], width, height, "light_sources.position") == []:
                raise BattleMapError("light source position is required")
            bright = item.get("bright_radius_ft")
            dim = item.get("dim_radius_ft")
            for label, radius in (("bright", bright), ("dim", dim)):
                if (
                    isinstance(radius, bool) or not isinstance(radius, int)
                    or radius < 0 or radius > 1000 or radius % 5
                ):
                    raise BattleMapError(
                        f"light source {label}_radius_ft must be a five-foot increment"
                    )
            if bright > dim:
                raise BattleMapError("bright light radius cannot exceed dim light radius")
            source_ref = str(item.get("source_ref") or "").strip()
            source_excerpt = " ".join(str(item.get("source_excerpt") or "").split())
            if (
                not source_ref or len(source_ref) > 500
                or not source_excerpt or len(source_excerpt) > 1000
            ):
                raise BattleMapError("light sources require bounded source references and excerpts")
            seen_ids.add(identifier)
            sources.append({
                "id": identifier,
                "position": {"x": int(position["x"]), "y": int(position["y"])},
                "bright_radius_ft": bright,
                "dim_radius_ft": dim,
                "source_ref": source_ref,
                "source_excerpt": source_excerpt,
            })
        result["light_sources"] = sources
    raw_cells = request.get("vision_cells")
    if raw_cells is not None:
        if not isinstance(raw_cells, list) or len(raw_cells) > width * height:
            raise BattleMapError("vision_cells must be a bounded array of unique cells")
        cells = []
        seen_cells: set[str] = set()
        fields = {
            "x", "y", "illumination", "obscuration", "magical_darkness", "opaque",
            "source_ref", "source_excerpt",
        }
        for item in raw_cells:
            if not isinstance(item, dict) or set(item) != fields:
                raise BattleMapError(
                    "vision_cells require exact illumination, obscuration, and source fields"
                )
            if type(item.get("x")) is not int or type(item.get("y")) is not int:
                raise BattleMapError("vision cell positions must use integer grid cells")
            key = _cells([{"x": item.get("x"), "y": item.get("y")}], width, height,
                         "vision_cells")[0]
            if key in seen_cells:
                raise BattleMapError("vision_cells contains duplicate cells")
            illumination = item.get("illumination")
            obscuration = item.get("obscuration")
            if illumination not in {None, "bright", "dim", "dark"}:
                raise BattleMapError("vision cell illumination must be bright, dim, dark, or null")
            if obscuration not in {None, "lightly", "heavily"}:
                raise BattleMapError("vision cell obscuration must be lightly, heavily, or null")
            if (
                type(item.get("magical_darkness")) is not bool
                or type(item.get("opaque")) is not bool
            ):
                raise BattleMapError("vision cell magical_darkness and opaque must be booleans")
            source_ref = str(item.get("source_ref") or "").strip()
            source_excerpt = " ".join(str(item.get("source_excerpt") or "").split())
            if (
                not source_ref or len(source_ref) > 500
                or not source_excerpt or len(source_excerpt) > 1000
            ):
                raise BattleMapError("vision cells require bounded source references and excerpts")
            seen_cells.add(key)
            cells.append({
                "x": int(item["x"]), "y": int(item["y"]),
                "illumination": illumination, "obscuration": obscuration,
                "magical_darkness": item["magical_darkness"], "opaque": item["opaque"],
                "source_ref": source_ref, "source_excerpt": source_excerpt,
            })
        result["vision_cells"] = sorted(cells, key=lambda item: (item["y"], item["x"]))
    return result


def patch_battle_map(battle_map: dict[str, Any], patches: list[dict[str, Any]]) -> dict[str, Any]:
    """Append reviewed world patches and refresh the immutable map identity.

    Patches document scene-runtime changes. They do not create walls, cover,
    line of sight, terrain costs, or any other mechanic the combat engine has
    not explicitly implemented.
    """
    next_map = deepcopy(battle_map)
    normalized: list[dict[str, Any]] = []
    for patch in patches:
        if not isinstance(patch, dict):
            raise BattleMapError("each map patch must be an object")
        key = patch.get("key")
        if not isinstance(key, str) or not key.strip():
            raise BattleMapError("each map patch needs a non-empty string key")
        normalized.append({"key": key.strip(), "value": deepcopy(patch.get("value"))})
    next_map["world_patches"] = [
        *list(next_map.get("world_patches") or []),
        *normalized,
    ]
    next_map["map_revision"] = int(next_map.get("map_revision") or 1) + 1
    next_map["checksum"] = _checksum(next_map)
    return next_map


def validate_position(battle_map: dict[str, Any], position: dict[str, Any] | None) -> None:
    if position is None:
        return
    if not isinstance(position, dict):
        raise BattleMapError("battle-map positions must be objects")
    x, y = position.get("x"), position.get("y")
    if (
        isinstance(x, bool)
        or isinstance(y, bool)
        or not isinstance(x, int)
        or not isinstance(y, int)
    ):
        raise BattleMapError("battle-map positions need integer x and y cells")
    bounds = dict(battle_map.get("bounds") or {})
    if not (
        0 <= x < int(bounds.get("width_cells", 0)) and 0 <= y < int(bounds.get("height_cells", 0))
    ):
        raise BattleMapError("position is outside temporary battle-map bounds")
    key = _cell_key(x, y)
    if key in set(battle_map.get("blocked_cells") or []):
        raise BattleMapError("position is blocked on the temporary battle map")


def _cells(values: list[Any], width: int, height: int, field: str) -> list[str]:
    result: list[str] = []
    for value in values:
        if not isinstance(value, dict):
            raise BattleMapError(f"{field} entries must be objects")
        x, y = value.get("x"), value.get("y")
        if (
            not isinstance(x, int)
            or not isinstance(y, int)
            or not (0 <= x < width and 0 <= y < height)
        ):
            raise BattleMapError(f"{field} contains an out-of-bounds cell")
        key = _cell_key(x, y)
        if key not in result:
            result.append(key)
    return sorted(result)


def _portable_cells(values: Any, width: int, height: int, field: str) -> list[dict[str, int]]:
    if not isinstance(values, list):
        raise BattleMapError(f"{field} must be an array")
    result: dict[tuple[int, int], dict[str, int]] = {}
    for value in values:
        if not isinstance(value, Mapping) or set(value) != {"x", "y"}:
            raise BattleMapError(f"{field} entries must contain exactly integer x and y")
        x, y = value.get("x"), value.get("y")
        if (
            isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, int)
            or not isinstance(y, int)
            or not (0 <= x < width and 0 <= y < height)
        ):
            raise BattleMapError(f"{field} contains an out-of-bounds cell")
        if (x, y) in result:
            raise BattleMapError(f"{field} contains duplicate cells")
        result[(x, y)] = {"x": x, "y": y}
    return [result[key] for key in sorted(result, key=lambda item: (item[1], item[0]))]


def _source_refs(values: Any, *, source_ref_key: str) -> list[dict[str, Any]]:
    if source_ref_key not in {"chunk_key", "chunk_hash"}:
        raise ValueError("source_ref_key must be chunk_key or chunk_hash")
    if not isinstance(values, list) or not values:
        raise BattleMapError("combat-grid template source_refs must be a non-empty array")
    expected = {"source_key", source_ref_key, "page", "note"}
    result = []
    identities: set[tuple[str, str, int | None, str]] = set()
    for index, raw in enumerate(values):
        field = f"source_refs[{index}]"
        if not isinstance(raw, Mapping) or set(raw) != expected:
            raise BattleMapError(
                f"{field} must contain exactly source_key, {source_ref_key}, page, and note"
            )
        source_key = _bounded_text(raw.get("source_key"), f"{field}.source_key", 300)
        chunk_value = _bounded_text(raw.get(source_ref_key), f"{field}.{source_ref_key}", 300)
        page = raw.get("page")
        if page is not None and (
            isinstance(page, bool) or not isinstance(page, int) or page < 1
        ):
            raise BattleMapError(f"{field}.page must be null or a 1-based integer")
        note = _bounded_text(raw.get("note"), f"{field}.note", 1000)
        identity = (source_key, chunk_value, page, note)
        if identity in identities:
            raise BattleMapError("combat-grid template source_refs contains duplicates")
        identities.add(identity)
        result.append(
            {
                "source_key": source_key,
                source_ref_key: chunk_value,
                "page": page,
                "note": note,
            }
        )
    return sorted(
        result,
        key=lambda item: (
            item["source_key"],
            item[source_ref_key],
            item["page"] or 0,
            item["note"],
        ),
    )


def _bounded_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise BattleMapError(f"{field} must contain 1 to {maximum} characters")
    return value.strip()


def _portable_id(value: Any, field: str) -> str:
    text = _bounded_text(value, field, 128)
    if not _PORTABLE_ID.fullmatch(text):
        raise BattleMapError(f"{field} must be a lowercase portable id")
    return text


def _bounded_int(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise BattleMapError(f"{field} must be an integer between {minimum} and {maximum}")
    return value


def _cell_key(x: int | float, y: int | float) -> str:
    return f"{int(x)},{int(y)}"


def _checksum(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "checksum"}
    return json_sha256(payload)
