"""Public spell settlement must work without fabricated coordinates in Agent mode."""
import asyncio

import pytest
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.spells import CORE_MAGIC_MISSILE_MECHANIC_ID, CORE_MAGIC_MISSILE_SPELL_ID
from test_magic_missile_mcp import _slots, _spell
from test_structured_spell_mcp import _campaign_with_combat, _config, _raw, create_server


def test_agent_magic_missile_validates_every_target_before_spending(tmp_path):
    async def exercise():
        server = create_server(_config(tmp_path))
        caster = default_character_sheet()
        caster["spellcasting"]["spell_slots"] = _slots()
        caster["content"]["spells"] = [_spell(
            CORE_MAGIC_MISSILE_SPELL_ID, "Magic Missile",
            CORE_MAGIC_MISSILE_MECHANIC_ID, "1 action",
        )]
        target = default_character_sheet()
        target["combat"]["hp"] = {"value": 30, "max": 30, "temp": 0}
        campaign, revision, actors = await _campaign_with_combat(
            server, [("Caster", caster), ("Target", target)], positioning_mode="agent",
        )
        target_id = actors[1]["id"]
        arguments = {
            "campaign_id": campaign, "actor_id": actors[0]["id"],
            "spell_id": CORE_MAGIC_MISSILE_SPELL_ID, "cast_level": 1,
            "target_allocations": [{"target_id": target_id, "darts": 3}],
            "expected_revision": revision, "idempotency_key": "agent-missile",
        }
        facts = {
            "decision_id": "room-target", "reason": "Visible creature within 120 feet",
            "targetable": True, "in_range": True, "attacker_can_see_target": True,
        }
        for declaration in (
            {},
            {"target_spatial_facts": {}},
            {"target_spatial_facts": {target_id: {**facts, "in_range": False}}},
            {"target_spatial_facts": {target_id: {**facts, "attacker_can_see_target": False}}},
            {"target_spatial_facts": {target_id: {**facts, "in_range": "true"}}},
        ):
            with pytest.raises(Exception):
                await _raw(server, "combat_cast_spell", {**arguments, "declaration": declaration})
        result = await _raw(server, "combat_cast_spell", {
            **arguments, "declaration": {"target_spatial_facts": {target_id: facts}},
        })
        assert result["status"] == "committed"
        assert len(result["result"]["targets"][0]["dart_results"]) == 3
        assert result["combat"]["combatants"][0]["turn_budget"]["main_action"] == 0
        assert all(not actor.get("position") for actor in result["combat"]["combatants"])

    asyncio.run(exercise())
