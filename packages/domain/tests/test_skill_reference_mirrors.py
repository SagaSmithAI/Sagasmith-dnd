from __future__ import annotations

from pathlib import Path

import pytest

WORKSPACE = Path(__file__).resolve().parents[3]
FULL_SRD = WORKSPACE / "skills" / "full" / "skills" / "dnd-dm" / "srd"
STANDALONE_SRD = WORKSPACE / "skills" / "standalone" / "skills" / "dnd-dm" / "srd"

MAGE_REFERENCE_PATHS = (
    Path("references-2014-en/10_Monsters/Monsters_Each/Mage_(NPC).md"),
    Path("references-2014-en/10_Monsters/Monsters_A-Z/NPCs.md"),
    Path("references-2014-zh/Monsters/Mage (NPC).md"),
    Path("references-2014-zh/Monsters (Alt)/NPCs.md"),
)

ADVENTURING_REFERENCE_PATH = Path("references-2014-en/06_Gameplay/Adventuring.md")
TRAVEL_PACE_ROWS = (
    ("Fast", "400 feet", "4 miles", "30 miles", "-5 penalty to passive Wisdom (Perception) scores"),
    ("Normal", "300 feet", "3 miles", "24 miles", "-"),
    ("Slow", "200 feet", "2 miles", "18 miles", "Able to use stealth"),
)


@pytest.mark.parametrize("relative_path", MAGE_REFERENCE_PATHS)
def test_standalone_mage_references_match_full_srd_mirror(relative_path: Path) -> None:
    assert (STANDALONE_SRD / relative_path).read_bytes() == (
        FULL_SRD / relative_path
    ).read_bytes()


def test_2014_travel_pace_table_matches_the_official_srd_and_mirror() -> None:
    full_text = (FULL_SRD / ADVENTURING_REFERENCE_PATH).read_text(encoding="utf-8")
    standalone_text = (STANDALONE_SRD / ADVENTURING_REFERENCE_PATH).read_text(
        encoding="utf-8"
    )

    travel_section = full_text.split("**Table- Travel Pace**", 1)[1].split(
        "### Difficult Terrain", 1
    )[0]
    table_rows = tuple(
        tuple(cells)
        for line in travel_section.splitlines()
        if line.startswith(("| Fast ", "| Normal ", "| Slow "))
        if len(cells := [cell.strip() for cell in line.strip("|").split("|")]) == 5
    )
    assert table_rows == TRAVEL_PACE_ROWS
    assert standalone_text == full_text
