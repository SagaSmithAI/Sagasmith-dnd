import pytest

from sagasmith_dnd.character_schema import (
    default_character_sheet,
    derive_character_sheet,
    validate_character_sheet,
)
from sagasmith_dnd.lifecycle import advance_effect_durations
from sagasmith_dnd.spells import (
    CORE_MAGE_ARMOR_SPELL_ID,
    CORE_MAGIC_MISSILE_MECHANIC_ID,
    CORE_MAGIC_MISSILE_SPELL_ID,
    CORE_SHIELD_MECHANIC_ID,
    CORE_SHIELD_SPELL_ID,
    available_shield_attack_defenses,
    available_shield_magic_missile_defenses,
    consume_magic_item_spell_cast,
    consume_readied_spell,
    consume_shield_reaction,
    consume_spell_cast,
    recharge_magic_item_charges,
    replace_prepared_spells,
    resolve_magic_item_last_charge,
    validate_magic_missile_allocations,
)


def _spell(spell_id: str, *, level: int, concentration: bool = False) -> dict:
    return {
        "id": spell_id,
        "name": spell_id,
        "level": level,
        "access": {"known": True, "prepared": True, "ritual_available": False},
        "definition": {
            "casting_time": "1 action",
            "duration": {
                "kind": "timed",
                "value": 1,
                "unit": "minute",
                "concentration": concentration,
            },
        },
    }


def test_spell_slot_and_concentration_are_settled_from_card_data() -> None:
    sheet = default_character_sheet()
    sheet["spellcasting"]["spell_slots"] = {
        "1": {"label": "1st", "value": 1, "max": 1, "recovers_on": "long_rest", "source_key": ""}
    }
    sheet["content"]["spells"] = [_spell("bless", level=1, concentration=True)]
    result = consume_spell_cast(validate_character_sheet(sheet), spell_id="bless")
    assert result["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] == 0
    assert result["concentration_started"] is True


def test_shield_reaction_pays_slot_and_expires_at_turn_start() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["ac"]["override"] = 13
    sheet["spellcasting"]["spell_slots"] = {
        "1": {
            "label": "1st",
            "value": 1,
            "max": 1,
            "recovers_on": "long_rest",
            "source_key": "wizard",
        }
    }
    shield = _spell(CORE_SHIELD_SPELL_ID, level=1)
    shield["name"] = "Shield"
    shield["definition"]["casting_time"] = "1 reaction, which you take when hit"
    shield["definition"]["duration"] = {
        "kind": "timed",
        "value": 1,
        "unit": "round",
        "concentration": False,
    }
    shield["mechanic_refs"] = [CORE_SHIELD_MECHANIC_ID]
    sheet["content"]["spells"] = [shield]
    sheet = validate_character_sheet(sheet)

    assert available_shield_attack_defenses(sheet) == [
        {
            "id": CORE_SHIELD_SPELL_ID,
            "name": "Shield",
            "kind": "spell_armor_class_bonus",
            "bonus": 5,
            "spell_id": CORE_SHIELD_SPELL_ID,
            "cast_levels": [1],
            "cast_options": [
                {
                    "cast_level": 1,
                    "payment": {
                        "economy": "slots",
                        "level": 1,
                        "ritual": False,
                    },
                }
            ],
            "mechanic_id": CORE_SHIELD_MECHANIC_ID,
            "source_key": "",
            "rule_refs": [],
        }
    ]
    applied = consume_shield_reaction(
        sheet,
        spell_id=CORE_SHIELD_SPELL_ID,
        cast_level=1,
    )
    assert applied["payment"]["economy"] == "slots"
    assert applied["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] == 0
    assert available_shield_attack_defenses(applied["sheet"]) == []
    assert derive_character_sheet(applied["sheet"])["armor_class"] == 18

    ended = advance_effect_durations(applied["sheet"], period="turn_end")
    assert derive_character_sheet(ended["sheet"])["armor_class"] == 18
    started = advance_effect_durations(ended["sheet"], period="turn_start")
    assert started["expired"] == [applied["effect_id"]]
    expired_effect = next(
        effect for effect in started["sheet"]["effects"] if effect["id"] == applied["effect_id"]
    )
    assert expired_effect["ended_reason"] == "duration_expired"
    assert derive_character_sheet(started["sheet"])["armor_class"] == 13


def test_magic_item_charges_cast_source_bound_defenses() -> None:
    sheet = default_character_sheet()
    sheet["abilities"]["dexterity"]["score"] = 14
    sheet["spellcasting"]["ability"] = "intelligence"
    sheet["spellcasting"]["class_lists"] = ["wizard"]
    mage_armor = _spell(CORE_MAGE_ARMOR_SPELL_ID, level=1)
    mage_armor.update(
        name="Mage Armor",
        classes=["wizard", "sorcerer"],
        pack_id="dnd5e.content.srd2014",
        pack_version="1.6.0",
        rule_refs=["bundled:srd2014/spells/mage-armor"],
    )
    shield = _spell(CORE_SHIELD_SPELL_ID, level=1)
    shield.update(
        name="Shield",
        classes=["wizard", "sorcerer"],
        pack_id="dnd5e.content.srd2014",
        pack_version="1.6.0",
        rule_refs=["bundled:srd2014/spells/shield"],
        mechanic_refs=[CORE_SHIELD_MECHANIC_ID],
    )
    shield["definition"]["casting_time"] = "1 reaction, which you take when hit"
    sheet["inventory"]["items"] = [
        {
            "id": "staff-of-defense",
            "name": "Staff of Defense",
            "kind": "magic_item",
            "equipped": True,
            "equipped_slot": "main_hand",
            "attunement": "attuned",
            "charges": {
                "label": "Staff charges",
                "value": 10,
                "max": 10,
                "recovers_on": "dawn",
                "source_key": "module-chunk:staff",
            },
            "source_key": "module-chunk:staff",
            "mechanics": {
                "ac_bonus": 1,
                "charge_rules": {
                    "recovery_trigger": "dawn",
                    "recovery_formula": "1d6+4",
                    "last_charge_check_formula": "1d20",
                    "destroy_on": [1],
                },
                "spellcasting": {
                    "requires_attunement": True,
                    "requires_class_spell_list": True,
                    "components_required": False,
                    "spells": [
                        {
                            "artifact_id": CORE_MAGE_ARMOR_SPELL_ID,
                            "charge_cost": 1,
                            "casting_time": "1 action",
                            "card": mage_armor,
                        },
                        {
                            "artifact_id": CORE_SHIELD_SPELL_ID,
                            "charge_cost": 2,
                            "casting_time": "1 action",
                            "card": shield,
                        },
                    ],
                },
            },
        }
    ]
    sheet["inventory"]["equipment_slots"]["main_hand"] = "staff-of-defense"
    sheet = validate_character_sheet(sheet)

    assert derive_character_sheet(sheet)["armor_class"] == 13
    assert available_shield_attack_defenses(sheet) == []

    armored = consume_magic_item_spell_cast(
        sheet,
        source_item_id="staff-of-defense",
        spell_id=CORE_MAGE_ARMOR_SPELL_ID,
    )
    assert armored["status"] == "committed"
    assert armored["automatic_effect"] == "mage_armor"
    assert armored["payment"] == {
        "economy": "item_charges",
        "item_id": "staff-of-defense",
        "cost": 1,
        "level": 1,
        "ritual": False,
    }
    staff = armored["sheet"]["inventory"]["items"][0]
    assert staff["charges"]["value"] == 9
    assert derive_character_sheet(armored["sheet"])["armor_class"] == 16

    shielded = consume_magic_item_spell_cast(
        armored["sheet"],
        source_item_id="staff-of-defense",
        spell_id=CORE_SHIELD_SPELL_ID,
    )
    assert shielded["automatic_effect"] == "shield"
    assert shielded["sheet"]["inventory"]["items"][0]["charges"]["value"] == 7
    assert derive_character_sheet(shielded["sheet"])["armor_class"] == 21

    started = advance_effect_durations(shielded["sheet"], period="turn_start")
    assert derive_character_sheet(started["sheet"])["armor_class"] == 16


def test_magic_item_charge_recovery_and_last_charge_check() -> None:
    sheet = default_character_sheet()
    sheet["inventory"]["items"] = [
        {
            "id": "staff",
            "name": "Staff",
            "kind": "magic_item",
            "equipped": True,
            "equipped_slot": "main_hand",
            "charges": {
                "label": "Charges",
                "value": 0,
                "max": 10,
                "recovers_on": "dawn",
            },
            "mechanics": {
                "charge_rules": {
                    "recovery_trigger": "dawn",
                    "recovery_formula": "1d6+4",
                    "last_charge_check_formula": "1d20",
                    "destroy_on": [1],
                }
            },
        }
    ]
    sheet["inventory"]["equipment_slots"]["main_hand"] = "staff"
    sheet = validate_character_sheet(sheet)

    safe = resolve_magic_item_last_charge(sheet, source_item_id="staff", rolled_total=2)
    assert safe["destroyed"] is False
    destroyed = resolve_magic_item_last_charge(sheet, source_item_id="staff", rolled_total=1)
    assert destroyed["destroyed"] is True
    assert destroyed["sheet"]["inventory"]["items"][0]["condition"] == "destroyed"
    assert destroyed["sheet"]["inventory"]["equipment_slots"]["main_hand"] is None

    recharged = recharge_magic_item_charges(
        safe["sheet"],
        source_item_id="staff",
        trigger="dawn",
        rolled_total=9,
    )
    assert recharged["recovered"] == 9
    assert recharged["charges"]["value"] == 9


def test_magic_item_spell_cast_requires_attunement_and_class_list() -> None:
    sheet = default_character_sheet()
    mage_armor = _spell(CORE_MAGE_ARMOR_SPELL_ID, level=1)
    mage_armor.update(
        classes=["wizard"],
        pack_id="dnd5e.content.srd2014",
        pack_version="1.6.0",
        rule_refs=["bundled:srd2014/spells/mage-armor"],
    )
    sheet["inventory"]["items"] = [
        {
            "id": "staff",
            "name": "Staff",
            "kind": "magic_item",
            "equipped": True,
            "equipped_slot": "main_hand",
            "attunement": "required",
            "charges": {"label": "Charges", "value": 1, "max": 1, "recovers_on": "dawn"},
            "mechanics": {
                "spellcasting": {
                    "requires_attunement": True,
                    "requires_class_spell_list": True,
                    "spells": [
                        {
                            "artifact_id": CORE_MAGE_ARMOR_SPELL_ID,
                            "charge_cost": 1,
                            "card": mage_armor,
                        }
                    ],
                }
            },
        }
    ]
    sheet["inventory"]["equipment_slots"]["main_hand"] = "staff"
    sheet = validate_character_sheet(sheet)

    with pytest.raises(ValueError, match="requires attunement"):
        consume_magic_item_spell_cast(
            sheet,
            source_item_id="staff",
            spell_id=CORE_MAGE_ARMOR_SPELL_ID,
        )

    sheet["inventory"]["items"][0]["attunement"] = "attuned"
    with pytest.raises(ValueError, match="recorded actor spell class list"):
        consume_magic_item_spell_cast(
            validate_character_sheet(sheet),
            source_item_id="staff",
            spell_id=CORE_MAGE_ARMOR_SPELL_ID,
        )


def test_shield_name_without_source_bound_mechanic_is_not_executable() -> None:
    sheet = default_character_sheet()
    spell = _spell("homebrew-shield", level=1)
    spell["name"] = "Shield"
    spell["definition"]["casting_time"] = "1 reaction"
    sheet["content"]["spells"] = [spell]
    assert available_shield_attack_defenses(validate_character_sheet(sheet)) == []


def test_magic_missile_allocation_and_shield_trigger_are_source_bound() -> None:
    allocations = validate_magic_missile_allocations(
        [
            {"target_id": "goblin-a", "darts": 1},
            {"target_id": "goblin-b", "darts": 1},
            {"target_id": "goblin-a", "darts": 1},
        ],
        cast_level=1,
    )
    assert allocations == [
        {"target_id": "goblin-a", "darts": 2},
        {"target_id": "goblin-b", "darts": 1},
    ]
    with pytest.raises(ValueError, match="exactly 4 darts"):
        validate_magic_missile_allocations(
            [{"target_id": "goblin-a", "darts": 3}], cast_level=2
        )

    sheet = default_character_sheet()
    sheet["spellcasting"]["spell_slots"] = {
        "1": {
            "label": "1st",
            "value": 1,
            "max": 1,
            "recovers_on": "long_rest",
            "source_key": "wizard",
        }
    }
    shield = _spell(CORE_SHIELD_SPELL_ID, level=1)
    shield["name"] = "Shield"
    shield["definition"]["casting_time"] = "1 reaction"
    shield["mechanic_refs"] = [CORE_SHIELD_MECHANIC_ID]
    sheet["content"]["spells"] = [shield]
    candidates = available_shield_magic_missile_defenses(validate_character_sheet(sheet))
    assert candidates[0]["kind"] == "spell_magic_missile_immunity"

    unrelated = _spell(CORE_MAGIC_MISSILE_SPELL_ID, level=1)
    unrelated["mechanic_refs"] = [CORE_MAGIC_MISSILE_MECHANIC_ID]
    sheet["content"]["spells"] = [unrelated]
    assert available_shield_magic_missile_defenses(validate_character_sheet(sheet)) == []


def test_ritual_and_cantrip_do_not_spend_a_slot() -> None:
    sheet = default_character_sheet()
    sheet["spellcasting"]["ritual_casting"] = True
    spell = _spell("alarm", level=1)
    spell["access"]["ritual_available"] = True
    cantrip = _spell("light", level=0)
    sheet["content"]["spells"] = [spell, cantrip]
    sheet = validate_character_sheet(sheet)
    assert consume_spell_cast(sheet, spell_id="alarm", ritual=True)["payment"]["economy"] == "none"
    assert consume_spell_cast(sheet, spell_id="light")["payment"]["economy"] == "none"


def test_cantrip_and_ritual_reject_slot_levels() -> None:
    sheet = default_character_sheet()
    sheet["spellcasting"]["ritual_casting"] = True
    ritual = _spell("alarm", level=1)
    ritual["access"]["ritual_available"] = True
    sheet["content"]["spells"] = [ritual, _spell("light", level=0)]
    sheet = validate_character_sheet(sheet)
    with pytest.raises(ValueError, match="cantrips"):
        consume_spell_cast(sheet, spell_id="light", cast_level=1)
    with pytest.raises(ValueError, match="ritual casting"):
        consume_spell_cast(sheet, spell_id="alarm", ritual=True, cast_level=2)


def test_pact_magic_uses_its_recorded_slot_level() -> None:
    sheet = default_character_sheet()
    sheet["spellcasting"]["pact_magic"] = {
        "label": "Pact Magic",
        "value": 1,
        "max": 1,
        "slot_level": 3,
        "recovers_on": "short_rest",
        "source_key": "warlock",
    }
    sheet["content"]["spells"] = [_spell("fireball", level=3)]
    result = consume_spell_cast(validate_character_sheet(sheet), spell_id="fireball", cast_level=3)
    assert result["payment"]["economy"] == "pact_magic"
    assert result["cast_level"] == 3
    assert result["sheet"]["spellcasting"]["pact_magic"]["value"] == 0


def test_costly_material_component_requires_dm_confirmation() -> None:
    sheet = default_character_sheet()
    sheet["spellcasting"]["spell_slots"] = {
        "1": {"label": "1st", "value": 1, "max": 1, "recovers_on": "long_rest", "source_key": ""}
    }
    chromatic_orb = _spell("chromatic-orb", level=1)
    chromatic_orb["definition"]["components"] = {
        "material": True,
        "material_cost_cp": 5000,
        "consumed": False,
    }
    sheet["content"]["spells"] = [chromatic_orb]
    sheet = validate_character_sheet(sheet)
    with pytest.raises(ValueError, match="material_confirmed"):
        consume_spell_cast(sheet, spell_id="chromatic-orb")
    result = consume_spell_cast(
        sheet, spell_id="chromatic-orb", component_ruling={"material_confirmed": True}
    )
    assert "material_component" in result["ruling_required"]


def test_source_bound_spell_with_unknown_components_requires_confirmation_before_payment() -> None:
    sheet = default_character_sheet()
    sheet["spellcasting"]["spell_slots"] = {
        "1": {"label": "1st", "value": 1, "max": 1, "recovers_on": "long_rest", "source_key": ""}
    }
    spell = _spell("source-ray", level=1)
    spell["custom_definition"] = {
        "source": "module-review:master-of-souls",
        "component_details": "not_repeated_in_statblock",
    }
    sheet["content"]["spells"] = [spell]
    sheet = validate_character_sheet(sheet)

    with pytest.raises(ValueError, match="source_components_confirmed"):
        consume_spell_cast(sheet, spell_id="source-ray")

    assert sheet["spellcasting"]["spell_slots"]["1"]["value"] == 1
    result = consume_spell_cast(
        sheet,
        spell_id="source-ray",
        component_ruling={"source_components_confirmed": True},
    )
    assert result["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] == 0
    assert "source_components" in result["ruling_required"]


def test_readied_spell_pays_now_and_replaces_existing_concentration() -> None:
    sheet = default_character_sheet()
    sheet["spellcasting"]["spell_slots"] = {
        "1": {"label": "1st", "value": 1, "max": 1, "recovers_on": "long_rest", "source_key": ""}
    }
    sheet["content"]["spells"] = [_spell("magic-missile", level=1)]
    sheet["effects"] = [
        {
            "id": "old-concentration",
            "name": "Old",
            "kind": "concentration",
            "source": "spell.cast",
            "source_spell_id": "magic-missile",
            "active": True,
            "concentration": True,
            "duration": {"period": "minute", "remaining": 1},
            "changes": [],
            "description": "",
        }
    ]
    result = consume_readied_spell(validate_character_sheet(sheet), spell_id="magic-missile")
    assert result["payment"]["economy"] == "slots"
    assert result["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] == 0
    active = [effect for effect in result["sheet"]["effects"] if effect["active"]]
    assert len(active) == 1
    assert active[0]["id"] == result["holding_effect_id"]
    assert active[0]["kind"] == "readied_spell"
    old = next(
        effect
        for effect in result["sheet"]["effects"]
        if effect["id"] == "old-concentration"
    )
    assert old["ended_reason"] == "replaced_by_readied_spell"


def test_only_one_action_spells_can_be_readied() -> None:
    sheet = default_character_sheet()
    spell = _spell("healing-word", level=0)
    spell["definition"]["casting_time"] = "bonus action"
    sheet["content"]["spells"] = [spell]
    with pytest.raises(ValueError, match="one action"):
        consume_readied_spell(validate_character_sheet(sheet), spell_id="healing-word")


def test_readied_spell_requires_recorded_action_casting_time() -> None:
    sheet = default_character_sheet()
    spell = _spell("incomplete-spell", level=0)
    spell["definition"].pop("casting_time")
    sheet["content"]["spells"] = [spell]
    with pytest.raises(ValueError, match="one action"):
        consume_readied_spell(validate_character_sheet(sheet), spell_id="incomplete-spell")


def test_prepared_caster_cannot_cast_unprepared_known_spell() -> None:
    sheet = default_character_sheet()
    sheet["progression"] = {
        "level": 1,
        "classes": [{"name": "Cleric", "level": 1, "hit_die": 8}],
    }
    sheet["spellcasting"]["preparation"] = {
        "mode": "prepared",
        "max_prepared": 4,
        "changes_on": "long_rest",
        "selected_spell_ids": ["bless"],
    }
    bless = _spell("bless", level=1)
    command = _spell("command", level=1)
    sheet["content"]["spells"] = [bless, command]
    sheet = validate_character_sheet(sheet)
    with pytest.raises(ValueError, match="not available"):
        consume_spell_cast(sheet, spell_id="command")


def test_2024_ranger_long_rest_replaces_only_one_spell() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2024"
    sheet["progression"] = {
        "level": 5,
        "classes": [{"name": "Ranger", "level": 5, "hit_die": 10}],
    }
    sheet["spellcasting"]["preparation"] = {
        "mode": "prepared",
        "max_prepared": 6,
        "changes_on": "long_rest",
        "selected_spell_ids": ["a", "b"],
    }
    spells = []
    for spell_id in ("a", "b", "c", "d"):
        spell = _spell(spell_id, level=1)
        spell["grant"] = {"source_type": "class", "source_key": "ranger"}
        spells.append(spell)
    sheet["content"]["spells"] = spells
    sheet = validate_character_sheet(sheet)

    changed = replace_prepared_spells(sheet, spell_ids=["a", "c"], event="long_rest")
    assert changed["added"] == ["c"]
    assert changed["removed"] == ["b"]
    with pytest.raises(ValueError, match="only 1 spell"):
        replace_prepared_spells(sheet, spell_ids=["c", "d"], event="long_rest")
    with pytest.raises(ValueError, match="replaces spells"):
        replace_prepared_spells(sheet, spell_ids=["a", "b", "c"], event="long_rest")


def test_2014_ranger_uses_spells_known_instead_of_preparation() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["progression"] = {
        "level": 5,
        "classes": [{"name": "Ranger", "level": 5, "hit_die": 10}],
    }
    sheet["spellcasting"]["preparation"] = {
        "mode": "prepared",
        "max_prepared": 4,
        "changes_on": "long_rest",
        "selected_spell_ids": [],
    }
    spell = _spell("cure-wounds", level=1)
    spell["grant"] = {"source_type": "class", "source_key": "ranger"}
    sheet["content"]["spells"] = [spell]
    with pytest.raises(ValueError, match="spells known"):
        replace_prepared_spells(
            validate_character_sheet(sheet),
            spell_ids=["cure-wounds"],
            event="setup",
        )


def test_wizard_preparation_uses_authoritative_spellbook_membership() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2024"
    sheet["progression"] = {
        "level": 3,
        "classes": [{"name": "Wizard", "level": 3, "hit_die": 6}],
    }
    sheet["spellcasting"]["preparation"] = {
        "mode": "spellbook",
        "max_prepared": 6,
        "changes_on": "long_rest",
        "selected_spell_ids": [],
    }
    sheet["spellcasting"]["spellbook"] = {
        "enabled": True,
        "spell_ids": ["magic-missile"],
    }
    magic_missile = _spell("magic-missile", level=1)
    shield = _spell("shield", level=1)
    for spell in (magic_missile, shield):
        spell["grant"] = {"source_type": "class", "source_key": "wizard"}
        spell["access"]["in_spellbook"] = True
    sheet["content"]["spells"] = [magic_missile, shield]
    sheet = validate_character_sheet(sheet)
    assert sheet["content"]["spells"][0]["access"]["in_spellbook"] is True
    assert sheet["content"]["spells"][1]["access"]["in_spellbook"] is False
    with pytest.raises(ValueError, match="spellbook"):
        replace_prepared_spells(sheet, spell_ids=["shield"], event="setup")
