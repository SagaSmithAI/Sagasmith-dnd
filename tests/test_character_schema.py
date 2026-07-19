from __future__ import annotations

import pytest

from sagasmith_dnd.character_schema import (
    add_effect,
    add_inventory_item,
    add_memory,
    adjust_wallet,
    consume_weapon_ammunition,
    derive_character_sheet,
    equip_inventory_item,
    remove_inventory_item,
    set_spell_prepared,
    validate_character_notes,
    validate_character_sheet,
    validate_party_state,
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
    assert derived["attacks_per_action"] == 1
    assert derived["spellcasting"]["save_dc"] == 13
    assert set(derived["spellcasting"]["prepared_spell_ids"]) == {"cure-wounds", "bless"}


def test_class_prepared_spell_does_not_have_to_be_known() -> None:
    sheet = {
        "progression": {
            "level": 1,
            "classes": [{"name": "Cleric", "level": 1, "hit_die": 8}],
        },
        "spellcasting": {
            "preparation": {
                "mode": "prepared",
                "max_prepared": 1,
                "selected_spell_ids": ["bless"],
            },
        },
        "content": {
            "spells": [
                {
                    "id": "bless",
                    "name": "Bless",
                    "level": 1,
                    "grant": {
                        "source_type": "class",
                        "source_key": "Cleric",
                        "method": "class_prepared",
                    },
                    "access": {"known": False},
                }
            ]
        },
    }

    normalized = validate_character_sheet(sheet)

    assert normalized["content"]["spells"][0]["access"]["known"] is False
    assert normalized["content"]["spells"][0]["access"]["prepared"] is True


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


def test_spellbook_inventory_preserves_structured_copy_sources() -> None:
    sheet, item_id = add_inventory_item(
        validate_character_sheet({}),
        {
            "id": "d11-red-spellbook",
            "name": "Red leather spellbook",
            "kind": "spellbook",
            "source_key": "module:avernus:d11:red-spellbook",
            "mechanics": {
                "edition": "2014",
                "spell_ids": [
                    "dnd5e.content.srd2014.spell.burning-hands",
                    "dnd5e.content.srd2014.spell.detect-magic",
                ],
                "unresolved_spell_names": ["Ray of Sickness"],
                "owner_mark": "No recorded owner mark",
                "source_scene_id": "d11-scene",
                "deciphered": True,
                "copyable": True,
            },
        },
    )

    assert item_id == "d11-red-spellbook"
    item = sheet["inventory"]["items"][0]
    assert item["kind"] == "spellbook"
    assert item["mechanics"]["spell_ids"] == [
        "dnd5e.content.srd2014.spell.burning-hands",
        "dnd5e.content.srd2014.spell.detect-magic",
    ]
    assert item["mechanics"]["unresolved_spell_names"] == ["Ray of Sickness"]

    with pytest.raises(ValueError, match="duplicate ids"):
        add_inventory_item(
            validate_character_sheet({}),
            {
                "name": "Invalid spellbook",
                "kind": "spellbook",
                "mechanics": {"spell_ids": ["spell:a", "spell:a"]},
            },
        )


def test_party_state_validates_structured_world_effect_targets() -> None:
    state = validate_party_state(
        {
            "world_effects": [
                {
                    "id": "mace-light",
                    "name": "Light on Mara's mace",
                    "kind": "light",
                    "source_spell_id": "dnd5e.content.srd2014.spell.light",
                    "source_actor_id": "mara",
                    "target": {"kind": "object", "id": "mara-mace", "label": "Mace"},
                    "duration": {"period": "hour", "remaining": 1},
                }
            ]
        }
    )
    assert state["world_effects"][0]["target"]["kind"] == "object"

    with pytest.raises(ValueError, match="target.id is required"):
        validate_party_state(
            {
                "world_effects": [
                    {
                        "name": "Invalid",
                        "target": {"kind": "scene"},
                    }
                ]
            }
        )


def test_equipment_slots_and_ac_derive_from_armor_shield_magic_and_effects() -> None:
    sheet = validate_character_sheet(
        {
            "abilities": {"dexterity": {"score": 16}},
            "combat": {"ac": {"base": 10}},
        }
    )
    sheet, armor_id = add_inventory_item(
        sheet,
        {
            "id": "leather",
            "name": "Leather Armor",
            "kind": "armor",
            "mechanics": {
                "base_ac": 11,
                "dexterity_mode": "full",
                "magic_bonus": 0,
                "stealth_disadvantage": True,
            },
        },
    )
    sheet, shield_id = add_inventory_item(
        sheet,
        {
            "id": "shield",
            "name": "Shield",
            "kind": "shield",
            "mechanics": {"ac_bonus": 2, "magic_bonus": 0},
        },
    )
    sheet, cloak_id = add_inventory_item(
        sheet,
        {
            "id": "cloak",
            "name": "Cloak of Protection",
            "kind": "magic_item",
            "mechanics": {"ac_bonus": 1},
        },
    )
    sheet = equip_inventory_item(sheet, armor_id, "armor")
    sheet = equip_inventory_item(sheet, shield_id, "shield")
    sheet = equip_inventory_item(sheet, cloak_id, "cloak")
    sheet, _ = add_effect(
        sheet,
        {
            "name": "Shield of Faith",
            "kind": "spell",
            "changes": [{"path": "derived.armor_class", "mode": "add", "value": 2}],
        },
    )

    derived = derive_character_sheet(sheet)
    assert derived["armor_class"] == 19
    assert derived["armor_class_breakdown"]["armor"]["dexterity_bonus"] == 3
    assert derived["stealth_disadvantage"] is True
    assert derived["armor_class_breakdown"]["shield"]["bonus"] == 2
    assert derived["armor_class_breakdown"]["magic_items"] == [
        {"item_id": "cloak", "name": "Cloak of Protection", "bonus": 1}
    ]
    assert derived["unresolved_rules"] == []


def test_ac_override_does_not_erase_equipped_armor_stealth_disadvantage() -> None:
    sheet = validate_character_sheet(
        {
            "abilities": {"dexterity": {"score": 12}},
            "combat": {"ac": {"base": 10, "override": 19}},
        }
    )
    sheet, armor_id = add_inventory_item(
        sheet,
        {
            "id": "scale-mail",
            "name": "Scale Mail",
            "kind": "armor",
            "mechanics": {
                "base_ac": 14,
                "dexterity_mode": "max",
                "dexterity_max": 2,
                "magic_bonus": 0,
                "stealth_disadvantage": True,
            },
        },
    )
    sheet = equip_inventory_item(sheet, armor_id, "armor")

    derived = derive_character_sheet(sheet)
    assert derived["armor_class"] == 19
    assert derived["armor_class_breakdown"]["mode"] == "override"
    assert derived["stealth_disadvantage"] is True


def test_equipment_schema_rejects_incompatible_slots_and_inconsistent_state() -> None:
    with pytest.raises(ValueError, match="base_ac is required"):
        add_inventory_item(
            validate_character_sheet({}),
            {"name": "Broken Armor", "kind": "armor", "mechanics": {}},
        )
    potion_sheet, potion_id = add_inventory_item(
        validate_character_sheet({}),
        {"id": "potion", "name": "Potion", "kind": "consumable"},
    )
    with pytest.raises(ValueError, match="cannot be equipped in armor"):
        equip_inventory_item(potion_sheet, potion_id, "armor")
    with pytest.raises(ValueError, match="equipment slot and item equipped state must agree"):
        validate_character_sheet(
            {
                "inventory": {
                    "items": [
                        {
                            "id": "armor",
                            "name": "Leather",
                            "kind": "armor",
                            "mechanics": {"base_ac": 11},
                        }
                    ],
                    "equipment_slots": {"armor": "armor"},
                }
            }
        )


def test_complete_card_supports_identity_weapons_spells_encumbrance_and_adventure_state() -> None:
    sheet = validate_character_sheet(
        {
            "identity": {
                "gender": "female",
                "age": "27",
                "height_cm": 168,
                "weight_lb": 132,
                "faith": "The Triad",
                "deity": "Tyr",
                "hair": "black",
                "skin": "olive",
                "eyes": "brown",
                "portrait_uri": "asset://portraits/mira.png",
            },
            "progression": {
                "background": "Soldier",
                "background_grants": {
                    "feature": "Military Rank",
                    "equipment_item_ids": ["longbow"],
                    "languages": ["Common"],
                    "tools": ["Dice set"],
                },
            },
            "abilities": {"strength": {"score": 16}, "dexterity": {"score": 14}},
            "combat": {
                "inspiration": True,
                "wounded": True,
                "hp_progression": [
                    {"level": 1, "method": "fixed", "value": 10, "source": "Fighter d10"},
                    {"level": 2, "method": "rolled", "value": 7, "source": "d10 roll"},
                ],
            },
            "traits": {"size": "medium", "senses": {"darkvision": 60, "truesight": 30}},
            "spellcasting": {
                "ability": "wisdom",
                "casting_economy": "spell_points",
                "spell_points": {"value": 7, "max": 10, "recovers_on": "long_rest"},
            },
            "content": {
                "spells": [
                    {
                        "id": "bless",
                        "name": "Bless",
                        "level": 1,
                        "point_cost": 2,
                        "definition": {
                            "school": "enchantment",
                            "casting_time": "1 action",
                            "range": {"kind": "distance", "normal_ft": 30},
                            "duration": {
                                "kind": "timed",
                                "value": 1,
                                "unit": "minute",
                                "concentration": True,
                            },
                            "components": {
                                "verbal": True,
                                "somatic": True,
                                "material": True,
                                "material_description": "holy water",
                            },
                            "effect": "Bless up to three creatures.",
                        },
                    }
                ],
                "features": [
                    {
                        "name": "Second Wind",
                        "resource_key": "second_wind",
                        "activation": {"type": "bonus_action", "cost": 1},
                        "scaling": [{"level": 1, "value": 1, "description": "One use."}],
                    }
                ],
            },
            "effects": [
                {
                    "name": "Bless",
                    "source_spell_id": "bless",
                    "concentration": True,
                    "duration": {"period": "round", "remaining": 10},
                }
            ],
            "adventure_state": {
                "reputation": {"Baldur's Gate": 3},
                "contributions": {"Harpers": 1},
                "blessings": ["Blessing of Health"],
                "wards": ["Temple ward"],
                "legendary_boons": ["Boon of Fortitude"],
                "status_tags": ["wanted"],
            },
            "inventory": {
                "encumbrance": {"mode": "variant", "ignore_currency_weight": True},
                "items": [
                    {
                        "id": "arrows",
                        "name": "Arrows",
                        "kind": "ammunition",
                        "quantity": 20,
                        "weight_oz": 1,
                    },
                    {
                        "id": "longbow",
                        "name": "Longbow",
                        "kind": "weapon",
                        "equipped": True,
                        "equipped_slot": "main_hand",
                        "mechanics": {
                            "category": "martial",
                            "attack_type": "ranged",
                            "attack_ability": "dexterity",
                            "damage_formula": "1d8",
                            "damage_type": "piercing",
                            "properties": ["ammunition", "heavy", "two_handed"],
                            "normal_range_ft": 150,
                            "long_range_ft": 600,
                            "ammunition_item_id": "arrows",
                        },
                    },
                    {
                        "id": "bag",
                        "name": "Bag of Holding",
                        "kind": "container",
                        "mechanics": {
                            "capacity_oz": 4000,
                            "weightless_contents": True,
                            "extra_dimensional": True,
                        },
                    },
                    {
                        "id": "anvil",
                        "name": "Anvil",
                        "kind": "equipment",
                        "weight_oz": 1600,
                        "container_id": "bag",
                    },
                ],
                "equipment_slots": {"main_hand": "longbow"},
            },
        }
    )
    assert sheet["identity"]["deity"] == "Tyr"
    assert sheet["content"]["spells"][0]["definition"]["components"]["material"] is True
    assert sheet["effects"][0]["concentration"] is True
    assert sheet["inventory"]["items"][2]["mechanics"]["extra_dimensional"] is True
    derived = derive_character_sheet(sheet)
    assert derived["inventory"]["encumbrance"]["carried_weight_oz"] == 20
    assert derived["inventory"]["weapon_attacks"][0]["attack_bonus"] == 4
    assert derived["inventory"]["weapon_attacks"][0]["damage_expression"] == "1d8 + 2"
    assert derived["hit_point_progression"]["recorded_gain_total"] == 17
    after_shot, consumed = consume_weapon_ammunition(sheet, "longbow")
    assert consumed["item_id"] == "arrows"
    assert (
        next(item for item in after_shot["inventory"]["items"] if item["id"] == "arrows")[
            "quantity"
        ]
        == 19
    )

    notes = validate_character_notes({"profile": {"backstory": "A veteran of the border wars."}})
    assert notes["profile"]["backstory"] == "A veteran of the border wars."


def test_schema_rejects_invalid_ammunition_capacity_and_multiple_concentration_effects() -> None:
    with pytest.raises(ValueError, match="ammunition_item_id"):
        validate_character_sheet(
            {
                "inventory": {
                    "items": [
                        {
                            "id": "bow",
                            "name": "Bow",
                            "kind": "weapon",
                            "mechanics": {"ammunition_item_id": "missing"},
                        }
                    ]
                }
            }
        )
    with pytest.raises(ValueError, match="exceed capacity"):
        validate_character_sheet(
            {
                "inventory": {
                    "items": [
                        {
                            "id": "pack",
                            "name": "Pack",
                            "kind": "container",
                            "mechanics": {"capacity_oz": 1},
                        },
                        {
                            "id": "rope",
                            "name": "Rope",
                            "kind": "equipment",
                            "weight_oz": 2,
                            "container_id": "pack",
                        },
                    ]
                }
            }
        )
    with pytest.raises(ValueError, match="one active concentration"):
        validate_character_sheet(
            {
                "effects": [
                    {"name": "First", "concentration": True},
                    {"name": "Second", "concentration": True},
                ]
            }
        )


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


def test_content_selection_provenance_is_normalized_and_unique() -> None:
    sheet = validate_character_sheet(
        {
            "content": {
                "selections": [
                    {
                        "artifact_id": "dnd5e.content.srd2014.subclass.path-of-the-berserker",
                        "kind": "subclass",
                        "name": "Path of the Berserker",
                        "pack_id": "dnd5e.content.srd2014",
                        "pack_version": "1.1.0",
                        "rule_refs": ["bundled:srd2014/02_Classes/Barbarian.md"],
                        "selection": {"target_class_name": "Barbarian"},
                    }
                ]
            }
        }
    )
    assert sheet["content"]["selections"][0]["pack_version"] == "1.1.0"
    with pytest.raises(ValueError, match="duplicate artifact ids"):
        validate_character_sheet(
            {
                "content": {
                    "selections": [
                        {"artifact_id": "same", "kind": "background"},
                        {"artifact_id": "same", "kind": "subclass"},
                    ]
                }
            }
        )
