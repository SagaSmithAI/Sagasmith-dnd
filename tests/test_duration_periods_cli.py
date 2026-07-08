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


def test_time_advance_period_ticks_document_effect_durations(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "duration.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Duration")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
        )
        effect = documents.create_effect(
            campaign_id=campaign.id,
            parent_type="actor",
            parent_id=actor.id,
            actor_id=actor.id,
            name="Guidance",
            duration={"period": "declared_minute", "remaining": 2},
        )
    finally:
        database.dispose()

    first = _call(
        capsys,
        "time",
        "advance",
        "--campaign",
        campaign.id,
        "--period",
        "declared_minute",
    )
    assert first["period"]["advanced"][0]["duration"]["remaining"] == 1

    second = _call(
        capsys,
        "time",
        "advance",
        "--campaign",
        campaign.id,
        "--period",
        "declared_minute",
    )
    assert second["period"]["expired"][0]["id"] == effect.id
