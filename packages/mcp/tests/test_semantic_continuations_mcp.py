import asyncio
import random
from copy import deepcopy

import pytest
from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    equip_inventory_item,
)
from sagasmith_dnd.combat_engine import roll_attack_action

from sagasmith_dnd_mcp import server as server_module
from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import finalize_and_activate_module
from tests.test_semantic_plan_mcp import _call, _raw


async def prepare(tmp_path, *, reaction, movement_payment=None, mover_incapacitated=False,
                  protection=False, legendary_resistance=False):
    text = "Twin Strikes. Make two unarmed attacks against one adjacent visible creature."
    if movement_payment:
        text = (
            f"Commanded March. Another creature uses its {movement_payment} to move up to "
            "its speed, even outside its turn. After it finishes moving, it becomes prone."
        )
    if legendary_resistance:
        text = ("Dread Pulse. One target must make a DC 30 Wisdom saving throw, "
                "taking 1d6 force damage on failure and no damage on success.")
    source = tmp_path / "room.md"
    source.write_text(f"# Room\n\n## Encounter\n\n{text}\n", encoding="utf8")
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "skills",
        modulegen_skills_dir=tmp_path / "modulegen",
        module_import_roots=(tmp_path,),
        auto_seed_rules=False,
    )
    server = create_server(config)
    campaign = await _call(
        server,
        "campaign_create",
        {
            "name": "Continuations",
            "edition": "2014",
            "idempotency_key": "campaign",
        },
    )
    cid = campaign["id"]
    staged = await _call(
        server,
        "module_draft",
        {
            "campaign_id": cid,
            "action": "start",
            "payload": {"source_path": str(source), "source_key": "room", "title": "Room"},
            "idempotency_key": "stage",
        },
    )
    await finalize_and_activate_module(
        _call,
        server,
        cid,
        staged,
        source_key="room",
        title="Room",
        portable_id="dnd5e.module.continuations-test",
    )
    chunks = await _call(server, "module_search", {
        "campaign_id": cid,
        "query": ("Dread Pulse" if legendary_resistance else
                  "Commanded March" if movement_payment else "Twin Strikes"),
    })
    expanded = await _call(server, "module_expand", {"chunk_id": chunks[0]["id"]})
    actors = []
    for name in (("attacker", "target", "protector") if protection else ("attacker", "target")):
        sheet = default_character_sheet()
        if name == "target" and legendary_resistance:
            from tests.test_legendary_resistance_mcp import dragon_sheet

            sheet = dragon_sheet()
        if name == "target" and mover_incapacitated:
            sheet["conditions"] = ["incapacitated"]
        sheet["combat"]["hp"].update(value=100, max=100)
        sheet["combat"]["ac"]["override"] = 1
        sheet["abilities"]["strength"]["score"] = 16
        if name == "attacker":
            plan = {
                "schema_version": 2,
                "id": "module.room.twin",
                "source_card_id": "twin",
                "source_card_kind": "monster_action",
                "trigger": "action",
                "slots": {
                    key: {"kind": "actor_id", "owner": "agent", "description": key}
                    for key in ("source", "target")
                },
                "steps": [
                    {
                        "id": f"hit-{i}",
                        "op": "attack.resolve",
                        "args": {
                            "source_actor_id": {"$slot": "source"},
                            "target_actor_id": {"$slot": "target"},
                            "attack_ref": "unarmed-strike",
                            "context": {"target_can_see_attacker": True},
                        },
                    }
                    for i in range(2)
                ],
                "citations": [
                    {
                        "source": "module:room",
                        "source_ref": expanded["source_ref"],
                        "source_excerpt": text,
                    }
                ],
            }
            if movement_payment:
                sheet["inventory"]["items"] = [{
                    "id": "whip", "name": "Whip", "kind": "weapon",
                    "equipped": True, "equipped_slot": "main_hand",
                    "mechanics": {
                        "category": "martial", "attack_type": "melee",
                        "attack_ability": "dexterity", "damage_formula": "1d4",
                        "damage_type": "slashing", "reach_ft": 10,
                        "properties": ["finesse", "reach"],
                    },
                }]
                sheet["inventory"]["equipment_slots"]["main_hand"] = "whip"
                plan["steps"] = [
                    {"id": "move", "op": "movement.move", "args": {
                        "actor_id": {"$slot": "target"}, "distance_ft": 15,
                        "destination": {"x": 4, "y": 0}, "payment": movement_payment,
                        "distance_limit": "speed", "voluntary": True,
                    }},
                    {"id": "after", "op": "condition.apply", "args": {
                        "target_ids": [{"$slot": "target"}], "condition_id": "prone",
                        "source": "Commanded March",
                    }},
                ]
                # Keep the paid source actor binding meaningful in the source contract.
                plan["steps"].insert(0, {"id": "target", "op": "target.validate", "args": {
                    "source_actor_id": {"$slot": "source"},
                    "target_ids": [{"$slot": "target"}], "exclude_self": True,
                }})
            if legendary_resistance:
                plan["steps"] = [
                    {"id": "target", "op": "target.validate", "args": {
                        "source_actor_id": {"$slot": "source"},
                        "target_ids": [{"$slot": "target"}], "exclude_self": True,
                    }},
                    {"id": "save", "op": "check.save", "args": {
                        "target_ids": [{"$slot": "target"}], "ability": "wisdom", "dc": 30,
                        "success_damage": "none", "source": text,
                    }},
                    {"id": "damage", "op": "damage.apply", "args": {
                        "target_ids": [{"$slot": "target"}], "expression": "1d6",
                        "damage_type": "force", "source": text,
                        "reduction": {"$result": "save.damage_reduction_by_actor_id"},
                    }},
                ]
            sheet["content"]["activities"] = [
                {
                    "id": "twin",
                    "name": "Twin Strikes",
                    "description": text,
                    "activation": {"type": "action", "cost": 1},
                    "uses": {"value": 0, "max": 0, "unlimited": True},
                    "choices": {"resolution_plan": {"id": plan["id"], "fingerprint": "compiled"}},
                    "resolution_plan": plan,
                }
            ]
        elif name == "protector":
            sheet["content"]["features"] = [{
                "id": "dnd5e.content.srd2014.feature.fighter-fighting-style",
                "name": "Fighting Style", "choices": {"option": "Protection"},
            }]
            sheet, shield = add_inventory_item(sheet, {
                "id": "shield", "name": "Shield", "kind": "shield", "mechanics": {"ac_bonus": 2},
            })
            sheet = equip_inventory_item(sheet, shield, "shield")
        elif reaction:
            sheet["progression"] = {
                "level": 5,
                "classes": [{"name": "Rogue", "level": 5, "hit_die": 8}],
            }
            sheet["content"]["features"] = [
                {
                    "id": "dnd5e.content.srd2014.feature.rogue-uncanny-dodge",
                    "name": "Uncanny Dodge",
                    "source_key": "Rogue",
                    "description": "Uncanny Dodge source text",
                    "activation": {"type": "reaction", "cost": 0, "trigger": "attack.after_hit"},
                    "choices": {
                        "source_trait": {
                            "kind": "uncanny_dodge",
                            "trigger": "attacker_visible_hits_with_attack",
                            "damage_outcome": "half",
                            "automatic": True,
                            "source_excerpt": "Uncanny Dodge source text",
                        }
                    },
                    "mechanic_refs": ["dnd5e.core.reaction.uncanny_dodge"],
                }
            ]
        elif not protection and not legendary_resistance:
            sheet["abilities"]["constitution"]["score"] = 30
            sheet["effects"] = [
                {
                    "id": "focus",
                    "name": "Focus",
                    "kind": "concentration",
                    "active": True,
                    "concentration": True,
                    "duration": {"period": "hour", "remaining": 1},
                    "changes": [],
                }
            ]
        actors.append(
            await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": cid,
                        "name": name,
                        "character_type": "monster",
                        "sheet": sheet,
                    },
                    "idempotency_key": name,
                },
            )
        )
    current = await _call(
        server, "campaign_query", {"view": "get", "payload": {"campaign_id": cid}}
    )
    play = await _call(
        server,
        "game_phase",
        {
            "campaign_id": cid,
            "action": "set",
            "tool_profile": "play",
            "expected_revision": current["revision"],
            "idempotency_key": "play",
        },
    )
    started = await _call(
        server,
        "combat_start",
        {
            "campaign_id": cid,
            "positioning_mode": "grid",
            "participant_ids": [a["id"] for a in actors],
            "participant_config": [
                {"actor_id": a["id"], "initiative": 20 - i * 10,
                 "position": {"x": i, "y": 0},
                 "disposition": "friendly" if i else "hostile"}
                for i, a in enumerate(actors)
            ],
            "scene_id": expanded["scene"]["id"],
            "battle_map": {"bounds": {"width_cells": 10, "height_cells": 10}},
            "ruleset": "2014",
            "expected_revision": play["campaign_revision"],
            "idempotency_key": "start",
        },
    )
    pending = await _raw(
        server,
        "combat_use_activity",
        {
            "campaign_id": cid,
            "actor_id": actors[0]["id"],
            "activity_id": "twin",
            "expected_revision": started["campaign_revision"],
            "idempotency_key": "contract",
        },
    )
    contract = pending["result"]["resolution_plan_contract"]
    commitment = {
        "application_id": "twin-1",
        "plan_id": contract["plan_id"],
        "plan_fingerprint": contract["plan_fingerprint"],
        "source_card_id": "twin",
        "source_card_kind": "monster_action",
        "bindings": {"source": actors[0]["id"], "target": actors[1]["id"]},
        "agent_ruling": {
            "application_id": "twin-1",
            "default_resolver": "agent",
            "ruling_kind": "agent_dm_adjudication",
            "decision": (
                "Move the adjacent target." if movement_payment else "Attack the adjacent target."
            ),
            "reason": text if movement_payment else "The reviewed source permits two attacks.",
            "source_ref": expanded["source_ref"],
            "source_excerpt": text,
        },
    }
    paid = await _raw(
        server,
        "combat_use_activity",
        {
            "campaign_id": cid,
            "actor_id": actors[0]["id"],
            "activity_id": "twin",
            "declaration": {"agent_resolution_commitment": commitment},
            "expected_revision": started["campaign_revision"],
            "idempotency_key": "pay",
        },
    )
    return server, config, cid, actors, paid


@pytest.mark.parametrize("reaction,accept", [(False, False), (True, False), (True, True)])
def test_plan_resumes_after_each_owned_choice_without_reroll_or_repay(
    tmp_path,
    monkeypatch,
    reaction,
    accept,
):
    rolls = []

    def attack_roll(*, plan):
        rolls.append(deepcopy(plan))
        return roll_attack_action(plan=plan, rng=random.Random(5))

    monkeypatch.setattr(server_module, "roll_attack_action", attack_roll)

    async def exercise():
        server, config, cid, actors, paid = await prepare(tmp_path, reaction=reaction)
        source, target = (a["id"] for a in actors)
        commitment = paid["result"]["declaration"]["agent_resolution_commitment"]
        revision = paid["campaign_revision"]
        try:
            for index in range(1 if accept else 2):
                request = {
                    "campaign_id": cid,
                    "actor_id": source,
                    "action": "execute_plan",
                    "payload": {"commitment": commitment},
                    "expected_revision": revision,
                    "idempotency_key": f"execute-{index}",
                }
                settled = await _call(server, "combat_choice", request)
                assert settled["status"] == "pending_choice"
                assert len(rolls) == index + 1
                replay = await _call(server, "combat_choice", request)
                assert replay == settled
                assert len(rolls) == index + 1
                revision = settled["campaign_revision"]
                premature = await _raw(server, "combat_choice", {
                    **request, "expected_revision": revision,
                    "idempotency_key": f"premature-{index}",
                })
                assert premature["status"] == "pending_ruling"
                assert len(rolls) == index + 1
                with pytest.raises(Exception, match="resume the paid semantic plan"):
                    await _call(server, "combat_movement", {
                        "campaign_id": cid, "actor_id": source, "action": "move",
                        "payload": {"distance": 5, "destination": {"x": 0, "y": 1}},
                        "expected_revision": revision, "idempotency_key": f"move-{index}",
                    })
                windows = [
                    w
                    for w in settled["combat"]["pending"]
                    if w.get("status", "pending") == "pending"
                ]
                (window,) = windows
                # Restart between checkpoint and player response; no in-memory cursor.
                close_server(server)
                server = create_server(config)
                if reaction:
                    selection_id = (
                        next(
                            c["id"]
                            for c in window["candidates"]
                            if c.get("kind") == "uncanny_dodge"
                        )
                        if accept
                        else "decline"
                    )
                    resolved = await _call(
                        server,
                        "combat_choice",
                        {
                            "campaign_id": cid,
                            "actor_id": target,
                            "action": "resolve_defense",
                            "payload": {
                                "choice_id": window["id"],
                                "selection": {"id": selection_id},
                            },
                            "expected_revision": revision,
                            "idempotency_key": f"resolve-{index}",
                        },
                    )
                else:
                    resolved = await _raw(
                        server,
                        "combat_concentration_check",
                        {
                            "campaign_id": cid,
                            "target_id": target,
                            "dc": window["dc"],
                            "effect_ids": window["effect_ids"],
                            "expected_revision": revision,
                            "idempotency_key": f"resolve-{index}",
                        },
                    )
                revision = resolved["campaign_revision"]
            finished = await _call(
                server,
                "combat_choice",
                {
                    "campaign_id": cid,
                    "actor_id": source,
                    "action": "execute_plan",
                    "payload": {"commitment": commitment},
                    "expected_revision": revision,
                    "idempotency_key": "finish",
                },
            )
            assert finished["status"] == "committed"
            assert len(rolls) == 2
            assert len(finished["result"]["results"]) == 2
            assert not finished["combat"]["semantic_state"]["continuations"]
            sheet = await _call(
                server, "character_query", {"view": "get", "payload": {"character_id": target}}
            )
            assert sheet["sheet"]["combat"]["hp"]["value"] == (94 if accept else 92)
        finally:
            close_server(server)

    asyncio.run(exercise())
