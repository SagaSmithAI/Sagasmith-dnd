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


def test_ready_action_spends_action_and_trigger_spends_reaction(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'ready.db').as_posix()}")
    campaign = _call(capsys, "campaign", "start", "--name", "Ready")["campaign"]

    readied = _call(
        capsys,
        "ready",
        "set",
        "--campaign",
        campaign["id"],
        "--actor",
        "hero",
        "--condition",
        "when the goblin leaves cover",
        "--payload",
        '{"activity":"longbow_attack"}',
    )

    ready_id = readied["ready"]["id"]
    assert readied["turn_budget"]["main_action"] == 0
    assert readied["ready"]["payload"]["activity"] == "longbow_attack"

    triggered = _call(
        capsys,
        "ready",
        "trigger",
        "--campaign",
        campaign["id"],
        "--id",
        ready_id,
    )

    assert triggered["ready"]["status"] == "triggered"
    assert triggered["ready"]["turn_budget"]["reaction"] == 0
