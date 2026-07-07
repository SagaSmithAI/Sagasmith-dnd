from __future__ import annotations

import json
from pathlib import Path

from sagasmith_core import CampaignService, Database, FoundryDocumentService
from sagasmith_core.database import sqlite_database_url

from sagasmith_dnd.cli import main


def _call(capsys, *args: str) -> dict:
    code = main([*args, "--json"])
    output = capsys.readouterr()
    value = json.loads(output.out)
    assert code == 0, value
    assert value["ok"] is True
    return value["data"]


def test_long_rest_recovers_document_spell_slots_and_activity_uses(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "rest.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Rest")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={"spells": {"spell1": {"value": 0, "max": 2}}},
        )
        item = documents.create_item(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_id=actor.id,
            item_type="feat",
            name="Second Wind",
        )
        activity = documents.create_activity(
            item_id=item.id,
            activity_type="heal",
            name="Second Wind",
            uses={"spent": 1, "max": 1, "recovery": [{"period": "shortRest"}]},
        )
    finally:
        database.dispose()

    result = _call(
        capsys,
        "rest",
        "long",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
    )

    recovered = result["document_recovery"]["recovered"]
    assert any(item["type"] == "spell_slots" for item in recovered)
    assert any(item.get("activity_id") == activity.id for item in recovered)

    database = Database(url)
    database.upgrade_schema()
    try:
        documents = FoundryDocumentService(database)
        assert documents.get_actor(actor.id).system["spells"]["spell1"]["value"] == 2
        assert documents.get_activity(activity.id).uses["spent"] == 0
    finally:
        database.dispose()
