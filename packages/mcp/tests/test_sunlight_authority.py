"""Sunlight source review through the public Runtime and durable local Host."""

import asyncio
from copy import deepcopy
from dataclasses import replace

import pytest
from sagasmith_core.state import StateMutationService
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd_runtime.application import create_runtime
from sagasmith_dnd_runtime.operations import RequestIdentity

from tests.test_source_object_authority import EXCERPT, setup
from tests.test_structured_spell_mcp import _slot, _spell

SUNLIGHT_EXCERPT = (
    "The stone wall borders a courtyard in direct sunlight. "
    "The archway shades anyone standing beneath it."
)


async def invoke(world, name, arguments, principal="system:local"):
    return await world.runtime.execute(name, arguments, context=RequestIdentity(principal))


async def drow_world(tmp_path, *, combat=False, local=False, spell=False):
    world = await setup(tmp_path, source_description=EXCERPT + "\n\n" + SUNLIGHT_EXCERPT)
    try:
        _, actor = await world.snapshot()
        applied = await world.call(
            "character_content_apply",
            {
                "character_id": world.aid,
                "artifact_id": "dnd5e.content.standard2014.species.drow",
                "selection": {},
                "expected_revision": actor["revision"],
                "idempotency_key": "drow",
            },
        )
        assert applied["sheet"]["progression"]["species"] == "Drow"
        feature = next(
            f
            for f in applied["sheet"]["content"]["features"]
            if f["name"] == "Sunlight Sensitivity"
        )
        assert feature["choices"]["source_trait"]["scope"] == "self_or_subject"
        if spell:
            _, caster = await world.snapshot()
            caster_sheet = deepcopy(caster["sheet"])
            card = _spell("Scorching Ray", 2, casting_time="1 action", range_ft=120)
            caster_sheet["content"]["spells"] = [card]
            caster_sheet["spellcasting"].update(ability="intelligence", spell_slots=_slot(2))
            await world.call(
                "character_sheet_replace",
                {
                    "character_id": world.aid,
                    "sheet": caster_sheet,
                    "expected_revision": caster["revision"],
                    "idempotency_key": "caster",
                },
            )
            world.spell_id = card["id"]
        sheet = default_character_sheet()
        sheet["combat"]["hp"] = {"value": 100, "max": 100, "temp": 0}
        target = await world.call(
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": world.cid, "name": "Target", "sheet": sheet},
                "idempotency_key": "target",
            },
        )
        world.target = target["id"]
        campaign, _ = await world.snapshot()
        await world.call(
            "game_phase",
            {
                "campaign_id": world.cid,
                "action": "set",
                "tool_profile": "play",
                "expected_revision": campaign["revision"],
                "idempotency_key": "play",
            },
        )
        if combat:
            campaign, _ = await world.snapshot()
            await world.call(
                "combat_start",
                {
                    "campaign_id": world.cid,
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 12, "height_cells": 12},
                    "scene_id": world.source["scene_id"],
                    "participant_ids": [world.aid, world.target],
                    "participant_config": [
                        {
                            "actor_id": world.aid,
                            "initiative": 20,
                            "tie_breaker": 0,
                            "position": {"x": 0, "y": 0},
                        },
                        {
                            "actor_id": world.target,
                            "initiative": 10,
                            "tie_breaker": 1,
                            "position": {"x": 3, "y": 0},
                        },
                    ],
                    "expected_revision": campaign["revision"],
                    "idempotency_key": "combat",
                },
            )
        if local:
            world.close()
            world.config = replace(
                world.config, local_authority=True, bound_principal_id="system:local"
            )
            world.runtime = create_runtime(world.config)
        return world
    except BaseException:
        world.close()
        raise


async def sunlight(world, *, observer=False, subject=True, sight=True, kind="actor", local=False):
    campaign, actor = await world.snapshot()
    result = {
        "subject": {
            "kind": kind,
            "id": world.target if kind == "actor" else world.profile["id"],
            "scene_id": world.source["scene_id"],
        },
        "actor_in_direct_sunlight": observer,
        "subject_in_direct_sunlight": subject,
        "relies_on_sight": sight,
        "ruling": {
            "source_ref": world.source,
            "source_excerpt": SUNLIGHT_EXCERPT,
            "reason": (
                "DM places the observer in shade and the named subject in direct sunlight "
                "in this source scene."
            ),
        },
    }
    if not local:
        branches = await world.call("branch_query", {"campaign_id": world.cid})
        result["binding"] = {
            "campaign_id": world.cid,
            "branch_id": branches[0]["id"],
            "campaign_revision": campaign["revision"],
            "actor_id": world.aid,
            "actor_revision": actor["revision"],
        }
    return result


def test_drow_build_attack_receipt_survives_restart_and_replay(tmp_path):
    async def run():
        world = await drow_world(tmp_path, combat=True)
        try:
            action = {"weapon_id": "bow", "context": {"sunlight": await sunlight(world)}}
            arguments = {
                "campaign_id": world.cid,
                "actor_id": world.aid,
                "target_id": world.target,
                "action": action,
            }
            plan = await invoke(world, "combat_preflight_attack", arguments)
            assert plan["disadvantage"]
            assert plan["sunlight_context"]["receipt"]
            campaign, _ = await world.snapshot()
            args = {
                **arguments,
                "action": {
                    "weapon_id": "bow",
                    "context": {
                        "sunlight": plan["sunlight_context"],
                    },
                },
                "expected_revision": campaign["revision"],
                "idempotency_key": "hit",
            }
            world.close()
            world.runtime = create_runtime(world.config)
            result = await invoke(world, "combat_resolve_attack", args)
            assert len(result["result"]["rolls"]) == 2
            assert any(
                r["mechanic_id"] == "dnd5e.core.trait.sunlight_sensitivity"
                for r in result["result"]["rule_receipts"]
            )
            after = await world.snapshot()
            world.close()
            world.runtime = create_runtime(world.config)
            assert await invoke(world, "combat_resolve_attack", args) == result
            assert await world.snapshot() == after
        finally:
            world.close()

    asyncio.run(run())


@pytest.mark.parametrize("combat,sight", [(False, True), (False, False), (True, True)])
def test_perception_declares_subject_and_sight_before_payment(tmp_path, combat, sight):
    async def run():
        world = await drow_world(tmp_path, combat=combat)
        try:
            campaign, _ = await world.snapshot()
            fields = {"actor_id": world.aid, "kind": "check", "ability": "perception", "dc": 10}
            args = {
                "campaign_id": world.cid,
                "expected_revision": campaign["revision"],
                "idempotency_key": "perceive",
            }
            name = "combat_check" if combat else "character_check"
            args.update(fields if combat else {"action": "check", "payload": fields})
            if combat:
                args["action"] = "search"
            before = await world.snapshot()
            pending = await invoke(world, name, args)
            assert pending["status"] == "pending_ruling"
            assert pending["committed"] is False
            assert await world.snapshot() == before
            review = await sunlight(world, sight=sight)
            if not sight:
                review.pop("actor_in_direct_sunlight")
                review.pop("subject_in_direct_sunlight")
            (args if combat else args["payload"])["rule_facts"] = {"sunlight": review}
            result = await invoke(world, name, args)
            assert len(result["result"]["rolls"]) == (2 if sight else 1)
            assert await invoke(world, name, args) == result
        finally:
            world.close()

    asyncio.run(run())


def test_local_host_binds_object_sunlight_without_model_metadata(tmp_path):
    async def run():
        world = await drow_world(tmp_path, local=True)
        try:
            args = await world.arguments(
                "sunlit-object", sunlight=await sunlight(world, kind="object", local=True)
            )
            args.pop("expected_revision")
            args["payload"].pop("expected_campaign_revision")
            result = await world.call("character_action", args)
            assert len(result["attack"]["rolls"]) == 2
            world.close()
            world.runtime = create_runtime(world.config)
            assert await world.call("character_action", args) == result
        finally:
            world.close()

    asyncio.run(run())


@pytest.mark.parametrize("local,sight", [(False, True), (True, True), (True, False)])
def test_passive_sunlight_reuses_source_authority_and_local_replay(tmp_path, local, sight):
    async def run():
        world = await drow_world(tmp_path, local=local)
        try:
            campaign, _ = await world.snapshot()
            args = {
                "campaign_id": world.cid, "action": "passive", "idempotency_key": "passive-sun",
                "payload": {
                    "actor_id": world.aid, "ability": "perception",
                    "rule_facts": {"sunlight": await sunlight(world, sight=sight, local=local)},
                    "task": {"mode": "exploration", "source_ref": world.source,
                             "source_excerpt": SUNLIGHT_EXCERPT,
                             "reason": "DM compares passive observation in the reviewed courtyard.",
                             "dc": 10},
                },
            }
            if not local:
                args["expected_revision"] = campaign["revision"]
            result = await invoke(world, "character_check", args)
            assert result["status"] == "committed"
            assert result["result"]["passive_adjustment"] == (-5 if sight else 0)
            assert result["result"]["rolls"] == []
            assert "random_stream_receipt" not in result
            after, _ = await world.snapshot()
            assert after["state"]["random_stream"] == campaign["state"]["random_stream"]
            world.close()
            world.runtime = create_runtime(world.config)
            replay = await invoke(world, "character_check", args)
            # Local timing/queue metrics describe this dispatch, while every
            # committed game-state and audience field stays byte-for-byte equal.
            assert {key: replay[key] for key in result if key != "local_execution"} == {
                key: value for key, value in result.items() if key != "local_execution"
            }
            if local:
                assert replay["local_execution"]["replayed"] is True
                assert replay["receipt_is_current"] is True
        finally:
            world.close()

    asyncio.run(run())


def test_sunlight_authority_rejects_forgery_and_expires_after_movement(tmp_path):
    async def run():
        world = await drow_world(tmp_path, combat=True)
        try:
            for scope, payload in [
                ("campaign", {"role": "player"}),
                ("actor", {"actor_id": world.aid, "can_control": True}),
            ]:
                await world.call(
                    "access_grant",
                    {
                        "scope": scope,
                        "campaign_id": world.cid,
                        "principal_id": "user:player",
                        "payload": payload,
                    },
                )
            review = await sunlight(world)
            base = {"campaign_id": world.cid, "actor_id": world.aid, "target_id": world.target}
            before = await world.snapshot()
            invalid = []
            for key, value in [
                ("disadvantage", False),
                ("actor_in_direct_sunlight", "false"),
                ("facts", {"actor_in_direct_sunlight": False}),
                ("subject", {**review["subject"], "id": world.aid}),
                ("binding", {**review["binding"], "branch_id": "another-branch"}),
                (
                    "ruling",
                    {**review["ruling"], "source_excerpt": "Invented private source excerpt."},
                ),
            ]:
                invalid.append({**deepcopy(review), key: value})
            for forged in invalid:
                try:
                    response = await invoke(
                        world,
                        "combat_preflight_attack",
                        {
                            **base,
                            "action": {"weapon_id": "bow", "context": {"sunlight": forged}},
                        },
                    )
                except (ValueError, PermissionError):
                    pass
                else:
                    assert response["status"] == "pending_ruling"
                assert await world.snapshot() == before
            with pytest.raises(PermissionError, match="signed DM receipt"):
                await invoke(
                    world,
                    "combat_preflight_attack",
                    {
                        **base,
                        "action": {"weapon_id": "bow", "context": {"sunlight": review}},
                    },
                    principal="user:player",
                )
            plan = await invoke(
                world,
                "combat_preflight_attack",
                {
                    **base,
                    "action": {"weapon_id": "bow", "context": {"sunlight": review}},
                },
            )
            signed = plan["sunlight_context"]
            player_plan = await invoke(
                world,
                "combat_preflight_attack",
                {
                    **base,
                    "action": {"weapon_id": "bow", "context": {"sunlight": signed}},
                },
                principal="user:player",
            )
            assert player_plan["opaque"]
            assert "sunlight_context" not in player_plan
            forged = deepcopy(signed)
            forged["receipt"]["facts"]["subject_in_direct_sunlight"] = False
            with pytest.raises(ValueError, match="signature"):
                await invoke(
                    world,
                    "combat_preflight_attack",
                    {
                        **base,
                        "action": {"weapon_id": "bow", "context": {"sunlight": forged}},
                    },
                )
            assert await world.snapshot() == before
            await invoke(
                world,
                "combat_movement",
                {
                    "campaign_id": world.cid,
                    "actor_id": world.aid,
                    "action": "move",
                    "payload": {"distance": 5, "destination": {"x": 0, "y": 1}},
                    "expected_revision": before[0]["revision"],
                    "idempotency_key": "move-to-shade",
                },
            )
            moved = await world.snapshot()
            stale = await invoke(
                world,
                "combat_resolve_attack",
                {
                    **base,
                    "action": {"weapon_id": "bow", "context": {"sunlight": signed}},
                    "expected_revision": moved[0]["revision"],
                    "idempotency_key": "stale-light",
                },
            )
            assert stale["status"] == "pending_ruling"
            assert await world.snapshot() == moved
            fresh = await sunlight(world, observer=False, subject=False)
            current = await invoke(
                world,
                "combat_preflight_attack",
                {
                    **base,
                    "action": {"weapon_id": "bow", "context": {"sunlight": fresh}},
                },
            )
            assert not current["disadvantage"]
        finally:
            world.close()

    asyncio.run(run())


def test_reviewed_perception_actor_cas_rolls_back_rng_and_search_action(tmp_path, monkeypatch):
    async def run():
        world = await drow_world(tmp_path, combat=True)
        try:
            before = await world.snapshot()
            args = {
                "campaign_id": world.cid,
                "actor_id": world.aid,
                "kind": "check",
                "ability": "perception",
                "action": "search",
                "dc": 10,
                "rule_facts": {"sunlight": await sunlight(world)},
                "expected_revision": before[0]["revision"],
                "idempotency_key": "search",
            }
            original = StateMutationService.replace
            attempted = []

            def conflict(service, campaign_id, **kwargs):
                updates = kwargs["character_updates"]
                assert len(updates) == 1
                attempted.append(True)
                kwargs["character_updates"] = [replace(updates[0], expected_revision=-1)]
                return original(service, campaign_id, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(StateMutationService, "replace", conflict)
                with pytest.raises(ValueError, match="revision conflict"):
                    await invoke(world, "combat_check", args)
            assert attempted
            assert await world.snapshot() == before
            result = await invoke(world, "combat_check", args)
            assert result["status"] == "committed"
            assert len(result["result"]["rolls"]) == 2
            assert await invoke(world, "combat_check", args) == result
        finally:
            world.close()

    asyncio.run(run())


@pytest.mark.parametrize("spell", [False, True])
def test_local_ready_refreshes_only_sunlight_without_repaying_or_replacing_source(tmp_path, spell):
    async def run():
        world = await drow_world(tmp_path, combat=True, local=True, spell=spell)
        try:

            async def ready(action, payload, key):
                return await world.call(
                    "combat_ready",
                    {
                        "campaign_id": world.cid,
                        "action": action,
                        "payload": payload,
                        "idempotency_key": key,
                    },
                )

            if spell:
                review = await sunlight(world, local=True)
                armed = await ready(
                    "ready_spell",
                    {
                        "actor_id": world.aid,
                        "spell_id": world.spell_id,
                        "trigger": "the bell rings",
                        "declaration": {
                            "attacks": [
                                {
                                    "target_id": world.target,
                                    "context": {"sunlight": deepcopy(review)},
                                }
                                for _ in range(3)
                            ]
                        },
                    },
                    "arm",
                )
            else:
                armed = await invoke(
                    world,
                    "combat_common_action",
                    {
                        "campaign_id": world.cid,
                        "actor_id": world.aid,
                        "action": "ready",
                        "trigger": "the bell rings",
                        "payload": {
                            "action": "attack",
                            "target_id": world.target,
                            "attack": {"weapon_id": "bow"},
                        },
                        "idempotency_key": "arm",
                    },
                )
            await invoke(
                world,
                "combat_end_turn",
                {
                    "campaign_id": world.cid,
                    "actor_id": world.aid,
                    "idempotency_key": "end",
                },
            )
            kind = "spell" if spell else "action"
            triggered = await ready(
                f"trigger_{kind}",
                {
                    "readied_id": armed["combat"]["readied"][0]["id"],
                    "event": "the bell rings",
                },
                "trigger",
            )
            payload = {
                "actor_id": world.aid,
                "choice_id": triggered["combat"]["pending"][0]["id"],
                "release": True,
            }
            before = await world.snapshot()
            pending = await ready(f"resolve_{kind}", payload, "missing-light")
            assert pending["status"] == "pending_ruling"
            assert await world.snapshot() == before
            review = await sunlight(world, local=True)
            payload.update(
                {"sunlight_contexts": [deepcopy(review) for _ in range(3)]}
                if spell
                else {"sunlight": review}
            )
            result = await ready(f"resolve_{kind}", payload, "release")
            assert len(result["result"]["rolls"]) == 2
            assert result["combat"]["combatants"][0]["turn_budget"]["reaction"] == 0
            after = await world.snapshot()
            world.close()
            world.runtime = create_runtime(world.config)
            assert await ready(f"resolve_{kind}", payload, "release") == result
            assert await world.snapshot() == after
            if spell:
                resolution = next(iter(result["combat"]["spell_resolutions"].values()))
                assert resolution["remaining_attacks"] == 2
                args = {
                    "campaign_id": world.cid,
                    "actor_id": world.aid,
                    "target_id": world.target,
                    "idempotency_key": "replace-ray",
                    "action": {
                        "spell_resolution_id": resolution["id"],
                        "context": {
                            "advantage": True,
                            "sunlight": await sunlight(world, local=True),
                        },
                    },
                }
                with pytest.raises(Exception, match="stored target and context"):
                    await invoke(world, "combat_resolve_attack", args)
                assert await world.snapshot() == after
                for index in range(2):
                    args = {
                        "campaign_id": world.cid,
                        "actor_id": world.aid,
                        "target_id": world.target,
                        "idempotency_key": f"ray-{index}",
                        "action": {
                            "spell_resolution_id": resolution["id"],
                            "context": {
                                "sunlight": await sunlight(world, local=True),
                            },
                        },
                    }
                    result = await invoke(world, "combat_resolve_attack", args)
                    assert len(result["result"]["rolls"]) == 2
                    settled = await world.snapshot()
                    replay = await invoke(world, "combat_resolve_attack", args)
                    telemetry = {"local_execution", "receipt_is_current"}
                    assert {k: v for k, v in replay.items() if k not in telemetry} == {
                        k: v for k, v in result.items() if k not in telemetry
                    }
                    assert await world.snapshot() == settled
                assert result["combat"]["pending"] == []
                _, caster = await world.snapshot()
                assert (
                    caster["sheet"]["spellcasting"]["spell_slots"]
                    == (before[1]["sheet"]["spellcasting"]["spell_slots"])
                )
        finally:
            world.close()

    asyncio.run(run())
