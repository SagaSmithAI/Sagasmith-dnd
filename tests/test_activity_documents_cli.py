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


def test_activity_use_executes_foundry_document_activity(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "dnd.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Activity Docs")
        documents = FoundryDocumentService(database)
        actor = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
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
            uses={"spent": 0, "max": 1, "recovery": [{"period": "shortRest"}]},
            effects=[
                {
                    "name": "Recovering",
                    "duration": {"period": "turn_end", "value": 1},
                    "statuses": ["recovering"],
                }
            ],
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

    assert result["type"] == "activity_result"
    assert result["payment"] == "bonus_action"
    assert result["activity"]["uses"]["spent"] == 1
    assert result["effects"][0]["statuses"] == ["recovering"]
    assert result["messages"][0]["message_type"] == "activity"
    assert result["state_delta"]["runtime"]["turn_budgets"][actor.id]["bonus_action"] == 0


def test_attack_activity_creates_resolvable_reaction_window(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "dnd.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Reactions")
        documents = FoundryDocumentService(database)
        attacker = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
        )
        defender = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="npc",
            name="Acolyte",
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
        defender.id,
    )

    window = result["pending"][0]
    assert window["trigger"] == "targeted_by_attack"
    assert window["actor_id"] == defender.id

    listed = _call(capsys, "reaction", "list", "--campaign", campaign.id, "--actor", defender.id)
    assert listed["pending"][0]["id"] == window["id"]

    resolved = _call(
        capsys,
        "reaction",
        "resolve",
        "--campaign",
        campaign.id,
        "--id",
        window["id"],
        "--payload",
        '{"activity":"shield"}',
    )
    assert resolved["pending"][0]["status"] == "resolved"


def test_eligible_reaction_defers_attack_until_resolution(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "deferred-reaction.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    monkeypatch.setattr("sagasmith_dnd.engine.random.randint", lambda _low, _high: 9)
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Deferred reactions")
        documents = FoundryDocumentService(database)
        attacker = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="character",
            name="Mira",
        )
        defender = documents.create_actor(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_type="npc",
            name="Acolyte",
            system={"attributes": {"ac": {"value": 10}, "hp": {"value": 10, "max": 10}}},
        )
        sword = documents.create_item(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_id=attacker.id,
            item_type="weapon",
            name="Longsword",
        )
        slash = documents.create_activity(
            item_id=sword.id,
            activity_type="attack",
            name="Slash",
            activation={"type": "action"},
            system={"attack_bonus": 5, "damage": "1d6", "damage_type": "slashing"},
        )
        shield = documents.create_item(
            campaign_id=campaign.id,
            system_id="dnd5e",
            actor_id=defender.id,
            item_type="spell",
            name="Shield",
        )
        shield_activity = documents.create_activity(
            item_id=shield.id,
            activity_type="effect",
            name="Shield",
            activation={"type": "reaction"},
            system={"trigger": "before_hit_resolution"},
            duration={"period": "until_turn_start", "anchor": "self"},
            effects=[
                {
                    "name": "Shield",
                    "changes": [{"key": "system.attributes.ac.value", "mode": "ADD", "value": 5}],
                }
            ],
        )
    finally:
        database.dispose()

    declared = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign.id,
        "--actor",
        attacker.id,
        "--item",
        sword.id,
        "--activity",
        slash.id,
        "--target-id",
        defender.id,
    )
    assert declared["deferred"] is True
    window = declared["pending"][0]
    assert window["candidates"][0]["activity_id"] == shield_activity.id

    resolved = _call(
        capsys,
        "reaction",
        "resolve",
        "--campaign",
        campaign.id,
        "--id",
        window["id"],
        "--payload",
        json.dumps({"item_id": shield.id, "activity_id": shield_activity.id}),
    )
    assert resolved["reaction_result"]["effects"][0]["name"] == "Shield"
    assert resolved["continuation_result"]["execution"]["hit"] is False
