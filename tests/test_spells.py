import pytest

from sagasmith_dnd.character_schema import (
    default_character_sheet,
    derive_character_sheet,
    validate_character_sheet,
)
from sagasmith_dnd.combat_engine import (
    CombatEngineError,
    NeedsRulingError,
    _end_attack_broken_invisibility,
)
from sagasmith_dnd.lifecycle import advance_effect_durations
from sagasmith_dnd.spells import (
    CORE_2024_MAGE_ARMOR_SPELL_ID,
    CORE_MAGE_ARMOR_SPELL_ID,
    CORE_MAGIC_MISSILE_MECHANIC_ID,
    CORE_MAGIC_MISSILE_SPELL_ID,
    CORE_SHIELD_MECHANIC_ID,
    CORE_SHIELD_SPELL_ID,
    apply_core_fly_effects,
    apply_core_invisibility_effects,
    available_shield_attack_defenses,
    available_shield_magic_missile_defenses,
    consume_magic_item_spell_cast,
    consume_readied_spell,
    consume_shield_reaction,
    consume_spell_cast,
    fly_target_limit,
    invisibility_target_limit,
    recharge_magic_item_charges,
    reconcile_source_effect_dependencies,
    replace_prepared_spells,
    resolve_magic_item_last_charge,
    validate_magic_missile_allocations,
)
from sagasmith_dnd.standard_spell_ids import (
    CORE_2024_FLY_SPELL_ID,
    CORE_2024_INVISIBILITY_SPELL_ID,
    CORE_FLY_SPELL_ID,
    CORE_INVISIBILITY_SPELL_ID,
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


def test_ordinary_mage_armor_cast_applies_its_engine_owned_effect() -> None:
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
    spell = _spell(CORE_MAGE_ARMOR_SPELL_ID, level=1)
    spell["mechanic_refs"] = ["dnd5e.core.spell.mage_armor"]
    sheet["content"]["spells"] = [spell]

    result = consume_spell_cast(
        validate_character_sheet(sheet),
        spell_id=CORE_MAGE_ARMOR_SPELL_ID,
    )

    assert result["automatic_effect"] == "mage_armor"
    assert result["effect_id"]
    assert any(
        item["id"] == result["effect_id"] and item["active"]
        for item in result["sheet"]["effects"]
    )


def test_2024_mage_armor_uses_the_same_mechanic_without_borrowing_its_card_id() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2024"
    sheet["spellcasting"]["spell_slots"] = {
        "1": {
            "label": "1st",
            "value": 1,
            "max": 1,
            "recovers_on": "long_rest",
            "source_key": "wizard",
        }
    }
    spell = _spell(CORE_2024_MAGE_ARMOR_SPELL_ID, level=1)
    spell["mechanic_refs"] = ["dnd5e.core.spell.mage_armor"]
    sheet["content"]["spells"] = [spell]

    result = consume_spell_cast(
        validate_character_sheet(sheet),
        spell_id=CORE_2024_MAGE_ARMOR_SPELL_ID,
    )

    assert result["automatic_effect"] == "mage_armor"
    effect = next(
        item for item in result["sheet"]["effects"] if item["id"] == result["effect_id"]
    )
    assert effect["source_spell_id"] == CORE_2024_MAGE_ARMOR_SPELL_ID


def test_fly_applies_willing_target_speed_and_tracks_concentration() -> None:
    caster = default_character_sheet()
    caster["spellcasting"]["spell_slots"] = {
        "3": {
            "label": "3rd",
            "value": 1,
            "max": 1,
            "recovers_on": "long_rest",
            "source_key": "wizard",
        }
    }
    caster["content"]["spells"] = [
        _spell(CORE_FLY_SPELL_ID, level=3, concentration=True)
    ]
    target = default_character_sheet()
    paid = consume_spell_cast(
        validate_character_sheet(caster),
        spell_id=CORE_FLY_SPELL_ID,
        cast_level=3,
    )
    concentration = next(
        effect
        for effect in paid["sheet"]["effects"]
        if effect["active"] and effect["concentration"]
    )

    applied = apply_core_fly_effects(
        {"caster": paid["sheet"], "target": target},
        caster_id="caster",
        target_ids=["target"],
        willing_target_ids=["target"],
        spell_id=CORE_FLY_SPELL_ID,
        cast_level=3,
        concentration_effect_id=concentration["id"],
    )
    target_sheet = validate_character_sheet(applied["sheets"]["target"])

    assert fly_target_limit(3) == 1
    assert derive_character_sheet(target_sheet)["speed"]["fly"] == 60
    fly_effect = next(
        effect
        for effect in target_sheet["effects"]
        if effect["id"] == applied["effect_ids"]["target"]
    )
    assert fly_effect["dependency"] == "source_effect_active"
    assert fly_effect["source_actor_id"] == "caster"
    assert fly_effect["source_effect_id"] == concentration["id"]


def test_2024_fly_and_invisibility_preserve_the_exact_source_spell_ids() -> None:
    fly_caster = default_character_sheet()
    fly_caster["edition"] = "2024"
    fly_caster["effects"] = [
        {
            "id": "fly-source",
            "name": "Concentrating: Fly",
            "kind": "concentration",
            "source": "spell.cast",
            "source_spell_id": CORE_2024_FLY_SPELL_ID,
            "active": True,
            "concentration": True,
            "duration": {"period": "minute", "remaining": 10},
            "changes": [],
            "description": "",
        }
    ]
    invisibility_caster = default_character_sheet()
    invisibility_caster["edition"] = "2024"
    invisibility_caster["effects"] = [
        {
            "id": "invisibility-source",
            "name": "Concentrating: Invisibility",
            "kind": "concentration",
            "source": "spell.cast",
            "source_spell_id": CORE_2024_INVISIBILITY_SPELL_ID,
            "active": True,
            "concentration": True,
            "duration": {"period": "hour", "remaining": 1},
            "changes": [],
            "description": "",
        },
    ]
    fly_caster = validate_character_sheet(fly_caster)
    invisibility_caster = validate_character_sheet(invisibility_caster)
    target = default_character_sheet()
    target["edition"] = "2024"

    flew = apply_core_fly_effects(
        {"caster": fly_caster, "target": target},
        caster_id="caster",
        target_ids=["target"],
        willing_target_ids=["target"],
        spell_id=CORE_2024_FLY_SPELL_ID,
        cast_level=3,
        concentration_effect_id="fly-source",
    )
    invisible = apply_core_invisibility_effects(
        {"caster": invisibility_caster, "target": target},
        caster_id="caster",
        target_ids=["target"],
        spell_id=CORE_2024_INVISIBILITY_SPELL_ID,
        cast_level=2,
        concentration_effect_id="invisibility-source",
    )

    assert next(
        effect
        for effect in flew["sheets"]["target"]["effects"]
        if effect["id"] == flew["effect_ids"]["target"]
    )["source_spell_id"] == CORE_2024_FLY_SPELL_ID
    assert next(
        effect
        for effect in invisible["sheets"]["target"]["effects"]
        if effect["id"] == invisible["effect_ids"]["target"]
    )["source_spell_id"] == CORE_2024_INVISIBILITY_SPELL_ID


def test_fly_upcast_target_limit_and_source_dependency_are_hard_settled() -> None:
    caster = default_character_sheet()
    caster["effects"] = [
        {
            "id": "fly-concentration",
            "name": "Concentrating: Fly",
            "kind": "concentration",
            "source": "spell.cast",
            "source_spell_id": CORE_FLY_SPELL_ID,
            "active": True,
            "concentration": True,
            "duration": {"period": "minute", "remaining": 10},
            "changes": [],
            "description": "",
        }
    ]
    sheets = {
        "caster": validate_character_sheet(caster),
        **{
            f"target-{index}": default_character_sheet()
            for index in range(1, 5)
        },
    }
    with pytest.raises(CombatEngineError, match="target count exceeds"):
        apply_core_fly_effects(
            sheets,
            caster_id="caster",
            target_ids=["target-1", "target-2", "target-3", "target-4"],
            willing_target_ids=[
                "target-1",
                "target-2",
                "target-3",
                "target-4",
            ],
            spell_id=CORE_FLY_SPELL_ID,
            cast_level=5,
            concentration_effect_id="fly-concentration",
        )

    applied = apply_core_fly_effects(
        sheets,
        caster_id="caster",
        target_ids=["target-1", "target-2", "target-3"],
        willing_target_ids=["target-1", "target-2", "target-3"],
        spell_id=CORE_FLY_SPELL_ID,
        cast_level=5,
        concentration_effect_id="fly-concentration",
    )
    assert applied["target_limit"] == 3
    applied["sheets"]["caster"]["effects"][0]["active"] = False
    applied["sheets"]["caster"]["effects"][0][
        "ended_reason"
    ] = "failed_concentration_save"
    reconciled = reconcile_source_effect_dependencies(applied["sheets"])

    assert reconciled["changed_actor_ids"] == [
        "target-1",
        "target-2",
        "target-3",
    ]
    assert all(
        derive_character_sheet(
            validate_character_sheet(reconciled["sheets"][target_id])
        )["speed"]["fly"]
        == 0
        for target_id in reconciled["changed_actor_ids"]
    )


def test_invisibility_applies_to_explicit_targets_and_tracks_concentration() -> None:
    caster = default_character_sheet()
    caster["spellcasting"]["spell_slots"] = {
        "2": {
            "label": "2nd",
            "value": 1,
            "max": 1,
            "recovers_on": "long_rest",
            "source_key": "bard",
        }
    }
    invisibility = _spell(
        CORE_INVISIBILITY_SPELL_ID,
        level=2,
        concentration=True,
    )
    invisibility["definition"]["duration"] = {
        "kind": "timed",
        "value": 1,
        "unit": "hour",
        "concentration": True,
    }
    caster["content"]["spells"] = [invisibility]
    paid = consume_spell_cast(
        validate_character_sheet(caster),
        spell_id=CORE_INVISIBILITY_SPELL_ID,
        cast_level=2,
    )
    concentration = next(
        effect
        for effect in paid["sheet"]["effects"]
        if effect["active"] and effect["concentration"]
    )

    applied = apply_core_invisibility_effects(
        {
            "caster": paid["sheet"],
            "target": default_character_sheet(),
        },
        caster_id="caster",
        target_ids=["target"],
        spell_id=CORE_INVISIBILITY_SPELL_ID,
        cast_level=2,
        concentration_effect_id=concentration["id"],
    )
    target = validate_character_sheet(applied["sheets"]["target"])

    assert invisibility_target_limit(2) == 1
    assert "invisible" in target["conditions"]
    effect = next(
        item
        for item in target["effects"]
        if item["id"] == applied["effect_ids"]["target"]
    )
    assert effect["duration"] == {"period": "hour", "remaining": 1}
    assert effect["dependency"] == "source_effect_active"
    assert effect["source_effect_id"] == concentration["id"]


def test_upcast_invisibility_targets_end_independently_and_with_the_source() -> None:
    caster = default_character_sheet()
    caster["effects"] = [
        {
            "id": "invisibility-concentration",
            "name": "Concentrating: Invisibility",
            "kind": "concentration",
            "source": "spell.cast",
            "source_spell_id": CORE_INVISIBILITY_SPELL_ID,
            "active": True,
            "concentration": True,
            "duration": {"period": "hour", "remaining": 1},
            "changes": [],
            "description": "",
        }
    ]
    sheets = {
        "caster": validate_character_sheet(caster),
        "target-1": default_character_sheet(),
        "target-2": default_character_sheet(),
    }
    applied = apply_core_invisibility_effects(
        sheets,
        caster_id="caster",
        target_ids=["target-1", "target-2"],
        spell_id=CORE_INVISIBILITY_SPELL_ID,
        cast_level=3,
        concentration_effect_id="invisibility-concentration",
    )

    ended = _end_attack_broken_invisibility(
        applied["sheets"]["target-1"]
    )
    assert ended == [applied["effect_ids"]["target-1"]]
    assert "invisible" not in applied["sheets"]["target-1"]["conditions"]
    assert "invisible" in applied["sheets"]["target-2"]["conditions"]
    assert applied["sheets"]["caster"]["effects"][0]["active"] is True

    applied["sheets"]["caster"]["effects"][0]["active"] = False
    applied["sheets"]["caster"]["effects"][0][
        "ended_reason"
    ] = "failed_concentration_save"
    reconciled = reconcile_source_effect_dependencies(applied["sheets"])

    assert reconciled["changed_actor_ids"] == ["target-2"]
    assert "invisible" not in reconciled["sheets"]["target-2"]["conditions"]
    assert (
        reconciled["sheets"]["target-2"]["effects"][0]["ended_reason"]
        == "source_effect_ended"
    )


def test_innate_spell_cast_spends_per_spell_daily_use_and_starts_concentration() -> None:
    sheet = default_character_sheet()
    suggestion = _spell("suggestion", level=2, concentration=True)
    suggestion["grant"] = {
        "source_type": "statblock",
        "source_key": "monster-manual:yuan-ti-malison",
        "method": "innate",
    }
    suggestion["access"].update(
        {
            "known": True,
            "prepared": True,
            "always_prepared": True,
            "at_will": False,
        }
    )
    sheet["content"]["spells"] = [suggestion]
    sheet["resources"]["innate_spell:suggestion"] = {
        "label": "Suggestion (3/day)",
        "value": 3,
        "max": 3,
        "recovers_on": "long_rest",
        "source_key": "monster-manual:yuan-ti-malison",
    }

    result = consume_spell_cast(
        validate_character_sheet(sheet),
        spell_id="suggestion",
    )

    assert result["payment"] == {
        "economy": "innate_spell",
        "resource_key": "innate_spell:suggestion",
        "level": 2,
        "ritual": False,
    }
    assert result["sheet"]["resources"]["innate_spell:suggestion"]["value"] == 2
    assert result["concentration_started"] is True


def test_casting_a_spell_ends_only_the_exact_invisibility_spell() -> None:
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
    invisibility = _spell(
        "dnd5e.content.srd2014.spell.invisibility",
        level=2,
        concentration=True,
    )
    sheet["content"]["spells"] = [
        _spell("magic-missile", level=1),
        invisibility,
    ]
    sheet["conditions"] = ["invisible"]
    sheet["effects"] = [
        {
            "id": "invisibility-effect",
            "name": "Concentrating: Invisibility",
            "kind": "concentration",
            "source": "spell.cast",
            "source_spell_id": "dnd5e.content.srd2014.spell.invisibility",
            "active": True,
            "concentration": True,
            "duration": {"period": "hour", "remaining": 1},
            "changes": [],
        }
    ]

    result = consume_spell_cast(
        validate_character_sheet(sheet),
        spell_id="magic-missile",
    )

    assert result["ended_invisibility_effect_ids"] == ["invisibility-effect"]
    assert "invisible" not in result["sheet"]["conditions"]
    effect = result["sheet"]["effects"][0]
    assert effect["active"] is False
    assert effect["ended_reason"] == "actor_cast_spell"


def test_casting_preserves_invisible_condition_owned_by_another_effect() -> None:
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
    sheet["content"]["spells"] = [
        _spell("magic-missile", level=1),
        _spell(
            "dnd5e.content.srd2014.spell.invisibility",
            level=2,
            concentration=True,
        ),
    ]
    sheet["conditions"] = ["invisible"]
    sheet["effects"] = [
        {
            "id": "invisibility-spell",
            "name": "Concentrating: Invisibility",
            "kind": "concentration",
            "source": "spell.cast",
            "source_spell_id": "dnd5e.content.srd2014.spell.invisibility",
            "active": True,
            "concentration": True,
            "duration": {"period": "hour", "remaining": 1},
            "changes": [],
        },
        {
            "id": "other-invisibility",
            "name": "Other Invisibility",
            "kind": "timed_conditions",
            "source": "feature",
            "active": True,
            "concentration": False,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [{"path": "conditions", "mode": "add", "value": "invisible"}],
            "description": "",
        },
    ]

    result = consume_spell_cast(
        validate_character_sheet(sheet),
        spell_id="magic-missile",
    )

    assert result["ended_invisibility_effect_ids"] == ["invisibility-spell"]
    assert "invisible" in result["sheet"]["conditions"]


def test_magic_item_spell_cast_also_ends_invisibility() -> None:
    sheet = default_character_sheet()
    item_spell = _spell("module-spell", level=1)
    item_spell.update(
        pack_id="dnd5e.module",
        pack_version="1.0.0",
        rule_refs=["module-chunk:item-spell"],
    )
    sheet["inventory"]["items"] = [
        {
            "id": "spell-wand",
            "name": "Spell Wand",
            "kind": "magic_item",
            "equipped": True,
            "equipped_slot": "main_hand",
            "charges": {
                "label": "Charges",
                "value": 2,
                "max": 2,
                "recovers_on": "dawn",
                "source_key": "module-chunk:item-spell",
            },
            "mechanics": {
                "spellcasting": {
                    "requires_attunement": False,
                    "requires_class_spell_list": False,
                    "components_required": False,
                    "spells": [
                        {
                            "artifact_id": "module-spell",
                            "charge_cost": 1,
                            "casting_time": "1 action",
                            "card": item_spell,
                        }
                    ],
                }
            },
        }
    ]
    sheet["inventory"]["equipment_slots"]["main_hand"] = "spell-wand"
    sheet["content"]["spells"] = [_spell("invisibility", level=2, concentration=True)]
    sheet["conditions"] = ["invisible"]
    sheet["effects"] = [
        {
            "id": "invisibility-effect",
            "name": "Concentrating: Invisibility",
            "kind": "concentration",
            "source": "spell.cast",
            "source_spell_id": "invisibility",
            "active": True,
            "concentration": True,
            "duration": {"period": "hour", "remaining": 1},
            "changes": [],
        }
    ]

    result = consume_magic_item_spell_cast(
        validate_character_sheet(sheet),
        source_item_id="spell-wand",
        spell_id="module-spell",
    )

    assert result["ended_invisibility_effect_ids"] == ["invisibility-effect"]
    assert "invisible" not in result["sheet"]["conditions"]
    assert result["sheet"]["effects"][0]["ended_reason"] == "actor_cast_spell"


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


def test_magic_item_concentration_spell_replaces_existing_concentration() -> None:
    sheet = default_character_sheet()
    sheet["spellcasting"]["class_lists"] = ["wizard"]
    web = _spell("core:spell/web", level=2, concentration=True)
    web.update(
        name="Web",
        classes=["wizard"],
        pack_id="dnd5e.content.srd2014",
        pack_version="1.6.0",
        rule_refs=["bundled:srd2014/spells/web"],
    )
    sheet["inventory"]["items"] = [
        {
            "id": "wand-of-web",
            "name": "Wand of Web",
            "kind": "magic_item",
            "equipped": True,
            "equipped_slot": "main_hand",
            "attunement": "attuned",
            "charges": {
                "label": "Wand charges",
                "value": 3,
                "max": 3,
                "recovers_on": "dawn",
                "source_key": "module-chunk:wand-of-web",
            },
            "source_key": "module-chunk:wand-of-web",
            "mechanics": {
                "spellcasting": {
                    "requires_attunement": True,
                    "requires_class_spell_list": True,
                    "components_required": False,
                    "spells": [
                        {
                            "artifact_id": "core:spell/web",
                            "charge_cost": 1,
                            "casting_time": "1 action",
                            "card": web,
                        }
                    ],
                }
            },
        }
    ]
    sheet["inventory"]["equipment_slots"]["main_hand"] = "wand-of-web"
    sheet["effects"] = [
        {
            "id": "old-concentration",
            "name": "Old concentration",
            "kind": "concentration",
            "source": "spell.cast",
            "source_spell_id": "",
            "active": True,
            "concentration": True,
            "duration": {"period": "minute", "remaining": 1},
            "changes": [],
            "description": "",
        }
    ]

    result = consume_magic_item_spell_cast(
        validate_character_sheet(sheet),
        source_item_id="wand-of-web",
        spell_id="core:spell/web",
    )

    assert result["concentration_started"] is True
    assert result["sheet"]["inventory"]["items"][0]["charges"]["value"] == 2
    active = [
        effect
        for effect in result["sheet"]["effects"]
        if effect["active"] and effect["concentration"]
    ]
    assert len(active) == 1
    assert active[0]["source"] == "magic_item:wand-of-web"
    assert active[0]["source_spell_id"] == "core:spell/web"
    old = next(
        effect for effect in result["sheet"]["effects"] if effect["id"] == "old-concentration"
    )
    assert old["active"] is False
    assert old["ended_reason"] == "replaced_by_concentration"


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
        validate_magic_missile_allocations([{"target_id": "goblin-a", "darts": 3}], cast_level=2)

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


def test_mystic_arcanum_spends_its_own_long_rest_resource() -> None:
    sheet = default_character_sheet()
    arcanum = _spell("mass-suggestion", level=6)
    arcanum["grant"] = {
        "source_type": "feature",
        "source_key": "Warlock",
        "method": "mystic_arcanum",
    }
    sheet["content"]["spells"] = [arcanum]
    sheet["resources"]["mystic_arcanum:mass-suggestion"] = {
        "label": "Mystic Arcanum: Mass Suggestion",
        "value": 1,
        "max": 1,
        "recovers_on": "long_rest",
        "source_key": "Warlock",
    }

    result = consume_spell_cast(
        validate_character_sheet(sheet),
        spell_id="mass-suggestion",
        cast_level=6,
    )

    assert result["payment"] == {
        "economy": "mystic_arcanum",
        "resource_key": "mystic_arcanum:mass-suggestion",
        "level": 6,
        "ritual": False,
    }
    assert result["sheet"]["resources"]["mystic_arcanum:mass-suggestion"]["value"] == 0
    with pytest.raises(ValueError, match="unavailable"):
        consume_spell_cast(result["sheet"], spell_id="mass-suggestion")


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
    with pytest.raises(NeedsRulingError, match="material_confirmed") as raised:
        consume_spell_cast(sheet, spell_id="chromatic-orb")
    assert raised.value.ruling_kind == "source_or_scene_fact"
    assert raised.value.missing == ("material_component",)
    result = consume_spell_cast(
        sheet, spell_id="chromatic-orb", component_ruling={"material_confirmed": True}
    )
    assert "material_component" in result["ruling_required"]
    assert {
        item["default_resolver"] for item in result["ruling_requirements"]
    } == {"agent"}
    assert {
        item["ruling_kind"] for item in result["ruling_requirements"]
    } == {"generic_spell_effect"}


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

    with pytest.raises(NeedsRulingError, match="source_components_confirmed") as raised:
        consume_spell_cast(sheet, spell_id="source-ray")
    assert raised.value.ruling_kind == "missing_or_conflicting_source_review"
    assert raised.value.missing == ("source_components",)

    assert sheet["spellcasting"]["spell_slots"]["1"]["value"] == 1
    result = consume_spell_cast(
        sheet,
        spell_id="source-ray",
        component_ruling={"source_components_confirmed": True},
    )
    assert result["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] == 0
    assert "source_components" in result["ruling_required"]
    assert next(
        item
        for item in result["ruling_requirements"]
        if item["kind"] == "source_components"
    ) == {
        "kind": "source_components",
        "default_resolver": "agent",
        "ruling_kind": "generic_spell_effect",
    }


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
        effect for effect in result["sheet"]["effects"] if effect["id"] == "old-concentration"
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


def test_spell_mastery_requires_preparation_and_upcasts_spend_slots() -> None:
    sheet = default_character_sheet()
    sheet["progression"] = {
        "level": 18,
        "classes": [{"name": "Wizard", "level": 18, "hit_die": 6}],
    }
    sheet["spellcasting"]["preparation"] = {
        "mode": "spellbook",
        "max_prepared": 23,
        "changes_on": "long_rest",
        "selected_spell_ids": [],
    }
    sheet["spellcasting"]["spell_slots"] = {
        "2": {"value": 1, "max": 3, "recovers_on": "long_rest", "slot_level": 2}
    }
    spell = _spell("shield", level=1)
    spell["access"].update(
        {"known": True, "prepared": False, "in_spellbook": True, "at_will": True}
    )
    sheet["content"]["spells"] = [spell]
    sheet["content"]["features"] = [
        {
            "id": "spell-mastery",
            "name": "Spell Mastery",
            "choices": {"spell_artifact_ids": ["shield", "misty-step"]},
        }
    ]

    with pytest.raises(ValueError, match="not available"):
        consume_spell_cast(sheet, spell_id="shield")

    sheet["content"]["spells"][0]["access"]["prepared"] = True
    base_cast = consume_spell_cast(sheet, spell_id="shield", cast_level=1)
    assert base_cast["payment"]["economy"] == "none"
    upcast = consume_spell_cast(sheet, spell_id="shield", cast_level=2)
    assert upcast["payment"]["economy"] == "slots"
    assert upcast["sheet"]["spellcasting"]["spell_slots"]["2"]["value"] == 0


def test_invocation_at_will_spell_cannot_be_upcast_for_free() -> None:
    sheet = default_character_sheet()
    spell = _spell("false-life", level=1)
    spell["access"].update({"at_will": True, "known": False, "prepared": False})
    spell["grant"] = {
        "source_type": "feature",
        "source_key": "Fiendish Vigor",
        "method": "eldritch_invocation",
    }
    sheet["content"]["spells"] = [spell]

    with pytest.raises(ValueError, match="lowest level"):
        consume_spell_cast(sheet, spell_id="false-life", cast_level=2)


def test_at_will_spell_with_independent_known_access_can_upcast_with_a_slot() -> None:
    sheet = default_character_sheet()
    sheet["spellcasting"]["spell_slots"] = {
        "2": {"value": 1, "max": 1, "recovers_on": "long_rest", "slot_level": 2}
    }
    spell = _spell("false-life", level=1)
    spell["access"].update({"at_will": True, "known": True})
    sheet["content"]["spells"] = [spell]

    result = consume_spell_cast(sheet, spell_id="false-life", cast_level=2)

    assert result["payment"]["economy"] == "slots"
    assert result["sheet"]["spellcasting"]["spell_slots"]["2"]["value"] == 0


def test_signature_spell_free_use_is_explicit_and_limited_to_third_level() -> None:
    sheet = default_character_sheet()
    sheet["progression"] = {
        "level": 20,
        "classes": [{"name": "Wizard", "level": 20, "hit_die": 6}],
    }
    sheet["spellcasting"]["preparation"] = {
        "mode": "spellbook",
        "max_prepared": 25,
        "changes_on": "long_rest",
        "selected_spell_ids": [],
    }
    sheet["spellcasting"]["spell_slots"] = {
        "3": {"value": 1, "max": 3, "recovers_on": "long_rest", "slot_level": 3},
        "4": {"value": 1, "max": 3, "recovers_on": "long_rest", "slot_level": 4},
    }
    spell = _spell("fireball", level=3)
    spell["access"].update(
        {
            "known": True,
            "prepared": True,
            "always_prepared": True,
            "in_spellbook": True,
        }
    )
    sheet["content"]["spells"] = [spell]
    sheet["content"]["features"] = [
        {
            "id": "signature-spells",
            "name": "Signature Spells",
            "choices": {"spell_artifact_ids": ["fireball", "counterspell"]},
        }
    ]
    sheet["resources"]["signature_spell:fireball"] = {
        "label": "Signature Spell: Fireball",
        "value": 1,
        "max": 1,
        "recovers_on": "short_rest",
        "source_key": "Wizard",
    }

    ordinary = consume_spell_cast(sheet, spell_id="fireball", cast_level=3)
    assert ordinary["payment"]["economy"] == "slots"
    assert ordinary["sheet"]["resources"]["signature_spell:fireball"]["value"] == 1

    free = consume_spell_cast(
        sheet,
        spell_id="fireball",
        cast_level=3,
        signature_free_cast=True,
    )
    assert free["payment"]["economy"] == "signature_spell"
    assert free["sheet"]["resources"]["signature_spell:fireball"]["value"] == 0

    with pytest.raises(ValueError, match="3rd level"):
        consume_spell_cast(
            sheet,
            spell_id="fireball",
            cast_level=4,
            signature_free_cast=True,
        )


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


def test_preparation_rejects_illegal_event_and_class_timing() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["progression"] = {
        "level": 3,
        "classes": [{"name": "Cleric", "level": 3, "hit_die": 8}],
    }
    sheet["abilities"]["wisdom"]["score"] = 16
    sheet["spellcasting"]["preparation"] = {
        "mode": "prepared",
        "max_prepared": 6,
        "changes_on": "long_rest",
        "selected_spell_ids": ["bless"],
    }
    bless = _spell("bless", level=1)
    aid = _spell("aid", level=2)
    for spell in (bless, aid):
        spell["grant"] = {"source_type": "class", "source_key": "cleric"}
    sheet["content"]["spells"] = [bless, aid]
    sheet = validate_character_sheet(sheet)

    with pytest.raises(ValueError, match="only when finishing a long rest"):
        replace_prepared_spells(
            sheet,
            spell_ids=["bless", "aid"],
            event="level_up",
        )
    with pytest.raises(ValueError, match="setup, long_rest, or level_up"):
        replace_prepared_spells(
            sheet,
            spell_ids=["bless", "aid"],
            event="scene_change",
        )

    changed = replace_prepared_spells(
        sheet,
        spell_ids=["bless", "aid"],
        event="long_rest",
    )
    assert changed["added"] == ["aid"]
    assert changed["preparation_minutes"] == 3


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
