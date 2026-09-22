import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_core import IdempotencyService
from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    equip_inventory_item,
)
from sagasmith_dnd.spells import CORE_MAGE_ARMOR_SPELL_ID

from sagasmith_dnd_mcp.server import create_server
from tests.test_structured_spell_mcp import (
    _call,
    _campaign_actor_snapshot,
    _campaign_with_combat,
    _config,
)


def _caster(case):
    sheet = default_character_sheet()
    sheet["spellcasting"]["spell_slots"] = {
        "1": {"value": 2, "max": 2, "recovers_on": "long_rest"},
    }
    sheet["content"]["spells"] = [
        {
            "id": CORE_MAGE_ARMOR_SPELL_ID,
            "name": "Mage Armor",
            "level": 1,
            "grant": {"source_type": "class", "source_key": "wizard"},
            "access": {"known": True, "prepared": True},
            "mechanic_refs": ["dnd5e.core.spell.mage_armor"],
            "definition": {
                "casting_time": "1 action",
                "range": {"kind": "self"},
                "components": {
                    "verbal": True,
                    "somatic": True,
                    "material": True,
                    "consumed": case == "consumed",
                    "material_cost_cp": 100 if case == "consumed" else 0,
                },
            },
        }
    ]
    if case != "missing":
        role = {"kind": "pouch", "source": "SRD 2014 Equipment: component pouch"}
        if case == "consumed":
            role = {
                "kind": "material",
                "source": "test:reviewed consumed casting kit",
                "spell_ids": [CORE_MAGE_ARMOR_SPELL_ID],
            }
        sheet, _ = add_inventory_item(
            sheet,
            {
                "id": "materials",
                "name": "Materials",
                "kind": "equipment",
                "quantity": 2,
                "price_cp": 100,
                "mechanics": {"spell_component": role},
            },
        )
    if case in {"silence", "gagged", "hands"}:
        sheet["effects"].append(
            {
                "id": "constraint",
                "source": f"review:scene {case}",
                "active": True,
                "duration": {"period": "round", "remaining": 1},
                "metadata": {
                    "spell_component_constraints": {"usable_hands": 0}
                    if case == "hands"
                    else {"can_speak": False}
                },
            }
        )
    if case == "shield_weapon":
        sheet["traits"]["proficiencies"]["armor"] = ["shields"]
        for item, slot in [
            ({"id": "sword", "kind": "weapon"}, "main_hand"),
            ({"id": "shield", "kind": "shield", "mechanics": {"ac_bonus": 2}}, "shield"),
        ]:
            sheet, _ = add_inventory_item(sheet, item)
            sheet = equip_inventory_item(sheet, item["id"], slot)
    return sheet


async def _prepare(server, sheet, combat):
    if combat:
        campaign_id, revision, actors = await _campaign_with_combat(
            server,
            [("Caster", sheet), ("Target", default_character_sheet())],
        )
        actor = actors[0]
        tool = "combat_cast_spell"
        arguments = {
            "campaign_id": campaign_id,
            "actor_id": actor["id"],
            "spell_id": CORE_MAGE_ARMOR_SPELL_ID,
            "expected_revision": revision,
            "idempotency_key": "cast",
        }
    else:
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "Components",
                "edition": "2014",
                "idempotency_key": "campaign",
            },
        )
        campaign_id = campaign["id"]
        actor = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign_id, "name": "Caster", "sheet": sheet},
                "principal_id": "system:local",
                "idempotency_key": "actor",
            },
        )
        tool = "character_action"
        arguments = {
            "character_id": actor["id"],
            "action": "cast_spell",
            "payload": {"spell_id": CORE_MAGE_ARMOR_SPELL_ID},
            "expected_revision": actor["revision"],
            "idempotency_key": "cast",
        }
    return campaign_id, actor["id"], tool, arguments


async def _cast(server, tool, args):
    _, result = await server.call_tool(tool, args)
    return result["result"] if tool == "character_action" else result


@pytest.mark.parametrize("slug,eligible", [("component-pouch", True), ("pouch", False)])
def test_bundled_catalog_item_binds_only_reviewed_component_pouch(tmp_path, slug, eligible):
    async def exercise():
        server = create_server(replace(
            _config(tmp_path), dnd_skills_dir=Path(__file__).resolve().parents[3] / "skills",
        ))
        campaign_id, actor_id, tool, args = await _prepare(server, _caster("missing"), False)
        assert (await _cast(server, tool, args))["status"] == "pending_ruling"
        request = {
            "character_id": actor_id,
            "artifact_id": "dnd5e.content.srd2014.item." + slug,
            "expected_revision": args["expected_revision"],
            "idempotency_key": "catalog-item",
        }
        actor = await _call(server, "character_content_apply", request)
        assert await _call(server, "character_content_apply", request) == actor
        args["expected_revision"] = actor["revision"]
        before = await _campaign_actor_snapshot(server, campaign_id, [actor_id])
        result = await _cast(server, tool, args)
        assert result["status"] == ("committed" if eligible else "pending_ruling")
        if eligible:
            item = actor["sheet"]["inventory"]["items"][0]
            assert item["mechanics"]["spell_component"]["kind"] == "pouch"
            assert item["source_key"] == request["artifact_id"]
            assert await _cast(server, tool, args) == result
        else:
            assert await _campaign_actor_snapshot(server, campaign_id, [actor_id]) == before

    asyncio.run(exercise())


@pytest.mark.parametrize("combat", [False, True])
@pytest.mark.parametrize("case", ["silence", "gagged", "hands", "shield_weapon", "missing"])
def test_component_rejection_preserves_campaign_actor_rng_and_expiring_effects(
    tmp_path,
    combat,
    case,
):
    async def exercise():
        server = create_server(_config(tmp_path))
        campaign_id, actor_id, tool, args = await _prepare(server, _caster(case), combat)
        before = await _campaign_actor_snapshot(server, campaign_id, [actor_id])
        for requested in (args, {**args, "expected_revision": args["expected_revision"] - 1}):
            try:
                result = await _cast(server, tool, requested)
            except ToolError:
                pass
            else:
                assert result["status"] == "pending_ruling"
            assert await _campaign_actor_snapshot(server, campaign_id, [actor_id]) == before

    asyncio.run(exercise())


@pytest.mark.parametrize("combat", [False, True])
def test_consumed_material_slot_and_effect_roll_back_and_replay_after_restart(
    tmp_path,
    monkeypatch,
    combat,
):
    async def exercise():
        config = _config(tmp_path)
        server = create_server(config)
        campaign_id, actor_id, tool, args = await _prepare(server, _caster("consumed"), combat)
        before = await _campaign_actor_snapshot(server, campaign_id, [actor_id])
        remember = IdempotencyService.remember_write_in_session

        def fail_receipt(self, session, **kwargs):
            if kwargs.get("key") == "cast":
                raise RuntimeError("injected component transaction failure")
            return remember(self, session, **kwargs)

        monkeypatch.setattr(IdempotencyService, "remember_write_in_session", fail_receipt)
        with pytest.raises(ToolError, match="injected component transaction failure"):
            await _cast(server, tool, args)
        assert await _campaign_actor_snapshot(server, campaign_id, [actor_id]) == before
        monkeypatch.setattr(IdempotencyService, "remember_write_in_session", remember)
        committed = await _cast(server, tool, args)
        assert committed["status"] == "committed"
        receipt = committed["result"]["component_receipt"]
        assert receipt["material"]["consumed"] is True
        after = await _campaign_actor_snapshot(server, campaign_id, [actor_id])
        sheet = after["actors"][0]["sheet"]
        assert sheet["inventory"]["items"][0]["quantity"] == 1
        assert sheet["spellcasting"]["spell_slots"]["1"]["value"] == 1
        assert len([effect for effect in sheet["effects"] if effect["active"]]) == 1
        restarted = create_server(config)
        assert await _cast(restarted, tool, args) == committed
        assert await _campaign_actor_snapshot(restarted, campaign_id, [actor_id]) == after

    asyncio.run(exercise())


@pytest.mark.parametrize("combat", [False, True])
def test_failed_component_request_can_be_corrected_with_original_key(tmp_path, combat):
    async def exercise():
        server = create_server(_config(tmp_path))
        campaign_id, actor_id, tool, args = await _prepare(server, _caster("pouch"), combat)
        bad = deepcopy(args)
        (bad if combat else bad["payload"])["component_ruling"] = {"material_item_id": "absent"}
        before = await _campaign_actor_snapshot(server, campaign_id, [actor_id])
        assert (await _cast(server, tool, bad))["status"] == "pending_ruling"
        assert await _campaign_actor_snapshot(server, campaign_id, [actor_id]) == before
        assert (await _cast(server, tool, args))["status"] == "committed"

    asyncio.run(exercise())


def test_ready_checks_components_before_arming(tmp_path):
    async def exercise():
        server = create_server(_config(tmp_path))
        campaign_id, actor_id, _, cast = await _prepare(server, _caster("silence"), True)
        before = await _campaign_actor_snapshot(server, campaign_id, [actor_id])
        with pytest.raises(ToolError, match="verbal component"):
            await _call(
                server,
                "combat_ready",
                {
                    "campaign_id": campaign_id,
                    "action": "ready_spell",
                    "payload": {
                        "actor_id": actor_id,
                        "spell_id": CORE_MAGE_ARMOR_SPELL_ID,
                        "trigger": "the bell rings",
                    },
                    "expected_revision": cast["expected_revision"],
                    "idempotency_key": "ready",
                },
            )
        assert await _campaign_actor_snapshot(server, campaign_id, [actor_id]) == before

    asyncio.run(exercise())


def test_player_cannot_forge_unknown_source_components(tmp_path):
    async def exercise():
        server = create_server(_config(tmp_path))
        sheet = _caster("pouch")
        sheet["content"]["spells"][0]["custom_definition"] = {
            "component_details": "not_repeated_in_statblock",
        }
        campaign_id, actor_id, tool, args = await _prepare(server, sheet, False)
        for scope, payload in (
            ("campaign", {"role": "player"}),
            ("actor", {"actor_id": actor_id, "can_control": True, "can_view_private": True}),
        ):
            await _call(server, "access_grant", {
                "scope": scope, "campaign_id": campaign_id, "principal_id": "player:test",
                "payload": payload, "by_principal_id": "system:local",
            })
        args["principal_id"] = "player:test"
        args["payload"]["component_ruling"] = {
            "source": "player asserts componentless", "source_components": {
                "verbal": False, "somatic": False, "material": False,
            },
        }
        before = await _campaign_actor_snapshot(server, campaign_id, [actor_id])
        result = await _cast(server, tool, args)
        assert result["status"] == "pending_ruling"
        assert result["committed"] is False
        assert "Agent-as-DM" in result["reason"]
        assert await _campaign_actor_snapshot(server, campaign_id, [actor_id]) == before
    asyncio.run(exercise())


@pytest.mark.parametrize("components_required", [False, True])
def test_item_source_controls_component_waiver_before_charge_spend(tmp_path, components_required):
    async def exercise():
        from dataclasses import replace

        config = replace(
            _config(tmp_path), dnd_skills_dir=Path(__file__).resolve().parents[3] / "skills"
        )
        server = create_server(config)
        campaign_id, actor_id, tool, args = await _prepare(server, _caster("silence"), False)
        added = await _call(
            server,
            "inventory_change",
            {
                "owner": "character",
                "owner_id": actor_id,
                "action": "add",
                "payload": {
                    "item": {
                        "id": "staff",
                        "kind": "magic_item",
                        "name": "Reviewed casting staff",
                        "source_key": "review:source staff",
                        "charges": {"value": 2, "max": 2, "recovers_on": "dawn"},
                        "mechanics": {
                            "spellcasting": {
                                "requires_attunement": False,
                                "requires_class_spell_list": False,
                                "components_required": components_required,
                                "spells": [
                                    {
                                        "artifact_id": CORE_MAGE_ARMOR_SPELL_ID,
                                        "charge_cost": 1,
                                        "casting_time": "1 action",
                                    }
                                ],
                            }
                        },
                    }
                },
                "expected_revision": args["expected_revision"],
                "idempotency_key": "add-staff",
            },
        )
        equipped = await _call(
            server,
            "inventory_change",
            {
                "owner": "character",
                "owner_id": actor_id,
                "action": "equip",
                "payload": {"item_id": "staff", "slot": "main_hand"},
                "expected_revision": added["character"]["revision"],
                "idempotency_key": "equip-staff",
            },
        )
        args["expected_revision"] = equipped.get("character", equipped)["revision"]
        args["payload"]["source_item_id"] = "staff"
        before = await _campaign_actor_snapshot(server, campaign_id, [actor_id])
        if components_required:
            with pytest.raises(ToolError, match="verbal component"):
                await _cast(server, tool, args)
            assert await _campaign_actor_snapshot(server, campaign_id, [actor_id]) == before
        else:
            result = await _cast(server, tool, args)
            assert result["status"] == "committed"
            after = await _campaign_actor_snapshot(server, campaign_id, [actor_id])
            sheet = after["actors"][0]["sheet"]
            staff = next(item for item in sheet["inventory"]["items"] if item["id"] == "staff")
            assert staff["charges"]["value"] == 1
            assert sheet["spellcasting"]["spell_slots"]["1"]["value"] == 2

    asyncio.run(exercise())
