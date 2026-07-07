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


def test_cast_activity_consumes_spell_slot_and_starts_concentration(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "spells.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Spells")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={"spells": {"spell1": {"value": 2, "max": 2}}},
        )
        item = documents.create_item(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_id=actor.id,
            item_type="spell",
            name="Bless",
        )
        activity = documents.create_activity(
            item_id=item.id,
            activity_type="cast",
            name="Cast Bless",
            activation={"type": "action"},
            duration={"unit": "minute", "value": 1, "concentration": True},
            system={"level": 1, "concentration": True},
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

    assert result["actor"]["system"]["spells"]["spell1"]["value"] == 1
    assert result["effects"][0]["statuses"] == ["concentrating"]
    assert result["state_delta"]["runtime"]["turn_budgets"][actor.id]["main_action"] == 0
