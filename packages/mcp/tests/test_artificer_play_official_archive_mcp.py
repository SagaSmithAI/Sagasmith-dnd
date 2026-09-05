"""Real locked Eberron build-to-Defender play; not full class/errata acceptance."""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from scripts.regression_official_expansions import _ProtocolTools
from tests.test_official_expansions_mcp import _call, _locked_official_library, _selection_for
from tests.test_steel_defender_lifecycle_mcp import _exercise_defender_lifecycle

_PREFIX = "dnd5e.addon.rulebook.d-d-5e-eberron-rising-from-the-last-war.31293633134f"
_VERSION = "1.0.6-local.starting-equipment.1"
_RULE_VERSION = "1.0.4-local.starting-equipment.1"
_CLASS = _PREFIX + ".class.artificer"
_DEFENDER = _PREFIX + ".statblock.steel-defender"
_SRD = "dnd5e.content.srd2014."
_FEATURES = (
    "magical-tinkering", "spellcasting", "infuse-item", "artificer-specialist",
    "the-right-tool-for-the-job", "tool-proficiency-battle-smith", "battle-smith-spells",
    "battle-ready", "steel-defender",
)


def test_protocol_combat_uses_and_persists_campaign_random_stream(tmp_path: Path) -> None:
    """Public CI checks the protocol boundary without needing commercial archives."""
    config = McpConfig(
        home=tmp_path / "protocol", database_url=None, chroma_url=None,
        chroma_path_override=None, dnd_skills_dir=tmp_path / "skills",
        modulegen_skills_dir=tmp_path / "modulegen", auto_seed_rules=False,
    )

    async def exercise() -> None:
        runtime = create_server(config)
        try:
            async with Client(runtime, mode="2026-07-28") as client:
                server = _ProtocolTools(client)
                campaign = await _call(server, "campaign_create", {
                    "name": "Protocol combat stream", "edition": "2014",
                    "random_seed": "official-artificer-play-v10", "idempotency_key": "campaign",
                })
                actors = []
                for name in ("Attacker", "Target"):
                    actors.append(await _call(server, "character_create_from", {
                        "mode": "direct", "payload": {
                            "campaign_id": campaign["id"], "name": name,
                            "sheet": default_character_sheet(),
                        }, "idempotency_key": name,
                    }))
                current = await _call(server, "campaign_query", {
                    "view": "get", "payload": {"campaign_id": campaign["id"]},
                })
                started = await _call(server, "combat_start", {
                    "campaign_id": campaign["id"], "positioning_mode": "agent",
                    "participant_ids": [actor["id"] for actor in actors],
                    "participant_config": [
                        {"actor_id": actor["id"], "initiative": 20 - index * 20}
                        for index, actor in enumerate(actors)
                    ], "expected_revision": current["revision"], "idempotency_key": "combat",
                })
                request = {
                    "campaign_id": campaign["id"], "actor_id": actors[0]["id"],
                    "target_id": actors[1]["id"], "action": {
                        "weapon_id": "unarmed-strike", "attack_mode": "melee",
                        "context": {"spatial_facts": {
                            "decision_id": "protocol-attack", "reason": "Adjacent visible target",
                            "targetable": True, "in_range": True, "cover_degree": "none",
                            "attacker_can_see_target": True, "target_can_see_attacker": True,
                        }},
                    }, "expected_revision": started["campaign_revision"],
                    "idempotency_key": "attack",
                }
                content, response = await server.call_tool("combat_resolve_attack", request)
                attack = response["result"]
                assert attack["natural"] == 17 and attack["hit"] is True
                assert attack["damage"]["applied_amount"] == 1
                receipt = response["random_stream_receipt"]
                assert receipt["position_before"] == 0 and receipt["position_after"] == 1
                assert json.loads(content[0].text) == response
                assert await server.call_tool("combat_resolve_attack", request) == (
                    content, response,
                )
                assert await _call(server, "combat_resolve_attack", request) == attack
                after = await _call(server, "campaign_query", {
                    "view": "get", "payload": {"campaign_id": campaign["id"]},
                })
                assert after["state"]["random_stream"]["position"] == 1
                assert after["state"]["random_stream"]["last_receipt"] == receipt
                with pytest.raises(ToolError):
                    await server.call_tool("combat_resolve_attack", {
                        **request, "target_id": actors[0]["id"],
                    })
                assert await _call(server, "campaign_query", {
                    "view": "get", "payload": {"campaign_id": campaign["id"]},
                }) == after
        finally:
            close_server(runtime)

        restarted = create_server(config)
        try:
            async with Client(restarted, mode="2026-07-28") as client:
                server = _ProtocolTools(client)
                assert await server.call_tool("combat_resolve_attack", request) == (
                    content, response,
                )
                assert await _call(server, "campaign_query", {
                    "view": "get", "payload": {"campaign_id": campaign["id"]},
                }) == after
        finally:
            close_server(restarted)

    asyncio.run(exercise())


async def _exercise_defender_combat(server, campaign_id: str, owner: dict, defender: dict):
    """Exercise existing public combat contracts; also reusable for local failure diagnosis."""
    async def current() -> dict:
        return await _call(server, "campaign_query", {
            "view": "get", "payload": {"campaign_id": campaign_id},
        })

    async def end(actor_id: str, key: str) -> dict:
        return await _call(server, "combat_end_turn", {
            "campaign_id": campaign_id, "actor_id": actor_id,
            "expected_revision": (await current())["revision"], "idempotency_key": key,
        })

    target_sheet = default_character_sheet()
    target_sheet["combat"]["hp"] = {"value": 50, "max": 50, "temp": 0}
    target = await _call(server, "character_create_from", {
        "mode": "direct", "payload": {
            "campaign_id": campaign_id, "name": "Training target", "sheet": target_sheet,
        }, "idempotency_key": "training-target",
    })
    started = await _call(server, "combat_start", {
        "campaign_id": campaign_id, "positioning_mode": "agent",
        "participant_ids": [owner["id"], defender["id"], target["id"]],
        "participant_config": [{"actor_id": owner["id"], "initiative": 20},
                               {"actor_id": target["id"], "initiative": 0}],
        "expected_revision": (await current())["revision"], "idempotency_key": "combat",
    })
    combatants = started["combat"]["combatants"]
    assert [item["actor_id"] for item in combatants] == [
        owner["id"], defender["id"], target["id"],
    ]
    assert combatants[0]["initiative"] == combatants[1]["initiative"]
    assert all(item.get("position") is None for item in combatants)
    damaged = await _call(server, "combat_hp_change", {
        "campaign_id": campaign_id, "target_id": defender["id"], "action": "damage",
        "payload": {"parts": [{"amount": 10, "damage_type": "force"}]},
        "expected_revision": started["campaign_revision"], "idempotency_key": "damage",
    })
    command_request = {
        "campaign_id": campaign_id, "actor_id": owner["id"],
        "action": "command_dependent", "target_id": defender["id"],
        "expected_revision": damaged["campaign_revision"], "idempotency_key": "command",
    }
    command = await _call(server, "combat_common_action", command_request)
    assert await _call(server, "combat_common_action", command_request) == command
    assert (await current())["state"]["combat"]["combatants"][0]["turn_budget"][
        "bonus_action"
    ] == 0
    turn = await end(owner["id"], "owner-end")
    commanded = (await current())["state"]["combat"]["combatants"][1]
    assert commanded["turn_budget"]["main_action"] == 1
    assert commanded["turn_flags"]["dependent_command_active"] is True
    repair = next(item for item in defender["sheet"]["content"]["activities"]
                  if item["name"].startswith("Repair"))
    repair_request = {
        "campaign_id": campaign_id, "actor_id": defender["id"],
        "activity_id": repair["id"], "declaration": {"target_id": defender["id"]},
        "expected_revision": turn["campaign_revision"], "idempotency_key": "repair",
    }
    repaired = await server.call_tool("combat_use_activity", repair_request)
    assert repaired[1]["random_stream_receipt"]["draw_count"] == 2
    assert json.loads(repaired[0][0].text) == repaired[1]
    assert await server.call_tool("combat_use_activity", repair_request) == repaired
    final = await _call(server, "character_query", {
        "view": "get", "payload": {"character_id": defender["id"]},
    })
    assert 10 < final["sheet"]["combat"]["hp"]["value"] <= 20
    assert next(item for item in final["sheet"]["content"]["activities"]
                if item["id"] == repair["id"])["uses"]["value"] == 2
    await end(defender["id"], "defender-repair-end")
    await end(target["id"], "target-repair-round-end")
    await end(owner["id"], "owner-no-command")
    dodging = (await current())["state"]["combat"]["combatants"][1]
    assert dodging["turn_flags"]["dependent_default_dodge"] is True
    assert dodging["turn_flags"]["dodging"] is True
    assert dodging["turn_budget"]["main_action"] == 0
    assert dodging["turn_budget"]["reaction"] == 1
    rend = next(item for item in defender["sheet"]["inventory"]["items"]
                if item["name"] == "Force-Empowered Rend")
    # This exact archive contains the old Eberron printing, not the revised
    # spell-attack-modifier statblock. Never substitute the errata formula here.
    assert rend["mechanics"]["attack_bonus_override"] == 4
    assert rend["mechanics"]["damage_bonus_override"] == 2
    attack_action = {
        "weapon_id": rend["id"], "attack_mode": "melee", "context": {"spatial_facts": {
            "decision_id": "actual-defender-rend", "reason": "Visible adjacent sparring partner",
            "targetable": True, "in_range": True, "cover_degree": "none",
            "attacker_can_see_target": True, "target_can_see_attacker": True,
        }},
    }
    before = await current()
    with pytest.raises(ToolError):
        await _call(server, "combat_resolve_attack", {
            "campaign_id": campaign_id, "actor_id": defender["id"], "target_id": target["id"],
            "action": attack_action, "expected_revision": before["revision"],
            "idempotency_key": "uncommanded-rend",
        })
    assert await current() == before
    await end(defender["id"], "defender-dodge-end")
    await end(target["id"], "target-dodge-round-end")
    await _call(server, "combat_common_action", {
        **command_request, "expected_revision": (await current())["revision"],
        "idempotency_key": "command-rend",
    })
    await end(owner["id"], "owner-rend-command-end")
    before = await current()
    attack_request = {
        "campaign_id": campaign_id, "actor_id": defender["id"], "target_id": target["id"],
        "action": attack_action, "expected_revision": before["revision"],
        "idempotency_key": "commanded-rend",
    }
    preflight = await _call(server, "combat_preflight_attack", {
        key: value for key, value in attack_request.items()
        if key not in {"expected_revision", "idempotency_key"}
    })
    assert preflight["attack_bonus"] == 4
    assert await current() == before
    attack_response = await server.call_tool("combat_resolve_attack", attack_request)
    assert await server.call_tool("combat_resolve_attack", attack_request) == attack_response
    assert attack_response[1]["random_stream_receipt"]["position_after"] == 4
    assert json.loads(attack_response[0][0].text) == attack_response[1]
    attack = attack_response[1]["result"]
    assert attack["weapon_id"] == rend["id"]
    assert attack["natural"] == 19 and attack["total"] == 23 and attack["hit"] is True
    assert attack["damage"]["applied_amount"] == 5
    assert attack["damage"]["damage_type"] == "force"
    assert attack["damage"]["before_hp"] == 50 and attack["damage"]["after_hp"] == 45
    assert (await current())["state"]["random_stream"]["position"] == 4
    target_after = await _call(server, "character_query", {
        "view": "get", "payload": {"character_id": target["id"]},
    })
    assert target_after["sheet"]["combat"]["hp"]["value"] == 45
    assert (await current())["state"]["combat"]["combatants"][1]["turn_budget"][
        "main_action"
    ] == 0
    return repair_request, repaired, attack_request, attack_response


@pytest.mark.fresh_database
def test_locked_artificer_build_creates_and_commands_defender(tmp_path: Path) -> None:
    library = _locked_official_library()
    workspace = Path(__file__).resolve().parents[3]
    config = McpConfig(
        home=tmp_path / "home", database_url=None, chroma_url=None, chroma_path_override=None,
        dnd_skills_dir=workspace / "skills", modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=True, official_content_library=library,
    )

    async def exercise() -> None:
        runtime = create_server(config)
        sessions = AsyncExitStack()
        server = _ProtocolTools(await sessions.enter_async_context(
            Client(runtime, mode="2026-07-28"),
        ))
        try:
            campaign = await _call(server, "campaign_create", {
                "name": "Actual Eberron Defender play", "edition": "2014",
                "idempotency_key": "campaign", "random_seed": "official-artificer-play-v10",
            })

            async def current() -> dict:
                return await _call(server, "campaign_query", {
                    "view": "get", "payload": {"campaign_id": campaign["id"]},
                })

            profile = await _call(server, "campaign_rules", {
                "campaign_id": campaign["id"], "action": "get_profile",
            })
            await _call(server, "content_pack", {
                "action": "activate", "payload": {
                    "campaign_id": campaign["id"], "kind": "addon",
                    "addon_id": _PREFIX + ".addon", "version": _VERSION,
                }, "expected_revision": profile["campaign_revision"], "idempotency_key": "activate",
            })
            sheet = default_character_sheet()
            sheet["abilities"]["intelligence"]["score"] = 16
            owner = await _call(server, "character_create_from", {
                "mode": "direct", "payload": {
                    "campaign_id": campaign["id"], "name": "Battle Smith", "sheet": sheet,
                }, "idempotency_key": "owner",
            })

            async def apply(artifact: str, selection: dict | None = None) -> dict:
                nonlocal owner
                selected = await _selection_for(server, campaign["id"], artifact)
                selected.update(selection or {})
                request = {
                    "character_id": owner["id"], "artifact_id": artifact,
                    "selection": selected, "expected_revision": owner["revision"],
                    "idempotency_key": "apply-" + artifact,
                }
                owner = await _call(server, "character_content_apply", request)
                assert "revision" in owner, owner.get("status")
                assert await _call(server, "character_content_apply", request) == owner
                return owner

            await apply(_CLASS, {"starting_equipment": {"mode": "equipment", "choices": {
                "simple_weapons": [_SRD + "item.dagger", _SRD + "item.club"],
                "armor": [_SRD + "item.scale-mail"],
            }}})
            assert owner["class_materialization"]["spellcasting"]["spell_choices"] == {
                "cantrips_to_add": 2, "leveled_spells_to_add": 0,
            }
            for spell in ("mending", "light"):
                await apply(_SRD + "spell." + spell, {
                    "source_class": "Artificer", "method": "known",
                })
            for level in (2, 3):
                advanced = await _call(server, "character_state_change", {
                    "character_id": owner["id"], "action": "level_advance", "payload": {
                        "class_name": "Artificer", "hp_method": "fixed",
                        "reason": "Actual official build test",
                        "source_ref": "bundled:srd2014/03_Characterization/Beyond_1st_Level.md",
                    }, "expected_revision": owner["revision"], "idempotency_key": f"level-{level}",
                })
                owner = advanced["character"]
            await apply(_PREFIX + ".subclass.battle-smith", {"target_class_name": "Artificer"})
            for feature in _FEATURES[:-1]:
                await apply(_PREFIX + ".feature." + feature, {
                    "infusions": ["Enhanced Arcane Focus", "Enhanced Defense",
                                  "Enhanced Weapon", "Repeating Shot"],
                } if feature == "infuse-item" else {})
            before = await current()
            with pytest.raises(ToolError, match="feature entitlement"):
                await _call(server, "addon_actor_instantiate", {
                    "campaign_id": campaign["id"], "artifact_id": _DEFENDER,
                    "owner_character_id": owner["id"],
                    "expected_revision": before["revision"], "idempotency_key": "not-entitled",
                })
            assert await current() == before
            await apply(_PREFIX + ".feature.steel-defender")
            prepared = await _call(server, "character_spell_prepare", {
                "character_id": owner["id"], "mode": "replace_all", "payload": {
                    "spell_ids": [_SRD + "spell.cure-wounds"], "event": "setup",
                }, "expected_revision": owner["revision"], "idempotency_key": "prepare",
            })
            owner = prepared["character"]
            assert {item["id"] for item in owner["sheet"]["content"]["features"]} >= {
                _PREFIX + ".feature." + feature for feature in _FEATURES
            }
            assert len(owner["sheet"]["inventory"]["items"]) == 7
            # Starting equipment supplies thieves' tools, not smith's tools;
            # proficiency alone must not create an item. For this lifecycle
            # encounter the DM awards one real SRD tool artifact via public
            # content application. This does not exercise Right Tool for the Job.
            assert not any(item["name"].casefold() == "smith's tools"
                           for item in owner["sheet"]["inventory"]["items"])
            await apply(_SRD + "item.smith-s-tools")
            tools = [item for item in owner["sheet"]["inventory"]["items"]
                     if item["source_key"] == _SRD + "item.smith-s-tools"]
            assert len(tools) == 1 and tools[0]["quantity"] == 1
            assert tools[0]["weight_oz"] == 128
            create_request = {
                "campaign_id": campaign["id"], "artifact_id": _DEFENDER,
                "owner_character_id": owner["id"],
                "expected_revision": (await current())["revision"], "idempotency_key": "defender",
            }
            created = await _call(server, "addon_actor_instantiate", create_request)
            assert await _call(server, "addon_actor_instantiate", create_request) == created
            defender = created["character"]
            assert defender["sheet"]["combat"]["hp"]["max"] == 20
            relation = (await current())["state"]["dependent_actor_relations"][0]
            assert relation["source_pack_id"] == _PREFIX
            assert relation["source_pack_version"] == _RULE_VERSION
            assert relation["owner_character_id"] == owner["id"]
            assert relation["dependent_actor_id"] == defender["id"]
            before = await current()
            with pytest.raises(ToolError):
                await _call(server, "addon_actor_instantiate", {
                    **create_request, "expected_revision": before["revision"],
                    "idempotency_key": "duplicate-defender",
                })
            assert await current() == before
            repair_request, repaired, attack_request, attack = await _exercise_defender_combat(
                server, campaign["id"], owner, defender,
            )
            lifecycle_replays, lifecycle_final = await _exercise_defender_lifecycle(
                server, campaign["id"], owner, defender,
            )
            final = await _call(server, "character_query", {
                "view": "get", "payload": {"character_id": defender["id"]},
            })
            final_campaign = await current()
            await sessions.aclose()
            close_server(runtime)
            runtime = create_server(config)
            server = _ProtocolTools(await sessions.enter_async_context(
                Client(runtime, mode="2026-07-28"),
            ))
            assert await current() == final_campaign
            assert await _call(server, "character_query", {
                "view": "get", "payload": {"character_id": defender["id"]},
            }) == final
            assert await _call(server, "addon_actor_instantiate", create_request) == created
            assert await server.call_tool("combat_use_activity", repair_request) == repaired
            assert await server.call_tool("combat_resolve_attack", attack_request) == attack
            for tool, request, response in lifecycle_replays:
                assert await server.call_tool(tool, request) == response
            assert await _call(server, "character_query", {
                "view": "get", "payload": {"character_id": owner["id"]},
            }) == lifecycle_final[1]
            assert await current() == final_campaign
        finally:
            await sessions.aclose()
            close_server(runtime)

    asyncio.run(exercise())
