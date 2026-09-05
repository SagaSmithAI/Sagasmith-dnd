from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from sagasmith_core import CampaignService, CharacterService, Database
from sagasmith_core.database import sqlite_database_url
from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    equip_inventory_item,
)

from sagasmith_dnd_mcp.actor_inventory_lifecycle import InventoryActorLifecycleService


def _authorization(campaign_id: str, owner_id: str, dependent_id: str) -> dict:
    return {
        "schema_version": 1,
        "purpose": "dependent_actor_template",
        "campaign_id": campaign_id,
        "owner_character_id": owner_id,
        "dependent_actor_id": dependent_id,
        "relation_key": "steel_defender",
        "source_artifact_id": "steel-defender",
        "source_pack_id": "pack",
        "source_pack_version": "1.0.0",
        "owner_class_name": "artificer",
        "casting_slot_level": None,
        "template_variant": None,
        "numeric_parameters": {
            "owner_class_level": 5,
            "owner_proficiency_bonus": 3,
        },
        "reviewed_expression_hash": "a" * 64,
        "signature": "b" * 64,
    }


def _service_fixture(tmp_path: Path):
    database = Database(sqlite_database_url(tmp_path / "replacement.db"))
    database.create_schema()
    campaign = CampaignService(database).create(system_id="dnd5e", name="Replacement")
    characters = CharacterService(database)
    owner = characters.create(
        system_id="dnd5e",
        campaign_id=campaign.id,
        name="Artificer",
        sheet=default_character_sheet(),
    )
    old_sheet = default_character_sheet()
    old_sheet["combat"]["hp"]["value"] = 1
    old_sheet, held_item_id = add_inventory_item(
        old_sheet,
        {
            "id": "old-defender-held-item",
            "name": "Held Component",
            "kind": "weapon",
            "mechanics": {
                "category": "simple",
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d4",
                "damage_type": "bludgeoning",
                "properties": [],
            },
        },
    )
    old_sheet = equip_inventory_item(old_sheet, held_item_id, "main_hand")
    old = characters.create(
        system_id="dnd5e", campaign_id=campaign.id, name="Old Defender", sheet=old_sheet
    )
    current_state = {
        "dependent_actor_relations": [
            {
                "owner_character_id": owner.id,
                "dependent_actor_id": old.id,
                "relation_key": "steel_defender",
                "source_artifact_id": "steel-defender",
                "source_pack_id": "pack",
                "source_pack_version": "1.0.0",
                "status": "active",
                "created_campaign_revision": 1,
                "created_long_rest_elapsed_ticks": None,
                "death_elapsed_ticks": None,
                "revival_started_elapsed_ticks": None,
                "revival_completes_elapsed_ticks": None,
                "template_binding": {
                    "owner_class_name": "artificer",
                    "casting_slot_level": None,
                    "template_variant": None,
                    "numeric_parameters": {
                        "owner_class_level": 5,
                        "owner_proficiency_bonus": 3,
                    },
                    "reviewed_expression_hash": "a" * 64,
                    "authorization": _authorization(campaign.id, owner.id, old.id),
                },
            }
        ]
    }
    campaign = CampaignService(database).update(campaign.id, state=current_state)
    return database, campaign, owner, old, current_state


def _ground_context(campaign, _state: dict, actor_id: str) -> dict:
    return {
        "scene_id": None,
        "encounter_id": None,
        "campaign_revision": campaign.revision,
        "location": {"mode": "agent", "anchor_actor_id": actor_id},
    }


def _replacement_state(
    current_state: dict, campaign_id: str, owner_id: str, old_id: str, new_id: str
) -> dict:
    state = deepcopy(current_state)
    state["dependent_actor_relations"][0].update(
        status="replaced",
        death_elapsed_ticks=4800,
    )
    state["dependent_actor_relations"].append(
        {
            "owner_character_id": owner_id,
            "dependent_actor_id": new_id,
            "relation_key": "steel_defender",
            "source_artifact_id": "steel-defender",
            "source_pack_id": "pack",
            "source_pack_version": "1.0.0",
            "status": "active",
            "created_campaign_revision": 3,
            "created_long_rest_elapsed_ticks": 4800,
            "death_elapsed_ticks": None,
            "revival_started_elapsed_ticks": None,
            "revival_completes_elapsed_ticks": None,
            "template_binding": {
                "owner_class_name": "artificer",
                "casting_slot_level": None,
                "template_variant": None,
                "numeric_parameters": {
                    "owner_class_level": 5,
                    "owner_proficiency_bonus": 3,
                },
                "reviewed_expression_hash": "a" * 64,
                "authorization": _authorization(campaign_id, owner_id, new_id),
            },
        }
    )
    return state


def _create_args(campaign_id: str, actor_id: str, old_id: str, state: dict, *, name: str) -> dict:
    return {
        "system_id": "dnd5e",
        "name": name,
        "character_type": "npc",
        "sheet": default_character_sheet(),
        "notes": {},
        "principal_id": "system:local",
        "idempotency_key": "replacement",
        "actor_id": actor_id,
        "campaign_state": state,
        "expected_campaign_revision": 2,
        "dependent_actor_replacement": {
            "character_id": old_id,
            "expected_revision": 1,
        },
    }


def test_replacement_perishes_old_actor_in_same_ambient_transaction(tmp_path: Path) -> None:
    database, campaign, owner, old, current_state = _service_fixture(tmp_path)
    try:
        args = _create_args(
            campaign.id,
            "new-defender",
            old.id,
            _replacement_state(current_state, campaign.id, owner.id, old.id, "new-defender"),
            name="New Defender",
        )
        result = InventoryActorLifecycleService(database, ground_context=_ground_context).create(
            campaign.id, **args
        )

        perished = CharacterService(database).get(old.id)
        updated_campaign = CampaignService(database).get(campaign.id)
        assert perished.revision == 2
        assert perished.sheet["combat"]["hp"]["value"] == 0
        assert "dead" in perished.sheet["conditions"]
        assert perished.sheet["inventory"]["equipment_slots"]["main_hand"] is None
        assert perished.sheet["inventory"]["items"] == []
        ground = updated_campaign.state["ground_items"]
        assert len(ground) == 1
        assert ground[0]["source_actor_id"] == old.id
        assert ground[0]["root_item_id"] == "old-defender-held-item"
        assert ground[0]["location"] == {
            "mode": "agent",
            "anchor_actor_id": old.id,
        }
        assert perished.sheet["inventory"]["external_items"][0]["location"] == {
            "kind": "ground",
            "ground_id": ground[0]["id"],
            "item_id": "old-defender-held-item",
        }
        assert result.character.id == "new-defender"
    finally:
        database.dispose()


def test_replacement_super_failure_rolls_back_perished_sheet(tmp_path: Path) -> None:
    database, campaign, owner, old, current_state = _service_fixture(tmp_path)
    try:
        args = _create_args(
            campaign.id,
            "new-defender",
            old.id,
            _replacement_state(current_state, campaign.id, owner.id, old.id, "new-defender"),
            name="Artificer",
        )
        with pytest.raises(Exception):
            InventoryActorLifecycleService(database, ground_context=_ground_context).create(
                campaign.id, **args
            )

        unchanged = CharacterService(database).get(old.id)
        after_campaign = CampaignService(database).get(campaign.id)
        assert unchanged.revision == 1
        assert unchanged.sheet["combat"]["hp"]["value"] == 1
        assert "dead" not in unchanged.sheet["conditions"]
        assert unchanged.sheet["inventory"]["equipment_slots"]["main_hand"] == (
            "old-defender-held-item"
        )
        assert after_campaign.revision == campaign.revision
        assert after_campaign.state == current_state
    finally:
        database.dispose()
