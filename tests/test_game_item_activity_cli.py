from __future__ import annotations

import json
import random
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


def test_game_item_and_activity_cli_create_update_execute(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'game-item.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Game Item")["campaign"]
    attacker = _call(capsys, "actor", "create", "--campaign", campaign["id"], "--name", "Mira")
    target = _call(
        capsys,
        "actor",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Goblin",
        "--type",
        "npc",
        "--payload",
        '{"attributes":{"ac":{"value":12},"hp":{"value":10,"max":10}}}',
    )

    item = _call(
        capsys,
        "game-item",
        "create",
        "--campaign",
        campaign["id"],
        "--actor",
        attacker["id"],
        "--name",
        "Longsword",
        "--type",
        "weapon",
        "--payload",
        '{"equipped":true,"attack_bonus":99}',
    )
    activity = _call(
        capsys,
        "game-activity",
        "create",
        "--item",
        item["id"],
        "--name",
        "Slash",
        "--type",
        "attack",
        "--payload",
        '{"activation":{"type":"action"},"system":{"attack_bonus":99,"damage":"1","damage_type":"slashing"}}',
    )

    assert _call(capsys, "game-item", "list", "--campaign", campaign["id"], "--actor", attacker["id"])[
        "items"
    ][0]["id"] == item["id"]
    assert _call(capsys, "game-activity", "list", "--item", item["id"])["activities"][0]["id"] == activity["id"]

    updated = _call(
        capsys,
        "game-activity",
        "update",
        "--activity",
        activity["id"],
        "--payload",
        '{"uses":{"spent":0,"max":1,"recovery":[{"period":"shortRest"}]}}',
    )
    assert updated["uses"]["max"] == 1
    assert _call(capsys, "game-item", "show", "--item", item["id"])["activities"][0]["uses"]["max"] == 1

    random.seed(0)
    result = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign["id"],
        "--actor",
        attacker["id"],
        "--item",
        item["id"],
        "--activity",
        activity["id"],
        "--target-id",
        target["id"],
    )

    assert result["execution"]["type"] == "attack"
    assert result["execution"]["hit"] is True
    assert result["execution"]["damage"]["after_hp"] == 9
    assert result["activity"]["uses"]["spent"] == 1


def test_game_item_and_activity_contract_defaults_and_validation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'contracts.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Contracts")["campaign"]
    actor = _call(capsys, "actor", "create", "--campaign", campaign["id"], "--name", "Mira")

    item = _call(
        capsys,
        "game-item",
        "create",
        "--campaign",
        campaign["id"],
        "--actor",
        actor["id"],
        "--name",
        "Torch",
        "--type",
        "equipment",
    )
    assert item["system"]["quantity"] == 1
    assert item["system"]["equipped"] is False
    assert item["system"]["identified"] is True

    activity = _call(
        capsys,
        "game-activity",
        "create",
        "--item",
        item["id"],
        "--name",
        "Use Torch",
        "--type",
        "utility",
    )
    assert activity["activation"]["type"] == "free"
    assert activity["uses"] == {}

    item_error = _call_error(
        capsys,
        "game-item",
        "create",
        "--campaign",
        campaign["id"],
        "--actor",
        actor["id"],
        "--name",
        "Bad",
        "--type",
        "artifact-of-doom",
    )
    assert "unknown item type" in item_error["message"]

    activity_error = _call_error(
        capsys,
        "game-activity",
        "create",
        "--item",
        item["id"],
        "--name",
        "Bad",
        "--type",
        "ritual-dance",
    )
    assert "unknown activity type" in activity_error["message"]
