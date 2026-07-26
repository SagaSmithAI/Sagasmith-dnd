from __future__ import annotations

import pytest

from sagasmith_dnd.character_schema import (
    add_effect,
    add_inventory_item,
    add_memory,
    adjust_wallet,
    attune_inventory_item,
    consume_weapon_ammunition,
    default_character_sheet,
    derive_character_sheet,
    equip_inventory_item,
    legacy_memory_candidates,
    receive_inventory_item,
    remove_effect,
    remove_inventory_item,
    set_exhaustion_level,
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

    with pytest.warns(DeprecationWarning, match="ActorKnowledge"):
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
    candidates = legacy_memory_candidates(notes, actor_id="mira")
    assert candidates == [
        {
            "action": "add",
            "actor_id": "mira",
            "knowledge_key": f"legacy-memory:{memory_id}",
            "subject_ref": "",
            "proposition": "Mira promised to return the signet ring.",
            "epistemic_status": "known",
            "confidence": 4,
            "source_event_id": None,
            "cause": "legacy_character_note",
            "disclosure_scope": "dm",
            "legacy_memory": {
                "id": memory_id,
                "kind": "promise",
                "participants": [],
                "status": "active",
            },
        }
    ]


def test_removing_an_effect_cleans_only_conditions_no_longer_owned() -> None:
    sheet = default_character_sheet()
    sheet["conditions"] = ["prone"]
    fear = {
        "name": "Fear Ray",
        "kind": "timed_conditions",
        "source": "gazer-a",
        "duration": {"period": "source_turn_start", "remaining": 1},
        "changes": [
            {"path": "conditions", "mode": "add", "value": "frightened"}
        ],
    }
    sheet, first_id = add_effect(sheet, {"id": "fear-a", **fear})
    sheet, second_id = add_effect(
        sheet,
        {"id": "fear-b", **fear, "source": "gazer-b"},
    )
    assert sheet["conditions"] == ["prone", "frightened"]

    one_removed = remove_effect(sheet, first_id)
    assert one_removed["conditions"] == ["prone", "frightened"]

    both_removed = remove_effect(one_removed, second_id)
    assert both_removed["conditions"] == ["prone"]


def test_exhaustion_level_setter_enforces_the_character_sheet_range() -> None:
    base = default_character_sheet()
    base["combat"]["hp"] = {"value": 37, "max": 37, "temp": 0}
    sheet = set_exhaustion_level(base, 3)

    assert sheet["combat"]["exhaustion"] == 3
    level_four = set_exhaustion_level(sheet, 4)
    assert level_four["combat"]["hp"] == {"value": 18, "max": 37, "temp": 0}
    dead = set_exhaustion_level(sheet, 6)
    assert dead["conditions"] == ["dead"]
    with pytest.raises(ValueError, match="at most 6"):
        set_exhaustion_level(sheet, 7)


def test_inventory_weight_supports_rule_book_fractional_ounce_units() -> None:
    sheet = validate_character_sheet(
        {
            "inventory": {
                "items": [
                    {
                        "id": "arrows",
                        "name": "Arrows",
                        "kind": "ammunition",
                        "quantity": 20,
                        "weight_oz": 0.8,
                    },
                    {
                        "id": "crossbow-bolts",
                        "name": "Crossbow bolts",
                        "kind": "ammunition",
                        "quantity": 20,
                        "weight_oz": 1.2,
                    },
                ]
            }
        }
    )

    assert derive_character_sheet(sheet)["inventory"]["total_weight_oz"] == 40


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


def test_imported_ac_override_accepts_magic_item_bonus_and_mage_armor_alternative() -> None:
    sheet = validate_character_sheet(
        {
            "abilities": {"dexterity": {"score": 14}},
            "combat": {"ac": {"base": 12, "override": 12}},
        }
    )
    sheet, staff_id = add_inventory_item(
        sheet,
        {
            "id": "staff-of-defense",
            "name": "Staff of Defense",
            "kind": "magic_item",
            "mechanics": {"ac_bonus": 1},
        },
    )
    sheet = equip_inventory_item(sheet, staff_id, "main_hand")

    held = derive_character_sheet(sheet)
    assert held["armor_class"] == 13
    assert held["armor_class_breakdown"]["mode"] == "override"
    assert held["armor_class_breakdown"]["magic_items"] == [
        {"item_id": "staff-of-defense", "name": "Staff of Defense", "bonus": 1}
    ]

    sheet, effect_id = add_effect(
        sheet,
        {
            "id": "mage-armor",
            "name": "Mage Armor",
            "kind": "spell",
            "changes": [{"path": "combat.ac.unarmored_base", "mode": "override", "value": 13}],
        },
    )
    protected = derive_character_sheet(sheet)

    assert protected["armor_class"] == 16
    assert protected["armor_class_breakdown"]["mode"] == "mage_armor"
    assert protected["armor_class_breakdown"]["effects"] == [
        {
            "effect_id": effect_id,
            "name": "Mage Armor",
            "mode": "override",
            "value": 13,
            "applied": True,
        }
    ]
    assert protected["unresolved_rules"] == []


def test_magic_item_ac_bonus_waits_for_required_attunement() -> None:
    sheet = validate_character_sheet(
        {
            "abilities": {"dexterity": {"score": 14}},
            "combat": {"ac": {"base": 10}},
        }
    )
    sheet, staff_id = add_inventory_item(
        sheet,
        {
            "id": "staff-of-defense",
            "name": "Staff of Defense",
            "kind": "magic_item",
            "attunement": "required",
            "mechanics": {"ac_bonus": 1},
        },
    )
    sheet = equip_inventory_item(sheet, staff_id, "main_hand")

    assert derive_character_sheet(sheet)["armor_class"] == 12

    staff = next(item for item in sheet["inventory"]["items"] if item["id"] == staff_id)
    staff["attunement"] = "attuned"
    assert derive_character_sheet(validate_character_sheet(sheet))["armor_class"] == 13


def test_required_attunement_suppresses_all_equipment_magic_properties() -> None:
    sheet = validate_character_sheet(
        {
            "abilities": {
                "strength": {"score": 16},
                "dexterity": {"score": 14},
            },
            "combat": {"ac": {"base": 10}},
        }
    )
    sheet, armor_id = add_inventory_item(
        sheet,
        {
            "id": "warded-mail",
            "name": "Warded Mail",
            "kind": "armor",
            "attunement": "required",
            "mechanics": {
                "base_ac": 14,
                "dexterity_mode": "none",
                "magic_bonus": 2,
            },
        },
    )
    sheet = equip_inventory_item(sheet, armor_id, "armor")
    sheet, shield_id = add_inventory_item(
        sheet,
        {
            "id": "warded-shield",
            "name": "Warded Shield",
            "kind": "shield",
            "attunement": "required",
            "mechanics": {"ac_bonus": 2, "magic_bonus": 1},
        },
    )
    sheet = equip_inventory_item(sheet, shield_id, "shield")
    sheet, weapon_id = add_inventory_item(
        sheet,
        {
            "id": "flame-blade",
            "name": "Flame Blade",
            "kind": "weapon",
            "attunement": "required",
            "mechanics": {
                "damage_formula": "1d8",
                "damage_type": "slashing",
                "magic_bonus": 2,
                "additional_damage": [{"damage_formula": "1d6", "damage_type": "fire"}],
                "on_hit_effect": "target burns",
            },
        },
    )
    sheet = equip_inventory_item(sheet, weapon_id, "main_hand")

    unattuned = derive_character_sheet(sheet)
    assert unattuned["armor_class"] == 16
    assert unattuned["armor_class_breakdown"]["armor"]["magic_bonus"] == 0
    assert unattuned["armor_class_breakdown"]["shield"]["magic_bonus"] == 0
    attack = unattuned["inventory"]["weapon_attacks"][0]
    assert attack["attack_bonus"] == 5
    assert attack["damage_bonus"] == 3
    assert attack["additional_damage"] == []
    assert attack["on_hit_effect"] == ""
    assert attack["magic_suppressed_by_attunement"] is True

    for item in sheet["inventory"]["items"]:
        if item["id"] in {armor_id, shield_id, weapon_id}:
            item["attunement"] = "attuned"
    attuned = derive_character_sheet(validate_character_sheet(sheet))
    assert attuned["armor_class"] == 19
    attack = attuned["inventory"]["weapon_attacks"][0]
    assert attack["attack_bonus"] == 7
    assert attack["damage_bonus"] == 5
    assert attack["additional_damage"][0]["damage_type"] == "fire"
    assert attack["on_hit_effect"] == "target burns"


def test_attunement_enforces_capacity_copies_transfer_and_death() -> None:
    sheet = validate_character_sheet({})
    for index, name in enumerate(("Ring A", "Ring B", "Ring C", "Ring D"), start=1):
        sheet, _ = add_inventory_item(
            sheet,
            {
                "id": f"ring-{index}",
                "name": name,
                "kind": "magic_item",
                "source_key": f"core:item/ring-{index}",
                "attunement": "required",
            },
        )
    for item_id in ("ring-1", "ring-2", "ring-3"):
        sheet = attune_inventory_item(sheet, item_id)
    with pytest.raises(ValueError, match="more than three"):
        attune_inventory_item(sheet, "ring-4")

    duplicate_sheet, _ = add_inventory_item(
        validate_character_sheet({}),
        {
            "id": "ring-copy-1",
            "name": "Ring of Protection",
            "kind": "magic_item",
            "source_key": "core:item/ring-of-protection",
            "attunement": "required",
        },
    )
    duplicate_sheet, _ = add_inventory_item(
        duplicate_sheet,
        {
            "id": "ring-copy-2",
            "name": "Ring of Protection",
            "kind": "magic_item",
            "source_key": "core:item/ring-of-protection",
            "attunement": "required",
        },
    )
    duplicate_sheet = attune_inventory_item(duplicate_sheet, "ring-copy-1")
    with pytest.raises(ValueError, match="more than one copy"):
        attune_inventory_item(duplicate_sheet, "ring-copy-2")

    separate_sources, _ = add_inventory_item(
        validate_character_sheet({}),
        {
            "id": "separate-source-1",
            "name": "Ring of Protection",
            "kind": "magic_item",
            "source_key": "module:first-treasure",
            "attunement": "required",
        },
    )
    separate_sources, _ = add_inventory_item(
        separate_sources,
        {
            "id": "separate-source-2",
            "name": "Ring of Protection",
            "kind": "magic_item",
            "source_key": "module:second-treasure",
            "attunement": "required",
        },
    )
    separate_sources = attune_inventory_item(separate_sources, "separate-source-1")
    with pytest.raises(ValueError, match="more than one copy"):
        attune_inventory_item(separate_sources, "separate-source-2")

    with pytest.raises(ValueError, match="cannot be transferred"):
        receive_inventory_item(
            validate_character_sheet({}),
            next(
                item
                for item in sheet["inventory"]["items"]
                if item["id"] == "ring-1"
            ),
        )

    sheet["conditions"] = ["dead"]
    dead = validate_character_sheet(sheet)
    assert {
        item["attunement"] for item in dead["inventory"]["items"]
    } == {"required"}


def test_unarmored_base_formula_keeps_shield_and_chooses_highest_source() -> None:
    sheet = validate_character_sheet(
        {
            "abilities": {"dexterity": {"score": 16}},
            "combat": {"ac": {"base": 10}},
        }
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
    sheet = equip_inventory_item(sheet, shield_id, "shield")
    sheet, weaker_id = add_effect(
        sheet,
        {
            "name": "Weaker Formula",
            "kind": "feature",
            "changes": [{"path": "combat.ac.unarmored_base", "mode": "override", "value": 12}],
        },
    )
    sheet, stronger_id = add_effect(
        sheet,
        {
            "name": "Draconic Resilience",
            "kind": "feature",
            "changes": [{"path": "combat.ac.unarmored_base", "mode": "override", "value": 13}],
        },
    )

    derived = derive_character_sheet(sheet)

    assert derived["armor_class"] == 18
    assert derived["armor_class_breakdown"]["mode"] == "unarmored_formula"
    assert derived["armor_class_breakdown"]["shield"]["bonus"] == 2
    applied = {
        item["effect_id"]: item["applied"] for item in derived["armor_class_breakdown"]["effects"]
    }
    assert applied == {weaker_id: False, stronger_id: True}


def test_class_unarmored_formulas_honor_ability_and_shield_conditions() -> None:
    sheet = validate_character_sheet(
        {
            "abilities": {
                "dexterity": {"score": 14},
                "constitution": {"score": 14},
                "wisdom": {"score": 18},
            },
            "combat": {"ac": {"base": 10}},
        }
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
    sheet = equip_inventory_item(sheet, shield_id, "shield")
    sheet, barbarian_id = add_effect(
        sheet,
        {
            "name": "Barbarian Unarmored Defense",
            "kind": "feature",
            "changes": [
                {
                    "path": "combat.ac.unarmored_formula",
                    "mode": "override",
                    "value": {
                        "base": 10,
                        "ability": "constitution",
                        "allows_shield": True,
                    },
                }
            ],
        },
    )
    sheet, monk_id = add_effect(
        sheet,
        {
            "name": "Monk Unarmored Defense",
            "kind": "feature",
            "changes": [
                {
                    "path": "combat.ac.unarmored_formula",
                    "mode": "override",
                    "value": {
                        "base": 10,
                        "ability": "wisdom",
                        "allows_shield": False,
                    },
                }
            ],
        },
    )

    shielded = derive_character_sheet(sheet)
    assert shielded["armor_class"] == 16
    assert shielded["armor_class_breakdown"]["ability_bonus"] == {
        "ability": "constitution",
        "bonus": 2,
    }
    shielded_effects = {
        item["effect_id"]: item["applied"] for item in shielded["armor_class_breakdown"]["effects"]
    }
    assert shielded_effects == {barbarian_id: True, monk_id: False}

    unshielded = equip_inventory_item(sheet, shield_id, None)
    derived = derive_character_sheet(unshielded)
    assert derived["armor_class"] == 16
    assert derived["armor_class_breakdown"]["ability_bonus"] == {
        "ability": "wisdom",
        "bonus": 4,
    }


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
    last_shot_sheet = validate_character_sheet(after_shot)
    next(item for item in last_shot_sheet["inventory"]["items"] if item["id"] == "arrows")[
        "quantity"
    ] = 1
    empty_quiver, last_arrow = consume_weapon_ammunition(last_shot_sheet, "longbow")
    assert last_arrow["remaining"] == 0
    assert (
        next(item for item in empty_quiver["inventory"]["items"] if item["id"] == "arrows")[
            "quantity"
        ]
        == 0
    )
    with pytest.raises(ValueError, match="not enough"):
        consume_weapon_ammunition(empty_quiver, "longbow")

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


def test_2014_exhaustion_halves_effective_hit_point_maximum() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["combat"]["hp"] = {"value": 37, "max": 37, "temp": 0}
    sheet["combat"]["exhaustion"] = 4

    derived = derive_character_sheet(sheet)

    assert derived["hit_points"] == {
        "value": 18,
        "max": 18,
        "temp": 0,
        "base_max": 37,
    }
