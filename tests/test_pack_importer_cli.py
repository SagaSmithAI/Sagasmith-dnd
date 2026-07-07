from __future__ import annotations

import json
from pathlib import Path

from sagasmith_core import CampaignService, Database, FoundryDocumentService
from sagasmith_core.database import sqlite_database_url

from sagasmith_dnd.cli import main


def _call(capsys, *args: str) -> dict:
    code = main([*args, "--json"])
    output = capsys.readouterr()
    value = json.loads(output.out)
    assert code == 0, value
    assert value["ok"] is True
    return value["data"]


def test_pack_import_converts_foundry_spell_yaml_to_item_activity(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    url = sqlite_database_url(tmp_path / "pack.db")
    monkeypatch.setenv("DND_DATABASE_URL", url)
    pack_dir = tmp_path / "pack"
    pack_dir.mkdir()
    (pack_dir / "bless.yml").write_text(
        """
_id: bless001
name: Bless
type: spell
img: icons/bless.webp
system:
  level: 1
  properties:
    - concentration
  activation:
    type: action
  duration:
    value: '1'
    units: minute
  activities:
    dnd5eactivity000:
      _id: dnd5eactivity000
      type: utility
      activation:
        type: action
      consumption:
        spellSlot: true
      effects: []
""",
        encoding="utf-8",
    )
    database = Database(url)
    database.upgrade_schema()
    try:
        campaign = CampaignService(database).create(system_id="dnd5e", name="Pack")
    finally:
        database.dispose()

    result = _call(
        capsys,
        "pack",
        "import",
        "--campaign",
        campaign.id,
        "--path",
        str(pack_dir),
    )

    assert result["count"] == 1
    imported = result["imported"][0]
    assert imported["item"]["name"] == "Bless"
    assert imported["activities"][0]["activity_type"] == "cast"
    assert imported["activities"][0]["duration"]["concentration"] is True

    database = Database(url)
    database.upgrade_schema()
    try:
        documents = FoundryDocumentService(database)
        items = documents.list_items(campaign.id)
        assert items[0].source_key == "bless001"
        assert documents.list_activities(items[0].id)[0].activity_type == "cast"
    finally:
        database.dispose()
