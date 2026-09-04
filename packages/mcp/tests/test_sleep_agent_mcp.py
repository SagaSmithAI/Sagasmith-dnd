from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.standard_spell_ids import CORE_SLEEP_SPELL_ID
from test_official_expansions_mcp import _call, _config

from sagasmith_dnd_mcp.server import close_server, create_server


@pytest.mark.fresh_database
def test_sleep_noncombat_missing_agent_spatial_facts_is_pending_without_payment(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        workspace = Path(__file__).resolve().parents[3]
        config = replace(
            _config(tmp_path), auto_seed_rules=True, dnd_skills_dir=workspace / "skills"
        )
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Sleep agent", "edition": "2014", "idempotency_key": "campaign"},
            )
            spells = await _call(
                server,
                "character_query",
                {
                    "view": "catalog",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "kind": "spell",
                        "query": "Sleep",
                    },
                },
            )
            sleep = next(item for item in spells if item["id"] == CORE_SLEEP_SPELL_ID)
            sheet = default_character_sheet()
            sheet["progression"]["level"] = 3
            sheet["progression"]["classes"] = [
                {"name": "Bard", "level": 3, "subclass": "", "hit_die": 8}
            ]
            sheet["spellcasting"]["spell_slots"] = {
                "1": {"label": "Level 1", "value": 1, "max": 1, "recovers_on": "long_rest"}
            }
            caster = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {"campaign_id": campaign["id"], "name": "Bard", "sheet": sheet},
                    "idempotency_key": "caster",
                },
            )
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": caster["id"],
                    "artifact_id": sleep["id"],
                    "selection": {"source_class": "Bard", "method": "known"},
                    "expected_revision": caster["revision"],
                    "idempotency_key": "sleep",
                },
            )
            before = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": caster["id"]}},
            )
            pending = await _call(
                server,
                "character_action",
                {
                    "character_id": caster["id"],
                    "action": "cast_spell",
                    "payload": {
                        "spell_id": sleep["id"],
                        "cast_level": 1,
                        "declaration": {},
                    },
                    "expected_revision": before["revision"],
                    "idempotency_key": "agent-sleep",
                },
            )
            assert pending["status"] == "pending_ruling"
            assert pending["missing"] == ["sleep.spatial_facts"]
            after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": caster["id"]}},
            )
            assert after == before
        finally:
            close_server(server)

    asyncio.run(exercise())
