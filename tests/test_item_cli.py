from __future__ import annotations

import json
from pathlib import Path

from sagasmith_dnd.cli import main


def _call(capsys, *args: str) -> tuple[int, dict]:
    code = main([*args, "--json"])
    output = capsys.readouterr()
    assert output.err == ""
    return code, json.loads(output.out)


def test_item_cli_lifecycle_and_character_inventory_import(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setenv("DND_DATABASE_URL", f"sqlite+pysqlite:///{(tmp_path / 'dnd.db').as_posix()}")

    _, created = _call(capsys, "campaign", "start", "--name", "Ledger")
    campaign_id = created["data"]["campaign"]["id"]

    _, template = _call(
        capsys,
        "item",
        "template",
        "create",
        "--name",
        "Potion of Healing",
        "--source-key",
        "srd:potion-healing",
        "--category",
        "consumable",
        "--value",
        '{"gp":50}',
    )
    template_id = template["data"]["id"]

    _, added = _call(
        capsys,
        "item",
        "add",
        "--campaign",
        campaign_id,
        "--template",
        template_id,
        "--name",
        "Potion of Healing",
        "--quantity",
        "2",
    )
    item_id = added["data"]["id"]

    assert _call(
        capsys,
        "item",
        "move",
        "--item",
        item_id,
        "--owner-type",
        "character",
        "--owner-id",
        "hero",
    )[1]["data"]["owner_type"] == "character"
    assert _call(capsys, "item", "equip", "--item", item_id, "--slot", "belt")[1]["data"][
        "equipped_slot"
    ] == "belt"
    assert _call(capsys, "item", "use", "--item", item_id, "--quantity", "1")[1]["data"][
        "quantity"
    ] == 1
    assert len(_call(capsys, "item", "history", "--campaign", campaign_id)[1]["data"]["entries"]) == 4

    _, character = _call(
        capsys,
        "character",
        "create",
        "--campaign",
        campaign_id,
        "--name",
        "Mira",
        "--sheet",
        '{"inventory":["Torch",{"name":"Rope","quantity":1}]}',
    )
    character_id = character["data"]["id"]
    assert character["data"]["sheet"]["inventory_managed"] is True
    assert [item["name"] for item in character["data"]["sheet"]["inventory"]] == [
        "Rope",
        "Torch",
    ]

    shown = _call(capsys, "character", "show", "--id", character_id)[1]
    assert len(shown["data"]["sheet"]["inventory"]) == 2

    listed = _call(
        capsys,
        "item",
        "list",
        "--campaign",
        campaign_id,
        "--owner-type",
        "character",
        "--owner-id",
        character_id,
    )[1]
    assert {item["name"] for item in listed["data"]["items"]} == {"Rope", "Torch"}
