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


def test_actor_document_skill_roll_uses_effective_system_and_expertise(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "rolls.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Rolls")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={
                "level": 5,
                "abilities": {"wis": {"value": 16}},
                "skills": {"perception": {"expertise": True}},
            },
        )
        documents.create_effect(
            campaign_id=campaign.id,
            parent_type="actor",
            parent_id=actor.id,
            actor_id=actor.id,
            name="Owl's Wisdom",
            changes=[{"key": "system.abilities.wis.value", "mode": 4, "value": 18}],
        )
    finally:
        database.dispose()

    _call(capsys, "actor", "prepare", "--campaign", campaign.id, "--actor", actor.id)

    result = _call(
        capsys,
        "roll",
        "skill",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
        "--skill",
        "perception",
        "--dc",
        "10",
    )

    roll = result["roll"]
    assert roll["ability"] == "wis"
    assert roll["ability_modifier"] == 4
    assert roll["proficiency_multiplier"] == 2
    assert roll["breakdown"]["proficiency_bonus"] == 6
    assert result["messages"][0]["message_type"] == "roll"


def test_poisoned_actor_has_disadvantage_on_skill_checks(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "roll-poisoned.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    rolls = iter([18, 2])
    monkeypatch.setattr("sagasmith_dnd.engine.random.randint", lambda _low, _high: next(rolls))
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Poisoned Roll")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={"abilities": {"wis": {"value": 16}}},
        )
        documents.create_effect(
            campaign_id=campaign.id,
            parent_type="actor",
            parent_id=actor.id,
            actor_id=actor.id,
            name="Poisoned",
            statuses=["poisoned"],
        )
    finally:
        database.dispose()

    result = _call(
        capsys,
        "roll",
        "skill",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
        "--skill",
        "perception",
        "--dc",
        "10",
    )

    roll = result["roll"]
    assert roll["disadvantage"] is True
    assert "actor:poisoned" in roll["disadvantage_sources"]
    assert roll["rolls"] == [18, 2]
    assert roll["natural"] == 2


def test_restrained_actor_has_disadvantage_on_dexterity_saves(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "roll-restrained.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    rolls = iter([17, 4])
    monkeypatch.setattr("sagasmith_dnd.engine.random.randint", lambda _low, _high: next(rolls))
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Restrained Roll")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={"abilities": {"dex": {"value": 16}}},
        )
        documents.create_effect(
            campaign_id=campaign.id,
            parent_type="actor",
            parent_id=actor.id,
            actor_id=actor.id,
            name="Restrained",
            statuses=["restrained"],
        )
    finally:
        database.dispose()

    result = _call(
        capsys,
        "roll",
        "save",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
        "--ability",
        "dex",
        "--dc",
        "10",
    )

    roll = result["roll"]
    assert roll["disadvantage"] is True
    assert "actor:restrained:dex_save" in roll["disadvantage_sources"]
    assert roll["rolls"] == [17, 4]
    assert roll["natural"] == 4
