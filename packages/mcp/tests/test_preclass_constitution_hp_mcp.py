"""Constitution accounting survives public content writes and rejects sheet forgery."""

import asyncio
from copy import deepcopy
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import import_and_activate_addon_fixture
from tests.test_progression_feature_sources_mcp import _call, _printing


@pytest.mark.fresh_database
def test_preclass_hp_order_and_engine_owned_accounting(tmp_path):
    workspace = Path(__file__).resolve().parents[3]
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=workspace / "skills",
        modulegen_skills_dir=workspace / "skills" / "dnd-module-generator",
    )
    server = create_server(config)

    async def exercise():
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "HP order",
                "edition": "2014",
                "idempotency_key": "campaign",
            },
        )
        pack = "dnd5e.addon.hp-order"
        await import_and_activate_addon_fixture(
            _call,
            server,
            campaign["id"],
            config.home,
            manifest={
                "id": pack,
                "version": "1.0.0",
                "title": "HP order",
                "namespace": pack,
                "system_id": "dnd5e",
                "editions": ["2014"],
                "capabilities": [],
            },
            artifacts=_printing(pack),
            mechanics=[],
            expected_revision=campaign["revision"],
            request_key="fixture",
        )
        for species_first in (True, False):
            key = str(species_first)
            sheet = default_character_sheet()
            sheet["abilities"]["constitution"]["score"] = 14
            actor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": key,
                        "sheet": sheet,
                    },
                    "idempotency_key": key,
                },
            )
            order = ["species", "class"] if species_first else ["class", "species"]
            for kind in order:
                arguments = {
                    "character_id": actor["id"],
                    "artifact_id": (
                        "dnd5e.content.srd2014.species.hill-dwarf"
                        if kind == "species"
                        else f"{pack}.class.fighter"
                    ),
                    "selection": {"tools": ["smith's tools"]}
                    if kind == "species"
                    else {"skills": ["athletics", "perception"]},
                    "expected_revision": actor["revision"],
                    "idempotency_key": f"{key}-{kind}",
                }
                actor = await _call(server, "character_content_apply", arguments)
                assert await _call(server, "character_content_apply", arguments) == actor
                if species_first and kind == "species":
                    assert actor["sheet"]["combat"]["preclass_constitution_hp_adjustment"] == 1
                    for value in (None, 7):
                        forged = deepcopy(actor["sheet"])
                        if value is None:
                            forged["combat"].pop("preclass_constitution_hp_adjustment")
                        else:
                            forged["combat"]["preclass_constitution_hp_adjustment"] = value
                        with pytest.raises(Exception, match="engine-owned"):
                            await _call(
                                server,
                                "character_sheet_replace",
                                {
                                    "character_id": actor["id"],
                                    "sheet": forged,
                                    "expected_revision": actor["revision"],
                                    "idempotency_key": f"tamper-{value}",
                                },
                            )
            assert actor["sheet"]["combat"]["hp"] == {"max": 14, "value": 14, "temp": 0}
            assert "preclass_constitution_hp_adjustment" not in actor["sheet"]["combat"]
        forged = default_character_sheet()
        forged["combat"]["preclass_constitution_hp_adjustment"] = 1
        with pytest.raises(Exception, match="engine-owned"):
            await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "forged",
                        "sheet": forged,
                    },
                    "idempotency_key": "forged",
                },
            )

    try:
        asyncio.run(exercise())
    finally:
        close_server(server)
