from pathlib import Path

from sagasmith_dnd.core_content import build_srd2014_content
from sagasmith_dnd.core_rule_pack import get_core_rule_pack
from sagasmith_dnd.spell_resolution import spell_resolution_path
from sagasmith_dnd.standard_spell_ids import CORE_SLEEP_MECHANIC_ID, CORE_SLEEP_SPELL_ID


def test_real_sleep_card_has_source_bound_2014_native_path() -> None:
    _, artifacts = build_srd2014_content(Path(__file__).resolve().parents[3] / "skills")
    sleep = next(item for item in artifacts if item["id"] == CORE_SLEEP_SPELL_ID)
    assert sleep["mechanic_refs"] == [CORE_SLEEP_MECHANIC_ID]
    assert spell_resolution_path(sleep["card"]) == "engine_mechanic"
    assert any("Sleep.md" in reference for reference in sleep["rule_refs"])
    assert "5d8" in sleep["card"]["definition"]["effect"]
    assert CORE_SLEEP_MECHANIC_ID in {item.id for item in get_core_rule_pack("2014").boundaries}
    assert CORE_SLEEP_MECHANIC_ID not in {item.id for item in get_core_rule_pack("2024").boundaries}
