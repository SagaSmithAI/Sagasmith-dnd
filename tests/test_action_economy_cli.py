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


def test_combat_status_lists_current_actor_document_activities(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'status.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Action Status")["campaign"]
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
        "Second Wind",
        "--type",
        "feat",
    )
    activity = _call(
        capsys,
        "game-activity",
        "create",
        "--item",
        item["id"],
        "--name",
        "Second Wind",
        "--type",
        "heal",
        "--payload",
        '{"activation":{"type":"bonus"},"system":{"healing":"1"}}',
    )
    scene = _call(capsys, "scene", "create", "--campaign", campaign["id"], "--name", "Arena")
    _call(capsys, "token", "create", "--scene", scene["id"], "--name", "Mira", "--actor-id", actor["id"])

    status = _call(capsys, "combat", "start", "--campaign", campaign["id"], "--scene", scene["id"])

    assert status["current"]["id"] == actor["id"]
    assert status["activity_options"]["turn_budget"]["bonus_action"] == 1
    assert status["activity_options"]["turn_budget"]["movement"] == 30
    assert status["activity_options"]["turn_budget"]["object_interaction"] == 1
    assert status["legal_activities"] == [
        {
            "item_id": item["id"],
            "item_name": "Second Wind",
            "item_type": "feat",
            "activity_id": activity["id"],
            "activity_name": "Second Wind",
            "activity_type": "heal",
            "activation": {"type": "bonus"},
            "payments": ["bonus_action"],
            "requires_target": True,
        }
    ]


def test_extra_attack_and_action_surge_payments_flow_through_activity_use(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'payments.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Action Payments")["campaign"]
    hero = _call(
        capsys,
        "actor",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Fighter",
        "--payload",
        '{"features":["extra-attack"],"class_levels":{"fighter":11},"attributes":{"hp":{"value":20,"max":20}}}',
    )
    target = _call(
        capsys,
        "actor",
        "create",
        "--campaign",
        campaign["id"],
        "--name",
        "Dummy",
        "--type",
        "npc",
        "--payload",
        '{"attributes":{"ac":{"value":1},"hp":{"value":20,"max":20}}}',
    )
    weapon = _call(
        capsys,
        "game-item",
        "create",
        "--campaign",
        campaign["id"],
        "--actor",
        hero["id"],
        "--name",
        "Longsword",
        "--type",
        "weapon",
    )
    attack = _call(
        capsys,
        "game-activity",
        "create",
        "--item",
        weapon["id"],
        "--name",
        "Slash",
        "--type",
        "attack",
        "--payload",
        '{"activation":{"type":"action"},"system":{"attack_bonus":99,"damage":"1"}}',
    )
    surge_item = _call(
        capsys,
        "game-item",
        "create",
        "--campaign",
        campaign["id"],
        "--actor",
        hero["id"],
        "--name",
        "Action Surge",
        "--type",
        "feat",
    )
    surge = _call(
        capsys,
        "game-activity",
        "create",
        "--item",
        surge_item["id"],
        "--name",
        "Action Surge",
        "--type",
        "utility",
        "--payload",
        '{"activation":{"type":"free"},"uses":{"spent":0,"max":1},"system":{"grant":{"extra_actions":1}}}',
    )
    scene = _call(capsys, "scene", "create", "--campaign", campaign["id"], "--name", "Arena")
    _call(capsys, "token", "create", "--scene", scene["id"], "--name", "Fighter", "--actor-id", hero["id"])
    _call(capsys, "token", "create", "--scene", scene["id"], "--name", "Dummy", "--actor-id", target["id"])
    _call(capsys, "combat", "start", "--campaign", campaign["id"], "--scene", scene["id"])

    first = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign["id"],
        "--actor",
        hero["id"],
        "--item",
        weapon["id"],
        "--activity",
        attack["id"],
        "--target-id",
        target["id"],
    )
    assert first["payment"] == "main_action"
    assert first["state_delta"]["runtime"]["turn_budgets"][hero["id"]]["attack_budget"] == 2

    second = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign["id"],
        "--actor",
        hero["id"],
        "--item",
        weapon["id"],
        "--activity",
        attack["id"],
        "--target-id",
        target["id"],
    )
    assert second["payment"] == "attack_budget"
    assert second["state_delta"]["runtime"]["turn_budgets"][hero["id"]]["attack_budget"] == 1

    third = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign["id"],
        "--actor",
        hero["id"],
        "--item",
        weapon["id"],
        "--activity",
        attack["id"],
        "--target-id",
        target["id"],
    )
    assert third["payment"] == "attack_budget"
    assert third["state_delta"]["runtime"]["turn_budgets"][hero["id"]]["attack_budget"] == 0

    error = _call_error(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign["id"],
        "--actor",
        hero["id"],
        "--item",
        weapon["id"],
        "--activity",
        attack["id"],
        "--target-id",
        target["id"],
    )
    assert "cannot pay" in error["message"]

    surged = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign["id"],
        "--actor",
        hero["id"],
        "--item",
        surge_item["id"],
        "--activity",
        surge["id"],
    )
    surge_budget = surged["state_delta"]["runtime"]["turn_budgets"][hero["id"]]
    assert surge_budget["extra_action"] == 1
    assert surge_budget["bonus_action"] == 1
    assert surge_budget["reaction"] == 1

    fourth = _call(
        capsys,
        "activity",
        "use",
        "--campaign",
        campaign["id"],
        "--actor",
        hero["id"],
        "--item",
        weapon["id"],
        "--activity",
        attack["id"],
        "--target-id",
        target["id"],
    )
    assert fourth["payment"] == "extra_action"
    assert fourth["state_delta"]["runtime"]["turn_budgets"][hero["id"]]["attack_budget"] == 2
