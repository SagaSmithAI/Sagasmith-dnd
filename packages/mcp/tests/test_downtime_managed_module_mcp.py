from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from sagasmith_dnd.character_schema import default_character_sheet

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import close_server, create_server
from tests.authoring_helpers import finalize_and_activate_module

ADVENTURING_REF = "bundled:srd2014/06_Gameplay/Adventuring.md"
RESEARCH_RULE = (
    "For each day of research, you must spend 1 gp to cover your expenses. "
    "This cost is in addition to your normal lifestyle expenses."
)
TRAINING_RULE = "The training lasts for 250 days and costs 1 gp per day."
RESEARCH_CONTENT = "The sealed atlas places the lost observatory north of the river."
INSTRUCTOR_CONTENT = "Master Oren is a patient tutor who teaches the Orc language."
MODULE_CONTENT = f"""# The Observatory Archive

## Research Record

{RESEARCH_CONTENT}

The record can be found after two full days of research.

## Instructor Record

{INSTRUCTOR_CONTENT}
"""


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


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


async def _response(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result


async def _expand_source(server, campaign_id: str, query: str, required_excerpt: str):
    hits = await _call(
        server,
        "module_search",
        {"campaign_id": campaign_id, "query": query, "top_k": 10},
    )
    for hit in hits:
        expanded = await _call(server, "module_expand", {"chunk_id": hit["id"]})
        if required_excerpt.casefold() not in expanded["content"].casefold():
            continue
        source_ref = {
            "module_id": expanded["module"]["id"],
            "scene_id": expanded["scene"]["id"],
            "chunk_id": expanded["chunk_id"],
            "page_start": expanded["page_start"],
            "page_end": expanded["page_end"],
            "heading_path": expanded["heading_path"],
            "content_sha256": hashlib.sha256(expanded["content"].encode("utf-8")).hexdigest(),
        }
        return source_ref, expanded["content"]
    raise AssertionError(f"activated module has no source chunk containing {required_excerpt!r}")


async def _advance_one_day(server, campaign_id: str, key: str) -> None:
    campaign = await _call(
        server,
        "campaign_query",
        {"view": "get", "payload": {"campaign_id": campaign_id}},
    )
    elapsed = int(campaign["state"]["game_time"]["elapsed_ticks"])
    await _call(
        server,
        "campaign_change",
        {
            "campaign_id": campaign_id,
            "action": "clock_advance",
            "payload": {
                "period": "day",
                "count": 1,
                "expected_elapsed_ticks": elapsed + 24 * 600,
            },
            "expected_revision": campaign["revision"],
            "idempotency_key": key,
        },
    )


def test_managed_module_research_and_source_backed_training_settle_and_replay(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        config = _config(tmp_path)
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {
                    "name": "Managed downtime research and training",
                    "edition": "2014",
                    "idempotency_key": "campaign",
                },
            )
            started = await _call(
                server,
                "module_draft",
                {
                    "campaign_id": campaign["id"],
                    "action": "start",
                    "payload": {
                        "name": "observatory-archive.md",
                        "content": MODULE_CONTENT,
                        "source_key": "observatory-archive",
                        "title": "The Observatory Archive",
                    },
                    "idempotency_key": "module-start",
                },
            )
            activation = await finalize_and_activate_module(
                _call,
                server,
                campaign["id"],
                started,
                source_key="observatory-archive",
                title="The Observatory Archive",
                portable_id="dnd5e.module.observatory-archive-test",
                edition="2014",
                request_key="module-review",
            )
            module_id = activation["activated"]["activation"]["module_id"]
            research_ref, research_content = await _expand_source(
                server,
                campaign["id"],
                "sealed atlas lost observatory north river research record",
                RESEARCH_CONTENT,
            )
            instructor_ref, instructor_content = await _expand_source(
                server,
                campaign["id"],
                "Master Oren patient tutor teaches Orc language instructor record",
                INSTRUCTOR_CONTENT,
            )
            assert research_ref["module_id"] == module_id
            assert instructor_ref["module_id"] == module_id

            instructor = await _call(
                server,
                "character_create_from",
                {
                    "mode": "narrative_npc",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Master Oren",
                        "role": "A patient language tutor.",
                        "summary": "Teaches the Orc language, as recorded in the archive.",
                        "source_ref": instructor_ref,
                        "source_excerpt": INSTRUCTOR_CONTENT,
                    },
                    "idempotency_key": "instructor-create",
                },
            )
            instructor_id = instructor["character"]["id"]
            assert instructor["narrative_npc"]["source_ref"] == instructor_ref

            student_sheet = default_character_sheet()
            student_sheet["edition"] = "2014"
            student_sheet["inventory"]["wallet"]["gp"] = 1000
            student = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Archive student",
                        "sheet": student_sheet,
                    },
                    "idempotency_key": "student-create",
                },
            )

            research_source_ref = json.dumps(research_ref, sort_keys=True, separators=(",", ":"))
            info_source_ref = json.dumps(research_ref, sort_keys=True, separators=(",", ":"))
            research_plan = {
                "available": True,
                "required_days": 2,
                "restrictions_satisfied": True,
                "required_check_ids": [],
                "passed_check_ids": [],
                "source_ref": research_source_ref,
                "source_excerpt": RESEARCH_CONTENT,
                "information_source_ref": info_source_ref,
                "information_source_excerpt": RESEARCH_CONTENT,
            }
            research_requests = []
            for day_index in range(1):
                campaign_now = await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
                student_now = await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": student["id"]}},
                )
                request = {
                    "campaign_id": campaign["id"],
                    "actor_id": student["id"],
                    "activity": "research",
                    "payload": {
                        "source_ref": ADVENTURING_REF,
                        "source_excerpt": RESEARCH_RULE,
                        "hours": 8,
                        "lifestyle": "modest",
                        "payment": {"gp": 2},
                        "plan": research_plan,
                    },
                    "expected_revision": campaign_now["revision"],
                    "expected_actor_revision": student_now["revision"],
                    "idempotency_key": f"research-day-{day_index + 1}",
                }
                research_requests.append(request)
                if day_index == 0:
                    forged = {
                        **request,
                        "payload": {
                            **request["payload"],
                            "plan": {
                                **research_plan,
                                "source_ref": json.dumps(
                                    {**research_ref, "content_sha256": "0" * 64},
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ),
                            },
                        },
                        "idempotency_key": "research-forged-source",
                    }
                    try:
                        await _call(server, "character_downtime_settle", forged)
                    except Exception as error:
                        assert "content_sha256" in str(error)
                    else:
                        raise AssertionError("downtime accepted forged module source provenance")
                    after_forgery = await _call(
                        server,
                        "campaign_query",
                        {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                    )
                    student_after_forgery = await _call(
                        server,
                        "character_query",
                        {"view": "get", "payload": {"character_id": student["id"]}},
                    )
                    assert after_forgery["revision"] == campaign_now["revision"]
                    assert student_after_forgery["revision"] == student_now["revision"]

                research_result = await _response(server, "character_downtime_settle", request)
                assert research_result["status"] == "committed"
                details = research_result["result"]["research"]
                assert details["qualifying_days"] == day_index + 1
                assert details["complete"] is False
                assert research_result["result"]["cost_cp"] == 200
                assert (
                    research_result["rule_receipts"][0]["citations"][0]["source"] == ADVENTURING_REF
                )

            research_final_request = research_requests[-1]
            final_research_response = await _response(
                server, "character_downtime_settle", research_final_request
            )
            assert final_research_response == research_result
            assert final_research_response["result"]["research"]["qualifying_days"] == 1
            close_server(server)
            server = create_server(config)
            assert (
                await _response(server, "character_downtime_settle", research_final_request)
                == final_research_response
            )

            await _advance_one_day(server, campaign["id"], "research-day-advance")
            campaign_before_completion = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            student_before_completion = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": student["id"]}},
            )
            research_completion_request = {
                **research_final_request,
                "expected_revision": campaign_before_completion["revision"],
                "expected_actor_revision": student_before_completion["revision"],
                "idempotency_key": "research-day-2",
            }
            research_completion = await _response(
                server, "character_downtime_settle", research_completion_request
            )
            research_result = research_completion["result"]["research"]
            assert research_result["complete"] is True
            assert research_result["qualifying_days"] == 2
            assert research_result["information_source"] is not None
            close_server(server)
            server = create_server(config)
            assert (
                await _response(
                    server, "character_downtime_settle", research_completion_request
                )
                == research_completion
            )

            training_request = None
            for day_index in range(2):
                if day_index:
                    await _advance_one_day(
                        server, campaign["id"], f"training-day-advance-{day_index}"
                    )
                campaign_now = await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
                student_now = await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": student["id"]}},
                )
                training_request = {
                    "campaign_id": campaign["id"],
                    "actor_id": student["id"],
                    "activity": "training",
                    "payload": {
                        "source_ref": ADVENTURING_REF,
                        "source_excerpt": TRAINING_RULE,
                        "hours": 8,
                        "lifestyle": "modest",
                        "payment": {"gp": 2},
                        "plan": {
                            "instructor_id": instructor_id,
                            "instructor_willing": True,
                            "target_kind": "language",
                            "target_id": "Orc",
                            "required_check_ids": [],
                            "passed_check_ids": [],
                        },
                    },
                    "expected_revision": campaign_now["revision"],
                    "expected_actor_revision": student_now["revision"],
                    "idempotency_key": f"training-day-{day_index + 1}",
                }
                training_response = await _response(
                    server, "character_downtime_settle", training_request
                )
                training = training_response["result"]["training"]
                assert training["qualifying_days"] == day_index + 1
                assert training_response["result"]["cost_cp"] == 200
                assert training["complete"] is False

            assert training_request is not None
            assert training["target"] == {"kind": "language", "id": "Orc"}
            assert training["instructor_id"] == instructor_id
            training_final_response = await _response(
                server, "character_downtime_settle", training_request
            )
            close_server(server)
            server = create_server(config)
            assert (
                await _response(server, "character_downtime_settle", training_request)
                == training_final_response
            )

            student_after = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": student["id"]}},
            )
            assert "Orc" not in student_after["sheet"]["traits"]["languages"]
            assert student_after["sheet"]["inventory"]["wallet"]["gp"] == 992
        finally:
            close_server(server)

    asyncio.run(exercise())
