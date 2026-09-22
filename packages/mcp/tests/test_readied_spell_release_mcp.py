import asyncio
from copy import deepcopy

import pytest
from sagasmith_core.idempotency import IdempotencyService
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.random_stream import CampaignRandomStream, use_random_stream
from sagasmith_dnd.spells import CORE_MAGIC_MISSILE_MECHANIC_ID, CORE_MAGIC_MISSILE_SPELL_ID
from sagasmith_dnd.standard_spell_ids import CORE_BLADE_WARD_SPELL_ID
from test_structured_spell_mcp import (
    _call,
    _campaign_actor_snapshot,
    _campaign_with_combat,
    _config,
    _deterministic_rolls,
    _equipped_caster,
    _fly,
    _hypnotic_pattern,
    _raw,
    _shield,
    _slot,
    _spell,
    _standard_spell,
)

import sagasmith_dnd_mcp.server as server_module
from sagasmith_dnd_mcp.server import close_server, create_server


async def ready(server, cid, rev, action, payload, key):
    response = await _raw(
        server,
        "combat_ready",
        {
            "campaign_id": cid,
            "action": action,
            "payload": payload,
            "expected_revision": rev,
            "idempotency_key": key,
        },
    )
    return response["result"]


async def prepare(tmp_path, kind, *, shield=False):
    config = _config(tmp_path)
    server = create_server(config)
    caster = _equipped_caster()
    caster["combat"]["hp"].update(value=100, max=100)
    caster["abilities"]["intelligence"]["score"] = 18
    caster["spellcasting"].update(
        ability="intelligence", spell_slots={**_slot(1), **_slot(2), **_slot(3)}
    )
    if kind == "healing":
        spell = _spell("Cure Wounds", 1, casting_time="1 action", range_ft=5)
    elif kind == "saving_throw":
        spell = _spell("Sacred Flame", 0, casting_time="1 action", range_ft=60)
        caster["abilities"]["intelligence"]["score"] = 30
    elif kind == "spell_attack":
        spell = _spell("Scorching Ray", 2, casting_time="1 action", range_ft=120)
    elif kind == "hypnotic_pattern":
        spell = _hypnotic_pattern()
        caster["abilities"]["intelligence"]["score"] = 30
    elif kind == "blade_ward":
        spell = _standard_spell(CORE_BLADE_WARD_SPELL_ID)
    elif kind == "magic_missile":
        spell = _fly()
        spell.update(
            id=CORE_MAGIC_MISSILE_SPELL_ID,
            name="Magic Missile",
            level=1,
            mechanic_refs=[CORE_MAGIC_MISSILE_MECHANIC_ID],
        )
        spell["definition"].update(
            range={"kind": "distance", "normal_ft": 120, "long_ft": 120},
            duration={"kind": "instantaneous", "concentration": False},
        )
    else:
        spell = _fly()
    caster["content"]["spells"] = [spell]
    target = default_character_sheet()
    target["combat"]["hp"].update(value=30, max=100)
    target["combat"]["ac"]["override"] = 1
    target["abilities"]["wisdom"]["score"] = 1
    target["abilities"]["dexterity"]["score"] = 1
    if shield:
        target["combat"]["ac"]["override"] = 14
        target["content"]["spells"] = [_shield()]
        target["spellcasting"]["spell_slots"] = _slot(1)
    cid, rev, actors = await _campaign_with_combat(server, [("Caster", caster), ("Target", target)])
    aid, tid = [a["id"] for a in actors]
    declaration = {"target_id": tid}
    if kind == "spell_attack":
        declaration = {"attacks": [{"target_id": tid} for _ in range(3)]}
    elif kind == "fly":
        declaration = {"target_ids": [tid], "willing_target_ids": [tid]}
    elif kind == "hypnotic_pattern":
        declaration = {
            "origin": {"x": 1, "y": 0},
            "cube": {"min": {"x": 1, "y": 0}, "max": {"x": 6, "y": 5}},
        }
    elif kind in {"magic_missile", "blade_ward"}:
        declaration = {}
    armed = await ready(
        server,
        cid,
        rev,
        "ready_spell",
        {
            "actor_id": aid,
            "spell_id": spell["id"],
            "trigger": "the bell rings",
            "declaration": declaration,
            **(
                {"target_allocations": [{"target_id": tid, "darts": 3}]}
                if kind == "magic_missile"
                else {}
            ),
        },
        "arm",
    )
    advanced = await _raw(
        server,
        "combat_end_turn",
        {
            "campaign_id": cid,
            "actor_id": aid,
            "expected_revision": armed["campaign_revision"],
            "idempotency_key": "end",
        },
    )
    triggered = await ready(
        server,
        cid,
        advanced["campaign_revision"],
        "trigger_spell",
        {
            "readied_id": armed["readied"]["id"],
            "event": "the bell rings",
        },
        "trigger",
    )
    return server, config, cid, actors, spell, declaration, triggered


@pytest.mark.parametrize(
    "kind", ["healing", "saving_throw", "spell_attack", "fly", "magic_missile", "hypnotic_pattern"]
)
def test_release_executes_persisted_source_off_turn_once_after_restart(tmp_path, monkeypatch, kind):
    _deterministic_rolls(monkeypatch)

    async def exercise():
        server, config, cid, actors, spell, declaration, state = await prepare(tmp_path, kind)
        aid, tid = [a["id"] for a in actors]
        ids = [aid, tid]
        before = await _campaign_actor_snapshot(server, cid, ids)
        assert before["actors"][1]["sheet"]["combat"]["hp"]["value"] == 30
        assert not any(
            e.get("kind") == "fly" and e.get("active")
            for a in before["actors"]
            for e in a["sheet"]["effects"]
        )
        close_server(server)
        server = create_server(config)
        try:
            payload = {"actor_id": aid, "choice_id": state["choice"]["id"], "release": True}
            with pytest.raises(Exception, match="cannot replace"):
                await ready(
                    server,
                    cid,
                    state["campaign_revision"],
                    "resolve_spell",
                    {**payload, "declaration": {"target_id": aid}},
                    "replace",
                )
            with pytest.raises(Exception, match="revision conflict"):
                await ready(
                    server, cid, state["campaign_revision"] - 1, "resolve_spell", payload, "stale"
                )
            assert await _campaign_actor_snapshot(server, cid, ids) == before
            result = await ready(
                server, cid, state["campaign_revision"], "resolve_spell", payload, "release"
            )
            assert result["released"] is True
            assert result["declaration"] == declaration
            assert result["combat"]["readied"] == []
            assert result["combat"]["combatants"][0]["turn_budget"]["reaction"] == 0
            assert (
                await ready(
                    server, cid, state["campaign_revision"], "resolve_spell", payload, "release"
                )
                == result
            )
            after = await _campaign_actor_snapshot(server, cid, ids)
            assert (
                after["actors"][0]["sheet"]["spellcasting"]["spell_slots"]
                == before["actors"][0]["sheet"]["spellcasting"]["spell_slots"]
            )
            assert not any(
                e.get("kind") == "readied_spell" and e.get("active")
                for e in after["actors"][0]["sheet"]["effects"]
            )
            hp = after["actors"][1]["sheet"]["combat"]["hp"]["value"]
            if kind == "healing":
                assert hp > 30
            elif kind in {"saving_throw", "spell_attack", "magic_missile"}:
                assert hp < 30
            else:
                assert any(
                    e["active"] and e["concentration"]
                    for e in after["actors"][0]["sheet"]["effects"]
                )
                assert any(e["active"] for e in after["actors"][1]["sheet"]["effects"])
            receipts = await _call(
                server,
                "campaign_rules",
                {
                    "campaign_id": cid,
                    "action": "receipts",
                    "payload": {},
                },
            )
            assert any(r["mechanic_id"] == "dnd5e.core.ready.spell_release" for r in receipts)
            if kind == "spell_attack":
                resolution = next(iter(result["combat"]["spell_resolutions"].values()))
                assert resolution["remaining_attacks"] == 2
                with pytest.raises(Exception, match="stored target and context"):
                    await _raw(
                        server,
                        "combat_resolve_attack",
                        {
                            "campaign_id": cid,
                            "actor_id": aid,
                            "target_id": tid,
                            "action": {
                                "spell_resolution_id": resolution["id"],
                                "context": {"advantage": True},
                            },
                            "expected_revision": result["campaign_revision"],
                            "idempotency_key": "replace-ray",
                        },
                    )
                for index in range(2):
                    result = await _raw(
                        server,
                        "combat_resolve_attack",
                        {
                            "campaign_id": cid,
                            "actor_id": aid,
                            "target_id": tid,
                            "action": {"spell_resolution_id": resolution["id"], "context": {}},
                            "expected_revision": result["campaign_revision"],
                            "idempotency_key": f"ray-{index}",
                        },
                    )
                assert result["combat"]["pending"] == []
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_readied_rays_preserve_nested_shield_and_source_through_restart(tmp_path, monkeypatch):
    _deterministic_rolls(monkeypatch)

    async def exercise():
        server, config, cid, actors, _, _, state = await prepare(
            tmp_path, "spell_attack", shield=True
        )
        aid, tid = [a["id"] for a in actors]
        try:
            payload = {"actor_id": aid, "choice_id": state["choice"]["id"], "release": True}
            paused = await ready(
                server, cid, state["campaign_revision"], "resolve_spell", payload, "release"
            )
            assert paused["released"] is True
            assert paused["status"] == "pending_reaction"
            snapshot = await _campaign_actor_snapshot(server, cid, [aid, tid])
            assert snapshot["actors"][1]["sheet"]["combat"]["hp"]["value"] == 30
            assert not any(
                e["active"] and e["kind"] == "readied_spell"
                for e in snapshot["actors"][0]["sheet"]["effects"]
            )
            close_server(server)
            server = create_server(config)
            assert (
                await ready(
                    server, cid, state["campaign_revision"], "resolve_spell", payload, "release"
                )
                == paused
            )
            settled = await _raw(
                server,
                "combat_choice",
                {
                    "campaign_id": cid,
                    "actor_id": tid,
                    "action": "resolve_defense",
                    "payload": {
                        "choice_id": paused["choice"]["id"],
                        "selection": {"id": _shield()["id"], "cast_level": 1},
                    },
                    "expected_revision": paused["campaign_revision"],
                    "idempotency_key": "shield",
                },
            )
            settled = settled["result"]
            assert settled["result"]["hit"] is False
            resolution = next(iter(settled["combat"]["spell_resolutions"].values()))
            assert resolution["remaining_attacks"] == 2
            for index in range(2):
                settled = await _raw(
                    server,
                    "combat_resolve_attack",
                    {
                        "campaign_id": cid,
                        "actor_id": aid,
                        "target_id": tid,
                        "action": {"spell_resolution_id": resolution["id"], "context": {}},
                        "expected_revision": settled["campaign_revision"],
                        "idempotency_key": f"ray-{index}",
                    },
                )
            assert not settled["combat"]["pending"]
            snapshot = await _campaign_actor_snapshot(server, cid, [aid, tid])
            assert snapshot["actors"][1]["sheet"]["combat"]["hp"]["value"] == 30
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_release_attack_failure_rolls_back_holding_reaction_and_random_receipt(
    tmp_path, monkeypatch
):
    attempts = []
    actual = server_module.resolve_attack_damage

    def fail_once(*args, **kwargs):
        attempts.append(deepcopy(kwargs["attack"]))
        if len(attempts) == 1:
            raise RuntimeError("injected readied spell failure")
        return actual(*args, **kwargs)

    monkeypatch.setattr(server_module, "resolve_attack_damage", fail_once)

    async def exercise():
        server, _, cid, actors, _, _, state = await prepare(tmp_path, "spell_attack")
        try:
            ids = [a["id"] for a in actors]
            before = await _campaign_actor_snapshot(server, cid, ids)
            payload = {"actor_id": ids[0], "choice_id": state["choice"]["id"], "release": True}

            def stream():
                return use_random_stream(
                    CampaignRandomStream.from_campaign_state(
                        cid,
                        before["campaign"]["state"],
                        operation="combat_ready",
                        idempotency_key="release",
                        campaign_revision=state["campaign_revision"],
                    )
                )

            with stream(), pytest.raises(Exception, match="injected readied spell failure"):
                await ready(
                    server, cid, state["campaign_revision"], "resolve_spell", payload, "release"
                )
            assert await _campaign_actor_snapshot(server, cid, ids) == before
            with stream():
                result = await ready(
                    server, cid, state["campaign_revision"], "resolve_spell", payload, "release"
                )
            assert attempts[0] == attempts[1]
            assert result["random_stream_receipt"]["draw_count"] > 0
            assert (
                await ready(
                    server, cid, state["campaign_revision"], "resolve_spell", payload, "release"
                )
                == result
            )
            assert len(attempts) == 2
        finally:
            close_server(server)

    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["healing", "fly"])
def test_release_receipt_failure_rolls_back_caster_target_and_reaction(tmp_path, monkeypatch, kind):
    async def exercise():
        server, _, cid, actors, _, _, state = await prepare(tmp_path, kind)
        try:
            ids = [a["id"] for a in actors]
            before = await _campaign_actor_snapshot(server, cid, ids)
            payload = {"actor_id": ids[0], "choice_id": state["choice"]["id"], "release": True}
            remember = IdempotencyService.remember_write_in_session

            def fail_receipt(self, session, **kwargs):
                if kwargs.get("key") == "release":
                    raise RuntimeError("injected readied receipt failure")
                return remember(self, session, **kwargs)

            monkeypatch.setattr(IdempotencyService, "remember_write_in_session", fail_receipt)
            with pytest.raises(Exception, match="injected readied receipt failure"):
                await ready(
                    server, cid, state["campaign_revision"], "resolve_spell", payload, "release"
                )
            assert await _campaign_actor_snapshot(server, cid, ids) == before
            monkeypatch.setattr(IdempotencyService, "remember_write_in_session", remember)
            settled = await ready(
                server, cid, state["campaign_revision"], "resolve_spell", payload, "release"
            )
            assert settled["released"] is True
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_changed_target_range_preserves_original_hold_without_rolls(tmp_path, monkeypatch):
    async def exercise():
        server, _, cid, actors, _, _, state = await prepare(tmp_path, "healing")
        aid, tid = [a["id"] for a in actors]
        try:
            readied_id = state["combat"]["readied"][0]["id"]
            declined = await ready(
                server,
                cid,
                state["campaign_revision"],
                "resolve_spell",
                {
                    "actor_id": aid,
                    "choice_id": state["choice"]["id"],
                    "release": False,
                },
                "decline",
            )
            moved = await _raw(
                server,
                "combat_movement",
                {
                    "campaign_id": cid,
                    "actor_id": tid,
                    "action": "move",
                    "payload": {
                        "movement_mode": "teleport",
                        "distance": 30,
                        "destination": {"x": 7, "y": 0},
                    },
                    "expected_revision": declined["campaign_revision"],
                    "idempotency_key": "move",
                },
            )
            state = await ready(
                server,
                cid,
                moved["campaign_revision"],
                "trigger_spell",
                {
                    "readied_id": readied_id,
                    "event": "the bell rings again",
                },
                "retrigger",
            )
            before = await _campaign_actor_snapshot(server, cid, [aid, tid])

            def forbidden(*args, **kwargs):
                pytest.fail("invalid Ready target must not roll")

            monkeypatch.setattr(server_module, "roll", forbidden)
            with pytest.raises(Exception, match="range|reach"):
                await ready(
                    server,
                    cid,
                    state["campaign_revision"],
                    "resolve_spell",
                    {
                        "actor_id": aid,
                        "choice_id": state["choice"]["id"],
                        "release": True,
                    },
                    "release",
                )
            assert await _campaign_actor_snapshot(server, cid, [aid, tid]) == before
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_readied_blade_ward_starts_on_release_and_expires_after_next_caster_turn(tmp_path):
    async def exercise():
        server, _, cid, actors, _, _, state = await prepare(tmp_path, "blade_ward")
        aid, tid = [a["id"] for a in actors]
        try:
            before = await _campaign_actor_snapshot(server, cid, [aid])
            assert not any(
                e["active"] and e["kind"] == "spell_blade_ward"
                for e in before["actors"][0]["sheet"]["effects"]
            )
            released = await ready(
                server,
                cid,
                state["campaign_revision"],
                "resolve_spell",
                {
                    "actor_id": aid,
                    "choice_id": state["choice"]["id"],
                    "release": True,
                },
                "release",
            )
            after = await _campaign_actor_snapshot(server, cid, [aid])
            effect = next(
                e
                for e in after["actors"][0]["sheet"]["effects"]
                if e["active"] and e["kind"] == "spell_blade_ward"
            )
            assert effect["duration"] == {"period": "turn_end", "remaining": 1}
            state = released
            for index, actor_id in enumerate([tid, aid]):
                state = await _raw(
                    server,
                    "combat_end_turn",
                    {
                        "campaign_id": cid,
                        "actor_id": actor_id,
                        "expected_revision": state["campaign_revision"],
                        "idempotency_key": f"end-{index}",
                    },
                )
            after = await _campaign_actor_snapshot(server, cid, [aid])
            assert not any(
                e["active"] and e["kind"] == "spell_blade_ward"
                for e in after["actors"][0]["sheet"]["effects"]
            )
        finally:
            close_server(server)

    asyncio.run(exercise())
