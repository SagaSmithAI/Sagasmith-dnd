from __future__ import annotations

from copy import deepcopy

import pytest

from sagasmith_dnd.spatial import (
    BattleMapError,
    compile_battle_map_template,
    normalize_combat_grid_template,
)


def _source_ref() -> dict[str, object]:
    return {
        "source_key": "keep",
        "chunk_key": "layout",
        "page": 1,
        "note": "Reviewed source layout.",
    }


def _public_asset() -> dict[str, object]:
    return {
        "asset_key": "gate-map",
        "checksum": "a" * 64,
        "media_type": "image/png",
        "width": 800,
        "height": 600,
        "alt_text": "A reviewed stone gatehouse floor plan.",
        "license": "private party display",
        "attribution": "User-supplied map, reviewed for this party.",
        "grid_alignment": {
            "mode": "contain",
            "x": 0,
            "y": 0,
            "width_cells": 6,
            "height_cells": 4,
        },
        "review": {
            "status": "approved",
            "audience": "party_public",
            "reviewer": "dm:keeper",
            "reviewed_at": "2026-08-28T00:00:00Z",
            "note": "Contains no hidden doors, traps, labels, or DM notes.",
        },
    }


def _template() -> dict[str, object]:
    return {
        "schema_version": 1,
        "id": "gate-ambush",
        "title": "Gate ambush",
        "location_key": "gate",
        "grid": {"kind": "square", "cell_ft": 5},
        "bounds": {"width_cells": 6, "height_cells": 4},
        "blocked_cells": [],
        "difficult_cells": [],
        "deployment_zones": [{"id": "party", "cells": [{"x": 0, "y": 3}]}],
        "map_asset_key": "dm-source-map",
        "party_public_map_asset": _public_asset(),
        "source_refs": [_source_ref()],
    }


def test_party_public_map_asset_is_distinct_and_copied_to_encounter() -> None:
    normalized = normalize_combat_grid_template(_template())
    compiled = compile_battle_map_template(
        {
            "scene_id": "scene-1",
            "spatial": {"locations": [{"key": "gate"}]},
        },
        normalized,
    )

    assert normalized["map_asset_key"] == "dm-source-map"
    assert normalized["party_public_map_asset"]["asset_key"] == "gate-map"
    assert compiled["map_asset_key"] == "dm-source-map"
    assert compiled["party_public_map_asset"] == normalized["party_public_map_asset"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda asset: asset["review"].update({"status": "pending"}),
            "approved party_public publication review",
        ),
        (
            lambda asset: asset.update({"license": ""}),
            "license must contain",
        ),
        (
            lambda asset: asset["grid_alignment"].update({"mode": "cover"}),
            r"contain \(letterbox\)",
        ),
        (
            lambda asset: asset["grid_alignment"].update({"x": 5, "width_cells": 2}),
            "exceeds map bounds",
        ),
    ],
)
def test_party_public_map_asset_requires_rights_review_and_bounded_alignment(
    mutation,
    message: str,
) -> None:
    value = deepcopy(_template())
    mutation(value["party_public_map_asset"])

    with pytest.raises(BattleMapError, match=message):
        normalize_combat_grid_template(value)


def test_private_map_asset_key_does_not_create_party_public_reference() -> None:
    value = _template()
    value.pop("party_public_map_asset")

    normalized = normalize_combat_grid_template(value)
    compiled = compile_battle_map_template(
        {
            "scene_id": "scene-1",
            "spatial": {"locations": [{"key": "gate"}]},
        },
        normalized,
    )

    assert compiled["map_asset_key"] == "dm-source-map"
    assert "party_public_map_asset" not in compiled
