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


def test_damage_apply_uses_temporary_hp_before_hit_points(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "temp-hp.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Temp HP")
        actor = FoundryDocumentService(database).create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={"attributes": {"hp": {"value": 20, "max": 20, "temp": 5}}},
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
        "8",
        "--damage-type",
        "slashing",
    )

    assert result["damage"]["absorbed_temp"] == 5
    assert result["damage"]["hp_damage"] == 3
    assert result["actor"]["system"]["attributes"]["hp"]["temp"] == 0
    assert result["actor"]["system"]["attributes"]["hp"]["value"] == 17


def test_damage_apply_handles_immunity_and_vulnerability(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "damage-traits.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Damage Traits")
        actor = FoundryDocumentService(database).create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="npc",
            name="Dummy",
            system={
                "attributes": {"hp": {"value": 30, "max": 30}},
                "traits": {"di": {"value": ["poison"]}, "dv": {"value": ["radiant"]}},
            },
        )
    finally:
        database.dispose()

    immune = _call(
        capsys,
        "damage",
        "apply",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
        "--amount",
        "10",
        "--damage-type",
        "poison",
    )
    vulnerable = _call(
        capsys,
        "damage",
        "apply",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
        "--amount",
        "10",
        "--damage-type",
        "radiant",
    )

    assert immune["damage"]["adjustment"] == "immune"
    assert immune["damage"]["after_hp"] == 30
    assert vulnerable["damage"]["adjustment"] == "vulnerable"
    assert vulnerable["damage"]["applied_amount"] == 20
    assert vulnerable["damage"]["after_hp"] == 10


def test_damage_apply_requests_concentration_save_when_concentrating(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "concentration-damage.db")
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
            system={"attributes": {"hp": {"value": 20, "max": 20}}},
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
        "damage",
        "apply",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
        "--amount",
        "24",
        "--damage-type",
        "slashing",
    )

    pending = result["pending"][0]
    assert pending["type"] == "concentration_save_required"
    assert pending["dc"] == 12
    assert pending["effect_ids"] == [effect.id]
