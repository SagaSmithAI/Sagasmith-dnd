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

    assert (
        _call(
            capsys,
            "campaign",
            "update",
            "--campaign",
            campaign_id,
            "--state",
            '{"door":"open"}',
        )[0]
        == 0
    )
    assert _call(capsys, "state", "undo", "--campaign", campaign_id)[0] == 0
    _, restored_campaign = _call(
        capsys,
        "campaign",
        "show",
        "--campaign",
        campaign_id,
    )
    assert restored_campaign["data"]["campaign"]["state"]["party"]["inventory"]["items"] == []

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
    assert imported["data"]["scenes"] == 2
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
    scene_index = _call(
        capsys,
        "module",
        "export-scenes",
        "--campaign",
        campaign_id,
        "--output",
        str(tmp_path / "scenes.json"),
    )[1]
    assert [scene["title"] for scene in scene_index["data"]["scenes"]] == [
        "第一章",
        "酒馆",
    ]
    assert scene_index["data"]["scenes"][1]["subsections"] == [
        {"title": "遭遇", "line": 3, "type": "section"},
        {"title": "A1. 地窖", "line": 4, "type": "room"},
    ]
    scene_id = scene_index["data"]["scenes"][1]["scene_id"]
    assert (
        _call(
            capsys,
            "module",
            "set-progress",
            "--campaign",
            campaign_id,
            "--scene",
            scene_id,
            "--progress",
            "25",
            "--room",
            "A1. 地窖",
            "--state",
            '{"discovered":["宝箱"]}',
        )[0]
        == 0
    )
    current = _call(
        capsys,
        "module",
        "current",
        "--campaign",
        campaign_id,
    )[1]["data"]["scene"]
    assert current["title"] == "酒馆"
    assert current["scope_id"] == "party"
    assert current["progress"]["current_room"] == "A1. 地窖"
    assert current["progress"]["state"] == {"discovered": ["宝箱"]}
    scoped = _call(
        capsys,
        "module",
        "current",
        "--campaign",
        campaign_id,
        "--scope",
        "player:hero",
    )[1]["data"]["scene"]
    assert scoped["title"] == "酒馆"
    assert scoped["inherited_from_party"] is True
    assert (
        _call(
            capsys,
            "module",
            "set-progress",
            "--campaign",
            campaign_id,
            "--scope",
            "player:hero",
            "--scene",
            scene_index["data"]["scenes"][0]["scene_id"],
            "--progress",
            "10",
        )[0]
        == 0
    )
    personal = _call(
        capsys,
        "module",
        "current",
        "--campaign",
        campaign_id,
        "--scope",
        "player:hero",
    )[1]["data"]["scene"]
    assert personal["title"] == "第一章"
    assert personal["scope_id"] == "player:hero"
    party = _call(
        capsys,
        "module",
        "current",
        "--campaign",
        campaign_id,
    )[1]["data"]["scene"]
    assert party["title"] == "酒馆"
    created_save = _call(
        capsys,
        "save",
        "create",
        "--campaign",
        campaign_id,
        "--label",
        "After arrival",
    )[1]
    slot = str(created_save["data"]["slot"])
    recap = _call(
        capsys,
        "save",
        "regenerate-recap",
        "--campaign",
        campaign_id,
        "--slot",
        slot,
    )[1]
    assert recap["data"]["source"] == "deterministic"
    memory_status = _call(
        capsys,
        "memory",
        "status",
        "--campaign",
        campaign_id,
    )[1]
    assert memory_status["data"]["count"] == 0


def test_cli_error_is_a_single_json_document(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'bad.db').as_posix()}")
    code, result = _call(capsys, "campaign", "show", "--campaign", "missing")
    assert code == 3
    assert result["ok"] is False
    assert result["error"]["code"] == "not_found"


def test_cli_character_v2_inventory_party_and_memory_workflow(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv(
        "DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'state.db').as_posix()}"
    )
    campaign_id = _call(capsys, "campaign", "start", "--name", "Stateful", "--edition", "2014")[1][
        "data"
    ]["campaign"]["id"]
    caster_sheet = json.dumps(
        {
            "progression": {
                "level": 3,
                "classes": [{"name": "Cleric", "level": 3, "hit_die": 8}],
            },
            "abilities": {"wisdom": {"score": 16}},
            "spellcasting": {
                "ability": "wisdom",
                "preparation": {
                    "mode": "prepared",
                    "max_prepared": 2,
                    "selected_spell_ids": ["cure"],
                },
            },
            "content": {
                "spells": [
                    {"id": "cure", "name": "Cure Wounds", "level": 1, "access": {"known": True}},
                    {"id": "bless", "name": "Bless", "level": 1, "access": {"known": True}},
                ]
            },
        }
    )
    _, mira_created = _call(
        capsys,
        "character",
        "create",
        "--campaign",
        campaign_id,
        "--name",
        "Mira",
        "--sheet",
        caster_sheet,
    )
    mira_id = mira_created["data"]["id"]
    _, nox_created = _call(
        capsys,
        "character",
        "create",
        "--campaign",
        campaign_id,
        "--name",
        "Nox",
        "--type",
        "npc",
        "--notes",
        '{"profile":{"summary":"A cautious innkeeper."}}',
    )
    nox_id = nox_created["data"]["id"]

    _, added = _call(
        capsys,
        "character",
        "inventory",
        "add",
        "--id",
        mira_id,
        "--payload",
        '{"id":"key","name":"Rusty Key","kind":"loot","description":"Opens an old cellar."}',
    )
    assert added["data"]["item_id"] == "key"
    assert (
        _call(
            capsys,
            "character",
            "wallet",
            "credit",
            "--id",
            mira_id,
            "--denomination",
            "gp",
            "--amount",
            "5",
        )[0]
        == 0
    )
    assert (
        _call(capsys, "character", "spell", "prepare", "--id", mira_id, "--spell", "bless")[0] == 0
    )
    assert (
        _call(
            capsys,
            "character",
            "effect",
            "add",
            "--id",
            mira_id,
            "--payload",
            '{"name":"Bless","kind":"spell","source":"srd.bless"}',
        )[0]
        == 0
    )
    assert (
        _call(
            capsys,
            "character",
            "memory",
            "add",
            "--id",
            nox_id,
            "--payload",
            '{"kind":"conversation","summary":"The party accepted the cellar job.","importance":4}',
        )[0]
        == 0
    )

    assert (
        _call(
            capsys,
            "character",
            "inventory",
            "transfer",
            "--id",
            mira_id,
            "--target",
            nox_id,
            "--item",
            "key",
        )[0]
        == 0
    )
    assert (
        _call(
            capsys,
            "character",
            "wallet",
            "transfer",
            "--id",
            mira_id,
            "--target",
            nox_id,
            "--denomination",
            "gp",
            "--amount",
            "3",
        )[0]
        == 0
    )
    assert (
        _call(
            capsys,
            "party",
            "inventory",
            "deposit",
            "--campaign",
            campaign_id,
            "--id",
            nox_id,
            "--item",
            "key",
        )[0]
        == 0
    )
    _, party = _call(
        capsys,
        "party",
        "wallet",
        "deposit",
        "--campaign",
        campaign_id,
        "--id",
        nox_id,
        "--denomination",
        "gp",
        "--amount",
        "2",
    )
    assert party["data"]["wallet"]["gp"] == 2

    assert (
        _call(
            capsys,
            "character",
            "wallet",
            "credit",
            "--id",
            mira_id,
            "--denomination",
            "gp",
            "--amount",
            "1",
        )[0]
        == 0
    )
    assert _call(capsys, "state", "undo", "--campaign", campaign_id)[0] == 0
    _, after_undo = _call(capsys, "character", "show", "--id", mira_id)
    assert after_undo["data"]["sheet"]["inventory"]["wallet"]["gp"] == 2
    assert _call(capsys, "state", "redo", "--campaign", campaign_id)[0] == 0

    _, saved_v2 = _call(capsys, "save", "create", "--campaign", campaign_id, "--label", "v2 state")
    assert (
        _call(
            capsys,
            "party",
            "wallet",
            "credit",
            "--campaign",
            campaign_id,
            "--denomination",
            "gp",
            "--amount",
            "1",
        )[0]
        == 0
    )
    assert (
        _call(
            capsys,
            "save",
            "restore",
            "--campaign",
            campaign_id,
            "--slot",
            str(saved_v2["data"]["slot"]),
        )[0]
        == 0
    )
    _, restored_party = _call(capsys, "party", "show", "--campaign", campaign_id)
    assert restored_party["data"]["inventory"]["wallet"]["gp"] == 2

    _, mira = _call(capsys, "character", "show", "--id", mira_id)
    assert set(mira["data"]["derived"]["spellcasting"]["prepared_spell_ids"]) == {"cure", "bless"}
    _, nox = _call(capsys, "character", "show", "--id", nox_id)
    assert nox["data"]["notes"]["profile"]["summary"] == "A cautious innkeeper."
    assert nox["data"]["notes"]["memories"][0]["importance"] == 4


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

    assert (
        _call(
            capsys,
            "rules",
            "ingest",
            "--path",
            str(tmp_path / "en"),
            "--edition",
            "2014",
            "--locale",
            "en",
        )[0]
        == 0
    )
    assert (
        _call(
            capsys,
            "rules",
            "ingest",
            "--path",
            str(tmp_path / "zh"),
            "--edition",
            "2014",
            "--locale",
            "zh",
        )[0]
        == 0
    )
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
