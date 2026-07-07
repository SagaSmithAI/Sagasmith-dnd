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


def test_actor_document_cli_create_list_show(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'actors.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Actors")["campaign"]

    actor = _call(
        capsys,
        "actor",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Mira",
        "--type",
        "character",
        "--payload",
        '{"level":5}',
    )

    assert actor["name"] == "Mira"
    listed = _call(capsys, "actor", "list", "--campaign", campaign["id"])
    assert listed["actors"][0]["id"] == actor["id"]
    shown = _call(capsys, "actor", "show", "--id", actor["id"])
    assert shown["system"]["level"] == 5
