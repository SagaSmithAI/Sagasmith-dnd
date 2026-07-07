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


def test_structured_combat_flow(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'dnd.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Arena")["campaign"]
    campaign_id = campaign["id"]

    started = _call(
        capsys,
        "combat",
        "start",
        "--campaign",
        campaign_id,
        "--name",
        "Goblin Ambush",
        "--participants",
        json.dumps(
            [
                {"id": "hero", "name": "Hero", "ac": 12, "hp": 10, "max_hp": 10, "initiative": 15},
                {"id": "goblin", "name": "Goblin", "ac": 10, "hp": 7, "max_hp": 7, "initiative": 12},
            ]
        ),
    )
    assert started["current"]["id"] == "hero"
    assert "attack" in started["legal_actions"]

    attacked = _call(
        capsys,
        "combat",
        "attack",
        "--campaign",
        campaign_id,
        "--actor",
        "hero",
        "--target-id",
        "goblin",
        "--attack-bonus",
        "99",
        "--expression",
        "1d2+2",
        "--damage-type",
        "slashing",
        "--weapon",
        "Training Sword",
    )
    assert attacked["result"]["hit"] is True
    goblin = next(item for item in attacked["combat"]["participants"] if item["id"] == "goblin")
    assert goblin["hp"] < 7
    hero = next(item for item in attacked["combat"]["participants"] if item["id"] == "hero")
    assert hero["action_available"] is False

    conditioned = _call(
        capsys,
        "combat",
        "condition",
        "add",
        "--campaign",
        campaign_id,
        "--target-id",
        "goblin",
        "--condition",
        "prone",
    )
    goblin = next(item for item in conditioned["combat"]["participants"] if item["id"] == "goblin")
    assert "prone" in goblin["conditions"]

    ended = _call(capsys, "combat", "end-turn", "--campaign", campaign_id, "--actor", "hero")
    assert ended["combat"]["current"]["id"] == "goblin"

    _call(capsys, "state", "undo", "--campaign", campaign_id)
    status = _call(capsys, "combat", "status", "--campaign", campaign_id)
    assert status["current"]["id"] == "hero"
