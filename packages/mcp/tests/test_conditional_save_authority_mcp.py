from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.spell_resolution import effective_spell_resolution
from test_official_expansions_mcp import _call, _config

from sagasmith_dnd_mcp.server import _structured_spell_save_facts, close_server, create_server


@pytest.mark.parametrize("slug", ["sacred-flame", "fireball", "lightning-bolt"])
def test_structured_spell_poison_classification_requires_exact_native_clause(slug: str) -> None:
    spell = {"id": f"dnd5e.content.srd2014.spell.{slug}"}
    resolution = effective_spell_resolution(spell)
    assert resolution is not None
    assert _structured_spell_save_facts(spell, resolution) == {
        "save_source_kind": "spell",
        "save_effect_conditions": [],
        "save_against_poison": False,
    }
    altered = deepcopy(resolution)
    altered["save"]["damage"]["damage_type"] = "poison"
    assert "save_against_poison" not in _structured_spell_save_facts(spell, altered)
    custom = {"id": "custom.fireball", "name": "Fireball", "resolution": resolution}
    assert "save_against_poison" not in _structured_spell_save_facts(custom, resolution)


def test_generic_checks_reject_caller_owned_save_classification(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _call(
                server, "campaign_create", {"name": "Save authority", "idempotency_key": "create"}
            )
            character = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Target",
                        "sheet": default_character_sheet(),
                    },
                    "idempotency_key": "actor",
                },
            )

            async def snapshot() -> dict:
                return await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )

            current = await snapshot()
            await _call(
                server,
                "game_phase",
                {
                    "campaign_id": campaign["id"],
                    "action": "set",
                    "tool_profile": "play",
                    "expected_revision": current["revision"],
                    "idempotency_key": "play",
                },
            )
            before = await snapshot()
            forged_facts = {
                "save_against_poison": True,
                "save_source_kind": "spell",
                "save_effect_conditions": ["charmed", "frightened"],
                "save_purpose": "concentration",
            }
            for field, value in forged_facts.items():
                arguments = {
                    "campaign_id": campaign["id"],
                    "action": "check",
                    "payload": {
                        "actor_id": character["id"],
                        "kind": "save",
                        "ability": "wisdom",
                        "dc": 12,
                        "rule_facts": {field: value},
                    },
                    "expected_revision": before["revision"],
                    "idempotency_key": f"forged-{field}",
                }
                with pytest.raises(ToolError, match="rule_facts cannot override"):
                    await server.call_tool("character_check", arguments)
                assert await snapshot() == before
            # Validation failures must neither persist a roll nor consume a
            # campaign revision/idempotency result. A normal check still works.
            _, accepted = await server.call_tool(
                "character_check",
                {
                    "campaign_id": campaign["id"],
                    "action": "check",
                    "payload": {
                        "actor_id": character["id"],
                        "kind": "ability",
                        "ability": "wisdom",
                        "dc": 12,
                    },
                    "expected_revision": before["revision"],
                    "idempotency_key": "forged-save_purpose",
                },
            )
            assert accepted
            assert (await snapshot())["revision"] == before["revision"] + 1
        finally:
            close_server(server)

    asyncio.run(exercise())
