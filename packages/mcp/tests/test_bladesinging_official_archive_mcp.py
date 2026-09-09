from __future__ import annotations

import asyncio
import json
import random
from dataclasses import replace
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.combat_engine import roll_attack_action as engine_roll_attack_action
from test_official_expansions_mcp import _call, _config, _locked_official_library

import sagasmith_dnd_mcp.server as server_module
from sagasmith_dnd_mcp.server import close_server, create_server

_SCAG_RULE_ID = "dnd5e.addon.rulebook.d-d-5e-sword-coast-adventurer-s-guide.16e6a243ef0a"
_SCAG_ADDON_ID = f"{_SCAG_RULE_ID}.addon"
_BLADESINGING_ID = f"{_SCAG_RULE_ID}.subclass.bladesinging"
_BLADESONG_ID = f"{_SCAG_RULE_ID}.feature.bladesong"
_EXTRA_ATTACK_ID = f"{_SCAG_RULE_ID}.feature.extra-attack"
_SONG_DEFENSE_ID = f"{_SCAG_RULE_ID}.feature.song-of-defense"
_SONG_VICTORY_ID = f"{_SCAG_RULE_ID}.feature.song-of-victory"
_TRAINING_ID = f"{_SCAG_RULE_ID}.feature.training-in-war-and-song"


def _result_payload(value: dict) -> dict:
    if any(key in value for key in ("core_effect", "reaction_defense", "damage", "activity_id")):
        return value
    nested = value.get("result")
    return _result_payload(nested) if isinstance(nested, dict) else value


async def _combat_call(server: object, name: str, arguments: dict) -> dict:
    _, response = await server.call_tool(name, arguments)  # type: ignore[attr-defined]
    return response


async def _campaign_revision(server: object, campaign_id: str) -> int:
    value = await _call(
        server,
        "campaign_query",
        {"view": "get", "payload": {"campaign_id": campaign_id}},
    )
    return int(value["revision"])


def _wizard_sheet(*, level: int = 14, intelligence: int = 16) -> dict:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["abilities"]["intelligence"]["score"] = intelligence
    sheet["progression"].update(
        {
            "level": level,
            "species": "Elf",
            "classes": [{"name": "Wizard", "level": level, "hit_die": 6}],
        }
    )
    sheet["combat"]["hp"] = {"value": 30, "max": 30, "temp": 0}
    sheet["spellcasting"].update(
        {
            "ability": "intelligence",
            "class_lists": ["wizard"],
            "spell_slots": {
                "1": {
                    "label": "1st-level slots",
                    "value": 1,
                    "max": 1,
                    "unlimited": False,
                    "recovers_on": "long_rest",
                    "source_key": "Wizard",
                    "slot_level": 1,
                }
            },
        }
    )
    sheet["inventory"]["items"] = [
        {
            "id": "wizard-longsword",
            "name": "Longsword",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d8",
                "versatile_damage_formula": "1d10",
                "damage_type": "slashing",
                "properties": ["versatile"],
            },
        }
    ]
    sheet["inventory"]["equipment_slots"]["main_hand"] = "wizard-longsword"
    return sheet


def _enemy_sheet() -> dict:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["combat"]["hp"] = {"value": 100, "max": 100, "temp": 0}
    sheet["combat"]["ac"]["override"] = 1
    sheet["inventory"]["items"] = [
        {
            "id": "enemy-club",
            "name": "Club",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d8",
                "damage_type": "bludgeoning",
                "properties": [],
            },
        }
    ]
    sheet["inventory"]["equipment_slots"]["main_hand"] = "enemy-club"
    return sheet


@pytest.mark.fresh_database
def test_locked_scag_bladesinging_materializes_and_settles_full_runtime_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = _locked_official_library()
    workspace = Path(__file__).resolve().parents[3]
    config = replace(
        _config(tmp_path),
        official_content_library=library,
        auto_seed_rules=True,
        dnd_skills_dir=workspace / "skills",
    )
    monkeypatch.setattr(
        server_module,
        "roll_attack_action",
        lambda *, plan: engine_roll_attack_action(plan=plan, rng=random.Random(0)),
    )

    async def exercise() -> None:
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Locked Bladesinging", "edition": "2014", "idempotency_key": "campaign"},
            )
            profile = await _call(
                server, "campaign_rules", {"campaign_id": campaign["id"], "action": "get_profile"}
            )
            index = json.loads((library / "index.json").read_text(encoding="utf-8"))
            version = next(
                item["version"] for item in index["packages"] if item["id"] == _SCAG_ADDON_ID
            )
            await _call(
                server,
                "content_pack",
                {
                    "action": "activate",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "kind": "addon",
                        "addon_id": _SCAG_ADDON_ID,
                        "version": version,
                    },
                    "expected_revision": profile["campaign_revision"],
                    "idempotency_key": "activate-scag",
                },
            )
            wizard = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Elf Bladesinger",
                        "sheet": _wizard_sheet(),
                    },
                    "idempotency_key": "wizard",
                },
            )
            enemy = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Training Dummy",
                        "sheet": _enemy_sheet(),
                    },
                    "idempotency_key": "enemy",
                },
            )

            subclass_args = {
                "character_id": wizard["id"],
                "artifact_id": _BLADESINGING_ID,
                "selection": {
                    "target_class_name": "Wizard",
                    "war_and_song_training": {"weapon": ["Longsword"]},
                },
                "expected_revision": wizard["revision"],
                "idempotency_key": "apply-bladesinging",
            }
            applied = await _call(server, "character_content_apply", subclass_args)
            wizard = applied
            features = {item["id"]: item for item in wizard["sheet"]["content"]["features"]}
            assert {
                _BLADESONG_ID,
                _EXTRA_ATTACK_ID,
                _SONG_DEFENSE_ID,
                _SONG_VICTORY_ID,
                _TRAINING_ID,
            } <= set(features)
            assert wizard["sheet"]["resources"]["scag_bladesong"]["max"] == 5
            assert wizard["sheet"]["resources"]["scag_bladesong"]["recovers_on"] == "long_rest"
            assert wizard["sheet"]["combat"]["attacks_per_action"] == 2
            assert "light armor" in wizard["sheet"]["traits"]["proficiencies"]["armor"]
            assert "performance" in wizard["sheet"]["skills"]
            assert wizard["sheet"]["skills"]["performance"]["proficiency"] == "proficient"
            assert "Longsword" in wizard["sheet"]["traits"]["proficiencies"]["weapons"]

            replay = await _call(server, "character_content_apply", subclass_args)
            assert replay == applied
            with pytest.raises(ToolError, match="character revision conflict"):
                await _call(
                    server,
                    "character_content_apply",
                    {
                        **subclass_args,
                        "idempotency_key": "wrong-cas",
                        "expected_revision": wizard["revision"] - 1,
                    },
                )
            unchanged = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": wizard["id"]}},
            )
            assert unchanged["sheet"] == wizard["sheet"]

            bad_sheet = _wizard_sheet()
            bad_sheet["progression"]["species"] = "Human"
            bad = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Human Candidate",
                        "sheet": bad_sheet,
                    },
                    "idempotency_key": "human",
                },
            )
            before_bad = await _call(
                server, "character_query", {"view": "get", "payload": {"character_id": bad["id"]}}
            )
            with pytest.raises(ToolError, match="Elf or Half-Elf"):
                await _call(
                    server,
                    "character_content_apply",
                    {
                        "character_id": bad["id"],
                        "artifact_id": _BLADESINGING_ID,
                        "selection": {
                            "target_class_name": "Wizard",
                            "war_and_song_training": {"weapon": ["Longsword"]},
                        },
                        "expected_revision": before_bad["revision"],
                        "idempotency_key": "reject-human",
                    },
                )
            after_bad = await _call(
                server, "character_query", {"view": "get", "payload": {"character_id": bad["id"]}}
            )
            assert after_bad["revision"] == before_bad["revision"]
            assert after_bad["sheet"] == before_bad["sheet"]
            forged_override_args = {
                **subclass_args,
                "character_id": bad["id"],
                "selection": {
                    "target_class_name": "Wizard",
                    "war_and_song_training": {"weapon": ["Longsword"]},
                    "species_prerequisite_override": {
                        "reason": "A forged caller authorization must never be trusted.",
                        "authorization": {"signature": "forged"},
                    },
                },
                "expected_revision": after_bad["revision"],
                "idempotency_key": "reject-forged-override",
            }
            with pytest.raises(ToolError, match="only a reason"):
                await _call(server, "character_content_apply", forged_override_args)
            after_forged = await _call(
                server, "character_query", {"view": "get", "payload": {"character_id": bad["id"]}}
            )
            assert after_forged["revision"] == after_bad["revision"]
            assert after_forged["sheet"] == after_bad["sheet"]
            authorized_human = await _call(
                server,
                "character_content_apply",
                {
                    **subclass_args,
                    "character_id": bad["id"],
                    "selection": {
                        "target_class_name": "Wizard",
                        "war_and_song_training": {"weapon": ["Longsword"]},
                        "species_prerequisite_override": {
                            "reason": "The DM authorizes this campaign-specific exception.",
                        },
                    },
                    "expected_revision": after_forged["revision"],
                    "idempotency_key": "authorize-human-bladesinger",
                },
            )
            authorized_selection = next(
                item
                for item in authorized_human["sheet"]["content"]["selections"]
                if item.get("artifact_id") == _BLADESINGING_ID
            )
            override = authorized_selection["selection"]["species_prerequisite_override"]
            assert len(override["authority_id"]) == 32
            assert override["reason"] == "The DM authorizes this campaign-specific exception."
            assert override["authorization"]["signature"]

            current_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            await _combat_call(
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
            await _combat_call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 12, "height_cells": 12},
                    "participant_ids": [wizard["id"], enemy["id"]],
                    "participant_config": [
                        {
                            "actor_id": wizard["id"],
                            "initiative": 20,
                            "position": {"x": 0, "y": 0},
                            "disposition": "friendly",
                        },
                        {
                            "actor_id": enemy["id"],
                            "initiative": 10,
                            "position": {"x": 1, "y": 0},
                            "disposition": "hostile",
                        },
                    ],
                    "expected_revision": await _campaign_revision(server, campaign["id"]),
                    "idempotency_key": "start",
                },
            )
            activated = await _combat_call(
                server,
                "combat_use_activity",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": wizard["id"],
                    "activity_id": _BLADESONG_ID,
                    "expected_revision": await _campaign_revision(server, campaign["id"]),
                    "idempotency_key": "activate-bladesong",
                },
            )
            wizard = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": wizard["id"]}},
            )
            active_effect = next(
                item
                for item in wizard["sheet"]["effects"]
                if item.get("metadata", {}).get("scag_bladesong") is True and item["active"]
            )
            assert wizard["sheet"]["resources"]["scag_bladesong"]["value"] == 4
            assert wizard["derived"]["armor_class"] == 13
            assert wizard["derived"]["speed"]["walk"] == 40
            assert (
                _result_payload(activated)["core_effect"]["bladesong_effect"] == active_effect["id"]
            )

            await _combat_call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": wizard["id"],
                    "expected_revision": await _campaign_revision(server, campaign["id"]),
                    "idempotency_key": "end-wizard",
                },
            )
            await _combat_call(
                server,
                "combat_resolve_attack",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": enemy["id"],
                    "target_id": wizard["id"],
                    "action": {"weapon_id": "enemy-club"},
                    "expected_revision": await _campaign_revision(server, campaign["id"]),
                    "idempotency_key": "incoming-hit",
                },
            )
            reaction = (
                await _call(
                    server,
                    "combat_query",
                    {"campaign_id": campaign["id"], "view": "reactions", "actor_id": wizard["id"]},
                )
            )[0]
            assert reaction["status"] == "pending"
            song_defense = next(
                item for item in reaction["candidates"] if item["id"] == _SONG_DEFENSE_ID
            )
            assert song_defense["cast_levels"] == [1]
            defended = await _combat_call(
                server,
                "combat_choice",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": wizard["id"],
                    "action": "resolve_defense",
                    "payload": {
                        "choice_id": reaction["id"],
                        "selection": {"id": _SONG_DEFENSE_ID, "cast_level": 1},
                    },
                    "expected_revision": await _campaign_revision(server, campaign["id"]),
                    "idempotency_key": "song-defense",
                },
            )
            assert _result_payload(defended)["reaction_defense"]["reduction"] == 5
            defended_damage = _result_payload(defended)["damage"]
            assert defended_damage["reduction"] == 5
            defended_roll = defended_damage["roll_parts"][0]
            assert defended_roll["amount"] == max(0, defended_roll["amount_before_reduction"] - 5)
            wizard = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": wizard["id"]}},
            )
            assert wizard["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] == 0

            await _combat_call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": enemy["id"],
                    "expected_revision": await _campaign_revision(server, campaign["id"]),
                    "idempotency_key": "end-enemy",
                },
            )
            first_attack = await _combat_call(
                server,
                "combat_resolve_attack",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": wizard["id"],
                    "target_id": enemy["id"],
                    "action": {"weapon_id": "wizard-longsword", "weapon_grip": "one_handed"},
                    "expected_revision": await _campaign_revision(server, campaign["id"]),
                    "idempotency_key": "song-victory-attack-1",
                },
            )
            first_attack_result = _result_payload(first_attack)
            victory_parts = [
                part
                for part in first_attack_result["damage"]["roll_parts"]
                if part.get("source") == "scag.song_of_victory"
            ]
            assert len(victory_parts) == 1
            assert victory_parts[0]["amount"] == 3
            second_attack = await _combat_call(
                server,
                "combat_resolve_attack",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": wizard["id"],
                    "target_id": enemy["id"],
                    "action": {"weapon_id": "wizard-longsword", "weapon_grip": "one_handed"},
                    "expected_revision": await _campaign_revision(server, campaign["id"]),
                    "idempotency_key": "song-victory-attack-2",
                },
            )
            assert (
                len(
                    [
                        part
                        for part in _result_payload(second_attack)["damage"]["roll_parts"]
                        if part.get("source") == "scag.song_of_victory"
                    ]
                )
                == 1
            )
            combatant = next(
                item
                for item in second_attack["combat"]["combatants"]
                if item["actor_id"] == wizard["id"]
            )
            assert combatant["turn_budget"]["attack_budget"] == 0

            dismissed = await _combat_call(
                server,
                "combat_use_activity",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": wizard["id"],
                    "activity_id": _BLADESONG_ID,
                    "declaration": {"dismiss": True},
                    "expected_revision": await _campaign_revision(server, campaign["id"]),
                    "idempotency_key": "dismiss-bladesong",
                },
            )
            assert _result_payload(dismissed)["activity_id"] == _BLADESONG_ID
            after_dismiss = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": wizard["id"]}},
            )
            assert not any(
                item.get("active") and item.get("metadata", {}).get("scag_bladesong") is True
                for item in after_dismiss["sheet"]["effects"]
            )
            assert after_dismiss["sheet"]["resources"]["scag_bladesong"]["value"] == 4

            # A fresh turn can start another song, and a two-handed attack
            # must end that active effect in the same transaction as the hit.
            await _combat_call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": wizard["id"],
                    "expected_revision": await _campaign_revision(server, campaign["id"]),
                    "idempotency_key": "end-wizard-before-restart-song",
                },
            )
            await _combat_call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": enemy["id"],
                    "expected_revision": await _campaign_revision(server, campaign["id"]),
                    "idempotency_key": "end-enemy-before-restart-song",
                },
            )
            await _combat_call(
                server,
                "combat_use_activity",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": wizard["id"],
                    "activity_id": _BLADESONG_ID,
                    "expected_revision": await _campaign_revision(server, campaign["id"]),
                    "idempotency_key": "reactivate-bladesong",
                },
            )
            reactivated = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": wizard["id"]}},
            )
            assert reactivated["sheet"]["resources"]["scag_bladesong"]["value"] == 3
            assert any(
                item.get("active") and item.get("metadata", {}).get("scag_bladesong") is True
                for item in reactivated["sheet"]["effects"]
            )
            two_handed = await _combat_call(
                server,
                "combat_resolve_attack",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": wizard["id"],
                    "target_id": enemy["id"],
                    "action": {"weapon_id": "wizard-longsword", "weapon_grip": "two_handed"},
                    "expected_revision": await _campaign_revision(server, campaign["id"]),
                    "idempotency_key": "two-handed-bladesong-ending-attack",
                },
            )
            assert _result_payload(two_handed)["damage"] is not None
            ended_after_two_handed = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": wizard["id"]}},
            )
            assert not any(
                item.get("active") and item.get("metadata", {}).get("scag_bladesong") is True
                for item in ended_after_two_handed["sheet"]["effects"]
            )
            ended_effect = next(
                item
                for item in ended_after_two_handed["sheet"]["effects"]
                if item.get("metadata", {}).get("scag_bladesong") is True
                and item.get("ended_reason") == "two_handed_attack"
            )
            assert ended_effect["active"] is False
            assert ended_effect["ended_reason"] == "two_handed_attack"

            # Bladesong uses recover only on a long rest; an ordinary short
            # rest must leave the spent pool unchanged.
            closed = await _combat_call(
                server,
                "combat_end",
                {
                    "campaign_id": campaign["id"],
                    "expected_revision": await _campaign_revision(server, campaign["id"]),
                    "idempotency_key": "close-bladesong-combat",
                },
            )
            after_close = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": wizard["id"]}},
            )
            short_rest = await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "party_rest",
                    "payload": {
                        "rest_type": "short_rest",
                        "duration_minutes": 60,
                        "members": [
                            {
                                "character_id": wizard["id"],
                                "expected_revision": after_close["revision"],
                            }
                        ],
                    },
                    "expected_revision": closed["campaign_revision"],
                    "idempotency_key": "short-rest-no-bladesong-recovery",
                },
            )
            after_short_rest = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": wizard["id"]}},
            )
            assert short_rest["rest_type"] == "short_rest"
            assert after_short_rest["sheet"]["resources"]["scag_bladesong"]["value"] == 3
            long_rest_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            long_rest = await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "party_rest",
                    "payload": {
                        "rest_type": "long_rest",
                        "duration_minutes": 480,
                        "members": [
                            {
                                "character_id": wizard["id"],
                                "expected_revision": after_short_rest["revision"],
                            }
                        ],
                    },
                    "expected_revision": long_rest_campaign["revision"],
                    "idempotency_key": "long-rest-bladesong-recovery",
                },
            )
            assert long_rest["rest_type"] == "long_rest"
            after_long_rest = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": wizard["id"]}},
            )
            assert after_long_rest["sheet"]["resources"]["scag_bladesong"]["value"] == 5
        finally:
            close_server(server)

        restarted = create_server(config)
        try:
            restored = await _call(
                restarted,
                "character_query",
                {"view": "get", "payload": {"character_id": wizard["id"]}},
            )
            assert restored["sheet"]["progression"]["classes"][0]["subclass"] == "Bladesinging"
            assert restored["sheet"]["resources"]["scag_bladesong"]["recovers_on"] == "long_rest"
            assert restored["sheet"]["resources"]["scag_bladesong"]["value"] == 5
        finally:
            close_server(restarted)

    asyncio.run(exercise())
