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


def test_advancement_apply_updates_actor_and_grants_item(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "advancement.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Advancement")
        actor = FoundryDocumentService(database).create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={"level": 1, "attributes": {"hp": {"value": 8, "max": 8}}},
        )
    finally:
        database.dispose()

    result = _call(
        capsys,
        "advancement",
        "apply",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
        "--payload",
        json.dumps(
            {
                "steps": [
                    {"type": "level", "value": 2},
                    {"type": "hit_points", "increase": 6},
                    {
                        "type": "scale_value",
                        "namespace": "fighter",
                        "key": "action_surge",
                        "value": 1,
                    },
                    {"type": "item_grant", "item_type": "feat", "name": "Action Surge"},
                ]
            }
        ),
    )

    assert result["actor"]["system"]["level"] == 2
    assert result["actor"]["system"]["attributes"]["hp"]["max"] == 14
    assert result["actor"]["system"]["scale"]["fighter"]["action_surge"] == 1
    assert result["granted_items"][0]["name"] == "Action Surge"
