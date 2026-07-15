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
from sagasmith_dnd.spatial import BattleMapError, compile_battle_map, validate_position
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
    assert battle_map["source"]["location_key"] == "a1-cellar"
    validate_position(battle_map, {"x": 1, "y": 1})
    try:
        validate_position(battle_map, {"x": 2, "y": 1})
    except BattleMapError as exc:
        assert "blocked" in str(exc)
    else:
        raise AssertionError("blocked map cells must reject a token position")
