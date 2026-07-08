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


def _call_error(capsys, *args: str) -> dict:
    code = main([*args, "--json"])
    output = capsys.readouterr()
    value = json.loads(output.out)
    assert code != 0, value
    assert value["ok"] is False
    return value["error"]


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
    assert shown["system"]["details"]["level"] == 5
    assert shown["system"]["abilities"]["str"]["value"] == 10
    assert shown["system"]["attributes"]["hp"]["max"] == 1
    assert shown["system"]["skills"]["perception"]["ability"] == "wis"


def test_actor_show_includes_items_activities_and_effects(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'actor-show.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Actor Show")["campaign"]
    actor = _call(capsys, "actor", "create", "--campaign", campaign["id"], "--name", "Mira")
    pack = tmp_path / "pack"
    pack.mkdir()
    (pack / "feature.yml").write_text(
        """
_id: secondWind
name: Second Wind
type: feat
system:
  activities:
    use:
      _id: use
      type: heal
      activation:
        type: bonus
""",
        encoding="utf-8",
    )
    _call(capsys, "pack", "import", "--campaign", campaign["id"], "--actor", actor["id"], "--path", str(pack))
    _call(capsys, "condition", "add", "--campaign", campaign["id"], "--actor", actor["id"], "--condition", "poisoned")

    shown = _call(capsys, "actor", "show", "--id", actor["id"])
    assert shown["items"][0]["name"] == "Second Wind"
    assert shown["items"][0]["activities"][0]["activity_type"] == "heal"
    assert shown["effects"][0]["statuses"] == ["poisoned"]


def test_actor_update_changes_system_and_flags(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'actor-update.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Actor Update")["campaign"]
    actor = _call(capsys, "actor", "create", "--campaign", campaign["id"], "--name", "Mira")

    updated = _call(
        capsys,
        "actor",
        "update",
        "--actor",
        actor["id"],
        "--payload",
        '{"level":3,"attributes":{"hp":{"value":18,"max":18}}}',
        "--metadata",
        '{"source":"manual"}',
    )

    assert updated["revision"] == actor["revision"] + 1
    shown = _call(capsys, "actor", "show", "--id", actor["id"])
    assert shown["system"]["level"] == 3
    assert shown["system"]["details"]["level"] == 3
    assert shown["system"]["abilities"]["dex"]["value"] == 10
    assert shown["flags"]["source"] == "manual"


def test_actor_document_rejects_unknown_actor_type(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'actor-invalid.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Actor Invalid")["campaign"]

    error = _call_error(
        capsys,
        "actor",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Mira",
        "--type",
        "demigod",
    )

    assert error["code"] == "invalid_value"
    assert "unknown actor type" in error["message"]
