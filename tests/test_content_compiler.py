from __future__ import annotations

import json
from pathlib import Path

from sagasmith_dnd.content_compiler import compile_foundry_content, write_content_pack


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_compiler_emits_canonical_content_pack(tmp_path: Path) -> None:
    _write(
        tmp_path / "spells" / "spark.yml",
        """
_id: spark-id
name: Spark
type: spell
system:
  identifier: spark
  source: {rules: '2014', license: CC-BY-4.0}
  level: 0
  activities:
    spark-activity:
      type: attack
      activation: {type: action}
      attack: {bonus: '5'}
      damage:
        parts: [{number: 1, denomination: 8, types: [fire]}]
""",
    )
    _write(
        tmp_path / "monsters" / "beast" / "wolf.yml",
        """
_id: wolf-id
name: Wolf
type: npc
system:
  identifier: wolf
  source: {rules: '2014', license: CC-BY-4.0}
  attributes: {hp: {value: 11, max: 11}}
items:
  - _id: bite-id
    name: Bite
    type: weapon
    system:
      activities:
        bite-activity:
          type: attack
          activation: {type: action}
""",
    )
    _write(
        tmp_path / "classes" / "fighter.yml",
        """
name: Fighter
type: class
system:
  identifier: fighter
  source: {rules: '2014', license: CC-BY-4.0}
  hitDice: d10
""",
    )
    _write(
        tmp_path / "subclasses" / "champion.yml",
        """
name: Champion
type: subclass
system:
  identifier: champion
  classIdentifier: fighter
  source: {rules: '2014', license: CC-BY-4.0}
""",
    )
    _write(
        tmp_path / "classfeatures" / "action-surge.yml",
        """
name: Action Surge
type: feat
system:
  identifier: action-surge
  source: {rules: '2014', license: CC-BY-4.0}
  activities:
    surge:
      type: utility
      activation: {type: special}
""",
    )

    pack = compile_foundry_content(tmp_path)

    assert pack["schema_version"] == 1
    assert pack["content"]["spells"]["spark"]["activities"][0]["type"] == "cast"
    assert (
        pack["content"]["spells"]["spark"]["activities"][0]["system"]["damage"]["parts"][0][
            "denomination"
        ]
        == 8
    )
    assert pack["content"]["monsters"]["wolf"]["items"][0]["activities"][0]["type"] == "attack"
    assert pack["content"]["classes"]["fighter"]["hit_die"] == "d10"
    assert pack["content"]["subclasses"]["champion"]["class_key"] == "fighter"
    assert pack["coverage"]["spells"]["executable"] == 1

    output = write_content_pack(pack, tmp_path / "compiled" / "pack.json")
    assert json.loads(output.read_text(encoding="utf-8"))["id"] == "dnd5e-2014-srd"
