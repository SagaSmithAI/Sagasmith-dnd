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


def test_advancement_grant_feature_uses_ruleset_activity_templates(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "feature-grant.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    monkeypatch.setattr("sagasmith_dnd.engine.random.randint", lambda _low, _high: 1)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Feature Grant")
        actor = FoundryDocumentService(database).create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={
                "class_levels": {"fighter": 2},
                "attributes": {"hp": {"value": 1, "max": 20}},
            },
        )
    finally:
        database.dispose()

    granted = _call(
        capsys,
        "advancement",
        "grant-feature",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
        "--feature",
        "second-wind",
    )

    assert granted["item"]["name"] == "Second Wind"
    activity = granted["activities"][0]
    assert activity["activation"] == {"type": "bonus"}
    assert activity["uses"]["recovery"] == ["short_rest"]
    assert activity["system"]["healing"] == "1d10 + @classes.fighter.levels"

    used = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
        "--item",
        granted["item"]["id"],
        "--activity",
        activity["id"],
        "--target-id",
        actor.id,
    )

    assert used["payment"] == "bonus_action"
    assert used["execution"]["after_hp"] == 4
    assert used["activity"]["uses"]["spent"] == 1


def test_advancement_grant_class_uses_compiled_progression_content(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "class-progression.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Class progression")
        actor = FoundryDocumentService(database).create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
        )
    finally:
        database.dispose()

    result = _call(
        capsys,
        "advancement",
        "grant-class",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
        "--class-id",
        "fighter",
        "--level",
        "2",
    )

    assert result["actor"]["system"]["class_levels"]["fighter"] == 2
    assert result["actor"]["system"]["abilities"]["str"]["proficient"] == 1
    assert result["class_item"]["source_key"] == "fighter"
    assert any(item["item"]["name"] == "Action Surge" for item in result["granted_features"])


def test_advancement_grant_feature_can_create_multiple_activities(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "feature-cunning.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Cunning Grant")
        actor = FoundryDocumentService(database).create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
        )
    finally:
        database.dispose()

    granted = _call(
        capsys,
        "advancement",
        "grant-feature",
        "--campaign",
        campaign.id,
        "--actor",
        actor.id,
        "--feature",
        "cunning-action",
    )

    assert granted["item"]["name"] == "Cunning Action"
    assert [activity["name"] for activity in granted["activities"]] == [
        "Cunning Action: Dash",
        "Cunning Action: Disengage",
        "Cunning Action: Hide",
    ]
    assert {activity["activation"]["type"] for activity in granted["activities"]} == {"bonus"}


def test_advancement_grant_spell_uses_ruleset_spell_templates(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "spell-grant.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    rolls = iter([10, 1, 1])
    monkeypatch.setattr("sagasmith_dnd.engine.random.randint", lambda _low, _high: next(rolls))
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Spell Grant")
        documents = FoundryDocumentService(database)
        caster = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={
                "level": 5,
                "attributes": {"prof": 3, "spellcasting": "int"},
                "abilities": {"int": {"value": 16}},
            },
        )
        target = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="npc",
            name="Dummy",
            system={"attributes": {"ac": {"value": 10}, "hp": {"value": 10, "max": 10}}},
        )
    finally:
        database.dispose()

    granted = _call(
        capsys,
        "advancement",
        "grant-spell",
        "--campaign",
        campaign.id,
        "--actor",
        caster.id,
        "--spell",
        "fire-bolt",
    )

    assert granted["item"]["name"] == "Fire Bolt"
    activity = granted["activities"][0]
    assert activity["activity_type"] == "attack"
    assert activity["range"] == {"value": 120, "type": "ranged"}

    used = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign.id,
        "--actor",
        caster.id,
        "--item",
        granted["item"]["id"],
        "--activity",
        activity["id"],
        "--target-id",
        target.id,
    )

    assert used["execution"]["hit"] is True
    assert used["execution"]["damage_roll"]["expression"] == "2d10"
    assert used["execution"]["damage"]["after_hp"] == 8


def test_actor_create_monster_uses_ruleset_monster_templates(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "monster-template.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    rolls = iter([10, 1])
    monkeypatch.setattr("sagasmith_dnd.engine.random.randint", lambda _low, _high: next(rolls))
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Monster Template")
        target = FoundryDocumentService(database).create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
            system={"attributes": {"ac": {"value": 10}, "hp": {"value": 10, "max": 10}}},
        )
    finally:
        database.dispose()

    created = _call(
        capsys,
        "actor",
        "create-monster",
        "--campaign",
        campaign.id,
        "--monster",
        "goblin",
    )

    assert created["actor"]["name"] == "Goblin"
    assert created["actor"]["system"]["attributes"]["ac"]["value"] == 15
    assert [item["name"] for item in created["items"]] == ["Scimitar", "Shortbow"]
    scimitar = created["items"][0]
    slash = scimitar["activities"][0]
    assert slash["range"] == {"value": 5, "type": "melee"}

    used = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign.id,
        "--actor",
        created["actor"]["id"],
        "--item",
        scimitar["id"],
        "--activity",
        slash["id"],
        "--target-id",
        target.id,
    )

    assert used["execution"]["hit"] is True
    assert used["execution"]["damage"]["after_hp"] == 7
