from __future__ import annotations

import json
from pathlib import Path

from sagasmith_core import CampaignService, Database, FoundryDocumentService, MapService
from sagasmith_core.database import sqlite_database_url

from sagasmith_dnd.cli import main
from sagasmith_dnd.timeline import TimelineService


def _call(capsys, *args: str) -> dict:
    code = main([*args, "--json"])
    output = capsys.readouterr()
    value = json.loads(output.out)
    assert code == 0, value
    assert value["ok"] is True
    return value["data"]


def test_declared_time_ticks_effect_durations_for_each_crossed_minute(
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
        "declare",
        "--campaign",
        campaign.id,
        "--elapsed",
        "PT1M",
        "--reason",
        "testing",
        "--intent-id",
        "duration-first",
    )
    assert first["effects"]["advanced"][0]["remaining"] == 1

    second = _call(
        capsys,
        "time",
        "declare",
        "--campaign",
        campaign.id,
        "--elapsed",
        "PT1M",
        "--reason",
        "testing",
        "--intent-id",
        "duration-second",
    )
    assert second["effects"]["expired"][0]["id"] == effect.id


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
        "declare",
        "--campaign",
        campaign.id,
        "--elapsed",
        "PT1M",
        "--reason",
        "testing",
        "--intent-id",
        "duration-alias",
    )
    assert first["effects"]["advanced"][0]["id"] == effect.id
    assert first["effects"]["advanced"][0]["remaining"] == 1


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

    database = Database(url)
    database.upgrade_schema()
    try:
        timeline = TimelineService(database)
        goblin_turn = timeline.emit_period(
            campaign_id=campaign.id,
            period="turn_start",
            actor_id=goblin.id,
        )
        assert [item["id"] for item in goblin_turn["effects"]["expired"]] == [other.id]

        mira_turn = timeline.emit_period(
            campaign_id=campaign.id,
            period="turn_start",
            actor_id=mira.id,
        )
        assert [item["id"] for item in mira_turn["effects"]["expired"]] == [shield.id]
    finally:
        database.dispose()


def test_round_end_duration_period_is_supported(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "duration-round-end.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Round End")
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
            name="Round Aura",
            duration={"period": "round_end", "remaining": 1},
        )
    finally:
        database.dispose()

    database = Database(url)
    database.upgrade_schema()
    try:
        result = TimelineService(database).emit_period(
            campaign_id=campaign.id,
            period="round_end",
        )
    finally:
        database.dispose()

    assert [item["id"] for item in result["effects"]["expired"]] == [effect.id]


def test_declared_time_expires_regions_and_is_idempotent(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "timeline-region.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Timeline region")
        maps = MapService(database)
        scene = maps.create_scene(campaign.id, name="Chamber")
        region = maps.create_region(
            scene.id,
            name="Burning oil",
            shape={"type": "circle", "radius": 20},
            duration={"period": "declared_minute", "remaining": 2},
        )
    finally:
        database.dispose()

    first = _call(
        capsys,
        "time",
        "declare",
        "--campaign",
        campaign.id,
        "--elapsed",
        "PT2M",
        "--reason",
        "waiting",
        "--intent-id",
        "wait-001",
    )
    assert first["regions"]["expired"][0]["id"] == region.id

    retried = _call(
        capsys,
        "time",
        "declare",
        "--campaign",
        campaign.id,
        "--elapsed",
        "PT2M",
        "--reason",
        "waiting",
        "--intent-id",
        "wait-001",
    )
    assert retried["idempotent"] is True
    assert retried["clock"]["elapsed_seconds"] == 120
