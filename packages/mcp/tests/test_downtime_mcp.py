from __future__ import annotations

import asyncio
from pathlib import Path

from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server

EXPENSES_REF = "bundled:srd2014/04_Equipment/Expenses.md"
LIFESTYLE_EXCERPT = (
    "At the start of each week or month (your choice), choose a lifestyle from the "
    "Expenses table and pay the price to sustain that lifestyle."
)
ADVENTURING_REF = "bundled:srd2014/06_Gameplay/Adventuring.md"
CRAFTING_EXCERPT = (
    "Each character contributes 5 gp worth of effort for every day spent helping to craft the item."
)


def _config(path: Path) -> McpConfig:
    return McpConfig(
        home=path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=path / "dnd",
        modulegen_skills_dir=path / "modulegen",
        auto_seed_rules=False,
    )


async def _response(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result


async def _result(server, name: str, arguments: dict):
    response = await _response(server, name, arguments)
    return response.get("result", response)


def test_downtime_lifestyle_is_source_bound_atomic_and_replays_after_restart(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _result(
                server,
                "campaign_create",
                {
                    "name": "Downtime transaction",
                    "edition": "2014",
                    "idempotency_key": "downtime-campaign",
                },
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["inventory"]["wallet"]["gp"] = 10
            actor = await _result(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Downtime tester",
                        "sheet": sheet,
                    },
                    "idempotency_key": "downtime-actor",
                },
            )
            current = await _result(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            actor_state = await _result(
                server, "character_query", {"view": "get", "payload": {"character_id": actor["id"]}}
            )
            base = {
                "campaign_id": campaign["id"],
                "actor_id": actor["id"],
                "activity": "lifestyle",
                "payload": {
                    "source_ref": EXPENSES_REF,
                    "source_excerpt": "fabricated lifestyle text that is not in the SRD",
                    "hours": 0,
                    "days": 2,
                    "lifestyle": "modest",
                    "payment": {"gp": 2},
                },
                "expected_revision": current["revision"],
                "expected_actor_revision": actor_state["revision"],
                "idempotency_key": "downtime-rejected-source",
            }
            try:
                await _result(server, "character_downtime_settle", base)
            except Exception as error:
                assert "source excerpt" in str(error).casefold()
            else:
                raise AssertionError("unverified downtime source excerpt was accepted")
            after_reject = await _result(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_reject["revision"] == current["revision"]

            base["payload"]["source_excerpt"] = LIFESTYLE_EXCERPT
            base["idempotency_key"] = "downtime-lifestyle-save"
            response = await _response(server, "character_downtime_settle", base)
            assert response["status"] == "committed"
            assert response["rule_receipts"][0]["citations"][0]["source"] == EXPENSES_REF
            close_server(server)
            server = create_server(_config(tmp_path))
            assert await _response(server, "character_downtime_settle", base) == response
            updated = await _result(
                server, "character_query", {"view": "get", "payload": {"character_id": actor["id"]}}
            )
            assert updated["sheet"]["inventory"]["wallet"]["gp"] == 8
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_crafting_team_cas_and_real_inventory_material_consumption(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _result(
                server,
                "campaign_create",
                {"name": "Crafting team", "edition": "2014", "idempotency_key": "craft-campaign"},
            )
            actors = []
            for name in ("Smith", "Helper"):
                sheet = default_character_sheet()
                sheet["edition"] = "2014"
                sheet["traits"]["proficiencies"]["tools"] = ["Smith's Tools"]
                if name == "Smith":
                    sheet["inventory"]["items"] = [
                        {
                            "id": "stock-a",
                            "name": "Iron stock A",
                            "kind": "equipment",
                            "quantity": 1,
                            "price_cp": 250,
                            "source_key": "test:stock-a",
                        },
                        {
                            "id": "stock-b",
                            "name": "Iron stock B",
                            "kind": "equipment",
                            "quantity": 1,
                            "price_cp": 250,
                            "source_key": "test:stock-b",
                        },
                    ]
                actors.append(
                    await _result(
                        server,
                        "character_create_from",
                        {
                            "mode": "direct",
                            "payload": {
                                "campaign_id": campaign["id"],
                                "name": name,
                                "sheet": sheet,
                            },
                            "idempotency_key": f"craft-{name}",
                        },
                    )
                )
            primary, helper = actors
            campaign_state = await _result(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            primary_state = await _result(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": primary["id"]}},
            )
            helper_state = await _result(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": helper["id"]}},
            )
            request = {
                "campaign_id": campaign["id"],
                "actor_id": primary["id"],
                "activity": "crafting",
                "payload": {
                    "source_ref": ADVENTURING_REF,
                    "source_excerpt": CRAFTING_EXCERPT,
                    "hours": 8,
                    "required_tools": "Smith's Tools",
                    "market_value_remaining_gp": 10,
                    "collaborator_ids": [primary["id"], helper["id"]],
                    "collaborator_revisions": {helper["id"]: helper_state["revision"]},
                    "collaborating_in_same_place": True,
                    "facility_required": False,
                    "facility_available": True,
                    "materials_item_ids": ["stock-a"],
                    "lifestyle": "modest",
                    "payment": {},
                },
                "expected_revision": campaign_state["revision"],
                "expected_actor_revision": primary_state["revision"],
                "idempotency_key": "craft-insufficient-stock",
            }
            try:
                await _result(server, "character_downtime_settle", request)
            except Exception as error:
                assert "raw materials" in str(error).casefold()
            else:
                raise AssertionError("crafting settled without enough owned material value")
            after_reject = await _result(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_reject["revision"] == campaign_state["revision"]
            after_failed_actor = await _result(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": primary["id"]}},
            )
            assert after_failed_actor["revision"] == primary_state["revision"]

            request["payload"]["materials_item_ids"] = ["stock-a", "stock-b"]
            request["idempotency_key"] = "crafting-team-commit"
            result = await _result(server, "character_downtime_settle", request)
            assert result["progress_gp"] == 10
            assert result["collaborator_ids"] == [primary["id"], helper["id"]]
            settled = await _result(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": primary["id"]}},
            )
            assert settled["sheet"]["inventory"]["items"] == []
            settled_helper = await _result(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": helper["id"]}},
            )
            assert settled_helper["revision"] > helper_state["revision"]
        finally:
            close_server(server)

    asyncio.run(exercise())
