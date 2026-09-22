"""Public Runtime authority, persistence, RNG and transaction tests for objects."""

import asyncio
import hashlib
import json
from copy import deepcopy
from dataclasses import replace

import pytest
from sagasmith_core.idempotency import IdempotencyService
from sagasmith_core.state import StateMutationService
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd_runtime.application import create_runtime
from sagasmith_dnd_runtime.config import McpConfig
from sagasmith_dnd_runtime.operations import RequestIdentity

from tests.authoring_helpers import finalize_and_activate_module

EXCERPT = "The stone wall consists of separate Large sections, each AC 17 and 100 hit points."
RULING = {
    "reason": "DM reviewed this section and assigned its defenses and weapon applicability.",
    "source_excerpt": EXCERPT,
}


class World:
    async def call(self, name, arguments, *, principal="system:local"):
        value = await self.runtime.execute(name, arguments, context=RequestIdentity(principal))
        return value.get("result", value) if isinstance(value, dict) else value

    async def snapshot(self):
        return (
            await self.call(
                "campaign_query", {"view": "get", "payload": {"campaign_id": self.cid}}
            ),
            await self.call(
                "character_query", {"view": "get", "payload": {"character_id": self.aid}}
            ),
        )

    async def arguments(self, key="attack", **payload):
        campaign, actor = await self.snapshot()
        return {
            "character_id": self.aid,
            "action": "attack_source_object",
            "payload": {
                "object": deepcopy(self.profile),
                "weapon_id": "bow",
                "source_ref": self.source,
                "object_ruling": RULING,
                "reason": "Strike the reviewed wall section within weapon range.",
                "expected_campaign_revision": campaign["revision"],
                **payload,
            },
            "expected_revision": actor["revision"],
            "idempotency_key": key,
        }

    def close(self):
        self.runtime.close()


async def setup(
    tmp_path,
    *,
    kind="piercing",
    limited=False,
    local=False,
    ammunition=True,
    source_description=EXCERPT,
    random_seed=None,
):
    world = World()
    world.config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "dnd",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
        local_authority=local,
        bound_principal_id="system:local" if local else None,
    )
    world.runtime = create_runtime(world.config)
    try:
        campaign = await world.call(
            "campaign_create", {"name": "Objects", "edition": "2014", "idempotency_key": "campaign",
                                **({"random_seed": random_seed} if random_seed else {})}
        )
        world.cid = campaign["id"]
        staged = await world.call(
            "module_draft",
            {
                "campaign_id": world.cid,
                "action": "start",
                "payload": {
                    "name": "wall.md",
                    "content": "# Vault\n\n## Wall\n\n" + source_description,
                    "source_key": "wall",
                    "title": "Wall",
                },
                "idempotency_key": "module",
            },
        )

        async def authoring_call(server, name, arguments):
            return await world.call(name, arguments)

        activation = await finalize_and_activate_module(
            authoring_call,
            world.runtime,
            world.cid,
            staged,
            source_key="wall",
            title="Wall",
            portable_id="dnd5e.module.object-test",
        )
        hits = await world.call(
            "module_search", {"campaign_id": world.cid, "query": "stone wall", "top_k": 3}
        )
        expanded = await world.call("module_expand", {"chunk_id": hits[0]["id"]})
        world.source = {
            "module_id": activation["activated"]["activation"]["module_id"],
            "scene_id": expanded["scene"]["id"],
            "chunk_id": expanded["chunk_id"],
            "page_start": expanded["page_start"],
            "page_end": expanded["page_end"],
            "heading_path": expanded["heading_path"],
            "content_sha256": hashlib.sha256(expanded["content"].encode("utf-8")).hexdigest(),
        }
        world.profile = {
            "id": "wall-section",
            "name": "Wall section",
            "scene_id": world.source["scene_id"],
            "armor_class": 17,
            "hit_points": 100,
            "material": "stone",
            "size": "large",
            "resilience": "resilient",
            "section_of": {"id": "wall", "size": "huge"},
        }
        sheet = default_character_sheet()
        sheet["edition"] = "2014"
        sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
        bow = {
            "id": "bow",
            "name": "Test bow",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "ranged",
                "attack_ability": "dexterity",
                "damage_formula": "1d6",
                "damage_type": kind,
                "properties": ["ammunition"] if ammunition else [],
                "normal_range_ft": 80,
                "long_range_ft": 320,
                "ammunition_item_id": "arrows" if ammunition else None,
                "attack_bonus_override": 20,
                "proficient": True,
            },
        }
        if limited:
            bow["mechanics"]["recharge"] = {
                "kind": "d6_turn_start",
                "minimum": 5,
                "maximum": 6,
                "source_marker": "(Recharge 5-6)",
            }
            bow["uses"] = {
                "label": "Test recharge",
                "value": 1,
                "max": 1,
                "recovers_on": "manual",
                "source_key": "test",
            }
        sheet["inventory"]["items"] = [
            bow,
            {"id": "arrows", "name": "Arrows", "kind": "ammunition", "quantity": 20},
        ]
        sheet["inventory"]["equipment_slots"]["main_hand"] = "bow"
        actor = await world.call(
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "name": "Breaker",
                    "campaign_id": world.cid,
                    "character_type": "pc",
                    "sheet": sheet,
                },
                "idempotency_key": "actor",
            },
        )
        world.aid = actor["id"]
        return world
    except BaseException:
        world.close()
        raise


@pytest.mark.parametrize("kind", ["poison", "psychic", "piercing"])
def test_source_object_real_rng_restart_and_replay(tmp_path, kind):
    async def run():
        world = await setup(tmp_path, kind=kind)
        try:
            arguments = await world.arguments()
            before, actor = await world.snapshot()
            first = await world.call("character_action", arguments)
            assert first["random_stream_receipt"]["draw_count"] >= 1
            assert first["character"]["revision"] == actor["revision"] + 1
            assert first["object"]["profile_approval"]
            assert first["object"]["source_ref"] == world.source
            if kind in {"poison", "psychic"}:
                assert first["object"]["hit_points"] == 100
            receipts = first["rule_receipts"]
            object_receipt = next(
                r for r in receipts if r["mechanic_id"] == "dnd5e.core.objects.damage"
            )
            assert "08_Gamemastering/Objects.md" in str(object_receipt)
            after, updated = await world.snapshot()
            assert (
                after["state"]["random_stream"]["position"]
                > before["state"]["random_stream"]["position"]
            )
            assert (
                next(i for i in updated["sheet"]["inventory"]["items"] if i["id"] == "arrows")[
                    "quantity"
                ]
                == 19
            )
            world.close()
            world.runtime = create_runtime(world.config)
            replay = await world.call("character_action", arguments)
            assert replay == first
            assert await world.snapshot() == (after, updated)
            reference = {"id": world.profile["id"], "scene_id": world.profile["scene_id"]}
            second = await world.call(
                "character_action",
                await world.arguments("second", object=reference, object_ruling=None),
            )
            assert second["object"]["profile_approval"] == first["object"]["profile_approval"]
            assert second["object"]["hit_points"] <= first["object"]["hit_points"]
            with pytest.raises(Exception, match="idempotency"):
                await world.call(
                    "character_action",
                    {**arguments, "payload": {**arguments["payload"], "reason": "changed intent"}},
                )
        finally:
            world.close()

    asyncio.run(run())


def test_players_cannot_author_stats_or_advantage_but_can_attack_reviewed_object(tmp_path):
    async def run():
        world = await setup(tmp_path)
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
            before = await world.snapshot()
            with pytest.raises(Exception, match="DM source review"):
                await world.call(
                    "character_action", await world.arguments(), principal="user:player"
                )
            assert await world.snapshot() == before
            await world.call("character_action", await world.arguments("approve"))
            reference = {"id": world.profile["id"], "scene_id": world.profile["scene_id"]}
            before = await world.snapshot()
            with pytest.raises(Exception, match="DM source review"):
                await world.call(
                    "character_action",
                    await world.arguments(
                        "forged-advantage",
                        object=reference,
                        object_ruling=None,
                        advantage=True,
                        attack_ruling=RULING,
                    ),
                    principal="user:player",
                )
            assert await world.snapshot() == before
            with pytest.raises(Exception, match="different reviewed data"):
                await world.call(
                    "character_action",
                    await world.arguments(
                        "forged-ac", object={**world.profile, "armor_class": 1}, object_ruling=None
                    ),
                    principal="user:player",
                )
            player_arguments = await world.arguments(
                "player-hit", object=reference, object_ruling=None
            )
            result = await world.call(
                "character_action",
                player_arguments,
                principal="user:player",
            )
            assert result["status"] == "committed"
            assert set(result["character"]) == {"id", "name", "revision"}
            assert set(result["object"]) == {"id", "name", "scene_id", "destroyed"}
            assert "armor_class" not in result["attack"]
            assert RULING["reason"] not in json.dumps(result)
            assert EXCERPT not in json.dumps(result)
            assert "random_stream_receipt" not in result
            assert "revisions" not in result
            world.close()
            world.runtime = create_runtime(world.config)
            assert (
                await world.call("character_action", player_arguments, principal="user:player")
                == result
            )
            reviewed = await world.call(
                "character_action",
                await world.arguments(
                    "reviewed-advantage",
                    object=reference,
                    object_ruling=None,
                    advantage=True,
                    attack_ruling=RULING,
                ),
            )
            assert reviewed["object"]["last_attack"]["context_approval"]
            # A formerly trusted DM must not retrieve its old full receipt after
            # losing that role, even though it still controls the attacker.
            await world.call(
                "access_grant",
                {
                    "scope": "campaign",
                    "campaign_id": world.cid,
                    "principal_id": "user:player",
                    "payload": {"role": "dm"},
                },
            )
            privileged_arguments = await world.arguments(
                "former-dm-hit", object=reference, object_ruling=None
            )
            privileged = await world.call(
                "character_action", privileged_arguments, principal="user:player"
            )
            assert privileged["object"]["profile_approval"]
            await world.call(
                "access_grant",
                {
                    "scope": "campaign",
                    "campaign_id": world.cid,
                    "principal_id": "user:player",
                    "payload": {"role": "player"},
                },
            )
            world.close()
            world.runtime = create_runtime(world.config)
            downgraded = await world.call(
                "character_action", privileged_arguments, principal="user:player"
            )
            assert set(downgraded["object"]) == {"id", "name", "scene_id", "destroyed"}
            assert set(downgraded["character"]) == {"id", "name", "revision"}
            assert EXCERPT not in json.dumps(downgraded)
        finally:
            world.close()

    asyncio.run(run())


def test_bad_profiles_citations_modifiers_and_stale_revisions_do_not_roll(tmp_path):
    async def run():
        world = await setup(tmp_path)
        try:
            before = await world.snapshot()
            valid = await world.arguments()
            for index, change in enumerate(
                [
                    {"object_ruling": None},
                    {"advantage": True},
                    {"damage_bonus": 999},
                    {"object": {**world.profile, "damage_threshold": True}},
                    {"object": {**world.profile, "death_saves": False}},
                    {
                        "object_ruling": {
                            **RULING,
                            "source_excerpt": "An invented source excerpt has no authority.",
                        }
                    },
                    {"source_ref": {**world.source, "content_sha256": "0" * 64}},
                    {"expected_campaign_revision": before[0]["revision"] - 1},
                ]
            ):
                with pytest.raises(Exception):
                    await world.call(
                        "character_action",
                        {
                            **valid,
                            "idempotency_key": f"bad-{index}",
                            "payload": {**valid["payload"], **change},
                        },
                    )
                assert await world.snapshot() == before
            with pytest.raises(Exception, match="character revision conflict"):
                await world.call(
                    "character_action", {**valid, "expected_revision": before[1]["revision"] - 1}
                )
            assert await world.snapshot() == before
        finally:
            world.close()

    asyncio.run(run())


def test_receipt_failure_rolls_back_hp_ammo_limited_use_and_random_progress(tmp_path, monkeypatch):
    async def run():
        world = await setup(tmp_path, limited=True)
        try:
            arguments = await world.arguments()
            before = await world.snapshot()
            remember = IdempotencyService.remember_write_in_session
            attempts = []

            def fail(self, session, **kwargs):
                if kwargs.get("key") == "attack":
                    write = kwargs["write"]
                    attempts.append(deepcopy(write.response(kwargs["result"])))
                    raise RuntimeError("injected object receipt failure")
                return remember(self, session, **kwargs)

            monkeypatch.setattr(IdempotencyService, "remember_write_in_session", fail)
            with pytest.raises(Exception, match="injected object receipt failure"):
                await world.call("character_action", arguments)
            assert attempts
            assert await world.snapshot() == before
            monkeypatch.setattr(IdempotencyService, "remember_write_in_session", remember)
            result = await world.call("character_action", arguments)
            assert result["attack"] == attempts[0]["attack"]
            assert result["damage"] == attempts[0]["damage"]
            assert result["random_stream_receipt"] == attempts[0]["random_stream_receipt"]
            assert result["random_stream_receipt"]["draw_count"] >= 1
            items = result["character"]["sheet"]["inventory"]["items"]
            assert next(i for i in items if i["id"] == "arrows")["quantity"] == 19
            assert next(i for i in items if i["id"] == "bow")["uses"]["value"] == 0
            assert (await world.call("character_action", arguments)) == result
        finally:
            world.close()

    asyncio.run(run())


@pytest.mark.parametrize("ammunition", [True, False])
def test_character_cas_is_checked_inside_object_commit(tmp_path, monkeypatch, ammunition):
    async def run():
        world = await setup(tmp_path, ammunition=ammunition)
        try:
            arguments = await world.arguments()
            before = await world.snapshot()
            original = StateMutationService.replace

            def race(self, campaign_id, **kwargs):
                if kwargs.get("operation") == "character.source_object.attack":
                    updates = kwargs["character_updates"]
                    assert len(updates) == 1
                    kwargs["character_updates"] = [replace(updates[0], expected_revision=-1)]
                return original(self, campaign_id, **kwargs)

            monkeypatch.setattr(StateMutationService, "replace", race)
            with pytest.raises(Exception, match="revision conflict"):
                await world.call("character_action", arguments)
            assert await world.snapshot() == before
        finally:
            world.close()

    asyncio.run(run())


def test_local_host_object_attack_owns_revisions_and_returns_current_object_state(tmp_path):
    async def run():
        world = await setup(tmp_path)
        try:
            world.close()
            world.config = replace(
                world.config, local_authority=True, bound_principal_id="system:local"
            )
            world.runtime = create_runtime(world.config)
            arguments = await world.arguments()
            arguments["payload"]["object"]["damage_threshold"] = 1000
            arguments.pop("expected_revision")
            arguments["payload"].pop("expected_campaign_revision")
            result = await world.call("character_action", arguments)
            assert result["status"] == "committed"
            assert result["object"]["profile_approval"]
            assert result["object"]["hit_points"] == 100
            path = world.runtime.local_session.journal / (
                hashlib.sha256(b"attack").hexdigest() + ".json"
            )
            journal = json.loads(path.read_text(encoding="utf-8"))
            journal.pop("result")
            world.runtime.local_session._save(path, journal)
            world.close()
            world.runtime = create_runtime(world.config)
            assert await world.call("character_action", arguments) == result
        finally:
            world.close()

    asyncio.run(run())
