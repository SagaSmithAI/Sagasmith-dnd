import pytest

import sagasmith_dnd.progression as progression_module
from sagasmith_dnd.character_schema import default_character_sheet, validate_character_sheet
from sagasmith_dnd.combat_engine import CombatEngineError
from sagasmith_dnd.progression import (
    advance_single_class_level,
    apply_constitution_score_hit_point_change,
    apply_per_level_hit_point_bonus,
    award_experience,
    experience_status,
    synchronize_class_feature_resources,
)


class _SequenceRng:
    def __init__(self, *values: int) -> None:
        self.values = list(values)

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0)
        assert minimum <= value <= maximum
        return value


def test_bard_magical_secrets_are_not_double_counted_as_class_list_spells() -> None:
    assert progression_module._spell_choice_delta("Bard", 9, 10) == {
        "cantrips_to_add": 1,
        "leveled_spells_to_add": 0,
    }
    assert progression_module._spell_choice_delta("Bard", 13, 14)["leveled_spells_to_add"] == 0
    assert progression_module._spell_choice_delta("Bard", 17, 18)["leveled_spells_to_add"] == 0


def _single_class_sheet(
    class_name: str, *, hit_die: int, constitution: int, hp: tuple[int, int]
) -> dict:
    sheet = default_character_sheet()
    sheet["progression"]["classes"] = [
        {"name": class_name, "level": 1, "subclass": "", "hit_die": hit_die}
    ]
    sheet["abilities"]["constitution"]["score"] = constitution
    sheet["combat"]["hp"] = {"value": hp[0], "max": hp[1], "temp": 0}
    sheet["combat"]["hit_dice"] = {
        f"d{hit_die}": {
            "label": f"d{hit_die}",
            "value": 1,
            "max": 1,
            "recovers_on": "long_rest",
            "source_key": class_name,
        }
    }
    return sheet


def test_fixed_level_advancement_updates_max_hp_and_hit_die_without_healing() -> None:
    sheet = _single_class_sheet("Rogue", hit_die=8, constitution=14, hp=(5, 10))

    result = advance_single_class_level(
        sheet,
        class_name="Rogue",
        hp_method="fixed",
        source="module milestone",
    )

    updated = validate_character_sheet(result["sheet"])
    assert updated["progression"]["level"] == 2
    assert updated["progression"]["classes"][0]["level"] == 2
    assert updated["combat"]["hp"] == {"value": 5, "max": 17, "temp": 0}
    assert updated["combat"]["hit_dice"]["d8"]["value"] == 2
    assert updated["combat"]["hit_dice"]["d8"]["max"] == 2
    assert updated["combat"]["hp_progression"] == [
        {"level": 2, "method": "fixed", "value": 7, "source": "module milestone"}
    ]
    assert result["spellcasting"]["kind"] == "none"
    assert result["spell_choices"] == {"cantrips_to_add": 0, "leveled_spells_to_add": 0}
    assert sheet["progression"]["level"] == 1


def test_level_advancement_keeps_machine_source_separate_from_display_source() -> None:
    sheet = _single_class_sheet("Rogue", hit_die=8, constitution=14, hp=(10, 10))
    source_ref = '{"content_sha256":"' + ("a" * 64) + '","heading_path":[' + (
        '"long heading",' * 30
    ).rstrip(",") + "]}"
    reason = "Reached the module milestone after resolving the chapter."

    result = advance_single_class_level(
        sheet,
        class_name="Rogue",
        hp_method="fixed",
        source="Rogue level 2",
        source_ref=source_ref,
        reason=reason,
    )

    updated = validate_character_sheet(result["sheet"])
    gain = updated["combat"]["hp_progression"][-1]
    assert gain["source"] == "Rogue level 2"
    assert gain["source_ref"] == source_ref
    assert gain["reason"] == reason


def test_feature_resources_scale_without_refilling_spent_capacity() -> None:
    sheet = _single_class_sheet("Fighter", hit_die=10, constitution=14, hp=(12, 12))
    sheet["progression"]["level"] = 16
    sheet["progression"]["classes"][0]["level"] = 16
    sheet["combat"]["hit_dice"]["d10"]["value"] = 8
    sheet["combat"]["hit_dice"]["d10"]["max"] = 16
    sheet["content"]["features"] = [
        {
            "id": "action-surge",
            "name": "Action Surge",
            "source_key": "Fighter",
            "uses": {
                "label": "Action Surge",
                "value": 0,
                "max": 1,
                "recovers_on": "short_rest",
                "source_key": "Fighter",
            },
            "resource_scaling": {
                "target": "uses",
                "label": "Action Surge",
                "class_name": "Fighter",
                "maximum_by_level": {"2": 1, "17": 2},
                "recovers_on": "short_rest",
                "recovery_by_level": {},
            },
        }
    ]

    result = advance_single_class_level(
        sheet,
        class_name="Fighter",
        hp_method="fixed",
    )

    uses = result["sheet"]["content"]["features"][0]["uses"]
    assert uses["max"] == 2
    assert uses["value"] == 1
    assert result["feature_resource_changes"] == [
        {
            "feature_id": "action-surge",
            "target": "uses",
            "class_level": 17,
            "old_max": 1,
            "new_max": 2,
            "old_value": 0,
            "new_value": 1,
            "recovers_on": "short_rest",
            "unlimited": False,
        }
    ]


def test_feature_resource_formula_reacts_to_ability_changes_and_unlimited_levels() -> None:
    sheet = _single_class_sheet("Bard", hit_die=8, constitution=12, hp=(8, 8))
    sheet["abilities"]["charisma"]["score"] = 16
    sheet["content"]["features"] = [
        {
            "id": "bardic-inspiration",
            "name": "Bardic Inspiration",
            "source_key": "Bard",
            "uses": {
                "label": "Bardic Inspiration",
                "value": 1,
                "max": 1,
                "recovers_on": "long_rest",
                "source_key": "Bard",
            },
            "resource_scaling": {
                "target": "uses",
                "label": "Bardic Inspiration",
                "class_name": "Bard",
                "maximum_by_level": {},
                "maximum_formula": {
                    "kind": "ability_modifier",
                    "ability": "charisma",
                    "minimum": 1,
                    "multiplier": 1,
                    "offset": 0,
                },
                "recovers_on": "long_rest",
                "recovery_by_level": {"5": "short_rest"},
            },
        }
    ]

    synchronized = synchronize_class_feature_resources(sheet)

    assert synchronized["sheet"]["content"]["features"][0]["uses"] == {
        "label": "Bardic Inspiration",
        "value": 3,
        "max": 3,
        "unlimited": False,
        "recovers_on": "long_rest",
        "source_key": "Bard",
        "slot_level": 0,
    }


def test_feature_resource_sync_removes_only_unreferenced_shadow_counter() -> None:
    sheet = _single_class_sheet("Bard", hit_die=8, constitution=12, hp=(8, 8))
    sheet["abilities"]["charisma"]["score"] = 20
    sheet["resources"] = {
        "bardic_inspiration": {
            "label": "Bardic Inspiration",
            "value": 3,
            "max": 3,
            "recovers_on": "long_rest",
            "source_key": "Bard",
        },
        "shared_inspiration": {
            "label": "Bardic Inspiration",
            "value": 1,
            "max": 1,
            "recovers_on": "long_rest",
            "source_key": "Bard",
        },
    }
    sheet["content"]["features"] = [
        {
            "id": "bardic-inspiration",
            "name": "Bardic Inspiration",
            "source_key": "Bard",
            "uses": {
                "label": "Bardic Inspiration",
                "value": 2,
                "max": 3,
                "recovers_on": "long_rest",
                "source_key": "Bard",
            },
            "resource_scaling": {
                "target": "uses",
                "label": "Bardic Inspiration",
                "class_name": "Bard",
                "maximum_by_level": {},
                "maximum_formula": {
                    "kind": "ability_modifier",
                    "ability": "charisma",
                    "minimum": 1,
                    "multiplier": 1,
                    "offset": 0,
                },
                "recovers_on": "long_rest",
                "recovery_by_level": {"5": "short_rest"},
            },
        },
        {
            "id": "shared-inspiration-consumer",
            "name": "Shared Inspiration Consumer",
            "resource_key": "shared_inspiration",
        },
    ]

    synchronized = synchronize_class_feature_resources(sheet)

    assert synchronized["sheet"]["content"]["features"][0]["uses"] == {
        "label": "Bardic Inspiration",
        "value": 4,
        "max": 5,
        "unlimited": False,
        "recovers_on": "long_rest",
        "source_key": "Bard",
        "slot_level": 0,
    }
    assert "bardic_inspiration" not in synchronized["sheet"]["resources"]
    assert synchronized["sheet"]["resources"]["shared_inspiration"]["value"] == 1
    assert synchronized["changes"][-1] == {
        "feature_id": "bardic-inspiration",
        "target": "resources.bardic_inspiration",
        "operation": "remove_shadow",
        "old_resource": {
            "label": "Bardic Inspiration",
            "value": 3,
            "max": 3,
            "recovers_on": "long_rest",
            "source_key": "Bard",
        },
    }


def test_zero_capacity_and_unlimited_class_resources_remain_distinct() -> None:
    paladin = _single_class_sheet("Paladin", hit_die=10, constitution=12, hp=(10, 10))
    paladin["abilities"]["charisma"]["score"] = 6
    paladin["content"]["features"] = [
        {
            "id": "divine-sense",
            "name": "Divine Sense",
            "uses": {"value": 0, "max": 0, "unlimited": False},
            "resource_scaling": {
                "target": "uses",
                "label": "Divine Sense",
                "class_name": "Paladin",
                "maximum_by_level": {},
                "maximum_formula": {
                    "kind": "ability_modifier",
                    "ability": "charisma",
                    "minimum": 0,
                    "multiplier": 1,
                    "offset": 1,
                },
                "recovers_on": "long_rest",
                "recovery_by_level": {},
            },
        }
    ]
    paladin_result = synchronize_class_feature_resources(paladin)
    assert paladin_result["sheet"]["content"]["features"][0]["uses"]["max"] == 0
    assert paladin_result["sheet"]["content"]["features"][0]["uses"]["unlimited"] is False

    barbarian = _single_class_sheet("Barbarian", hit_die=12, constitution=14, hp=(14, 14))
    barbarian["progression"]["level"] = 20
    barbarian["progression"]["classes"][0]["level"] = 20
    barbarian["content"]["features"] = [
        {
            "id": "rage",
            "name": "Rage",
            "uses": {"value": 0, "max": 6, "unlimited": False},
            "resource_scaling": {
                "target": "uses",
                "label": "Rage",
                "class_name": "Barbarian",
                "maximum_by_level": {"1": 2, "17": 6},
                "unlimited_at_level": 20,
                "recovers_on": "long_rest",
                "recovery_by_level": {},
            },
        }
    ]
    barbarian_result = synchronize_class_feature_resources(barbarian)
    assert barbarian_result["sheet"]["content"]["features"][0]["uses"]["max"] == 0
    assert barbarian_result["sheet"]["content"]["features"][0]["uses"]["unlimited"] is True


def test_extra_attack_scaling_uses_the_highest_class_feature_without_stacking() -> None:
    sheet = default_character_sheet()
    sheet["progression"].update(
        {
            "level": 16,
            "classes": [
                {
                    "name": "Fighter",
                    "level": 11,
                    "subclass": "Champion",
                    "hit_die": 10,
                },
                {
                    "name": "Ranger",
                    "level": 5,
                    "subclass": "Hunter",
                    "hit_die": 10,
                },
            ],
        }
    )
    sheet["content"]["features"] = [
        {
            "id": "fighter-extra-attack",
            "name": "Extra Attack",
            "attack_scaling": {
                "class_name": "Fighter",
                "attacks_per_action_by_level": {
                    "5": 2,
                    "11": 3,
                    "20": 4,
                },
            },
        },
        {
            "id": "ranger-extra-attack",
            "name": "Extra Attack",
            "attack_scaling": {
                "class_name": "Ranger",
                "attacks_per_action_by_level": {"5": 2},
            },
        },
    ]

    synchronized = synchronize_class_feature_resources(sheet)

    assert synchronized["sheet"]["combat"]["attacks_per_action"] == 3
    assert synchronized["changes"][-1] == {
        "target": "combat.attacks_per_action",
        "old_value": 1,
        "new_value": 3,
        "source_feature_ids": ["fighter-extra-attack"],
    }


@pytest.mark.parametrize(
    ("class_name", "ability", "mode", "expected"),
    [
        ("Cleric", "wisdom", "prepared", 8),
        ("Druid", "wisdom", "prepared", 8),
        ("Paladin", "charisma", "prepared", 6),
        ("Wizard", "intelligence", "spellbook", 8),
    ],
)
def test_resource_sync_recomputes_2014_prepared_limit_after_an_ability_change(
    class_name: str,
    ability: str,
    mode: str,
    expected: int,
) -> None:
    sheet = default_character_sheet()
    sheet["progression"]["level"] = 4
    sheet["progression"]["classes"] = [
        {"name": class_name, "level": 4, "subclass": "", "hit_die": 8}
    ]
    sheet["abilities"][ability]["score"] = 18
    sheet["spellcasting"]["ability"] = ability
    sheet["spellcasting"]["preparation"] = {
        "mode": mode,
        "max_prepared": expected - 1,
        "changes_on": "long_rest",
        "selected_spell_ids": [],
    }
    if mode == "spellbook":
        sheet["spellcasting"]["spellbook"] = {"enabled": True, "spell_ids": []}

    synchronized = synchronize_class_feature_resources(sheet)

    assert synchronized["sheet"]["spellcasting"]["preparation"]["max_prepared"] == expected
    assert synchronized["changes"] == [
        {
            "target": "spellcasting.preparation.max_prepared",
            "old_value": expected - 1,
            "new_value": expected,
            "class_limits": {class_name.casefold(): expected},
        }
    ]
    assert (
        synchronize_class_feature_resources(synchronized["sheet"])["changes"]
        == []
    )


def test_per_level_hp_bonus_is_separate_from_the_minimum_class_gain() -> None:
    sheet = _single_class_sheet("Cleric", hit_die=8, constitution=16, hp=(7, 12))
    sheet["abilities"]["wisdom"]["score"] = 14
    sheet["spellcasting"]["ability"] = "wisdom"
    sheet["spellcasting"]["preparation"] = {
        "mode": "prepared",
        "max_prepared": 3,
        "changes_on": "long_rest",
        "selected_spell_ids": [],
    }
    sheet["spellcasting"]["spell_slots"] = {
        "1": {
            "label": "Level 1 spell slots",
            "value": 0,
            "max": 2,
            "recovers_on": "long_rest",
            "source_key": "Cleric",
            "slot_level": 1,
        }
    }

    result = advance_single_class_level(
        sheet,
        class_name="Cleric",
        hp_method="fixed",
        hp_per_level_bonus=1,
    )

    updated = validate_character_sheet(result["sheet"])
    assert updated["combat"]["hp"] == {"value": 7, "max": 21, "temp": 0}
    assert result["hit_points"]["class_gain"] == 8
    assert result["hit_points"]["per_level_bonus"] == 1
    assert updated["spellcasting"]["spell_slots"]["1"]["max"] == 3
    assert updated["spellcasting"]["spell_slots"]["1"]["value"] == 1
    assert updated["spellcasting"]["preparation"]["max_prepared"] == 4


def test_wizard_gains_only_new_slot_capacity_and_reports_spellbook_choices() -> None:
    sheet = _single_class_sheet("Wizard", hit_die=6, constitution=12, hp=(1, 7))
    sheet["abilities"]["intelligence"]["score"] = 14
    sheet["spellcasting"]["ability"] = "intelligence"
    sheet["spellcasting"]["preparation"] = {
        "mode": "spellbook",
        "max_prepared": 3,
        "changes_on": "long_rest",
        "selected_spell_ids": [],
    }
    sheet["spellcasting"]["spellbook"] = {"enabled": True, "spell_ids": []}
    sheet["spellcasting"]["spell_slots"] = {
        "1": {
            "label": "Level 1 spell slots",
            "value": 0,
            "max": 2,
            "recovers_on": "long_rest",
            "source_key": "Wizard",
            "slot_level": 1,
        }
    }

    result = advance_single_class_level(
        sheet,
        class_name="Wizard",
        hp_method="rolled",
        rng=_SequenceRng(3),
    )

    updated = validate_character_sheet(result["sheet"])
    assert updated["combat"]["hp"]["max"] == 11
    assert updated["spellcasting"]["spell_slots"]["1"]["value"] == 1
    assert updated["spellcasting"]["preparation"]["max_prepared"] == 4
    assert result["spell_choices"]["leveled_spells_to_add"] == 2
    assert result["hit_points"]["roll"]["expression"] == "1d6"
    assert result["hit_points"]["roll"]["total"] == 3


def test_level_advancement_rejects_multiclass_mismatch_and_invalid_method() -> None:
    sheet = _single_class_sheet("Fighter", hit_die=10, constitution=14, hp=(12, 12))
    with pytest.raises(CombatEngineError, match="match"):
        advance_single_class_level(sheet, class_name="Rogue", hp_method="fixed")
    with pytest.raises(CombatEngineError, match="fixed or rolled"):
        advance_single_class_level(sheet, class_name="Fighter", hp_method="unknown")
    sheet["progression"]["classes"].append(
        {"name": "Rogue", "level": 1, "subclass": "", "hit_die": 8}
    )
    with pytest.raises(CombatEngineError, match="single-class"):
        advance_single_class_level(sheet, class_name="Fighter", hp_method="fixed")


def test_experience_award_reports_eligibility_without_auto_leveling() -> None:
    sheet = _single_class_sheet("Fighter", hit_die=10, constitution=14, hp=(12, 12))

    first = award_experience(sheet, amount=299)
    assert first["sheet"]["progression"]["level"] == 1
    assert first["advancement"] == {
        "level": 1,
        "xp": 299,
        "current_level_threshold": 0,
        "next_level": 2,
        "next_level_threshold": 300,
        "xp_to_next_level": 1,
        "eligible": False,
    }

    second = award_experience(first["sheet"], amount=1)
    assert second["sheet"]["progression"]["level"] == 1
    assert second["advancement"]["eligible"] is True
    assert second["advancement"]["xp_to_next_level"] == 0
    assert sheet["progression"]["xp"] == 0

    with pytest.raises(CombatEngineError, match="positive integer"):
        award_experience(sheet, amount=0)


def test_experience_status_handles_level_twenty_without_a_false_next_level() -> None:
    sheet = _single_class_sheet("Fighter", hit_die=10, constitution=14, hp=(12, 12))
    sheet["progression"]["level"] = 20
    sheet["progression"]["classes"][0]["level"] = 20
    sheet["progression"]["xp"] = 400_000

    assert experience_status(sheet) == {
        "level": 20,
        "xp": 400_000,
        "current_level_threshold": 355_000,
        "next_level": None,
        "next_level_threshold": None,
        "xp_to_next_level": None,
        "eligible": False,
    }


def test_per_level_hit_point_bonus_updates_every_recorded_level() -> None:
    sheet = _single_class_sheet("Cleric", hit_die=8, constitution=16, hp=(11, 20))
    sheet["progression"]["level"] = 2
    sheet["progression"]["classes"][0]["level"] = 2
    sheet["combat"]["hp_progression"] = [
        {"level": 1, "method": "manual", "value": 11, "source": "Cleric level 1"},
        {"level": 2, "method": "fixed", "value": 9, "source": "Cleric level 2"},
    ]

    updated = apply_per_level_hit_point_bonus(
        sheet,
        amount=1,
        source="Hill Dwarf: Dwarven Toughness",
    )

    assert updated["combat"]["hp"] == {"value": 11, "max": 22, "temp": 0}
    assert [entry["value"] for entry in updated["combat"]["hp_progression"]] == [12, 10]
    assert all(
        entry["adjustments"]
        == [
            {
                "kind": "per_level_bonus",
                "amount": 1,
                "source": "Hill Dwarf: Dwarven Toughness",
            }
        ]
        for entry in updated["combat"]["hp_progression"]
    )
    assert sheet["combat"]["hp"]["max"] == 20


def test_per_level_hit_point_bonus_rejects_a_partial_existing_ledger() -> None:
    sheet = _single_class_sheet("Cleric", hit_die=8, constitution=16, hp=(11, 20))
    sheet["progression"]["level"] = 2
    sheet["progression"]["classes"][0]["level"] = 2
    sheet["combat"]["hp_progression"] = [
        {"level": 2, "method": "fixed", "value": 9, "source": "Cleric level 2"}
    ]

    with pytest.raises(CombatEngineError, match="every existing level"):
        apply_per_level_hit_point_bonus(
            sheet,
            amount=1,
            source="Hill Dwarf: Dwarven Toughness",
        )


def test_constitution_score_change_updates_maximum_not_current_hp_and_ledger() -> None:
    sheet = default_character_sheet()
    sheet["progression"]["level"] = 2
    sheet["combat"]["hp"] = {"value": 14, "max": 14, "temp": 0}
    sheet["combat"]["hp_progression"] = [
        {"level": 1, "method": "fixed", "value": 8, "source": "Bard level 1"},
        {"level": 2, "method": "fixed", "value": 6, "source": "Bard level 2"},
    ]

    updated = apply_constitution_score_hit_point_change(
        sheet,
        previous_score=13,
        new_score=14,
        source="Half-Elf Constitution increase",
    )

    assert updated["combat"]["hp"] == {"value": 14, "max": 16, "temp": 0}
    assert [item["value"] for item in updated["combat"]["hp_progression"]] == [9, 7]
    assert all(
        item["adjustments"]
        == [
            {
                "kind": "constitution_modifier_change",
                "amount": 1,
                "source": "Half-Elf Constitution increase",
                "previous_score": 13,
                "new_score": 14,
            }
        ]
        for item in updated["combat"]["hp_progression"]
    )


def test_constitution_change_preserves_long_base_sources_in_structured_adjustment() -> None:
    sheet = default_character_sheet()
    sheet["progression"]["level"] = 2
    sheet["combat"]["hp"] = {"value": 14, "max": 14, "temp": 0}
    long_source = "module:" + ("source-bound-evidence-" * 13)
    assert len(long_source) <= 300
    sheet["combat"]["hp_progression"] = [
        {"level": 1, "method": "fixed", "value": 8, "source": long_source},
        {"level": 2, "method": "fixed", "value": 6, "source": long_source},
    ]

    updated = apply_constitution_score_hit_point_change(
        sheet,
        previous_score=13,
        new_score=14,
        source="Rogue level 10 Ability Score Improvement",
    )

    assert all(
        item["source"] == long_source for item in updated["combat"]["hp_progression"]
    )
    assert all(
        item["adjustments"][0]["source"]
        == "Rogue level 10 Ability Score Improvement"
        for item in updated["combat"]["hp_progression"]
    )


def test_constitution_score_change_can_fill_setup_hp_explicitly() -> None:
    sheet = default_character_sheet()
    sheet["progression"]["level"] = 1
    sheet["combat"]["hp"] = {"value": 9, "max": 9, "temp": 0}

    updated = apply_constitution_score_hit_point_change(
        sheet,
        previous_score=13,
        new_score=14,
        source="Character creation species increase",
        adjust_current=True,
    )

    assert updated["combat"]["hp"] == {"value": 10, "max": 10, "temp": 0}
