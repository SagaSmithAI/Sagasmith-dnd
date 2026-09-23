from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.diseases import DISEASE_SOURCE_REF, infection_state
from sagasmith_dnd.poisons import POISON_SOURCE_REF

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
RECUPERATION_EXCERPT = (
    "After three days of downtime spent recuperating, you can make a DC 15 "
    "Constitution saving throw."
)
PROFESSION_EXCERPT = (
    "If you spend your time between adventures practicing a profession, you can "
    "eke out the equivalent of a poor lifestyle."
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
            base["payload"]["payment"] = {}
            base["idempotency_key"] = "downtime-insufficient-funds"
            try:
                await _result(server, "character_downtime_settle", base)
            except Exception as error:
                assert "got 0 cp" in str(error).casefold()
            else:
                raise AssertionError("lifestyle settled without its exact payment")
            after_funds_reject = await _result(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_funds_reject["revision"] == current["revision"]

            base["payload"]["payment"] = {"gp": 2}
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
                        {
                            "id": "stock-c",
                            "name": "Iron stock C",
                            "kind": "equipment",
                            "quantity": 1,
                            "price_cp": 250,
                            "source_key": "test:stock-c",
                        },
                        {
                            "id": "stock-d",
                            "name": "Iron stock D",
                            "kind": "equipment",
                            "quantity": 1,
                            "price_cp": 250,
                            "source_key": "test:stock-d",
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
                    "market_value_remaining_gp": 20,
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

            unavailable_facility = {
                **request,
                "payload": {
                    **request["payload"],
                    "facility_required": True,
                    "facility_available": False,
                },
                "idempotency_key": "craft-unavailable-facility",
            }
            try:
                await _result(server, "character_downtime_settle", unavailable_facility)
            except Exception as error:
                assert "facility" in str(error).casefold()
            else:
                raise AssertionError("crafting proceeded without its required facility")
            after_facility_reject = await _result(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_facility_reject["revision"] == campaign_state["revision"]

            unproficient_tools = {
                **request,
                "payload": {**request["payload"], "required_tools": "Weaver's Tools"},
                "idempotency_key": "craft-unproficient-tools",
            }
            try:
                await _result(server, "character_downtime_settle", unproficient_tools)
            except Exception as error:
                assert "proficiency" in str(error).casefold()
            else:
                raise AssertionError("crafting proceeded without required tool proficiency")
            after_proficiency_reject = await _result(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after_proficiency_reject["revision"] == campaign_state["revision"]

            request["payload"]["materials_item_ids"] = ["stock-a", "stock-b"]
            request["idempotency_key"] = "crafting-team-commit"
            result = await _result(server, "character_downtime_settle", request)
            assert result["progress_gp"] == 10
            assert result["market_value_remaining_gp"] == 10
            assert result["collaborator_ids"] == [primary["id"], helper["id"]]
            settled = await _result(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": primary["id"]}},
            )
            assert [item["id"] for item in settled["sheet"]["inventory"]["items"]] == [
                "stock-c",
                "stock-d",
            ]
            settled_helper = await _result(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": helper["id"]}},
            )
            assert settled_helper["revision"] > helper_state["revision"]

            campaign_day = await _result(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            elapsed = int(campaign_day["state"]["game_time"]["elapsed_ticks"])
            await _result(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "clock_advance",
                    "payload": {
                        "period": "day",
                        "count": 1,
                        "expected_elapsed_ticks": elapsed + 24 * 600,
                    },
                    "expected_revision": campaign_day["revision"],
                    "idempotency_key": "craft-day-two-advance",
                },
            )
            campaign_day_two = await _result(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            primary_day_two = await _result(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": primary["id"]}},
            )
            helper_day_two = await _result(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": helper["id"]}},
            )
            next_day = {
                **request,
                "payload": {
                    **request["payload"],
                    "market_value_remaining_gp": 10,
                    "materials_item_ids": ["stock-c", "stock-d"],
                    "collaborator_revisions": {helper["id"]: helper_day_two["revision"]},
                    "payment": {},
                },
                "expected_revision": campaign_day_two["revision"],
                "expected_actor_revision": primary_day_two["revision"],
                "idempotency_key": "crafting-team-day-two",
            }
            reset_value = {
                **next_day,
                "payload": {**next_day["payload"], "market_value_remaining_gp": 20},
                "idempotency_key": "crafting-team-reset-value",
            }
            try:
                await _result(server, "character_downtime_settle", reset_value)
            except Exception as error:
                assert "cannot be reset" in str(error)
            else:
                raise AssertionError("crafting accepted a reset market value")
            still_current = await _result(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert still_current["revision"] == campaign_day_two["revision"]
            second_day = await _result(server, "character_downtime_settle", next_day)
            assert second_day["progress_gp"] == 10
            assert second_day["market_value_remaining_gp"] == 0
        finally:
            close_server(server)

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("constitution_score", "expected_success", "choice_kind"),
    [
        (10, False, "effect"),
        (30, True, "effect"),
        (30, True, "condition"),
        (30, True, "poison"),
    ],
)
def test_recuperation_resolves_save_and_applies_choice_only_on_success(
    tmp_path: Path, constitution_score: int, expected_success: bool, choice_kind: str
) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _result(
                server,
                "campaign_create",
                {
                    "name": "Recuperation failed save",
                    "edition": "2014",
                    "random_seed": "downtime-fail-7",
                    "idempotency_key": "recuperation-campaign",
                },
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["abilities"]["constitution"]["score"] = constitution_score
            sheet["effects"].append(
                {
                    "id": "blocks-hp-recovery",
                    "name": "Healing ward",
                    "kind": "manual",
                    "source": "test:healing-ward",
                    "active": True,
                    "duration": {"period": "manual", "remaining": 0},
                    "changes": [],
                    "metadata": {"prevents_hp_recovery": True},
                }
            )
            if choice_kind == "condition":
                disease_state = infection_state(
                    "cackle_fever",
                    actor_id="pending",
                    elapsed_ticks=0,
                    save_succeeded=False,
                    incubation_roll=1,
                )
                disease_state["actor_id"] = "pending"
                sheet["effects"].append(
                    {
                        "id": "active-cackle-disease",
                        "name": "Cackle Fever",
                        "kind": "disease_state",
                        "source": DISEASE_SOURCE_REF,
                        "active": True,
                        "duration": {"period": "manual", "remaining": 0},
                        "changes": [],
                        "metadata": {"disease_state": disease_state},
                    }
                )
            elif choice_kind == "poison":
                sheet["effects"].append(
                    {
                        "id": "active-poison",
                        "name": "Sample poison",
                        "kind": "poison",
                        "source": POISON_SOURCE_REF,
                        "active": True,
                        "duration": {"period": "manual", "remaining": 0},
                        "changes": [],
                        "metadata": {
                            "poison_state": {
                                "source_ref": POISON_SOURCE_REF,
                                "active": True,
                            }
                        },
                    }
                )
            actor = await _result(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Recuperating character",
                        "sheet": sheet,
                    },
                    "idempotency_key": "recuperation-actor",
                },
            )
            result = None
            random_receipt = None
            for day_index in range(3):
                if day_index:
                    before_advance = await _result(
                        server,
                        "campaign_query",
                        {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                    )
                    elapsed = int(before_advance["state"]["game_time"]["elapsed_ticks"])
                    await _result(
                        server,
                        "campaign_change",
                        {
                            "campaign_id": campaign["id"],
                            "action": "clock_advance",
                            "payload": {
                                "period": "day",
                                "count": 1,
                                "expected_elapsed_ticks": elapsed + 24 * 600,
                            },
                            "expected_revision": before_advance["revision"],
                            "idempotency_key": f"recuperation-advance-{day_index}",
                        },
                    )
                current_campaign = await _result(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
                current_actor = await _result(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actor["id"]}},
                )
                choice_payload = (
                    {
                        "choice": "advantage_against_condition",
                        "condition_id": (
                            "active-cackle-disease"
                            if choice_kind == "condition"
                            else "active-poison"
                        ),
                        "condition_kind": "disease" if choice_kind == "condition" else "poison",
                    }
                    if choice_kind in {"condition", "poison"}
                    else {
                        "choice": "end_hp_recovery_effect",
                        "effect_id": "blocks-hp-recovery",
                    }
                )
                response = await _response(
                    server,
                    "character_downtime_settle",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": actor["id"],
                        "activity": "recuperating",
                        "payload": {
                            "source_ref": ADVENTURING_REF,
                            "source_excerpt": RECUPERATION_EXCERPT,
                            "hours": 8,
                            **choice_payload,
                            "payment": {},
                        },
                        "expected_revision": current_campaign["revision"],
                        "expected_actor_revision": current_actor["revision"],
                        "idempotency_key": f"recuperation-day-{day_index + 1}",
                    },
                )
                result = response["result"]
                random_receipt = response.get("random_stream_receipt")

            assert result["recuperation"]["success"] is expected_success
            if expected_success:
                assert result["recuperation"]["choice"]["kind"] == (
                    "advantage_against_condition"
                    if choice_kind in {"condition", "poison"}
                    else "end_hp_recovery_effect"
                )
            else:
                assert result["recuperation"]["choice"] is None
            assert result["save"]["natural"] == 6
            assert random_receipt is not None, sorted(response)
            assert random_receipt["draw_count"] == 1
            actor_after = await _result(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            effect = next(
                item
                for item in actor_after["sheet"]["effects"]
                if item["id"] == "blocks-hp-recovery"
            )
            assert effect["active"] is (
                not expected_success or choice_kind in {"condition", "poison"}
            )
            if choice_kind in {"condition", "poison"} and expected_success:
                marker = next(
                    item
                    for item in actor_after["sheet"]["effects"]
                    if item.get("metadata", {}).get("recuperation_advantage_against")
                )
                assert marker["duration"] == {"period": "hour", "remaining": 24}
                assert marker["metadata"]["recuperation_advantage_against"] == {
                    "kind": "advantage_against_condition",
                    "condition_id": (
                        "active-cackle-disease"
                        if choice_kind == "condition"
                        else "active-poison"
                    ),
                    "condition_kind": "disease" if choice_kind == "condition" else "poison",
                    "duration_hours": 24,
                }
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_profession_records_support_tier_without_minting_currency(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _result(
                server,
                "campaign_create",
                {
                    "name": "Profession downtime",
                    "edition": "2014",
                    "idempotency_key": "profession-campaign",
                },
            )
            sheet = default_character_sheet()
            sheet["edition"] = "2014"
            sheet["inventory"]["wallet"]["gp"] = 5
            actor = await _result(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Profession worker",
                        "sheet": sheet,
                    },
                    "idempotency_key": "profession-actor",
                },
            )
            current_campaign = await _result(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            current_actor = await _result(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            response = await _response(
                server,
                "character_downtime_settle",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": actor["id"],
                    "activity": "profession",
                    "payload": {
                        "source_ref": EXPENSES_REF,
                        "source_excerpt": PROFESSION_EXCERPT,
                        "hours": 8,
                        "organization_employment": True,
                        "performance_proficient": False,
                        "performance_used": False,
                        "payment": {},
                    },
                    "expected_revision": current_campaign["revision"],
                    "expected_actor_revision": current_actor["revision"],
                    "idempotency_key": "profession-day-one",
                },
            )
            assert response["result"]["supported_lifestyle"] == "comfortable"
            assert response["result"]["cost_cp"] == 0
            assert response["rule_receipts"][0]["citations"][0]["source"] == EXPENSES_REF
            updated_actor = await _result(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            assert updated_actor["sheet"]["inventory"]["wallet"]["gp"] == 5
        finally:
            close_server(server)

    asyncio.run(exercise())


def test_2024_campaign_downtime_remains_unsupported_without_writes(tmp_path: Path) -> None:
    async def exercise() -> None:
        server = create_server(_config(tmp_path))
        try:
            campaign = await _result(
                server,
                "campaign_create",
                {
                    "name": "2024 downtime boundary",
                    "edition": "2024",
                    "idempotency_key": "2024-downtime-campaign",
                },
            )
            actor = await _result(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "2024 downtime character",
                        "sheet": default_character_sheet(),
                    },
                    "idempotency_key": "2024-downtime-actor",
                },
            )
            current_campaign = await _result(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            current_actor = await _result(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": actor["id"]}},
            )
            try:
                await _result(
                    server,
                    "character_downtime_settle",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": actor["id"],
                        "activity": "lifestyle",
                        "payload": {
                            "source_ref": EXPENSES_REF,
                            "source_excerpt": LIFESTYLE_EXCERPT,
                            "hours": 0,
                            "days": 1,
                            "lifestyle": "modest",
                            "payment": {"gp": 1},
                        },
                        "expected_revision": current_campaign["revision"],
                        "expected_actor_revision": current_actor["revision"],
                        "idempotency_key": "2024-downtime-reject",
                    },
                )
            except Exception as error:
                assert "require a 2014" in str(error)
            else:
                raise AssertionError("2014 downtime settlement altered a 2024 campaign")
            after = await _result(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert after["revision"] == current_campaign["revision"]
        finally:
            close_server(server)

    asyncio.run(exercise())
