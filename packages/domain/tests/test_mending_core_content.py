from pathlib import Path

from sagasmith_dnd.core_content import build_srd2014_content
from sagasmith_dnd.core_rule_pack import get_core_rule_pack
from sagasmith_dnd.spell_resolution import spell_resolution_path
from sagasmith_dnd.standard_spell_ids import (
    CORE_MENDING_MECHANIC_ID,
    CORE_MENDING_SPELL_ID,
)


def test_real_mending_card_has_source_bound_2014_native_path() -> None:
    _, artifacts = build_srd2014_content(Path(__file__).resolve().parents[3] / "skills")
    mending = next(item for item in artifacts if item["id"] == CORE_MENDING_SPELL_ID)
    assert mending["mechanic_refs"] == [CORE_MENDING_MECHANIC_ID]
    assert spell_resolution_path(mending["card"]) == "engine_mechanic"
    assert any("Mending.md" in reference for reference in mending["rule_refs"])
    assert CORE_MENDING_MECHANIC_ID in {
        item.id for item in get_core_rule_pack("2014").boundaries
    }
    assert CORE_MENDING_MECHANIC_ID not in {
        item.id for item in get_core_rule_pack("2024").boundaries
    }
