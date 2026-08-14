from pathlib import Path

from sagasmith_core import (
    CampaignService,
    CharacterService,
    Database,
    ModuleService,
    RuleService,
)
from sagasmith_core.database import sqlite_database_url
from sagasmith_core.modules import MarkdownModuleParser

from sagasmith_dnd.module_profile import DndModuleProfile
from sagasmith_dnd.spatial import (
    BattleMapError,
    compile_battle_map,
    compile_battle_map_template,
    normalize_combat_grid_template,
    patch_battle_map,
    validate_position,
)
from sagasmith_dnd.system import DND5E, validate_character_sheet


def test_dnd_vertical_slice(tmp_path: Path) -> None:
    database = Database(sqlite_database_url(tmp_path / "dnd.db"))
    database.create_schema()
    campaign = CampaignService(database).create(system_id=DND5E.id, name="Keep")
    template = CharacterService(database).create(
        system_id=DND5E.id,
        name="Mira",
        sheet=validate_character_sheet(
            {
                "progression": {
                    "level": 1,
                    "classes": [{"name": "Fighter", "level": 1, "hit_die": 10}],
                },
                "combat": {"ac": {"base": 16}},
            }
        ),
    )
    character = CharacterService(database).instantiate(
        template.id,
        campaign_id=campaign.id,
    )
    RuleService(database).ingest(
        system_id=DND5E.id,
        source_key="rules",
        title="Rules",
        content="# Grapple\nMake an ability check.",
    )
    ModuleService(database).ingest(
        campaign_id=campaign.id,
        source_key="keep",
        title="Keep",
        content="# Arrival\n## Gate\nTwo guards wait here.",
    )

    assert character.sheet["combat"]["ac"]["base"] == 16
    assert RuleService(database).search(system_id=DND5E.id, query="grapple")
    assert ModuleService(database).search(campaign_id=campaign.id, query="guards")


def test_dnd_module_spatial_manifest_and_temporary_map() -> None:
    parsed = MarkdownModuleParser(profile=DndModuleProfile()).parse(
        "# Keep\n## Guard Room\nA 30 by 20 foot chamber.\n#### A1. Cellar\nA locked stair descends."
    )
    scene = next(item for item in parsed[0].scenes if item.title == "Guard Room")
    spatial = scene.metadata["spatial"]
    assert spatial["locations"][0]["key"] == "a1-cellar"

    battle_map = compile_battle_map(
        {"scene_id": "scene-1", "spatial": spatial},
        {
            "location_key": "a1-cellar",
            "width_cells": 8,
            "height_cells": 6,
            "blocked_cells": [{"x": 2, "y": 1}],
        },
    )
    assert battle_map["lifecycle"] == "temporary"
    assert battle_map["grid"] == {"kind": "square", "cell_ft": 5}
    assert battle_map["map_revision"] == 1
    assert battle_map["source"]["location_key"] == "a1-cellar"
    assert battle_map["source"]["encounter_scene_id"] == "scene-1"
    assert battle_map["dm_overrides"] is True
    validate_position(battle_map, {"x": 1, "y": 1})
    try:
        validate_position(battle_map, {"x": 2, "y": 1})
    except BattleMapError as exc:
        assert "blocked" in str(exc)
    else:
        raise AssertionError("blocked map cells must reject a token position")

    linked_map = compile_battle_map(
        {
            "scene_id": "spatial-scene",
            "encounter_scene_id": "ambush-scene",
            "spatial": spatial,
        },
        {"location_key": "a1-cellar"},
    )
    assert linked_map["source"]["scene_id"] == "spatial-scene"
    assert linked_map["source"]["encounter_scene_id"] == "ambush-scene"
    assert linked_map["bounds"] == {"width_cells": 12, "height_cells": 12}
    assert linked_map["dm_overrides"] is False

    ambiguous_map = compile_battle_map(
        {
            "scene_id": "stronghold-scene",
            "spatial": {
                "schema_version": 1,
                "locations": [
                    {"key": "island-chamber"},
                    {"key": "cultist-common-room"},
                ],
            },
        }
    )
    assert ambiguous_map["source"]["location_key"] is None
    assert ambiguous_map["bounds"] == {"width_cells": 12, "height_cells": 12}
    assert ambiguous_map["dm_overrides"] is False

    updated = patch_battle_map(battle_map, [{"key": "gate.open", "value": True}])
    assert updated["map_revision"] == 2
    assert updated["checksum"] != battle_map["checksum"]
    assert battle_map["world_patches"] == []

    for invalid in ({"x": 1.5, "y": 1}, {"x": 1, "y": 1.5}):
        try:
            validate_position(battle_map, invalid)
        except BattleMapError as exc:
            assert "integer" in str(exc)
        else:
            raise AssertionError("fractional token positions must be rejected")


def test_dnd_temporary_map_rejects_non_five_foot_cells() -> None:
    try:
        compile_battle_map(
            {"scene_id": "scene-1", "spatial": {}},
            {"cell_ft": 10},
        )
    except BattleMapError as exc:
        assert "five-foot" in str(exc)
    else:
        raise AssertionError("the combat engine only resolves five-foot grid cells")


def test_combat_grid_template_is_canonical_and_compiles_to_a_fresh_map() -> None:
    template = {
        "schema_version": 1,
        "id": "gate-ambush",
        "title": "Gate ambush",
        "location_key": "a1-gate",
        "grid": {"kind": "square", "cell_ft": 5},
        "bounds": {"width_cells": 8, "height_cells": 6},
        "blocked_cells": [{"x": 4, "y": 2}],
        "difficult_cells": [{"x": 2, "y": 1}],
        "deployment_zones": [
            {"id": "party", "cells": [{"x": 1, "y": 5}, {"x": 0, "y": 5}]}
        ],
        "source_refs": [
            {"source_key": "keep", "chunk_key": "chunk-1", "page": 3, "note": "Map"}
        ],
    }
    normalized = normalize_combat_grid_template(template)
    assert normalized["deployment_zones"][0]["cells"] == [
        {"x": 0, "y": 5},
        {"x": 1, "y": 5},
    ]
    scene = {
        "scene_id": "scene-layout",
        "encounter_scene_id": "scene-ambush",
        "module_id": "module-1",
        "spatial": {"schema_version": 1, "locations": [{"key": "a1-gate"}]},
    }
    first = compile_battle_map_template(
        scene,
        normalized,
        authority_receipt={"kind": "content_pack_template"},
    )
    second = compile_battle_map_template(scene, normalized)
    assert first["id"] != second["id"]
    assert first["source"]["battle_map_template_id"] == "gate-ambush"
    assert first["blocked_cells"] == ["4,2"]
    assert first["deployment_zones"] == [{"id": "party", "cells": ["0,5", "1,5"]}]
    assert first["authority_receipt"] == {"kind": "content_pack_template"}


def test_combat_grid_template_rejects_mechanically_undefined_cells() -> None:
    template = {
        "schema_version": 1,
        "id": "bad-grid",
        "title": "Bad grid",
        "location_key": "room",
        "grid": {"kind": "square", "cell_ft": 5},
        "bounds": {"width_cells": 2, "height_cells": 2},
        "blocked_cells": [{"x": 1, "y": 1}],
        "difficult_cells": [{"x": 1, "y": 1}],
        "deployment_zones": [],
        "source_refs": [
            {"source_key": "book", "chunk_key": "chunk", "page": 1, "note": "Map"}
        ],
    }
    try:
        normalize_combat_grid_template(template)
    except BattleMapError as exc:
        assert "must not overlap" in str(exc)
    else:
        raise AssertionError("overlapping blocked and difficult terrain must fail")
