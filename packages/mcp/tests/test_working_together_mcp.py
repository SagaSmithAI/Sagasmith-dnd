"""Public Working Together keeps source, RNG, cards and retry in one transaction."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_core.idempotency import IdempotencyService
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd_runtime.application_support import StateMutationService

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import finalize_and_activate_module
from tests.sight_rot_test_support import install_symptomatic_sight_rot


async def call(server, name, arguments):
    _, result = await server.call_tool(name, arguments)
    return result


async def read(server, name, arguments):
    result = await call(server, name, arguments)
    return result.get("result", result)


def test_working_together_source_eligibility_atomicity_and_restart(tmp_path, monkeypatch):
    source = tmp_path / "lock.md"
    excerpt = (
        "The complex gate lock requires thieves' tools proficiency and a DC 15 Dexterity check. "
        "Two trained characters can productively work together on its paired tumblers."
    )
    source.write_text(f"# Gate\n\n## Paired lock\n\n{excerpt}\n", encoding="utf-8")
    config = McpConfig(
        home=tmp_path / "home", database_url=None, chroma_url=None, chroma_path_override=None,
        dnd_skills_dir=Path(__file__).resolve().parents[3] / "skills",
        modulegen_skills_dir=tmp_path / "modulegen", module_import_roots=(tmp_path,),
        auto_seed_rules=False,
    )

    async def exercise():
        server = create_server(config)
        try:
            campaign = await read(server, "campaign_create", {
                "name": "Paired lock", "edition": "2014", "idempotency_key": "campaign",
            })
            campaign_id = campaign["id"]
            staged = await read(server, "module_draft", {
                "campaign_id": campaign_id, "action": "start", "idempotency_key": "draft",
                "payload": {"source_path": str(source), "source_key": "paired-lock",
                            "title": "Gate"},
            })

            async def helper_call(server, name, arguments):
                value = await read(server, name, arguments)
                if isinstance(value, dict) and "action" in value and "result" in value:
                    return value["result"]
                return value

            await finalize_and_activate_module(
                helper_call, server, campaign_id, staged, source_key="paired-lock", title="Gate",
                portable_id="dnd5e.module.paired-lock",
            )
            hits = await read(server, "module_search", {
                "campaign_id": campaign_id, "query": "complex gate lock", "top_k": 3,
            })
            expanded = await read(server, "module_expand", {"chunk_id": hits[0]["id"]})
            actors = []
            for name, score, tools in (("Leader", 16, ["Thieves' Tools"]),
                                       ("Helper", 12, ["Thieves' Tools"]), ("Untrained", 10, [])):
                sheet = default_character_sheet()
                sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
                sheet["progression"]["species"] = "human"
                sheet["abilities"]["dexterity"]["score"] = score
                sheet["traits"]["proficiencies"]["tools"] = tools
                actors.append(await read(server, "character_create_from", {
                    "mode": "direct", "payload": {
                        "campaign_id": campaign_id, "name": name, "sheet": sheet,
                    }, "idempotency_key": name,
                }))
            leader, helper, untrained = actors

            async def snapshot():
                return await read(server, "campaign_query", {
                    "view": "get", "payload": {"campaign_id": campaign_id},
                })

            async def actor_snapshot(identifier):
                return await read(server, "character_query", {
                    "view": "get", "payload": {"character_id": identifier},
                })

            infected_leader = await install_symptomatic_sight_rot(
                lambda name, arguments: call(server, name, arguments),
                campaign_id, leader["id"], key="wt-sight-rot",
                member_ids=[actor["id"] for actor in actors],
            )
            disease_state = next(
                item["metadata"]["disease_state"]
                for item in infected_leader["sheet"]["effects"]
                if item.get("kind") == "disease_state"
                and item["metadata"]["disease_state"]["disease_id"] == "sight_rot"
            )
            disease_penalty = -disease_state["sight_penalty"]

            current = await snapshot()
            await call(server, "game_phase", {
                "campaign_id": campaign_id, "action": "set", "tool_profile": "play",
                "expected_revision": current["revision"], "idempotency_key": "play",
            })
            before = await snapshot()
            before_cards = [await actor_snapshot(item["id"]) for item in actors]
            task = {
                "source_ref": expanded["source_ref"], "source_excerpt": excerpt,
                "reason": "Two trained lockpickers can manipulate the paired tumblers.",
                "dc": 15, "productive": True,
                "requirements": {"tools": ["Thieves' Tools"], "skills": [], "features": []},
            }
            args = {
                "campaign_id": campaign_id, "action": "working_together",
                "payload": {"actor_ids": [leader["id"], helper["id"]], "ability": "dexterity",
                            "tool": "Thieves' Tools", "task": task},
                "expected_revision": before["revision"], "idempotency_key": "unlock",
            }
            for index, patch in enumerate((
                {"bonus": 7}, {"advantage": True}, {"helper_eligible": True},
                {"actor_ids": [leader["id"], untrained["id"]]},
                {"actor_ids": [leader["id"], leader["id"]]},
                {"task": {**task, "productive": False}},
                {"task": {**task, "source_excerpt": "A made-up source with no locked passage."}},
            )):
                with pytest.raises(ToolError):
                    await call(server, "character_check", {
                        **args, "payload": {**args["payload"], **patch},
                        "idempotency_key": f"invalid-{index}",
                    })
            with pytest.raises(ToolError, match="revision conflict"):
                await call(server, "character_check", {**args, "expected_revision": 0})
            assert await snapshot() == before
            assert [await actor_snapshot(item["id"]) for item in actors] == before_cards

            tied_args = {**args, "idempotency_key": "tie", "payload": {
                **args["payload"], "ability": "strength", "tool": None,
            }}
            tied = await call(server, "character_check", tied_args)
            assert tied["status"] == "pending_ruling" and tied["committed"] is False
            assert tied["ruling_kind"] == "player_owned_choice"
            assert tied["missing"] == ["leader_id"]
            assert await snapshot() == before

            original_commit = StateMutationService.replace
            failed_rolls = []
            # Both leader and helper must be guarded even though neither spends a resource.
            for stale_id in (leader["id"], helper["id"]):
                injected = False

                def stale_card(self, *positional, **kwargs):
                    nonlocal injected
                    failed_rolls.append(
                        kwargs["campaign_state"]["resolution_log"][-1]["result"]["check"]["rolls"]
                    )
                    injected = any(u.character_id == stale_id for u in kwargs["character_updates"])
                    kwargs["character_updates"] = [
                        replace(u, expected_revision=u.expected_revision - 1)
                        if u.character_id == stale_id else u for u in kwargs["character_updates"]
                    ]
                    return original_commit(self, *positional, **kwargs)

                with monkeypatch.context() as patch:
                    patch.setattr(StateMutationService, "replace", stale_card)
                    with pytest.raises(ToolError, match="revision conflict"):
                        await call(server, "character_check", args)
                assert injected
                assert await snapshot() == before
                assert [await actor_snapshot(item["id"]) for item in actors] == before_cards

            remember = IdempotencyService.remember_write_in_session
            injected = False

            def fail_receipt(self, session, **kwargs):
                nonlocal injected
                if kwargs["key"] == "unlock":
                    injected = True
                    raise RuntimeError("injected receipt failure")
                return remember(self, session, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(IdempotencyService, "remember_write_in_session", fail_receipt)
                with pytest.raises(ToolError):
                    await call(server, "character_check", args)
            assert injected and await snapshot() == before
            assert [await actor_snapshot(item["id"]) for item in actors] == before_cards

            result = await call(server, "character_check", args)
            assert result["status"] == "committed"
            assert result["result"]["leader_id"] == leader["id"]
            assert result["result"]["helper_ids"] == [helper["id"]]
            check = result["result"]["check"]
            assert check["roll_mode"] == "advantage" and len(check["rolls"]) == 2
            assert failed_rolls == [check["rolls"], check["rolls"]]
            assert check["total"] == max(check["rolls"]) + 5
            assert "random_stream_receipt" in result
            assert any(r["mechanic_id"] == "dnd5e.core.check.working_together"
                       for r in result["result"]["rule_receipts"])
            after = await snapshot()
            assert after["revision"] == before["revision"] + 1
            for old in before_cards[:2]:
                current_actor = await actor_snapshot(old["id"])
                assert current_actor["revision"] == old["revision"] + 1
                assert current_actor["sheet"] == old["sheet"]
            assert await call(server, "character_check", args) == result
            with pytest.raises(ToolError, match="different request"):
                await call(server, "character_check", {
                    **args, "payload": {**args["payload"], "leader_id": helper["id"]},
                })
            close_server(server)
            server = create_server(config)
            assert await call(server, "character_check", args) == result
            assert await snapshot() == after

            # A reviewed sight task applies the exact leader's disease penalty;
            # the same infected leader is unaffected on a nonvisual task.
            sight_task = {**task, "relies_on_sight": True}
            sight_args = {
                **args, "expected_revision": after["revision"],
                "idempotency_key": "unlock-sight-task",
                "payload": {**args["payload"], "task": sight_task},
            }
            sight_result = await call(server, "character_check", sight_args)
            sight_check = sight_result["result"]["check"]
            assert sight_check["total"] == max(sight_check["rolls"]) + 5 + disease_penalty
            disease_receipt = sight_check["disease_modifier"]
            assert disease_receipt["facts"]["actor_id"] == leader["id"]
            assert disease_receipt["facts"]["penalty"] == disease_penalty
            assert disease_receipt in sight_result["result"]["rule_receipts"]

            after_sight = await snapshot()
            nonvisual_args = {
                **sight_args, "expected_revision": after_sight["revision"],
                "idempotency_key": "unlock-nonvisual-task",
                "payload": {**args["payload"], "task": {**task, "relies_on_sight": False}},
            }
            nonvisual = await call(server, "character_check", nonvisual_args)
            nonvisual_check = nonvisual["result"]["check"]
            assert nonvisual_check["total"] == max(nonvisual_check["rolls"]) + 5
            assert "disease_modifier" not in nonvisual_check

            after_nonvisual = await snapshot()
            unaffected_args = {
                **sight_args, "expected_revision": after_nonvisual["revision"],
                "idempotency_key": "unlock-unaffected-leader",
                "payload": {
                    **args["payload"], "leader_id": helper["id"], "task": sight_task,
                },
            }
            unaffected = await call(server, "character_check", unaffected_args)
            unaffected_check = unaffected["result"]["check"]
            assert unaffected_check["total"] == max(unaffected_check["rolls"]) + 3
            assert "disease_modifier" not in unaffected_check
            after = await snapshot()

            # Conditions changed after the first review must affect the next attempt.
            await call(server, "game_phase", {
                "campaign_id": campaign_id, "action": "set", "tool_profile": "lobby",
                "expected_revision": after["revision"], "idempotency_key": "lobby",
            })
            current_helper = await actor_snapshot(helper["id"])
            await call(server, "character_sheet_replace", {
                "character_id": helper["id"], "patch": {"conditions": ["unconscious"]},
                "expected_revision": current_helper["revision"], "idempotency_key": "unconscious",
            })
            current = await snapshot()
            await call(server, "game_phase", {
                "campaign_id": campaign_id, "action": "set", "tool_profile": "play",
                "expected_revision": current["revision"], "idempotency_key": "play-again",
            })
            changed = await snapshot()
            with pytest.raises(ToolError, match="incapacitated"):
                await call(server, "character_check", {
                    **args, "expected_revision": changed["revision"],
                    "idempotency_key": "retry-new",
                })
            assert await snapshot() == changed
            # The original operation still returns its historical committed outcome.
            assert await call(server, "character_check", args) == result

            started = await call(server, "combat_start", {
                "positioning_mode": "grid", "battle_map": {"width_cells": 12, "height_cells": 12},
                "campaign_id": campaign_id, "participant_ids": [leader["id"], untrained["id"]],
                "participant_config": [
                    {"actor_id": leader["id"], "initiative": 20, "position": {"x": 0, "y": 0}},
                    {"actor_id": untrained["id"], "initiative": 10, "position": {"x": 1, "y": 0}},
                ], "expected_revision": changed["revision"], "idempotency_key": "combat",
            })
            # The facade in combat directs the caller to the paid task Help procedure.
            with pytest.raises(ToolError, match="Help|Play phase|profile|phase"):
                await call(server, "character_check", {
                    **args, "expected_revision": started["campaign_revision"],
                    "idempotency_key": "no-free-help",
                })
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_working_together_does_not_enable_an_unreviewed_2024_procedure(tmp_path):
    config = McpConfig(
        home=tmp_path / "home", database_url=None, chroma_url=None, chroma_path_override=None,
        dnd_skills_dir=tmp_path / "skills", modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
    )

    async def exercise():
        server = create_server(config)
        try:
            campaign = await read(server, "campaign_create", {
                "name": "2024 isolation", "edition": "2024", "idempotency_key": "campaign",
            })
            await call(server, "game_phase", {
                "campaign_id": campaign["id"], "action": "set", "tool_profile": "play",
                "expected_revision": campaign["revision"], "idempotency_key": "play",
            })
            query = {"view": "get", "payload": {"campaign_id": campaign["id"]}}
            before = await read(server, "campaign_query", query)
            with pytest.raises(ToolError, match="2014"):
                await call(server, "character_check", {
                    "campaign_id": campaign["id"], "action": "working_together",
                    "expected_revision": before["revision"], "idempotency_key": "not-reviewed",
                    "payload": {"actor_ids": ["one", "two"], "ability": "strength", "task": {
                        "source_ref": {}, "source_excerpt": "not consulted in another edition",
                        "reason": "Not reviewed", "productive": True, "dc": 10,
                        "requirements": {"tools": [], "skills": [], "features": []},
                    }},
                })
            assert await read(server, "campaign_query", query) == before
        finally:
            close_server(server)

    asyncio.run(exercise())
