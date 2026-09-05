"""Real locked Eberron awards through public tools, without distributing the book."""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.test_official_expansions_mcp import _call, _locked_official_library, _selection_for

_PREFIX = "dnd5e.addon.rulebook.d-d-5e-eberron-rising-from-the-last-war.31293633134f"
_CLASS = _PREFIX + ".class.artificer"
_VERSION = "1.0.7-local.steel-defender-lifecycle.1"
_SRD = "dnd5e.content.srd2014.item."
_WEAPONS = (
    "club", "dagger", "greatclub", "handaxe", "javelin", "light-hammer", "mace",
    "quarterstaff", "sickle", "spear", "crossbow-light", "dart", "shortbow", "sling",
)
_FIXED = {"Crossbow, light": 1, "Crossbow bolts": 20, "Thieves' tools": 1,
          "Dungeoneer's Pack": 1}


@pytest.mark.fresh_database
def test_locked_artificer_all_starting_awards_and_restart(tmp_path: Path) -> None:
    library = _locked_official_library()
    workspace = Path(__file__).resolve().parents[3]
    config = McpConfig(
        home=tmp_path / "home", database_url=None, chroma_url=None, chroma_path_override=None,
        dnd_skills_dir=workspace / "skills", modulegen_skills_dir=tmp_path / "modulegen",
        auto_seed_rules=True, official_content_library=library,
    )

    async def exercise() -> None:
        server = create_server(config)
        saved = []
        try:
            campaign = await _call(server, "campaign_create", {
                "name": "Real Eberron equipment", "edition": "2014", "idempotency_key": "c",
            })
            profile = await _call(server, "campaign_rules", {
                "campaign_id": campaign["id"], "action": "get_profile",
            })
            await _call(server, "content_pack", {
                "action": "activate", "payload": {
                    "campaign_id": campaign["id"], "kind": "addon",
                    "addon_id": _PREFIX + ".addon", "version": _VERSION,
                }, "expected_revision": profile["campaign_revision"], "idempotency_key": "a",
            })
            entries = await _call(server, "character_query", {
                "view": "catalog", "payload": {"campaign_id": campaign["id"], "query": _CLASS},
            })
            entry = next(item for item in entries if item["id"] == _CLASS)
            contract = entry["selection_requirements"]["starting_equipment"]
            weapons = next(
                group for group in contract["choices"] if group["id"] == "simple_weapons"
            )
            assert set(weapons["options"]) == {_SRD + name for name in _WEAPONS}
            assert weapons["count"] == 2 and weapons["allow_duplicates"] is True
            assert contract["gold_alternative"] == {
                "dice": "5d4", "multiplier": 10, "denomination": "gp",
                "replaces_background_equipment": True,
            }
            base_selection = await _selection_for(server, campaign["id"], _CLASS)
            # Every simple-weapon option is actually awarded; also test duplicate
            # choices and the fixed crossbow overlapping a selected crossbow.
            choices = [list(_WEAPONS[index:index + 2]) for index in range(0, 14, 2)]
            choices += [["club", "club"], []]
            for index, selected in enumerate(choices):
                sheet = default_character_sheet()
                sheet["abilities"]["intelligence"]["score"] = 16
                sheet["inventory"]["wallet"]["gp"] = 17
                actor = await _call(server, "character_create_from", {
                    "mode": "direct", "payload": {
                        "campaign_id": campaign["id"], "name": f"Artificer {index}", "sheet": sheet,
                    }, "idempotency_key": f"actor-{index}",
                })
                background_items = set()
                if not selected:
                    background = _PREFIX + ".background.house-agent-sivis"
                    actor = await _call(server, "character_content_apply", {
                        "character_id": actor["id"], "artifact_id": background,
                        "selection": await _selection_for(server, campaign["id"], background),
                        "expected_revision": actor["revision"], "idempotency_key": "background",
                    })
                    assert actor["sheet"]["inventory"]["wallet"]["gp"] == 37
                    assert {item["name"] for item in actor["sheet"]["inventory"]["items"]} == {
                        "Fine Clothes", "House Signet Ring", "Identification Papers",
                    }
                    background_items = {
                        item["id"] for item in actor["sheet"]["inventory"]["items"]
                    }
                armor = "scale-mail" if index % 2 else "studded-leather"
                selection = {**base_selection, "starting_equipment": (
                    {"mode": "equipment", "choices": {
                        "simple_weapons": [_SRD + name for name in selected],
                        "armor": [_SRD + armor],
                    }} if selected else {"mode": "gold"}
                )}
                request = {
                    "character_id": actor["id"], "artifact_id": _CLASS,
                    "selection": selection, "expected_revision": actor["revision"],
                    "idempotency_key": f"apply-{index}",
                }
                if index == 0:
                    pending = await _call(server, "character_content_apply", {
                        **request, "selection": base_selection, "idempotency_key": "missing",
                    })
                    assert pending["status"] == "pending_choice"
                    with pytest.raises(ToolError, match="starting equipment"):
                        await _call(server, "character_content_apply", {
                            **request, "selection": {**base_selection, "starting_equipment": {
                                "mode": "equipment", "choices": {
                                    "simple_weapons": [_SRD + "longsword", _SRD + "club"],
                                    "armor": [_SRD + armor],
                                },
                            }}, "idempotency_key": "martial-not-simple",
                        })
                    assert await _call(server, "character_query", {
                        "view": "get", "payload": {"character_id": actor["id"]},
                    }) == actor
                before = await _call(server, "campaign_query", {
                    "view": "get", "payload": {"campaign_id": campaign["id"]},
                })
                applied = await _call(server, "character_content_apply", request)
                award = applied["class_materialization"]["starting_equipment"]
                inventory = applied["sheet"]["inventory"]
                assert all(not item["equipped"] for item in inventory["items"])
                assert all(value is None for value in inventory["equipment_slots"].values())
                if selected:
                    assert len(inventory["items"]) == len(set(award["item_ids"])) == 7
                    quantities = Counter()
                    for item in inventory["items"]:
                        quantities[item["name"]] += item["quantity"]
                    for name, count in _FIXED.items():
                        assert quantities[name] == count + (
                            selected.count("crossbow-light") if name == "Crossbow, light" else 0
                        )
                    assert quantities["Scale mail" if index % 2 else "Studded leather"] == 1
                    assert inventory["wallet"]["gp"] == 17
                    assert award["roll"] is None
                    assert set(award["item_sources"]) == {
                        _SRD + name for name in (
                            *selected, armor, "crossbow-light", "crossbow-bolts",
                            "thieves-tools", "dungeoneer-s-pack",
                        )
                    }
                    for item in inventory["items"]:
                        assert item["source_key"].endswith(":" + _CLASS)
                        if item["kind"] == "weapon":
                            assert item["mechanics"]["category"] == "simple"
                        if item["name"] == "Crossbow, light":
                            assert item["mechanics"]["damage_formula"] == "1d8"
                            assert item["mechanics"]["normal_range_ft"] == 80
                        if item["kind"] == "armor":
                            assert item["mechanics"]["base_ac"] == (14 if index % 2 else 12)
                            assert item["mechanics"]["stealth_disadvantage"] is bool(index % 2)
                    if selected == ["club", "club"]:
                        assert quantities["Club"] == 2
                else:
                    assert inventory["items"] == []
                    assert background_items
                    grants = applied["sheet"]["progression"]["background_grants"]
                    assert grants["equipment_item_ids"] == []
                    assert grants["choices"]["equipment_mode"] == "class_starting_gold"
                    assert grants["choices"]["starting_equipment_award"] == {
                        "items": [], "wallet": {},
                    }
                    assert award["item_sources"] == {}
                    assert 50 <= award["wallet"]["gp"] <= 200
                    assert award["wallet"]["gp"] == award["roll"]["total"] * 10
                    assert inventory["wallet"]["gp"] == 17 + award["wallet"]["gp"]
                    assert applied["random_stream_receipt"]["draw_count"] == 5
                    assert applied["random_stream_receipt"]["position_before"] == (
                        before["state"]["random_stream"]["position"]
                    )
                after = await _call(server, "campaign_query", {
                    "view": "get", "payload": {"campaign_id": campaign["id"]},
                })
                assert (
                    after["state"]["random_stream"]["position"]
                    - before["state"]["random_stream"]["position"]
                ) == (0 if selected else 5)
                assert await _call(server, "character_content_apply", request) == applied
                final = await _call(server, "character_query", {
                    "view": "get", "payload": {"character_id": actor["id"]},
                })
                assert final["sheet"] == applied["sheet"]
                saved.append((request, applied, final))
            close_server(server)
            server = create_server(config)
            for request, applied, final in saved:
                assert await _call(server, "character_content_apply", request) == applied
                assert await _call(server, "character_query", {
                    "view": "get", "payload": {"character_id": final["id"]},
                }) == final
        finally:
            close_server(server)

    asyncio.run(exercise())
