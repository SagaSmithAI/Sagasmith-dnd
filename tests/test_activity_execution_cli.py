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


def _call_error(capsys, *args: str) -> dict:
    code = main([*args, "--json"])
    output = capsys.readouterr()
    value = json.loads(output.out)
    assert code != 0, value
    assert value["ok"] is False
    return value["error"]


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


def test_attack_activity_doubles_damage_dice_on_critical(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "activity-critical.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    rolls = iter([20, 1, 1])
    monkeypatch.setattr("sagasmith_dnd.engine.random.randint", lambda _low, _high: next(rolls))
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Critical Activity")
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
            system={"attack_bonus": 0, "damage": "1d8+3", "damage_type": "slashing"},
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
        attacker.id,
        "--item",
        item.id,
        "--activity",
        activity.id,
        "--target-id",
        target.id,
    )

    assert result["execution"]["roll"]["critical"] is True
    assert result["execution"]["damage_roll"]["base_expression"] == "1d8+3"
    assert result["execution"]["damage_roll"]["expression"] == "2d8+3"
    assert result["execution"]["damage_roll"]["total"] == 5
    assert result["execution"]["damage"]["after_hp"] == 5


def test_attack_activity_applies_condition_advantage_and_disadvantage(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "activity-conditions.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    rolls = iter([19, 2])
    monkeypatch.setattr("sagasmith_dnd.engine.random.randint", lambda _low, _high: next(rolls))
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Condition Activity")
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
        documents.create_effect(
            campaign_id=campaign.id,
            parent_type="actor",
            parent_id=attacker.id,
            actor_id=attacker.id,
            name="Poisoned",
            statuses=["poisoned"],
        )
        documents.create_effect(
            campaign_id=campaign.id,
            parent_type="actor",
            parent_id=target.id,
            actor_id=target.id,
            name="Prone",
            statuses=["prone"],
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
            range={"value": 5},
            system={"attack_bonus": 99},
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
        attacker.id,
        "--item",
        item.id,
        "--activity",
        activity.id,
        "--target-id",
        target.id,
        "--payload",
        '{"range_context":{"distance":5}}',
    )

    execution = result["execution"]
    assert execution["advantage"] is True
    assert execution["disadvantage"] is True
    assert "target:prone:within_5_ft" in execution["advantage_sources"]
    assert "attacker:poisoned" in execution["disadvantage_sources"]
    assert execution["roll"]["rolls"] == [19]


def test_incapacitated_actor_cannot_use_action_activity(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "activity-incapacitated.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Incapacitated Activity")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
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
        )
        documents.create_effect(
            campaign_id=campaign.id,
            parent_type="actor",
            parent_id=actor.id,
            actor_id=actor.id,
            name="Stunned",
            statuses=["stunned"],
        )
        item = documents.create_item(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_id=actor.id,
            item_type="weapon",
            name="Longsword",
        )
        activity = documents.create_activity(
            item_id=item.id,
            activity_type="attack",
            name="Slash",
            activation={"type": "action"},
            system={"attack_bonus": 99},
        )
    finally:
        database.dispose()

    error = _call_error(
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
    assert "incapacitated" in error["message"]


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


def test_attack_activity_resolves_foundry_roll_data_formulas(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "activity-formula.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Formula Activity")
        documents = FoundryDocumentService(database)
        attacker = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={
                "attributes": {"prof": 3},
                "abilities": {"str": {"value": 16}},
            },
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
            name="Spear",
        )
        activity = documents.create_activity(
            item_id=item.id,
            activity_type="attack",
            name="Thrust",
            activation={"type": "action"},
            system={"ability": "str", "attack_bonus": "@prof + @mod", "damage": "1 + @mod"},
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

    assert result["execution"]["attack_bonus"] == 6
    assert result["execution"]["hit"] is True
    assert result["execution"]["damage_roll"]["expression"] == "4"
    assert result["execution"]["damage"]["after_hp"] == 6


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


def test_check_activity_can_resolve_contested_checks(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "activity-contest.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    rolls = iter([15, 6])
    monkeypatch.setattr("sagasmith_dnd.engine.random.randint", lambda _low, _high: next(rolls))
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Contest Activity")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={"abilities": {"str": {"value": 16}}},
        )
        target = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="npc",
            name="Goblin",
            system={"abilities": {"str": {"value": 10}}},
        )
        item = documents.create_item(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_id=actor.id,
            item_type="feat",
            name="Grapple",
        )
        activity = documents.create_activity(
            item_id=item.id,
            activity_type="check",
            name="Grapple",
            activation={"type": "action"},
            system={"ability": "str", "contest": {"ability": "str"}},
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

    assert result["execution"]["type"] == "check"
    assert result["execution"]["actor"]["total"] == 18
    assert result["execution"]["target"]["total"] == 6
    assert result["execution"]["success"] is True


def test_damage_activity_uses_foundry_damage_parts(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "activity-damage-parts.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Damage Parts")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(campaign_id=campaign.id, system_id="dnd5e", name="Mira")
        target = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="npc",
            name="Ooze",
            system={"attributes": {"hp": {"value": 20, "max": 20}}},
        )
        item = documents.create_item(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_id=actor.id,
            item_type="spell",
            name="Flame",
            system={"level": 3},
        )
        activity = documents.create_activity(
            item_id=item.id,
            activity_type="damage",
            name="Burn",
            system={
                "damage": {
                    "parts": [
                        {
                            "number": 2,
                            "denomination": 4,
                            "bonus": "@item.level",
                            "types": ["fire"],
                        }
                    ]
                }
            },
        )
    finally:
        database.dispose()

    random.seed(1)
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

    assert result["execution"]["type"] == "damage"
    assert result["execution"]["damage_type"] == "fire"
    assert result["execution"]["roll"]["parts"] == [{"formula": "2d4+@item.level", "types": ["fire"]}]
    assert result["execution"]["roll"]["expression"] == "2d4+3"


def test_activity_effects_can_be_gated_on_attack_hit(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "activity-hit-effect.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    rolls = iter([2, 2])
    monkeypatch.setattr("sagasmith_dnd.engine.random.randint", lambda _low, _high: next(rolls))
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Hit Effects")
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
            system={"attributes": {"ac": {"value": 30}, "hp": {"value": 10, "max": 10}}},
        )
        item = documents.create_item(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_id=attacker.id,
            item_type="weapon",
            name="Net",
        )
        activity = documents.create_activity(
            item_id=item.id,
            activity_type="attack",
            name="Snare",
            activation={"type": "action"},
            effects=[
                {
                    "name": "Restrained",
                    "apply_on": "hit",
                    "statuses": ["restrained"],
                }
            ],
        )
    finally:
        database.dispose()

    miss = _call(
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
        "--payload",
        '{"attack_bonus":0}',
    )
    assert miss["execution"]["hit"] is False
    assert miss["effects"] == []

    hit = _call(
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
        "--payment",
        "free",
        "--payload",
        '{"attack_bonus":99}',
    )
    assert hit["execution"]["hit"] is True
    assert hit["effects"][0]["statuses"] == ["restrained"]


def test_heal_activity_uses_foundry_healing_formula(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "activity-healing-parts.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Healing Parts")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={"attributes": {"hp": {"value": 3, "max": 20}}},
        )
        item = documents.create_item(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_id=actor.id,
            item_type="spell",
            name="Aid",
            system={"level": 4},
        )
        activity = documents.create_activity(
            item_id=item.id,
            activity_type="heal",
            name="Aid",
            system={
                "healing": {
                    "custom": {"enabled": True, "formula": "5"},
                    "scaling": {"formula": "(@item.level - 2) * 5"},
                }
            },
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
    assert result["execution"]["amount"] == 15
    assert result["execution"]["after_hp"] == 18
