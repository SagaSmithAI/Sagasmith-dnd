from __future__ import annotations

import json
from pathlib import Path

from sagasmith_dnd.cli import main


def _call(capsys, *args: str) -> dict:
    code = main([*args, "--json"])
    output = capsys.readouterr()
    value = json.loads(output.out)
    assert code == 0, value
    assert value["ok"] is True
    return value["data"]


def test_token_move_reports_distance_and_difficult_terrain_cost(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'map.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Map")["campaign"]
    scene = _call(
        capsys,
        "scene",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Grid",
        "--grid-size",
        "70",
        "--metadata",
        '{"grid_distance":5}',
    )
    token = _call(capsys, "token", "create", "--scene", scene["id"], "--name", "Hero")
    _call(
        capsys,
        "region",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Web",
        "--behavior",
        "difficult_terrain",
        "--shape",
        '{"type":"circle","x":70,"y":0,"radius":10}',
    )

    moved = _call(
        capsys,
        "token",
        "move",
        "--token",
        token["id"],
        "--x",
        "70",
        "--y",
        "0",
    )

    assert moved["x"] == 70
    assert moved["movement"]["distance"] == 5
    assert moved["movement"]["cost"] == 10
    assert moved["movement"]["regions"][0]["behavior"] == "difficult_terrain"


def test_token_move_leaving_reach_creates_opportunity_attack_window(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'opportunity.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Opportunity")["campaign"]
    scene = _call(
        capsys,
        "scene",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Grid",
        "--grid-size",
        "70",
        "--metadata",
        '{"grid_distance":5}',
    )
    hero = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Hero",
        "--actor-id",
        "hero",
        "--disposition",
        "friendly",
        "--x",
        "70",
        "--y",
        "0",
    )
    _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Goblin",
        "--actor-id",
        "goblin",
        "--disposition",
        "hostile",
        "--x",
        "0",
        "--y",
        "0",
        "--metadata",
        '{"reach":5}',
    )

    moved = _call(
        capsys,
        "token",
        "move",
        "--token",
        hero["id"],
        "--x",
        "140",
        "--y",
        "0",
    )

    pending = moved["movement"]["pending"][0]
    assert pending["trigger"] == "opportunity_attack"
    assert pending["actor_id"] == "goblin"
    assert pending["target_actor_id"] == "hero"
