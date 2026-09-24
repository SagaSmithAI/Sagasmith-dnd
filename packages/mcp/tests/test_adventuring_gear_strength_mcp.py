"""Reviewed Crowbar and Portable Ram checks against exact scene objects."""

from __future__ import annotations

import asyncio
import hashlib
from copy import deepcopy

import pytest
from sagasmith_dnd.adventuring_gear import ADVENTURING_GEAR_SOURCE_REF
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd_runtime.application import create_runtime
from sagasmith_dnd_runtime.config import McpConfig
from sagasmith_dnd_runtime.operations import RequestIdentity

from tests.authoring_helpers import finalize_and_activate_module

DOOR_EXCERPT = "The wooden door leads to the vault."
WALL_EXCERPT = "A stone wall borders the hall."


class World:
    async def call(self, name: str, arguments: dict, *, principal: str = "system:local"):
        result = await self.runtime.execute(name, arguments, context=RequestIdentity(principal))
        return result.get("result", result) if isinstance(result, dict) else result

    async def snapshot(self):
        campaign = await self.call(
            "campaign_query", {"view": "get", "payload": {"campaign_id": self.cid}}
        )
        actor = await self.call(
            "character_query", {"view": "get", "payload": {"character_id": self.aid}}
        )
        return campaign, actor

    def close(self):
        self.runtime.close()


async def setup(tmp_path, *, include_helper: bool = False) -> World:
    world = World()
    world.config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "dnd",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
    )
    world.runtime = create_runtime(world.config)
    try:
        campaign = await world.call(
            "campaign_create",
            {
                "name": "Gear Strength Checks",
                "edition": "2014",
                "random_seed": "gear-strength-checks",
                "idempotency_key": "campaign",
            },
        )
        world.cid = campaign["id"]
        staged = await world.call(
            "module_draft",
            {
                "campaign_id": world.cid,
                "action": "start",
                "payload": {
                    "name": "gear-strength.md",
                    "content": (f"# Vault\n\n## Entry\n\n{DOOR_EXCERPT} {WALL_EXCERPT}"),
                    "source_key": "gear-strength",
                    "title": "Gear Strength Scene",
                },
                "idempotency_key": "module-draft",
            },
        )

        async def authoring_call(runtime, name, arguments):
            return await world.call(name, arguments)

        activation = await finalize_and_activate_module(
            authoring_call,
            world.runtime,
            world.cid,
            staged,
            source_key="gear-strength",
            title="Gear Strength Scene",
            portable_id="dnd5e.module.gear-strength-test",
        )
        module_id = activation["activated"]["activation"]["module_id"]
        hits = await world.call(
            "module_search",
            {"campaign_id": world.cid, "query": "wooden door stone wall", "top_k": 3},
        )
        expanded = await world.call("module_expand", {"chunk_id": hits[0]["id"]})
        world.source = {
            "module_id": module_id,
            "scene_id": expanded["scene"]["id"],
            "chunk_id": expanded["chunk_id"],
            "page_start": expanded["page_start"],
            "page_end": expanded["page_end"],
            "heading_path": expanded["heading_path"],
            "content_sha256": hashlib.sha256(expanded["content"].encode("utf-8")).hexdigest(),
        }
        world.door = {
            "id": "vault-door",
            "name": "Wooden vault door",
            "scene_id": world.source["scene_id"],
            "material": "wood",
            "size": "large",
            "resilience": "resilient",
            "armor_class": 10,
            "hit_points": 25,
        }
        world.wall = {
            "id": "hall-wall",
            "name": "Stone hall wall",
            "scene_id": world.source["scene_id"],
            "material": "stone",
            "size": "large",
            "resilience": "resilient",
            "armor_class": 15,
            "hit_points": 30,
        }
        sheet = default_character_sheet()
        sheet["edition"] = "2014"
        sheet["abilities"]["strength"]["score"] = 3
        sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
        sheet["inventory"]["items"] = [
            {
                "id": "crowbar-1",
                "name": "Crowbar",
                "source_key": "dnd5e.content.srd2014.item.crowbar",
                "quantity": 1,
            },
            {
                "id": "ram-1",
                "name": "Ram, portable",
                "source_key": "dnd5e.content.srd2014.item.ram-portable",
                "quantity": 1,
            },
        ]
        actor = await world.call(
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": world.cid,
                    "name": "Gear user",
                    "sheet": sheet,
                },
                "idempotency_key": "gear-actor",
            },
        )
        world.aid = actor["id"]
        if include_helper:
            helper_sheet = default_character_sheet()
            helper_sheet["edition"] = "2014"
            helper_sheet["combat"]["hp"] = {"value": 12, "max": 12, "temp": 0}
            helper = await world.call(
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": world.cid,
                        "name": "Ram helper",
                        "sheet": helper_sheet,
                    },
                    "idempotency_key": "gear-helper",
                },
            )
            world.helper_id = helper["id"]
        return world
    except BaseException:
        world.close()
        raise


async def review_object(world: World, profile: dict, *, door: bool, leverage: bool, excerpt: str):
    campaign, _ = await world.snapshot()
    return await world.call(
        "environment_change",
        {
            "campaign_id": world.cid,
            "action": "object_strength_review",
            "payload": {
                "object": deepcopy(profile),
                "object_source_ref": deepcopy(world.source),
                "object_ruling": {
                    "reason": "The DM reviewed this exact scene object and set its check DC.",
                    "source_excerpt": excerpt,
                },
                "strength_check": {
                    "door": door,
                    "strength_dc": 30 if not door else 1,
                    "crowbar_leverage": leverage,
                    "success_state": "open" if door else "breached",
                },
            },
            "expected_revision": campaign["revision"],
            "idempotency_key": f"review-{profile['id']}",
        },
    )


def gear_request(
    world: World,
    campaign: dict,
    actor: dict,
    *,
    item_id: str,
    intent: str,
    action_id: str,
    target: dict,
    **extra,
):
    return {
        "campaign_id": world.cid,
        "action_id": action_id,
        "item_id": item_id,
        "intent": intent,
        "source_ref": ADVENTURING_GEAR_SOURCE_REF,
        "actor_id": world.aid,
        "expected_actor_revision": actor["revision"],
        "expected_revision": campaign["revision"],
        "target_object": target,
        "idempotency_key": action_id,
        **extra,
    }


def test_crowbar_uses_reviewed_scene_fact_and_failed_check_does_not_change_object(tmp_path):
    async def run():
        world = await setup(tmp_path)
        try:
            player = "user:gear-player"
            await world.call(
                "access_grant",
                {
                    "scope": "campaign",
                    "campaign_id": world.cid,
                    "principal_id": player,
                    "payload": {"role": "player"},
                },
            )
            before_review, _ = await world.snapshot()
            review_request = {
                "campaign_id": world.cid,
                "action": "object_strength_review",
                "payload": {
                    "object": deepcopy(world.door),
                    "object_source_ref": world.source,
                    "object_ruling": {
                        "reason": "The DM reviewed this exact scene object and set its check DC.",
                        "source_excerpt": DOOR_EXCERPT,
                    },
                    "strength_check": {
                        "door": True,
                        "strength_dc": 1,
                        "crowbar_leverage": True,
                        "success_state": "open",
                    },
                },
                "expected_revision": before_review["revision"],
                "idempotency_key": "review-vault-door",
            }
            with pytest.raises(Exception, match="role|permission|campaign"):
                await world.call("environment_change", review_request, principal=player)
            assert (await world.snapshot())[0] == before_review

            reviewed = await world.call("environment_change", review_request)
            assert reviewed["status"] == "committed"
            reviewed_campaign, actor = await world.snapshot()
            target_ref = {"id": world.door["id"], "scene_id": world.door["scene_id"]}

            # Caller-supplied target facts, DCs, and success claims are rejected
            # before a roll, receipt, resource, or campaign write.
            for label, changes in (
                ("forged-dc", {"target_object": {**target_ref, "strength_dc": 1}}),
                ("forged-outcome", {"target_object": {**target_ref, "success": True}}),
                ("caller-context", {"action_context": {"dc": 1, "success": True}}),
            ):
                request = gear_request(
                    world,
                    reviewed_campaign,
                    actor,
                    item_id="crowbar-1",
                    intent="apply_leverage",
                    action_id=label,
                    target=target_ref,
                    **changes,
                )
                before = await world.snapshot()
                with pytest.raises(Exception):
                    await world.call("adventuring_gear_action", request)
                assert await world.snapshot() == before

            stale = gear_request(
                world,
                reviewed_campaign,
                actor,
                item_id="crowbar-1",
                intent="apply_leverage",
                action_id="stale-cas",
                target=target_ref,
            )
            stale["expected_revision"] -= 1
            before_stale = await world.snapshot()
            with pytest.raises(Exception, match="revision conflict"):
                await world.call("adventuring_gear_action", stale)
            assert await world.snapshot() == before_stale

            request = gear_request(
                world,
                reviewed_campaign,
                actor,
                item_id="crowbar-1",
                intent="apply_leverage",
                action_id="crowbar-open-door",
                target=target_ref,
            )
            result = await world.call("adventuring_gear_action", request)
            assert result["success"] is True
            assert len(result["check"]["rolls"]) == 2
            assert result["receipt"]["rule_plan"]["intent"] == "apply_leverage"
            assert result["receipt"]["target_scene_id"] == world.door["scene_id"]
            assert result["receipt"]["target_object_id"] == world.door["id"]
            assert result["receipt"]["target_source_ref"] == world.source
            assert result["receipt"]["source_ref"] == ADVENTURING_GEAR_SOURCE_REF
            assert result["target_object"]["state"]["state"] == "open"
            assert await world.call("adventuring_gear_action", request) == result

            after_success, after_actor = await world.snapshot()
            assert after_success["revision"] == reviewed_campaign["revision"] + 1
            crowbar = next(
                item
                for item in after_actor["sheet"]["inventory"]["items"]
                if item["id"] == "crowbar-1"
            )
            assert crowbar["quantity"] == 1

            # A second DM-reviewed DC is intentionally unreachable with this
            # actor's Strength. The check is recorded, but the object stays intact.
            await review_object(
                world,
                world.wall,
                door=False,
                leverage=False,
                excerpt=WALL_EXCERPT,
            )
            before_fail_campaign, before_fail_actor = await world.snapshot()
            wall_ref = {"id": world.wall["id"], "scene_id": world.wall["scene_id"]}
            fail_request = gear_request(
                world,
                before_fail_campaign,
                before_fail_actor,
                item_id="crowbar-1",
                intent="apply_leverage",
                action_id="crowbar-wall-fail",
                target=wall_ref,
            )
            failed = await world.call("adventuring_gear_action", fail_request)
            assert failed["success"] is False
            assert len(failed["check"]["rolls"]) == 1
            after_fail_campaign, _ = await world.snapshot()
            wall_state = after_fail_campaign["state"]["scene_objects"][world.wall["scene_id"]][
                world.wall["id"]
            ]
            assert "gear_strength_check_state" not in wall_state
            assert after_fail_campaign["state"]["item_spends"][-1]["success"] is False
        finally:
            world.close()

    asyncio.run(run())


def test_portable_ram_pays_combat_help_and_check_in_one_cas(tmp_path):
    async def run():
        world = await setup(tmp_path, include_helper=True)
        try:
            await review_object(
                world,
                world.door,
                door=True,
                leverage=False,
                excerpt=DOOR_EXCERPT,
            )
            campaign, _ = await world.snapshot()
            phase = await world.call(
                "game_phase",
                {
                    "campaign_id": world.cid,
                    "action": "set",
                    "tool_profile": "play",
                    "expected_revision": campaign["revision"],
                    "idempotency_key": "phase-play",
                },
            )
            started = await world.call(
                "combat_start",
                {
                    "campaign_id": world.cid,
                    "positioning_mode": "grid",
                    "participant_ids": [world.aid, world.helper_id],
                    "participant_config": [
                        {"actor_id": world.aid, "initiative": 20, "position": {"x": 0, "y": 0}},
                        {
                            "actor_id": world.helper_id,
                            "initiative": 10,
                            "position": {"x": 1, "y": 0},
                        },
                    ],
                    "battle_map": {"width_cells": 10, "height_cells": 10},
                    "expected_revision": phase["campaign_revision"],
                    "idempotency_key": "start-combat",
                },
            )
            end_actor_turn = await world.call(
                "combat_end_turn",
                {
                    "campaign_id": world.cid,
                    "actor_id": world.aid,
                    "expected_revision": started["campaign_revision"],
                    "idempotency_key": "end-ram-actor-turn",
                },
            )
            help_action = await world.call(
                "combat_common_action",
                {
                    "campaign_id": world.cid,
                    "actor_id": world.helper_id,
                    "target_id": world.aid,
                    "action": "help",
                    "payload": {"kind": "task", "action": "break_door", "ability": "strength"},
                    "expected_revision": end_actor_turn["campaign_revision"],
                    "idempotency_key": "help-ram-check",
                },
            )
            actor_turn = await world.call(
                "combat_end_turn",
                {
                    "campaign_id": world.cid,
                    "actor_id": world.helper_id,
                    "expected_revision": help_action["campaign_revision"],
                    "idempotency_key": "end-ram-helper-turn",
                },
            )
            campaign, actor = await world.snapshot()
            request = gear_request(
                world,
                campaign,
                actor,
                item_id="ram-1",
                intent="break_door",
                action_id="ram-break-door",
                target={"id": world.door["id"], "scene_id": world.door["scene_id"]},
                helper_actor_id=world.helper_id,
            )
            result = await world.call("adventuring_gear_action", request)
            assert result["success"] is True
            assert result["receipt"]["action_paid"] is True
            assert result["receipt"]["helper_actor_id"] == world.helper_id
            assert result["receipt"]["rule_plan"]["check"]["bonus"] == 4
            assert result["check"]["helped_by"] == world.helper_id
            assert result["check"]["advantage_source"] == "help"
            assert len(result["check"]["rolls"]) == 2
            assert result["target_object"]["state"]["state"] == "open"
            assert await world.call("adventuring_gear_action", request) == result
            final_campaign, _ = await world.snapshot()
            assert final_campaign["revision"] == actor_turn["campaign_revision"] + 1
            combatants = final_campaign["state"]["combat"]["combatants"]
            actor_combatant = next(item for item in combatants if item["actor_id"] == world.aid)
            helper_combatant = next(
                item for item in combatants if item["actor_id"] == world.helper_id
            )
            assert actor_combatant["turn_budget"]["main_action"] == 0
            assert "helping" not in helper_combatant.get("turn_flags", {})
        finally:
            world.close()

    asyncio.run(run())
