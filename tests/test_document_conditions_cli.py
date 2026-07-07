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


def test_document_condition_add_recalculate_and_remove(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "conditions.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Conditions")
        actor = FoundryDocumentService(database).create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
        )
    finally:
        database.dispose()

    added = _call(
        capsys,
        "condition",
        "add",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
        "--condition",
        "unconscious",
    )
    assert added["statuses"] == ["prone", "unconscious"]

    recalculated = _call(
        capsys,
        "effect",
        "recalculate",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
    )
    assert recalculated["statuses"] == ["prone", "unconscious"]

    removed = _call(
        capsys,
        "condition",
        "remove",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
        "--condition",
        "unconscious",
    )
    assert removed["removed"][0]["name"] == "unconscious"
