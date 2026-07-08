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


def test_long_rest_recovers_hp_death_saves_hit_dice_and_resources(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "long-rest-actor.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Long Rest Actor")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={
                "level": 5,
                "attributes": {
                    "hp": {"value": 2, "max": 30, "temp": 4, "tempmax": 3},
                    "hd": {"spent": 4, "max": 5, "die": "d8"},
                    "death": {"successes": 2, "failures": 1, "stable": True},
                },
                "resources": {
                    "second_wind": {"spent": 1, "max": 1, "sr": True},
                    "lay_on_hands": {"value": 0, "max": 15, "lr": True},
                },
            },
        )
    finally:
        database.dispose()

    result = _call(capsys, "rest", "long", "--campaign", campaign.id, "--actor", actor.id)
    recovered_types = {item["type"] for item in result["document_recovery"]["recovered"]}

    assert {"hit_points", "hit_dice_recovered", "death_saves_reset", "resource"} <= recovered_types

    database = Database(url)
    database.upgrade_schema()
    try:
        system = FoundryDocumentService(database).get_actor(actor.id).system
        assert system["attributes"]["hp"]["value"] == 30
        assert system["attributes"]["hp"]["temp"] == 0
        assert system["attributes"]["hd"]["spent"] == 2
        assert system["attributes"]["death"]["successes"] == 0
        assert system["resources"]["second_wind"]["spent"] == 0
        assert system["resources"]["lay_on_hands"]["value"] == 15
    finally:
        database.dispose()


def test_short_rest_can_spend_hit_dice_to_heal(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "short-rest-hd.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Short Rest HD")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={
                "level": 3,
                "abilities": {"con": {"value": 14}},
                "attributes": {"hp": {"value": 5, "max": 20}, "hd": {"spent": 0, "max": 3, "die": "d8"}},
            },
        )
    finally:
        database.dispose()

    result = _call(
        capsys,
        "rest",
        "short",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
        "--payload",
        '{"hit_dice":1}',
    )

    spent = [item for item in result["document_recovery"]["recovered"] if item["type"] == "hit_dice_spent"][0]
    assert spent["spent"] == 1
    assert spent["healed"] >= 3

    database = Database(url)
    database.upgrade_schema()
    try:
        system = FoundryDocumentService(database).get_actor(actor.id).system
        assert system["attributes"]["hd"]["spent"] == 1
        assert system["attributes"]["hp"]["value"] > 5
    finally:
        database.dispose()
