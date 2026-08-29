from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
from typing import Any, Callable
from xml.etree import ElementTree

from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import create_server

CAMPAIGNS = (
    (
        "Amber Observatory",
        "Wind-scoured observatory above salt cliffs, where a sealed lens tracks old stars.",
        (
            ("Aria", "pc", "A patient cartographer."),
            ("Borin", "npc", "A veteran guide."),
            ("Lumen", "npc", "A lantern keeper."),
            ("Nyx", "npc", "Keeper of the sealed lens."),
            ("Glass Drake", "monster", "A crystal-scaled sentinel."),
        ),
    ),
    (
        "Bronze Citadel",
        "Bronze walls guard a river archive and its forgotten gatehouse.",
        (
            ("Zephyr", "pc", "A storm-touched envoy."),
            ("Mira", "npc", "The citadel archivist."),
            ("Ash Wyrm", "monster", "A creature beneath the gate."),
        ),
    ),
    (
        "Cobalt Labyrinth",
        "Flooded passages connect a moonlit market to the lower vaults.",
        (
            ("Cato", "pc", "A cautious delver."),
            ("Dena", "pc", "A mapmaker who reads currents."),
            ("Edda", "npc", "A market guide."),
            ("Fenn", "npc", "A keeper of flood charts."),
            ("Gale", "npc", "A broker for the moonlit market."),
            ("Gloom Ooze", "monster", "A thing in the cistern."),
            ("Harrow Beast", "monster", "A hunter in the lower vault."),
        ),
    ),
    (
        "Ivory Archive",
        "Silent stacks preserve crown treaties and testimony from vanished courts.",
        (
            ("Iona", "pc", "A treaty scholar."),
            ("Jori", "npc", "A meticulous indexer."),
            ("Kestrel", "npc", "A courier of sealed records."),
            ("Morrow", "npc", "A retired court witness."),
            ("Nacre", "npc", "The archive conservator."),
        ),
    ),
    (
        "Jade Harbor",
        "Tidal bells guide ships through green fog toward a sheltered quay.",
        (
            ("Orin", "pc", "A patient navigator."),
            ("Petra", "pc", "A harbor scout."),
            ("Quill", "npc", "The keeper of tidal bells."),
            ("Rhea", "npc", "A quay registrar."),
        ),
    ),
    (
        "Violet March",
        "A caravan road crosses fields of violet ash beneath an empty watchtower.",
        (
            ("Sable", "pc", "A caravan outrider."),
            ("Taro", "npc", "The last watchtower keeper."),
            ("Umbra", "monster", "An ash-shadow predator."),
            ("Ulan", "npc", "A caravan quartermaster."),
            ("Vale", "monster", "A burrowing road hunter."),
            ("Wren", "monster", "A winged scavenger."),
        ),
    ),
)


async def _raw(server, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    _, structured = await server.call_tool(name, arguments or {})
    assert isinstance(structured, dict)
    return structured


async def _call(server, name: str, arguments: dict[str, Any] | None = None) -> Any:
    structured = await _raw(server, name, arguments)
    return structured.get("result", structured)


async def _fixture(server) -> None:
    campaigns: dict[str, dict[str, Any]] = {}
    for name, description, _ in CAMPAIGNS:
        campaigns[name] = await _call(
            server,
            "campaign_create",
            {
                "name": name,
                "description": description,
                "idempotency_key": f"evaluation:{name}",
            },
        )
    for campaign_name, _, roster in CAMPAIGNS:
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


async def _paged(
    server,
    name: str,
    arguments: dict[str, Any],
    stats: Counter[str],
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        request = {**arguments, "limit": 2}
        if cursor is not None:
            request["cursor"] = cursor
            stats["continuations"] += 1
        structured = await _raw(server, name, request)
        stats["calls"] += 1
        page_values = structured.get("result")
        page = structured.get("page")
        assert isinstance(page_values, list)
        assert isinstance(page, dict)
        assert page["returned"] == len(page_values) <= 2
        assert structured.get("next_cursor") == page.get("next_cursor")
        values.extend(page_values)
        cursor = page.get("next_cursor")
        if cursor is None:
            assert page["has_more"] is False
            assert page["total_count"] == len(values)
            return values
        assert page["has_more"] is True


async def _explore_fixture(server) -> tuple[dict[str, Any], Counter[str]]:
    """Perform a fresh, paginated, multi-hop exploration for one QA pair."""

    stats: Counter[str] = Counter()
    campaigns = await _paged(server, "campaign_query", {"view": "list"}, stats)
    rosters: dict[str, list[dict[str, Any]]] = {}
    details: list[dict[str, Any]] = []
    for campaign in campaigns:
        roster = await _paged(
            server,
            "character_query",
            {"view": "list", "payload": {"campaign_id": campaign["id"]}},
            stats,
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
            stats["calls"] += 1
    systems = await _call(server, "system_list")
    stats["calls"] += 1
    return {
        "campaigns": campaigns,
        "campaign_by_id": {item["id"]: item for item in campaigns},
        "rosters": rosters,
        "details": details,
        "systems": systems,
    }, stats


def _role_counts(data: dict[str, Any], campaign: dict[str, Any]) -> Counter[str]:
    return Counter(
        actor["character_type"] for actor in data["rosters"][campaign["id"]]
    )


def _answer_largest_npc_roster(data: dict[str, Any]) -> str:
    return max(
        data["campaigns"],
        key=lambda campaign: (_role_counts(data, campaign)["npc"], campaign["name"]),
    )["name"]


def _answer_largest_monster_roster(data: dict[str, Any]) -> str:
    return max(
        data["campaigns"],
        key=lambda campaign: (_role_counts(data, campaign)["monster"], campaign["name"]),
    )["name"]


def _answer_two_pc_largest_roster(data: dict[str, Any]) -> str:
    candidates = [
        campaign
        for campaign in data["campaigns"]
        if _role_counts(data, campaign)["pc"] == 2
    ]
    return max(candidates, key=lambda item: len(data["rosters"][item["id"]]))["name"]


def _answer_last_pc(data: dict[str, Any]) -> str:
    return max(
        actor["name"]
        for roster in data["rosters"].values()
        for actor in roster
        if actor["character_type"] == "pc"
    )


def _answer_lens_campaign_slug(data: dict[str, Any]) -> str:
    keeper = next(
        actor for actor in data["details"] if "sealed lens" in actor["summary"].casefold()
    )
    return data["campaign_by_id"][keeper["campaign_id"]]["slug"]


def _answer_tidal_first_npc(data: dict[str, Any]) -> str:
    campaign = next(
        item for item in data["campaigns"] if "tidal bells" in item["description"].casefold()
    )
    return min(
        actor["name"]
        for actor in data["rosters"][campaign["id"]]
        if actor["character_type"] == "npc"
    )


def _answer_no_monster_ratio(data: dict[str, Any]) -> str:
    candidates = [
        campaign
        for campaign in data["campaigns"]
        if _role_counts(data, campaign)["monster"] == 0
    ]
    return max(
        candidates,
        key=lambda campaign: (
            _role_counts(data, campaign)["npc"] / _role_counts(data, campaign)["pc"],
            campaign["name"],
        ),
    )["name"]


def _answer_largest_roster_classifications(data: dict[str, Any]) -> str:
    campaign = max(
        data["campaigns"], key=lambda item: len(data["rosters"][item["id"]])
    )
    return str(len(_role_counts(data, campaign)))


def _answer_outer_campaign_system(data: dict[str, Any]) -> str:
    campaigns = sorted(data["campaigns"], key=lambda item: item["name"])
    assert campaigns[0]["system_id"] == campaigns[-1]["system_id"]
    displays = {item["id"]: item["display_name"] for item in data["systems"]}
    return displays[campaigns[0]["system_id"]]


def _answer_monsters_equal_others(data: dict[str, Any]) -> str:
    campaign = next(
        item
        for item in data["campaigns"]
        if _role_counts(data, item)["monster"]
        == _role_counts(data, item)["pc"] + _role_counts(data, item)["npc"]
    )
    return campaign["slug"]


SOLVERS: tuple[Callable[[dict[str, Any]], str], ...] = (
    _answer_largest_npc_roster,
    _answer_largest_monster_roster,
    _answer_two_pc_largest_roster,
    _answer_last_pc,
    _answer_lens_campaign_slug,
    _answer_tidal_first_npc,
    _answer_no_monster_ratio,
    _answer_largest_roster_classifications,
    _answer_outer_campaign_system,
    _answer_monsters_equal_others,
)


def test_builder_evaluations_are_independent_read_only_and_actually_solved(
    tmp_path: Path,
) -> None:
    evaluation_path = Path(__file__).parents[1] / "evaluations" / "read_only.xml"
    root = ElementTree.parse(evaluation_path).getroot()
    pairs = root.findall("qa_pair")
    assert len(pairs) == len(SOLVERS) == 10
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

        observed: list[str] = []
        for solve in SOLVERS:
            # Every QA pair starts from a fresh catalog traversal. No answer or
            # intermediate result from another pair is reused.
            data, stats = await _explore_fixture(server)
            observed.append(solve(data))
            assert stats["calls"] >= 35
            assert stats["continuations"] >= 7
        assert observed == answers

    asyncio.run(exercise())
