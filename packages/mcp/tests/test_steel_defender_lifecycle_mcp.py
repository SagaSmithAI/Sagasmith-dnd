"""Public protocol lifecycle regressions using a reviewed synthetic addon."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import sagasmith_core.idempotency as idempotency_module
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import add_inventory_item, default_character_sheet
from sagasmith_dnd.standard_spell_ids import CORE_MENDING_MECHANIC_ID, CORE_MENDING_SPELL_ID

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from scripts.regression_official_expansions import _ProtocolTools
from tests.authoring_helpers import import_and_activate_addon_fixture
from tests.test_addon_selection_contracts_mcp import _bound_defender_fixture_artifacts
from tests.test_official_expansions_mcp import _call


async def _create_bound_defender(server, config: McpConfig) -> tuple[dict, dict, dict]:
    artifact, feature, source_key, source_text = _bound_defender_fixture_artifacts()
    campaign = await _call(server, "campaign_create", {
        "name": "Defender lifecycle", "edition": "2014",
        "random_seed": "defender-lifecycle-v11", "idempotency_key": "campaign",
    })
    await import_and_activate_addon_fixture(
        _call, server, campaign["id"], config.home,
        manifest={
            "id": "dnd5e.addon.binding", "version": "1.0.0", "title": "Bound Defender",
            "namespace": "dnd5e.addon.binding", "system_id": "dnd5e",
            "editions": ["2014"], "capabilities": [],
        },
        artifacts=[feature, artifact], mechanics=[],
        expected_revision=campaign["revision"], request_key="addon",
        source_key_override=source_key, source_chunks_override=[source_text],
    )
    sheet = default_character_sheet()
    sheet["progression"]["level"] = 3
    sheet["progression"]["classes"] = [
        {"name": "Artificer", "level": 3, "subclass": "Battle Smith", "hit_die": 8},
    ]
    sheet["abilities"]["intelligence"]["score"] = 16
    sheet["spellcasting"]["ability"] = "intelligence"
    sheet["spellcasting"]["spell_slots"] = {
        "1": {"value": 1, "max": 1, "unlimited": False},
    }
    sheet["content"]["spells"] = [{
        "id": CORE_MENDING_SPELL_ID, "name": "Mending", "level": 0,
        "grant": {"source_type": "class", "source_key": "artificer", "method": "known"},
        "access": {"known": True, "prepared": True},
        "mechanic_refs": [CORE_MENDING_MECHANIC_ID],
        "definition": {
            "casting_time": "1 minute", "range": {"kind": "touch"},
            "duration": {"kind": "instantaneous", "concentration": False},
            "components": {
                "verbal": True, "somatic": True, "material": True,
                "material_description": "two lodestones",
            },
            "effect": "Mending fixture; component completeness is outside this test.",
        },
        "pack_id": "dnd5e.content.srd2014", "pack_version": "1.16.0",
        "rule_refs": ["bundled:srd2014/07_Spells/Spells_Each/Mending.md"],
    }]
    sheet, _ = add_inventory_item(sheet, {
        "id": "smiths-tools", "name": "Smith's Tools", "kind": "tool", "quantity": 1,
    })
    owner = await _call(server, "character_create_from", {
        "mode": "direct", "payload": {
            "campaign_id": campaign["id"], "name": "Battle Smith", "sheet": sheet,
        }, "idempotency_key": "owner",
    })
    owner = await _call(server, "character_content_apply", {
        "character_id": owner["id"], "artifact_id": feature["id"],
        "expected_revision": owner["revision"], "idempotency_key": "entitle",
    })
    current = await _call(server, "campaign_query", {
        "view": "get", "payload": {"campaign_id": campaign["id"]},
    })
    created = await _call(server, "addon_actor_instantiate", {
        "campaign_id": campaign["id"], "artifact_id": artifact["id"],
        "owner_character_id": owner["id"], "expected_revision": current["revision"],
        "idempotency_key": "defender",
    })
    return campaign, owner, created["character"]


async def _exercise_defender_lifecycle(server, campaign_id: str, owner: dict, defender: dict):
    """Same public actions for cheap synthetic and opt-in actual archive acceptance."""
    replays = []

    async def snapshot():
        campaign = await _call(server, "campaign_query", {
            "view": "get", "payload": {"campaign_id": campaign_id},
        })
        actors = [await _call(server, "character_query", {
            "view": "get", "payload": {"character_id": actor["id"]},
        }) for actor in (owner, defender)]
        return campaign, *actors

    async def commit(tool, request):
        response = await server.call_tool(tool, request)
        after = await snapshot()
        assert await server.call_tool(tool, request) == response
        assert await snapshot() == after
        replays.append((tool, request, response))
        return response

    async def end_combat(key):
        campaign, _, _ = await snapshot()
        if campaign["state"].get("combat", {}).get("active"):
            await _call(server, "combat_end", {
                "campaign_id": campaign_id, "outcome": {
                    "status": "interrupted", "summary": "End the lifecycle exercise combat.",
                }, "expected_revision": campaign["revision"], "idempotency_key": key,
            })

    def repair_uses(actor):
        return next(item for item in actor["sheet"]["content"]["activities"]
                    if item["name"].startswith("Repair"))["uses"]["value"]

    await end_combat("lifecycle-end-existing-combat")
    campaign, _, current_defender = await snapshot()
    await _call(server, "combat_start", {
        "campaign_id": campaign_id, "positioning_mode": "agent",
        "participant_ids": [owner["id"], defender["id"]],
        "participant_config": [{"actor_id": owner["id"], "initiative": 20}],
        "expected_revision": campaign["revision"], "idempotency_key": "lifecycle-combat",
    })
    repair_id = next(item["id"] for item in current_defender["sheet"]["content"]["activities"]
                     if item["name"].startswith("Repair"))
    remaining = repair_uses(current_defender)
    assert 0 < remaining <= 3
    for index in range(remaining + 1):
        campaign, _, _ = await snapshot()
        await _call(server, "combat_common_action", {
            "campaign_id": campaign_id, "actor_id": owner["id"],
            "action": "command_dependent", "target_id": defender["id"],
            "expected_revision": campaign["revision"],
            "idempotency_key": f"lifecycle-command-{index}",
        })
        campaign, _, _ = await snapshot()
        await _call(server, "combat_end_turn", {
            "campaign_id": campaign_id, "actor_id": owner["id"],
            "expected_revision": campaign["revision"], "idempotency_key": f"owner-end-{index}",
        })
        before = await snapshot()
        request = {
            "campaign_id": campaign_id, "actor_id": defender["id"], "activity_id": repair_id,
            "declaration": {"target_id": defender["id"]},
            "expected_revision": before[0]["revision"],
            "idempotency_key": f"lifecycle-repair-{index}",
        }
        if index == remaining:
            with pytest.raises(ToolError):
                await server.call_tool("combat_use_activity", request)
            assert await snapshot() == before
            break
        await commit("combat_use_activity", request)
        campaign, _, current_defender = await snapshot()
        assert repair_uses(current_defender) == remaining - index - 1
        await _call(server, "combat_end_turn", {
            "campaign_id": campaign_id, "actor_id": defender["id"],
            "expected_revision": campaign["revision"],
            "idempotency_key": f"defender-end-{index}",
        })
    await end_combat("lifecycle-end-repair-combat")

    # Mending is separate from Repair: two d6, one minute, no daily use or slot.
    _, _, current_defender = await snapshot()
    await _call(server, "character_state_change", {
        "character_id": defender["id"], "action": "damage",
        "payload": {"parts": [{"amount": 5, "damage_type": "force"}]},
        "expected_revision": current_defender["revision"], "idempotency_key": "mending-damage",
    })
    before = await snapshot()
    spatial = {
        "distance_ft": 5, "default_resolver": "agent", "ruling_kind": "agent_dm_adjudication",
        "reason": "The caster touches the source-bound defender.",
    }
    mending_request = {
        "character_id": owner["id"], "action": "cast_spell",
        "payload": {
            "spell_id": CORE_MENDING_SPELL_ID, "target_character_ids": [defender["id"]],
            "declaration": {"spatial_facts": spatial},
        },
        "expected_revision": before[1]["revision"], "idempotency_key": "lifecycle-mending",
    }
    for key, changed in [
        ("range", {"declaration": {"spatial_facts": {**spatial, "distance_ft": 6}}}),
        ("missing-spell", {"spell_id": "dnd5e.content.srd2014.spell.not-recorded"}),
        ("not-defender", {"target_character_ids": [owner["id"]]}),
    ]:
        with pytest.raises(ToolError):
            await server.call_tool("character_action", {
                **mending_request, "payload": {**mending_request["payload"], **changed},
                "idempotency_key": "reject-mending-" + key,
            })
        assert await snapshot() == before
    mended = await commit("character_action", mending_request)
    after = await snapshot()
    assert mended[1]["result"]["random_stream_receipt"]["draw_count"] == 2
    healing = mended[1]["result"]["result"]
    assert healing["automatic_effect"] == "steel_defender_mending"
    assert healing["roll"]["expression"] == "2d6"
    assert 2 <= healing["roll"]["total"] <= 12
    source = before[0]["state"]["dependent_actor_relations"][0]
    receipt = next(item for item in healing["rule_receipts"]
                   if item["mechanic_id"] == "dnd5e.expansion.steel_defender.mending")
    assert receipt["citations"] == [{
        "source_artifact_id": source["source_artifact_id"],
        "source_pack_id": source["source_pack_id"],
        "source_pack_version": source["source_pack_version"],
        "reviewed_expression_hash": source["template_binding"]["reviewed_expression_hash"],
    }]
    assert after[0]["state"]["game_time"]["elapsed_ticks"] == (
        before[0]["state"]["game_time"]["elapsed_ticks"] + 10
    )
    assert after[0]["state"]["random_stream"]["position"] == (
        before[0]["state"]["random_stream"]["position"] + 2
    )
    hp_before, hp_after = (item[2]["sheet"]["combat"]["hp"] for item in (before, after))
    assert hp_after["value"] == min(
        hp_after["max"], hp_before["value"] + healing["roll"]["total"],
    )
    assert repair_uses(after[2]) == 0
    assert after[1]["sheet"]["spellcasting"]["spell_slots"] == (
        before[1]["sheet"]["spellcasting"]["spell_slots"]
    )

    # X/Day recharges on a long rest, not a short rest.
    for rest_type, minutes, expected_uses in [("short_rest", 60, 0), ("long_rest", 480, 3)]:
        campaign, current_owner, current_defender = await snapshot()
        await commit("campaign_change", {
            "campaign_id": campaign_id, "action": "party_rest",
            "payload": {
                "rest_type": rest_type, "duration_minutes": minutes,
                "members": [
                    {"character_id": actor["id"], "expected_revision": actor["revision"]}
                    for actor in (current_owner, current_defender)
                ],
            },
            "expected_revision": campaign["revision"], "idempotency_key": "lifecycle-" + rest_type,
        })
        after = await snapshot()
        assert repair_uses(after[2]) == expected_uses
        assert after[0]["state"]["game_time"]["elapsed_ticks"] == (
            campaign["state"]["game_time"]["elapsed_ticks"] + minutes * 10
        )
        if rest_type == "short_rest":
            for actor in after[1:]:
                window = actor["sheet"]["combat"].get("short_rest_hit_dice")
                if window:
                    current, _, _ = await snapshot()
                    await commit("campaign_change", {
                        "campaign_id": campaign_id, "action": "short_rest_hit_die",
                        "payload": {
                            "character_id": actor["id"],
                            "expected_character_revision": actor["revision"],
                            "decision": "stop",
                            "rest_completed_elapsed_ticks": window["rest_completed_elapsed_ticks"],
                        },
                        "expected_revision": current["revision"],
                        "idempotency_key": "lifecycle-decline-dice-" + actor["id"],
                    })

    _, _, current_defender = await snapshot()
    await _call(server, "character_state_change", {
        "character_id": defender["id"], "action": "damage",
        "payload": {"parts": [{"amount": 100, "damage_type": "force"}]},
        "expected_revision": current_defender["revision"], "idempotency_key": "lifecycle-death",
    })
    before = await snapshot()
    with pytest.raises(ToolError):
        await server.call_tool("character_action", {
            **mending_request, "expected_revision": before[1]["revision"],
            "idempotency_key": "reject-mending-dead",
        })
    assert await snapshot() == before
    revival_request = {
        "character_id": owner["id"], "action": "revive_steel_defender",
        "payload": {
            "dependent_actor_id": defender["id"], "slot_level": 1, "spatial_facts": spatial,
        },
        "expected_revision": before[1]["revision"], "idempotency_key": "lifecycle-revival",
    }
    await _call(server, "character_state_change", {
        "character_id": owner["id"], "action": "effect_add", "payload": {
            "effect": {
                "id": "revival-stun", "name": "Revival timing stun", "kind": "timed_conditions",
                "active": True, "duration": {"period": "round", "remaining": 1},
                "changes": [{"path": "conditions", "mode": "add", "value": "stunned"}],
            },
        },
        "expected_revision": before[1]["revision"], "idempotency_key": "lifecycle-stun",
    })
    stunned = await snapshot()
    assert "stunned" in stunned[1]["sheet"]["conditions"]
    with pytest.raises(ToolError, match="incapacitated"):
        await server.call_tool("character_action", {
            **revival_request, "expected_revision": stunned[1]["revision"],
        })
    assert await snapshot() == stunned
    await _call(server, "character_state_change", {
        "character_id": owner["id"], "action": "effect_remove",
        "payload": {"effect_id": "revival-stun"}, "expected_revision": stunned[1]["revision"],
        "idempotency_key": "lifecycle-unstun",
    })
    before = await snapshot()
    revival_request["expected_revision"] = before[1]["revision"]
    await commit("character_action", revival_request)
    after = await snapshot()
    assert after[0]["state"]["game_time"]["elapsed_ticks"] == (
        before[0]["state"]["game_time"]["elapsed_ticks"] + 10
    )
    assert after[1]["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] == (
        before[1]["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] - 1
    )
    hp = after[2]["sheet"]["combat"]["hp"]
    assert hp["value"] == hp["max"] and hp["temp"] == 0
    assert "dead" not in after[2]["sheet"]["conditions"]
    assert repair_uses(after[2]) == 3
    relation = after[0]["state"]["dependent_actor_relations"][0]
    assert relation["status"] == "active" and relation["death_elapsed_ticks"] is None
    return replays, after


@pytest.mark.parametrize("compressed_receipts", [False, True])
def test_defender_mending_rest_and_revival_protocol_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, compressed_receipts: bool,
) -> None:
    if compressed_receipts:
        # Exercise the real compressed persistence path without loading a large
        # commercial archive. The normal threshold is 64 KiB; no codec is mocked.
        monkeypatch.setattr(idempotency_module, "_COMPRESSED_RESPONSE_THRESHOLD", 1)
    config = McpConfig(
        home=tmp_path / "home", database_url=None, chroma_url=None,
        chroma_path_override=None, dnd_skills_dir=tmp_path / "skills",
        modulegen_skills_dir=tmp_path / "modulegen", auto_seed_rules=False,
    )

    async def exercise():
        runtime = create_server(config)
        try:
            async with Client(runtime, mode="2026-07-28") as client:
                server = _ProtocolTools(client)
                campaign, owner, defender = await _create_bound_defender(server, config)
                replays, after = await _exercise_defender_lifecycle(
                    server, campaign["id"], owner, defender,
                )
        finally:
            close_server(runtime)
        restarted = create_server(config)
        try:
            async with Client(restarted, mode="2026-07-28") as client:
                server = _ProtocolTools(client)
                for tool, request, response in replays:
                    assert await server.call_tool(tool, request) == response
                assert await _call(server, "campaign_query", {
                    "view": "get", "payload": {"campaign_id": campaign["id"]},
                }) == after[0]
                for actor, expected in zip((owner, defender), after[1:], strict=True):
                    assert await _call(server, "character_query", {
                        "view": "get", "payload": {"character_id": actor["id"]},
                    }) == expected
        finally:
            close_server(restarted)

    asyncio.run(exercise())


@pytest.mark.parametrize("period,remaining", [("round", 1), ("minute", 1), ("round", 11)])
def test_noncombat_revival_rejects_owner_incapacitated_at_start(
    tmp_path: Path, period: str, remaining: int,
) -> None:
    # 2014 Conditions: incapacitated creatures cannot take actions; stunned
    # includes incapacitated. Expiration during the minute cannot pay an earlier action.
    config = McpConfig(
        home=tmp_path / "home", database_url=None, chroma_url=None,
        chroma_path_override=None, dnd_skills_dir=tmp_path / "skills",
        modulegen_skills_dir=tmp_path / "modulegen", auto_seed_rules=False,
    )

    async def exercise() -> None:
        runtime = create_server(config)
        try:
            async with Client(runtime, mode="2026-07-28") as client:
                server = _ProtocolTools(client)
                campaign, owner, defender = await _create_bound_defender(server, config)

                async def snapshot() -> tuple[dict, dict, dict]:
                    current = await _call(server, "campaign_query", {
                        "view": "get", "payload": {"campaign_id": campaign["id"]},
                    })
                    actors = [await _call(server, "character_query", {
                        "view": "get", "payload": {"character_id": actor["id"]},
                    }) for actor in (owner, defender)]
                    return current, *actors

                await _call(server, "character_state_change", {
                    "character_id": defender["id"], "action": "damage",
                    "payload": {"parts": [{"amount": 100, "damage_type": "force"}]},
                    "expected_revision": defender["revision"], "idempotency_key": "kill",
                })
                _, current_owner, _ = await snapshot()
                await _call(server, "character_state_change", {
                    "character_id": owner["id"], "action": "effect_add", "payload": {
                        "effect": {
                            "id": "temporary-stun", "name": "Temporary stun",
                            "kind": "timed_conditions", "active": True,
                            "duration": {"period": period, "remaining": remaining},
                            "changes": [{"path": "conditions", "mode": "add", "value": "stunned"}],
                        },
                    },
                    "expected_revision": current_owner["revision"], "idempotency_key": "stun",
                })
                before = await snapshot()
                assert "stunned" in before[1]["sheet"]["conditions"]
                assert "dead" in before[2]["sheet"]["conditions"]
                assert before[0]["state"]["dependent_actor_relations"][0]["status"] == "dead"
                request = {
                    "character_id": owner["id"], "action": "revive_steel_defender",
                    "payload": {
                        "dependent_actor_id": defender["id"], "slot_level": 1,
                        "spatial_facts": {
                            "distance_ft": 5, "default_resolver": "agent",
                            "ruling_kind": "agent_dm_adjudication",
                            "reason": "The owner and destroyed defender are within reach.",
                        },
                    },
                    "expected_revision": before[1]["revision"], "idempotency_key": "revive",
                }
                with pytest.raises(ToolError, match="incapacitated"):
                    await server.call_tool("character_action", request)
                assert await snapshot() == before

                # A rejected call must not consume its key. Remove the blocker
                # explicitly, then use the same request/key at the new revision.
                await _call(server, "character_state_change", {
                    "character_id": owner["id"], "action": "effect_remove",
                    "payload": {"effect_id": "temporary-stun"},
                    "expected_revision": before[1]["revision"], "idempotency_key": "unstun",
                })
                ready = await snapshot()
                await _call(server, "character_state_change", {
                    "character_id": owner["id"], "action": "effect_add", "payload": {
                        "effect": {
                            "id": "temporary-poison", "name": "Temporary poison",
                            "kind": "timed_conditions", "active": True,
                            "duration": {"period": "round", "remaining": 1},
                            "changes": [{"path": "conditions", "mode": "add", "value": "poisoned"}],
                        },
                    },
                    "expected_revision": ready[1]["revision"], "idempotency_key": "poison",
                })
                ready = await snapshot()
                request["expected_revision"] = ready[1]["revision"]
                response = await server.call_tool("character_action", request)
                after = await snapshot()
                assert after[0]["state"]["game_time"]["elapsed_ticks"] == (
                    ready[0]["state"]["game_time"]["elapsed_ticks"] + 10
                )
                assert after[1]["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] == 0
                assert "poisoned" not in after[1]["sheet"]["conditions"]
                assert next(item for item in after[1]["sheet"]["effects"]
                            if item["id"] == "temporary-poison")["active"] is False
                hp = after[2]["sheet"]["combat"]["hp"]
                assert hp["value"] == hp["max"] and hp["temp"] == 0
                assert "dead" not in after[2]["sheet"]["conditions"]
                assert after[0]["state"]["dependent_actor_relations"][0]["status"] == "active"
                assert await server.call_tool("character_action", request) == response
                assert await snapshot() == after
        finally:
            close_server(runtime)

        restarted = create_server(config)
        try:
            async with Client(restarted, mode="2026-07-28") as client:
                server = _ProtocolTools(client)
                assert await server.call_tool("character_action", request) == response
                assert await snapshot() == after
        finally:
            close_server(restarted)

    asyncio.run(exercise())
