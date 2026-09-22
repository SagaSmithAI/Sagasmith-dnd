import pytest

from sagasmith_dnd.character_schema import (
    add_effect,
    default_character_sheet,
    derive_character_sheet,
)
from sagasmith_dnd.combat_engine import apply_damage_to_sheet
from sagasmith_dnd.lifecycle import advance_elapsed_effect_durations


@pytest.mark.parametrize(
    "defense,expected", [("vulnerabilities", 14), ("resistances", 3), ("immunities", 0)]
)
def test_temporary_defense_is_typed_and_expires(defense, expected):
    sheet = default_character_sheet()
    sheet["combat"]["hp"].update(value=100, max=100)
    sheet, effect_id = add_effect(sheet, {
        "name": "Source-bound temporary defense",
        "source": "module-chunk:test-source",
        "duration": {"period": "hour", "remaining": 24},
        "changes": [{"path": f"traits.{defense}", "mode": "add", "value": "necrotic"}],
    })
    assert effect_id not in derive_character_sheet(sheet)["unresolved_rules"]
    damage = apply_damage_to_sheet(sheet, amount=7, damage_type="necrotic")
    assert damage["applied_amount"] == expected
    assert apply_damage_to_sheet(sheet, amount=7, damage_type="fire")["applied_amount"] == 7
    assert sheet["traits"][defense] == []
    expired = advance_elapsed_effect_durations(sheet, elapsed_ticks=24 * 600)["sheet"]
    assert apply_damage_to_sheet(expired, amount=7, damage_type="necrotic")["applied_amount"] == 7


@pytest.mark.parametrize("value", [1, True, "unknown", ["necrotic"]])
def test_temporary_defense_rejects_ambiguous_values(value):
    with pytest.raises(ValueError, match="canonical damage type"):
        add_effect(default_character_sheet(), {
            "changes": [{"path": "traits.vulnerabilities", "mode": "add", "value": value}],
        })
