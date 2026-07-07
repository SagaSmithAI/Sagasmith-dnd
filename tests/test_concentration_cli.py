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


def test_concentration_fail_removes_concentrating_effect(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "concentration.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Concentration")
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
            name="Concentrating: Bless",
            statuses=["concentrating"],
        )
    finally:
        database.dispose()

    result = _call(
        capsys,
        "concentration",
        "fail",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
    )

    assert result["removed"][0]["id"] == effect.id
    database = Database(url)
    database.upgrade_schema()
    try:
        assert FoundryDocumentService(database).list_effects(campaign.id, actor_id=actor.id) == []
    finally:
        database.dispose()
