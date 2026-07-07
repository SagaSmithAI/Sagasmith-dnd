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


def test_damage_apply_uses_resistance_before_hp_delta(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "damage.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Damage")
        actor = FoundryDocumentService(database).create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={
                "attributes": {"hp": {"value": 20, "max": 20}},
                "traits": {"dr": {"value": ["fire"]}},
            },
        )
    finally:
        database.dispose()

    result = _call(
        capsys,
        "damage",
        "apply",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
        "--amount",
        "9",
        "--damage-type",
        "fire",
    )

    assert result["damage"]["adjustment"] == "resistant"
    assert result["damage"]["applied_amount"] == 4
    assert result["actor"]["system"]["attributes"]["hp"]["value"] == 16
