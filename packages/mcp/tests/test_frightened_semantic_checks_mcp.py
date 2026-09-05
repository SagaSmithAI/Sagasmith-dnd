from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.random_stream import CampaignRandomStream, use_random_stream
from test_frightened_checks_mcp import raw
from test_official_expansions_mcp import _call, _config

from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import finalize_and_activate_module


@pytest.mark.parametrize("visibility", ["visible", "unseen", "missing"])
def test_semantic_ability_and_contest_preserve_fear_context_and_atomicity(
    tmp_path: Path, visibility: str
) -> None:
    async def exercise() -> None:
        module_root = tmp_path / "modules"
        module_root.mkdir()
        excerpt = (
            "Survey. The observer first makes a Wisdom check. The investigator then makes "
            "a Wisdom check and contests its Wisdom against the observer's Wisdom."
        )
        source_path = module_root / "survey.md"
        source_path.write_text("# Survey Chamber\n\n## Encounter\n\n" + excerpt, encoding="utf-8")
        config = replace(_config(tmp_path), module_import_roots=(module_root,))
        server = create_server(config)
        try:
            campaign = await _call(server, "campaign_create", {
                "name": "Semantic frightened checks", "edition": "2014",
                "idempotency_key": "campaign",
            })
            campaign_id = campaign["id"]

            async def snapshot() -> dict:
                return await _call(server, "campaign_query", {
                    "view": "get", "payload": {"campaign_id": campaign_id},
                })

            staged = await _call(server, "module_draft", {
                "campaign_id": campaign_id, "action": "start", "payload": {
                    "source_path": str(source_path), "source_key": "survey-chamber",
                    "title": "Survey Chamber",
                }, "idempotency_key": "stage",
            })
            await finalize_and_activate_module(
                _call, server, campaign_id, staged, source_key="survey-chamber",
                title="Survey Chamber", portable_id="dnd5e.module.frightened-survey-test",
            )
            chunks = await _call(server, "module_search", {
                "campaign_id": campaign_id, "query": "Survey observer investigator Wisdom",
                "top_k": 3,
            })
            expanded = await _call(server, "module_expand", {"chunk_id": chunks[0]["id"]})
            base_sheet = default_character_sheet()
            base_sheet["edition"] = "2014"
            base_sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}

            async def make_actor(name: str, sheet: dict) -> dict:
                return await _call(server, "character_create_from", {
                    "mode": "direct", "payload": {
                        "campaign_id": campaign_id, "name": name, "sheet": sheet,
                        "character_type": "monster" if name == "Investigator" else "pc",
                    }, "idempotency_key": name,
                })

            fear_source = await make_actor("Fear source", base_sheet)
            observer = await make_actor("Observer", base_sheet)
            sheet = deepcopy(base_sheet)
            sheet["conditions"] = ["frightened"]
            sheet["effects"] = [{
                "id": "fear", "name": "Fear", "kind": "timed_conditions", "active": True,
                "source": fear_source["id"],
                "duration": {"period": "source_turn_start", "remaining": 1},
                "changes": [{"path": "conditions", "mode": "add", "value": "frightened"}],
            }]
            plan_id = "module.survey.checks"
            sheet["content"]["activities"] = [{
                "id": "survey", "name": "Survey", "description": excerpt,
                "activation": {"type": "action", "cost": 1},
                "uses": {"value": 0, "max": 0, "unlimited": True},
                "choices": {"resolution_plan": {"id": plan_id, "fingerprint": "compiled"}},
                "resolution_plan": {
                    "schema_version": 2, "id": plan_id, "source_card_id": "survey",
                    "source_card_kind": "monster_action", "trigger": "action",
                    "slots": {
                        "source_actor": {
                            "kind": "actor_id", "owner": "agent", "description": "Investigator",
                        },
                        "observer": {
                            "kind": "actor_id", "owner": "agent", "description": "Observer",
                        },
                    },
                    "steps": [
                        {"id": "first", "op": "check.ability", "args": {
                            "actor_id": {"$slot": "observer"}, "ability": "wisdom", "dc": 10,
                        }},
                        {"id": "ability", "op": "check.ability", "args": {
                            "actor_id": {"$slot": "source_actor"}, "ability": "wisdom", "dc": 10,
                        }},
                        {"id": "contest", "op": "check.contest", "args": {
                            "source_actor_id": {"$slot": "source_actor"},
                            "target_actor_id": {"$slot": "observer"},
                            "source_ability": "wisdom", "target_ability": "wisdom",
                        }},
                    ],
                    "citations": [{
                        "source": "module:survey-chamber", "source_ref": expanded["source_ref"],
                        "source_excerpt": excerpt,
                    }],
                },
            }]
            actor = await make_actor("Investigator", sheet)
            current = await snapshot()
            phase = await _call(server, "game_phase", {
                "campaign_id": campaign_id, "action": "set", "tool_profile": "play",
                "expected_revision": current["revision"], "idempotency_key": "play",
            })
            participants = [
                {"actor_id": actor["id"], "initiative": 30},
                {"actor_id": observer["id"], "initiative": 20},
            ]
            if visibility != "missing":
                participants.append({
                    "actor_id": fear_source["id"], "initiative": 10,
                    "visible_to_actor_ids": [] if visibility == "unseen" else [actor["id"]],
                })
            await raw(server, "combat_start", {
                "campaign_id": campaign_id, "positioning_mode": "agent",
                "participant_ids": [item["actor_id"] for item in participants],
                "participant_config": participants, "scene_id": expanded["scene"]["id"],
                "expected_revision": phase["campaign_revision"], "idempotency_key": "start",
            })
            before_contract = await snapshot()
            activity_args = {
                "campaign_id": campaign_id, "actor_id": actor["id"], "activity_id": "survey",
                "expected_revision": before_contract["revision"], "idempotency_key": "contract",
            }
            pending = await raw(server, "combat_use_activity", activity_args)
            contract = pending["result"]["resolution_plan_contract"]
            assert await snapshot() == before_contract
            commitment = {
                "application_id": "survey-once", "plan_id": plan_id,
                "plan_fingerprint": contract["plan_fingerprint"], "source_card_id": "survey",
                "source_card_kind": "monster_action",
                "bindings": {"source_actor": actor["id"], "observer": observer["id"]},
                "agent_ruling": {
                    "application_id": "survey-once", "default_resolver": "agent",
                    "ruling_kind": "agent_dm_adjudication",
                    "decision": "The investigator and observer perform the recorded survey.",
                    "reason": "Both actors are in the active source scene.",
                    "source_ref": expanded["source_ref"], "source_excerpt": excerpt,
                },
            }
            paid = await raw(server, "combat_use_activity", {
                **activity_args, "declaration": {"agent_resolution_commitment": commitment},
                "idempotency_key": "pay",
            })
            normalized = paid["result"]["declaration"]["agent_resolution_commitment"]
            # This API records payment separately; an execution pause must preserve that
            # payment exactly and must not commit any partial check or extra RNG draw.
            before = await snapshot()
            assert before["revision"] == before_contract["revision"] + 1
            arguments = {
                "campaign_id": campaign_id, "actor_id": actor["id"], "action": "execute_plan",
                "payload": {"commitment": normalized}, "expected_revision": before["revision"],
                "idempotency_key": "execute",
            }
            with pytest.raises(ToolError, match="revision conflict"):
                await raw(server, "combat_choice", {
                    **arguments, "expected_revision": before["revision"] - 1,
                })
            assert await snapshot() == before
            stream = CampaignRandomStream.from_campaign_state(
                campaign_id, before["state"], operation="combat_choice", idempotency_key="execute",
                campaign_revision=before["revision"],
            )
            with use_random_stream(stream):
                outer = await raw(server, "combat_choice", arguments)
            settled = outer.get("result", outer)
            if visibility == "missing":
                assert settled["status"] == "pending_ruling"
                assert settled["committed"] is False
                assert stream.draw_count == 0
                assert await snapshot() == before
            else:
                assert settled["status"] == "committed"
                results = settled["result"]["results"]
                dice = 2 if visibility == "visible" else 1
                assert len(results["first"]["rolls"]) == 1
                assert len(results["ability"]["rolls"]) == dice
                assert len(results["contest"]["source_check"]["rolls"]) == dice
                assert len(results["contest"]["target_check"]["rolls"]) == 1
                assert stream.draw_count == 2 + 2 * dice
                for check in (results["ability"], results["contest"]["source_check"]):
                    assert "dnd5e.core.check.frightened" in {
                        receipt["mechanic_id"] for receipt in check["rule_receipts"]
                    }
                assert (await snapshot())["revision"] == before["revision"] + 1
            after = await snapshot()
            assert await raw(server, "combat_choice", arguments) == outer
            assert await snapshot() == after
            close_server(server)
            server = create_server(config)
            assert await snapshot() == after
            assert await raw(server, "combat_choice", arguments) == outer
            assert await snapshot() == after
        finally:
            close_server(server)

    asyncio.run(exercise())
