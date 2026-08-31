from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.random_stream import CampaignRandomStream, use_random_stream

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server
from sagasmith_dnd_mcp.tool_profiles import policy_for_tool
from tests.authoring_helpers import finalize_and_activate_module


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


def _config(tmp_path: Path, import_root: Path) -> McpConfig:
    return McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "dnd",
        modulegen_skills_dir=tmp_path / "modulegen",
        module_import_roots=(import_root,),
        auto_seed_rules=False,
    )


def _complication_choice(chase: dict) -> str:
    pending = dict(chase.get("pending_complication") or {})
    return {
        1: "acrobatics",
        2: "athletics",
        3: "strength",
        4: "intelligence",
        5: "dexterity",
        6: "acrobatics",
        7: "athletics",
        8: "athletics",
        10: "dexterity",
    }.get(pending.get("number"), "")


def test_chase_facade_is_play_only() -> None:
    assert policy_for_tool("chase").phases == frozenset({"play"})


def test_public_chase_uses_exact_module_source_and_no_combat_map(tmp_path: Path) -> None:
    import_root = tmp_path / "modules"
    import_root.mkdir()
    source_excerpt = (
        "A kenku has the Stone of Golorr and is 60 feet away at the start of the "
        "chase. The kenku drags a heavy sack and suffers a 10-foot reduction to "
        "its speed, and when the characters are close, the kenku ducks into an old tower."
    )
    speed_excerpt = "The kenku drags a heavy sack and suffers a 10-foot reduction to its speed"
    source = import_root / "chase.md"
    source.write_text(
        "# Chapter Four\n\n"
        "## Street Chase\n\n"
        "Use the chase rules and the Urban Chase Complications table. "
        f"{source_excerpt}\n",
        encoding="utf-8",
    )
    async def exercise() -> None:
        server = create_server(_config(tmp_path, import_root))
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "Source-reviewed chase",
                "edition": "2014",
                "idempotency_key": "campaign",
            },
        )
        staged = await _call(
            server,
            "module_draft",
            {
                "campaign_id": campaign["id"],
                "action": "start",
                "payload": {
                    "source_path": str(source),
                    "source_key": "chase-module",
                    "title": "Chase Module",
                },
                "idempotency_key": "stage",
            },
        )
        await finalize_and_activate_module(
            _call,
            server,
            campaign["id"],
            staged,
            source_key="chase-module",
            title="Chase Module",
            portable_id="dnd5e.module.chase-module",
        )
        hits = await _call(
            server,
            "module_search",
            {
                "campaign_id": campaign["id"],
                "query": "kenku Stone Golorr 60 feet chase",
            },
        )
        expanded = await _call(
            server,
            "module_expand",
            {"chunk_id": hits[0]["id"]},
        )
        transition_excerpt = "when the characters are close, the kenku ducks into an old tower."
        transition_expanded = expanded
        source_ref = {
            "module_id": expanded["module"]["id"],
            "scene_id": expanded["scene"]["id"],
            "chunk_id": expanded["chunk_id"],
            "page_start": expanded["page_start"],
            "page_end": expanded["page_end"],
            "heading_path": expanded["heading_path"],
            "content_sha256": hashlib.sha256(expanded["content"].encode("utf-8")).hexdigest(),
        }
        transition_source_ref = {
            "module_id": transition_expanded["module"]["id"],
            "scene_id": transition_expanded["scene"]["id"],
            "chunk_id": transition_expanded["chunk_id"],
            "page_start": transition_expanded["page_start"],
            "page_end": transition_expanded["page_end"],
            "heading_path": transition_expanded["heading_path"],
            "content_sha256": hashlib.sha256(
                transition_expanded["content"].encode("utf-8")
            ).hexdigest(),
        }

        actor_sheet = default_character_sheet()
        actor_sheet["edition"] = "2014"
        actor_sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
        actor_sheet["abilities"]["wisdom"]["score"] = 1
        actor_sheet["traits"]["senses"]["passive_perception_bonus"] = -5
        pursuer = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Pursuer", "sheet": actor_sheet},
                "principal_id": "system:local",
                "idempotency_key": "pursuer",
            },
        )
        quarry = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Kenku",
                    "character_type": "npc",
                    "sheet": actor_sheet,
                },
                "principal_id": "system:local",
                "idempotency_key": "quarry",
            },
        )
        high_sheet = default_character_sheet()
        high_sheet["edition"] = "2014"
        high_sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
        high_sheet["abilities"]["wisdom"]["score"] = 20
        high_sheet["traits"]["senses"]["passive_perception_bonus"] = 5
        high_pursuer = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Watchful pursuer",
                    "sheet": high_sheet,
                },
                "principal_id": "system:local",
                "idempotency_key": "high-pursuer",
            },
        )
        current_campaign = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        phase = await _call(
            server,
            "game_phase",
            {
                "campaign_id": campaign["id"],
                "action": "set",
                "tool_profile": "play",
                "expected_revision": current_campaign["revision"],
                "idempotency_key": "play",
            },
        )
        with pytest.raises(Exception, match="source_ref"):
            await _call(
                server,
                "chase",
                {
                    "campaign_id": campaign["id"],
                    "action": "start",
                    "payload": {
                        "participant_ids": [pursuer["id"], quarry["id"]],
                        "quarry_ids": [quarry["id"]],
                        "initial_distance_ft": 60,
                        "scene_id": expanded["scene"]["id"],
                        "source_ref": source_ref,
                        "source_excerpt": source_excerpt,
                        "close_transition": {
                            "distance_ft": 0,
                            "status": "destination_reached",
                            "summary": transition_excerpt,
                        },
                    },
                    "expected_revision": phase["campaign_revision"],
                    "idempotency_key": "chase-start-without-transition-evidence",
                },
            )
        started = await _call(
            server,
            "chase",
            {
                "campaign_id": campaign["id"],
                "action": "start",
                "payload": {
                    "participant_ids": [pursuer["id"], quarry["id"]],
                    "quarry_ids": [quarry["id"]],
                    "initial_distance_ft": 60,
                    "scene_id": expanded["scene"]["id"],
                    "source_ref": source_ref,
                    "source_excerpt": source_excerpt,
                    "participant_config": [
                        {"actor_id": pursuer["id"], "initiative": 20, "tie_breaker": 0},
                        {
                            "actor_id": quarry["id"],
                            "initiative": 10,
                            "tie_breaker": 1,
                            "speed_adjustment_ft": -10,
                            "source_excerpt": speed_excerpt,
                        },
                    ],
                    "close_transition": {
                        "distance_ft": 0,
                        "status": "destination_reached",
                        "summary": transition_excerpt,
                        "source_ref": transition_source_ref,
                        "source_excerpt": transition_excerpt,
                    },
                },
                "expected_revision": phase["campaign_revision"],
                "idempotency_key": "chase-start",
            },
        )

        assert started["chase"]["mode"] == "theater_of_the_mind"
        assert started["chase"]["pursuer_passive_perception_max"] == 0
        assert all(
            isinstance(item["passive_perception"], int)
            for item in started["chase"]["participants"]
        )
        assert "battle_map" not in started["chase"]
        assert started["chase"]["source_ref"]["chunk_id"] == expanded["chunk_id"]
        assert (
            started["chase"]["close_transition"]["source_ref"]["chunk_id"]
            == transition_expanded["chunk_id"]
        )
        quarry_state = next(
            item for item in started["chase"]["participants"] if item["actor_id"] == quarry["id"]
        )
        assert quarry_state["base_speed_ft"] == 30
        assert quarry_state["speed_adjustment_ft"] == -10
        assert quarry_state["speed_ft"] == 20
        assert quarry_state["speed_source_excerpt"] == speed_excerpt
        assert all(
            receipt["mechanic_id"].startswith("dnd5e.core.chase.")
            or receipt["mechanic_id"] == "dnd5e.core.check.jack_of_all_trades"
            for receipt in started["rule_receipts"]
        )

        with pytest.raises(Exception, match="end the active chase"):
            await _call(
                server,
                "npc_conversation",
                {
                    "campaign_id": campaign["id"],
                    "action": "open",
                    "payload": {
                        "participant_actor_ids": [pursuer["id"], quarry["id"]],
                        "idempotency_key": "conversation-during-chase",
                    },
                },
            )

        with pytest.raises(Exception, match="end the active chase"):
            await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "participant_ids": [pursuer["id"], quarry["id"]],
                    "positioning_mode": "agent",
                    "expected_revision": started["campaign_revision"],
                    "idempotency_key": "combat-before-chase-end",
                },
            )

        current_pursuer = await _call(
            server,
            "character_query",
            {
                "view": "get",
                "payload": {"character_id": pursuer["id"]},
                "principal_id": "system:local",
            },
        )
        with pytest.raises(Exception, match="required"):
            await _call(
                server,
                "chase",
                {
                    "campaign_id": campaign["id"],
                    "action": "take_turn",
                    "payload": {
                        "actor_id": pursuer["id"],
                        "turn_action": "dash",
                        "expected_actor_revision": current_pursuer["revision"],
                    },
                    "expected_revision": started["campaign_revision"],
                    "idempotency_key": "implicit-pursuer-turn",
                },
            )
        turn = await _call(
            server,
            "chase",
            {
                "campaign_id": campaign["id"],
                "action": "take_turn",
                "payload": {
                    "actor_id": pursuer["id"],
                    "turn_action": "dash",
                    "complication_choice": "",
                    "stand_from_prone": True,
                    "quarry_visibility": {quarry["id"]: True},
                    "expected_actor_revision": current_pursuer["revision"],
                },
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "pursuer-turn",
            },
        )

        assert turn["turn"]["moved_ft"] == 60
        assert turn["chase"]["active"] is False
        assert turn["chase"]["outcome"]["status"] == "destination_reached"
        queried = await _call(
            server,
            "chase",
            {"campaign_id": campaign["id"], "action": "query"},
        )
        assert queried["chase"]["outcome"] == turn["chase"]["outcome"]
        with pytest.raises(Exception, match=r"chase\(query\)\.payload must be empty"):
            await _call(
                server,
                "chase",
                {
                    "campaign_id": campaign["id"],
                    "action": "query",
                    "payload": {"unexpected": True},
                },
            )

        dynamic_started = await _call(
            server,
            "chase",
            {
                "campaign_id": campaign["id"],
                "action": "start",
                "payload": {
                    "participant_ids": [high_pursuer["id"], pursuer["id"], quarry["id"]],
                    "quarry_ids": [quarry["id"]],
                    "initial_distance_ft": 60,
                    "scene_id": expanded["scene"]["id"],
                    "source_ref": source_ref,
                    "source_excerpt": source_excerpt,
                    "participant_config": [
                        {"actor_id": high_pursuer["id"], "initiative": 30, "tie_breaker": 0},
                        {"actor_id": pursuer["id"], "initiative": 20, "tie_breaker": 0},
                        {"actor_id": quarry["id"], "initiative": 10, "tie_breaker": 0},
                    ],
                },
                "expected_revision": turn["campaign_revision"],
                "idempotency_key": "dynamic-chase-start",
            },
        )
        assert dynamic_started["chase"]["pursuer_passive_perception_max"] == 20

        current_high = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": high_pursuer["id"]}},
        )
        current_campaign = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        high_stream = CampaignRandomStream.from_campaign_state(
            campaign["id"],
            current_campaign["state"],
            operation="chase",
            idempotency_key="high-pursuer-drops",
        )
        with use_random_stream(high_stream):
            high_dropped = await _call(
                server,
                "chase",
                {
                    "campaign_id": campaign["id"],
                    "action": "take_turn",
                    "payload": {
                        "actor_id": high_pursuer["id"],
                        "turn_action": "drop_out",
                        "complication_choice": "",
                        "stand_from_prone": True,
                        "quarry_visibility": {quarry["id"]: True},
                        "expected_actor_revision": current_high["revision"],
                    },
                    "expected_revision": dynamic_started["campaign_revision"],
                    "idempotency_key": "high-pursuer-drops",
                },
            )
        assert high_dropped["chase"]["pursuer_passive_perception_max"] == 0
        assert high_dropped["random_stream_receipt"]["draw_count"] == 1

        current_low = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": pursuer["id"]}},
        )
        current_campaign = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        low_stream = CampaignRandomStream.from_campaign_state(
            campaign["id"],
            current_campaign["state"],
            operation="chase",
            idempotency_key="low-pursuer-turn",
        )
        with use_random_stream(low_stream):
            low_turn = await _call(
                server,
                "chase",
                {
                    "campaign_id": campaign["id"],
                    "action": "take_turn",
                    "payload": {
                        "actor_id": pursuer["id"],
                        "turn_action": "move",
                        "complication_choice": _complication_choice(high_dropped["chase"]),
                        "stand_from_prone": True,
                        "quarry_visibility": {quarry["id"]: True},
                        "expected_actor_revision": current_low["revision"],
                    },
                    "expected_revision": high_dropped["campaign_revision"],
                    "idempotency_key": "low-pursuer-turn",
                },
            )
        assert low_turn["chase"]["pursuer_passive_perception_max"] == 0
        assert low_turn["random_stream_receipt"]["draw_count"] >= 1

        current_quarry = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": quarry["id"]}},
        )
        quarry_turn_arguments = {
            "campaign_id": campaign["id"],
            "action": "take_turn",
            "payload": {
                "actor_id": quarry["id"],
                "turn_action": "move",
                "complication_choice": _complication_choice(low_turn["chase"]),
                "stand_from_prone": True,
                "quarry_visibility": {quarry["id"]: False},
                "expected_actor_revision": current_quarry["revision"],
            },
            "expected_revision": low_turn["campaign_revision"],
            "idempotency_key": "quarry-escapes",
        }
        current_campaign = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        quarry_stream = CampaignRandomStream.from_campaign_state(
            campaign["id"],
            current_campaign["state"],
            operation="chase",
            idempotency_key="quarry-escapes",
        )
        with use_random_stream(quarry_stream):
            escaped = await _call(server, "chase", quarry_turn_arguments)
        replayed = await _call(server, "chase", quarry_turn_arguments)
        escape = escaped["turn"]["escape_checks"][0]
        assert escape["passive_perception_max"] == 0
        assert escape["check"]["dc"] == 1
        assert escape["escaped"] is True
        assert escaped["chase"]["active"] is False
        assert escaped["chase"]["outcome"]["status"] == "quarry_escaped"
        assert escaped["random_stream_receipt"]["draw_count"] >= 2
        assert replayed["chase"] == escaped["chase"]
        assert replayed["campaign_revision"] == escaped["campaign_revision"]
        assert replayed["random_stream_receipt"] == escaped["random_stream_receipt"]
        settled_query = await _call(
            server,
            "chase",
            {"campaign_id": campaign["id"], "action": "query"},
        )
        assert settled_query["chase"] == escaped["chase"]
        assert settled_query["campaign_revision"] == escaped["campaign_revision"]

        terminal_started = await _call(
            server,
            "chase",
            {
                "campaign_id": campaign["id"],
                "action": "start",
                "payload": {
                    "participant_ids": [pursuer["id"], quarry["id"]],
                    "quarry_ids": [quarry["id"]],
                    "initial_distance_ft": 60,
                    "scene_id": expanded["scene"]["id"],
                    "source_ref": source_ref,
                    "source_excerpt": source_excerpt,
                    "participant_config": [
                        {"actor_id": pursuer["id"], "initiative": 20, "tie_breaker": 0},
                        {"actor_id": quarry["id"], "initiative": 10, "tie_breaker": 0},
                    ],
                },
                "expected_revision": escaped["campaign_revision"],
                "idempotency_key": "terminal-chase-start",
            },
        )
        current_pursuer = await _call(
            server,
            "character_query",
            {"view": "get", "payload": {"character_id": pursuer["id"]}},
        )
        current_campaign = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        terminal_arguments = {
            "campaign_id": campaign["id"],
            "action": "take_turn",
            "payload": {
                "actor_id": pursuer["id"],
                "turn_action": "drop_out",
                "complication_choice": "",
                "stand_from_prone": True,
                "quarry_visibility": {quarry["id"]: True},
                "expected_actor_revision": current_pursuer["revision"],
            },
            "expected_revision": terminal_started["campaign_revision"],
            "idempotency_key": "last-pursuer-drops",
        }
        terminal_stream = CampaignRandomStream.from_campaign_state(
            campaign["id"],
            current_campaign["state"],
            operation="chase",
            idempotency_key="last-pursuer-drops",
        )
        with use_random_stream(terminal_stream):
            terminal = await _call(server, "chase", terminal_arguments)
        terminal_replay = await _call(server, "chase", terminal_arguments)

        assert terminal_stream.draw_count == 0
        assert terminal["chase"]["active"] is False
        assert terminal["chase"]["pending_complication"] is None
        assert terminal["chase"]["outcome"]["status"] == "quarry_escaped"
        assert terminal["turn"]["next_complication_roll"] is None
        assert terminal["turn"]["next_complication"] is None
        assert "random_stream_receipt" not in terminal
        assert terminal_replay == terminal
        assert "random_stream_receipt" not in terminal_replay

    asyncio.run(exercise())
