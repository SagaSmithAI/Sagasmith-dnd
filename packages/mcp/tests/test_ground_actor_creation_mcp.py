from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_core import Database
from sagasmith_core.actor_lifecycle import ActorLifecycleService
from sagasmith_core.database import sqlite_database_url
from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    equip_inventory_item,
)
from test_ground_items_mcp import _shield, _snapshot, _weapon
from test_official_expansions_mcp import _call, _config

import sagasmith_dnd_mcp.actor_inventory_lifecycle as lifecycle_module
from sagasmith_dnd_mcp.server import close_server, create_server


@pytest.mark.fresh_database
@pytest.mark.parametrize("mode", ["direct", "template", "build"])
def test_unconscious_actor_creation_drops_held_items_atomically(tmp_path: Path, mode: str) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Creation custody",
                    "edition": "2014",
                    "idempotency_key": "campaign",
                },
            )
            sheet = default_character_sheet()
            sheet, sword_id = add_inventory_item(sheet, _weapon())
            sheet, shield_id = add_inventory_item(sheet, _shield())
            sheet = equip_inventory_item(sheet, sword_id, "main_hand")
            sheet = equip_inventory_item(sheet, shield_id, "shield")
            sheet["conditions"] = ["unconscious"]
            payload = {"campaign_id": campaign["id"], "name": "Sleeping arrival", "sheet": sheet}
            template = None
            if mode == "template":
                template = await _call(
                    server,
                    "character_create_from",
                    {
                        "mode": "direct",
                        "payload": {"name": "Template", "sheet": sheet},
                        "idempotency_key": "template",
                    },
                )
                payload = {"campaign_id": campaign["id"], "template_id": template["id"]}
            arguments = {"mode": mode, "payload": payload, "idempotency_key": "arrival"}
            result = await server.call_tool("character_create_from", arguments)
            value = result[1]["result"]
            actor = value["instance"] if mode == "build" else value
            assert "prone" in actor["sheet"]["conditions"]
            assert actor["sheet"]["inventory"]["equipment_slots"]["main_hand"] is None
            assert actor["sheet"]["inventory"]["equipment_slots"]["shield"] == shield_id
            after = await _snapshot(server, campaign["id"], [actor["id"]])
            assert after[1][0]["sheet"] == actor["sheet"]
            ground = after[0]["state"]["ground_items"]
            assert len(ground) == 1
            assert ground[0]["source_actor_id"] == actor["id"]
            assert ground[0]["root_item_id"] == sword_id
            assert ground[0]["location"] == {"mode": "agent", "anchor_actor_id": actor["id"]}
            assert [item["id"] for item in ground[0]["items"]] == [sword_id]
            assert actor["sheet"]["inventory"]["external_items"][0]["location"] == {
                "kind": "ground",
                "ground_id": ground[0]["id"],
                "item_id": sword_id,
            }
            if mode == "build":
                template = value["template"]
            if template is not None:
                # A reusable library template is not a live campaign actor.
                assert template["sheet"]["inventory"]["equipment_slots"]["main_hand"] == sword_id
            assert await server.call_tool("character_create_from", arguments) == result
            close_server(server)
            server = create_server(config)
            assert await server.call_tool("character_create_from", arguments) == result
            assert await _snapshot(server, campaign["id"], [actor["id"]]) == after
            history = await _call(
                server,
                "state_revision",
                {
                    "campaign_id": campaign["id"],
                    "action": "history",
                },
            )
            created_revisions = [item for item in history if item["idempotency_key"] == "arrival"]
            assert {item["entity_type"] for item in created_revisions} == {
                "campaign",
                "actor_lifecycle",
            }
            assert len({item["mutation_group_id"] for item in created_revisions}) == 1
            await _call(
                server,
                "state_revision",
                {
                    "campaign_id": campaign["id"],
                    "action": "undo",
                    "payload": {"expected_history_sequence": history[0]["sequence"]},
                    "idempotency_key": "undo-arrival",
                },
            )
            undone_campaign, _ = await _snapshot(server, campaign["id"], [])
            assert not undone_campaign["state"].get("ground_items")
            assert (
                await _call(
                    server,
                    "character_query",
                    {
                        "view": "list",
                        "payload": {"campaign_id": campaign["id"]},
                    },
                )
                == []
            )
            history = await _call(
                server,
                "state_revision",
                {
                    "campaign_id": campaign["id"],
                    "action": "history",
                },
            )
            sequence = next((item["sequence"] for item in history if item["applied"]), 0)
            await _call(
                server,
                "state_revision",
                {
                    "campaign_id": campaign["id"],
                    "action": "redo",
                    "payload": {"expected_history_sequence": sequence},
                    "idempotency_key": "redo-arrival",
                },
            )
            redone = await _snapshot(server, campaign["id"], [actor["id"]])
            assert redone[0]["state"]["ground_items"] == ground
            assert redone[1][0]["sheet"] == actor["sheet"]
        finally:
            close_server(server)

    asyncio.run(exercise())


def _creation_rows(path: Path) -> dict:
    with sqlite3.connect(path) as connection:
        return {
            table: sorted(connection.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr)
            for table in (
                "campaigns",
                "characters",
                "actor_grants",
                "state_documents",
                "state_revisions",
                "mutation_groups",
                "idempotency_records",
            )
        }


@pytest.mark.fresh_database
@pytest.mark.parametrize("mode", ["direct", "build"])
def test_late_creation_custody_failure_rolls_back_every_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Creation rollback",
                    "edition": "2014",
                    "idempotency_key": "campaign",
                },
            )
            sheet, item_id = add_inventory_item(default_character_sheet(), _weapon())
            sheet = equip_inventory_item(sheet, item_id, "main_hand")
            sheet["conditions"] = ["unconscious"]
            arguments = {
                "mode": mode,
                "payload": {"campaign_id": campaign["id"], "name": "Rollback", "sheet": sheet},
                "idempotency_key": "arrival",
            }
            before = _creation_rows(config.database_path)
            core_results = []
            original_create = ActorLifecycleService.create
            original_validate = lifecycle_module.validate_external_inventory_custody

            def record_creation(service, *args, **kwargs):
                result = original_create(service, *args, **kwargs)
                core_results.append(result)
                return result

            def fail_after_core_write(*args, **kwargs):
                original_validate(*args, **kwargs)
                if core_results:
                    raise ValueError("injected late custody rejection")

            with monkeypatch.context() as patch:
                patch.setattr(ActorLifecycleService, "create", record_creation)
                patch.setattr(
                    lifecycle_module, "validate_external_inventory_custody", fail_after_core_write
                )
                with pytest.raises(ToolError, match="injected late custody rejection"):
                    await server.call_tool("character_create_from", arguments)
            assert len(core_results) == 1
            assert core_results[0].mutation_group_id
            assert _creation_rows(config.database_path) == before
            # The failed receipt must not poison retries, including build's
            # template creation and its outer idempotency scope.
            result = await _call(server, "character_create_from", arguments)
            actor = result["instance"] if mode == "build" else result
            after = await _snapshot(server, campaign["id"], [actor["id"]])
            assert len(after[0]["state"]["ground_items"]) == 1
            assert actor["sheet"]["inventory"]["equipment_slots"]["main_hand"] is None
        finally:
            close_server(server)

    asyncio.run(exercise())


@pytest.mark.fresh_database
def test_inventory_lifecycle_preserves_core_request_digests(tmp_path: Path) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        database = None
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Old lifecycle receipts",
                    "edition": "2014",
                    "idempotency_key": "campaign",
                },
            )
            database = Database(sqlite_database_url(config.database_path))
            core = ActorLifecycleService(database)
            wrapped = lifecycle_module.InventoryActorLifecycleService(
                database,
                ground_context=lambda *_: {},
            )
            arguments = {
                "system_id": "dnd5e",
                "name": "Existing request",
                "character_type": "pc",
                "sheet": default_character_sheet(),
                "notes": {},
                "principal_id": "system:local",
                "operation": "character.create",
                "actor": "system:local",
            }
            for key, first, second in (("old", core, wrapped), ("new", wrapped, core)):
                arguments["name"] = f"Existing request {key}"
                original = first.create(campaign["id"], **arguments, idempotency_key=key)
                before = _creation_rows(config.database_path)
                replay = second.create(campaign["id"], **arguments, idempotency_key=key)
                assert replay.replayed is True
                assert replay.character == original.character
                assert replay.revisions == original.revisions
                assert _creation_rows(config.database_path) == before
        finally:
            if database is not None:
                database.dispose()
            close_server(server)

    asyncio.run(exercise())
