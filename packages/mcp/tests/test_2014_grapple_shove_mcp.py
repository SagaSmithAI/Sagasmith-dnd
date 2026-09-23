from __future__ import annotations

import asyncio
from pathlib import Path

from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


def _config(tmp_path: Path) -> McpConfig:
    return McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "dnd",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
    )


async def _campaign_and_combat(
    server,
    *,
    incapacitated_target: bool = False,
    random_seed: str | None = None,
    attacker_strength: int = 18,
    target_strength: int = 10,
    target_dexterity: int = 8,
):
    campaign_request = {
        "name": "2014 grapple",
        "edition": "2014",
        "idempotency_key": "campaign",
    }
    if random_seed is not None:
        campaign_request["random_seed"] = random_seed
    campaign = await _call(
        server,
        "campaign_create",
        campaign_request,
    )
    attacker_sheet = default_character_sheet()
    attacker_sheet["combat"]["attacks_per_action"] = 2
    attacker_sheet["abilities"]["strength"]["score"] = attacker_strength
    target_sheet = default_character_sheet()
    target_sheet["abilities"]["strength"]["score"] = target_strength
    target_sheet["abilities"]["dexterity"]["score"] = target_dexterity
    if incapacitated_target:
        target_sheet["conditions"] = ["incapacitated"]
    actors = []
    for name, sheet in (("attacker", attacker_sheet), ("target", target_sheet)):
        actors.append(await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": name,
                    "sheet": sheet,
                },
                "principal_id": "system:local",
                "idempotency_key": name,
            },
        ))
    latest = await _call(
        server,
        "campaign_query",
        {
            "view": "get",
            "payload": {"campaign_id": campaign["id"]},
            "principal_id": "system:local",
        },
    )
    started = await _call(
        server,
        "combat_start",
        {
            "positioning_mode": "grid",
            "battle_map": {"width_cells": 8, "height_cells": 8},
            "campaign_id": campaign["id"],
            "participant_ids": [actor["id"] for actor in actors],
            "participant_config": [
                {"actor_id": actors[0]["id"], "initiative": 20, "position": {"x": 0, "y": 0}},
                {"actor_id": actors[1]["id"], "initiative": 10, "position": {"x": 1, "y": 0}},
            ],
            "expected_revision": latest["revision"],
            "idempotency_key": "combat-start",
        },
    )
    return campaign["id"], actors, started


def test_2014_grapple_automatically_succeeds_against_incapacitated_target(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign_id, actors, started = await _campaign_and_combat(
            server, incapacitated_target=True
        )
        settled = await _call(
            server,
            "combat_common_action",
            {
                "campaign_id": campaign_id,
                "actor_id": actors[0]["id"],
                "action": "grapple",
                "target_id": actors[1]["id"],
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "grapple-auto",
            },
        )
        replayed = await _call(
            server,
            "combat_common_action",
            {
                "campaign_id": campaign_id,
                "actor_id": actors[0]["id"],
                "action": "grapple",
                "target_id": actors[1]["id"],
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "grapple-auto",
            },
        )
        assert replayed == settled
        assert settled["condition_resolution"]["automatic_success"] is True
        assert settled["condition_resolution"]["condition"] == "grappled"
        assert len(settled["combat"]["grapple_sources"]) == 1
        target = await _call(
            server,
            "character_query",
            {
                "view": "get",
                "payload": {"character_id": actors[1]["id"]},
                "principal_id": "system:local",
            },
        )
        assert "grappled" in target["sheet"]["conditions"]
        source_effect = next(
            effect for effect in target["sheet"]["effects"]
            if effect["id"] == settled["condition_resolution"]["effect_id"]
        )
        assert source_effect["source"] == actors[0]["id"]
        assert settled["combat"]["combatants"][0]["turn_budget"]["attack_budget"] == 1

        released = await _call(
            server,
            "combat_common_action",
            {
                "campaign_id": campaign_id,
                "actor_id": actors[0]["id"],
                "action": "release_grapple",
                "target_id": actors[1]["id"],
                "payload": {
                    "grapple_id": settled["condition_resolution"]["effect_id"],
                },
                "expected_revision": settled["campaign_revision"],
                "idempotency_key": "grapple-release",
            },
        )
        assert released["condition_resolution"]["kind"] == "grapple_release"
        assert released["condition_resolution"]["remaining_grappled"] is False
        assert not any(
            source["active"]
            for source in released["combat"]["grapple_sources"]
        )

    asyncio.run(exercise())


def test_2014_shove_settles_prone_and_five_foot_push_as_attack_replacements(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign_id, actors, started = await _campaign_and_combat(
            server, incapacitated_target=True
        )
        prone = await _call(
            server,
            "combat_common_action",
            {
                "campaign_id": campaign_id,
                "actor_id": actors[0]["id"],
                "action": "shove",
                "target_id": actors[1]["id"],
                "payload": {"outcome": "prone"},
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "shove-prone",
            },
        )
        assert prone["condition_resolution"]["automatic_success"] is True
        assert prone["condition_resolution"]["condition"] == "prone"
        assert prone["condition_resolution"]["applied"] is True
        assert prone["combat"]["combatants"][0]["turn_budget"]["attack_budget"] == 1

        pushed = await _call(
            server,
            "combat_common_action",
            {
                "campaign_id": campaign_id,
                "actor_id": actors[0]["id"],
                "action": "shove",
                "target_id": actors[1]["id"],
                "payload": {"outcome": "push_5_ft"},
                "expected_revision": prone["campaign_revision"],
                "idempotency_key": "shove-push",
            },
        )
        assert pushed["condition_resolution"]["movement"]["requested_distance_ft"] == 5
        assert pushed["condition_resolution"]["movement"]["moved_distance_ft"] == 5
        positions = {
            item["actor_id"]: item["position"]
            for item in pushed["combat"]["combatants"]
        }
        assert positions[actors[1]["id"]] == {"x": 2, "y": 0}
        assert pushed["combat"]["combatants"][0]["turn_budget"]["attack_budget"] == 0

    asyncio.run(exercise())


def test_2014_grapple_defense_is_an_owned_skill_choice_and_replayable(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign_id, actors, started = await _campaign_and_combat(server)
        request = {
            "campaign_id": campaign_id,
            "actor_id": actors[0]["id"],
            "action": "grapple",
            "target_id": actors[1]["id"],
            "expected_revision": started["campaign_revision"],
            "idempotency_key": "grapple-choice",
        }
        pending = await _call(server, "combat_common_action", request)
        replayed_pending = await _call(server, "combat_common_action", request)
        assert replayed_pending == pending
        choice = pending["choice"]
        assert choice["actor_id"] == actors[1]["id"]
        assert {item["id"] for item in choice["candidates"]} == {"athletics", "acrobatics"}
        resolved_request = {
            "campaign_id": campaign_id,
            "action": "resolve",
            "actor_id": actors[1]["id"],
            "payload": {"choice_id": choice["id"], "selection": {"id": "acrobatics"}},
            "expected_revision": pending["campaign_revision"],
            "idempotency_key": "grapple-defense",
        }
        settled = await _call(server, "combat_choice", resolved_request)
        replayed = await _call(server, "combat_choice", resolved_request)
        assert replayed == settled
        assert settled["condition_resolution"]["target_check"]["kind"] == "ability"
        assert settled["condition_resolution"]["attack_payment"]["kind"] == (
            "special_attack_replacement"
        )
        assert settled.get("random_stream_receipt") is not None

    asyncio.run(exercise())


def test_2014_grapple_escape_uses_the_target_turn_and_replays_exactly(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign_id, actors, started = await _campaign_and_combat(
            server,
            random_seed="grapple-escape",
            attacker_strength=8,
            target_strength=30,
        )
        pending = await _call(
            server,
            "combat_common_action",
            {
                "campaign_id": campaign_id,
                "actor_id": actors[0]["id"],
                "action": "grapple",
                "target_id": actors[1]["id"],
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "escape-grapple",
            },
        )
        grappled = await _call(
            server,
            "combat_choice",
            {
                "campaign_id": campaign_id,
                "actor_id": actors[1]["id"],
                "action": "resolve",
                "payload": {
                    "choice_id": pending["choice"]["id"],
                    "selection": {"id": "acrobatics"},
                },
                "expected_revision": pending["campaign_revision"],
                "idempotency_key": "escape-grapple-defense",
            },
        )
        assert grappled["condition_resolution"]["success"] is True
        ended = await _call(
            server,
            "combat_end_turn",
            {
                "campaign_id": campaign_id,
                "actor_id": actors[0]["id"],
                "expected_revision": grappled["campaign_revision"],
                "idempotency_key": "escape-grapple-end-source-turn",
            },
        )
        request = {
            "campaign_id": campaign_id,
            "actor_id": actors[1]["id"],
            "action": "escape",
            "target_id": actors[0]["id"],
            "payload": {
                "grapple_id": grappled["condition_resolution"]["effect_id"],
                "skill": "athletics",
            },
            "expected_revision": ended["campaign_revision"],
            "idempotency_key": "escape-grapple-contest",
        }
        escaped = await _call(server, "combat_common_action", request)
        replayed = await _call(server, "combat_common_action", request)
        assert replayed == escaped
        resolution = escaped["condition_resolution"]
        assert resolution["kind"] == "grapple_escape"
        assert resolution["action_payment"] in {"main_action", "extra_action"}
        assert resolution["contest"]["target_check"] is not None
        assert escaped.get("random_stream_receipt") is not None
        assert resolution["escaped"] is True, resolution["contest"]
        target = await _call(
            server,
            "character_query",
            {
                "view": "get",
                "payload": {"character_id": actors[1]["id"]},
                "principal_id": "system:local",
            },
        )
        assert "grappled" not in target["sheet"]["conditions"]

    asyncio.run(exercise())


def test_2014_grapple_ends_atomically_when_the_grid_source_leaves_reach(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign_id, actors, started = await _campaign_and_combat(
            server, incapacitated_target=True
        )
        grappled = await _call(
            server,
            "combat_common_action",
            {
                "campaign_id": campaign_id,
                "actor_id": actors[0]["id"],
                "action": "grapple",
                "target_id": actors[1]["id"],
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "grapple-before-separation",
            },
        )
        moved = await _call(
            server,
            "combat_movement",
            {
                "campaign_id": campaign_id,
                "actor_id": actors[0]["id"],
                "action": "move",
                "payload": {"distance": 25, "destination": {"x": 5, "y": 0}},
                "expected_revision": grappled["campaign_revision"],
                "idempotency_key": "grappler-leaves-reach",
            },
        )
        assert grappled["condition_resolution"]["effect_id"] in moved["ended_grapple_ids"]
        assert not any(source["active"] for source in moved["combat"]["grapple_sources"])
        target = await _call(
            server,
            "character_query",
            {
                "view": "get",
                "payload": {"character_id": actors[1]["id"]},
                "principal_id": "system:local",
            },
        )
        assert "grappled" not in target["sheet"]["conditions"]
        assert not any(
            effect["id"] == grappled["condition_resolution"]["effect_id"]
            for effect in target["sheet"]["effects"]
        )

    asyncio.run(exercise())


def test_2014_grapple_drag_moves_the_target_and_pays_half_speed(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign_id, actors, started = await _campaign_and_combat(
            server, incapacitated_target=True
        )
        grappled = await _call(
            server,
            "combat_common_action",
            {
                "campaign_id": campaign_id,
                "actor_id": actors[0]["id"],
                "action": "grapple",
                "target_id": actors[1]["id"],
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "grapple-before-drag",
            },
        )
        grapple_id = grappled["condition_resolution"]["effect_id"]
        moved = await _call(
            server,
            "combat_movement",
            {
                "campaign_id": campaign_id,
                "actor_id": actors[0]["id"],
                "action": "move",
                "payload": {
                    "distance": 10,
                    "destination": {"x": 2, "y": 0},
                    "drag_grapple_ids": [grapple_id],
                },
                "expected_revision": grappled["campaign_revision"],
                "idempotency_key": "drag-held-creature",
            },
        )
        combatants = {
            item["actor_id"]: item for item in moved["combat"]["combatants"]
        }
        assert combatants[actors[0]["id"]]["position"] == {"x": 2, "y": 0}
        assert combatants[actors[1]["id"]]["position"] == {"x": 3, "y": 0}
        budget = combatants[actors[0]["id"]]["turn_budget"]
        assert budget["movement_spent"] == 20
        assert budget["movement"] == 10
        assert any(source["active"] for source in moved["combat"]["grapple_sources"])

    asyncio.run(exercise())


def test_2014_grapple_ends_with_source_incapacitation_in_the_same_write(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        campaign_id, actors, started = await _campaign_and_combat(
            server, incapacitated_target=True
        )
        grappled = await _call(
            server,
            "combat_common_action",
            {
                "campaign_id": campaign_id,
                "actor_id": actors[0]["id"],
                "action": "grapple",
                "target_id": actors[1]["id"],
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "grapple-before-source-falls",
            },
        )
        damaged = await _call(
            server,
            "combat_hp_change",
            {
                "campaign_id": campaign_id,
                "target_id": actors[0]["id"],
                "action": "damage",
                "payload": {"parts": [{"amount": 1000, "damage_type": "force"}]},
                "expected_revision": grappled["campaign_revision"],
                "idempotency_key": "grappler-incapacitated",
            },
        )
        assert grappled["condition_resolution"]["effect_id"] in damaged["ended_grapple_ids"]
        assert not any(source["active"] for source in damaged["combat"]["grapple_sources"])
        target = await _call(
            server,
            "character_query",
            {
                "view": "get",
                "payload": {"character_id": actors[1]["id"]},
                "principal_id": "system:local",
            },
        )
        assert "grappled" not in target["sheet"]["conditions"]

    asyncio.run(exercise())
