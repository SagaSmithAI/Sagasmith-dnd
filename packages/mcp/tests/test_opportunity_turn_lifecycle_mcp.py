import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from test_opportunity_sneak_attack_mcp import _call
from test_structured_spell_mcp import _slot, _spell

import sagasmith_dnd_mcp.server as server_module
from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server


async def _raw(server, name, arguments):
    _, response = await server.call_tool(name, arguments)
    if isinstance(response, dict) and "action" in response and "result" in response:
        return response["result"]
    return response


class _FixedD20:
    def __init__(self, value):
        self.value = value

    def randint(self, lower, upper):
        assert (lower, upper) == (1, 20)
        assert lower <= self.value <= upper
        return self.value


@pytest.mark.parametrize("miss", [False, True])
@pytest.mark.parametrize("advantage_source", ["help", "guiding_bolt"])
def test_opportunity_help_is_consumed_on_hit_or_miss_and_sneak_attack_resets_each_turn(
    tmp_path: Path, monkeypatch, miss: bool, advantage_source: str
) -> None:
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=tmp_path / "dnd",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
    )
    plans = []
    real_roll = server_module.roll_attack_action

    def attack_roll(*, plan):
        if plan.get("weapon_id") == "dagger":
            plans.append(plan)
        value = (
            1
            if miss and plan.get("weapon_id") == "dagger" and len(plans) == 1
            else 12 - plan["attack_bonus"]
        )
        return real_roll(plan=plan, rng=_FixedD20(value))

    monkeypatch.setattr(server_module, "roll_attack_action", attack_roll)

    async def exercise():
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Opportunity turns",
                    "edition": "2014",
                    "idempotency_key": "campaign",
                },
            )
            rogue_sheet = default_character_sheet()
            rogue_sheet["abilities"]["dexterity"]["score"] = 16
            rogue_sheet["progression"]["classes"] = [{"name": "Rogue", "level": 1, "hit_die": 8}]
            rogue_sheet["content"]["features"] = [
                {
                    "id": "dnd5e.content.srd2014.feature.rogue-sneak-attack",
                    "name": "Sneak Attack",
                    "source_key": "Rogue",
                }
            ]
            rogue_sheet["inventory"]["items"] = [
                {
                    "id": "dagger",
                    "name": "Dagger",
                    "kind": "weapon",
                    "equipped": True,
                    "equipped_slot": "main_hand",
                    "mechanics": {
                        "category": "simple",
                        "attack_type": "melee",
                        "attack_ability": "dexterity",
                        "damage_formula": "1d4",
                        "damage_type": "piercing",
                        "properties": ["finesse", "light"],
                    },
                }
            ]
            rogue_sheet["inventory"]["equipment_slots"]["main_hand"] = "dagger"
            target_sheet = default_character_sheet()
            target_sheet["combat"]["hp"] = {"value": 100, "max": 100, "temp": 0}
            actors = []
            helper_sheet = default_character_sheet()
            guiding_bolt = _spell("Guiding Bolt", 1, casting_time="1 action", range_ft=120)
            if advantage_source == "guiding_bolt":
                helper_sheet["abilities"]["wisdom"]["score"] = 18
                helper_sheet["spellcasting"].update(ability="wisdom", spell_slots=_slot(1))
                helper_sheet["content"]["spells"] = [guiding_bolt]
            for name, sheet in [
                ("rogue", rogue_sheet),
                ("helper", helper_sheet),
                ("mover", target_sheet),
            ]:
                actors.append(
                    await _call(
                        server,
                        "character_create_from",
                        {
                            "mode": "direct",
                            "payload": {
                                "name": name,
                                "sheet": sheet,
                                "campaign_id": campaign["id"],
                            },
                            "idempotency_key": name,
                        },
                    )
                )
            rogue, helper, mover = actors

            async def snapshot():
                return {
                    "campaign": await _call(
                        server,
                        "campaign_query",
                        {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                    ),
                    "actors": [
                        await _call(
                            server,
                            "character_query",
                            {"view": "get", "payload": {"character_id": actor["id"]}},
                        )
                        for actor in actors
                    ],
                    "receipts": await _call(
                        server,
                        "campaign_rules",
                        {"campaign_id": campaign["id"], "action": "receipts", "payload": {}},
                    ),
                }

            async def status():
                return await _call(
                    server, "combat_query", {"campaign_id": campaign["id"], "view": "status"}
                )

            started = await _raw(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "grid",
                    "battle_map": {"width_cells": 8, "height_cells": 8},
                    "participant_ids": [actor["id"] for actor in actors],
                    "participant_config": [
                        {
                            "actor_id": helper["id"],
                            "initiative": 30,
                            "position": {"x": 1, "y": 1},
                            "disposition": "friendly",
                        },
                        {
                            "actor_id": mover["id"],
                            "initiative": 20,
                            "position": {"x": 0, "y": 0},
                            "disposition": "hostile",
                        },
                        {
                            "actor_id": rogue["id"],
                            "initiative": 10,
                            "position": {"x": 1, "y": 0},
                            "disposition": "friendly",
                            "reach_ft": 5,
                        },
                    ],
                    "expected_revision": campaign["revision"],
                    "idempotency_key": "start",
                },
            )
            if advantage_source == "guiding_bolt":
                cast = await _raw(
                    server,
                    "combat_cast_spell",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": helper["id"],
                        "spell_id": guiding_bolt["id"],
                        "cast_level": 1,
                        "expected_revision": started["campaign_revision"],
                        "idempotency_key": "guiding-bolt",
                    },
                )
                helped = await _raw(
                    server,
                    "combat_resolve_attack",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": helper["id"],
                        "target_id": mover["id"],
                        "action": {"spell_resolution_id": cast["result"]["resolution_id"]},
                        "expected_revision": cast["campaign_revision"],
                        "idempotency_key": "guiding-hit",
                    },
                )
                effect = helped["result"]["standard_on_hit_effects"][0]
                assert effect["kind"] == "next_attack_advantage"
                effect_id = effect["id"]
            else:
                helped = await _raw(
                    server,
                    "combat_common_action",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": helper["id"],
                        "target_id": rogue["id"],
                        "action": "help",
                        "expected_revision": started["campaign_revision"],
                        "idempotency_key": "help",
                    },
                )
            ended = await _raw(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": helper["id"],
                    "expected_revision": helped["campaign_revision"],
                    "idempotency_key": "end-helper",
                },
            )
            moved = await _raw(
                server,
                "combat_movement",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": mover["id"],
                    "action": "move",
                    "payload": {"distance": 15, "destination": {"x": 3, "y": 0}},
                    "expected_revision": ended["campaign_revision"],
                    "idempotency_key": "move",
                },
            )
            choices = await _call(
                server,
                "combat_query",
                {"campaign_id": campaign["id"], "view": "reactions", "actor_id": rogue["id"]},
            )
            request = {
                "campaign_id": campaign["id"],
                "actor_id": rogue["id"],
                "target_id": mover["id"],
                "choice_id": choices[0]["id"],
                "action": {"weapon_id": "dagger", "use_sneak_attack": True},
                "expected_revision": moved["campaign_revision"],
                "idempotency_key": "oa",
            }
            assert choices[0]["target_position"] != {"x": 3, "y": 0}
            assert next(
                item for item in moved["combat"]["combatants"] if item["actor_id"] == mover["id"]
            )["position"] == {"x": 3, "y": 0}
            before = await snapshot()
            with pytest.raises(ToolError, match="revision conflict"):
                await _raw(
                    server,
                    "combat_reaction_attack",
                    {**request, "expected_revision": 0, "idempotency_key": "stale"},
                )
            assert await snapshot() == before
            result = await _raw(server, "combat_reaction_attack", request)
            assert result["result"]["hit"] is not miss
            if advantage_source == "help":
                assert plans[0]["helped_by"] == helper["id"]
            else:
                assert plans[0]["next_attack_advantage_effect_id"] == effect_id
                assert result["result"]["consumed_next_attack_advantage_effect_id"] == effect_id
            assert plans[0]["sneak_attack"]["eligibility"] == "advantage"
            combat = await status()
            if advantage_source == "guiding_bolt":
                consumed = next(
                    item for item in combat["ongoing_effects"] if item["id"] == effect_id
                )
                assert consumed["active"] is False
            states = {item["actor_id"]: item for item in combat["combatants"]}
            assert not states[helper["id"]].get("turn_flags", {}).get("helping")
            assert states[rogue["id"]]["turn_budget"]["reaction"] == 0
            assert states[rogue["id"]]["turn_budget"]["main_action"] == 1
            token = states[rogue["id"]].get("turn_flags", {}).get("sneak_attack_turn_token")
            assert bool(token) is not miss
            if not miss:
                assert token == result["result"]["sneak_attack"]["turn_token"]
            after = await snapshot()
            hp = after["actors"][2]["sheet"]["combat"]["hp"]["value"]
            before_hp = before["actors"][2]["sheet"]["combat"]["hp"]["value"]
            assert (hp == before_hp) is miss
            if not miss:
                assert hp == before_hp - result["result"]["damage"]["applied_amount"]
            assert await _raw(server, "combat_reaction_attack", request) == result
            assert await snapshot() == after
            close_server(server)
            server = create_server(config)
            assert await _raw(server, "combat_reaction_attack", request) == result
            assert await snapshot() == after

            revision = after["campaign"]["revision"]
            for choice in (await status()).get("pending", []):
                declined = await _call(
                    server,
                    "combat_choice",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": choice["actor_id"],
                        "action": "resolve",
                        "payload": {"choice_id": choice["id"], "selection": {"id": "decline"}},
                        "expected_revision": revision,
                        "idempotency_key": f"decline-{choice['id']}",
                    },
                )
                revision = declined["campaign_revision"]
            next_turn = await _raw(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": mover["id"],
                    "expected_revision": revision,
                    "idempotency_key": "end-mover",
                },
            )
            approached = await _raw(
                server,
                "combat_movement",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": rogue["id"],
                    "action": "move",
                    "payload": {"distance": 5, "destination": {"x": 2, "y": 0}},
                    "expected_revision": next_turn["campaign_revision"],
                    "idempotency_key": "approach",
                },
            )
            own_attack = await _raw(
                server,
                "combat_resolve_attack",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": rogue["id"],
                    "target_id": mover["id"],
                    "action": {
                        "weapon_id": "dagger",
                        "use_sneak_attack": True,
                        "context": {"advantage": True},
                    },
                    "expected_revision": approached["campaign_revision"],
                    "idempotency_key": "own-attack",
                },
            )
            assert own_attack["result"]["sneak_attack"]["used"] is True
            own_token = own_attack["result"]["sneak_attack"]["turn_token"]
            old_token = plans[0]["sneak_attack"]["turn_token"]
            assert own_token != old_token
            assert own_token.split(":")[0] == old_token.split(":")[0]
            assert not plans[-1]["helped_by"]
            assert not plans[-1]["next_attack_advantage_effect_id"]
            own_state = next(
                item for item in (await status())["combatants"] if item["actor_id"] == rogue["id"]
            )
            assert own_state["turn_flags"]["sneak_attack_turn_token"] == own_token
        finally:
            close_server(server)

    asyncio.run(exercise())
