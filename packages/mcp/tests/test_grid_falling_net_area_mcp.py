"""Grid Falling Net area is bound to reviewed cells and resolved from combat positions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet
from test_official_expansions_mcp import _call

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import finalize_and_activate_module


def test_grid_falling_net_uses_reviewed_map_cells_and_authoritative_footprints(
    tmp_path: Path,
) -> None:
    profile = {"profile_id": "srd5.1.falling_net"}
    marker = json.dumps(profile, sort_keys=True, separators=(",", ":"))
    excerpt = (
        "A net covers a ten-foot square. Creatures in the area are restrained; "
        "each makes a DC 10 Strength save and those that fail are also knocked prone.\n"
        f"trap_profile: {marker}"
    )
    source = tmp_path / "net.md"
    source.write_text(f"# Trap\n\n## Falling Net\n\n{excerpt}\n", encoding="utf-8")
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=Path(__file__).resolve().parents[3] / "skills",
        modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=False,
        module_import_roots=(tmp_path,),
    )

    async def exercise() -> None:
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Grid trap", "edition": "2014", "random_seed": "grid-net",
                 "idempotency_key": "campaign"},
            )
            campaign_id = campaign["id"]
            staged = await _call(
                server,
                "module_draft",
                {"campaign_id": campaign_id, "action": "start", "idempotency_key": "draft",
                 "payload": {"source_path": str(source), "source_key": "net-source",
                             "title": "Net source"}},
            )

            async def helper_call(target, name, arguments):
                value = await _call(target, name, arguments)
                if isinstance(value, dict) and "action" in value and "result" in value:
                    return value["result"]
                return value

            await finalize_and_activate_module(
                helper_call, server, campaign_id, staged, source_key="net-source",
                title="Net source", portable_id="dnd5e.module.net-source",
            )
            actor_ids = []
            for name in ("inside", "outside"):
                sheet = default_character_sheet()
                sheet["edition"] = "2014"
                sheet["combat"]["hp"] = {"value": 100, "max": 100, "temp": 0}
                actor = await _call(
                    server, "character_create_from",
                    {"mode": "direct", "payload": {"campaign_id": campaign_id,
                     "name": name, "sheet": sheet}, "idempotency_key": name},
                )
                actor_ids.append(actor["id"])
            hits = await _call(server, "module_search", {
                "campaign_id": campaign_id, "query": "Falling Net", "top_k": 3,
            })
            expanded = await _call(server, "module_expand", {"chunk_id": hits[0]["id"]})
            source_ref = json.dumps(expanded["source_ref"], sort_keys=True, separators=(",", ":"))
            before = await _call(server, "campaign_query", {
                "view": "get", "payload": {"campaign_id": campaign_id},
            })
            await _call(server, "combat_start", {
                "campaign_id": campaign_id,
                "scene_id": expanded["scene"]["id"],
                "positioning_mode": "grid",
                "battle_map": {"width_cells": 8, "height_cells": 8},
                "participant_ids": actor_ids,
                "participant_config": [
                    {"actor_id": actor_ids[0], "initiative": 20, "position": {"x": 0, "y": 0}},
                    {"actor_id": actor_ids[1], "initiative": 10, "position": {"x": 4, "y": 4}},
                ],
                "expected_revision": before["revision"],
                "idempotency_key": "combat",
            })
            current = await _call(server, "campaign_query", {
                "view": "get", "payload": {"campaign_id": campaign_id},
            })
            encounter = current["state"]["combat"]
            battle_map = encounter["battle_map"]
            base_facts = {
                "decision_id": "dm-net-grid-area",
                "reason": "DM reviewed the marked net footprint.",
                "scene_id": expanded["scene"]["id"], "trap_id": "net-grid-1",
                "encounter_id": encounter["id"], "source_ref": source_ref,
                "campaign_revision": current["revision"], "reviewed_by": "system:local",
                "grid_area": {"map_id": battle_map["id"],
                              "map_revision": battle_map["map_revision"],
                              "cells": ["0,0", "0,1", "1,0", "1,1"]},
            }
            request = {
                "campaign_id": campaign_id, "trap_id": "net-grid-1", "action": "trigger",
                "source_ref": source_ref, "source_excerpt": excerpt, "profile": profile,
                "actor_id": actor_ids[0], "target_ids": None, "area_confirmed": None,
                "spatial_facts": base_facts, "expected_revision": current["revision"],
                "idempotency_key": "net-grid-trigger",
            }
            # An empty 10-foot square still releases the one-shot net.
            empty_request = {
                **request,
                "trap_id": "net-grid-empty",
                "spatial_facts": {**base_facts, "trap_id": "net-grid-empty",
                                  "grid_area": {**base_facts["grid_area"],
                                                "cells": ["2,2", "2,3", "3,2", "3,3"]}},
                "idempotency_key": "empty-area",
            }
            empty_settled = await _call(server, "trap_state_transition", empty_request)
            assert empty_settled["check"] is None
            assert empty_settled["checks"] == []
            assert empty_settled["targets"] == []
            assert empty_settled["affected_actor_ids"] == []
            assert empty_settled["trap"]["status"] == "triggered"
            assert await _call(server, "trap_state_transition", empty_request) == empty_settled
            current = await _call(server, "campaign_query", {
                "view": "get", "payload": {"campaign_id": campaign_id},
            })
            base_facts["campaign_revision"] = current["revision"]
            request["expected_revision"] = current["revision"]
            request["spatial_facts"] = base_facts
            # A map revision change makes an otherwise valid DM review stale.
            with pytest.raises(ToolError, match="current 5-foot square battle map and revision"):
                await _call(server, "trap_state_transition", {
                    **request, "spatial_facts": {**base_facts, "grid_area": {
                        **base_facts["grid_area"], "map_revision": battle_map["map_revision"] - 1,
                    }}, "idempotency_key": "stale-map-review",
                })
            # Unauthenticated/non-DM caller cannot turn a reviewed geometry into a write.
            with pytest.raises(ToolError):
                await _call(server, "trap_state_transition", {
                    **request, "principal_id": "player:untrusted",
                    "idempotency_key": "untrusted-reviewer",
                })
            after_rejects = await _call(server, "campaign_query", {
                "view": "get", "payload": {"campaign_id": campaign_id},
            })
            assert after_rejects["revision"] == current["revision"]
            # Current DM review resolves only the in-area actor and replays by CAS key.
            settled = await _call(server, "trap_state_transition", request)
            assert settled["affected_actor_ids"] == [actor_ids[0]]
            assert [item["target_id"] for item in settled["targets"]] == [actor_ids[0]]
            assert await _call(server, "trap_state_transition", request) == settled
        finally:
            close_server(server)

    import asyncio

    asyncio.run(exercise())
