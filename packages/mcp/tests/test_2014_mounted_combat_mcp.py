import asyncio

import sagasmith_dnd_runtime.application_support as runtime_support
from sagasmith_dnd.character_schema import default_character_sheet
from test_combat_transaction_boundaries_mcp import _config
from test_opportunity_sneak_attack_mcp import _call, _raw

from sagasmith_dnd_mcp.server import close_server, create_server


def test_mount_dismount_and_controlled_mount_turn_are_atomic(tmp_path):
    async def exercise():
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server, "campaign_create",
                {"name": "Mount lifecycle", "edition": "2014", "idempotency_key": "campaign"},
            )
            actors = []
            for name, size in (("rider", "medium"), ("horse", "large"), ("guard", "medium")):
                sheet = default_character_sheet()
                sheet["traits"]["size"] = size
                if name == "rider":
                    sheet["combat"]["hp"] = {"value": 30, "max": 30, "temp": 0}
                actors.append(await _call(
                    server, "character_create_from",
                    {
                        "mode": "direct",
                        "payload": {"campaign_id": campaign["id"], "name": name, "sheet": sheet},
                        "idempotency_key": name,
                    },
                ))
            rider, horse, guard = actors
            started = await _call(
                server, "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 8, "height_cells": 8},
                    "participant_ids": [actor["id"] for actor in actors],
                    "participant_config": [
                        {
                            "actor_id": rider["id"], "initiative": 30,
                            "position": {"x": 0, "y": 0}, "disposition": "friendly",
                        },
                        {
                            "actor_id": horse["id"], "initiative": 10,
                            "position": {"x": 1, "y": 0}, "disposition": "friendly",
                        },
                        {
                            "actor_id": guard["id"], "initiative": 20,
                            "position": {"x": 0, "y": 1}, "disposition": "hostile",
                        },
                    ],
                    "expected_revision": campaign["revision"],
                    "idempotency_key": "start",
                },
            )
            facts = {
                "decision_id": "stable-mounting-1",
                "reason": "The saddled horse accepts this rider and is trained to carry one rider.",
                "source_ref": "scene:stable/horse-1",
                "source_excerpt": "A saddled riding horse waits in the stable.",
                "willing": True,
                "suitable_anatomy": True,
                "trained": True,
                "capacity": 1,
            }
            request = {
                "campaign_id": campaign["id"],
                "actor_id": rider["id"],
                "action": "mount",
                "target_id": horse["id"],
                "payload": {"mounting_facts": facts},
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "mount",
            }
            mounted = await _call(server, "combat_common_action", request)
            assert await _call(server, "combat_common_action", request) == mounted
            mount_record = next(
                actor for actor in mounted["combat"]["combatants"]
                if actor["actor_id"] == horse["id"]
            )
            rider_record = next(
                actor for actor in mounted["combat"]["combatants"]
                if actor["actor_id"] == rider["id"]
            )
            assert mount_record["initiative"] == rider_record["initiative"] == 30
            assert mount_record["mounted_turn"]["owner_actor_id"] == rider["id"]
            assert rider_record["turn_budget"]["movement_spent"] == 15
            assert rider_record["position"] == mount_record["position"]

            mount_turn = await _call(server, "combat_end_turn", {
                "campaign_id": campaign["id"], "actor_id": rider["id"],
                "expected_revision": mounted["campaign_revision"], "idempotency_key": "end-rider",
            })
            invalid_attack = {
                "campaign_id": campaign["id"], "actor_id": horse["id"],
                "action": "grapple", "target_id": guard["id"],
                "expected_revision": mount_turn["campaign_revision"],
                "idempotency_key": "controlled-mount-attack",
            }
            try:
                await _call(server, "combat_common_action", invalid_attack)
            except Exception as exc:
                assert "only Dash, Disengage, or Dodge" in str(exc)
            else:
                raise AssertionError("a controlled mount accepted a prohibited attack action")

            moving = await _call(server, "combat_movement", {
                "campaign_id": campaign["id"],
                "actor_id": horse["id"],
                "action": "move",
                "payload": {"distance": 10, "destination": {"x": 3, "y": 0}},
                "expected_revision": mount_turn["campaign_revision"],
                "idempotency_key": "mount-move",
            })
            assert moving["status"] == "pending_reaction"
            window = moving["combat"]["pending"][0]
            assert set(window["target_actor_ids"]) == {rider["id"], horse["id"]}
            settled_move = await _raw(server, "combat_reaction_attack", {
                "campaign_id": campaign["id"],
                "actor_id": guard["id"],
                "choice_id": window["id"],
                "target_id": rider["id"],
                "action": {"weapon_id": "unarmed-strike"},
                "expected_revision": moving["campaign_revision"],
                "idempotency_key": "oa-target-rider",
            })
            assert "combat" in settled_move, settled_move
            mount_after_move = next(
                actor for actor in settled_move["combat"]["combatants"]
                if actor["actor_id"] == horse["id"]
            )
            rider_after_move = next(
                actor for actor in settled_move["combat"]["combatants"]
                if actor["actor_id"] == rider["id"]
            )
            assert mount_after_move["position"] == rider_after_move["position"] == {"x": 3, "y": 0}

            await _call(server, "combat_end_turn", {
                "campaign_id": campaign["id"], "actor_id": horse["id"],
                "expected_revision": settled_move["campaign_revision"],
                "idempotency_key": "end-mount",
            })
            rider_turn = await _call(server, "combat_end_turn", {
                "campaign_id": campaign["id"], "actor_id": guard["id"],
                "expected_revision": settled_move["campaign_revision"] + 1,
                "idempotency_key": "end-guard",
            })
            dismount = await _call(server, "combat_common_action", {
                "campaign_id": campaign["id"], "actor_id": rider["id"],
                "action": "dismount", "payload": {"destination": {"x": 2, "y": 0}},
                "expected_revision": rider_turn["campaign_revision"],
                "idempotency_key": "dismount",
            })
            rider_after = next(
                actor for actor in dismount["combat"]["combatants"]
                if actor["actor_id"] == rider["id"]
            )
            horse_after = next(
                actor for actor in dismount["combat"]["combatants"]
                if actor["actor_id"] == horse["id"]
            )
            assert rider_after["position"] == {"x": 2, "y": 0}
            assert horse_after["initiative"] == 10
            assert dismount["combat"]["mount_relations"][0]["active"] is False
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_forced_mount_movement_saves_and_commits_rider_fall_atomically(
    tmp_path, monkeypatch
):
    def failed_dexterity_save(*args, **kwargs):
        return {
            "kind": "save",
            "ability": "dexterity",
            "dc": 10,
            "natural": 4,
            "total": 4,
            "success": False,
        }

    monkeypatch.setattr(runtime_support, "resolve_actor_check", failed_dexterity_save)

    async def exercise():
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server, "campaign_create",
                {"name": "Forced mount fall", "edition": "2014", "idempotency_key": "campaign"},
            )
            actors = []
            for name, size in (("rider", "medium"), ("horse", "large")):
                sheet = default_character_sheet()
                sheet["traits"]["size"] = size
                actors.append(await _call(server, "character_create_from", {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": name, "sheet": sheet},
                    "idempotency_key": name,
                }))
            rider, horse = actors
            started = await _raw(server, "combat_start", {
                "campaign_id": campaign["id"],
                "positioning_mode": "grid",
                "battle_map": {"width_cells": 8, "height_cells": 8},
                "participant_ids": [rider["id"], horse["id"]],
                "participant_config": [
                    {"actor_id": rider["id"], "initiative": 30,
                     "position": {"x": 0, "y": 0}, "disposition": "friendly"},
                    {"actor_id": horse["id"], "initiative": 10,
                     "position": {"x": 1, "y": 0}, "disposition": "friendly"},
                ],
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            })
            mounted = await _call(server, "combat_common_action", {
                "campaign_id": campaign["id"], "actor_id": rider["id"],
                "action": "mount", "target_id": horse["id"],
                "payload": {"mounting_facts": {
                    "decision_id": "forced-fall-test",
                    "reason": "A trained riding horse accepts the rider.",
                    "source_ref": "scene:horse",
                    "source_excerpt": "The horse is saddled and trained.",
                    "willing": True, "suitable_anatomy": True,
                    "trained": True, "capacity": 1,
                }},
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "mount",
            })
            mount_turn = await _call(server, "combat_end_turn", {
                "campaign_id": campaign["id"], "actor_id": rider["id"],
                "expected_revision": mounted["campaign_revision"],
                "idempotency_key": "end-rider",
            })
            moved = await _call(server, "combat_movement", {
                "campaign_id": campaign["id"], "actor_id": horse["id"],
                "action": "move",
                "payload": {
                    "distance": 5, "destination": {"x": 2, "y": 0},
                    "movement_mode": "forced",
                },
                "expected_revision": mount_turn["campaign_revision"],
                "idempotency_key": "forced-move",
            })
            fall = moved["mount_fall_resolutions"][0]
            assert fall["trigger"] == "mount_moved_unwillingly"
            assert fall["save"]["dc"] == 10 and fall["fell"] is True
            relation = moved["combat"]["mount_relations"][0]
            assert relation["active"] is False
            rider_state = await _call(server, "character_query", {
                "view": "get", "payload": {"character_id": rider["id"]},
            })
            assert "prone" in rider_state["sheet"]["conditions"]
            replay = await _call(server, "combat_movement", {
                "campaign_id": campaign["id"], "actor_id": horse["id"],
                "action": "move",
                "payload": {
                    "distance": 5, "destination": {"x": 2, "y": 0},
                    "movement_mode": "forced",
                },
                "expected_revision": mount_turn["campaign_revision"],
                "idempotency_key": "forced-move",
            })
            assert replay == moved
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_prone_mount_offers_rider_reaction_to_dismount_steadily(tmp_path, monkeypatch):
    def attacker_wins(*args, **kwargs):
        return {
            "kind": "shove",
            "attacker_check": {"kind": "ability", "ability": "athletics", "total": 20},
            "target_check": {"kind": "ability", "ability": "acrobatics", "total": 1},
            "automatic_success": False,
            "success": True,
        }

    monkeypatch.setattr(runtime_support, "resolve_2014_special_attack_contest", attacker_wins)

    async def exercise():
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server, "campaign_create",
                {"name": "Mount prone reaction", "edition": "2014", "idempotency_key": "campaign"},
            )
            actors = []
            for name, size in (("rider", "medium"), ("horse", "large"), ("guard", "medium")):
                sheet = default_character_sheet()
                sheet["traits"]["size"] = size
                actors.append(await _call(server, "character_create_from", {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": name, "sheet": sheet},
                    "idempotency_key": name,
                }))
            rider, horse, guard = actors
            started = await _call(server, "combat_start", {
                "campaign_id": campaign["id"],
                "positioning_mode": "grid",
                "battle_map": {"width_cells": 8, "height_cells": 8},
                "participant_ids": [actor["id"] for actor in actors],
                "participant_config": [
                    {"actor_id": rider["id"], "initiative": 30,
                     "position": {"x": 0, "y": 0}, "disposition": "friendly"},
                    {"actor_id": horse["id"], "initiative": 10,
                     "position": {"x": 1, "y": 0}, "disposition": "friendly"},
                    {"actor_id": guard["id"], "initiative": 20,
                     "position": {"x": 2, "y": 1}, "disposition": "hostile"},
                ],
                "expected_revision": campaign["revision"],
                "idempotency_key": "start",
            })
            mounted = await _call(server, "combat_common_action", {
                "campaign_id": campaign["id"], "actor_id": rider["id"],
                "action": "mount", "target_id": horse["id"],
                "payload": {"mounting_facts": {
                    "decision_id": "mount-prone-test",
                    "reason": "The trained riding horse accepts the rider.",
                    "source_ref": "scene:horse",
                    "source_excerpt": "The horse is saddled and trained.",
                    "willing": True, "suitable_anatomy": True,
                    "trained": True, "capacity": 1,
                }},
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "mount",
            })
            mount_turn = await _call(server, "combat_end_turn", {
                "campaign_id": campaign["id"], "actor_id": rider["id"],
                "expected_revision": mounted["campaign_revision"],
                "idempotency_key": "end-rider",
            })
            guard_turn = await _call(server, "combat_end_turn", {
                "campaign_id": campaign["id"], "actor_id": horse["id"],
                "expected_revision": mount_turn["campaign_revision"],
                "idempotency_key": "end-mount",
            })
            declaration = await _call(server, "combat_common_action", {
                "campaign_id": campaign["id"], "actor_id": guard["id"],
                "action": "shove", "target_id": horse["id"],
                "payload": {"outcome": "prone"},
                "expected_revision": guard_turn["campaign_revision"],
                "idempotency_key": "shove-mount",
            })
            settled = await _call(server, "combat_choice", {
                "campaign_id": campaign["id"], "action": "resolve",
                "actor_id": horse["id"],
                "payload": {
                    "choice_id": declaration["choice"]["id"],
                    "selection": {"id": "acrobatics"},
                },
                "expected_revision": declaration["campaign_revision"],
                "idempotency_key": "defend-mounted-shove",
            })
            assert settled["status"] == "pending_reaction"
            fall_choice = settled["choice"]
            assert fall_choice["actor_id"] == rider["id"]
            assert {item["id"] for item in fall_choice["candidates"]} == {
                "dismount_steady", "fall_prone",
            }
            rider_state = next(
                item for item in settled["combat"]["combatants"]
                if item["actor_id"] == rider["id"]
            )
            assert rider_state["turn_budget"]["reaction"] == 1

            dismounted = await _call(server, "combat_choice", {
                "campaign_id": campaign["id"], "action": "resolve",
                "actor_id": rider["id"],
                "payload": {
                    "choice_id": fall_choice["id"],
                    "selection": {"id": "dismount_steady"},
                },
                "expected_revision": settled["campaign_revision"],
                "idempotency_key": "dismount-steady",
            })
            rider_after = next(
                item for item in dismounted["combat"]["combatants"]
                if item["actor_id"] == rider["id"]
            )
            mount_after = next(
                item for item in dismounted["combat"]["combatants"]
                if item["actor_id"] == horse["id"]
            )
            assert dismounted["combat"]["mount_relations"][0]["active"] is False
            assert rider_after["turn_budget"]["reaction"] == 0
            assert "prone" not in rider_after["conditions"]
            assert rider_after["position"] != mount_after["position"]
            persisted = await _call(server, "character_query", {
                "view": "get", "payload": {"character_id": rider["id"]},
            })
            assert "prone" not in persisted["sheet"]["conditions"]
        finally:
            close_server(server)

    asyncio.run(exercise())
