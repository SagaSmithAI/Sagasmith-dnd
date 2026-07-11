from __future__ import annotations

import pytest

from sagasmith_dnd.character_schema import (
    add_effect,
    add_inventory_item,
    add_memory,
    adjust_wallet,
    derive_character_sheet,
    equip_inventory_item,
    remove_inventory_item,
    set_spell_prepared,
    validate_character_notes,
    validate_character_sheet,
)


def _caster_sheet() -> dict:
    return {
        "progression": {
            "level": 3,
            "classes": [{"name": "Cleric", "level": 3, "hit_die": 8}],
        },
        "abilities": {"wisdom": {"score": 16, "save_proficient": True}},
        "spellcasting": {
            "ability": "wisdom",
            "spell_slots": {"1": {"value": 4, "max": 4, "recovers_on": "long_rest"}},
            "preparation": {
                "mode": "prepared",
                "max_prepared": 2,
                "selected_spell_ids": ["cure-wounds"],
            },
        },
        "content": {
            "spells": [
                {
                    "id": "cure-wounds",
                    "source_key": "srd.cure-wounds",
                    "name": "Cure Wounds",
                    "level": 1,
                    "access": {"known": True},
                },
                {
                    "id": "bless",
                    "source_key": "srd.bless",
                    "name": "Bless",
                    "level": 1,
                    "access": {"known": True},
                },
            ]
        },
    }


def test_v2_sheet_exposes_complete_derived_card_and_prepared_spells() -> None:
    sheet = validate_character_sheet(_caster_sheet())
    assert sheet["schema_version"] == 2
    assert sheet["content"]["spells"][0]["access"]["prepared"] is True
    assert sheet["content"]["spells"][1]["access"]["prepared"] is False

    prepared = set_spell_prepared(sheet, "bless", True)
    assert prepared["spellcasting"]["preparation"]["selected_spell_ids"] == [
        "cure-wounds",
        "bless",
    ]
    derived = derive_character_sheet(prepared)
    assert derived["proficiency_bonus"] == 2
    assert derived["spellcasting"]["save_dc"] == 13
    assert set(derived["spellcasting"]["prepared_spell_ids"]) == {"cure-wounds", "bless"}


def test_inventory_wallet_effect_and_memory_contracts() -> None:
    sheet, item_id = add_inventory_item(
        validate_character_sheet({}),
        {
            "id": "healing-potion",
            "name": "Potion of Healing",
            "kind": "consumable",
            "quantity": 2,
            "weight_oz": 8,
            "price_cp": 5000,
            "description": "A red herbal vial.",
        },
    )
    assert item_id == "healing-potion"
    sheet = adjust_wallet(sheet, "gp", 12)
    sheet = equip_inventory_item(sheet, item_id, "main_hand")
    sheet, effect_id = add_effect(
        sheet,
        {"name": "Bless", "kind": "spell", "source": "srd.bless", "changes": []},
    )
    derived = derive_character_sheet(sheet)
    assert derived["inventory"]["wallet_value_cp"] == 1200
    assert derived["active_effects"] == [{"id": effect_id, "name": "Bless"}]

    remaining, moved = remove_inventory_item(sheet, item_id, 1)
    assert moved["quantity"] == 1
    assert remaining["inventory"]["items"][0]["quantity"] == 1

    notes, memory_id = add_memory(
        validate_character_notes({}),
        {
            "kind": "promise",
            "summary": "Mira promised to return the signet ring.",
            "importance": 4,
            "visibility": "dm",
        },
    )
    assert notes["memories"][0]["id"] == memory_id


def test_schema_rejects_legacy_fields_and_invalid_container_cycles() -> None:
    with pytest.raises(ValueError, match="unsupported fields"):
        validate_character_sheet({"level": 3})
    with pytest.raises(ValueError, match="cycle"):
        validate_character_sheet(
            {
                "inventory": {
                    "items": [
                        {"id": "bag-a", "name": "A", "kind": "container", "container_id": "bag-b"},
                        {"id": "bag-b", "name": "B", "kind": "container", "container_id": "bag-a"},
                    ]
                }
            }
        )
    with pytest.raises(ValueError, match="npc notes.profile.summary"):
        validate_character_notes({}, character_type="npc")
