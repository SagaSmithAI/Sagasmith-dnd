from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_core import CharacterService, Database
from sagasmith_core.database import sqlite_database_url
from sagasmith_dnd.character_schema import (
    add_effect,
    default_character_notes,
    default_character_sheet,
)
from sagasmith_dnd.standard_spell_ids import CORE_INVISIBILITY_SPELL_ID
from test_ground_actor_creation_mcp import _creation_rows
from test_ground_items_mcp import _snapshot
from test_official_expansions_mcp import _call, _config
from test_structured_spell_mcp import _invisibility, _slot

from sagasmith_dnd_mcp.random_state import RandomStateMutationService
from sagasmith_dnd_mcp.server import close_server, create_server


@pytest.mark.fresh_database
@pytest.mark.parametrize("mode", ["direct", "build", "template"])
def test_initial_incapacitation_ends_imported_concentration(tmp_path: Path, mode: str) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Initial concentration",
                    "edition": "2014",
                    "idempotency_key": "campaign",
                },
            )
            sheet, _ = add_effect(
                default_character_sheet(),
                {
                    "id": "concentrating",
                    "name": "Concentration",
                    "kind": "concentration",
                    "active": True,
                    "concentration": True,
                    "duration": {"period": "minute", "remaining": 1},
                    "changes": [],
                },
            )
            sheet, _ = add_effect(
                sheet,
                {
                    "id": "non-concentration",
                    "name": "Non-concentration",
                    "kind": "other",
                    "active": True,
                    "concentration": False,
                    "changes": [],
                },
            )
            sheet["conditions"] = ["unconscious"]
            payload = {"campaign_id": campaign["id"], "name": "Initial", "sheet": sheet}
            if mode == "template":
                template = await _call(
                    server,
                    "character_create_from",
                    {
                        "mode": "direct",
                        "payload": {"name": "Library", "sheet": sheet},
                        "idempotency_key": "library",
                    },
                )
                payload = {"campaign_id": campaign["id"], "template_id": template["id"]}
            arguments = {"mode": mode, "payload": payload, "idempotency_key": "create"}
            result = await server.call_tool("character_create_from", arguments)
            value = result[1]["result"]
            actor = value["instance"] if mode == "build" else value
            effects = {effect["id"]: effect for effect in actor["sheet"]["effects"]}
            assert effects["concentrating"]["active"] is False
            assert effects["concentrating"]["ended_reason"] == "incapacitated"
            assert effects["non-concentration"]["active"] is True
            after = await _snapshot(server, campaign["id"], [actor["id"]])
            assert after[1][0]["sheet"] == actor["sheet"]
            close_server(server)
            server = create_server(config)
            assert await server.call_tool("character_create_from", arguments) == result
            assert await _snapshot(server, campaign["id"], [actor["id"]]) == after
        finally:
            close_server(server)

    asyncio.run(exercise())


@pytest.mark.fresh_database
def test_existing_core_build_receipt_replays_without_creating_new_state(tmp_path: Path) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        database = None
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Legacy build",
                    "edition": "2014",
                    "idempotency_key": "campaign",
                },
            )
            sheet = default_character_sheet()
            notes = default_character_notes()
            database = Database(sqlite_database_url(config.database_path))
            template, instance = CharacterService(database).create_with_instance(
                system_id="dnd5e",
                campaign_id=campaign["id"],
                name="Legacy actor",
                sheet=sheet,
                notes=notes,
                principal_id="system:local",
                idempotency_key="old-build",
            )
            expected = {
                key: await _call(
                    server,
                    "character_query",
                    {
                        "view": "get",
                        "payload": {"character_id": actor.id},
                    },
                )
                for key, actor in (("template", template), ("instance", instance))
            }
            arguments = {
                "mode": "build",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Legacy actor",
                    "sheet": sheet,
                    "notes": notes,
                },
                "idempotency_key": "old-build",
            }
            before = _creation_rows(config.database_path)
            result = await server.call_tool("character_create_from", arguments)
            assert result[1]["result"] == expected
            assert _creation_rows(config.database_path) == before
            close_server(server)
            server = create_server(config)
            assert await server.call_tool("character_create_from", arguments) == result
            assert _creation_rows(config.database_path) == before
        finally:
            if database is not None:
                database.dispose()
            close_server(server)

    asyncio.run(exercise())


@pytest.mark.fresh_database
@pytest.mark.parametrize("change", ["unconscious", "remove_concentration"])
@pytest.mark.parametrize("self_target", [False, True])
def test_sheet_replacement_reconciles_real_invisibility_target(
    tmp_path: Path, change: str, self_target: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Replace concentration",
                    "edition": "2014",
                    "idempotency_key": "campaign",
                },
            )
            caster_sheet = default_character_sheet()
            caster_sheet["spellcasting"].update(ability="intelligence", spell_slots=_slot(2))
            caster_sheet["content"]["spells"] = [_invisibility()]
            actors = []
            for name, sheet in (("Caster", caster_sheet), ("Target", default_character_sheet())):
                actors.append(
                    await _call(
                        server,
                        "character_create_from",
                        {
                            "mode": "direct",
                            "payload": {
                                "campaign_id": campaign["id"],
                                "name": name,
                                "sheet": sheet,
                            },
                            "idempotency_key": name,
                        },
                    )
                )
            caster, target = actors
            if self_target:
                target = caster
            target_index = 0 if self_target else 1
            cast = await _call(
                server,
                "character_action",
                {
                    "character_id": caster["id"],
                    "action": "cast_spell",
                    "payload": {
                        "spell_id": CORE_INVISIBILITY_SPELL_ID,
                        "cast_level": 2,
                        "target_character_ids": [target["id"]],
                    },
                    "expected_revision": caster["revision"],
                    "idempotency_key": "cast",
                },
            )
            assert cast["result"]["automatic_effect"] == "invisibility"
            ids = [actor["id"] for actor in actors]
            before = await _snapshot(server, campaign["id"], ids)
            assert "invisible" in before[1][target_index]["sheet"]["conditions"]
            replacement = deepcopy(before[1][0]["sheet"])
            if change == "unconscious":
                replacement["conditions"].append("unconscious")
            else:
                replacement["effects"] = [
                    effect for effect in replacement["effects"] if not effect["concentration"]
                ]
            arguments = {
                "character_id": caster["id"],
                "sheet": replacement,
                "expected_revision": before[1][0]["revision"],
                "idempotency_key": "replace",
            }
            if not self_target:
                rows_before = _creation_rows(config.database_path)
                original_replace = RandomStateMutationService.replace
                attempts = []

                def stale_target(service, campaign_id, **kwargs):
                    updates = kwargs["character_updates"]
                    assert {update.character_id for update in updates} == set(ids)
                    attempts.append(True)
                    kwargs["character_updates"] = [
                        replace(update, expected_revision=update.expected_revision - 1)
                        if update.character_id == target["id"]
                        else update
                        for update in updates
                    ]
                    return original_replace(service, campaign_id, **kwargs)

                with monkeypatch.context() as patch:
                    patch.setattr(RandomStateMutationService, "replace", stale_target)
                    with pytest.raises(ToolError, match="revision conflict"):
                        await server.call_tool("character_sheet_replace", arguments)
                assert attempts == [True]
                assert _creation_rows(config.database_path) == rows_before
            result = await server.call_tool("character_sheet_replace", arguments)
            after = await _snapshot(server, campaign["id"], ids)
            assert not any(
                effect["active"] and effect["concentration"]
                for effect in after[1][0]["sheet"]["effects"]
            )
            target_effect = next(
                effect
                for effect in after[1][target_index]["sheet"]["effects"]
                if effect["id"] == cast["result"]["effect_ids"][target["id"]]
            )
            assert target_effect["active"] is False
            assert target_effect["ended_reason"] == "source_effect_ended"
            assert "invisible" not in after[1][target_index]["sheet"]["conditions"]
            assert after[1][target_index]["revision"] == before[1][target_index]["revision"] + 1
            assert result[1]["sheet"] == after[1][0]["sheet"]
            if self_target:
                assert after[1][1] == before[1][1]
            history = await _call(
                server, "state_revision", {"campaign_id": campaign["id"], "action": "history"}
            )
            revisions = [item for item in history if item["idempotency_key"] == "replace"]
            assert {item["entity_id"] for item in revisions} == {caster["id"], target["id"]}
            assert len({item["mutation_group_id"] for item in revisions}) == 1
            assert await server.call_tool("character_sheet_replace", arguments) == result
            close_server(server)
            server = create_server(config)
            assert await server.call_tool("character_sheet_replace", arguments) == result
            assert await _snapshot(server, campaign["id"], ids) == after
        finally:
            close_server(server)

    asyncio.run(exercise())
