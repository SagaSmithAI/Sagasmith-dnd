from __future__ import annotations

import json
from pathlib import Path

from sagasmith_core import CampaignService, Database, FoundryDocumentService, MapService
from sagasmith_core.database import sqlite_database_url

from sagasmith_dnd.cli import main


def _call(capsys, *args: str) -> dict:
    code = main([*args, "--json"])
    output = capsys.readouterr()
    value = json.loads(output.out)
    assert code == 0, value
    assert value["ok"] is True
    return value["data"]


def test_template_place_creates_scene_region_from_activity_target(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "templates.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Templates")
        maps = MapService(database)
        scene = maps.create_scene(
            campaign.id,
            name="Battlefield",
            grid_size=70,
            metadata={"grid_distance": 5},
        )
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
        )
        item = documents.create_item(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_id=actor.id,
            item_type="spell",
            name="Fireball",
        )
        activity = documents.create_activity(
            item_id=item.id,
            activity_type="cast",
            name="Cast Fireball",
            target={"template": {"type": "circle", "size": 30, "units": "ft"}},
        )
    finally:
        database.dispose()

    result = _call(
        capsys,
        "template",
        "place",
        "--scene",
        scene.id,
        "--actor",
        actor.id,
        "--item",
        item.id,
        "--activity",
        activity.id,
        "--x",
        "100",
        "--y",
        "120",
    )

    region = result["region"]
    assert region["behavior"] == "template"
    assert region["origin_activity_id"] == activity.id
    assert region["shape"]["type"] == "circle"
    assert region["shape"]["radius"] == 420
    assert region["metadata"]["item_id"] == item.id
