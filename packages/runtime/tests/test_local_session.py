import asyncio
import hashlib
import json

import pytest
from sagasmith_dnd_runtime.application import create_runtime
from sagasmith_dnd_runtime.config import McpConfig
from sagasmith_dnd_runtime.operations import RequestIdentity


def config(root):
    return McpConfig(
        home=root, database_url=None, chroma_url=None, chroma_path_override=None,
        dnd_skills_dir=root / "skills", modulegen_skills_dir=root / "modulegen",
        auto_seed_rules=False, bound_principal_id="system:local", local_authority=True,
    )


def test_local_transfer_recovers_lost_response_after_restart(tmp_path):
    async def run():
        runtime = create_runtime(config(tmp_path))
        identity = RequestIdentity("system:local")

        async def call(operation, **args):
            value = await runtime.execute(operation, args, context=identity)
            return value.get("result", value)

        try:
            campaign = await call("campaign_create", name="Local", idempotency_key="create")
            source = await call("character_create_from", mode="direct", payload={
                "campaign_id": campaign["id"], "name": "Source",
            }, idempotency_key="source")
            target = await call("character_create_from", mode="direct", payload={
                "campaign_id": campaign["id"], "name": "Target",
            }, idempotency_key="target")
            player_context = runtime.local_session.context(campaign["id"], {"audience": "player"})
            assert player_context["binding"]["audience"] == "player"
            assert "actors" not in player_context
            assert player_context["state_token"] is None
            await call("inventory_change", owner="character", action="add", owner_id=source["id"],
                       payload={"item": {"id": "rope", "name": "Rope", "kind": "equipment",
                                         "quantity": 2}}, idempotency_key="add")
            arguments = {"mode": "character_to_character", "payload": {
                "source_character_id": source["id"], "target_character_id": target["id"],
                "item_id": "rope", "quantity": 1,
            }, "idempotency_key": "transfer"}
            result = await runtime.execute("inventory_transfer", arguments, context=identity)
            assert result["result"]["source"]["sheet"]["inventory"]["items"][0]["quantity"] == 1
            assert result["result"]["target"]["sheet"]["inventory"]["items"][0]["quantity"] == 1
            slices = {actor["id"]: actor for actor in result["local_context"]["actors"]}
            assert set(slices) == {source["id"], target["id"]}
            for actor_id in slices:
                assert slices[actor_id]["sheet"]["inventory"]["items"][0]["quantity"] == 1
            assert result["host_context_binding"] == result["local_context"]["binding"]
            # An unselected actor still participates in invalidation without
            # requiring its full sheet in the returned context.
            observer = await call("character_create_from", mode="direct", payload={
                "campaign_id": campaign["id"], "name": "Observer",
            }, idempotency_key="observer")
            before = runtime.local_session.context(campaign["id"], arguments)
            await call("inventory_change", owner="character", action="add",
                       owner_id=observer["id"], payload={"item": {
                           "id": "observer-rope", "name": "Rope", "kind": "equipment",
                       }}, idempotency_key="observer-item")
            after = runtime.local_session.context(campaign["id"], arguments)
            assert {actor["id"] for actor in after["actors"]} == {source["id"], target["id"]}
            assert after["state_token"] != before["state_token"]
            # Simulate commit success followed by process death before journal completion.
            path = runtime.local_session.journal / (
                hashlib.sha256(b"transfer").hexdigest() + ".json"
            )
            entry = json.loads(path.read_text(encoding="utf-8"))
            entry.pop("result")
            runtime.local_session._save(path, entry)
        finally:
            runtime.close()
        runtime = create_runtime(config(tmp_path))
        try:
            replay = await runtime.execute("inventory_transfer", arguments, context=identity)
            assert replay["result"] == result["result"]
            with pytest.raises(PermissionError):
                await runtime.execute("inventory_transfer", arguments,
                                      context=RequestIdentity("untrusted"))
            with pytest.raises(ValueError, match="another intent"):
                await runtime.execute("inventory_transfer", {
                    **arguments, "mode": "character_to_party",
                }, context=identity)
            snapshot = await runtime.execute("snapshot_create", {
                "campaign_id": campaign["id"], "idempotency_key": "snapshot",
            }, context=identity)
            before_binding = replay["host_context_binding"]
            restored = await runtime.execute("snapshot_restore", {
                "campaign_id": campaign["id"], "slot": snapshot["slot"],
                "idempotency_key": "restore",
            }, context=identity)
            assert (before_binding["context_epoch"]
                    != restored["host_context_binding"]["context_epoch"])
            restore_path = runtime.local_session.journal / (
                hashlib.sha256(b"restore").hexdigest() + ".json"
            )
            saved = json.loads(restore_path.read_text(encoding="utf-8"))
            saved.pop("result")
            saved["binding"] = entry["binding"]
            runtime.local_session._save(restore_path, saved)
            recovered = await runtime.execute("snapshot_restore", {
                "campaign_id": campaign["id"], "slot": snapshot["slot"],
                "idempotency_key": "restore",
            }, context=identity)
            assert recovered["host_context_binding"] == restored["host_context_binding"]
            with pytest.raises(ValueError, match="invalidated timeline"):
                await runtime.execute("inventory_transfer", arguments, context=identity)
            await call("game_phase", campaign_id=campaign["id"], action="set",
                       tool_profile="play", idempotency_key="play")
            rested = await call("campaign_change", campaign_id=campaign["id"],
                               action="party_rest", payload={
                                   "members": [{"character_id": source["id"]}],
                                   "rest_type": "long_rest", "duration_minutes": 480,
                               }, idempotency_key="rest")
            assert rested["affected_state"]["actors"][0]["id"] == source["id"]
        finally:
            runtime.close()
    asyncio.run(run())


def test_text_startup_never_initializes_optional_services(tmp_path):
    import subprocess
    import sys

    program = '''
import sys
from pathlib import Path
from sagasmith_dnd_runtime.application import create_runtime
from sagasmith_dnd_runtime.config import McpConfig
root = Path(sys.argv[1])
r = create_runtime(McpConfig(home=root, database_url=None, chroma_url=None,
    chroma_path_override=None, dnd_skills_dir=root / "skills",
    modulegen_skills_dir=root / "modulegen", auto_seed_rules=False,
    local_authority=True, bound_principal_id="system:local"))
try:
    forbidden = {"torch", "transformers", "chromadb", "onnxruntime", "rapidocr"}
    assert not (forbidden & {name.split(".")[0] for name in sys.modules})
    assert "sagasmith_dnd_mcp.gateway" not in sys.modules
finally:
    r.close()
'''
    subprocess.run([sys.executable, "-c", program, str(tmp_path)], check=True, timeout=60)


@pytest.mark.parametrize("legacy_cache", [False, True])
@pytest.mark.parametrize("native_effect", [False, True])
def test_local_missing_material_can_resume_same_intent_after_inventory_update(
    tmp_path, legacy_cache, native_effect,
):
    from sagasmith_dnd.character_schema import default_character_sheet
    from sagasmith_dnd.spells import CORE_MAGE_ARMOR_SPELL_ID

    spell_id = CORE_MAGE_ARMOR_SPELL_ID if native_effect else "source:unresolved-effect"

    async def run():
        runtime = create_runtime(config(tmp_path))
        identity = RequestIdentity("system:local")

        async def call(operation, **args):
            return await runtime.execute(operation, args, context=identity)

        try:
            campaign = await call("campaign_create", name="Components", edition="2014",
                                  idempotency_key="campaign")
            sheet = default_character_sheet()
            sheet["spellcasting"]["spell_slots"] = {
                "1": {"value": 1, "max": 1, "recovers_on": "long_rest"},
            }
            sheet["content"]["spells"] = [{
                "id": spell_id, "name": "Reviewed spell", "level": 1,
                "access": {"known": True},
                "definition": {"casting_time": "1 action", "components": {
                    "verbal": True, "somatic": True, "material": True,
                }},
            }]
            actor = await call("character_create_from", mode="direct", payload={
                "campaign_id": campaign["id"], "name": "Caster", "sheet": sheet,
            }, idempotency_key="caster")
            actor = actor["result"]
            args = {"character_id": actor["id"], "action": "cast_spell",
                    "payload": {"spell_id": spell_id},
                    "idempotency_key": "cast"}
            pending = await call("character_action", **args)
            assert pending["result"]["status"] == "pending_ruling"
            assert pending["result"]["committed"] is False
            journal_path = runtime.local_session.journal / (
                hashlib.sha256(b"cast").hexdigest() + ".json"
            )
            assert not journal_path.exists()
            if legacy_cache:
                prepared, campaign_id = runtime.local_session.prepare(
                    "character_action", args, None,
                )
                runtime.local_session._save(journal_path, {
                    "intent": {"operation": "character_action", "arguments": args,
                               "campaign_id": None, "principal_id": "system:local"},
                    "arguments": prepared, "campaign_id": campaign_id,
                    "binding": runtime.local_session.binding(campaign_id, prepared),
                    "result": pending,
                })
            await call("inventory_change", owner="character", owner_id=actor["id"],
                       action="add", payload={"item": {
                           "id": "pouch", "name": "Component pouch", "kind": "equipment",
                           "mechanics": {"spell_component": {
                               "kind": "pouch", "source": "SRD 2014 Equipment: component pouch",
                           }},
                       }}, idempotency_key="pouch")
            committed = await call("character_action", **args)
            assert committed["result"]["status"] == (
                "committed" if native_effect else "pending_ruling"
            )
            assert journal_path.exists()  # A paid effect ruling is a durable receipt.
            assert committed["result"]["result"]["component_receipt"]["status"] == "satisfied"
            assert committed["local_context"]["actors"][0]["sheet"]["spellcasting"][
                "spell_slots"
            ]["1"]["value"] == 0
            assert (await call("character_action", **args))["result"] == committed["result"]
        finally:
            runtime.close()
    asyncio.run(run())


@pytest.mark.parametrize("ready", [False, True, "spell"])
def test_one_local_attack_commits_dice_hp_and_replays_without_reroll(tmp_path, ready):
    from sagasmith_dnd.character_schema import default_character_sheet

    async def run():
        runtime = create_runtime(config(tmp_path))
        identity = RequestIdentity("system:local")

        async def call(operation, **args):
            return await runtime.execute(operation, args, context=identity)

        try:
            campaign = await call("campaign_create", name="Attack", edition="2014",
                                  idempotency_key="campaign")
            sheet = default_character_sheet()
            sheet["abilities"]["strength"]["score"] = 18
            sheet["inventory"]["items"] = [{
                "id": "sword", "name": "Sword", "kind": "weapon", "equipped": True,
                "equipped_slot": "main_hand", "mechanics": {
                    "attack_type": "melee", "attack_ability": "strength",
                    "damage_formula": "1d8", "damage_type": "slashing", "properties": [],
                },
            }]
            sheet["inventory"]["equipment_slots"]["main_hand"] = "sword"
            if ready == "spell":
                from sagasmith_dnd.spell_resolution import (
                    SPELL_RESOLUTION_MECHANIC_ID,
                    known_spell_resolution,
                )

                sheet["abilities"]["intelligence"]["score"] = 30
                sheet["spellcasting"]["ability"] = "intelligence"
                sheet["spellcasting"]["spell_slots"] = {
                    "2": {"value": 1, "max": 1, "recovers_on": "long_rest"},
                }
                sheet["content"]["spells"] = [{
                    "id": "test.scorching-ray", "name": "Scorching Ray", "level": 2,
                    "grant": {"source_type": "class", "source_key": "wizard", "method": "known"},
                    "access": {"known": True, "prepared": True},
                    "definition": {"casting_time": "1 action",
                                   "range": {"kind": "distance", "normal_ft": 120},
                                   "duration": {"kind": "instantaneous", "concentration": False},
                                   "components": {"verbal": True, "somatic": True}},
                    "resolution": known_spell_resolution("Scorching Ray"),
                    "mechanic_refs": [SPELL_RESOLUTION_MECHANIC_ID],
                }]
            created = await call("character_create_from", mode="direct", payload={
                "campaign_id": campaign["id"], "name": "Hero", "sheet": sheet,
            }, idempotency_key="hero")
            hero = created["result"]
            target_sheet = default_character_sheet()
            target_sheet["combat"]["hp"] = {"value": 100, "max": 100, "temp": 0}
            created = await call("character_create_from", mode="direct", payload={
                "campaign_id": campaign["id"], "name": "Target", "sheet": target_sheet,
            }, idempotency_key="target")
            target = created["result"]
            await call("game_phase", campaign_id=campaign["id"], action="set",
                       tool_profile="play", idempotency_key="play")
            await call("combat_start", campaign_id=campaign["id"], positioning_mode="grid",
                       battle_map={"width_cells": 12, "height_cells": 12},
                       participant_ids=[hero["id"], target["id"]], participant_config=[
                           {"actor_id": hero["id"], "initiative": 20, "position": {"x": 0, "y": 0}},
                           {"actor_id": target["id"], "initiative": 10,
                            "position": {"x": 1, "y": 0}},
                       ], idempotency_key="start")
            arguments = {"campaign_id": campaign["id"], "target_id": target["id"],
                         "action": {"weapon_id": "sword", "attack_mode": "melee"},
                         "idempotency_key": "attack"}
            operation = "combat_resolve_attack"
            if ready:
                if ready == "spell":
                    armed = await call("combat_ready", campaign_id=campaign["id"],
                                       action="ready_spell", payload={
                                           "actor_id": hero["id"], "spell_id": "test.scorching-ray",
                                           "trigger": "the bell rings", "declaration": {
                                               "attacks": [{"target_id": target["id"]}] * 3,
                                           },
                                       }, idempotency_key="ready")
                    armed = armed["result"]
                else:
                    armed = await call("combat_common_action", campaign_id=campaign["id"],
                                   action="ready", trigger="the bell rings", payload={
                                       "action": "attack", "target_id": target["id"],
                                       "attack": {"weapon_id": "sword", "attack_mode": "melee"},
                                   }, idempotency_key="ready")
                await call("combat_end_turn", campaign_id=campaign["id"], idempotency_key="end")
                triggered = await call("combat_ready", campaign_id=campaign["id"],
                                       action=("trigger_spell" if ready == "spell"
                                               else "trigger_action"),
                                       payload={
                                           "readied_id": armed["combat"]["readied"][0]["id"],
                                           "event": "the bell rings",
                                       }, idempotency_key="trigger")
                operation = "combat_ready"
                choice_id = triggered["result"]["combat"]["pending"][0]["id"]
                arguments = {"campaign_id": campaign["id"],
                             "action": "resolve_spell" if ready == "spell" else "resolve_action",
                             "payload": {"actor_id": hero["id"],
                                         "choice_id": choice_id,
                                         "release": True}, "idempotency_key": "release"}
                if ready == "spell":
                    arguments["payload"].pop("actor_id")
            # Actor, revision and branch are all resolved by the local authority.
            result = await runtime.execute(operation, arguments, context=identity)
            if ready:
                result = {**result, **result["result"]}
            assert result["status"] == "committed"
            assert result["random_stream_receipt"]
            assert result["affected_state"]["campaign_revision"] == result["campaign_revision"]
            replay = await runtime.execute(operation, arguments, context=identity)
            if ready:
                replay = {**replay, **replay["result"]}
            assert result["result"] == replay["result"]
            assert result["random_stream_receipt"] == replay["random_stream_receipt"]
            assert result["affected_state"] == replay["affected_state"]
            if ready == "spell":
                resolution_id = result["result"]["attack_payment"]["spell_resolution_id"]
                for index in range(2):
                    settled = await call("combat_resolve_attack", campaign_id=campaign["id"],
                                         target_id=target["id"], action={
                                             "spell_resolution_id": resolution_id, "context": {},
                                         }, idempotency_key=f"ray-{index}")
                    assert settled["status"] == "committed"
                assert settled["combat"]["pending"] == []
        finally:
            runtime.close()
    asyncio.run(run())
