from __future__ import annotations

import json
from pathlib import Path

from sagasmith_dnd.cli import main


def _call(capsys, *args: str) -> dict:
    code = main([*args, "--json"])
    output = capsys.readouterr()
    assert output.err == ""
    value = json.loads(output.out)
    assert code == 0, value
    assert value["ok"] is True
    return value["data"]


def test_character_based_2014_checks(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'dnd.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Checks", "--edition", "2014")["campaign"]
    character = _call(
        capsys,
        "character",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Mira",
        "--sheet",
        json.dumps(
            {
                "level": 5,
                "abilities": {"wisdom": 16, "dexterity": 14},
                "proficiencies": ["skill:perception", "save:dexterity"],
                "expertise": ["perception"],
            }
        ),
    )

    perception = _call(
        capsys,
        "check",
        "skill",
        "--character",
        character["id"],
        "--skill",
        "perception",
        "--dc",
        "10",
    )
    assert perception["ruleset"] == "5e-2014"
    assert perception["ability"] == "wisdom"
    assert perception["expertise"] is True
    assert perception["breakdown"]["ability_modifier"] == 3
    assert perception["breakdown"]["proficiency_bonus"] == 6

    initiative = _call(
        capsys,
        "check",
        "initiative",
        "--character",
        character["id"],
    )
    assert initiative["subject"] == "initiative"
    assert initiative["ability"] == "dexterity"
    assert initiative["dc"] == 0
