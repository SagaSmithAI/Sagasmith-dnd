from __future__ import annotations

import asyncio

import pytest
from sagasmith_dnd import madness
from sagasmith_dnd.character_schema import add_effect, default_character_sheet
from sagasmith_dnd.spells import CORE_MAGIC_MISSILE_MECHANIC_ID, CORE_MAGIC_MISSILE_SPELL_ID
from test_magic_missile_mcp import _slots, _spell
from test_structured_spell_mcp import _campaign_with_combat, _config, _raw, create_server


def test_babbling_madness_blocks_combat_spell_before_payment(tmp_path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            caster = default_character_sheet()
            caster["spellcasting"]["spell_slots"] = _slots()
            caster["content"]["spells"] = [
                _spell(
                    CORE_MAGIC_MISSILE_SPELL_ID,
                    "Magic Missile",
                    CORE_MAGIC_MISSILE_MECHANIC_ID,
                    "1 action",
                )
            ]
            babbling = madness.resolve_madness("short_term", 41, duration_die=2)[
                "runtime_effect"
            ]
            babbling.update({"id": "babbling-madness", "name": "Babbling"})
            caster, _ = add_effect(caster, babbling)
            campaign_id, revision, actors = await _campaign_with_combat(
                server,
                [("Babbling caster", caster), ("Target", default_character_sheet())],
            )
            with pytest.raises(Exception, match="madness prohibits spellcasting"):
                await _raw(
                    server,
                    "combat_cast_spell",
                    {
                        "campaign_id": campaign_id,
                        "actor_id": actors[0]["id"],
                        "spell_id": CORE_MAGIC_MISSILE_SPELL_ID,
                        "cast_level": 1,
                        "expected_revision": revision,
                        "idempotency_key": "babbling-cast",
                    },
                )
        finally:
            from sagasmith_dnd_mcp.server import close_server

            close_server(server)

    asyncio.run(exercise())
