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


def test_cast_activity_consumes_spell_slot_and_starts_concentration(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "spells.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Spells")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={"spells": {"spell1": {"value": 2, "max": 2}}},
        )
        item = documents.create_item(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_id=actor.id,
            item_type="spell",
            name="Bless",
        )
        activity = documents.create_activity(
            item_id=item.id,
            activity_type="cast",
            name="Cast Bless",
            activation={"type": "action"},
            duration={"unit": "minute", "value": 1, "concentration": True},
            system={"level": 1, "concentration": True},
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
    )

    assert result["actor"]["system"]["spells"]["spell1"]["value"] == 1
    assert result["effects"][0]["statuses"] == ["concentrating"]
    assert result["state_delta"]["runtime"]["turn_budgets"][actor.id]["main_action"] == 0


def test_cantrip_cast_uses_character_level_scaling_without_spell_slot(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "cantrip.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    random.seed(1)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Cantrip")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={
                "level": 5,
                "abilities": {"cha": {"value": 16}},
                "attributes": {"prof": 3},
                "spells": {"spell1": {"value": 1, "max": 1}},
            },
        )
        target = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="npc",
            name="Ooze",
            system={"attributes": {"ac": {"value": 1}, "hp": {"value": 30, "max": 30}}},
        )
        item = documents.create_item(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_id=actor.id,
            item_type="spell",
            name="Fire Bolt",
            system={
                "ability": "cha",
                "level": 0,
                "attack": {"type": "spell"},
                "damage": {
                    "damage_type": {"index": "fire", "name": "Fire"},
                    "damage_at_character_level": {"1": "1d10", "5": "2d10", "11": "3d10"},
                },
            },
        )
        activity = documents.create_activity(
            item_id=item.id,
            activity_type="cast",
            name="Cast Fire Bolt",
            activation={"type": "action"},
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
        target.id,
    )

    assert result["execution"]["type"] == "attack"
    assert result["execution"]["attack_bonus"] == 6
    assert result["execution"]["damage"]["damage_type"] == "fire"
    assert result["execution"]["damage_roll"]["expression"] == "2d10"
    assert result["actor"]["system"]["spells"]["spell1"]["value"] == 1


def test_save_spell_uses_spellcasting_dc_from_actor(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "spell-dc.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    random.seed(2)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Save Spell")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={
                "abilities": {"int": {"value": 18}},
                "attributes": {"prof": 3, "spell": {"dc": 1}},
            },
        )
        target = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="npc",
            name="Ooze",
            system={"abilities": {"dex": {"value": 8}}, "attributes": {"hp": {"value": 30, "max": 30}}},
        )
        item = documents.create_item(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_id=actor.id,
            item_type="spell",
            name="Acid Splash",
            system={
                "ability": "int",
                "level": 0,
                "damage": {
                    "damage_type": {"index": "acid", "name": "Acid"},
                    "damage_at_character_level": {"1": "1d6", "5": "2d6"},
                },
                "dc": {"dc_type": {"index": "dex"}, "dc_success": "none"},
            },
        )
        activity = documents.create_activity(
            item_id=item.id,
            activity_type="cast",
            name="Cast Acid Splash",
            activation={"type": "action"},
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
        target.id,
    )

    assert result["execution"]["type"] == "save"
    assert result["execution"]["dc"] == 16
    assert result["execution"]["ability"] == "dex"


def test_upcast_spell_uses_cast_level_for_healing_and_slot_consumption(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "upcast.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Upcast")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={
                "attributes": {"hp": {"value": 3, "max": 30}},
                "spells": {"spell2": {"value": 1, "max": 1}, "spell3": {"value": 1, "max": 1}},
            },
        )
        item = documents.create_item(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_id=actor.id,
            item_type="spell",
            name="Aid",
            system={"level": 2, "heal_at_slot_level": {"2": "5", "3": "10"}},
        )
        activity = documents.create_activity(
            item_id=item.id,
            activity_type="cast",
            name="Cast Aid",
            activation={"type": "action"},
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
        "--payload",
        '{"spell_level":3}',
    )

    assert result["execution"]["type"] == "heal"
    assert result["execution"]["amount"] == 10
    assert result["actor"]["system"]["spells"]["spell2"]["value"] == 1
    assert result["actor"]["system"]["spells"]["spell3"]["value"] == 0


def test_ritual_cast_does_not_consume_spell_slot(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "ritual.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Ritual")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={"spells": {"spell1": {"value": 1, "max": 1}}},
        )
        item = documents.create_item(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_id=actor.id,
            item_type="spell",
            name="Alarm",
            system={"level": 1, "ritual": True},
        )
        activity = documents.create_activity(
            item_id=item.id,
            activity_type="cast",
            name="Cast Alarm",
            activation={"type": "action"},
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
        "--payload",
        '{"ritual":true}',
    )

    assert result["execution"] is None
    assert result["actor"]["system"]["spells"]["spell1"]["value"] == 1
