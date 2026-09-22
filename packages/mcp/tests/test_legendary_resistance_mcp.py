import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from sagasmith_core.state import StateMutationService
from sagasmith_dnd.legendary_resistance import feature
from sagasmith_dnd.statblocks import parse_2014_statblock
from sagasmith_dnd_runtime.application import create_runtime

from sagasmith_dnd_mcp.server import close_server, create_server
from tests.test_source_object_authority import setup


def dragon_sheet():
    path = Path(__file__).parents[3] / (
        "skills/full/skills/dnd-dm/srd/references-2014-en/10_Monsters/Monsters_Each/"
        "Adult_Black_Dragon_(Chromatic).md"
    )
    return parse_2014_statblock(path.read_text(encoding="utf-8"), source_key="black-dragon",
                               rule_refs=["bundled:srd2014/" + path.name]).sheet


def test_bundled_dragon_public_statblock_import_materializes_source_bound_resistance(tmp_path):
    from tests.test_structured_spell_mcp import _call, _config

    async def run():
        path = Path(__file__).parents[3] / (
            "skills/full/skills/dnd-dm/srd/references-2014-en/10_Monsters/Monsters_Each/"
            "Adult_Black_Dragon_(Chromatic).md"
        )
        server = create_server(replace(_config(tmp_path), rule_import_roots=(path.parent,)))
        try:
            campaign = await _call(server, "campaign_create", {
                "name": "Source Dragon", "edition": "2014", "idempotency_key": "campaign",
            })
            staged = await _call(server, "rulebook_draft", {
                "campaign_id": campaign["id"], "action": "start", "payload": {
                    "source_path": str(path), "source_key": "srd/black-dragon",
                    "title": "Adult Black Dragon", "edition": "2014", "publication_id": "srd2014",
                }, "idempotency_key": "stage",
            })
            for key in ("inspect", "ingest"):
                imported = await _call(server, "rulebook_draft", {
                    "campaign_id": campaign["id"], "action": "get",
                    "payload": {"job_id": staged["job"]["id"]}, "idempotency_key": key,
                })
            request = {"mode": "statblock", "payload": {
                "campaign_id": campaign["id"], "source_id": imported["source_id"],
                "name": "Source Dragon", "character_type": "monster",
            }, "idempotency_key": "dragon"}
            created = await _call(server, "character_create_from", request)
            entry = feature(created["character"]["sheet"])
            assert entry and entry["uses"]["value"] == 3
            assert entry["source_key"] == "rule-source:srd/black-dragon"
            assert entry["rule_refs"] and "manual_ruling" not in entry["choices"]
            assert not any("Legendary Resistance" in w for w in created["statblock"]["warnings"])
            assert await _call(server, "character_create_from", request) == created
        finally:
            close_server(server)
    asyncio.run(run())


async def prepare(tmp_path, *, combat=True, dying=False, focus=False):
    world = await setup(tmp_path, random_seed="death-1" if dying else "legendary-resistance")
    sheet = dragon_sheet()
    if dying:
        sheet["combat"]["hp"]["value"] = 0
        sheet["combat"]["death_saves"] = {"successes": 2, "failures": 2}
        sheet["conditions"] = ["unconscious"]
    if focus:
        sheet["effects"] = [{
            "id": "focus", "name": "Focus", "kind": "concentration", "active": True,
            "concentration": True, "duration": {"period": "hour", "remaining": 1}, "changes": [],
        }]
    actor = await world.call("character_create_from", {"mode": "direct", "payload": {
        "campaign_id": world.cid, "name": "Black Dragon", "sheet": sheet,
        "character_type": "monster",
    }, "idempotency_key": "dragon"})
    world.dragon = actor["id"]
    world.close()
    world.config = replace(world.config, local_authority=True, bound_principal_id="system:local")
    world.runtime = create_runtime(world.config)
    await world.call("game_phase", {"campaign_id": world.cid, "action": "set",
                                    "tool_profile": "play", "idempotency_key": "play"})
    if not combat:
        return world
    started = await world.call("combat_start", {"campaign_id": world.cid,
                                      "participant_ids": [world.dragon, world.aid],
                                      "participant_config": [
                                          {"actor_id": world.dragon, "initiative": 20},
                                          {"actor_id": world.aid, "initiative": 10},
                                      ],
                                      "positioning_mode": "agent", "idempotency_key": "start"})
    assert started.get("combat", {}).get("active"), started
    return world


async def dragon_state(world):
    return await world.call("character_query", {"view": "get", "payload": {
        "character_id": world.dragon,
    }})


@pytest.mark.parametrize("accept", [True, False])
def test_failed_save_owned_choice_restart_no_reroll_and_exact_retry(tmp_path, accept):
    async def run():
        world = await prepare(tmp_path)
        try:
            before = await dragon_state(world)
            args = {"campaign_id": world.cid, "actor_id": world.dragon, "kind": "save",
                    "ability": "wisdom", "dc": 40, "idempotency_key": "failed-save"}
            offered = await world.call("combat_check", args)
            assert offered["status"] == "pending_save"
            snapshot = (await world.snapshot())[0]
            position = snapshot["state"]["random_stream"]["position"]
            current = await dragon_state(world)
            assert current["sheet"] == before["sheet"]
            assert snapshot["pending_save"]["id"] == offered["choice"]["id"]
            with pytest.raises(Exception, match="Legendary Resistance"):
                await world.call("combat_end_turn", {"campaign_id": world.cid,
                                                     "idempotency_key": "early"})
            world.close()
            world.runtime = create_runtime(world.config)
            choice = {"character_id": world.dragon, "action": "legendary_resistance",
                      "payload": {"choice_id": offered["choice"]["id"], "accept": accept},
                      "idempotency_key": "choose"}
            result = await world.call("character_state_change", choice)
            assert result["status"] == "committed"
            current = await dragon_state(world)
            assert feature(current["sheet"])["uses"]["value"] == 3 - int(accept)
            after = (await world.snapshot())[0]
            assert "pending_save" not in after
            assert after["state"]["random_stream"]["position"] == position
            resolved = result["operation_result"]["result"]
            assert resolved["success"] is accept
            assert resolved["natural"] == offered["choice"]["result"]["natural"]
            replay = await world.call("character_state_change", choice)
            assert replay["operation_result"] == result["operation_result"]
            original_replay = await world.call("combat_check", args)
            assert original_replay["choice"] == offered["choice"]
            assert (await dragon_state(world))["revision"] == current["revision"]
        finally:
            world.close()
    asyncio.run(run())


def test_noncombat_death_save_and_returned_card_are_settled_once(tmp_path):
    async def run():
        world = await prepare(tmp_path, combat=False, dying=True)
        try:
            pending = await world.call("character_state_change", {
                "character_id": world.dragon, "action": "death_save", "payload": {},
                "idempotency_key": "death-save",
            })
            assert pending["status"] == "pending_save", pending
            original = pending["choice"]["result"]
            assert original["natural"] == 9 and original["outcome"] == "dead"
            assert "dead" not in (await dragon_state(world))["sheet"]["conditions"]
            result = await world.call("character_state_change", {
                "character_id": world.dragon, "action": "legendary_resistance",
                "payload": {"choice_id": pending["choice"]["id"], "accept": True},
                "idempotency_key": "live",
            })
            card = await dragon_state(world)
            assert "stable" in card["sheet"]["conditions"]
            assert card["sheet"]["combat"]["hp"]["value"] == 0
            returned = result["operation_result"]["result"]["character"]
            assert feature(returned["sheet"])["uses"]["value"] == 2
        finally:
            world.close()
    asyncio.run(run())


@pytest.mark.parametrize("accept", [True, False])
def test_concentration_effect_survives_until_legendary_resistance_decision(tmp_path, accept):
    async def run():
        world = await prepare(tmp_path, focus=True)
        try:
            damaged = await world.call("combat_hp_change", {
                "campaign_id": world.cid, "target_id": world.dragon, "action": "damage",
                "payload": {"parts": [{"amount": 80, "damage_type": "force"}]},
                "idempotency_key": "damage",
            })
            window = next(p for p in damaged["combat"]["pending"] if p["kind"] == "concentration")
            pending = await world.call("combat_concentration_check", {
                "campaign_id": world.cid, "target_id": world.dragon, "dc": window["dc"],
                "effect_ids": window["effect_ids"], "idempotency_key": "concentration",
            })
            assert pending["status"] == "pending_save"
            card = await dragon_state(world)
            assert next(e for e in card["sheet"]["effects"] if e["id"] == "focus")["active"]
            result = await world.call("character_state_change", {
                "character_id": world.dragon, "action": "legendary_resistance",
                "payload": {"choice_id": pending["choice"]["id"], "accept": accept},
                "idempotency_key": "choose",
            })
            assert result["status"] == "committed"
            card = await dragon_state(world)
            effect = next(e for e in card["sheet"]["effects"] if e["id"] == "focus")
            assert effect["active"] is accept
            assert feature(card["sheet"])["uses"]["value"] == 3 - int(accept)
        finally:
            world.close()
    asyncio.run(run())


def test_current_dm_can_finish_a_save_after_initiating_dm_access_is_revoked(tmp_path):
    async def run():
        world = await prepare(tmp_path)
        try:
            await world.call("access_grant", {"campaign_id": world.cid, "scope": "campaign",
                                               "principal_id": "dm:old", "payload": {"role": "dm"}})
            world.close()
            world.config = replace(world.config, local_authority=False, bound_principal_id=None)
            world.runtime = create_runtime(world.config)
            campaign = (await world.snapshot())[0]
            request = {
                "campaign_id": world.cid, "actor_id": world.dragon, "kind": "save",
                "ability": "wisdom", "dc": 40, "expected_revision": campaign["revision"],
                "idempotency_key": "old-dm-save",
            }
            pending = await world.call("combat_check", request, principal="dm:old")
            await world.call("access_grant", {
                "campaign_id": world.cid, "scope": "campaign", "principal_id": "dm:old",
                "payload": {"role": "player"},
            })
            with pytest.raises(Exception, match="(?i)idempotency|payload|different"):
                await world.call("combat_check", request, principal="dm:old")
            await world.call("access_revoke", {"campaign_id": world.cid, "principal_id": "dm:old"})
            actor = await dragon_state(world)
            settled = await world.call("character_state_change", {
                "character_id": world.dragon, "action": "legendary_resistance",
                "payload": {"choice_id": pending["choice"]["id"], "accept": True},
                "expected_revision": actor["revision"], "idempotency_key": "owner-finish",
            })
            assert settled["status"] == "committed"
            assert feature((await dragon_state(world))["sheet"])["uses"]["value"] == 2
        finally:
            world.close()
    asyncio.run(run())


@pytest.mark.parametrize("spell_name", ["Fireball", "Hypnotic Pattern"])
def test_multitarget_spell_defers_effects_and_payment_until_owned_saves_settle(
    tmp_path, spell_name,
):
    from tests.test_structured_spell_mcp import (
        _campaign_actor_snapshot,
        _campaign_with_combat,
        _config,
        _equipped_caster,
        _hypnotic_pattern,
        _raw,
        _slot,
        _spell,
    )

    async def run():
        config = _config(tmp_path)
        server = create_server(config)
        try:
            caster = _equipped_caster()
            caster["spellcasting"].update(ability="wisdom", save_dc_override=30,
                                          spell_slots=_slot(3))
            spell = (_hypnotic_pattern() if spell_name == "Hypnotic Pattern" else
                     _spell("Fireball", 3, casting_time="1 action", range_ft=150))
            caster["content"]["spells"] = [spell]
            cid, revision, actors = await _campaign_with_combat(server, [
                ("Caster", caster), ("Black Dragon", dragon_sheet()),
                ("Second Black Dragon", dragon_sheet()),
            ], positions=[(0, 0), (4, 0), (4, 4)])
            ids = [a["id"] for a in actors]
            before = await _campaign_actor_snapshot(server, cid, ids)
            declaration = ({"origin": {"x": 4, "y": 0},
                            "cube": {"min": {"x": 4, "y": 0}, "max": {"x": 9, "y": 5}}}
                           if spell_name == "Hypnotic Pattern" else {
                               "origin": {"x": 6, "y": 3}, "target_contexts": [
                                   {"target_id": aid, "cover": "none"} for aid in ids[1:]
                               ],
                           })
            offered = await _raw(server, "combat_cast_spell", {
                "campaign_id": cid, "actor_id": ids[0], "spell_id": spell["id"],
                "cast_level": 3, "declaration": declaration, "expected_revision": revision,
                "idempotency_key": "cast",
            })
            assert offered["status"] == "pending_save", offered
            recorded = {}
            for index, accept in enumerate([True, False]):
                pending = offered["choice"]
                recorded[pending["actor_id"]] = (accept, pending["result"])
                snapshot = await _campaign_actor_snapshot(server, cid, ids)
                assert [a["sheet"] for a in snapshot["actors"]] == [
                    a["sheet"] for a in before["actors"]
                ]
                actor = next(a for a in snapshot["actors"] if a["id"] == pending["actor_id"])
                position = snapshot["campaign"]["state"]["random_stream"]["position"]
                close_server(server)
                server = create_server(config)
                offered = await _raw(server, "character_state_change", {
                    "character_id": actor["id"], "action": "legendary_resistance",
                    "payload": {"choice_id": pending["id"], "accept": accept},
                    "expected_revision": actor["revision"], "idempotency_key": f"choice-{index}",
                })
            assert offered["status"] == "committed", offered
            after = await _campaign_actor_snapshot(server, cid, ids)
            assert after["campaign"]["state"]["random_stream"]["position"] == position
            assert after["actors"][0]["sheet"]["spellcasting"]["spell_slots"]["3"]["value"] == 0
            assert next(a for a in after["campaign"]["state"]["combat"]["combatants"]
                        if a["actor_id"] == ids[0])["turn_budget"]["main_action"] == 0
            result = offered["operation_result"]["result"]
            for target in result["targets"]:
                accept, original = recorded[target["target_id"]]
                assert target["save"]["natural"] == original["natural"]
                assert target["save"]["success"] is accept
                actor = next(a for a in after["actors"] if a["id"] == target["target_id"])
                assert feature(actor["sheet"])["uses"]["value"] == 3 - int(accept)
                if spell_name == "Hypnotic Pattern":
                    assert ("charmed" in actor["sheet"]["conditions"]) is not accept
                else:
                    damage = result["damage_roll"]["total"]
                    assert actor["sheet"]["combat"]["hp"]["value"] == (
                        dragon_sheet()["combat"]["hp"]["value"]
                        - (damage // 2 if accept else damage)
                    )
        finally:
            close_server(server)
    asyncio.run(run())


def test_offer_and_resumption_cas_failures_rollback_dice_uses_and_receipts(tmp_path, monkeypatch):
    async def run():
        world = await prepare(tmp_path)
        original = StateMutationService.replace
        args = {"campaign_id": world.cid, "actor_id": world.dragon, "kind": "save",
                "ability": "wisdom", "dc": 40, "idempotency_key": "failed-save"}
        try:
            for phase in ("offer", "resolve"):
                if phase == "resolve":
                    offered = await world.call("combat_check", args)
                    name = "character_state_change"
                    request = {"character_id": world.dragon, "action": "legendary_resistance",
                               "payload": {"choice_id": offered["choice"]["id"], "accept": True},
                               "idempotency_key": "resolve"}
                else:
                    name, request = "combat_check", args
                before = (await world.snapshot(), await dragon_state(world))

                def race(self, cid, **kwargs):
                    kwargs["character_updates"] = [
                        replace(row, expected_revision=-1) if row.character_id == world.dragon
                        else row for row in kwargs.get("character_updates", [])
                    ]
                    return original(self, cid, **kwargs)

                monkeypatch.setattr(StateMutationService, "replace", race)
                with pytest.raises(Exception, match="revision conflict"):
                    await world.call(name, request)
                assert (await world.snapshot(), await dragon_state(world)) == before
                monkeypatch.setattr(StateMutationService, "replace", original)
            settled = await world.call(name, request)
            assert settled["status"] == "committed"
        finally:
            world.close()
    asyncio.run(run())


@pytest.mark.parametrize("accept", [True, False])
def test_source_monster_semantic_save_resumes_the_paid_plan_once(tmp_path, accept):
    from tests.test_semantic_continuations_mcp import prepare as semantic_prepare
    from tests.test_semantic_plan_mcp import _call, _raw

    async def run():
        server, config, cid, actors, paid = await semantic_prepare(
            tmp_path, reaction=False, legendary_resistance=True,
        )
        try:
            source, target = [a["id"] for a in actors]
            commitment = paid["result"]["declaration"]["agent_resolution_commitment"]
            request = {"campaign_id": cid, "actor_id": source, "action": "execute_plan",
                       "payload": {"commitment": commitment},
                       "expected_revision": paid["campaign_revision"], "idempotency_key": "execute"}
            pending = await _raw(server, "combat_choice", request)
            assert pending["status"] == "pending_save", pending
            card = await _call(server, "character_query", {"view": "get", "payload": {
                "character_id": target,
            }})
            assert card["sheet"]["combat"]["hp"]["value"] == 100
            close_server(server)
            server = create_server(config)
            settled = await _raw(server, "character_state_change", {
                "character_id": target, "action": "legendary_resistance",
                "payload": {"choice_id": pending["choice"]["id"], "accept": accept},
                "expected_revision": card["revision"], "idempotency_key": "choose",
            })
            assert settled["status"] == "committed", settled
            card = await _call(server, "character_query", {"view": "get", "payload": {
                "character_id": target,
            }})
            hp = card["sheet"]["combat"]["hp"]["value"]
            assert hp == 100 if accept else 94 <= hp <= 99
            assert feature(card["sheet"])["uses"]["value"] == 3 - int(accept)
            replay = await _raw(server, "combat_choice", request)
            assert replay == pending
        finally:
            close_server(server)
    asyncio.run(run())


def test_choice_requires_actual_owner_and_hides_original_dm_reply(tmp_path):
    async def run():
        world = await prepare(tmp_path)
        try:
            for principal, actor in [("player:owner", world.dragon), ("player:other", world.aid)]:
                for scope, payload in [("campaign", {"role": "player"}),
                                       ("actor", {"actor_id": actor, "can_control": True})]:
                    await world.call("access_grant", {
                        "campaign_id": world.cid, "scope": scope,
                        "principal_id": principal, "payload": payload,
                    })
            world.close()
            world.config = replace(world.config, local_authority=False, bound_principal_id=None)
            world.runtime = create_runtime(world.config)
            campaign = (await world.snapshot())[0]
            pending = await world.call("combat_check", {
                "campaign_id": world.cid, "actor_id": world.dragon, "kind": "save",
                "ability": "wisdom", "dc": 40, "expected_revision": campaign["revision"],
                "idempotency_key": "fail",
            })
            actor = await dragon_state(world)
            request = {"character_id": world.dragon, "action": "legendary_resistance",
                       "payload": {"choice_id": pending["choice"]["id"], "accept": True},
                       "expected_revision": actor["revision"], "idempotency_key": "choose"}
            before = (await world.snapshot(), actor)
            with pytest.raises(Exception, match="control|access|permission"):
                await world.call("character_state_change", request, principal="player:other")
            with pytest.raises(Exception, match="another actor"):
                other = (await world.snapshot())[1]
                await world.call("character_state_change", {
                    **request, "character_id": world.aid, "expected_revision": other["revision"],
                }, principal="player:other")
            with pytest.raises(Exception, match="revision conflict"):
                await world.call("character_state_change", {
                    **request, "expected_revision": actor["revision"] - 1,
                }, principal="player:owner")
            assert (await world.snapshot(), await dragon_state(world)) == before
            public = await world.call("campaign_query", {"view": "get", "payload": {
                "campaign_id": world.cid,
            }}, principal="player:other")
            assert "result" not in public["pending_save"]
            assert "_saving_throw_continuation" not in str(public)
            settled = await world.call("character_state_change", request, principal="player:owner")
            assert settled["status"] == "committed" and "operation_result" not in settled
            replay = await world.call("character_state_change", request, principal="player:owner")
            assert replay == settled
        finally:
            world.close()
    asyncio.run(run())
