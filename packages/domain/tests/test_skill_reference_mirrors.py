from __future__ import annotations

from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[3]
FULL_SRD = WORKSPACE / "skills" / "full" / "skills" / "dnd-dm" / "srd"
STANDALONE_SRD = WORKSPACE / "skills" / "standalone" / "skills" / "dnd-dm" / "srd"

REFERENCE_CORPORA = (
    "references",
    "references-2014-en",
    "references-2014-zh",
)

ADVENTURING_REFERENCE_PATH = Path("references-2014-en/06_Gameplay/Adventuring.md")
TRAVEL_PACE_ROWS = (
    ("Fast", "400 feet", "4 miles", "30 miles", "-5 penalty to passive Wisdom (Perception) scores"),
    ("Normal", "300 feet", "3 miles", "24 miles", "-"),
    ("Slow", "200 feet", "2 miles", "18 miles", "Able to use stealth"),
)


def test_standalone_rule_references_match_full_srd_mirror() -> None:
    def references(root: Path) -> dict[Path, bytes]:
        return {
            path.relative_to(root): path.read_bytes()
            for corpus in REFERENCE_CORPORA
            for path in (root / corpus).rglob("*.md")
            # Installation guidance intentionally differs between the two modes.
            if path.relative_to(root) != Path("references-2014-en/README.md")
        }

    full = references(FULL_SRD)
    standalone = references(STANDALONE_SRD)
    assert full
    assert full.keys() == standalone.keys()
    assert [path for path in full if full[path] != standalone[path]] == []


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
