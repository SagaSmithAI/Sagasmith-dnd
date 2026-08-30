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


@pytest.mark.parametrize("relative_path", MAGE_REFERENCE_PATHS)
def test_standalone_mage_references_match_full_srd_mirror(relative_path: Path) -> None:
    assert (STANDALONE_SRD / relative_path).read_bytes() == (
        FULL_SRD / relative_path
    ).read_bytes()
