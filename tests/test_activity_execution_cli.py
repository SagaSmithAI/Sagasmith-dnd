from __future__ import annotations

import json
import random
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


def test_attack_activity_rolls_hit_and_applies_damage(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "activity-attack.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Attack Activity")
        documents = FoundryDocumentService(database)
        attacker = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
        )
        target = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="npc",
            name="Goblin",
            system={"attributes": {"ac": {"value": 12}, "hp": {"value": 10, "max": 10}}},
        )
        item = documents.create_item(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_id=attacker.id,
            item_type="weapon",
            name="Longsword",
        )
        activity = documents.create_activity(
            item_id=item.id,
            activity_type="attack",
            name="Slash",
            activation={"type": "action"},
            system={"attack_bonus": 99, "damage": "1", "damage_type": "slashing"},
        )
    finally:
        database.dispose()

    random.seed(0)
    result = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign.id,
        "--actor",
        attacker.id,
        "--item",
        item.id,
        "--activity",
        activity.id,
        "--target-id",
        target.id,
    )

    assert result["execution"]["type"] == "attack"
    assert result["execution"]["hit"] is True
    assert result["execution"]["damage"]["after_hp"] == 9
    assert any(message["message_type"] == "damage" for message in result["messages"])


def test_heal_activity_updates_target_hp(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "activity-heal.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Heal Activity")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={"attributes": {"hp": {"value": 3, "max": 10}}},
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
            activation={"type": "bonus"},
            system={"healing": "4"},
        )
    finally:
        database.dispose()

    result = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
        "--item",
        item.id,
        "--activity",
        activity.id,
        "--target-id",
        actor.id,
    )

    assert result["execution"]["type"] == "heal"
    assert result["execution"]["after_hp"] == 7


def test_save_activity_rolls_save_and_applies_half_damage_on_success(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "activity-save.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Save Activity")
        documents = FoundryDocumentService(database)
        caster = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
        )
        target = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="npc",
            name="Goblin",
            system={
                "abilities": {"dex": {"value": 20, "save_proficient": True}},
                "attributes": {"hp": {"value": 20, "max": 20}},
                "level": 5,
            },
        )
        item = documents.create_item(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_id=caster.id,
            item_type="spell",
            name="Fireball",
        )
        activity = documents.create_activity(
            item_id=item.id,
            activity_type="save",
            name="Dexterity Save",
            activation={"type": "action"},
            system={"ability": "dex", "dc": 1, "damage": "10", "damage_type": "fire"},
        )
    finally:
        database.dispose()

    random.seed(0)
    result = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign.id,
        "--actor",
        caster.id,
        "--item",
        item.id,
        "--activity",
        activity.id,
        "--target-id",
        target.id,
    )

    assert result["execution"]["type"] == "save"
    assert result["execution"]["success"] is True
    assert result["execution"]["damage_roll"]["applied"] == 5
    assert result["execution"]["damage"]["after_hp"] == 15
