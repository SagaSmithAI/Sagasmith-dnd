"""Exact private Tasha subclass acceptance through the public protocol.

The Tasha archive has no class artifact. These builds explicitly use the locked
Eberron Artificer base class with the independently reviewed Tasha subclass.
No commercial source text is embedded in these tests.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_core import IdempotencyService
from sagasmith_core.integrity import json_sha256
from sagasmith_dnd.character_schema import (
    BATTLE_READY_SOURCES,
    default_character_sheet,
    derive_character_sheet,
)

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from scripts.regression_official_expansions import _ProtocolTools
from scripts.repair_tasha_battle_smith import PREFIX, SPELLS, VERSION
from tests.test_artificer_play_official_archive_mcp import (
    _CLASS,
    _SRD,
    _exercise_defender_combat,
)
from tests.test_artificer_play_official_archive_mcp import (
    _PREFIX as EBERRON,
)
from tests.test_artificer_play_official_archive_mcp import (
    _VERSION as EBERRON_VERSION,
)
from tests.test_official_expansions_mcp import _call, _locked_official_library, _selection_for
from tests.test_steel_defender_lifecycle_mcp import (
    _exercise_defender_lifecycle,
    _exercise_owner_death,
)

FEATURE_LEVELS = {
    "tool-proficiency-battle-smith": 3,
    "battle-smith-spells": 3,
    "battle-ready": 3,
    "steel-defender": 3,
    "extra-attack-21cf6f37cbf9": 5,
    "arcane-jolt": 9,
    "improved-defender": 15,
}
PHB = "dnd5e.addon.rulebook.d-d-5e-player-s-handbook.7ad6d3e9c93c"


def _canonical_card(actor):
    """Normalize only per-actor identities, retaining every mechanical/source field."""
    identities = {actor["id"]: "compared-owner", actor["name"]: "Compared Battle Smith"}
    for index, item in enumerate(actor["sheet"]["inventory"]["items"]):
        # Starting equipment shares its class source_key. Keep every instance
        # distinct so equality still detects cross-item binding mistakes.
        identities[item["id"]] = f"item:{index}:" + item["source_key"]
    for record in actor["sheet"]["content"]["selections"]:
        selection = record["selection"]
        if "_class_equipment_authority" in selection:
            assert selection["_class_equipment_authority"]["selection_checksum"] == json_sha256(
                {k: v for k, v in selection.items() if k != "_class_equipment_authority"}
            )

    def normalize(value):
        if isinstance(value, str):
            return identities.get(value, value)
        if isinstance(value, list):
            return [normalize(item) for item in value]
        if isinstance(value, dict):
            if value.get("purpose") == "class_starting_equipment":
                assert value["character_id"] == actor["id"]
                assert isinstance(value["signature"], str) and len(value["signature"]) == 64
                return {
                    k: normalize(v)
                    for k, v in value.items()
                    if k not in {"signature", "selection_checksum"}
                }
            return {k: normalize(v) for k, v in value.items()}
        return value

    return normalize(actor["sheet"])


def _config(tmp_path, *, official=True):
    return McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=Path(__file__).resolve().parents[3] / "skills",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=official,
        official_content_library=_locked_official_library() if official else None,
    )


async def _get(server, character_id):
    return await _call(
        server,
        "character_query",
        {
            "view": "get",
            "payload": {"character_id": character_id},
        },
    )


async def _campaign(server, campaign_id):
    return await _call(
        server,
        "campaign_query",
        {
            "view": "get",
            "payload": {"campaign_id": campaign_id},
        },
    )


async def _activate(server):
    campaign = await _call(
        server,
        "campaign_create",
        {
            "name": "Exact Tasha Battle Smith",
            "edition": "2014",
            "random_seed": "official-artificer-play-v10",
            "idempotency_key": "campaign",
        },
    )
    for prefix, version in ((PHB, "1.0.2"), (EBERRON, EBERRON_VERSION), (PREFIX, VERSION)):
        current = await _campaign(server, campaign["id"])
        await _call(
            server,
            "content_pack",
            {
                "action": "activate",
                "payload": {
                    "campaign_id": campaign["id"],
                    "kind": "addon",
                    "addon_id": prefix + ".addon",
                    "version": version,
                },
                "expected_revision": current["revision"],
                "idempotency_key": "activate-" + prefix,
            },
        )
    return campaign


async def _apply(server, campaign_id, owner, artifact, selection=None, *, key=None):
    selected = await _selection_for(server, campaign_id, artifact)
    selected.update(selection or {})
    request = {
        "character_id": owner["id"],
        "artifact_id": artifact,
        "selection": selected,
        "expected_revision": owner["revision"],
        "idempotency_key": key or owner["id"] + "-apply-" + artifact,
    }
    result = await _call(server, "character_content_apply", request)
    assert "revision" in result, result
    assert await _call(server, "character_content_apply", request) == result
    return result


async def _owner(server, campaign_id, name):
    sheet = default_character_sheet()
    sheet["abilities"]["intelligence"]["score"] = 16
    owner = await _call(
        server,
        "character_create_from",
        {
            "mode": "direct",
            "payload": {"campaign_id": campaign_id, "name": name, "sheet": sheet},
            "idempotency_key": name,
        },
    )
    pending = await _call(
        server,
        "character_content_apply",
        {
            "character_id": owner["id"],
            "artifact_id": _CLASS,
            "expected_revision": owner["revision"],
            "idempotency_key": name + "-missing-choices",
        },
    )
    assert pending["status"] == "pending_choice"
    assert (await _get(server, owner["id"]))["sheet"] == owner["sheet"]
    owner = await _apply(
        server,
        campaign_id,
        owner,
        _CLASS,
        {
            "starting_equipment": {
                "mode": "equipment",
                "choices": {
                    "simple_weapons": [_SRD + "item.dagger", _SRD + "item.club"],
                    "armor": [_SRD + "item.scale-mail"],
                },
            },
        },
    )
    for spell in ("mending", "light"):
        owner = await _apply(
            server,
            campaign_id,
            owner,
            _SRD + "spell." + spell,
            {
                "source_class": "Artificer",
                "method": "known",
            },
        )
    return owner


async def _level(server, owner, level):
    result = await _call(
        server,
        "character_state_change",
        {
            "character_id": owner["id"],
            "action": "level_advance",
            "payload": {
                "target_level": level,
                "class_name": "Artificer",
                "hp_method": "fixed",
                "reason": "Exact-source Battle Smith acceptance",
                "source_ref": "bundled:srd2014/03_Characterization/Beyond_1st_Level.md",
            },
            "expected_revision": owner["revision"],
            "idempotency_key": owner["id"] + f"-level-{level}",
        },
    )
    return result["character"]


async def _battle_ready_weapons(server, owner):
    for magical in (False, True):
        item_id = "battle-ready-" + str(magical).lower()
        result = await _call(
            server,
            "inventory_change",
            {
                "owner": "character",
                "owner_id": owner["id"],
                "action": "add",
                "payload": {
                    "item": {
                        "id": item_id,
                        "name": "Battle Ready acceptance weapon",
                        "kind": "weapon",
                        "source_key": "test:battle-ready-weapon",
                        "mechanics": {
                            "category": "martial",
                            "attack_type": "melee",
                            "attack_ability": "strength",
                            "damage_formula": "1d8",
                            "damage_type": "slashing",
                            "magical": magical,
                        },
                    }
                },
                "expected_revision": owner["revision"],
                "idempotency_key": "add-" + item_id,
            },
        )
        owner = result["character"]
        owner = await _call(
            server,
            "inventory_change",
            {
                "owner": "character",
                "owner_id": owner["id"],
                "action": "equip",
                "payload": {"item_id": item_id, "slot": "main_hand"},
                "expected_revision": owner["revision"],
                "idempotency_key": "equip-" + item_id,
            },
        )
        attack = next(
            a for a in owner["derived"]["inventory"]["weapon_attacks"] if a["item_id"] == item_id
        )
        assert attack["attack_ability"] == ("intelligence" if magical else "strength")
        assert attack["damage_bonus"] == (3 if magical else 0)
        owner = await _call(
            server,
            "inventory_change",
            {
                "owner": "character",
                "owner_id": owner["id"],
                "action": "equip",
                "payload": {"item_id": item_id, "slot": None},
                "expected_revision": owner["revision"],
                "idempotency_key": "unequip-" + item_id,
            },
        )
    return owner


def _assert_spells(owner, level):
    spells = owner["sheet"]["content"]["spells"]
    expected = {name for minimum, names in SPELLS.items() if minimum <= level for name in names}
    prepared = [s for s in spells if s["access"].get("always_prepared")]
    assert {s["name"] for s in prepared} == expected
    assert len(prepared) == len(expected)
    selected = owner["sheet"]["spellcasting"]["preparation"]["selected_spell_ids"]
    assert all(s["access"]["prepared"] and s["id"] not in selected for s in prepared)
    assert all(s["grant"]["source_type"] == "subclass" for s in prepared)


@pytest.mark.parametrize("pack_id,feature_id", BATTLE_READY_SOURCES.items())
def test_battle_ready_cannot_be_forged_through_direct_character_creation(
    tmp_path,
    pack_id,
    feature_id,
):
    config = _config(tmp_path, official=False)

    async def exercise():
        runtime = create_server(config)
        try:
            async with Client(runtime, mode="2026-07-28") as client:
                server = _ProtocolTools(client)
                campaign = await _call(
                    server,
                    "campaign_create",
                    {
                        "name": "Forgery guard",
                        "edition": "2014",
                        "idempotency_key": "campaign",
                    },
                )
                sheet = default_character_sheet()
                sheet["content"]["features"] = [
                    {
                        "id": feature_id,
                        "name": "Battle Ready",
                        "pack_id": pack_id,
                        "pack_version": VERSION,
                        "rule_refs": ["caller-forged#battle-ready"],
                    }
                ]
                before = await _campaign(server, campaign["id"])
                with pytest.raises(ToolError, match="Battle Ready provenance"):
                    await _call(
                        server,
                        "character_create_from",
                        {
                            "mode": "direct",
                            "payload": {
                                "campaign_id": campaign["id"],
                                "name": "Forgery",
                                "sheet": sheet,
                            },
                            "idempotency_key": "forge",
                        },
                    )
                assert await _campaign(server, campaign["id"]) == before
        finally:
            close_server(runtime)

    asyncio.run(exercise())


@pytest.mark.fresh_database
def test_exact_tasha_build_defender_lifecycle_and_restart(tmp_path, monkeypatch):
    config = _config(tmp_path)

    async def exercise():
        runtime = create_server(config)
        sessions = AsyncExitStack()
        server = _ProtocolTools(
            await sessions.enter_async_context(Client(runtime, mode="2026-07-28"))
        )
        try:
            campaign = await _activate(server)
            campaign_id = campaign["id"]
            owner = await _owner(server, campaign_id, "Tasha owner")
            for level in (2, 3):
                owner = await _level(server, owner, level)
            owner = await _apply(
                server,
                campaign_id,
                owner,
                PREFIX + ".subclass.battle-smith",
                {
                    "target_class_name": "Artificer",
                },
            )
            _assert_spells(owner, 3)
            for slug, minimum in FEATURE_LEVELS.items():
                if minimum != 3 or slug == "steel-defender":
                    continue
                owner = await _apply(server, campaign_id, owner, PREFIX + ".feature." + slug)
            profs = owner["sheet"]["traits"]["proficiencies"]
            assert "Smith's Tools" in profs["tools"]
            assert "martial weapons" in profs["weapons"]
            owner = await _battle_ready_weapons(server, owner)
            before = await _campaign(server, campaign_id)
            create_request = {
                "campaign_id": campaign_id,
                "artifact_id": PREFIX + ".statblock.steel-defender",
                "owner_character_id": owner["id"],
                "expected_revision": before["revision"],
                "idempotency_key": "not-entitled",
            }
            with pytest.raises(ToolError, match="feature entitlement"):
                await _call(server, "addon_actor_instantiate", create_request)
            assert await _campaign(server, campaign_id) == before
            owner = await _apply(server, campaign_id, owner, PREFIX + ".feature.steel-defender")
            owner = await _apply(server, campaign_id, owner, _SRD + "item.smith-s-tools")
            owner = await _apply(server, campaign_id, owner, _SRD + "item.component-pouch")
            current = await _campaign(server, campaign_id)
            create_request.update(expected_revision=current["revision"], idempotency_key="defender")
            created = await _call(server, "addon_actor_instantiate", create_request)
            defender = created["character"]
            assert defender["sheet"]["combat"]["hp"]["max"] == 20
            derived = derive_character_sheet(defender["sheet"])
            assert derived["armor_class"] == 15
            assert defender["sheet"]["combat"]["hit_dice"]["d8"]["max"] == 3
            assert derived["saving_throws"]["dexterity"] == 3
            assert derived["skills"]["perception"] == 4
            relation = (await _campaign(server, campaign_id))["state"]["dependent_actor_relations"][
                0
            ]
            assert relation["source_pack_id"] == PREFIX
            assert relation["source_pack_version"] == VERSION
            assert relation["template_binding"]["lifecycle_policy"]["owner_death"] == "perish"
            before = await _campaign(server, campaign_id)
            with pytest.raises(ToolError):
                await _call(
                    server,
                    "addon_actor_instantiate",
                    {
                        **create_request,
                        "expected_revision": before["revision"],
                        "idempotency_key": "duplicate-defender",
                    },
                )
            assert await _campaign(server, campaign_id) == before
            combat = await _exercise_defender_combat(
                server,
                campaign_id,
                owner,
                defender,
                attack_bonus=5,
                source_prefix=PREFIX,
                rule_version=VERSION,
            )
            replays, lifecycle_final = await _exercise_defender_lifecycle(
                server,
                campaign_id,
                owner,
                defender,
            )
            # Refresh the same damaged/rested dependent inside owner advancement.
            # HP, daily Repair uses and spent resources must not be reset by scaling.
            owner = lifecycle_final[1]
            previous_defender = lifecycle_final[2]
            current = await _campaign(server, campaign_id)
            await _call(
                server,
                "game_phase",
                {
                    "campaign_id": campaign_id,
                    "action": "set",
                    "tool_profile": "lobby",
                    "expected_revision": current["revision"],
                    "idempotency_key": "scaling-lobby",
                },
            )
            for level in (4, 5):
                owner = await _level(server, owner, level)
            scaled_defender = await _get(server, defender["id"])
            scaled = derive_character_sheet(scaled_defender["sheet"])
            assert scaled["hit_points"]["max"] == 30
            assert (
                scaled["hit_points"]["value"] == previous_defender["sheet"]["combat"]["hp"]["value"]
            )
            assert scaled["saving_throws"]["dexterity"] == 4
            assert scaled["skills"]["perception"] == 6
            assert scaled_defender["sheet"]["combat"]["hit_dice"]["d8"]["max"] == 5
            rend = next(
                i
                for i in scaled_defender["sheet"]["inventory"]["items"]
                if i["name"] == "Force-Empowered Rend"
            )
            assert rend["mechanics"]["attack_bonus_override"] == 6
            assert rend["mechanics"]["damage_bonus_override"] == 3
            # A replacement must follow the owner's just-completed long rest.
            # Advance the real campaign clock so the second long rest is legal.
            current = await _campaign(server, campaign_id)
            await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign_id,
                    "action": "clock_advance",
                    "payload": {
                        "period": "hour",
                        "count": 16,
                        "expected_elapsed_ticks": (
                            current["state"]["game_time"]["elapsed_ticks"] + 16 * 60 * 10
                        ),
                    },
                    "expected_revision": current["revision"],
                    "idempotency_key": "replacement-day",
                },
            )
            current = await _campaign(server, campaign_id)
            owner = await _get(server, owner["id"])
            await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign_id,
                    "action": "party_rest",
                    "payload": {
                        "rest_type": "long_rest",
                        "duration_minutes": 480,
                        "members": [
                            {"character_id": owner["id"], "expected_revision": owner["revision"]}
                        ],
                    },
                    "expected_revision": current["revision"],
                    "idempotency_key": "replacement-rest",
                },
            )
            before_replace = await _campaign(server, campaign_id)
            old_defender = await _get(server, defender["id"])
            old_owner = await _get(server, owner["id"])
            replacement_request = {
                **create_request,
                "replace_existing": True,
                "expected_revision": before_replace["revision"],
                "idempotency_key": "replacement-defender",
            }
            remember = IdempotencyService.remember_in_session
            receipt_failures = []

            def fail_receipt(self, session, scope, key, payload, response, **kwargs):
                if key == "replacement-defender":
                    receipt_failures.append(key)
                    raise RuntimeError("injected replacement receipt failure")
                return remember(self, session, scope, key, payload, response, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(IdempotencyService, "remember_in_session", fail_receipt)
                # Modern MCP masks unexpected server exception details. Confirm
                # the injection itself, then assert the complete persisted rollback.
                with pytest.raises(ToolError):
                    await _call(server, "addon_actor_instantiate", replacement_request)
            assert receipt_failures == ["replacement-defender"]
            assert await _campaign(server, campaign_id) == before_replace
            assert await _get(server, defender["id"]) == old_defender
            assert await _get(server, owner["id"]) == old_owner
            replacement = await _call(server, "addon_actor_instantiate", replacement_request)
            assert (
                await _call(server, "addon_actor_instantiate", replacement_request) == replacement
            )
            assert replacement["replaced_actor_id"] == old_defender["id"]
            perished = await _get(server, old_defender["id"])
            assert perished["sheet"]["combat"]["hp"]["value"] == 0
            assert "dead" in perished["sheet"]["conditions"]
            defender = replacement["character"]
            assert defender["sheet"]["combat"]["hp"]["max"] == 30
            current = await _campaign(server, campaign_id)
            assert [r["status"] for r in current["state"]["dependent_actor_relations"]] == [
                "replaced",
                "active",
            ]
            with pytest.raises(ToolError, match="already created"):
                await _call(
                    server,
                    "addon_actor_instantiate",
                    {
                        **replacement_request,
                        "expected_revision": current["revision"],
                        "idempotency_key": "same-rest-replacement",
                    },
                )
            assert await _campaign(server, campaign_id) == current
            death, _ = await _exercise_owner_death(
                server,
                campaign_id,
                owner,
                defender,
                owner_death="perish",
            )
            replays.append(death)
            for tool, offset in (
                ("combat_use_activity", 0),
                ("combat_resolve_attack", 2),
                ("combat_resolve_attack", 4),
            ):
                replays.append((tool, combat[offset], combat[offset + 1]))
            final = await _campaign(server, campaign_id)
            final_actors = [await _get(server, a["id"]) for a in (owner, defender)]
            await sessions.aclose()
            close_server(runtime)
            runtime = create_server(config)
            server = _ProtocolTools(
                await sessions.enter_async_context(
                    Client(runtime, mode="2026-07-28"),
                )
            )
            assert await _campaign(server, campaign_id) == final
            assert [await _get(server, a["id"]) for a in (owner, defender)] == final_actors
            assert await _call(server, "addon_actor_instantiate", create_request) == created
            assert (
                await _call(server, "addon_actor_instantiate", replacement_request) == replacement
            )
            assert await _get(server, old_defender["id"]) == perished
            for tool, request, response in replays:
                assert await server.call_tool(tool, request) == response
            assert await _campaign(server, campaign_id) == final
        finally:
            await sessions.aclose()
            close_server(runtime)

    asyncio.run(exercise())


@pytest.mark.fresh_database
def test_exact_tasha_subclass_order_and_feature_levels(tmp_path):
    config = _config(tmp_path)

    async def exercise():
        runtime = create_server(config)
        try:
            async with Client(runtime, mode="2026-07-28") as client:
                server = _ProtocolTools(client)
                campaign_id = (await _activate(server))["id"]
                early = await _owner(server, campaign_id, "Early subclass")
                late = await _owner(server, campaign_id, "Late subclass")
                for level in range(2, 18):
                    early = await _level(server, early, level)
                    late = await _level(server, late, level)
                    if level == 2:
                        with pytest.raises(ToolError, match="must reach level 3"):
                            await _apply(
                                server, campaign_id, early, PREFIX + ".subclass.battle-smith"
                            )
                        continue
                    if level == 3:
                        early = await _apply(
                            server, campaign_id, early, PREFIX + ".subclass.battle-smith"
                        )
                    _assert_spells(early, level)
                    for slug, minimum in FEATURE_LEVELS.items():
                        if minimum == level:
                            early = await _apply(
                                server, campaign_id, early, PREFIX + ".feature." + slug
                            )
                    ids = [f["id"] for f in early["sheet"]["content"]["features"]]
                    for slug, minimum in FEATURE_LEVELS.items():
                        assert ids.count(PREFIX + ".feature." + slug) == int(level >= minimum)
                late = await _apply(server, campaign_id, late, PREFIX + ".subclass.battle-smith")
                for slug in FEATURE_LEVELS:
                    late = await _apply(server, campaign_id, late, PREFIX + ".feature." + slug)
                _assert_spells(late, 17)

                assert _canonical_card(early) == _canonical_card(late)
                assert early["sheet"]["combat"]["attacks_per_action"] == 2
                before = await _get(server, late["id"])
                with pytest.raises(ToolError, match="revision conflict"):
                    await _call(
                        server,
                        "character_content_apply",
                        {
                            "character_id": late["id"],
                            "artifact_id": PREFIX + ".feature.battle-ready",
                            "expected_revision": late["revision"] - 1,
                            "idempotency_key": "stale-feature",
                        },
                    )
                assert await _get(server, late["id"]) == before
                with pytest.raises(ToolError, match="source-bound subclass"):
                    await _apply(server, campaign_id, late, EBERRON + ".subclass.battle-smith")
                assert await _get(server, late["id"]) == before
                with pytest.raises(ToolError):
                    await _apply(
                        server,
                        campaign_id,
                        late,
                        PREFIX + ".feature.battle-ready",
                        key="duplicate-feature",
                    )
                assert await _get(server, late["id"]) == before
                branch = next(
                    b
                    for b in await _call(
                        server,
                        "branch_query",
                        {
                            "campaign_id": campaign_id,
                            "view": "list",
                        },
                    )
                    if b["is_current"]
                )
                current = await _campaign(server, campaign_id)
                save_request = {
                    "campaign_id": campaign_id,
                    "label": "Exact Tasha grant parity",
                    "expected_revision": current["revision"],
                    "expected_head_snapshot_id": branch["head_snapshot_id"] or "",
                    "idempotency_key": "grant-parity-snapshot",
                }
                saved = await _call(server, "snapshot_create", save_request)
                verification = await _call(
                    server,
                    "snapshot_query",
                    {
                        "campaign_id": campaign_id,
                        "view": "verify",
                        "payload": {"slot": saved["slot"]},
                    },
                )
                assert verification["valid"] is True
                final = [await _get(server, a["id"]) for a in (early, late)]
        finally:
            close_server(runtime)
        restarted = create_server(config)
        try:
            async with Client(restarted, mode="2026-07-28") as client:
                server = _ProtocolTools(client)
                assert [await _get(server, a["id"]) for a in (early, late)] == final
                assert await _call(server, "snapshot_create", save_request) == saved
                assert (
                    await _call(
                        server,
                        "snapshot_query",
                        {
                            "campaign_id": campaign_id,
                            "view": "verify",
                            "payload": {"slot": saved["slot"]},
                        },
                    )
                    == verification
                )
        finally:
            close_server(restarted)

    asyncio.run(exercise())
