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
