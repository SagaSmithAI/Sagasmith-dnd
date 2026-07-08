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


def test_duration_unit_aliases_match_declared_periods(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "duration-alias.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Duration Alias")
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
            name="Bless",
            duration={"unit": "minute", "value": 2},
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
    assert first["period"]["advanced"][0]["id"] == effect.id
    assert first["period"]["advanced"][0]["duration"]["remaining"] == 1


def test_until_turn_start_duration_expires_only_for_anchor_actor(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "duration-anchor.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Duration Anchor")
        documents = FoundryDocumentService(database)
        mira = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
        )
        goblin = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="npc",
            name="Goblin",
        )
        shield = documents.create_effect(
            campaign_id=campaign.id,
            parent_type="actor",
            parent_id=mira.id,
            actor_id=mira.id,
            name="Shield",
            duration={"period": "until_turn_start", "anchor": "self"},
        )
        other = documents.create_effect(
            campaign_id=campaign.id,
            parent_type="actor",
            parent_id=goblin.id,
            actor_id=goblin.id,
            name="Other Shield",
            duration={"period": "until_turn_start", "anchor": "self"},
        )
    finally:
        database.dispose()

    goblin_turn = _call(
        capsys,
        "time",
        "advance",
        "--campaign",
        campaign.id,
        "--period",
        "turn_start",
        "--actor",
        goblin.id,
    )
    assert [item["id"] for item in goblin_turn["period"]["expired"]] == [other.id]

    mira_turn = _call(
        capsys,
        "time",
        "advance",
        "--campaign",
        campaign.id,
        "--period",
        "turn_start",
        "--actor",
        mira.id,
    )
    assert [item["id"] for item in mira_turn["period"]["expired"]] == [shield.id]
