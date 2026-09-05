"""Public starting-wealth settlement with source-neutral reviewed fixtures."""

import asyncio
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.random_state import RandomStateMutationService
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import import_and_activate_addon_fixture
from tests.test_progression_feature_sources_mcp import _artifact, _call, _printing

PACK = "dnd5e.addon.starting-equipment-fixture"


def _equipment_artifacts():
    artifacts = _printing(PACK)
    class_artifact = artifacts[1]
    equipment = {
        "items": [
            {"artifact_id": f"{PACK}.item.bolts", "quantity": 20},
            {"artifact_id": "dnd5e.content.srd2014.item.crossbow-light", "quantity": 1},
        ],
        "choices": [
            {
                "id": "weapons", "count": 2, "allow_duplicates": True,
                "options": [f"{PACK}.item.club", f"{PACK}.item.dagger"],
            },
            {
                "id": "armor", "count": 1,
                "options": [f"{PACK}.item.light-armor", f"{PACK}.item.medium-armor"],
            },
        ],
        "gold_alternative": {
            "dice": "5d4", "multiplier": 10, "denomination": "gp",
            "replaces_background_equipment": True,
        },
    }
    card = deepcopy(class_artifact["card"])
    card["class_definition"]["starting_equipment"] = equipment
    artifacts[1] = _artifact(PACK, "class", "fighter", card)
    for slug in ("bolts", "club", "dagger", "light-armor", "medium-armor"):
        artifacts.append(_artifact(PACK, "item", slug, {
            "name": slug,
            "inventory_template": {
                "name": slug, "kind": "equipment", "quantity": 1,
                "description": "Synthetic equipment selection fixture.", "mechanics": {},
            },
        }))
    artifacts.append(_artifact(PACK, "background", "courier", {
        "name": "Fixture Courier",
        "skill_proficiencies": ["insight", "persuasion"],
        "background_grants": {
            "skills": ["insight", "persuasion"], "feature": "Fixture Access",
            "languages": [], "tools": [], "equipment_item_ids": [],
            "choices": {"language_count": 0, "tool_choice_count": 0},
            "equipment": {
                "items": [{"inventory_template": {
                    "name": "Courier Badge", "kind": "equipment", "quantity": 1,
                    "description": "Synthetic background award.", "mechanics": {},
                }}],
                "wallet": {"gp": 10, "sp": 0},
            },
        },
    }))
    return artifacts


@pytest.mark.fresh_database
def test_starting_equipment_public_order_atomicity_and_restart(tmp_path, monkeypatch):
    workspace = Path(__file__).resolve().parents[3]
    config = McpConfig(
        home=tmp_path / "home", database_url=None, chroma_url=None, chroma_path_override=None,
        dnd_skills_dir=workspace / "skills",
        modulegen_skills_dir=workspace / "skills" / "dnd-module-generator",
    )
    saved = []

    async def exercise(server):
        campaign = await _call(server, "campaign_create", {
            "name": "Starting awards", "edition": "2014", "idempotency_key": "campaign",
        })
        await import_and_activate_addon_fixture(
            _call, server, campaign["id"], config.home,
            manifest={
                "id": PACK, "version": "1.0.0", "title": "Starting awards",
                "namespace": PACK, "system_id": "dnd5e", "editions": ["2014"],
                "capabilities": [],
            },
            artifacts=_equipment_artifacts(), mechanics=[],
            expected_revision=campaign["revision"], request_key="fixture",
        )
        for mode, background_first in (
            ("equipment", False), ("equipment", True), ("gold", False), ("gold", True),
        ):
            key = f"{mode}-{background_first}"
            initial = default_character_sheet()
            initial["inventory"]["wallet"]["gp"] = 17
            actor = await _call(server, "character_create_from", {
                "mode": "direct", "payload": {
                    "campaign_id": campaign["id"], "name": key, "sheet": initial,
                }, "idempotency_key": key,
            })

            async def get():
                return await _call(server, "character_query", {
                    "view": "get", "payload": {"character_id": actor["id"]},
                })

            async def apply_background():
                nonlocal actor
                actor = await _call(server, "character_content_apply", {
                    "character_id": actor["id"], "artifact_id": f"{PACK}.background.courier",
                    "selection": {}, "expected_revision": actor["revision"],
                    "idempotency_key": f"{key}-background",
                })

            if background_first:
                await apply_background()
                assert actor["sheet"]["inventory"]["wallet"]["gp"] == 27
            before_class = await get()
            campaign_before = await _call(server, "campaign_query", {
                "view": "get", "payload": {"campaign_id": campaign["id"]},
            })
            # Missing and malformed choices cannot materialize the class or consume wealth.
            base_arguments = {
                "character_id": actor["id"], "artifact_id": f"{PACK}.class.fighter",
                "expected_revision": actor["revision"],
                "selection": {"skills": ["athletics", "perception"]},
            }
            pending = await _call(server, "character_content_apply", {
                **base_arguments, "idempotency_key": f"{key}-missing",
            })
            assert pending["status"] == "pending_choice"
            assert await get() == before_class
            for index, invalid in enumerate((
                {"mode": "gold", "amount": 200},
                {"mode": "gold", "choices": {}},
                {"mode": "equipment", "choices": {"weapons": [], "armor": []}},
                {"mode": "equipment", "choices": {
                    "weapons": [f"{PACK}.item.club", "not-an-active-item"],
                    "armor": [f"{PACK}.item.light-armor"],
                }},
            )):
                with pytest.raises(Exception, match="starting equipment"):
                    await _call(server, "character_content_apply", {
                        **base_arguments, "selection": {
                            **base_arguments["selection"], "starting_equipment": invalid,
                        }, "idempotency_key": f"{key}-invalid-{index}",
                    })
                assert await get() == before_class
            chosen = {"mode": " GOLD "} if mode == "gold" else {
                "mode": "equipment", "choices": {
                    "weapons": [f"{PACK}.item.club", f"{PACK}.item.club"],
                    "armor": [f"{PACK}.item.{'medium' if background_first else 'light'}-armor"],
                },
            }
            arguments = {
                **base_arguments, "selection": {
                    **base_arguments["selection"], "starting_equipment": chosen,
                }, "idempotency_key": f"{key}-class",
            }
            with pytest.raises(Exception, match="revision|conflict"):
                await _call(server, "character_content_apply", {
                    **arguments, "expected_revision": actor["revision"] + 10,
                    "idempotency_key": f"{key}-stale",
                })
            assert await get() == before_class
            if mode == "gold":
                original_replace = RandomStateMutationService.replace
                attempts = []

                def stale_after_roll(service, campaign_id, **kwargs):
                    attempts.append(True)
                    kwargs["character_updates"] = [
                        replace(update, expected_revision=update.expected_revision + 1)
                        for update in kwargs["character_updates"]
                    ]
                    return original_replace(service, campaign_id, **kwargs)

                with monkeypatch.context() as patch:
                    patch.setattr(RandomStateMutationService, "replace", stale_after_roll)
                    with pytest.raises(Exception, match="revision conflict"):
                        await _call(server, "character_content_apply", arguments)
                assert attempts == [True]
                assert await get() == before_class
                assert await _call(server, "campaign_query", {
                    "view": "get", "payload": {"campaign_id": campaign["id"]},
                }) == campaign_before
            applied = await _call(server, "character_content_apply", arguments)
            actor = applied
            assert await _call(server, "character_content_apply", arguments) == applied
            campaign_after = await _call(server, "campaign_query", {
                "view": "get", "payload": {"campaign_id": campaign["id"]},
            })
            random_before = campaign_before["state"]["random_stream"]["position"]
            random_after = campaign_after["state"]["random_stream"]["position"]
            assert random_after - random_before == (5 if mode == "gold" else 0)
            result = applied["class_materialization"]["starting_equipment"]
            assert result["selection"]["mode"] == mode
            if not background_first:
                if mode == "gold":
                    with pytest.raises(Exception, match="cannot stack background equipment"):
                        await _call(server, "character_content_apply", {
                            "character_id": actor["id"],
                            "artifact_id": f"{PACK}.background.courier",
                            "selection": {"equipment_mode": "source"},
                            "expected_revision": actor["revision"],
                            "idempotency_key": f"{key}-reject-stacking",
                        })
                await apply_background()
            final = await get()
            inventory = final["sheet"]["inventory"]
            assert all(not item["equipped"] for item in inventory["items"])
            assert all(value is None for value in inventory["equipment_slots"].values())
            if mode == "gold":
                assert applied["random_stream_receipt"]["draw_count"] == 5
                assert applied["random_stream_receipt"]["position_before"] == random_before
                assert applied["random_stream_receipt"]["position_after"] == random_after
                assert inventory["items"] == []
                assert result["item_ids"] == []
                assert 50 <= result["wallet"]["gp"] <= 200
                assert result["wallet"]["gp"] == result["roll"]["total"] * 10
                assert inventory["wallet"]["gp"] == 17 + result["wallet"]["gp"]
                grants = final["sheet"]["progression"]["background_grants"]
                assert grants["equipment_item_ids"] == []
                assert grants["choices"]["equipment_mode"] == "class_starting_gold"
                assert grants["choices"]["starting_equipment_award"] == {"items": [], "wallet": {}}
            else:
                assert inventory["wallet"]["gp"] == 27
                names = Counter(item["name"] for item in inventory["items"])
                assert names == Counter({
                    "bolts": 1, "club": 2, "Courier Badge": 1, "Crossbow, light": 1,
                    f"{'medium' if background_first else 'light'}-armor": 1,
                })
                assert next(item for item in inventory["items"] if item["name"] == "bolts")[
                    "quantity"
                ] == 20
                assert len(result["item_ids"]) == len(set(result["item_ids"])) == 5
                assert result["roll"] is None
                for item in inventory["items"]:
                    if item["id"] in result["item_ids"]:
                        assert item["source_key"] == f"{PACK}@1.0.0:{PACK}.class.fighter"
            for tamper in ("remove", "forge"):
                replacement = deepcopy(final["sheet"])
                record = next(item for item in replacement["content"]["selections"]
                              if item["kind"] == "class")
                if tamper == "remove":
                    record["selection"] = {"skills": ["athletics", "perception"], "tools": []}
                else:
                    record["selection"]["starting_equipment_result"][
                        "replaces_background_equipment"
                    ] = not (mode == "gold")
                with pytest.raises(Exception, match="starting-equipment authority"):
                    await _call(server, "character_sheet_replace", {
                        "character_id": final["id"], "sheet": replacement,
                        "expected_revision": final["revision"],
                        "idempotency_key": f"{key}-{tamper}",
                    })
                assert await get() == final
            if mode == "equipment":
                # The immutable award receipt must not freeze ordinary custody/use.
                bolt_id = next(item["id"] for item in inventory["items"]
                               if item["name"] == "bolts")
                await _call(server, "inventory_change", {
                    "owner": "character", "owner_id": final["id"], "action": "remove",
                    "payload": {"item_id": bolt_id, "quantity": 1},
                    "expected_revision": final["revision"],
                    "idempotency_key": f"{key}-use-bolt",
                })
                final = await get()
                assert next(item for item in final["sheet"]["inventory"]["items"]
                            if item["id"] == bolt_id)["quantity"] == 19
                record = next(item for item in final["sheet"]["content"]["selections"]
                              if item["kind"] == "class")
                assert record["selection"]["starting_equipment_result"] == result
            saved.append((arguments, applied, final))

    server = create_server(config)
    try:
        asyncio.run(exercise(server))
    finally:
        close_server(server)
    restarted = create_server(config)
    try:
        for arguments, applied, final in saved:
            assert asyncio.run(_call(restarted, "character_content_apply", arguments)) == applied
            assert asyncio.run(_call(restarted, "character_query", {
                "view": "get", "payload": {"character_id": final["id"]},
            })) == final
    finally:
        close_server(restarted)
