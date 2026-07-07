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


def test_cover_check_uses_cover_region_degree(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'cover.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Cover")["campaign"]
    scene = _call(capsys, "scene", "create", "--campaign", campaign["id"], "--name", "Hall")
    attacker = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Archer",
        "--x",
        "0",
        "--y",
        "0",
    )
    target = _call(
        capsys,
        "token",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Guard",
        "--x",
        "100",
        "--y",
        "100",
    )
    _call(
        capsys,
        "region",
        "create",
        "--scene",
        scene["id"],
        "--name",
        "Arrow Slit",
        "--behavior",
        "cover",
        "--shape",
        '{"type":"rect","x":90,"y":90,"width":30,"height":30}',
        "--metadata",
        '{"degree":"three_quarters"}',
    )

    result = _call(
        capsys,
        "cover",
        "check",
        "--scene",
        scene["id"],
        "--token",
        attacker["id"],
        "--target-id",
        target["id"],
    )

    assert result["cover"]["degree"] == "three_quarters"
    assert result["cover"]["ac_bonus"] == 5
    assert result["cover"]["dex_save_bonus"] == 5
    assert result["targetable"] is True
