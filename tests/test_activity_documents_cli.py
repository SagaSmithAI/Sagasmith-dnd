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


def test_activity_use_executes_foundry_document_activity(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "dnd.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Activity Docs")
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
            item_type="feat",
            name="Second Wind",
        )
        activity = documents.create_activity(
            item_id=item.id,
            activity_type="heal",
            name="Second Wind",
            activation={"type": "bonus"},
            uses={"spent": 0, "max": 1, "recovery": [{"period": "shortRest"}]},
            effects=[
                {
                    "name": "Recovering",
                    "duration": {"period": "turn_end", "value": 1},
                    "statuses": ["recovering"],
                }
            ],
        )
    finally:
        database.dispose()

    result = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
        "--item",
        item.id,
        "--activity",
        activity.id,
    )

    assert result["type"] == "activity_result"
    assert result["payment"] == "bonus_action"
    assert result["activity"]["uses"]["spent"] == 1
    assert result["effects"][0]["statuses"] == ["recovering"]
    assert result["messages"][0]["message_type"] == "activity"
    assert result["state_delta"]["runtime"]["turn_budgets"][actor.id]["bonus_action"] == 0
