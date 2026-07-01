from __future__ import annotations

import json
from pathlib import Path

from sagasmith_dnd.cli import main


def _call(capsys, *args: str) -> tuple[int, dict]:
    code = main([*args, "--json"])
    output = capsys.readouterr()
    assert output.err == ""
    return code, json.loads(output.out)


def test_json_cli_campaign_rules_module_and_save(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'dnd.db').as_posix()}")
    code, created = _call(
        capsys,
        "campaign",
        "start",
        "--name",
        "Keep",
        "--edition",
        "2014",
        "--locale",
        "zh",
    )
    assert code == 0
    assert created["ok"] is True
    campaign_id = created["data"]["campaign"]["id"]
    assert created["data"]["rule_profile"]["edition"] == "2014"

    assert _call(
        capsys,
        "campaign",
        "update",
        "--campaign",
        campaign_id,
        "--state",
        '{"door":"open"}',
    )[0] == 0
    assert _call(capsys, "state", "undo", "--campaign", campaign_id)[0] == 0
    _, restored_campaign = _call(
        capsys,
        "campaign",
        "show",
        "--campaign",
        campaign_id,
    )
    assert restored_campaign["data"]["campaign"]["state"] == {}

    rules = tmp_path / "rules.md"
    rules.write_text("# 借机攻击\n敌人离开触及范围时可触发反应。\n", encoding="utf-8")
    code, ingested = _call(
        capsys,
        "rules",
        "ingest",
        "--path",
        str(rules),
        "--edition",
        "2014",
        "--locale",
        "zh",
    )
    assert code == 0
    assert ingested["data"]["chunks"] == 1
    code, found = _call(
        capsys,
        "rules",
        "search",
        "--campaign",
        campaign_id,
        "--query",
        "借机攻击",
    )
    assert code == 0
    assert found["data"]["hits"][0]["metadata"]["edition"] == "2014"

    module = tmp_path / "generated.md"
    module.write_text(
        "# 第一章\n## 酒馆\n### 遭遇\n#### A1. 地窖\n宝箱藏在墙后。\n",
        encoding="utf-8",
    )
    code, imported = _call(
        capsys,
        "module",
        "ingest",
        "--campaign",
        campaign_id,
        "--path",
        str(module),
    )
    assert code == 0
    assert imported["data"]["scenes"] == 1
    code, searched = _call(
        capsys,
        "module",
        "search",
        "--campaign",
        campaign_id,
        "--query",
        "宝箱",
    )
    assert code == 0
    assert searched["data"]["hits"][0]["title"] == "酒馆"


def test_cli_error_is_a_single_json_document(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'bad.db').as_posix()}")
    code, result = _call(capsys, "campaign", "show", "--campaign", "missing")
    assert code == 3
    assert result["ok"] is False
    assert result["error"]["code"] == "not_found"


def test_2014_translation_links_to_english_source(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv(
        "DND_DATABASE_URL",
        f"sqlite+pysqlite:///{(tmp_path / 'links.db').as_posix()}",
    )
    english = tmp_path / "en" / "06_Gameplay"
    chinese = tmp_path / "zh" / "Gameplay"
    english.mkdir(parents=True)
    chinese.mkdir(parents=True)
    (english / "Combat.md").write_text("# Initiative\nRoll Dexterity.\n", encoding="utf-8")
    (chinese / "Combat.md").write_text("# 先攻\n进行敏捷检定。\n", encoding="utf-8")

    assert _call(
        capsys,
        "rules",
        "ingest",
        "--path",
        str(tmp_path / "en"),
        "--edition",
        "2014",
        "--locale",
        "en",
    )[0] == 0
    assert _call(
        capsys,
        "rules",
        "ingest",
        "--path",
        str(tmp_path / "zh"),
        "--edition",
        "2014",
        "--locale",
        "zh",
    )[0] == 0
    _, found = _call(
        capsys,
        "rules",
        "search",
        "--query",
        "先攻",
        "--edition",
        "2014",
        "--locale",
        "zh",
    )
    assert found["data"]["hits"][0]["metadata"]["canonical_source_id"]
