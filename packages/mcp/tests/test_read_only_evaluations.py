from __future__ import annotations

import asyncio
from pathlib import Path
from xml.etree import ElementTree

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server


async def _call(server, name: str, arguments: dict | None = None):
    _, structured = await server.call_tool(name, arguments or {})
    if isinstance(structured, dict):
        return structured.get("result", structured)
    return structured


async def _fixture(server) -> None:
    campaigns: dict[str, dict] = {}
    for name in ("Alpha Expedition", "Beta Citadel"):
        campaigns[name] = await _call(
            server,
            "campaign_create",
            {"name": name, "idempotency_key": f"evaluation:{name}"},
        )
    actors = {
        "Alpha Expedition": [
            ("Aria", "pc", "A patient cartographer."),
            ("Borin", "npc", "A veteran guide."),
            ("Lumen", "npc", "A lantern keeper."),
            ("Nyx", "npc", "Custodian of the sealed observatory."),
        ],
        "Beta Citadel": [
            ("Zephyr", "pc", "A storm-touched envoy."),
            ("Mira", "npc", "The citadel archivist."),
            ("Ash Wyrm", "monster", "A creature beneath the gate."),
        ],
    }
    for campaign_name, roster in actors.items():
        for name, character_type, summary in roster:
            await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaigns[campaign_name]["id"],
                        "name": name,
                        "character_type": character_type,
                        "summary": summary,
                    },
                    "idempotency_key": f"evaluation:{campaign_name}:{name}",
                },
            )


async def _solve(server) -> list[str]:
    campaigns = await _call(server, "campaign_query", {"view": "list"})
    campaigns = sorted(campaigns, key=lambda item: item["name"])
    rosters: dict[str, list[dict]] = {}
    details: list[dict] = []
    for campaign in campaigns:
        roster = await _call(
            server,
            "character_query",
            {"view": "list", "payload": {"campaign_id": campaign["id"]}},
        )
        rosters[campaign["id"]] = roster
        for actor in roster:
            details.append(
                await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": actor["id"]}},
                )
            )
    campaign_by_id = {item["id"]: item for item in campaigns}
    all_actors = [actor for roster in rosters.values() for actor in roster]
    largest = max(campaigns, key=lambda item: len(rosters[item["id"]]))
    last_pc = max(actor["name"] for actor in all_actors if actor["character_type"] == "pc")
    monster = next(actor for actor in all_actors if actor["character_type"] == "monster")
    npc_count = sum(actor["character_type"] == "npc" for actor in all_actors)
    balanced = next(
        campaign
        for campaign in campaigns
        if sum(actor["character_type"] == "pc" for actor in rosters[campaign["id"]])
        == sum(actor["character_type"] == "npc" for actor in rosters[campaign["id"]])
    )
    observatory = next(
        actor for actor in details if "sealed observatory" in actor["summary"].casefold()
    )
    beta = next(item for item in campaigns if item["name"] == "Beta Citadel")
    systems = await _call(server, "system_list")
    system_names = {item["id"]: item["display_name"] for item in systems}
    return [
        largest["name"],
        last_pc,
        campaign_by_id[monster["campaign_id"]]["name"],
        str(npc_count),
        str(len(all_actors)),
        balanced["name"],
        observatory["name"],
        str(len({actor["character_type"] for actor in rosters[beta["id"]]})),
        system_names[campaigns[0]["system_id"]],
        campaigns[0]["slug"],
    ]


def test_builder_evaluations_are_independent_read_only_and_actually_solved(
    tmp_path: Path,
) -> None:
    evaluation_path = Path(__file__).parents[1] / "evaluations" / "read_only.xml"
    root = ElementTree.parse(evaluation_path).getroot()
    pairs = root.findall("qa_pair")
    assert len(pairs) >= 10
    questions = [str(pair.findtext("question") or "").strip() for pair in pairs]
    answers = [str(pair.findtext("answer") or "").strip() for pair in pairs]
    assert len(questions) == len(set(questions))
    assert all(questions) and all(answers)

    async def exercise() -> None:
        server = create_server(
            McpConfig(
                home=tmp_path / "home",
                database_url=None,
                chroma_url=None,
                chroma_path_override=None,
                dnd_skills_dir=tmp_path / "dnd",
                modulegen_skills_dir=tmp_path / "modulegen",
                auto_seed_rules=False,
            )
        )
        await _fixture(server)
        catalog = {tool.name: tool for tool in await server.list_tools()}
        for tool_name in ("campaign_query", "character_query", "system_list"):
            annotations = catalog[tool_name].annotations
            assert annotations is not None
            assert annotations.read_only_hint is True
            assert annotations.idempotent_hint is True
        assert await _solve(server) == answers

    asyncio.run(exercise())
