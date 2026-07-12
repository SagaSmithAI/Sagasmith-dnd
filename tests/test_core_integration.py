from pathlib import Path

from sagasmith_core import CampaignService, CharacterService, Database, ModuleService, RuleService
from sagasmith_core.database import sqlite_database_url

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
