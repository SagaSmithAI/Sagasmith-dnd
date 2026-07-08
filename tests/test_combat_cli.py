from __future__ import annotations

import json
import random
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
    hero = _call(
        capsys,
        "actor",
        "create",
        "--campaign",
        campaign_id,
        "--name",
        "Hero",
        "--payload",
        '{"attributes":{"ac":{"value":12},"hp":{"value":10,"max":10}}}',
    )
    goblin = _call(
        capsys,
        "actor",
        "create",
        "--campaign",
        campaign_id,
        "--name",
        "Goblin",
        "--type",
        "npc",
        "--payload",
        '{"attributes":{"ac":{"value":10},"hp":{"value":7,"max":7}}}',
    )
    scene = _call(capsys, "scene", "create", "--campaign", campaign_id, "--name", "Arena")
    _call(capsys, "token", "create", "--scene", scene["id"], "--name", "Hero", "--actor-id", hero["id"])
    _call(capsys, "token", "create", "--scene", scene["id"], "--name", "Goblin", "--actor-id", goblin["id"])

    started = _call(
        capsys,
        "combat",
        "start",
        "--campaign",
        campaign_id,
        "--name",
        "Goblin Ambush",
        "--scene",
        scene["id"],
    )
    assert started["current"]["id"] in {hero["id"], goblin["id"]}
    assert "attack" in started["legal_actions"]

    random.seed(2)
    attacked = _call(
        capsys,
        "combat",
        "attack",
        "--campaign",
        campaign_id,
        "--actor",
        hero["id"],
        "--target-id",
        goblin["id"],
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
    goblin_combatant = next(item for item in attacked["combat"]["combatants"] if item["id"] == goblin["id"])
    assert goblin_combatant["hp"] < 7
    hero_combatant = next(item for item in attacked["combat"]["combatants"] if item["id"] == hero["id"])
    assert hero_combatant["action_available"] is False

    conditioned = _call(
        capsys,
        "combat",
        "condition",
        "add",
        "--campaign",
        campaign_id,
        "--target-id",
        goblin["id"],
        "--condition",
        "prone",
    )
    goblin_combatant = next(item for item in conditioned["combat"]["combatants"] if item["id"] == goblin["id"])
    assert "prone" in goblin_combatant["conditions"]

    current = attacked["combat"]["current"]["id"]
    ended = _call(capsys, "combat", "end-turn", "--campaign", campaign_id, "--actor", current)
    assert ended["combat"]["current"]["id"] != current

    _call(capsys, "state", "undo", "--campaign", campaign_id)
    status = _call(capsys, "combat", "status", "--campaign", campaign_id)
    assert status["current"]["id"] == current


def test_combat_death_save_records_success(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'death.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Death Saves")["campaign"]
    hero = _call(
        capsys,
        "actor",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Hero",
        "--payload",
        '{"attributes":{"hp":{"value":0,"max":10}}}',
    )
    scene = _call(capsys, "scene", "create", "--campaign", campaign["id"], "--name", "Death")
    _call(capsys, "token", "create", "--scene", scene["id"], "--name", "Hero", "--actor-id", hero["id"])
    _call(
        capsys,
        "combat",
        "start",
        "--campaign",
        campaign["id"],
        "--scene",
        scene["id"],
    )

    random.seed(0)
    saved = _call(
        capsys,
        "combat",
        "death-save",
        "--campaign",
        campaign["id"],
        "--target-id",
        hero["id"],
    )

    assert saved["result"]["outcome"] == "pending"
    assert saved["result"]["death_saves"]["successes"] == 1
    hero_combatant = saved["combat"]["combatants"][0]
    assert hero_combatant["death_saves"]["successes"] == 1
