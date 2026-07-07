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


def test_actor_prepare_builds_foundry_style_derived_data(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "derived.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Derived")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={
                "level": 5,
                "attributes": {"ac": {"value": 10}, "hp": {"value": 20, "max": 20}},
                "traits": {"dr": {"value": ["cold"]}},
            },
        )
        documents.create_item(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_id=actor.id,
            item_type="equipment",
            name="Chain Mail",
            system={
                "equipped": True,
                "armor": {"value": 16},
                "ac_bonus": 1,
                "traits": {"dr": {"value": ["fire"]}},
            },
            effects=[
                {
                    "_id": "defense",
                    "transfer": True,
                    "changes": [{"key": "system.attributes.ac.bonus", "mode": "ADD", "value": 1}],
                }
            ],
        )
        documents.create_effect(
            campaign_id=campaign.id,
            parent_type="actor",
            parent_id=actor.id,
            actor_id=actor.id,
            name="Shielded",
            changes=[{"key": "attributes.ac.bonus", "mode": "ADD", "value": 2}],
            statuses=["blessed"],
        )
    finally:
        database.dispose()

    prepared = _call(
        capsys,
        "actor",
        "prepare",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
    )

    effective = prepared["derived"]["effective_system"]
    assert effective["attributes"]["prof"] == 3
    assert effective["attributes"]["ac"]["value"] == 16
    assert effective["attributes"]["ac"]["bonus"] == 4
    assert effective["traits"]["dr"]["value"] == ["cold", "fire"]
    assert prepared["derived"]["items"]["transferred_effects"] == ["defense"]
    assert prepared["derived"]["statuses"] == ["blessed"]
    assert prepared["messages"][0]["message_type"] == "actor_prepare"

    shown = _call(capsys, "actor", "show", "--id", actor.id)
    assert shown["derived"]["effective_system"]["attributes"]["ac"]["bonus"] == 4
