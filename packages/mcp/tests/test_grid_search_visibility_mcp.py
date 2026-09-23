from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_core.state import StateMutationService
from sagasmith_dnd.character_schema import default_character_sheet
from test_structured_spell_mcp import _campaign_with_combat, _raw

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server


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


def _vision_cell(
    *, illumination: str | None = "bright", obscuration: str | None = None
) -> dict:
    return {
        "x": 2,
        "y": 0,
        "illumination": illumination,
        "obscuration": obscuration,
        "magical_darkness": False,
        "opaque": False,
        "source_ref": "fixture:grid-search-vision",
        "source_excerpt": "The DM reviewed the illumination and obscuration at this grid cell.",
    }


@pytest.mark.parametrize(
    ("cell", "expected_light", "expected_obscuration", "expected_disadvantage"),
    [
        (_vision_cell(illumination="bright"), "bright", "none", False),
        (_vision_cell(illumination="dim"), "dim", "none", True),
        (_vision_cell(illumination="bright", obscuration="lightly"), "bright", "lightly", True),
    ],
    ids=["clear", "dim-light", "lightly-obscured"],
)
def test_grid_search_perception_uses_selected_cell_vision(
    tmp_path: Path,
    cell: dict,
    expected_light: str,
    expected_obscuration: str,
    expected_disadvantage: bool,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign_id, revision, actors = await _campaign_with_combat(
                server,
                [("Searcher", default_character_sheet()), ("Witness", default_character_sheet())],
                positions=[(0, 0), (1, 0)],
                battle_map={
                    "width_cells": 6,
                    "height_cells": 2,
                    "ambient_illumination": "bright",
                    "vision_cells": [cell],
                },
            )
            result = await _raw(
                server,
                "combat_check",
                {
                    "campaign_id": campaign_id,
                    "actor_id": actors[0]["id"],
                    "kind": "check",
                    "ability": "perception",
                    "action": "search",
                    "dc": 12,
                    "rule_facts": {"relies_on_sight": True},
                    "search_target": {"kind": "grid_cell", "x": 2, "y": 0},
                    "expected_revision": revision,
                    "idempotency_key": "grid-search-vision",
                },
            )

            check = result["result"]
            assert check["vision"]["light_level"] == expected_light
            assert check["vision"]["obscuration"] == expected_obscuration
            assert check["vision"]["perception_disadvantage"] is expected_disadvantage
            assert check["disadvantage_applied"] is expected_disadvantage
            assert len(check["rolls"]) == (2 if expected_disadvantage else 1)
            assert any(
                receipt["mechanic_id"] == "dnd5e.core.vision.light_obscuration_2014"
                for receipt in check["rule_receipts"]
            )
            assert "search_target" not in check
            assert "source_cells" not in check["vision"]
            assert all(
                "position" not in source
                for source in check["vision"].get("source_lights", [])
            )
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_grid_search_target_validation_rollbacks_and_restart_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        try:
            campaign_id, revision, actors = await _campaign_with_combat(
                server,
                [("Searcher", default_character_sheet()), ("Witness", default_character_sheet())],
                positions=[(0, 0), (1, 0)],
                battle_map={
                    "width_cells": 6,
                    "height_cells": 2,
                    "ambient_illumination": "bright",
                    "blocked_cells": [{"x": 4, "y": 0}],
                    "vision_cells": [_vision_cell(illumination="dim")],
                },
            )

            async def campaign_snapshot() -> dict:
                response = await _raw(
                    server,
                    "campaign_query",
                    {
                        "view": "get",
                        "payload": {"campaign_id": campaign_id, "detail": "full"},
                        "principal_id": "system:local",
                    },
                )
                return response["result"]

            base = {
                "campaign_id": campaign_id,
                "actor_id": actors[0]["id"],
                "kind": "check",
                "ability": "perception",
                "action": "search",
                "dc": 12,
                "rule_facts": {"relies_on_sight": True},
                "expected_revision": revision,
            }
            before_invalid = await campaign_snapshot()
            with pytest.raises(ToolError, match="requires search_target grid_cell"):
                await _raw(server, "combat_check", {**base, "idempotency_key": "missing-cell"})
            after_missing = await campaign_snapshot()
            assert after_missing["revision"] == before_invalid["revision"]
            assert after_missing["state"] == before_invalid["state"]

            for key, target in (
                ("outside-map", {"kind": "grid_cell", "x": 6, "y": 0}),
                ("blocked-cell", {"kind": "grid_cell", "x": 4, "y": 0}),
            ):
                with pytest.raises(ToolError, match="search_target is not a legal Grid cell"):
                    await _raw(
                        server,
                        "combat_check",
                        {
                            **base,
                            "search_target": target,
                            "idempotency_key": key,
                        },
                    )
                after_invalid = await campaign_snapshot()
                assert after_invalid["revision"] == before_invalid["revision"]
                assert after_invalid["state"] == before_invalid["state"]

            replayable = {
                **base,
                "search_target": {"kind": "grid_cell", "x": 2, "y": 0},
                "idempotency_key": "grid-search-restart",
            }
            original_replace = StateMutationService.replace
            attempted = []

            def conflict(service, campaign_id_value, **kwargs):
                attempted.append(True)
                kwargs["expected_campaign_revision"] = -1
                return original_replace(service, campaign_id_value, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(StateMutationService, "replace", conflict)
                with pytest.raises(ToolError, match="revision conflict"):
                    await _raw(server, "combat_check", replayable)
            assert attempted
            after_conflict = await campaign_snapshot()
            assert after_conflict["revision"] == before_invalid["revision"]
            assert after_conflict["state"] == before_invalid["state"]

            committed = await _raw(server, "combat_check", replayable)
            assert committed["status"] == "committed"
            with pytest.raises(ToolError, match="idempotency"):
                await _raw(
                    server,
                    "combat_check",
                    {
                        **replayable,
                        "search_target": {"kind": "grid_cell", "x": 3, "y": 0},
                    },
                )

            close_server(server)
            server = create_server(config)
            assert await _raw(server, "combat_check", replayable) == committed
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_grid_search_investigation_does_not_apply_visual_profile(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign_id, revision, actors = await _campaign_with_combat(
                server,
                [("Searcher", default_character_sheet()), ("Witness", default_character_sheet())],
                positions=[(0, 0), (1, 0)],
                battle_map={
                    "width_cells": 6,
                    "height_cells": 2,
                    "ambient_illumination": "bright",
                    "vision_cells": [_vision_cell(illumination="dim")],
                },
            )
            settled = await _raw(
                server,
                "combat_check",
                {
                    "campaign_id": campaign_id,
                    "actor_id": actors[0]["id"],
                    "kind": "check",
                    "ability": "investigation",
                    "action": "search",
                    "dc": 12,
                    "search_target": {"kind": "grid_cell", "x": 2, "y": 0},
                    "expected_revision": revision,
                    "idempotency_key": "grid-investigation-no-vision",
                },
            )
            check = settled["result"]
            assert check["action"] == "search"
            assert check["skill"] == "investigation"
            assert "vision" not in check
            assert "search_target" not in check
        finally:
            close_server(server)

    asyncio.run(exercise())
