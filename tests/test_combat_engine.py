import random
from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    derive_character_sheet,
    equip_inventory_item,
    remove_effect,
    validate_character_sheet,
)
from sagasmith_dnd.combat_engine import (
    CombatEngineError,
    NeedsRulingError,
    active_hypnotic_pattern_effect_ids,
    add_choice_window,
    apply_attack_ac_bonus,
    apply_concentration_result,
    apply_damage_parts_to_sheet,
    apply_damage_to_sheet,
    apply_healing_to_sheet,
    apply_hit_point_loss_to_sheet,
    arm_readied_spell,
    available_actions,
    available_attack_defenses,
    available_reactions,
    current_combatant,
    damage_amount_after_reduction,
    detach_attachment,
    end_concentration_for_incapacitating_conditions,
    end_turn,
    execute_split_reaction,
    force_move_directly_away,
    pay_activity_activation,
    pay_attack_action,
    pay_multiattack_activity,
    preflight_attack,
    preflight_spell_attack,
    queue_combatant,
    reconcile_effect_dependencies,
    resolve_actor_check,
    resolve_actor_contest,
    resolve_actor_group_check,
    resolve_attack_action,
    resolve_attack_damage,
    resolve_choice_window,
    resolve_common_action,
    resolve_death_save_to_sheet,
    resolve_heated_body_melee_hit,
    resolve_hypnotic_pattern_target,
    resolve_preserve_life_to_sheets,
    resolve_random_save_effects,
    resolve_readied_spell_window,
    resolve_save_damage_to_sheet,
    resolve_save_damage_to_sheets,
    resolve_second_wind_to_sheet,
    resolve_source_contest_effect,
    resolve_source_save_effect,
    resolve_standard_weapon_on_hit,
    resolve_turn_undead_to_sheets,
    roll_attack_action,
    settle_core_activity_effect,
    settle_start_turn_regeneration,
    source_speed_multiplier,
    spend_movement,
    stabilize_sheet,
    start_encounter,
    structured_critical_followup,
    trigger_readied_spell,
)
from sagasmith_dnd.engine import resolve_check, roll_d20
from sagasmith_dnd.lifecycle import apply_rest
from sagasmith_dnd.rule_engine import resolution_context
from sagasmith_dnd.spatial import compile_battle_map
from sagasmith_dnd.standard_spell_ids import CORE_HYPNOTIC_PATTERN_SPELL_ID


def test_damage_reduction_uses_one_round_down_contract() -> None:
    assert damage_amount_after_reduction(7, "full") == 7
    assert damage_amount_after_reduction(7, "half") == 3
    assert damage_amount_after_reduction(7, "none") == 0
    with pytest.raises(CombatEngineError, match="full, half, or none"):
        damage_amount_after_reduction(7, "quarter")


def test_generic_save_damage_rolls_and_applies_half_damage_atomically() -> None:
    target = _actor("target", hp=20)
    target["sheet"]["abilities"]["dexterity"]["score"] = 20
    target["derived"] = derive_character_sheet(target["sheet"])

    settled = resolve_save_damage_to_sheet(
        target,
        save_ability="dexterity",
        save_dc=10,
        damage_expression="3d6",
        damage_type="fire",
        half_on_success=True,
        source="agent-ruling:test-save-damage",
        rng=_SequenceRng(1, 2, 4, 10),
    )

    assert settled["result"]["save"]["success"] is True
    assert settled["result"]["damage_roll"]["total"] == 7
    assert settled["result"]["damage_reduction"] == "half"
    assert settled["result"]["damage_amount"] == 3
    assert settled["sheet"]["combat"]["hp"]["value"] == 17


def test_generic_save_damage_shares_one_roll_across_targets() -> None:
    agile = _actor("agile", hp=20)
    clumsy = _actor("clumsy", hp=20)
    agile["sheet"]["abilities"]["dexterity"]["score"] = 20
    clumsy["sheet"]["abilities"]["dexterity"]["score"] = 1
    agile["derived"] = derive_character_sheet(agile["sheet"])
    clumsy["derived"] = derive_character_sheet(clumsy["sheet"])

    settled = resolve_save_damage_to_sheets(
        [agile, clumsy],
        save_ability="dexterity",
        save_dc=10,
        damage_expression="2d6",
        damage_type="fire",
        half_on_success=True,
        source="agent-ruling:test-shared-save-damage",
        rng=_SequenceRng(3, 4, 20, 1),
    )

    assert settled["result"]["damage_roll"]["total"] == 7
    assert [item["success"] for item in settled["result"]["targets"]] == [
        True,
        False,
    ]
    assert [item["damage_amount"] for item in settled["result"]["targets"]] == [
        3,
        7,
    ]
    assert settled["sheets"]["agile"]["combat"]["hp"]["value"] == 17
    assert settled["sheets"]["clumsy"]["combat"]["hp"]["value"] == 13


def test_magic_resistance_requires_source_kind_and_applies_advantage() -> None:
    actor = _actor("archmage")
    actor["sheet"]["content"]["features"].append(
        {
            "id": "magic-resistance-passive",
            "name": "Magic Resistance",
            "choices": {
                "source_trait": {
                    "kind": "magic_resistance",
                    "trigger": "saving_throw",
                    "save_source_kinds": ["spell", "magical_effect"],
                    "grants": "advantage",
                    "automatic": True,
                    "source_excerpt": (
                        "The archmage has advantage on saving throws against "
                        "spells and other magical effects."
                    ),
                }
            },
        }
    )
    actor["derived"] = derive_character_sheet(actor["sheet"])
    effective = {
        "edition": "2014",
        "fingerprint": "",
        "lock": [],
        "mechanics": [],
    }

    with pytest.raises(NeedsRulingError, match="source kind"):
        resolve_actor_check(
            actor,
            kind="save",
            ability="wisdom",
            dc=15,
            rules=resolution_context(effective),
            rng=_SequenceRng(20),
        )

    magical = resolve_actor_check(
        actor,
        kind="save",
        ability="wisdom",
        dc=15,
        rules=resolution_context(
            effective,
            facts={"save_source_kind": "spell"},
        ),
        rng=_SequenceRng(3, 17),
    )
    assert magical["rolls"] == [3, 17]
    assert magical["roll_mode"] == "advantage"
    assert magical["success"] is True
    assert [
        receipt["mechanic_id"] for receipt in magical["rule_receipts"]
    ] == ["dnd5e.core.save.magic_resistance"]

    nonmagical = resolve_actor_check(
        actor,
        kind="save",
        ability="wisdom",
        dc=15,
        rules=resolution_context(
            effective,
            facts={"save_source_kind": "nonmagical_effect"},
        ),
        rng=_SequenceRng(3),
    )
    assert nonmagical["rolls"] == [3]
    assert nonmagical["roll_mode"] == "normal"
    assert nonmagical["rule_receipts"] == []


def test_evasion_rewrites_dexterity_save_for_half_damage() -> None:
    trait = {
        "kind": "evasion",
        "trigger": "dexterity_save_for_half_damage",
        "save_ability": "dexterity",
        "ordinary_successful_save": "half",
        "successful_save": "none",
        "failed_save": "half",
        "automatic": True,
        "source_excerpt": (
            "If the assassin is subjected to an effect that allows it to make "
            "a Dexterity saving throw to take only half damage, the assassin "
            "instead takes no damage if it succeeds on the saving throw, and "
            "only half damage if it fails."
        ),
    }
    agile = _actor("agile-assassin", hp=20)
    agile["sheet"]["abilities"]["dexterity"]["score"] = 20
    agile["sheet"]["content"]["features"].append(
        {
            "id": "evasion-passive",
            "name": "Evasion",
            "choices": {"source_trait": trait},
        }
    )
    agile["derived"] = derive_character_sheet(agile["sheet"])
    clumsy = deepcopy(agile)
    clumsy["id"] = "clumsy-assassin"
    clumsy["sheet"]["abilities"]["dexterity"]["score"] = 1
    clumsy["derived"] = derive_character_sheet(clumsy["sheet"])
    rules = resolution_context(
        {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []}
    )

    settled = resolve_save_damage_to_sheets(
        [agile, clumsy],
        save_ability="dexterity",
        save_dc=10,
        damage_expression="2d6",
        damage_type="fire",
        half_on_success=True,
        source="spell:fireball",
        rules=rules,
        rng=_SequenceRng(3, 4, 10, 1),
    )

    assert [
        item["damage_reduction"] for item in settled["result"]["targets"]
    ] == ["none", "half"]
    assert [
        item["damage_amount"] for item in settled["result"]["targets"]
    ] == [0, 3]
    assert settled["sheets"]["agile-assassin"]["combat"]["hp"]["value"] == 20
    assert settled["sheets"]["clumsy-assassin"]["combat"]["hp"]["value"] == 17
    assert all(
        [receipt["mechanic_id"] for receipt in item["rule_receipts"]]
        == ["dnd5e.core.save.evasion"]
        for item in settled["result"]["targets"]
    )


def test_generic_save_damage_rejects_conflicting_roll_states() -> None:
    with pytest.raises(
        CombatEngineError,
        match="save damage requires",
    ):
        resolve_save_damage_to_sheet(
            _actor("target", hp=20),
            save_ability="dexterity",
            save_dc=10,
            damage_expression="2d6",
            damage_type="fire",
            half_on_success=True,
            source="agent-ruling:test-invalid-save-damage",
            advantage=True,
            disadvantage=True,
        )


class _SequenceRng:
    def __init__(self, *values: int) -> None:
        self.values = list(values)

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0)
        assert minimum <= value <= maximum
        return value


def _actor(identifier: str, *, hp: int = 12, ac: int = 10) -> dict:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": hp, "max": hp, "temp": 0}
    sheet["combat"]["ac"] = {"base": ac, "override": None}
    sheet["abilities"]["strength"]["score"] = 16
    return {
        "id": identifier,
        "name": identifier,
        "sheet": sheet,
        "derived": derive_character_sheet(sheet),
    }


def test_hypnotic_pattern_effect_lifecycle_preserves_other_condition_sources() -> None:
    target = _actor("target", hp=20)
    target["sheet"]["effects"] = [
        {
            "id": "other-charm",
            "name": "Other charm",
            "kind": "timed_conditions",
            "source": "other-caster",
            "active": True,
            "concentration": False,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [
                {"path": "conditions", "mode": "add", "value": "charmed"},
                {"path": "conditions", "mode": "add", "value": "incapacitated"},
            ],
            "description": "",
        },
        {
            "id": "target-concentration",
            "name": "Target concentration",
            "kind": "concentration",
            "source": "spell.cast",
            "source_spell_id": "test.target-concentration",
            "active": True,
            "concentration": True,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [],
            "description": "",
        },
    ]
    target["sheet"]["conditions"] = ["charmed", "incapacitated"]
    target["derived"] = derive_character_sheet(target["sheet"])

    resolved = resolve_hypnotic_pattern_target(
        target,
        caster_id="caster",
        spell_id=CORE_HYPNOTIC_PATTERN_SPELL_ID,
        save_dc=15,
        rng=_SequenceRng(1),
    )

    assert resolved["result"]["outcome"] == "affected"
    assert resolved["result"]["ended_concentration_effect_ids"] == [
        "target-concentration"
    ]
    assert active_hypnotic_pattern_effect_ids(resolved["sheet"]) == [
        resolved["result"]["effect_id"]
    ]
    assert source_speed_multiplier(resolved["sheet"]) == 0.0

    no_damage = apply_damage_to_sheet(
        resolved["sheet"],
        amount=0,
        damage_type="force",
    )
    assert active_hypnotic_pattern_effect_ids(no_damage["sheet"])

    damaged = apply_damage_to_sheet(
        no_damage["sheet"],
        amount=1,
        damage_type="force",
    )
    assert active_hypnotic_pattern_effect_ids(damaged["sheet"]) == []
    assert {"charmed", "incapacitated"} <= set(damaged["sheet"]["conditions"])
    assert source_speed_multiplier(damaged["sheet"]) == 1.0
    assert resolved["result"]["effect_id"] in damaged["ended_effect_ids"]


def test_hypnotic_pattern_immunity_and_effect_dependency_are_hard_settled() -> None:
    immune = _actor("immune")
    immune["sheet"]["traits"]["condition_immunities"] = ["charmed"]
    immune["derived"] = derive_character_sheet(immune["sheet"])
    ignored = resolve_hypnotic_pattern_target(
        immune,
        caster_id="caster",
        spell_id=CORE_HYPNOTIC_PATTERN_SPELL_ID,
        save_dc=15,
        rng=_SequenceRng(1),
    )
    assert ignored["result"]["outcome"] == "immune_to_charmed"
    assert ignored["result"]["save"] is None

    target = _actor("target")
    affected = resolve_hypnotic_pattern_target(
        target,
        caster_id="caster",
        spell_id=CORE_HYPNOTIC_PATTERN_SPELL_ID,
        save_dc=15,
        rng=_SequenceRng(1),
    )
    target_effect_id = affected["result"]["effect_id"]
    caster_sheet = default_character_sheet()
    caster_sheet["effects"] = [
        {
            "id": "caster-concentration",
            "name": "Concentrating: Hypnotic Pattern",
            "kind": "concentration",
            "source": "spell.cast",
            "source_spell_id": CORE_HYPNOTIC_PATTERN_SPELL_ID,
            "active": False,
            "concentration": True,
            "duration": {"period": "round", "remaining": 10},
            "changes": [],
            "description": "",
            "ended_reason": "failed_save",
        }
    ]
    encounter = {
        "dependent_effects": [
            {
                "id": "link",
                "mechanic_id": "dnd5e.core.spell.hypnotic_pattern",
                "dependency": "source_effect_active",
                "source_actor_id": "caster",
                "source_effect_id": "caster-concentration",
                "target_actor_id": "target",
                "target_effect_id": target_effect_id,
                "active": True,
            }
        ]
    }

    reconciled = reconcile_effect_dependencies(
        encounter,
        {
            "caster": validate_character_sheet(caster_sheet),
            "target": affected["sheet"],
        },
    )

    assert reconciled["changed_actor_ids"] == ["target"]
    assert reconciled["ended_links"][0]["ended_reason"] == "source_effect_ended"
    assert active_hypnotic_pattern_effect_ids(
        reconciled["sheets"]["target"]
    ) == []
    assert "charmed" not in reconciled["sheets"]["target"]["conditions"]
    assert "incapacitated" not in reconciled["sheets"]["target"]["conditions"]


def test_corrosive_form_damages_attacker_and_corrodes_mundane_weapon() -> None:
    attacker = _actor("attacker", hp=20)
    attacker["sheet"]["inventory"]["items"] = [
        {
            "id": "longsword",
            "name": "Longsword",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d8",
                "damage_type": "slashing",
                "properties": [],
            },
        }
    ]
    attacker["sheet"]["inventory"]["equipment_slots"]["main_hand"] = "longsword"
    attacker["sheet"] = validate_character_sheet(attacker["sheet"])
    attacker["derived"] = derive_character_sheet(attacker["sheet"])
    pudding = _actor("pudding", hp=85, ac=7)
    pudding["sheet"]["traits"]["size"] = "large"
    pudding["sheet"]["traits"]["immunities"] = [
        "acid",
        "cold",
        "lightning",
        "slashing",
    ]
    pudding["sheet"]["content"]["features"].append(
        {
            "id": "corrosive-form-passive",
            "name": "Corrosive Form",
            "activation": {"type": "passive", "cost": 0},
            "choices": {
                "source_trait": {
                    "kind": "corrosive_form",
                    "trigger": "contact_or_melee_hit",
                    "melee_range_ft": 5,
                    "contact_damage_formula": "1d8",
                    "contact_damage_type": "acid",
                    "weapon_materials": ["metal", "wood"],
                    "requires_nonmagical_weapon": True,
                    "weapon_damage_roll_penalty": -1,
                    "weapon_destroyed_at_penalty": -5,
                    "ammunition_destroyed_after_hit": True,
                    "object_materials": ["wood", "metal"],
                    "object_maximum_thickness_inches": 2,
                    "object_dissolution_rounds": 1,
                    "automatic": True,
                }
            },
        }
    )
    pudding["sheet"]["content"]["activities"].append(
        {
            "id": "split-reaction",
            "name": "Split",
            "activation": {"type": "reaction", "cost": 1},
            "choices": {
                "source_trait": {
                    "kind": "split",
                    "trigger": "subjected_to_damage",
                    "damage_types": ["lightning", "slashing"],
                    "minimum_size": "medium",
                    "minimum_hit_points": 10,
                    "new_creature_count": 2,
                    "hit_points": "half_original_rounded_down",
                    "size_change": -1,
                }
            },
        }
    )
    pudding["derived"] = derive_character_sheet(pudding["sheet"])

    plan = preflight_attack(
        attacker,
        pudding,
        action={"weapon_id": "longsword"},
    )
    updated_attacker, updated_pudding, result = resolve_attack_action(
        attacker,
        pudding,
        plan=plan,
        rng=_SequenceRng(2, 4, 5),
    )

    assert result["hit"] is True
    assert result["damage"]["applied_amount"] == 0
    assert updated_pudding["sheet"]["combat"]["hp"]["value"] == 85
    assert updated_attacker["sheet"]["combat"]["hp"]["value"] == 15
    assert result["corrosive_form"]["weapon_corrosion"]["after_penalty"] == 1
    weapon = updated_attacker["sheet"]["inventory"]["items"][0]
    assert weapon["mechanics"]["corrosion_penalty"] == 1
    assert derive_character_sheet(updated_attacker["sheet"])["inventory"][
        "weapon_attacks"
    ][0]["damage_bonus"] == 2
    assert result["split_reaction"]["new_hit_points_each"] == 42
    children = execute_split_reaction(
        updated_pudding["sheet"],
        result["split_reaction"],
    )
    assert [child["traits"]["size"] for child in children] == ["medium", "medium"]
    assert [child["combat"]["hp"]["value"] for child in children] == [42, 42]


def test_heated_body_damages_only_a_melee_attacker_within_five_feet() -> None:
    attacker = _actor("attacker", hp=20)
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "longsword",
            "attack_type": "melee",
            "properties": [],
            "attack_bonus": 6,
            "damage_formula": "1d8",
            "damage_bonus": 3,
            "damage_expression": "1d8 + 3",
            "damage_type": "slashing",
            "additional_damage": [],
            "reach_ft": 5,
        }
    ]
    salamander = _actor("salamander", hp=90, ac=15)
    salamander["sheet"]["content"]["features"].append(
        {
            "id": "heated-body-passive",
            "name": "Heated Body",
            "activation": {"type": "passive", "cost": 0},
            "choices": {
                "source_trait": {
                    "kind": "heated_body",
                    "trigger": "contact_or_melee_hit",
                    "melee_range_ft": 5,
                    "contact_damage_formula": "2d6",
                    "average_damage": 7,
                    "contact_damage_type": "fire",
                    "automatic": True,
                    "source_excerpt": (
                        "A creature that touches the salamander or hits it with "
                        "a melee attack while within 5 feet of it takes 7 (2d6) "
                        "fire damage."
                    ),
                }
            },
        }
    )
    salamander["derived"] = derive_character_sheet(salamander["sheet"])
    attacker.update(
        initiative=20,
        tie_breaker=0,
        position={"x": 0, "y": 0},
        disposition="friendly",
    )
    salamander.update(
        initiative=10,
        tie_breaker=0,
        position={"x": 1, "y": 0},
        disposition="hostile",
    )
    encounter = start_encounter([attacker, salamander])

    plan = preflight_attack(
        attacker,
        salamander,
        action={"weapon_id": "longsword"},
        encounter=encounter,
    )
    updated_attacker, _, result = resolve_attack_action(
        attacker,
        salamander,
        plan=plan,
        rng=_SequenceRng(15, 4, 2, 3),
    )

    assert result["hit"] is True
    assert result["heated_body"]["fire_damage"]["applied_amount"] == 5
    assert updated_attacker["sheet"]["combat"]["hp"]["value"] == 15
    assert result["heated_body"]["mechanic_id"] == (
        "dnd5e.core.monster.heated_body"
    )
    beyond_range = resolve_heated_body_melee_hit(
        attacker["sheet"],
        salamander["sheet"],
        plan={
            "attack_mode": "melee",
            "range": {"distance_ft": 10},
            "weapon_reach_ft": 10,
            "attacker_uses_death_saves": True,
            "ruleset": "2014",
        },
    )
    assert beyond_range["triggered"] is False
    assert beyond_range["attacker_sheet"]["combat"]["hp"]["value"] == 20


def test_pseudopod_corrosion_reduces_and_destroys_worn_armor() -> None:
    pudding = _actor("pudding", hp=85, ac=7)
    source_excerpt = (
        "In addition, nonmagical armor worn by the target is partly dissolved "
        "and takes a permanent and cumulative -1 penalty to the AC it offers. "
        "The armor is destroyed if the penalty reduces its AC to 10."
    )
    pudding["sheet"]["inventory"]["items"] = [
        {
            "id": "pseudopod",
            "name": "Pseudopod",
            "kind": "weapon",
            "mechanics": {
                "attack_type": "melee",
                "attack_ability": "strength",
                "attack_bonus_override": 5,
                "damage_formula": "1d4",
                "damage_type": "bludgeoning",
                "always_available": True,
                "on_hit_resolution": {
                    "kind": "armor_corrosion",
                    "trigger": "weapon_hit",
                    "requires_worn_armor": True,
                    "requires_nonmagical_armor": True,
                    "armor_class_penalty": -1,
                    "destroyed_at_armor_class": 10,
                    "automatic": True,
                    "source_excerpt": source_excerpt,
                },
            },
        }
    ]
    pudding["sheet"] = validate_character_sheet(pudding["sheet"])
    pudding["derived"] = derive_character_sheet(pudding["sheet"])
    target = _actor("target", hp=20)
    target_sheet, armor_id = add_inventory_item(
        target["sheet"],
        {
            "id": "corrosion-test-armor",
            "name": "Corrosion test armor",
            "kind": "armor",
            "mechanics": {
                "base_ac": 11,
                "dexterity_mode": "none",
                "magic_bonus": 0,
            },
        },
    )
    target["sheet"] = equip_inventory_item(target_sheet, armor_id, "armor")
    target["derived"] = derive_character_sheet(target["sheet"])

    plan = preflight_attack(
        pudding,
        target,
        action={"weapon_id": "pseudopod"},
    )
    _updated_pudding, updated_target, result = resolve_attack_action(
        pudding,
        target,
        plan=plan,
        rng=_SequenceRng(10, 1),
    )

    assert result["structured_on_hit"]["destroyed"] is True
    assert updated_target["sheet"]["inventory"]["equipment_slots"]["armor"] is None
    armor = updated_target["sheet"]["inventory"]["items"][0]
    assert armor["condition"] == "destroyed"


def test_magmin_touch_compiles_standard_ongoing_damage() -> None:
    target = _actor("target")
    source_excerpt = (
        "If the target is a creature or a flammable object, it ignites. "
        "Until a creature takes an action to douse the fire, the creature "
        "takes 3 (1d6) fire damage at the end of each of its turns."
    )

    result = resolve_standard_weapon_on_hit(
        target["sheet"],
        {
            "kind": "ignition_ongoing_damage",
            "trigger": "weapon_hit",
            "creature_target_automatic": True,
            "flammable_object_requires_scene_fact": True,
            "damage_formula": "1d6",
            "average_damage": 3,
            "damage_type": "fire",
            "trigger_timing": "turn_end",
            "end_action": "use_object",
            "end_action_description": "douse the fire",
            "automatic": True,
            "source_excerpt": source_excerpt,
        },
    )

    assert result["sheet"] == target["sheet"]
    assert result["ongoing_effect"] == {
        "kind": "source_ongoing_damage",
        "damage_formula": "1d6",
        "average_damage": 3,
        "damage_type": "fire",
        "trigger_timing": "turn_end",
        "end_action": "use_object",
        "end_action_description": "douse the fire",
        "active": True,
        "source_excerpt": source_excerpt,
        "mechanic_id": "dnd5e.core.monster.ignition_ongoing_damage",
    }
    assert result["scene_fact_requirement"]["required_for_creature_target"] is False


def test_magmin_death_burst_surfaces_only_on_death_transition() -> None:
    magmin = _actor("magmin", hp=9)
    source_excerpt = (
        "When the magmin dies, it explodes in a burst of fire and magma. "
        "Each creature within 10 ft. of it must make a DC 11 Dexterity saving "
        "throw, taking 7 (2d6) fire damage on a failed save, or half as much "
        "damage on a successful one. Flammable objects that aren't being worn "
        "or carried in that area are ignited."
    )
    magmin["sheet"]["content"]["features"].append(
        {
            "id": "dnd5e.core.monster.death-burst",
            "name": "Death Burst",
            "activation": {"type": "passive", "cost": 0},
            "choices": {
                "source_trait": {
                    "kind": "death_burst",
                    "trigger": "death",
                    "range_ft": 10,
                    "target": "each_creature_in_range",
                    "save_ability": "dexterity",
                    "save_dc": 11,
                    "damage_formula": "2d6",
                    "average_damage": 7,
                    "damage_type": "fire",
                    "failed_save": "full",
                    "successful_save": "half",
                    "ignite_flammable_unworn_objects": True,
                    "automatic": True,
                    "source_excerpt": source_excerpt,
                }
            },
        }
    )

    wounded = apply_damage_to_sheet(
        magmin["sheet"],
        amount=8,
        damage_type="cold",
        death_saves=False,
    )
    killed = apply_damage_to_sheet(
        wounded["sheet"],
        amount=1,
        damage_type="cold",
        death_saves=False,
    )
    already_dead = apply_damage_to_sheet(
        killed["sheet"],
        amount=1,
        damage_type="cold",
        death_saves=False,
    )

    assert wounded["death_trigger"] is None
    assert killed["death_trigger"] == {
        **magmin["sheet"]["content"]["features"][0]["choices"]["source_trait"],
        "mechanic_id": "dnd5e.core.monster.death_burst",
    }
    assert already_dead["death_trigger"] is None


def test_actor_check_rejects_attack_rolls_owned_by_the_attack_engine() -> None:
    with pytest.raises(CombatEngineError, match="unsupported check kind"):
        resolve_actor_check(
            _actor("attacker"),
            kind="attack",
            ability="strength",
            dc=10,
        )


def _gazer_eye_ray_spec() -> dict:
    return {
        "kind": "gazer_eye_rays_2014",
        "draw_count": 2,
        "reroll_duplicates": True,
        "range_ft": 60,
        "target_count": {"minimum": 1, "maximum": 2},
        "effects": [
            {
                "id": "dazing-ray",
                "source_activity_id": "dazing-ray-action",
                "save": {"ability": "wisdom", "dc": 12},
                "failure": {
                    "kind": "timed_condition",
                    "condition": "charmed",
                    "duration": {
                        "period": "source_turn_start",
                        "remaining": 1,
                    },
                    "speed_multiplier": 0.5,
                    "attack_disadvantage": True,
                },
                "source_excerpt": "Dazing Ray source text",
            },
            {
                "id": "fear-ray",
                "source_activity_id": "fear-ray-action",
                "save": {"ability": "wisdom", "dc": 12},
                "failure": {
                    "kind": "timed_condition",
                    "condition": "frightened",
                    "duration": {
                        "period": "source_turn_start",
                        "remaining": 1,
                    },
                },
                "source_excerpt": "Fear Ray source text",
            },
            {
                "id": "frost-ray",
                "source_activity_id": "frost-ray-action",
                "save": {"ability": "dexterity", "dc": 12},
                "failure": {
                    "kind": "damage",
                    "expression": "3d6",
                    "damage_type": "cold",
                },
                "source_excerpt": "Frost Ray source text",
            },
            {
                "id": "telekinetic-ray",
                "source_activity_id": "telekinetic-ray-action",
                "save": {"ability": "strength", "dc": 12},
                "failure": {
                    "kind": "forced_movement",
                    "maximum_size": "medium",
                    "distance_ft": 30,
                    "direction": "directly_away",
                },
                "source_excerpt": "Telekinetic Ray source text",
            },
        ],
    }


def _rogue(identifier: str = "rogue") -> dict:
    actor = _actor(identifier, hp=30)
    actor["sheet"]["progression"] = {
        "level": 1,
        "classes": [{"name": "Rogue", "level": 1, "hit_die": 8}],
    }
    actor["sheet"]["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2014.feature.rogue-sneak-attack",
            "name": "Sneak Attack",
            "source_key": "Rogue",
        }
    ]
    actor["derived"] = derive_character_sheet(actor["sheet"])
    actor["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "dagger",
            "attack_type": "melee",
            "properties": ["finesse", "light", "thrown"],
            "attack_bonus": 99,
            "damage_expression": "1",
            "damage_type": "piercing",
        }
    ]
    return actor


def test_gazer_eye_rays_reroll_duplicates_and_resolve_each_save() -> None:
    gazer = _actor("gazer")
    first = _actor("first", hp=20)
    second = _actor("second", hp=20)

    result = resolve_random_save_effects(
        gazer,
        [first, second],
        spec=_gazer_eye_ray_spec(),
        rng=_SequenceRng(1, 1, 3, 5, 5, 2, 3, 4),
    )

    assert result["selected_effect_ids"] == ["dazing-ray", "frost-ray"]
    assert [item["duplicate"] for item in result["selection_rolls"]] == [
        False,
        True,
        False,
    ]
    assert result["targets"][0]["target_id"] == "first"
    assert result["targets"][0]["outcome"] == "condition"
    assert result["sheets"]["first"]["conditions"] == ["charmed"]
    assert source_speed_multiplier(result["sheets"]["first"]) == 0.5
    assert result["targets"][1]["target_id"] == "second"
    assert result["targets"][1]["damage_roll"]["total"] == 9
    assert result["sheets"]["second"]["combat"]["hp"]["value"] == 11


def test_dazing_ray_source_makes_attacks_disadvantaged_and_protects_charmer() -> None:
    gazer = _actor("gazer")
    dazed = _actor("dazed")
    other = _actor("other")
    dazed["sheet"]["conditions"] = ["charmed"]
    dazed["sheet"]["effects"] = [
        {
            "id": "dazing",
            "name": "Dazing Ray",
            "kind": "timed_conditions",
            "source": "gazer",
            "active": True,
            "duration": {"period": "source_turn_start", "remaining": 1},
            "changes": [
                {"path": "conditions", "mode": "add", "value": "charmed"}
            ],
        }
    ]
    dazed["derived"] = derive_character_sheet(dazed["sheet"])

    with pytest.raises(CombatEngineError, match="cannot attack its charmer"):
        preflight_attack(dazed, gazer, action={"weapon_id": "unarmed-strike"})
    plan = preflight_attack(
        dazed,
        other,
        action={"weapon_id": "unarmed-strike"},
    )

    assert plan["disadvantage"] is True
    assert "dazing_ray" in plan["disadvantage_sources"]


def test_telekinetic_ray_moves_up_to_the_last_legal_cell_without_reactions() -> None:
    source = _actor("gazer")
    source.update(
        initiative=20,
        position={"x": 1, "y": 1},
        disposition="hostile",
    )
    target = _actor("target")
    target.update(
        initiative=10,
        position={"x": 2, "y": 1},
        disposition="friendly",
    )
    encounter = start_encounter([source, target])
    encounter["battle_map"] = compile_battle_map(
        {"scene_id": "gazer-rays", "spatial": {}},
        {"width_cells": 6, "height_cells": 4},
    )

    moved = force_move_directly_away(
        encounter,
        source_actor_id="gazer",
        target_actor_id="target",
        distance_ft=30,
    )

    assert moved["moved_distance_ft"] == 15
    assert moved["destination"] == {"x": 5, "y": 1}
    assert moved["encounter"]["pending"] == []
    assert moved["encounter"]["log"][-1]["opportunity_reactions"] is False


def test_telekinetic_ray_projects_noncardinal_bearings_onto_the_square_grid() -> None:
    source = _actor("gazer")
    source.update(
        initiative=20,
        position={"x": 2, "y": 2},
        disposition="hostile",
    )
    target = _actor("target")
    target.update(
        initiative=10,
        position={"x": 1, "y": 4},
        disposition="friendly",
    )
    encounter = start_encounter([source, target])
    encounter["battle_map"] = compile_battle_map(
        {"scene_id": "gazer-rays", "spatial": {}},
        {"width_cells": 4, "height_cells": 7},
    )

    moved = force_move_directly_away(
        encounter,
        source_actor_id="gazer",
        target_actor_id="target",
        distance_ft=30,
    )

    # The continuous (-1, 2) outward ray crosses (0, 5), then (0, 6);
    # the following cell is outside the map, so "up to 30 feet" stops there.
    assert moved["moved_distance_ft"] == 10
    assert moved["destination"] == {"x": 0, "y": 6}


def test_frightened_creature_cannot_willingly_move_closer_to_visible_source() -> None:
    target = _actor("target")
    target["sheet"]["conditions"] = ["frightened"]
    target["sheet"]["effects"] = [
        {
            "id": "fear",
            "name": "Fear Ray",
            "kind": "timed_conditions",
            "source": "gazer",
            "active": True,
            "duration": {"period": "source_turn_start", "remaining": 1},
            "changes": [
                {"path": "conditions", "mode": "add", "value": "frightened"}
            ],
        }
    ]
    target["derived"] = derive_character_sheet(target["sheet"])
    target.update(initiative=20, position={"x": 2, "y": 1})
    source = _actor("gazer")
    source.update(initiative=10, position={"x": 0, "y": 1})
    encounter = start_encounter([target, source])

    with pytest.raises(CombatEngineError, match="cannot willingly move closer"):
        spend_movement(
            encounter,
            "target",
            5,
            destination={"x": 1, "y": 1},
        )
    moved = spend_movement(
        encounter,
        "target",
        5,
        destination={"x": 3, "y": 1},
    )

    assert current_combatant(moved)["position"] == {"x": 3, "y": 1}


def _lightfoot(identifier: str = "lightfoot") -> dict:
    actor = _actor(identifier)
    actor["sheet"]["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2014.species-feature.lightfoot-lucky",
            "name": "Lucky",
            "source_key": "Lightfoot",
        }
    ]
    actor["derived"] = derive_character_sheet(actor["sheet"])
    return actor


def test_ordinary_checks_do_not_use_attack_natural_rules() -> None:
    result = resolve_check(
        dc=21,
        ability_score=10,
        kind="ability",
        rng=random.Random(5),
    )
    assert result["natural"] == 20
    assert result["success"] is False


def test_2014_jack_of_all_trades_applies_only_to_unproficient_ability_checks() -> None:
    bard = _actor("bard")
    bard["sheet"]["progression"] = {
        "level": 2,
        "classes": [{"name": "Bard", "level": 2, "hit_die": 8}],
    }
    bard["sheet"]["abilities"]["charisma"]["score"] = 16
    bard["sheet"]["abilities"]["dexterity"]["score"] = 14
    bard["sheet"]["skills"]["deception"]["proficiency"] = "proficient"
    bard["sheet"]["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2014.feature.bard-jack-of-all-trades",
            "name": "Jack of All Trades",
            "source_key": "Bard",
        }
    ]
    bard["derived"] = derive_character_sheet(bard["sheet"])
    rules = resolution_context(
        {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []}
    )

    untrained = resolve_actor_check(
        bard,
        kind="check",
        ability="intimidation",
        dc=14,
        rules=rules,
        rng=_SequenceRng(10),
    )
    assert untrained["ability_modifier"] == 3
    assert untrained["proficiency_bonus"] == 0
    assert untrained["bonus"] == 1
    assert untrained["total"] == 14
    assert [
        receipt["mechanic_id"] for receipt in untrained["rule_receipts"]
    ] == ["dnd5e.core.check.jack_of_all_trades"]

    trained = resolve_actor_check(
        bard,
        kind="check",
        ability="deception",
        dc=15,
        rules=rules,
        rng=_SequenceRng(10),
    )
    assert trained["ability_modifier"] == 3
    assert trained["proficiency_bonus"] == 2
    assert trained["bonus"] == 0
    assert trained["total"] == 15
    assert trained["rule_receipts"] == []

    saving_throw = resolve_actor_check(
        bard,
        kind="save",
        ability="wisdom",
        dc=11,
        rules=rules,
        rng=_SequenceRng(10),
    )
    assert saving_throw["bonus"] == 0
    assert saving_throw["total"] == 10
    assert saving_throw["rule_receipts"] == []

    bard["sheet"]["edition"] = "2024"
    bard["derived"] = derive_character_sheet(bard["sheet"])
    revised_check = resolve_actor_check(
        bard,
        kind="check",
        ability="intimidation",
        dc=14,
        rng=_SequenceRng(10),
    )
    assert revised_check["bonus"] == 0
    assert revised_check["total"] == 13


def test_2014_group_check_succeeds_when_at_least_half_succeed() -> None:
    actors = [_actor(f"scout-{index}") for index in range(1, 7)]
    rules = resolution_context(
        {
            "edition": "2014",
            "fingerprint": "group-check-pack",
            "lock": [],
            "mechanics": [],
        }
    )

    result = resolve_actor_group_check(
        actors,
        ability="stealth",
        dc=16,
        advantage=True,
        rules_by_actor_id={actor["id"]: rules for actor in actors},
        rng=_SequenceRng(16, 4, 17, 3, 18, 2, 19, 1, 20, 5, 10, 6),
    )

    assert result["participant_count"] == 6
    assert result["success_count"] == 5
    assert result["failure_count"] == 1
    assert result["required_successes"] == 3
    assert result["success"] is True
    assert [entry["success"] for entry in result["participants"]] == [
        True,
        True,
        True,
        True,
        True,
        False,
    ]
    assert [item["mechanic_id"] for item in result["rule_receipts"]] == [
        "dnd5e.core.check.group"
    ]


def test_2014_group_check_rejects_duplicate_or_single_actor_groups() -> None:
    actor = _actor("scout")

    with pytest.raises(CombatEngineError, match="at least two actors"):
        resolve_actor_group_check([actor], ability="stealth", dc=10)
    with pytest.raises(CombatEngineError, match="must be unique"):
        resolve_actor_group_check([actor, actor], ability="stealth", dc=10)


def test_keen_perception_requires_and_uses_sensory_facts() -> None:
    scout = _actor("scout")
    scout["sheet"]["content"]["features"] = [
        {
            "id": "keen-hearing-and-sight-passive",
            "name": "Keen Hearing and Sight",
            "activation": {
                "type": "passive",
                "trigger": "hearing- or sight-based Perception check",
            },
            "choices": {
                "source_trait": {
                    "kind": "keen_perception",
                    "trigger": "perception_check",
                    "senses": ["hearing", "sight"],
                    "grants": "advantage",
                    "automatic": True,
                }
            },
        }
    ]
    scout["derived"] = derive_character_sheet(scout["sheet"])
    base_rules = {
        "edition": "2014",
        "fingerprint": "",
        "lock": [],
        "mechanics": [],
    }

    with pytest.raises(NeedsRulingError, match="hearing or sight basis"):
        resolve_actor_check(
            scout,
            kind="check",
            ability="perception",
            dc=12,
            rules=resolution_context(base_rules),
            rng=_SequenceRng(10),
        )

    hearing = resolve_actor_check(
        scout,
        kind="check",
        ability="perception",
        dc=12,
        rules=resolution_context(
            base_rules,
            facts={"perception_senses": ["hearing"]},
        ),
        rng=_SequenceRng(4, 17),
    )
    assert hearing["rolls"] == [4, 17]
    assert hearing["natural"] == 17
    assert any(
        receipt["mechanic_id"] == "dnd5e.core.check.keen_perception"
        for receipt in hearing["rule_receipts"]
    )

    unrelated = resolve_actor_check(
        scout,
        kind="check",
        ability="perception",
        dc=12,
        rules=resolution_context(
            base_rules,
            facts={"perception_senses": ["other"]},
        ),
        rng=_SequenceRng(9),
    )
    assert unrelated["rolls"] == [9]


def test_2014_ability_contest_compares_totals_and_uses_no_synthetic_dc() -> None:
    deceiver = _actor("deceiver")
    deceiver["sheet"]["abilities"]["charisma"]["score"] = 16
    deceiver["sheet"]["skills"]["deception"]["proficiency"] = "proficient"
    deceiver["derived"] = derive_character_sheet(deceiver["sheet"])
    observer = _actor("observer")
    observer["sheet"]["abilities"]["wisdom"]["score"] = 14
    observer["sheet"]["skills"]["insight"]["proficiency"] = "proficient"
    observer["derived"] = derive_character_sheet(observer["sheet"])

    result = resolve_actor_contest(
        deceiver,
        observer,
        source_ability="deception",
        target_ability="insight",
        target_advantage=True,
        rng=_SequenceRng(12, 4, 16),
    )

    assert result["kind"] == "ability_contest"
    assert result["source_check"]["rolls"] == [12]
    assert result["source_check"]["total"] == 17
    assert result["target_check"]["rolls"] == [4, 16]
    assert result["target_check"]["total"] == 20
    assert "dc" not in result["source_check"]
    assert "success" not in result["target_check"]
    assert result["winner_actor_id"] == "observer"
    assert result["outcome"] == "target_wins"


def test_2014_ability_contest_tie_leaves_situation_unchanged() -> None:
    source = _actor("source")
    target = _actor("target")

    result = resolve_actor_contest(
        source,
        target,
        source_ability="strength",
        target_ability="strength",
        rng=_SequenceRng(10, 10),
    )

    assert result["tie"] is True
    assert result["winner_actor_id"] == ""
    assert result["outcome"] == "tie_no_change"


def test_2014_jack_of_all_trades_applies_to_initiative() -> None:
    bard = _actor("bard")
    bard["sheet"]["progression"] = {
        "level": 2,
        "classes": [{"name": "Bard", "level": 2, "hit_die": 8}],
    }
    bard["sheet"]["abilities"]["dexterity"]["score"] = 14
    bard["sheet"]["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2014.feature.bard-jack-of-all-trades",
            "name": "Jack of All Trades",
            "source_key": "Bard",
        }
    ]
    bard["derived"] = derive_character_sheet(bard["sheet"])

    encounter = start_encounter([bard], ruleset="2014", rng=_SequenceRng(10))

    assert encounter["combatants"][0]["initiative_bonus"] == 3
    assert encounter["combatants"][0]["initiative"] == 13
    assert encounter["rule_boundary_ids"] == ["dnd5e.core.check.jack_of_all_trades"]


def test_attack_preflight_rejects_exhausted_linked_ammunition() -> None:
    attacker = _actor("archer")
    attacker["sheet"]["inventory"]["items"] = [
        {"id": "arrows", "name": "Arrows", "kind": "ammunition", "quantity": 0},
        {
            "id": "longbow",
            "name": "Longbow",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "ranged",
                "attack_ability": "dexterity",
                "damage_formula": "1d8",
                "damage_type": "piercing",
                "normal_range_ft": 150,
                "long_range_ft": 600,
                "ammunition_item_id": "arrows",
            },
        },
    ]
    attacker["sheet"]["inventory"]["equipment_slots"]["main_hand"] = "longbow"
    attacker["derived"] = derive_character_sheet(attacker["sheet"])

    with pytest.raises(CombatEngineError, match="no linked ammunition remaining"):
        preflight_attack(attacker, _actor("target"), action={"weapon_id": "longbow"})


def test_slaying_ammunition_opens_source_save_damage() -> None:
    source_excerpt = (
        "If a creature belonging to the type, race, or group associated with an "
        "arrow of slaying takes damage from the arrow, the creature must make a "
        "DC 17 Constitution saving throw, taking an extra 6d10 piercing damage "
        "on a failed save, or half as much extra damage on a successful one."
    )
    attacker = _actor("archer")
    attacker["sheet"]["inventory"]["items"] = [
        {"id": "arrows", "name": "Arrows", "kind": "ammunition", "quantity": 20},
        {
            "id": "dragon-slaying-arrows",
            "name": "Arrows of dragon slaying",
            "kind": "ammunition",
            "quantity": 2,
            "mechanics": {
                "magic": True,
                "rarity": "very_rare",
                "slaying": {
                    "target_groups": ["dragon"],
                    "save_ability": "constitution",
                    "save_dc": 17,
                    "damage_formula": "6d10",
                    "damage_type": "piercing",
                    "half_on_success": True,
                    "source_excerpt": source_excerpt,
                    "rule_refs": ["srd2014.magic-items.arrow-of-slaying"],
                },
            },
        },
        {
            "id": "longbow",
            "name": "Longbow",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
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
    ]
    attacker["sheet"]["inventory"]["equipment_slots"]["main_hand"] = "longbow"
    attacker["derived"] = derive_character_sheet(attacker["sheet"])
    target = _actor("dragon")
    target["sheet"]["progression"]["species"] = "Huge dragon"
    target["derived"] = derive_character_sheet(target["sheet"])

    plan = preflight_attack(
        attacker,
        target,
        action={
            "weapon_id": "longbow",
            "ammunition_item_id": "dragon-slaying-arrows",
        },
        rules=resolution_context(
            {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []}
        ),
    )

    assert plan["ammunition_item_id"] == "dragon-slaying-arrows"
    assert plan["on_hit_effect"] == source_excerpt
    assert plan["ammunition_slaying"]["matched_groups"] == ["dragon"]
    assert "dnd5e.core.magic_ammunition.slaying" in {
        receipt["mechanic_id"] for receipt in plan["rule_receipts"]
    }


def test_slaying_ammunition_does_not_trigger_for_an_unrelated_target() -> None:
    attacker = _actor("archer")
    attacker["sheet"]["inventory"]["items"] = [
        {
            "id": "dragon-slaying-arrow",
            "name": "Arrow of dragon slaying",
            "kind": "ammunition",
            "quantity": 1,
            "mechanics": {
                "magic": True,
                "slaying": {
                    "target_groups": ["dragon"],
                    "save_ability": "constitution",
                    "save_dc": 17,
                    "damage_formula": "6d10",
                    "damage_type": "piercing",
                    "half_on_success": True,
                    "source_excerpt": (
                        "The target must make a DC 17 Constitution saving throw, "
                        "taking an extra 6d10 piercing damage on a failed save, or "
                        "half as much extra damage on a successful one."
                    ),
                    "rule_refs": ["srd2014.magic-items.arrow-of-slaying"],
                },
            },
        },
        {
            "id": "shortbow",
            "name": "Shortbow",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "ranged",
                "attack_ability": "dexterity",
                "damage_formula": "1d6",
                "damage_type": "piercing",
                "properties": ["ammunition", "two_handed"],
                "normal_range_ft": 80,
                "long_range_ft": 320,
            },
        },
    ]
    attacker["sheet"]["inventory"]["equipment_slots"]["main_hand"] = "shortbow"
    attacker["derived"] = derive_character_sheet(attacker["sheet"])
    target = _actor("giant")
    target["sheet"]["progression"]["species"] = "Huge giant"
    target["derived"] = derive_character_sheet(target["sheet"])

    plan = preflight_attack(
        attacker,
        target,
        action={
            "weapon_id": "shortbow",
            "ammunition_item_id": "dragon-slaying-arrow",
        },
    )

    assert plan["ammunition_slaying"] is None
    assert plan["on_hit_effect"] == ""


def test_unarmed_strike_remains_available_with_an_unusable_equipped_weapon() -> None:
    attacker = _actor("archer")
    attacker["sheet"]["inventory"]["items"] = [
        {"id": "arrows", "name": "Arrows", "kind": "ammunition", "quantity": 0},
        {
            "id": "longbow",
            "name": "Longbow",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "ranged",
                "attack_ability": "dexterity",
                "damage_formula": "1d8",
                "damage_type": "piercing",
                "normal_range_ft": 150,
                "long_range_ft": 600,
                "ammunition_item_id": "arrows",
            },
        },
    ]
    attacker["sheet"]["inventory"]["equipment_slots"]["main_hand"] = "longbow"
    attacker["derived"] = derive_character_sheet(attacker["sheet"])

    plan = preflight_attack(
        attacker,
        _actor("target"),
        action={"weapon_id": "unarmed-strike"},
    )

    assert plan["weapon_id"] == "unarmed-strike"
    assert plan["damage_expression"] == "1 + 3"


def test_positioned_ranged_attack_requires_recorded_range() -> None:
    attacker = _actor("archer")
    attacker["sheet"]["inventory"]["items"] = [
        {
            "id": "mystery-bow",
            "name": "Mystery Bow",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "ranged",
                "attack_ability": "dexterity",
                "damage_formula": "1d8",
                "damage_type": "piercing",
            },
        }
    ]
    attacker["sheet"]["inventory"]["equipment_slots"]["main_hand"] = "mystery-bow"
    attacker["derived"] = derive_character_sheet(attacker["sheet"])
    target = _actor("target")
    attacker["position"] = {"x": 0, "y": 0}
    target["position"] = {"x": 1, "y": 0}

    with pytest.raises(NeedsRulingError, match="no recorded range") as raised:
        preflight_attack(attacker, target, action={"weapon_id": "mystery-bow"})
    assert raised.value.missing == ("weapon.range:mystery-bow",)


def test_ranged_attack_has_close_combat_disadvantage() -> None:
    attacker = _actor("archer")
    target = _actor("target")
    attacker.update(
        initiative=20,
        tie_breaker=0,
        position={"x": 0, "y": 0},
        disposition="friendly",
    )
    target.update(
        initiative=10,
        tie_breaker=0,
        position={"x": 1, "y": 0},
        disposition="hostile",
    )
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "shortbow",
            "attack_type": "ranged",
            "attack_bonus": 4,
            "damage_expression": "1d6",
            "damage_type": "piercing",
            "range_ft": {"normal": 80, "long": 320},
        }
    ]
    encounter = start_encounter([attacker, target])

    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "shortbow"},
        encounter=encounter,
    )

    assert plan["disadvantage"] is True
    assert plan["close_combat_threat_ids"] == ["target"]
    assert "hostile_creature_within_5_ft" in plan["disadvantage_sources"]

    encounter["combatants"][1]["conditions"] = ["incapacitated"]
    safe = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "shortbow"},
        encounter=encounter,
    )
    assert safe["close_combat_threat_ids"] == []


def test_source_weapon_targeting_requires_eligible_size_and_effective_advantage() -> None:
    attacker = _actor("garroter")
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "web-garrote",
            "attack_type": "melee",
            "reach_ft": 5,
            "attack_bonus": 4,
            "damage_expression": "1d4 + 2",
            "damage_type": "bludgeoning",
            "properties": [],
            "required_target_sizes": ["small", "medium"],
            "requires_attack_advantage": True,
        }
    ]
    target = _actor("target")
    target["sheet"]["traits"]["size"] = "medium"

    with pytest.raises(CombatEngineError, match="advantage requirement"):
        preflight_attack(attacker, target, action={"weapon_id": "web-garrote"})

    plan = preflight_attack(
        attacker,
        target,
        action={
            "weapon_id": "web-garrote",
            "context": {"advantage": True, "advantage_sources": ["attacker_unseen"]},
        },
        rules=resolution_context(
            {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []}
        ),
    )
    assert plan["advantage"] is True
    assert [receipt["mechanic_id"] for receipt in plan["rule_receipts"]] == [
        "dnd5e.core.attack.source_targeting"
    ]

    with pytest.raises(CombatEngineError, match="advantage requirement"):
        preflight_attack(
            attacker,
            target,
            action={
                "weapon_id": "web-garrote",
                "context": {"advantage": True, "disadvantage": True},
            },
        )

    target["sheet"]["traits"]["size"] = "large"
    with pytest.raises(CombatEngineError, match="target size"):
        preflight_attack(
            attacker,
            target,
            action={"weapon_id": "web-garrote", "context": {"advantage": True}},
        )


def test_spell_attack_preflight_uses_source_card_and_spellcasting_override() -> None:
    attacker = _actor("caster")
    target = _actor("target")
    attacker["sheet"]["spellcasting"].update(
        ability="intelligence",
        attack_bonus_override=6,
    )
    attacker["sheet"]["content"]["spells"] = [
        {
            "id": "module.spell.scorching-ray",
            "name": "Scorching Ray",
            "level": 2,
            "definition": {
                "range": {"kind": "distance", "normal_ft": 60},
            },
            "resolution": {
                "kind": "spell_attack",
                "targeting": {"mode": "creature", "max_targets": 100},
                "attack": {
                    "mode": "ranged",
                    "count": {"base": 3, "per_slot_above": 1, "slot_base_level": 2},
                    "damage": {"base_dice": "2d6", "damage_type": "fire"},
                },
            },
        }
    ]
    attacker["sheet"] = validate_character_sheet(attacker["sheet"])
    attacker["derived"] = derive_character_sheet(attacker["sheet"])
    attacker.update(
        initiative=20,
        tie_breaker=0,
        position={"x": 0, "y": 0},
        disposition="friendly",
    )
    target.update(
        initiative=10,
        tie_breaker=0,
        position={"x": 5, "y": 0},
        disposition="hostile",
    )
    encounter = start_encounter([attacker, target])

    plan = preflight_spell_attack(
        attacker,
        target,
        spell_id="module.spell.scorching-ray",
        cast_level=2,
        encounter=encounter,
    )

    assert plan["kind"] == "spell_attack"
    assert plan["attack_bonus"] == 6
    assert plan["damage_expression"] == "2d6"
    assert plan["range"]["normal_ft"] == 60


def test_preserve_life_enforces_pool_half_hp_and_creature_type() -> None:
    cleric = _actor("cleric", hp=21)
    cleric["sheet"]["progression"] = {
        "level": 2,
        "classes": [{"name": "Cleric", "level": 2, "hit_die": 8}],
    }
    cleric["sheet"]["content"]["features"] = [
        {
            "id": (
                "dnd5e.content.srd2014.feature."
                "life-domain-channel-divinity-preserve-life"
            ),
            "name": "Channel Divinity: Preserve Life",
            "source_key": "Life Domain",
        }
    ]
    rogue = _actor("rogue", hp=17)["sheet"]
    rogue["combat"]["hp"]["value"] = 1
    rogue["conditions"] = ["unconscious", "stable", "prone"]
    cleric_target = cleric["sheet"]
    cleric_target["combat"]["hp"]["value"] = 7

    result = resolve_preserve_life_to_sheets(
        cleric["sheet"],
        {"rogue": rogue, "cleric": cleric_target},
        allocations=[
            {"target_id": "rogue", "amount": 7},
            {"target_id": "cleric", "amount": 3},
        ],
    )

    assert result["allocated"] == result["pool"] == 10
    assert result["sheets"]["rogue"]["combat"]["hp"]["value"] == 8
    assert result["sheets"]["rogue"]["conditions"] == ["prone"]
    assert result["sheets"]["cleric"]["combat"]["hp"]["value"] == 10
    with pytest.raises(CombatEngineError, match="above half"):
        resolve_preserve_life_to_sheets(
            cleric["sheet"],
            {"rogue": rogue},
            allocations=[{"target_id": "rogue", "amount": 8}],
        )
    undead = _actor("undead")["sheet"]
    undead["progression"]["species"] = "undead"
    with pytest.raises(CombatEngineError, match="Undead or Constructs"):
        resolve_preserve_life_to_sheets(
            cleric["sheet"],
            {"undead": undead},
            allocations=[{"target_id": "undead", "amount": 1}],
        )


def test_turn_undead_applies_and_enforces_turned() -> None:
    cleric = _actor("cleric")
    cleric["sheet"]["edition"] = "2014"
    cleric["sheet"]["progression"] = {
        "level": 2,
        "classes": [{"name": "Cleric", "level": 2, "hit_die": 8}],
    }
    cleric["sheet"]["abilities"]["wisdom"]["score"] = 16
    cleric["sheet"]["spellcasting"]["ability"] = "wisdom"
    cleric["sheet"]["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2014.feature.cleric-channel-divinity",
            "name": "Channel Divinity",
            "source_key": "Cleric",
            "activation": {"type": "action", "cost": 1},
            "resource_key": "channel_divinity",
            "choices": {"options": ["Turn Undead", "selected-domain option"]},
        }
    ]
    cleric["derived"] = derive_character_sheet(cleric["sheet"])
    undead = _actor("undead")
    undead["sheet"]["edition"] = "2014"
    undead["sheet"]["progression"]["species"] = "undead"
    undead["derived"] = derive_character_sheet(undead["sheet"])

    resolved = resolve_turn_undead_to_sheets(
        cleric,
        {"undead": undead},
        rng=_SequenceRng(1),
    )

    assert resolved["save_dc"] == 13
    assert resolved["targets"][0]["turned"] is True
    turned_sheet = resolved["sheets"]["undead"]
    assert "turned" in turned_sheet["conditions"]
    assert turned_sheet["effects"][-1]["kind"] == "turn_undead"
    assert turned_sheet["effects"][-1]["duration"] == {
        "period": "minute",
        "remaining": 1,
    }

    cleric["initiative"] = 20
    cleric["position"] = {"x": 0, "y": 0}
    undead["sheet"] = turned_sheet
    undead["derived"] = derive_character_sheet(turned_sheet)
    undead["initiative"] = 10
    undead["position"] = {"x": 2, "y": 0}
    encounter = start_encounter([cleric, undead], ruleset="2014")
    target_state = next(
        item for item in encounter["combatants"] if item["actor_id"] == "undead"
    )
    target_state["turned"] = {
        "source_actor_id": "cleric",
        "effect_id": resolved["targets"][0]["effect_id"],
    }
    encounter = end_turn(encounter, actor_id_value="cleric")
    assert available_actions(encounter, "undead") == ["move", "dash", "dodge"]
    assert current_combatant(encounter)["turn_budget"]["reaction"] == 0
    with pytest.raises(CombatEngineError, match="farther"):
        spend_movement(encounter, "undead", 5, destination={"x": 1, "y": 0})
    moved = spend_movement(encounter, "undead", 5, destination={"x": 3, "y": 0})
    with pytest.raises(CombatEngineError, match="nowhere to move"):
        resolve_common_action(moved, actor_id_value="undead", action="dodge")

    restrained = deepcopy(encounter)
    restrained_target = current_combatant(restrained)
    assert restrained_target is not None
    restrained_target["conditions"].append("restrained")
    assert available_actions(restrained, "undead") == ["dash", "dodge", "escape"]
    escaping = resolve_common_action(
        restrained,
        actor_id_value="undead",
        action="escape",
        payload={"effect_id": "net"},
    )
    assert current_combatant(escaping)["turn_flags"]["escape_declared"] == {
        "effect_id": "net"
    }

    damaged = apply_damage_to_sheet(turned_sheet, amount=1, damage_type="radiant")
    assert "turned" not in damaged["sheet"]["conditions"]
    assert damaged["ended_effect_ids"] == [resolved["targets"][0]["effect_id"]]


def test_damage_condition_cleanup_preserves_other_active_effect_owners() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["conditions"] = ["turned", "unconscious"]
    actor["sheet"]["effects"] = [
        {
            "id": "turn-undead",
            "kind": "turn_undead",
            "active": True,
            "changes": [],
        },
        {
            "id": "other-turned-owner",
            "kind": "timed_conditions",
            "active": True,
            "changes": [{"path": "conditions", "mode": "add", "value": "turned"}],
        },
        {
            "id": "sleep",
            "kind": "timed_conditions",
            "active": True,
            "changes": [{"path": "conditions", "mode": "add", "value": "unconscious"}],
        },
    ]

    damaged = apply_damage_to_sheet(
        actor["sheet"],
        amount=1,
        damage_type="radiant",
    )

    assert set(damaged["sheet"]["conditions"]) == {"turned", "unconscious"}
    assert damaged["sheet"]["effects"][0]["active"] is False
    assert damaged["ended_effect_ids"] == ["turn-undead"]


def test_restrained_actor_can_spend_its_action_to_escape() -> None:
    first = _actor("first")
    second = _actor("second")
    first["initiative"] = 20
    second["initiative"] = 10
    encounter = end_turn(
        start_encounter([first, second], ruleset="2014"),
        actor_id_value="first",
    )
    acting = current_combatant(encounter)
    assert acting is not None
    acting["conditions"].append("restrained")

    assert "escape" in available_actions(encounter, "second")
    escaped = resolve_common_action(
        encounter,
        actor_id_value="second",
        action="escape",
        payload={"effect_id": "web"},
    )
    assert current_combatant(escaped)["turn_budget"]["main_action"] == 0


def test_attack_ends_the_specific_invisibility_spell_concentration() -> None:
    attacker = _actor("invisible-attacker")
    attacker["sheet"]["conditions"] = ["invisible"]
    attacker["sheet"]["effects"] = [
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
            "description": "",
        }
    ]
    target = _actor("target")
    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "unarmed-strike", "attack_mode": "melee"},
    )
    attack = roll_attack_action(plan=plan, rng=_SequenceRng(10, 10))

    updated_attacker, _updated_target, result = resolve_attack_damage(
        attacker,
        target,
        plan=plan,
        attack=attack,
    )

    assert result["ended_invisibility_effect_ids"] == ["invisibility-effect"]
    assert "invisible" not in updated_attacker["sheet"]["conditions"]
    effect = updated_attacker["sheet"]["effects"][0]
    assert effect["active"] is False
    assert effect["ended_reason"] == "actor_attacked"


def test_halfling_lucky_rerolls_only_one_natural_one_and_keeps_replacement() -> None:
    result = roll_d20(
        advantage=True,
        reroll_ones=True,
        rng=_SequenceRng(1, 7, 18),
    )
    assert result["rolls"] == [18, 7]
    assert result["natural"] == 18
    assert result["rerolls"] == [
        {"index": 0, "from": 1, "to": 18, "source": "halfling_lucky"}
    ]
    assert result["roll_mode"] == "advantage"
    assert result["advantage_applied"] is True
    assert result["disadvantage_applied"] is False


def test_d20_result_audits_disadvantage_and_cancellation() -> None:
    disadvantaged = roll_d20(
        disadvantage=True,
        rng=_SequenceRng(18, 2),
    )
    assert disadvantaged["natural"] == 2
    assert disadvantaged["roll_mode"] == "disadvantage"
    assert disadvantaged["advantage_applied"] is False
    assert disadvantaged["disadvantage_applied"] is True

    cancelled = roll_d20(
        advantage=True,
        disadvantage=True,
        rng=_SequenceRng(12),
    )
    assert cancelled["rolls"] == [12]
    assert cancelled["roll_mode"] == "normal"
    assert cancelled["advantage_applied"] is False
    assert cancelled["disadvantage_applied"] is False


def test_halfling_lucky_applies_to_actor_checks_attacks_and_death_saves() -> None:
    halfling = _lightfoot()
    check = resolve_actor_check(
        halfling,
        kind="ability",
        ability="strength",
        dc=10,
        rng=_SequenceRng(1, 15),
    )
    assert check["natural"] == 15
    assert check["rerolls"][0]["source"] == "halfling_lucky"

    halfling["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "shortsword",
            "attack_type": "melee",
            "properties": ["finesse"],
            "attack_bonus": 0,
            "damage_expression": "1",
            "damage_type": "piercing",
        }
    ]
    target = _actor("target", ac=10)
    plan = preflight_attack(halfling, target, action={"weapon_id": "shortsword"})
    _, _, attack = resolve_attack_action(
        halfling,
        target,
        plan=plan,
        rng=_SequenceRng(1, 15, 1),
    )
    assert attack["hit"] is True
    assert attack["rerolls"][0]["to"] == 15

    death_sheet = halfling["sheet"]
    death_sheet["combat"]["hp"]["value"] = 0
    death_sheet["conditions"] = ["unconscious"]
    death = resolve_death_save_to_sheet(death_sheet, rng=_SequenceRng(1, 14))
    assert death["natural"] == 14
    assert death["failures"] == 0
    assert death["successes"] == 1


def test_damage_applies_resistance_and_vulnerability_in_order() -> None:
    actor = _actor("target", hp=20)
    actor["sheet"]["traits"]["resistances"] = ["fire"]
    actor["sheet"]["traits"]["vulnerabilities"] = ["fire"]
    result = apply_damage_to_sheet(actor["sheet"], amount=9, damage_type="fire")
    assert result["applied_amount"] == 8
    assert result["after_hp"] == 12
    assert result["adjustment"] == "resistant_and_vulnerable"


def test_attuned_magic_item_grants_damage_resistance() -> None:
    actor = _actor("target", hp=20)
    actor["sheet"]["inventory"]["items"] = [
        {
            "id": "ring-of-cold-resistance",
            "name": "Ring of cold resistance",
            "kind": "magic_item",
            "equipped": True,
            "equipped_slot": "ring_1",
            "attunement": "attuned",
            "mechanics": {
                "grants": {"resistances": ["cold"]},
            },
        }
    ]
    actor["sheet"]["inventory"]["equipment_slots"]["ring_1"] = "ring-of-cold-resistance"

    result = apply_damage_to_sheet(actor["sheet"], amount=9, damage_type="cold")

    assert result["applied_amount"] == 4
    assert result["adjustment"] == "resistant"
    assert result["defense_sources"] == ["magic_item:ring-of-cold-resistance"]

    actor["sheet"]["inventory"]["items"][0]["attunement"] = "required"
    unattuned = apply_damage_to_sheet(actor["sheet"], amount=9, damage_type="cold")
    assert unattuned["applied_amount"] == 9
    assert unattuned["defense_sources"] == []


def test_attack_preflight_and_resolution_keep_target_sheet_auditable() -> None:
    attacker = _actor("attacker")
    target = _actor("target", hp=10, ac=1)
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "sword",
            "attack_bonus": 99,
            "damage_expression": "1d4",
            "damage_type": "slashing",
        }
    ]
    plan = preflight_attack(
        attacker,
        target,
        action={
            "weapon_id": "sword",
            "attack_bonus": 1,
            "damage_expression": "999d999",
        },
    )
    assert plan["attack_bonus"] == 99
    assert plan["damage_expression"] == "1d4"
    _, updated_target, result = resolve_attack_action(
        attacker,
        target,
        plan=plan,
        rng=random.Random(2),
    )
    assert result["hit"] is True
    assert result["damage"]["after_hp"] < 10
    assert updated_target["sheet"]["combat"]["hp"]["value"] < 10


def test_attack_settles_each_source_bound_damage_type_and_surfaces_on_hit_ruling() -> None:
    attacker = _actor("attacker")
    target = _actor("target", hp=30, ac=1)
    target["sheet"]["traits"]["resistances"] = ["necrotic"]
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "silvered-skull-flail",
            "attack_type": "melee",
            "reach_ft": 5,
            "properties": [],
            "attack_bonus": 99,
            "damage_expression": "1d8",
            "damage_type": "bludgeoning",
            "additional_damage": [
                {
                    "damage_expression": "4d6",
                    "damage_type": "necrotic",
                    "source": "monster-statblock",
                }
            ],
            "on_hit_effect": "The target has disadvantage on specified saving throws.",
        }
    ]
    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "silvered-skull-flail"},
    )

    _, updated_target, result = resolve_attack_action(
        attacker,
        target,
        plan=plan,
        rng=_SequenceRng(19, 4, 3, 3, 3, 3),
    )

    assert result["damage"]["input_amount"] == 16
    assert result["damage"]["applied_amount"] == 10
    assert [part["damage_type"] for part in result["damage"]["roll_parts"]] == [
        "bludgeoning",
        "necrotic",
    ]
    assert result["damage"]["roll_parts"][1]["source"] == "monster-statblock"
    assert updated_target["sheet"]["combat"]["hp"]["value"] == 20
    assert result["on_hit_ruling"] == {
        "required": True,
        "effect": "The target has disadvantage on specified saving throws.",
        "default_resolver": "agent",
        "ruling_kind": "source_or_scene_fact",
    }


def test_effect_only_attack_surfaces_ruling_without_applying_fake_damage() -> None:
    attacker = _actor("attacker")
    target = _actor("target", hp=30, ac=1)
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "web",
            "attack_type": "ranged",
            "range_ft": {"normal": 30, "long": 60},
            "properties": [],
            "attack_bonus": 99,
            "damage_expression": "",
            "damage_type": "",
            "additional_damage": [],
            "on_hit_effect": "The target is restrained by webbing.",
        }
    ]
    plan = preflight_attack(attacker, target, action={"weapon_id": "web"})

    _, updated_target, result = resolve_attack_action(
        attacker,
        target,
        plan=plan,
        rng=_SequenceRng(19),
    )

    assert result["hit"] is True
    assert result["damage"] is None
    assert updated_target["sheet"]["combat"]["hp"]["value"] == 30
    assert result["on_hit_ruling"] == {
        "required": True,
        "effect": "The target is restrained by webbing.",
        "default_resolver": "agent",
        "ruling_kind": "source_or_scene_fact",
    }


def test_structured_parry_opens_after_hit_and_before_damage() -> None:
    attacker = _actor("attacker")
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "sword",
            "attack_type": "melee",
            "reach_ft": 5,
            "attack_bonus": 4,
            "damage_expression": "1d8 + 2",
            "damage_type": "slashing",
            "properties": [],
        }
    ]
    attacker.update(
        initiative=20,
        position={"x": 0, "y": 0},
        disposition="hostile",
    )
    target = _actor("target", hp=20, ac=15)
    target["sheet"]["inventory"]["items"] = [
        {
            "id": "scimitar",
            "name": "Scimitar",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d6",
                "damage_type": "slashing",
                "properties": ["finesse", "light"],
            },
        }
    ]
    target["sheet"]["inventory"]["equipment_slots"]["main_hand"] = "scimitar"
    target["sheet"]["content"]["activities"] = [
        {
            "id": "bandit-captain-parry",
            "name": "Parry",
            "source_key": "Bandit Captain",
            "activation": {"type": "reaction"},
            "choices": {
                "reaction_defense": {
                    "kind": "armor_class_bonus",
                    "bonus": 2,
                    "attack_modes": ["melee"],
                    "requires_visible_attacker": True,
                    "requires_wielded_melee_weapon": True,
                }
            },
        }
    ]
    target["derived"] = derive_character_sheet(target["sheet"])
    target.update(
        initiative=10,
        position={"x": 1, "y": 0},
        disposition="friendly",
    )
    encounter = start_encounter([attacker, target])
    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "sword"},
        encounter=encounter,
    )
    attack = roll_attack_action(plan=plan, rng=_SequenceRng(12))
    assert attack["total"] == 16
    assert attack["hit"] is True
    defenses = available_attack_defenses(
        target,
        plan=plan,
        attack=attack,
        encounter=encounter,
    )
    assert defenses == [
        {
            "id": "bandit-captain-parry",
            "name": "Parry",
            "kind": "armor_class_bonus",
            "bonus": 2,
            "projected_hit": False,
            "source_key": "Bandit Captain",
            "rule_refs": [],
        }
    ]
    defended = apply_attack_ac_bonus(
        attack,
        bonus=defenses[0]["bonus"],
        source_id=defenses[0]["id"],
    )
    _, updated_target, result = resolve_attack_damage(
        attacker,
        target,
        plan=plan,
        attack=defended,
    )
    assert result["hit"] is False
    assert result["damage"] is None
    assert updated_target["sheet"]["combat"]["hp"]["value"] == 20

    ranged_plan = {**plan, "attack_mode": "ranged", "melee_attack": False}
    assert (
        available_attack_defenses(
            target,
            plan=ranged_plan,
            attack=attack,
            encounter=encounter,
        )
        == []
    )


def test_dueling_style_adds_damage_only_for_one_equipped_melee_weapon() -> None:
    attacker = _actor("duelist")
    attacker["sheet"]["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2014.feature.fighter-fighting-style",
            "name": "Fighting Style",
            "source_key": "Fighter",
            "choices": {"option": "Dueling"},
        }
    ]
    attacker["sheet"]["inventory"]["items"] = [
        {
            "id": "longsword",
            "name": "Longsword",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "category": "martial",
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d8",
                "damage_type": "slashing",
                "properties": ["versatile"],
            },
        }
    ]
    attacker["sheet"]["inventory"]["equipment_slots"]["main_hand"] = "longsword"
    attacker["derived"] = derive_character_sheet(attacker["sheet"])
    target = _actor("target", hp=20, ac=1)
    plan = preflight_attack(attacker, target, action={"weapon_id": "longsword"})
    assert plan["damage_expression"] == "1d8 + 3 + 2"
    assert plan["damage_modifiers"] == [
        {"source": "Fighting Style: Dueling", "value": 2}
    ]


def test_qualified_multiattack_preserves_recorded_weapon_composition() -> None:
    captain = _actor("captain", hp=65)
    captain["sheet"]["inventory"]["items"] = [
        {
            "id": "scimitar",
            "name": "Scimitar",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d6",
                "damage_type": "slashing",
                "properties": ["finesse", "light"],
            },
        },
        {
            "id": "dagger",
            "name": "Dagger",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "off_hand",
            "mechanics": {
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d4",
                "damage_type": "piercing",
                "properties": ["finesse", "light", "thrown"],
                "thrown_normal_range_ft": 20,
                "thrown_long_range_ft": 60,
            },
        },
    ]
    captain["sheet"]["inventory"]["equipment_slots"].update(
        {"main_hand": "scimitar", "off_hand": "dagger"}
    )
    captain["sheet"]["content"]["activities"] = [
        {
            "id": "bandit-captain-multiattack",
            "name": "Multiattack (Armed Form Only)",
            "source_key": "Bandit Captain",
            "activation": {"type": "action"},
            "choices": {
                "multiattack_options": [
                    {
                        "id": "melee",
                        "attacks": [
                            {"weapon_id": "scimitar", "attack_mode": "melee", "count": 2},
                            {"weapon_id": "dagger", "attack_mode": "melee", "count": 1},
                        ],
                    },
                    {
                        "id": "ranged",
                        "attacks": [
                            {"weapon_id": "dagger", "attack_mode": "ranged", "count": 2}
                        ],
                    },
                ]
            },
        }
    ]
    captain["derived"] = derive_character_sheet(captain["sheet"])
    assert captain["derived"]["attacks_per_action"] == 1
    assert {item["id"] for item in captain["derived"]["multiattack_options"]} == {
        "melee",
        "ranged",
    }
    target = _actor("target", hp=65)
    captain.update(
        initiative=20,
        tie_breaker=0,
        position={"x": 0, "y": 0},
        disposition="hostile",
    )
    target.update(
        initiative=10,
        tie_breaker=0,
        position={"x": 5, "y": 0},
        disposition="friendly",
    )
    encounter = start_encounter([captain, target])

    encounter, first = pay_attack_action(
        encounter,
        captain,
        weapon_id="scimitar",
        attack_mode="melee",
        multiattack_option_id="melee",
    )
    assert first["attack_count"] == 3
    encounter, _ = pay_attack_action(
        encounter, captain, weapon_id="scimitar", attack_mode="melee"
    )
    with pytest.raises(ValueError, match="remaining Multiattack"):
        pay_attack_action(
            encounter, captain, weapon_id="scimitar", attack_mode="melee"
        )
    encounter, _ = pay_attack_action(
        encounter, captain, weapon_id="dagger", attack_mode="melee"
    )
    current = encounter["combatants"][encounter["turn_index"]]
    assert current["turn_budget"]["attack_budget"] == 0
    assert "multiattack" not in current.get("turn_flags", {})

    ordinary = start_encounter([captain, target])
    ordinary, payment = pay_attack_action(
        ordinary, captain, weapon_id="scimitar", attack_mode="melee"
    )
    assert payment["kind"] == "attack_action"
    assert payment["attack_count"] == 1
    assert ordinary["combatants"][0]["turn_budget"]["attack_budget"] == 0


def test_mixed_multiattack_pays_one_weapon_and_one_source_activity() -> None:
    attacker = _actor("intellect-devourer", hp=21)
    attacker["sheet"]["inventory"]["items"] = [
        {
            "id": "claws",
            "name": "Claws",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "melee",
                "attack_ability": "dexterity",
                "damage_formula": "2d4",
                "damage_type": "slashing",
                "properties": [],
            },
        }
    ]
    attacker["sheet"]["inventory"]["equipment_slots"]["main_hand"] = "claws"
    attacker["sheet"]["content"]["activities"] = [
        {
            "id": "multiattack-activity",
            "name": "Multiattack",
            "activation": {"type": "action"},
            "choices": {
                "multiattack_options": [
                    {
                        "id": "claws-and-devour",
                        "attacks": [
                            {
                                "weapon_id": "claws",
                                "attack_mode": "melee",
                                "count": 1,
                            }
                        ],
                        "activities": [
                            {
                                "activity_id": "devour-intellect-action",
                                "count": 1,
                            }
                        ],
                    }
                ]
            },
        },
        {
            "id": "devour-intellect-action",
            "name": "Devour Intellect",
            "activation": {"type": "action"},
        },
    ]
    attacker["derived"] = derive_character_sheet(attacker["sheet"])
    target = _actor("target")
    attacker.update(initiative=20, tie_breaker=0)
    target.update(initiative=10, tie_breaker=0)
    encounter = start_encounter([attacker, target])

    encounter, attack_payment = pay_attack_action(
        encounter,
        attacker,
        weapon_id="claws",
        attack_mode="melee",
        multiattack_option_id="claws-and-devour",
    )
    encounter, activity_payment = pay_multiattack_activity(
        encounter,
        attacker["id"],
        activity_id="devour-intellect-action",
    )

    assert attack_payment["attack_count"] == 2
    assert activity_payment == {
        "kind": "multiattack_activity_followup",
        "activity_id": "devour-intellect-action",
        "option_id": "claws-and-devour",
    }
    current = encounter["combatants"][encounter["turn_index"]]
    assert current["turn_budget"]["main_action"] == 0
    assert current["turn_budget"]["attack_budget"] == 0
    assert "multiattack" not in current.get("turn_flags", {})


def test_devour_intellect_resolves_damage_score_reduction_and_stun() -> None:
    source = _actor("intellect-devourer", hp=21)
    target = _actor("target", hp=30)
    target["sheet"]["abilities"]["intelligence"]["score"] = 10
    target["derived"] = derive_character_sheet(target["sheet"])
    spec = {
        "kind": "intellect_devourer_devour_intellect_2014",
        "range_ft": 10,
        "target_count": 1,
        "target_requirement": "has_brain",
        "save": {"ability": "intelligence", "dc": 12},
        "failure": {
            "damage_expression": "2d10",
            "damage_type": "psychic",
            "secondary_roll": "3d6",
            "secondary_threshold": "target_intelligence_score",
            "ability_override": {"ability": "intelligence", "score": 0},
            "condition": "stunned",
            "ends_when": "target_intelligence_score_at_least_1",
        },
        "source_excerpt": "Exact Devour Intellect source text.",
    }

    settled = resolve_source_save_effect(
        source,
        target,
        spec=spec,
        rng=_SequenceRng(1, 5, 6, 4, 4, 4),
    )
    sheet = settled["sheet"]
    result = settled["result"]

    assert result["save"]["success"] is False
    assert result["damage_roll"]["total"] == 11
    assert result["secondary_roll"]["total"] == 12
    assert result["ability_reduced"] is True
    assert sheet["combat"]["hp"]["value"] == 19
    assert "stunned" in sheet["conditions"]
    assert derive_character_sheet(sheet)["ability_scores"]["intelligence"] == 0
    assert derive_character_sheet(sheet)["ability_modifiers"]["intelligence"] == -5

    restored = remove_effect(sheet, result["effect_instance_id"])
    assert "stunned" not in restored["conditions"]
    assert derive_character_sheet(restored)["ability_scores"]["intelligence"] == 10


def test_body_thief_wins_contest_and_adopts_body_with_source_mental_scores() -> None:
    source = _actor("intellect-devourer", hp=21)
    source["sheet"]["abilities"]["intelligence"]["score"] = 12
    source["sheet"]["abilities"]["wisdom"]["score"] = 11
    source["sheet"]["abilities"]["charisma"]["score"] = 10
    source["derived"] = derive_character_sheet(source["sheet"])
    target = _actor("target", hp=19, ac=16)
    target["sheet"]["abilities"]["intelligence"]["score"] = 10
    target["sheet"]["abilities"]["wisdom"]["score"] = 15
    target["sheet"]["abilities"]["charisma"]["score"] = 8
    target["sheet"]["conditions"] = ["stunned"]
    target["sheet"]["effects"] = [
        {
            "id": "devour-intellect",
            "name": "Devour Intellect",
            "kind": "timed_conditions",
            "source": source["id"],
            "active": True,
            "concentration": False,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [
                {
                    "path": "abilities.intelligence.score",
                    "mode": "override",
                    "value": 0,
                },
                {"path": "conditions", "mode": "add", "value": "stunned"},
            ],
            "description": "Devour Intellect",
        }
    ]
    target["derived"] = derive_character_sheet(target["sheet"])
    spec = {
        "kind": "intellect_devourer_body_thief_2014",
        "range_ft": 5,
        "target_count": 1,
        "target_requirements": ["incapacitated", "humanoid"],
        "contest": {
            "source_ability": "intelligence",
            "target_ability": "intelligence",
            "ties": "no_winner",
        },
        "success": {
            "brain_consumed": True,
            "source_inside_host": True,
            "source_total_cover": True,
            "source_retains": [
                "intelligence",
                "wisdom",
                "charisma",
                "deep_speech",
                "telepathy",
                "traits",
            ],
            "source_adopts": "target_statistics_otherwise",
            "knowledge_transfer": "all_target_knowledge",
            "host_zero_hp": "source_must_leave",
        },
        "source_excerpt": "Exact Body Thief source text.",
    }

    settled = resolve_source_contest_effect(
        source,
        target,
        spec=spec,
        rng=_SequenceRng(15, 4),
    )
    sheet = settled["sheet"]
    result = settled["result"]

    assert result["success"] is True
    assert result["source_check"]["total"] == 16
    assert result["target_check"]["total"] == -1
    assert result["brain_consumed"] is True
    assert "stunned" not in sheet["conditions"]
    assert sheet["combat"]["hp"]["value"] == 19
    assert derive_character_sheet(sheet)["armor_class"] == 16
    assert derive_character_sheet(sheet)["ability_scores"] == {
        "strength": 16,
        "dexterity": 10,
        "constitution": 10,
        "intelligence": 12,
        "wisdom": 11,
        "charisma": 10,
    }
    assert sheet["effects"][0]["active"] is False
    assert sheet["effects"][0]["ended_reason"] == "body_thief_takeover"


def test_body_thief_tie_has_no_winner_and_does_not_change_target() -> None:
    source = _actor("intellect-devourer")
    target = _actor("target")
    spec = {
        "kind": "intellect_devourer_body_thief_2014",
        "range_ft": 5,
        "target_count": 1,
        "target_requirements": ["incapacitated", "humanoid"],
        "contest": {
            "source_ability": "intelligence",
            "target_ability": "intelligence",
            "ties": "no_winner",
        },
        "success": {
            "brain_consumed": True,
            "source_inside_host": True,
            "source_total_cover": True,
            "source_retains": [
                "intelligence",
                "wisdom",
                "charisma",
                "deep_speech",
                "telepathy",
                "traits",
            ],
            "source_adopts": "target_statistics_otherwise",
            "knowledge_transfer": "all_target_knowledge",
            "host_zero_hp": "source_must_leave",
        },
        "source_excerpt": "Exact Body Thief source text.",
    }
    source["sheet"]["abilities"]["intelligence"]["score"] = 12
    source["derived"] = derive_character_sheet(source["sheet"])
    target["sheet"]["abilities"]["intelligence"]["score"] = 10
    target["derived"] = derive_character_sheet(target["sheet"])

    settled = resolve_source_contest_effect(
        source,
        target,
        spec=spec,
        rng=_SequenceRng(9, 10),
    )

    assert settled["result"]["tie"] is True
    assert settled["result"]["success"] is False
    assert settled["result"]["outcome"] == "contest_not_won"
    assert settled["sheet"] == target["sheet"]


def test_attack_cannot_target_intellect_devourer_inside_host() -> None:
    attacker = _actor("attacker")
    target = _actor("intellect-devourer")
    attacker.update(initiative=20, tie_breaker=0)
    target.update(initiative=10, tie_breaker=0)
    encounter = start_encounter([attacker, target])
    target_combatant = next(
        item
        for item in encounter["combatants"]
        if item["actor_id"] == target["id"]
    )
    target_combatant["inside_host"] = {"host_actor_id": "host"}

    with pytest.raises(CombatEngineError, match="total cover inside its host"):
        preflight_attack(
            attacker,
            target,
            action={"weapon_id": "unarmed-strike"},
            encounter=encounter,
        )


def test_unstructured_multiattack_does_not_block_an_ordinary_weapon_attack() -> None:
    attacker = _actor("attacker")
    attacker["sheet"]["content"]["activities"] = [
        {
            "id": "unresolved-multiattack",
            "name": "Multiattack",
            "activation": {"type": "action"},
            "description": "The actor attacks and uses a descriptive command.",
        }
    ]
    attacker["derived"] = derive_character_sheet(attacker["sheet"])
    target = _actor("target")
    participants = [
        {**attacker, "initiative": 20, "tie_breaker": 0},
        {**target, "initiative": 10, "tie_breaker": 1},
    ]
    encounter = start_encounter(participants)

    encounter, payment = pay_attack_action(
        encounter,
        attacker,
        weapon_id="unarmed-strike",
        attack_mode="melee",
    )
    assert payment["kind"] == "attack_action"
    assert payment["attack_count"] == 1

    with pytest.raises(CombatEngineError, match="no structured options"):
        pay_attack_action(
            start_encounter(participants),
            attacker,
            weapon_id="unarmed-strike",
            attack_mode="melee",
            multiattack_option_id="invented",
        )


def test_thrown_weapon_requires_explicit_ranged_attack_mode() -> None:
    attacker = _actor("thrower")
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "dagger",
            "attack_type": "melee",
            "reach_ft": 5,
            "attack_bonus": 5,
            "damage_expression": "1d4 + 3",
            "damage_type": "piercing",
            "properties": ["finesse", "light", "thrown"],
            "thrown_range_ft": {"normal": 20, "long": 60},
        }
    ]
    target = _actor("target")
    attacker["position"] = {"x": 0, "y": 0}
    target["position"] = {"x": 10, "y": 0}

    with pytest.raises(ValueError, match="outside melee reach"):
        preflight_attack(attacker, target, action={"weapon_id": "dagger"})
    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "dagger", "attack_mode": "ranged"},
    )
    assert plan["attack_mode"] == "ranged"
    assert plan["melee_attack"] is False
    assert plan["range"]["normal_ft"] == 20


def test_zero_reach_weapon_can_attack_only_a_target_in_the_same_space() -> None:
    attacker = _actor("swarm")
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "bites",
            "attack_type": "melee",
            "reach_ft": 0,
            "attack_bonus": 2,
            "damage_expression": "2d6",
            "damage_type": "piercing",
            "properties": [],
        }
    ]
    target = _actor("target")
    attacker["position"] = {"x": 0, "y": 0}
    target["position"] = {"x": 0, "y": 0}

    plan = preflight_attack(attacker, target, action={"weapon_id": "bites"})
    assert plan["range"]["normal_ft"] == 0
    assert plan["range"]["distance_ft"] == 0

    target["position"] = {"x": 1, "y": 0}
    with pytest.raises(ValueError, match="outside melee reach"):
        preflight_attack(attacker, target, action={"weapon_id": "bites"})


def test_preflight_stops_on_unresolved_rules() -> None:
    attacker = _actor("attacker")
    target = _actor("target")
    attacker["derived"]["unresolved_rules"] = ["effect:unknown"]
    with pytest.raises(NeedsRulingError):
        preflight_attack(attacker, target, action={"attack_bonus": 3})


def test_encounter_uses_actor_references_and_turn_budget() -> None:
    encounter = start_encounter([_actor("a"), _actor("b")], rng=random.Random(1))
    assert encounter["active"] is True
    assert {item["actor_id"] for item in encounter["combatants"]} == {"a", "b"}
    assert encounter["combatants"][0]["turn_budget"]["reaction"] == 1


def test_encounter_validates_every_participant_before_rolling_initiative() -> None:
    valid = _actor("valid")
    invalid = _actor("invalid")
    invalid["sheet"]["combat"]["exhaustion"] = 6
    rng = _SequenceRng(7)

    with pytest.raises(CombatEngineError, match="exhaustion level 6"):
        start_encounter([valid, invalid], rng=rng)

    assert rng.values == [7]


def test_initiative_ties_require_explicit_tie_breakers() -> None:
    with pytest.raises(NeedsRulingError, match="tie_breaker") as npc_tie:
        start_encounter(
            [{**_actor("a"), "initiative": 10}, {**_actor("b"), "initiative": 10}]
        )
    assert npc_tie.value.ruling_kind == "agent_dm_adjudication"

    with pytest.raises(NeedsRulingError, match="tie_breaker") as pc_tie:
        start_encounter(
            [
                {**_actor("pc-a"), "character_type": "pc", "initiative": 10},
                {**_actor("pc-b"), "character_type": "pc", "initiative": 10},
            ]
        )
    assert pc_tie.value.ruling_kind == "player_owned_choice"


def test_half_cover_uses_the_rules_ac_bonus() -> None:
    attacker = _actor("attacker")
    target = _actor("target", ac=10)
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "sword",
            "attack_bonus": 5,
            "damage_expression": "1",
            "damage_type": "slashing",
        }
    ]
    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "sword", "context": {"cover": {"degree": "half"}}},
    )
    assert plan["target_ac"] == 12
    assert plan["cover"] == {
        "degree": "half",
        "armor_class_bonus": 2,
    }


def test_cover_uses_only_the_rules_defined_degrees() -> None:
    attacker = _actor("attacker")
    target = _actor("target", ac=10)
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "sword",
            "attack_bonus": 5,
            "damage_expression": "1",
            "damage_type": "slashing",
        }
    ]

    three_quarters = preflight_attack(
        attacker,
        target,
        action={
            "weapon_id": "sword",
            "context": {"cover": {"degree": "three_quarters"}},
        },
    )
    assert three_quarters["target_ac"] == 15
    assert three_quarters["cover"] == {
        "degree": "three_quarters",
        "armor_class_bonus": 5,
    }
    _updated_attacker, _updated_target, resolved = resolve_attack_action(
        attacker,
        target,
        plan=three_quarters,
        rng=random.Random(0),
    )
    assert resolved["cover"] == three_quarters["cover"]

    with pytest.raises(CombatEngineError, match="total cover"):
        preflight_attack(
            attacker,
            target,
            action={
                "weapon_id": "sword",
                "context": {"cover": {"degree": "total"}},
            },
        )
    with pytest.raises(CombatEngineError, match="rules-defined degree"):
        preflight_attack(
            attacker,
            target,
            action={
                "weapon_id": "sword",
                "context": {"cover": {"degree": "half", "ac_bonus": 9}},
            },
        )
    with pytest.raises(CombatEngineError, match="cover degree"):
        preflight_attack(
            attacker,
            target,
            action={
                "weapon_id": "sword",
                "context": {"cover": {"degree": "nine_tenths"}},
            },
        )


def test_help_grants_and_then_consumes_attack_advantage() -> None:
    attacker = _actor("attacker")
    helper = _actor("helper")
    target = _actor("target")
    for actor in (attacker, helper, target):
        actor["initiative"] = {"attacker": 20, "helper": 15, "target": 10}[actor["id"]]
        actor["tie_breaker"] = 0
    attacker["position"] = {"x": 0, "y": 0}
    helper["position"] = {"x": 1, "y": 0}
    target["position"] = {"x": 1, "y": 0}
    attacker["disposition"] = helper["disposition"] = "friendly"
    target["disposition"] = "hostile"
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {"item_id": "sword", "attack_bonus": 5, "damage_expression": "1", "damage_type": "slashing"}
    ]
    encounter = start_encounter([attacker, helper, target])
    encounter["combatants"][1]["turn_flags"] = {"helping": {"target_id": "attacker"}}
    plan = preflight_attack(attacker, target, action={"weapon_id": "sword"}, encounter=encounter)
    assert plan["helped_by"] == "helper"
    assert "help" in plan["advantage_sources"]


def test_next_attack_advantage_uses_active_target_effect() -> None:
    attacker = _actor("attacker")
    target = _actor("target")
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "sword",
            "attack_bonus": 5,
            "damage_expression": "1",
            "damage_type": "slashing",
        }
    ]
    attacker.update(
        initiative=20,
        tie_breaker=0,
        position={"x": 0, "y": 0},
        disposition="friendly",
    )
    target.update(
        initiative=10,
        tie_breaker=0,
        position={"x": 1, "y": 0},
        disposition="hostile",
    )
    encounter = start_encounter([attacker, target])
    encounter["ongoing_effects"] = [
        {
            "id": "guiding-bolt-mark",
            "kind": "next_attack_advantage",
            "source_actor_id": "cleric",
            "target_id": "target",
            "active": True,
        }
    ]

    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "sword"},
        encounter=encounter,
    )

    assert plan["advantage"] is True
    assert plan["next_attack_advantage_effect_id"] == "guiding-bolt-mark"
    assert "guiding-bolt-mark" in plan["advantage_sources"]


def test_sneak_attack_requires_card_feature_and_records_critical_bonus_damage() -> None:
    rogue = _rogue()
    ally = _actor("ally")
    target = _actor("target", hp=30, ac=1)
    rogue.update(initiative=20, tie_breaker=0, position={"x": 0, "y": 0}, disposition="friendly")
    ally.update(initiative=15, tie_breaker=0, position={"x": 1, "y": 0}, disposition="friendly")
    target.update(initiative=10, tie_breaker=0, position={"x": 1, "y": 0}, disposition="hostile")
    encounter = start_encounter([rogue, ally, target])

    plan = preflight_attack(
        rogue,
        target,
        action={"weapon_id": "dagger", "use_sneak_attack": True},
        encounter=encounter,
    )
    assert plan["sneak_attack"]["expression"] == "1d6"
    assert plan["sneak_attack"]["eligibility"] == "adjacent_enemy"

    _, updated_target, result = resolve_attack_action(
        rogue,
        target,
        plan=plan,
        rng=random.Random(5),
    )
    assert result["weapon_id"] == "dagger"
    assert result["attack_mode"] == "melee"
    assert result["critical"] is True
    assert result["sneak_attack"]["used"] is True
    assert result["sneak_attack"]["rolled_expression"] == "2d6"
    assert result["damage"]["sneak_attack"] == result["sneak_attack"]
    assert updated_target["sheet"]["combat"]["hp"]["value"] < 29


def test_sneak_attack_enforces_once_per_turn_weapon_and_disadvantage_boundaries() -> None:
    rogue = _rogue()
    ally = _actor("ally")
    target = _actor("target", ac=1)
    rogue.update(initiative=20, tie_breaker=0, position={"x": 0, "y": 0}, disposition="friendly")
    ally.update(initiative=15, tie_breaker=0, position={"x": 1, "y": 0}, disposition="friendly")
    target.update(initiative=10, tie_breaker=0, position={"x": 1, "y": 0}, disposition="hostile")
    encounter = start_encounter([rogue, ally, target])
    turn_token = f"1:0:{rogue['id']}"
    encounter["combatants"][0]["turn_flags"] = {"sneak_attack_turn_token": turn_token}
    with pytest.raises(Exception, match="already been used"):
        preflight_attack(
            rogue,
            target,
            action={"weapon_id": "dagger", "use_sneak_attack": True},
            encounter=encounter,
        )

    encounter["combatants"][0].pop("turn_flags")
    with pytest.raises(Exception, match="disadvantage"):
        preflight_attack(
            rogue,
            target,
            action={
                "weapon_id": "dagger",
                "use_sneak_attack": True,
                "context": {"disadvantage": True},
            },
            encounter=encounter,
        )

    rogue["derived"]["inventory"]["weapon_attacks"][0]["properties"] = ["light"]
    with pytest.raises(Exception, match="finesse or ranged"):
        preflight_attack(
            rogue,
            target,
            action={
                "weapon_id": "dagger",
                "use_sneak_attack": True,
                "context": {"advantage": True},
            },
            encounter=encounter,
        )


def test_multi_damage_preserves_types_and_massive_damage() -> None:
    actor = _actor("target", hp=10)
    result = apply_damage_parts_to_sheet(
        actor["sheet"],
        [{"amount": 4, "damage_type": "fire"}, {"amount": 10, "damage_type": "cold"}],
    )
    assert len(result["parts"]) == 2
    assert "unconscious" in result["sheet"]["conditions"]
    assert "dead" not in result["sheet"]["conditions"]


@pytest.mark.parametrize("ruleset", ["2014", "2024"])
def test_multi_damage_kills_target_that_does_not_use_death_saves(ruleset: str) -> None:
    actor = _actor("monster", hp=5)

    result = apply_damage_parts_to_sheet(
        actor["sheet"],
        [{"amount": 5, "damage_type": "radiant"}],
        ruleset=ruleset,
        death_saves=False,
    )

    assert {"dead", "prone"} <= set(result["sheet"]["conditions"])
    assert "unconscious" not in result["sheet"]["conditions"]


def test_simultaneous_damage_parts_create_one_concentration_dc_from_total() -> None:
    actor = _actor("target", hp=30)
    actor["sheet"]["effects"] = [
        {
            "id": "bless",
            "name": "Bless",
            "kind": "concentration",
            "source": "spell.cast",
            "source_spell_id": "bless",
            "active": True,
            "concentration": True,
            "duration": {"period": "round", "remaining": 10},
            "changes": [],
            "description": "",
        }
    ]
    result = apply_damage_parts_to_sheet(
        actor["sheet"],
        [{"amount": 12, "damage_type": "fire"}, {"amount": 12, "damage_type": "cold"}],
    )
    assert result["concentration"]["dc"] == 12
    assert result["after_hp"] == 6


def test_zero_hp_ends_concentration_with_a_schema_valid_audit_reason() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["effects"] = [
        {
            "id": "bless",
            "name": "Bless",
            "kind": "concentration",
            "active": True,
            "concentration": True,
            "duration": {"period": "minute", "remaining": 1},
            "changes": [],
            "description": "",
        }
    ]

    damaged = apply_damage_to_sheet(
        validate_character_sheet(actor["sheet"]),
        amount=10,
        damage_type="fire",
    )

    assert damaged["ended_effect_ids"] == ["bless"]
    effect = damaged["sheet"]["effects"][0]
    assert effect["active"] is False
    assert effect["ended_reason"] == "unconscious"
    assert validate_character_sheet(damaged["sheet"])["effects"][0] == effect


def test_zero_hp_ends_invisibility_concentration_and_clears_condition() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["conditions"] = ["invisible"]
    actor["sheet"]["content"]["spells"] = [
        {
            "id": "dnd5e.content.srd2014.spell.invisibility",
            "name": "Invisibility",
            "level": 2,
        }
    ]
    actor["sheet"]["effects"] = [
        {
            "id": "invisibility",
            "name": "Invisibility",
            "kind": "concentration",
            "source_spell_id": "dnd5e.content.srd2014.spell.invisibility",
            "active": True,
            "concentration": True,
            "duration": {"period": "hour", "remaining": 1},
            "changes": [],
        }
    ]

    damaged = apply_damage_to_sheet(
        validate_character_sheet(actor["sheet"]),
        amount=10,
        damage_type="fire",
    )

    assert damaged["ended_effect_ids"] == ["invisibility"]
    assert "invisible" not in damaged["sheet"]["conditions"]


def test_failed_concentration_save_records_why_the_effect_ended() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["effects"] = [
        {
            "id": "bless",
            "name": "Bless",
            "kind": "concentration",
            "active": True,
            "concentration": True,
            "duration": {"period": "minute", "remaining": 1},
        }
    ]

    resolved = apply_concentration_result(
        validate_character_sheet(actor["sheet"]),
        effect_ids=["bless"],
        success=False,
    )

    assert resolved["effects"][0]["active"] is False
    assert resolved["effects"][0]["ended_reason"] == "failed_concentration_save"
    validate_character_sheet(resolved)


def test_failed_concentration_save_clears_invisibility_condition() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["conditions"] = ["invisible"]
    actor["sheet"]["content"]["spells"] = [
        {"id": "invisibility", "name": "Invisibility", "level": 2}
    ]
    actor["sheet"]["effects"] = [
        {
            "id": "invisibility",
            "name": "Invisibility",
            "kind": "concentration",
            "source_spell_id": "invisibility",
            "active": True,
            "concentration": True,
            "duration": {"period": "hour", "remaining": 1},
            "changes": [],
        }
    ]

    resolved = apply_concentration_result(
        validate_character_sheet(actor["sheet"]),
        effect_ids=["invisibility"],
        success=False,
    )

    assert "invisible" not in resolved["conditions"]
    assert resolved["effects"][0]["ended_reason"] == "failed_concentration_save"


@pytest.mark.parametrize(
    "condition",
    ["incapacitated", "paralyzed", "petrified", "stunned", "unconscious"],
)
def test_incapacitating_conditions_end_concentration(condition: str) -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["conditions"] = [condition]
    actor["sheet"]["effects"] = [
        {
            "id": "bless",
            "name": "Bless",
            "kind": "concentration",
            "active": True,
            "concentration": True,
            "duration": {"period": "minute", "remaining": 1},
            "changes": [],
        }
    ]

    ended = end_concentration_for_incapacitating_conditions(actor["sheet"])

    assert ended == ["bless"]
    assert actor["sheet"]["effects"][0]["active"] is False
    assert actor["sheet"]["effects"][0]["ended_reason"] == "incapacitated"


def test_same_type_simultaneous_parts_round_resistance_only_once() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["traits"]["resistances"] = ["fire"]
    result = apply_damage_parts_to_sheet(
        actor["sheet"],
        [{"amount": 1, "damage_type": "fire"}, {"amount": 1, "damage_type": "fire"}],
    )
    assert result["applied_amount"] == 1
    assert result["after_hp"] == 9
    assert len(result["parts"]) == 1


def test_critical_multi_part_damage_at_zero_causes_two_failures_once() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["combat"]["hp"]["value"] = 0
    actor["sheet"]["conditions"] = ["prone", "unconscious"]
    result = apply_damage_parts_to_sheet(
        actor["sheet"],
        [{"amount": 1, "damage_type": "fire"}, {"amount": 1, "damage_type": "cold"}],
        critical=True,
    )
    assert result["sheet"]["combat"]["death_saves"]["failures"] == 2


def test_damage_at_zero_equal_to_maximum_causes_instant_death() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["combat"]["hp"]["value"] = 0
    actor["sheet"]["conditions"] = ["unconscious"]
    result = apply_damage_to_sheet(actor["sheet"], amount=10, damage_type="force")
    assert "dead" in result["sheet"]["conditions"]
    assert result["sheet"]["combat"]["death_saves"]["failures"] == 0


def test_falling_unconscious_also_leaves_actor_prone_after_healing() -> None:
    actor = _actor("target", hp=5)
    dropped = apply_damage_to_sheet(actor["sheet"], amount=5, damage_type="force")
    assert {"prone", "unconscious"} <= set(dropped["sheet"]["conditions"])


def test_disciple_of_life_uses_recorded_spell_and_cast_level_before_hp_clamp() -> None:
    target = _actor("target", hp=20)
    target["sheet"]["combat"]["hp"]["value"] = 1
    cleric = _actor("cleric")
    cleric["sheet"]["content"]["spells"] = [
        {
            "id": "cure-wounds",
            "name": "Cure Wounds",
            "level": 1,
        }
    ]
    cleric["sheet"]["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2014.feature.life-domain-disciple-of-life",
            "name": "Disciple of Life",
            "source_key": "Life Domain",
        }
    ]

    result = apply_healing_to_sheet(
        target["sheet"],
        amount=8,
        source_sheet=cleric["sheet"],
        spell_id="cure-wounds",
        spell_level=2,
    )

    assert result["after_hp"] == 13
    assert result["requested_amount"] == 8
    assert result["bonus_amount"] == 4
    assert result["source"]["modifiers"][0]["name"] == "Disciple of Life"

    zero_roll = apply_healing_to_sheet(
        target["sheet"],
        amount=0,
        source_sheet=cleric["sheet"],
        spell_id="cure-wounds",
        spell_level=1,
    )
    assert zero_roll["requested_amount"] == 0
    assert zero_roll["bonus_amount"] == 3
    assert zero_roll["amount"] == 3


def test_spell_healing_rejects_unrecorded_spells_and_illegal_cast_levels() -> None:
    target = _actor("target")
    source = _actor("source")
    source["sheet"]["content"]["spells"] = [
        {"id": "cure-wounds", "name": "Cure Wounds", "level": 1}
    ]

    with pytest.raises(ValueError, match="not recorded"):
        apply_healing_to_sheet(
            target["sheet"],
            amount=1,
            source_sheet=source["sheet"],
            spell_id="invented-heal",
            spell_level=1,
        )
    with pytest.raises(ValueError, match="legal cast level"):
        apply_healing_to_sheet(
            target["sheet"],
            amount=1,
            source_sheet=source["sheet"],
            spell_id="cure-wounds",
            spell_level=0,
        )


def test_petrified_condition_grants_resistance_to_every_damage_type_once() -> None:
    actor = _actor("target", hp=20)
    actor["sheet"]["conditions"] = ["petrified"]
    result = apply_damage_to_sheet(actor["sheet"], amount=9, damage_type="force")
    assert result["applied_amount"] == 4
    assert result["adjustment"] == "resistant"


def test_negative_damage_is_rejected_instead_of_silently_healing_or_nooping() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        apply_damage_to_sheet(_actor("target")["sheet"], amount=-1)


def test_massive_damage_uses_excess_over_zero_hp() -> None:
    actor = _actor("target", hp=10)
    result = apply_damage_to_sheet(actor["sheet"], amount=20, damage_type="cold")
    assert "dead" in result["sheet"]["conditions"]


def test_relentless_endurance_drops_to_one_unless_damage_kills_outright() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["content"]["features"].append(
        {
            "id": "relentless-endurance",
            "name": "Relentless Endurance",
            "source_key": "module-chunk:test",
            "description": (
                "When reduced to 0 hit points, he drops to 1 hit point instead "
                "(but can't do this again until he finishes a long rest)."
            ),
            "activation": {
                "type": "passive",
                "cost": 0,
                "trigger": "reduced to 0 hit points",
            },
            "uses": {
                "label": "uses",
                "value": 1,
                "max": 1,
                "recovers_on": "long_rest",
            },
            "choices": {
                "source_trait": {
                    "kind": "relentless_endurance",
                    "trigger": "reduced_to_zero",
                    "drop_to_hit_points": 1,
                    "requires_not_killed_outright": True,
                    "automatic": True,
                }
            },
            "rule_refs": ["module-chunk:test"],
        }
    )

    endured = apply_damage_to_sheet(
        actor["sheet"],
        amount=10,
        damage_type="cold",
        death_saves=False,
    )
    killed = apply_damage_to_sheet(
        actor["sheet"],
        amount=20,
        damage_type="cold",
        death_saves=False,
    )
    spent = apply_damage_to_sheet(
        endured["sheet"],
        amount=1,
        damage_type="cold",
        death_saves=False,
    )
    recovered = apply_rest(endured["sheet"], rest_type="long_rest")

    assert endured["after_hp"] == 1
    assert endured["relentless_endurance_triggered"] is True
    assert endured["relentless_endurance_use"] == {
        "feature_id": "relentless-endurance",
        "before_uses": 1,
        "after_uses": 0,
        "recovers_on": "long_rest",
    }
    assert endured["sheet"]["conditions"] == []
    assert killed["after_hp"] == 0
    assert killed["relentless_endurance_triggered"] is False
    assert "dead" in killed["sheet"]["conditions"]
    assert spent["after_hp"] == 0
    assert spent["relentless_endurance_triggered"] is False
    assert "dead" in spent["sheet"]["conditions"]
    recovered_feature = next(
        item
        for item in recovered["sheet"]["content"]["features"]
        if item["id"] == "relentless-endurance"
    )
    assert recovered_feature["uses"]["value"] == 1


def test_stunned_and_unconscious_cannot_move() -> None:
    encounter = start_encounter([_actor("a"), _actor("b")], rng=random.Random(1))
    current = encounter["combatants"][encounter["turn_index"]]
    current["conditions"] = ["stunned"]
    with pytest.raises(ValueError):
        spend_movement(encounter, current["actor_id"], 5)


def test_surprise_semantics_are_ruleset_specific() -> None:
    actors = [_actor("a"), _actor("b")]
    actors[0]["surprised"] = True
    old = start_encounter(actors, ruleset="2014", rng=random.Random(1))
    modern = start_encounter(actors, ruleset="2024", rng=random.Random(1))
    old_surprised = next(item for item in old["combatants"] if item["actor_id"] == "a")
    modern_surprised = next(item for item in modern["combatants"] if item["actor_id"] == "a")
    assert old_surprised["turn_budget"]["main_action"] == 0
    assert old_surprised["turn_budget"]["bonus_action"] == 0
    assert old_surprised["turn_budget"]["object_interaction"] == 0
    assert modern_surprised["turn_budget"]["main_action"] == 1
    assert modern_surprised["turn_budget"]["bonus_action"] == 1
    assert modern_surprised["turn_budget"]["object_interaction"] == 1


def test_2014_surprised_actor_regains_reaction_when_first_turn_ends() -> None:
    surprised = _actor("surprised")
    surprised.update(initiative=20, surprised=True)
    other = _actor("other")
    other["initiative"] = 10
    encounter = start_encounter([surprised, other], ruleset="2014")
    ended = end_turn(encounter, actor_id_value="surprised")
    combatant = next(item for item in ended["combatants"] if item["actor_id"] == "surprised")
    assert combatant["turn_budget"]["reaction"] == 1
    assert combatant["turn_budget"]["bonus_action"] == 0
    assert combatant["turn_budget"]["object_interaction"] == 0


def test_end_turn_skips_dead_actor_but_keeps_death_save_turn() -> None:
    first = _actor("first")
    first["initiative"] = 30
    dead = _actor("dead")
    dead["initiative"] = 20
    dead["sheet"]["conditions"] = ["dead", "prone"]
    dying = _actor("dying")
    dying.update(initiative=10, death_saves=True)
    dying["sheet"]["conditions"] = ["unconscious", "prone"]
    encounter = start_encounter([first, dead, dying])

    advanced = end_turn(encounter, actor_id_value="first")

    assert current_combatant(advanced)["actor_id"] == "dying"
    assert any(
        item.get("type") == "turn_skipped"
        and item.get("actor_id") == "dead"
        and item.get("reason") == "dead"
        for item in advanced["log"]
    )
    with pytest.raises(ValueError, match="death save"):
        end_turn(advanced, actor_id_value="dying")


def test_dodge_lasts_until_start_of_next_turn_and_affects_attacks() -> None:
    dodger = _actor("dodger")
    dodger["initiative"] = 20
    attacker = _actor("attacker")
    attacker.update(initiative=10, position={"x": 1, "y": 0})
    dodger["position"] = {"x": 0, "y": 0}
    encounter = start_encounter([dodger, attacker])
    encounter = resolve_common_action(encounter, actor_id_value="dodger", action="dodge")
    encounter = end_turn(encounter, actor_id_value="dodger")
    plan = preflight_attack(attacker, dodger, action={}, encounter=encounter)
    assert plan["disadvantage"] is True
    assert "target_dodging" in plan["disadvantage_sources"]
    encounter = end_turn(encounter, actor_id_value="attacker")
    dodger_state = next(item for item in encounter["combatants"] if item["actor_id"] == "dodger")
    assert not dict(dodger_state.get("turn_flags") or {}).get("dodging")


def test_paralyzed_target_is_automatic_critical_within_five_feet() -> None:
    attacker = _actor("attacker")
    attacker.update(initiative=20, position={"x": 0, "y": 0})
    target = _actor("target", hp=20, ac=1)
    target.update(initiative=10, position={"x": 1, "y": 0})
    target["sheet"]["conditions"] = ["paralyzed"]
    target["derived"] = derive_character_sheet(target["sheet"])
    encounter = start_encounter([attacker, target])
    plan = preflight_attack(attacker, target, action={}, encounter=encounter)
    assert plan["automatic_critical_on_hit"] is True
    _, _, result = resolve_attack_action(attacker, target, plan=plan, rng=random.Random(1))
    assert result["hit"] is True
    assert result["critical"] is True


def test_unseen_attacker_and_target_apply_opposed_attack_modifiers() -> None:
    attacker = _actor("attacker")
    attacker.update(initiative=20, position={"x": 0, "y": 0}, hidden=True)
    target = _actor("target")
    target.update(initiative=10, position={"x": 1, "y": 0}, hidden=True)
    encounter = start_encounter([attacker, target])
    plan = preflight_attack(attacker, target, action={}, encounter=encounter)
    assert plan["advantage"] is True
    assert plan["disadvantage"] is True
    assert "attacker_unseen" in plan["advantage_sources"]
    assert "target_unseen" in plan["disadvantage_sources"]


def _kobold_attack_trait_actor(identifier: str) -> dict:
    actor = _actor(identifier)
    actor["sheet"]["content"]["features"] = [
        {
            "id": "pack-tactics-passive",
            "name": "Pack Tactics",
            "activation": {"type": "passive"},
            "choices": {
                "source_trait": {
                    "kind": "pack_tactics",
                    "trigger": "attack_roll",
                    "ally_within_target_ft": 5,
                    "requires_ally_not_incapacitated": True,
                    "grants": "advantage",
                    "automatic": True,
                }
            },
        },
        {
            "id": "sunlight-sensitivity-passive",
            "name": "Sunlight Sensitivity",
            "activation": {"type": "passive"},
            "choices": {
                "source_trait": {
                    "kind": "sunlight_sensitivity",
                    "trigger": "attack_roll_or_sight_perception",
                    "environment_fact": "direct_sunlight",
                    "grants": "disadvantage",
                    "automatic": True,
                }
            },
        },
    ]
    actor["derived"] = derive_character_sheet(actor["sheet"])
    return actor


def test_pack_tactics_uses_a_conscious_adjacent_ally() -> None:
    kobold = _kobold_attack_trait_actor("kobold")
    kobold.update(
        initiative=20,
        position={"x": 0, "y": 0},
        disposition="hostile",
    )
    ally = _actor("ally")
    ally.update(
        initiative=10,
        position={"x": 2, "y": 0},
        disposition="hostile",
    )
    target = _actor("target")
    target.update(
        initiative=5,
        position={"x": 1, "y": 0},
        disposition="friendly",
    )
    encounter = start_encounter([kobold, ally, target])
    rules = resolution_context(
        {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []}
    )

    plan = preflight_attack(
        kobold,
        target,
        action={"context": {"direct_sunlight": False}},
        encounter=encounter,
        rules=rules,
    )
    assert plan["advantage"] is True
    assert plan["pack_tactics_ally_id"] == "ally"
    assert "pack_tactics" in plan["advantage_sources"]
    assert any(
        item["mechanic_id"] == "dnd5e.core.attack.pack_tactics"
        for item in plan["rule_receipts"]
    )

    ally_state = next(
        item
        for item in encounter["combatants"]
        if item["actor_id"] == "ally"
    )
    ally_state["conditions"] = ["stunned"]
    without_ally = preflight_attack(
        kobold,
        target,
        action={"context": {"direct_sunlight": False}},
        encounter=encounter,
        rules=rules,
    )
    assert without_ally["advantage"] is False
    assert without_ally["pack_tactics_ally_id"] is None


def test_sunlight_sensitivity_requires_and_uses_the_environment_fact() -> None:
    kobold = _kobold_attack_trait_actor("kobold")
    kobold.update(
        initiative=20,
        position={"x": 0, "y": 0},
        disposition="hostile",
    )
    target = _actor("target")
    target.update(
        initiative=10,
        position={"x": 1, "y": 0},
        disposition="friendly",
    )
    encounter = start_encounter([kobold, target])
    rules = resolution_context(
        {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []}
    )

    with pytest.raises(NeedsRulingError, match="direct sunlight"):
        preflight_attack(
            kobold,
            target,
            action={},
            encounter=encounter,
            rules=rules,
        )

    plan = preflight_attack(
        kobold,
        target,
        action={"context": {"direct_sunlight": True}},
        encounter=encounter,
        rules=rules,
    )
    assert plan["disadvantage"] is True
    assert "sunlight_sensitivity" in plan["disadvantage_sources"]
    assert any(
        item["mechanic_id"]
        == "dnd5e.core.attack.sunlight_sensitivity"
        for item in plan["rule_receipts"]
    )


@pytest.mark.parametrize(
    ("operation", "expected_kind"),
    [
        ({"op": "ruling.require", "id": "weather"}, "agent_dm_adjudication"),
        ({"op": "choice.require", "id": "maneuver"}, "player_owned_choice"),
    ],
)
def test_attack_extension_preserves_pending_owner(
    operation: dict[str, str],
    expected_kind: str,
) -> None:
    attacker = _actor("attacker")
    target = _actor("target")
    rules = resolution_context(
        {
            "edition": "2014",
            "fingerprint": "",
            "lock": [],
            "mechanics": [
                {
                    "id": "dnd5e.extension.attack.pending",
                    "event": "attack.preflight",
                    "operations": [operation],
                    "citations": [{"source": "local:extension"}],
                }
            ],
        }
    )

    with pytest.raises(NeedsRulingError) as raised:
        preflight_attack(attacker, target, action={}, rules=rules)

    assert raised.value.ruling_kind == expected_kind


def test_2024_invisible_actor_has_initiative_advantage() -> None:
    invisible = _actor("invisible")
    invisible["sheet"]["conditions"] = ["invisible"]
    invisible["derived"] = derive_character_sheet(invisible["sheet"])
    encounter = start_encounter([invisible], ruleset="2024", rng=random.Random(1))
    assert len(encounter["combatants"][0]["initiative_roll"]["rolls"]) == 2


def test_2024_exhaustion_reduces_speed_attacks_and_death_saves() -> None:
    exhausted = _actor("exhausted")
    exhausted["sheet"]["edition"] = "2024"
    exhausted["sheet"]["combat"]["exhaustion"] = 1
    exhausted["derived"] = derive_character_sheet(exhausted["sheet"])
    encounter = start_encounter([exhausted], ruleset="2024", rng=random.Random(1))
    assert encounter["combatants"][0]["turn_budget"]["speed"] == 25
    plan = preflight_attack(exhausted, _actor("target"), action={}, encounter=encounter)
    assert plan["attack_bonus"] == 3

    exhausted["sheet"]["combat"]["hp"]["value"] = 0
    exhausted["sheet"]["conditions"] = ["prone", "unconscious"]
    save = resolve_death_save_to_sheet(exhausted["sheet"], rng=random.Random(7))
    assert save["natural"] == 11
    assert save["total"] == 9
    assert save["failures"] == 1

    legacy = _actor("legacy-exhausted")
    legacy["sheet"]["combat"]["hp"]["value"] = 0
    legacy["sheet"]["combat"]["exhaustion"] = 3
    legacy_save = resolve_death_save_to_sheet(legacy["sheet"], rng=random.Random(7))
    assert len(legacy_save["rolls"]) == 2


def test_active_roll_effects_apply_to_attacks_saves_and_ability_checks() -> None:
    actor = _actor("revived")
    actor["sheet"]["effects"].append(
        {
            "id": "raise-dead-ordeal",
            "name": "Raise Dead ordeal",
            "kind": "revival_ordeal",
            "source": "allied-cleric",
            "source_spell_id": "dnd5e.content.srd2014.spell.raise-dead",
            "active": True,
            "concentration": False,
            "duration": {"period": "long_rest", "remaining": 4},
            "changes": [
                {"path": "rolls.attack.bonus", "mode": "add", "value": -4},
                {"path": "rolls.ability_check.bonus", "mode": "add", "value": -4},
                {"path": "rolls.saving_throw.bonus", "mode": "add", "value": -4},
            ],
            "description": "Raise Dead ordeal.",
        }
    )
    actor["derived"] = derive_character_sheet(actor["sheet"])
    target = _actor("target")

    plan = preflight_attack(actor, target, action={})
    assert plan["effect_roll_bonus"] == -4
    assert plan["attack_bonus"] == 1
    check = resolve_actor_check(
        actor,
        kind="ability",
        ability="strength",
        dc=10,
        rng=_SequenceRng(10),
    )
    save = resolve_actor_check(
        actor,
        kind="save",
        ability="strength",
        dc=10,
        rng=_SequenceRng(10),
    )
    assert check["effect_roll_bonus"] == -4
    assert save["effect_roll_bonus"] == -4
    assert check["total"] == 9
    assert save["total"] == 9


def test_condition_saving_throw_effects_are_not_left_to_client_modifiers() -> None:
    actor = _actor("target")
    actor["sheet"]["conditions"] = ["paralyzed"]
    actor["derived"] = derive_character_sheet(actor["sheet"])
    result = resolve_actor_check(
        actor,
        kind="save",
        ability="dexterity",
        dc=1,
        ruleset="2024",
        rng=random.Random(5),
    )
    assert result["automatic_failure"] is True
    assert result["success"] is False

    actor = _actor("exhausted")
    actor["sheet"]["edition"] = "2024"
    actor["sheet"]["combat"]["exhaustion"] = 2
    actor["derived"] = derive_character_sheet(actor["sheet"])
    save = resolve_actor_check(
        actor,
        kind="save",
        ability="dexterity",
        dc=30,
        ruleset="2024",
        rng=random.Random(1),
    )
    assert save["bonus"] == -4


def test_equipped_armor_automatically_imposes_stealth_disadvantage() -> None:
    actor = _actor("armored-scout")
    sheet, armor_id = add_inventory_item(
        actor["sheet"],
        {
            "id": "scale-mail",
            "name": "Scale mail",
            "kind": "armor",
            "mechanics": {
                "base_ac": 14,
                "dexterity_mode": "max",
                "dexterity_max": 2,
                "stealth_disadvantage": True,
            },
        },
    )
    actor["sheet"] = equip_inventory_item(sheet, armor_id, "armor")
    actor["derived"] = derive_character_sheet(actor["sheet"])

    result = resolve_actor_check(
        actor,
        kind="ability",
        ability="stealth",
        dc=10,
        rng=_SequenceRng(18, 2),
    )

    assert result["rolls"] == [18, 2]
    assert result["natural"] == 2
    assert result["roll_mode"] == "disadvantage"
    assert result["disadvantage_applied"] is True
    assert result["success"] is False


def test_death_save_persists_nat20_recovery() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["combat"]["hp"]["value"] = 0
    actor["sheet"]["conditions"] = ["unconscious"]
    result = resolve_death_save_to_sheet(actor["sheet"], rng=random.Random(5))
    assert result["outcome"] == "revived"
    assert result["sheet"]["combat"]["hp"]["value"] == 1
    assert "unconscious" not in result["sheet"]["conditions"]


def test_fatal_death_save_does_not_leave_actor_unconscious() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["combat"]["hp"]["value"] = 0
    actor["sheet"]["combat"]["death_saves"] = {"successes": 1, "failures": 2}
    actor["sheet"]["conditions"] = ["prone", "unconscious"]

    result = resolve_death_save_to_sheet(actor["sheet"], rng=_SequenceRng(2))

    assert result["outcome"] == "dead"
    assert set(result["sheet"]["conditions"]) == {"dead", "prone"}


def test_natural_one_caps_fatal_death_save_failures_at_schema_limit() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["combat"]["hp"]["value"] = 0
    actor["sheet"]["combat"]["death_saves"] = {"successes": 1, "failures": 2}
    actor["sheet"]["conditions"] = ["prone", "unconscious"]

    result = resolve_death_save_to_sheet(actor["sheet"], rng=_SequenceRng(1))

    assert result["outcome"] == "dead"
    assert result["failures"] == 3
    assert result["sheet"]["combat"]["death_saves"] == {
        "successes": 1,
        "failures": 3,
    }
    assert set(result["sheet"]["conditions"]) == {"dead", "prone"}


def test_stabilize_sheet_requires_zero_hp_and_clears_death_saves() -> None:
    actor = _actor("dying")
    actor["sheet"]["combat"]["hp"]["value"] = 0
    actor["sheet"]["combat"]["death_saves"] = {"successes": 1, "failures": 2}
    actor["sheet"]["conditions"] = ["prone", "unconscious"]

    result = stabilize_sheet(actor["sheet"])

    assert result["status"] == "stable"
    assert result["before_death_saves"] == {"successes": 1, "failures": 2}
    assert result["sheet"]["combat"]["death_saves"] == {"successes": 0, "failures": 0}
    assert set(result["sheet"]["conditions"]) == {"prone", "stable", "unconscious"}

    with pytest.raises(ValueError, match="0 hit points"):
        stabilize_sheet(_actor("healthy")["sheet"])
    dead = actor["sheet"] | {"conditions": ["dead"]}
    with pytest.raises(ValueError, match="dead creature"):
        stabilize_sheet(dead)


def test_movement_and_choice_window_are_explicit() -> None:
    encounter = start_encounter([_actor("a"), _actor("b")], rng=random.Random(1))
    current = encounter["combatants"][encounter["turn_index"]]["actor_id"]
    moved = spend_movement(encounter, current, 10, destination={"x": 1, "y": 2})
    assert moved["combatants"][encounter["turn_index"]]["turn_budget"]["movement"] == 20
    pending = add_choice_window(
        moved,
        kind="opportunity_attack",
        actor_id_value="b",
        event="a leaves reach",
        candidates=[{"id": "skip"}, {"id": "attack"}],
    )
    choice_id = pending["pending"][0]["id"]
    resolved = resolve_choice_window(
        pending,
        choice_id=choice_id,
        actor_id_value="b",
        selection={"id": "skip"},
    )
    assert not resolved["pending"]


def test_common_actions_pay_action_and_keep_tactical_state_explicit() -> None:
    encounter = start_encounter([_actor("a"), _actor("b")], rng=random.Random(1))
    current = encounter["combatants"][encounter["turn_index"]]["actor_id"]
    dashed = resolve_common_action(encounter, actor_id_value=current, action="dash")
    actor = dashed["combatants"][dashed["turn_index"]]
    assert actor["turn_budget"]["main_action"] == 0
    assert actor["turn_budget"]["movement"] == 60

    encounter = start_encounter([_actor("a"), _actor("b")], rng=random.Random(1))
    current = encounter["combatants"][encounter["turn_index"]]["actor_id"]
    readied = resolve_common_action(
        encounter,
        actor_id_value=current,
        action="ready",
        trigger="the foe enters reach",
        payload={"action": "attack"},
    )
    assert readied["readied"][0]["status"] == "armed"


def test_free_object_interaction_consumes_only_its_turn_budget() -> None:
    encounter = start_encounter([_actor("goblin")])

    interacted = resolve_common_action(
        encounter,
        actor_id_value="goblin",
        action="interact_object",
        payload={
            "object_description": "an eyeless hollowed-out pumpkin",
            "interaction": "remove",
        },
    )

    actor = current_combatant(interacted)
    assert actor is not None
    assert actor["turn_budget"]["object_interaction"] == 0
    assert actor["turn_budget"]["main_action"] == 1
    assert "interact_object" not in available_actions(interacted, "goblin")
    assert actor["turn_flags"]["object_interaction_declared"] == {
        "object_description": "an eyeless hollowed-out pumpkin",
        "interaction": "remove",
    }
    with pytest.raises(CombatEngineError, match="legal action payment"):
        resolve_common_action(
            interacted,
            actor_id_value="goblin",
            action="interact_object",
            payload={
                "object_description": "a belt pouch",
                "interaction": "open",
            },
        )


def test_common_cast_can_pay_available_bonus_action_without_spending_main_action() -> None:
    encounter = start_encounter([_actor("a"), _actor("b")], rng=random.Random(1))
    current = encounter["combatants"][encounter["turn_index"]]["actor_id"]

    assert "bonus_action" in available_actions(encounter, current)
    cast = resolve_common_action(
        encounter,
        actor_id_value=current,
        action="cast",
        payment="bonus_action",
        payload={"spell_id": "healing-word"},
    )

    actor = cast["combatants"][cast["turn_index"]]
    assert actor["turn_budget"]["bonus_action"] == 0
    assert actor["turn_budget"]["main_action"] == 1
    assert actor["turn_flags"]["cast_declared"]["spell_id"] == "healing-word"
    assert "bonus_action" not in available_actions(cast, current)


def test_action_surge_grants_one_current_turn_action_and_never_carries_forward() -> None:
    encounter = start_encounter([_actor("a"), _actor("b")], rng=random.Random(1))
    actor_id = encounter["combatants"][encounter["turn_index"]]["actor_id"]
    surged, effect = settle_core_activity_effect(
        encounter,
        actor_id_value=actor_id,
        activity_id="dnd5e.content.srd2014.feature.fighter-action-surge",
    )

    current = surged["combatants"][surged["turn_index"]]
    assert effect == {
        "kind": "action_surge",
        "extra_actions_granted": 1,
        "extra_actions_available": 1,
    }
    assert current["turn_budget"]["extra_action"] == 1
    with pytest.raises(ValueError, match="once on the same turn"):
        settle_core_activity_effect(
            surged,
            actor_id_value=actor_id,
            activity_id="dnd5e.content.srd2014.feature.fighter-action-surge",
        )

    next_turn = end_turn(surged, actor_id_value=actor_id)
    other_id = next_turn["combatants"][next_turn["turn_index"]]["actor_id"]
    returned = end_turn(next_turn, actor_id_value=other_id)
    returned_actor = returned["combatants"][returned["turn_index"]]
    assert returned_actor["actor_id"] == actor_id
    assert returned_actor["turn_budget"]["extra_action"] == 0


def test_cunning_action_settles_dash_and_disengage_but_not_hide_outcome() -> None:
    rogue = _actor("rogue")
    rogue["initiative"] = 20
    threat = _actor("threat")
    threat["initiative"] = 10
    encounter = start_encounter([rogue, threat])

    paid_dash = pay_activity_activation(
        encounter, actor_id_value="rogue", activation_type="bonus_action"
    )
    dashed, dash_effect = settle_core_activity_effect(
        paid_dash,
        actor_id_value="rogue",
        activity_id="dnd5e.content.srd2014.feature.rogue-cunning-action",
        declaration={"action": "Dash"},
    )
    assert dash_effect == {
        "kind": "cunning_action",
        "action": "dash",
        "requires_ruling": False,
    }
    assert dashed["combatants"][0]["turn_budget"]["movement"] == 60
    assert dashed["combatants"][0]["turn_budget"]["bonus_action"] == 0

    encounter = start_encounter([rogue, threat])
    paid_disengage = pay_activity_activation(
        encounter, actor_id_value="rogue", activation_type="bonus_action"
    )
    disengaged, effect = settle_core_activity_effect(
        paid_disengage,
        actor_id_value="rogue",
        activity_id="dnd5e.content.srd2014.feature.rogue-cunning-action",
        declaration={"action": "disengage"},
    )
    assert effect["requires_ruling"] is False
    assert disengaged["combatants"][0]["turn_flags"]["disengaged"] is True

    encounter = start_encounter([rogue, threat])
    paid_hide = pay_activity_activation(
        encounter, actor_id_value="rogue", activation_type="bonus_action"
    )
    hiding, effect = settle_core_activity_effect(
        paid_hide,
        actor_id_value="rogue",
        activity_id="dnd5e.content.srd2014.feature.rogue-cunning-action",
        declaration={"action": "hide", "cover": "larger ally"},
    )
    assert effect["requires_ruling"] is True
    assert effect["ruling_requirement"] == {
        "default_resolver": "agent",
        "ruling_kind": "source_or_scene_fact",
        "reason": (
            "Determine from the current cover, visibility, and observer facts whether "
            "hiding is possible and resolve the Stealth boundary."
        ),
    }
    assert hiding["combatants"][0]["hidden"] is False
    assert hiding["combatants"][0]["turn_flags"]["hide_declared"] == {
        "source_activity_id": "dnd5e.content.srd2014.feature.rogue-cunning-action",
        "declaration": {"action": "hide", "cover": "larger ally"},
    }


def test_aggressive_grants_only_separately_paid_movement_toward_visible_hostile() -> None:
    orc = _actor("orc")
    target = _actor("target")
    orc.update(
        initiative=20,
        tie_breaker=0,
        position={"x": 0, "y": 0},
        disposition="hostile",
    )
    target.update(
        initiative=10,
        tie_breaker=0,
        position={"x": 6, "y": 0},
        disposition="friendly",
    )
    encounter = start_encounter([orc, target])
    paid = pay_activity_activation(
        encounter,
        actor_id_value="orc",
        activation_type="bonus_action",
    )
    aggressive, effect = settle_core_activity_effect(
        paid,
        actor_id_value="orc",
        activity_id="dnd5e.core.monster.aggressive",
        declaration={"target_id": "target"},
    )

    assert effect == {
        "kind": "aggressive",
        "target_id": "target",
        "movement_granted_ft": 30,
    }
    assert aggressive["combatants"][0]["turn_budget"]["movement"] == 30
    ordinary = spend_movement(
        aggressive,
        "orc",
        5,
        destination={"x": -1, "y": 0},
    )
    assert ordinary["combatants"][0]["turn_budget"]["movement"] == 25
    with pytest.raises(CombatEngineError, match="must move toward"):
        spend_movement(
            ordinary,
            "orc",
            5,
            destination={"x": -2, "y": 0},
            movement_mode="aggressive",
        )
    moved = spend_movement(
        ordinary,
        "orc",
        5,
        destination={"x": 0, "y": 0},
        movement_mode="aggressive",
    )
    assert moved["combatants"][0]["turn_budget"]["movement"] == 25
    assert moved["combatants"][0]["turn_flags"]["aggressive_movement"][
        "remaining_ft"
    ] == 25


def test_magmin_illumination_toggles_with_a_paid_bonus_action() -> None:
    magmin = _actor("magmin")
    other = _actor("other")
    magmin["initiative"] = 20
    other["initiative"] = 10
    encounter = start_encounter([magmin, other])

    paid = pay_activity_activation(
        encounter,
        actor_id_value="magmin",
        activation_type="bonus_action",
    )
    lit, lit_effect = settle_core_activity_effect(
        paid,
        actor_id_value="magmin",
        activity_id="dnd5e.core.monster.ignited-illumination",
    )
    assert lit_effect == {
        "kind": "ignited_illumination",
        "ablaze": True,
        "bright_light_radius_ft": 10,
        "dim_light_radius_ft": 20,
    }
    assert lit["combatants"][0]["emitted_light"]["ignited_illumination"] is True

    other_turn = end_turn(lit, actor_id_value="magmin")
    returned = end_turn(other_turn, actor_id_value="other")
    paid_again = pay_activity_activation(
        returned,
        actor_id_value="magmin",
        activation_type="bonus_action",
    )
    dark, dark_effect = settle_core_activity_effect(
        paid_again,
        actor_id_value="magmin",
        activity_id="dnd5e.core.monster.ignited-illumination",
    )
    assert dark_effect["ablaze"] is False
    assert dark["combatants"][0]["emitted_light"] == {}


def test_battle_cry_grants_temporary_attack_advantage_and_bonus_attack() -> None:
    war_chief = _actor("war-chief")
    ally = _actor("ally")
    target = _actor("target")
    for actor, initiative, position, disposition in (
        (war_chief, 20, {"x": 0, "y": 0}, "hostile"),
        (ally, 15, {"x": 1, "y": 0}, "hostile"),
        (target, 10, {"x": 2, "y": 0}, "friendly"),
    ):
        actor.update(
            initiative=initiative,
            tie_breaker=0,
            position=position,
            disposition=disposition,
        )
        actor["derived"]["inventory"]["weapon_attacks"] = [
            {
                "item_id": "sword",
                "attack_type": "melee",
                "properties": [],
                "attack_bonus": 5,
                "damage_expression": "1",
                "damage_type": "slashing",
            }
        ]
    encounter = start_encounter([war_chief, ally, target])
    paid = pay_activity_activation(
        encounter,
        actor_id_value="war-chief",
        activation_type="action",
    )
    affected, effect = settle_core_activity_effect(
        paid,
        actor_id_value="war-chief",
        activity_id="dnd5e.core.monster.battle-cry",
        declaration={
            "targets": [
                {
                    "actor_id": "ally",
                    "can_hear": True,
                    "reason": "The ally is five feet away in the same open area.",
                }
            ]
        },
    )

    assert effect == {
        "kind": "battle_cry",
        "target_ids": ["ally"],
        "bonus_attack_available": True,
    }
    plan = preflight_attack(
        ally,
        target,
        action={"weapon_id": "sword"},
        encounter=affected,
        allow_out_of_turn=True,
        require_attack_action=False,
    )
    assert plan["advantage"] is True
    assert "battle_cry" in plan["advantage_sources"]
    attacked, payment = pay_attack_action(
        affected,
        war_chief,
        weapon_id="sword",
        attack_mode="melee",
    )
    assert payment == {
        "kind": "battle_cry_bonus_attack",
        "payment": "bonus_action",
        "attack_count": 1,
    }
    assert attacked["combatants"][0]["turn_budget"]["bonus_action"] == 0

    ally_turn = end_turn(attacked, actor_id_value="war-chief")
    assert "battle_cry_advantage" in ally_turn["combatants"][1]["turn_flags"]
    target_turn = end_turn(ally_turn, actor_id_value="ally")
    returned = end_turn(target_turn, actor_id_value="target")
    assert "battle_cry_advantage" not in returned["combatants"][1].get(
        "turn_flags", {}
    )


def test_battle_cry_requires_agent_supplied_hearing_fact() -> None:
    war_chief = _actor("war-chief")
    ally = _actor("ally")
    war_chief.update(
        initiative=20,
        tie_breaker=0,
        position={"x": 0, "y": 0},
    )
    ally.update(
        initiative=10,
        tie_breaker=0,
        position={"x": 1, "y": 0},
    )
    encounter = start_encounter([war_chief, ally])
    paid = pay_activity_activation(
        encounter,
        actor_id_value="war-chief",
        activation_type="action",
    )

    with pytest.raises(CombatEngineError, match="can_hear scene fact"):
        settle_core_activity_effect(
            paid,
            actor_id_value="war-chief",
            activity_id="dnd5e.core.monster.battle-cry",
            declaration={"targets": [{"actor_id": "ally"}]},
        )


def test_statblock_sneak_attack_uses_recorded_formula_without_rogue_levels() -> None:
    spy = _actor("spy")
    ally = _actor("ally")
    target = _actor("target")
    spy["sheet"]["content"]["features"] = [
        {
            "id": "sneak-attack-1-turn-passive",
            "name": "Sneak Attack (1/Turn)",
            "choices": {
                "source_trait": {
                    "kind": "sneak_attack",
                    "damage_formula": "2d6",
                    "uses_per_turn": 1,
                    "requires_finesse_or_ranged": False,
                    "ally_within_target_ft": 5,
                    "requires_ally_not_incapacitated": True,
                    "requires_no_disadvantage": True,
                }
            },
        }
    ]
    spy["derived"] = derive_character_sheet(spy["sheet"])
    spy["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "shortsword",
            "attack_type": "melee",
            "properties": [],
            "attack_bonus": 5,
            "damage_expression": "1d6 + 2",
            "damage_type": "piercing",
        }
    ]
    spy.update(
        initiative=20,
        tie_breaker=0,
        position={"x": 0, "y": 0},
        disposition="friendly",
    )
    ally.update(
        initiative=15,
        tie_breaker=0,
        position={"x": 1, "y": 0},
        disposition="friendly",
    )
    target.update(
        initiative=10,
        tie_breaker=0,
        position={"x": 1, "y": 0},
        disposition="hostile",
    )
    encounter = start_encounter([spy, ally, target])

    plan = preflight_attack(
        spy,
        target,
        action={"weapon_id": "shortsword", "use_sneak_attack": True},
        encounter=encounter,
    )

    assert plan["sneak_attack"]["expression"] == "2d6"
    assert plan["sneak_attack"]["eligibility"] == "adjacent_enemy"


def test_versatile_weapon_grip_uses_exact_alternate_damage_once() -> None:
    orc = _actor("orc")
    target = _actor("target")
    orc["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "spear",
            "attack_type": "melee",
            "properties": ["thrown", "versatile"],
            "attack_bonus": 6,
            "damage_formula": "1d6",
            "damage_bonus": 4,
            "damage_expression": "1d6 + 4",
            "damage_type": "piercing",
            "additional_damage": [
                {
                    "damage_formula": "1d8",
                    "damage_bonus": 0,
                    "damage_expression": "1d8",
                    "damage_type": "piercing",
                }
            ],
            "versatile_damage_formula": "2d8",
            "reach_ft": 5,
            "thrown_range_ft": {"normal": 20, "long": 60},
        }
    ]
    orc.update(
        initiative=20,
        tie_breaker=0,
        position={"x": 0, "y": 0},
        disposition="hostile",
    )
    target.update(
        initiative=10,
        tie_breaker=0,
        position={"x": 1, "y": 0},
        disposition="friendly",
    )
    encounter = start_encounter([orc, target])

    one_handed = preflight_attack(
        orc,
        target,
        action={"weapon_id": "spear", "weapon_grip": "one_handed"},
        encounter=encounter,
    )
    two_handed = preflight_attack(
        orc,
        target,
        action={"weapon_id": "spear", "weapon_grip": "two_handed"},
        encounter=encounter,
    )

    assert one_handed["damage_expression"] == "1d6 + 4"
    assert [part["damage_expression"] for part in one_handed["additional_damage"]] == [
        "1d8"
    ]
    assert two_handed["damage_expression"] == "2d8 + 4"
    assert two_handed["additional_damage"] == []
    assert two_handed["weapon_grip"] == "two_handed"

    shielded = deepcopy(orc)
    shielded_sheet, shield_id = add_inventory_item(
        shielded["sheet"],
        {
            "id": "shield",
            "name": "Shield",
            "kind": "shield",
            "mechanics": {"ac_bonus": 2, "magic_bonus": 0},
        },
    )
    shielded["sheet"] = equip_inventory_item(
        shielded_sheet,
        shield_id,
        "shield",
    )
    with pytest.raises(CombatEngineError, match="wielding a shield"):
        preflight_attack(
            shielded,
            target,
            action={"weapon_id": "spear", "weapon_grip": "two_handed"},
            encounter=encounter,
        )


def test_versatile_weapon_retains_damage_printed_after_alternate_formula() -> None:
    salamander = _actor("salamander")
    target = _actor("target")
    salamander["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "spear",
            "attack_type": "melee",
            "properties": ["thrown", "versatile"],
            "attack_bonus": 7,
            "damage_formula": "2d6",
            "damage_bonus": 4,
            "damage_expression": "2d6 + 4",
            "damage_type": "piercing",
            "additional_damage": [
                {
                    "damage_formula": "1d6",
                    "damage_bonus": 0,
                    "damage_expression": "1d6",
                    "damage_type": "fire",
                }
            ],
            "versatile_damage_formula": "2d8",
            "versatile_additional_damage": [
                {
                    "damage_formula": "1d6",
                    "damage_bonus": 0,
                    "damage_expression": "1d6",
                    "damage_type": "fire",
                }
            ],
            "reach_ft": 5,
            "thrown_range_ft": {"normal": 20, "long": 60},
        }
    ]

    plan = preflight_attack(
        salamander,
        target,
        action={"weapon_id": "spear", "weapon_grip": "two_handed"},
    )

    assert plan["damage_expression"] == "2d8 + 4"
    assert [part["damage_expression"] for part in plan["additional_damage"]] == [
        "1d6"
    ]


def test_second_wind_rolls_fighter_level_healing_and_clamps_at_maximum() -> None:
    actor = _actor("fighter")
    actor["sheet"]["progression"]["level"] = 2
    actor["sheet"]["progression"]["classes"] = [
        {"name": "Fighter", "level": 2, "subclass": "", "hit_die": 10}
    ]
    actor["sheet"]["combat"]["hp"] = {"value": 5, "max": 10, "temp": 0}

    result = resolve_second_wind_to_sheet(actor["sheet"], rng=_SequenceRng(8))

    assert result["roll"]["total"] == 8
    assert result["healing_amount"] == 10
    assert result["applied_amount"] == 5
    assert result["sheet"]["combat"]["hp"]["value"] == 10


def test_common_stabilize_action_pays_main_action_and_records_target() -> None:
    encounter = start_encounter([_actor("helper"), _actor("target")], rng=random.Random(1))
    current = encounter["combatants"][encounter["turn_index"]]["actor_id"]
    target = "target" if current == "helper" else "helper"

    stabilized = resolve_common_action(
        encounter,
        actor_id_value=current,
        action="stabilize",
        target_id=target,
        payload={"method": "medicine"},
    )

    actor = stabilized["combatants"][stabilized["turn_index"]]
    assert actor["turn_budget"]["main_action"] == 0
    assert actor["turn_flags"]["stabilizing"] == {
        "target_id": target,
        "payload": {"method": "medicine"},
    }


def test_queued_combatant_joins_at_next_round_without_moving_current_turn() -> None:
    encounter = start_encounter(
        [
            {**_actor("fast"), "initiative": 20, "tie_breaker": 0},
            {**_actor("slow"), "initiative": 10, "tie_breaker": 1},
        ]
    )
    queued = queue_combatant(
        encounter,
        {**_actor("ally"), "initiative": 15, "tie_breaker": 2},
    )

    assert current_combatant(queued)["actor_id"] == "fast"
    assert [item["actor_id"] for item in queued["combatants"]] == ["fast", "slow"]
    assert queued["reinforcements"][0]["join_round"] == 2

    slow = end_turn(queued, actor_id_value="fast")
    assert current_combatant(slow)["actor_id"] == "slow"
    joined = end_turn(slow, actor_id_value="slow")
    assert joined["round"] == 2
    assert [item["actor_id"] for item in joined["combatants"]] == [
        "fast",
        "ally",
        "slow",
    ]
    assert joined["reinforcements"] == []
    assert current_combatant(joined)["actor_id"] == "fast"


def test_queued_combatant_requires_explicit_tie_breaker_for_initiative_tie() -> None:
    encounter = start_encounter(
        [
            {**_actor("fast"), "initiative": 20, "tie_breaker": 0},
            {**_actor("slow"), "initiative": 10, "tie_breaker": 1},
        ]
    )

    with pytest.raises(NeedsRulingError, match="tie_breaker") as raised:
        queue_combatant(encounter, {**_actor("ally"), "initiative": 10})
    assert raised.value.ruling_kind == "agent_dm_adjudication"


def test_generic_ready_rejects_spell_payload_that_would_bypass_resources() -> None:
    encounter = start_encounter([_actor("a"), _actor("b")], rng=random.Random(1))
    current = encounter["combatants"][encounter["turn_index"]]["actor_id"]
    with pytest.raises(ValueError, match="readying a spell is not supported"):
        resolve_common_action(
            encounter,
            actor_id_value=current,
            action="ready",
            trigger="the foe moves",
            payload={"kind": "spell", "spell_id": "fire-bolt"},
        )


def test_readied_spell_trigger_can_be_declined_then_released_with_reaction() -> None:
    first = _actor("first")
    first["initiative"] = 20
    second = _actor("second")
    second["initiative"] = 10
    encounter = start_encounter([first, second])
    encounter = resolve_common_action(
        encounter,
        actor_id_value="first",
        action="cast",
        payment="main_action",
    )
    encounter = arm_readied_spell(
        encounter,
        actor_id_value="first",
        spell_id="magic-missile",
        trigger="the goblin moves",
        holding_effect_id="holding",
        release_concentration=False,
        release_duration={"period": "manual", "remaining": 0},
        release_effect_kind="readied_spell",
    )
    readied_id = encounter["readied"][0]["id"]
    with pytest.raises(ValueError, match="observed event"):
        trigger_readied_spell(encounter, readied_id=readied_id, event="")
    triggered = trigger_readied_spell(encounter, readied_id=readied_id, event="the goblin moves")
    choice_id = triggered["pending"][0]["id"]
    declined, _ = resolve_readied_spell_window(
        triggered,
        actor_id_value="first",
        choice_id=choice_id,
        release=False,
    )
    assert declined["readied"][0]["status"] == "armed"
    assert declined["combatants"][0]["turn_budget"]["reaction"] == 1

    triggered_again = trigger_readied_spell(
        declined, readied_id=readied_id, event="the goblin moves again"
    )
    released, _ = resolve_readied_spell_window(
        triggered_again,
        actor_id_value="first",
        choice_id=triggered_again["pending"][0]["id"],
        release=True,
    )
    assert released["readied"] == []
    assert released["combatants"][0]["turn_budget"]["reaction"] == 0


def test_readied_spell_expires_at_start_of_casters_next_turn() -> None:
    first = _actor("first")
    first["initiative"] = 20
    second = _actor("second")
    second["initiative"] = 10
    encounter = start_encounter([first, second])
    encounter = arm_readied_spell(
        encounter,
        actor_id_value="first",
        spell_id="magic-missile",
        trigger="the goblin moves",
        holding_effect_id="holding",
        release_concentration=False,
        release_duration={"period": "manual", "remaining": 0},
        release_effect_kind="readied_spell",
    )
    encounter = end_turn(encounter, actor_id_value="first")
    encounter = end_turn(encounter, actor_id_value="second")
    assert encounter["readied"] == []


def test_reactions_are_available_outside_the_actors_turn() -> None:
    encounter = start_encounter([_actor("a"), _actor("b")], rng=random.Random(1))
    current = encounter["combatants"][encounter["turn_index"]]["actor_id"]
    reactor = next(
        item["actor_id"] for item in encounter["combatants"] if item["actor_id"] != current
    )
    pending = add_choice_window(
        encounter,
        kind="reaction",
        actor_id_value=reactor,
        event="movement.leave_reach",
        candidates=[{"id": "skip"}],
    )
    assert available_reactions(pending, reactor)[0]["event"] == "movement.leave_reach"


def test_grid_movement_opens_opportunity_window_only_when_leaving_hostile_reach() -> None:
    mover = _actor("mover")
    mover.update(initiative=20, position={"x": 0, "y": 0}, disposition="friendly")
    threat = _actor("threat")
    threat.update(initiative=10, position={"x": 1, "y": 0}, disposition="hostile", reach_ft=5)
    encounter = start_encounter([mover, threat])

    moved = spend_movement(encounter, "mover", 15, destination={"x": 3, "y": 0})
    reaction = available_reactions(moved, "threat")
    assert reaction[0]["trigger"] == "opportunity_attack"
    assert reaction[0]["target_id"] == "mover"

    disengaged = resolve_common_action(encounter, actor_id_value="mover", action="disengage")
    moved_safely = spend_movement(disengaged, "mover", 15, destination={"x": 3, "y": 0})
    assert available_reactions(moved_safely, "threat") == []


def test_positioned_movement_rejects_declared_distance_that_disagrees_with_grid() -> None:
    mover = _actor("mover")
    mover.update(initiative=20, position={"x": 0, "y": 0})
    threat = _actor("threat")
    threat.update(initiative=10, position={"x": 4, "y": 0})
    encounter = start_encounter([mover, threat])
    with pytest.raises(ValueError, match="grid distance"):
        spend_movement(encounter, "mover", 5, destination={"x": 2, "y": 0})


def test_explicit_path_pays_difficult_terrain_cost() -> None:
    mover = _actor("mover")
    mover.update(initiative=20, position={"x": 0, "y": 0})
    other = _actor("other")
    other.update(initiative=10, position={"x": 4, "y": 0})
    encounter = start_encounter([mover, other])
    encounter["battle_map"] = compile_battle_map(
        {"scene_id": "terrain", "spatial": {}},
        {
            "width_cells": 6,
            "height_cells": 4,
            "difficult_cells": [{"x": 1, "y": 0}],
        },
    )

    with pytest.raises(NeedsRulingError) as missing_path:
        spend_movement(encounter, "mover", 10, destination={"x": 2, "y": 0})
    assert missing_path.value.missing == ("movement_path_for_difficult_terrain",)

    moved = spend_movement(
        encounter,
        "mover",
        10,
        destination={"x": 2, "y": 0},
        path=[{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 2, "y": 0}],
    )
    assert current_combatant(moved)["turn_budget"]["movement"] == 15


def test_voluntary_movement_cannot_end_in_another_living_creatures_space() -> None:
    mover = _actor("mover")
    mover.update(initiative=20, position={"x": 0, "y": 0})
    occupant = _actor("occupant")
    occupant.update(initiative=10, position={"x": 1, "y": 0})
    encounter = start_encounter([mover, occupant])

    with pytest.raises(ValueError, match="cannot willingly end"):
        spend_movement(encounter, "mover", 5, destination={"x": 1, "y": 0})


def test_space_sharing_trait_allows_an_occupied_destination() -> None:
    mover = _actor("swarm")
    mover.update(
        initiative=20,
        position={"x": 0, "y": 0},
        can_share_space=True,
    )
    occupant = _actor("occupant")
    occupant.update(initiative=10, position={"x": 1, "y": 0})
    encounter = start_encounter([mover, occupant])

    moved = spend_movement(encounter, "swarm", 5, destination={"x": 1, "y": 0})

    assert moved["combatants"][0]["position"] == {"x": 1, "y": 0}


def test_forced_movement_into_occupied_space_requires_effect_specific_ruling() -> None:
    mover = _actor("mover")
    mover.update(initiative=20, position={"x": 0, "y": 0})
    occupant = _actor("occupant")
    occupant.update(initiative=10, position={"x": 1, "y": 0})
    encounter = start_encounter([mover, occupant])

    with pytest.raises(NeedsRulingError) as error:
        spend_movement(
            encounter,
            "mover",
            5,
            destination={"x": 1, "y": 0},
            movement_mode="forced",
        )

    assert error.value.missing == ("occupied_destination_resolution",)


def test_hidden_mover_does_not_automatically_reveal_itself_with_a_reaction_window() -> None:
    mover = _actor("mover")
    mover.update(
        initiative=20,
        position={"x": 0, "y": 0},
        disposition="friendly",
        hidden=True,
    )
    threat = _actor("threat")
    threat.update(initiative=10, position={"x": 1, "y": 0}, disposition="hostile")
    encounter = start_encounter([mover, threat])
    moved = spend_movement(encounter, "mover", 15, destination={"x": 3, "y": 0})
    assert available_reactions(moved, "threat") == []


def test_recorded_visibility_can_open_reaction_window_for_invisible_mover() -> None:
    mover = _actor("mover")
    mover["sheet"]["conditions"] = ["invisible"]
    mover["derived"] = derive_character_sheet(mover["sheet"])
    mover.update(
        initiative=20,
        position={"x": 0, "y": 0},
        disposition="friendly",
        visible_to_actor_ids=["threat"],
    )
    threat = _actor("threat")
    threat.update(initiative=10, position={"x": 1, "y": 0}, disposition="hostile")
    encounter = start_encounter([mover, threat])
    moved = spend_movement(encounter, "mover", 15, destination={"x": 3, "y": 0})
    assert available_reactions(moved, "threat")[0]["target_id"] == "mover"


def test_activity_activation_pays_only_the_matching_action_economy() -> None:
    first = _actor("first")
    first["initiative"] = 20
    second = _actor("second")
    second["initiative"] = 10
    encounter = start_encounter([first, second])
    paid = pay_activity_activation(
        encounter, actor_id_value="first", activation_type="bonus_action"
    )
    assert paid["combatants"][0]["turn_budget"]["bonus_action"] == 0

    reacted = pay_activity_activation(paid, actor_id_value="second", activation_type="reaction")
    assert reacted["combatants"][1]["turn_budget"]["reaction"] == 0


def test_incapacitated_actor_cannot_pay_reaction_activity() -> None:
    first = _actor("first")
    first.update(initiative=20)
    second = _actor("second")
    second.update(initiative=10)
    second["sheet"]["conditions"] = ["incapacitated"]
    second["derived"] = derive_character_sheet(second["sheet"])
    encounter = start_encounter([first, second])
    with pytest.raises(ValueError, match="cannot activate content"):
        pay_activity_activation(encounter, actor_id_value="second", activation_type="reaction")


def test_hit_point_loss_bypasses_temporary_hp_and_damage_traits() -> None:
    actor = _actor("target", hp=8)
    actor["sheet"]["combat"]["hp"]["temp"] = 7
    actor["sheet"]["traits"]["resistances"] = ["piercing"]

    result = apply_hit_point_loss_to_sheet(actor["sheet"], amount=5)

    assert result["after_hp"] == 3
    assert result["bypassed_temp_hp"] == 7
    assert result["sheet"]["combat"]["hp"]["temp"] == 7


def test_regenerating_zero_hp_creature_is_buffered_until_its_turn() -> None:
    actor = _actor("troll", hp=12)

    dropped = apply_hit_point_loss_to_sheet(
        actor["sheet"],
        amount=12,
        death_saves=False,
        zero_hp_recovery=True,
    )

    assert dropped["after_hp"] == 0
    assert "unconscious" in dropped["sheet"]["conditions"]
    assert "dead" not in dropped["sheet"]["conditions"]

    regenerated = settle_start_turn_regeneration(
        dropped["sheet"],
        amount=10,
        suppressed=False,
    )

    assert regenerated["after_hp"] == 10
    assert regenerated["died"] is False
    assert "unconscious" not in regenerated["sheet"]["conditions"]
    assert "prone" in regenerated["sheet"]["conditions"]


def test_suppressed_regeneration_kills_a_creature_that_starts_at_zero_hp() -> None:
    actor = _actor("troll", hp=12)
    dropped = apply_hit_point_loss_to_sheet(
        actor["sheet"],
        amount=12,
        death_saves=False,
        zero_hp_recovery=True,
    )

    result = settle_start_turn_regeneration(
        dropped["sheet"],
        amount=10,
        suppressed=True,
    )

    assert result["after_hp"] == 0
    assert result["died"] is True
    assert "dead" in result["sheet"]["conditions"]
    assert "unconscious" not in result["sheet"]["conditions"]


def test_attachment_blocks_attacks_and_can_be_removed_by_the_target_action() -> None:
    target = _actor("target")
    target.update(initiative=20, position={"x": 0, "y": 0})
    stirge = _actor("stirge")
    stirge.update(initiative=10, position={"x": 0, "y": 0})
    encounter = start_encounter([target, stirge])
    encounter["ongoing_effects"] = [
        {
            "id": "attachment-1",
            "kind": "attachment",
            "source_actor_id": "stirge",
            "target_id": "target",
            "self_detach_movement_ft": 5,
            "active": True,
        }
    ]

    detached = detach_attachment(
        encounter,
        actor_id_value="target",
        effect_id="attachment-1",
    )

    target_combatant = detached["combatants"][0]
    assert target_combatant["turn_budget"]["main_action"] == 0
    assert detached["ongoing_effects"][0]["active"] is False
    assert detached["ongoing_effects"][0]["ended_reason"] == "detached_by_action"

    stirge_turn = end_turn(encounter, actor_id_value="target")
    assert "attack" not in available_actions(stirge_turn, "stirge")
    assert "detach_attachment" in available_actions(stirge_turn, "stirge")


def test_common_use_object_action_preserves_the_reviewed_source_payload() -> None:
    encounter = start_encounter([_actor("hero")])
    payload = {
        "source_finisher_id": "source-zero-hp-finisher:troll",
        "stage": "douse",
        "source_excerpt": "douse the troll with lamp oil",
    }

    used = resolve_common_action(
        encounter,
        actor_id_value="hero",
        action="use_object",
        target_id="troll",
        payload=payload,
    )

    assert used["log"][-1] == {
        "type": "common_action",
        "action": "use_object",
        "actor_id": "hero",
        "target_id": "troll",
        "payload": payload,
        "round": 1,
        "turn_index": 0,
    }


def test_lookalike_critical_text_does_not_gain_an_automatic_attack_contract() -> None:
    effect = (
        "If the target is a creature and Durnan rolls a 20 on the d20 for the "
        "attack roll, the target takes an extra 14 slashing damage, and Durnan "
        "rolls another d20. On a roll of 20, he lops off one of the target's "
        "limbs, or some other part of its body if it is limbless."
    )
    attacker = _actor("durnan")
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "lookalike-sword",
            "attack_type": "melee",
            "reach_ft": 5,
            "properties": [],
            "attack_bonus": 8,
            "damage_expression": "2d6 + 4",
            "damage_type": "slashing",
            "additional_damage": [],
            "on_hit_effect": effect,
        }
    ]
    target = _actor("target")

    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "lookalike-sword"},
    )

    assert plan["critical_followup"] is None
    assert plan["on_hit_effect"] == effect


def test_grimvault_fixed_critical_followup_is_conditional_and_simultaneous() -> None:
    effect = (
        "If the target is an object, the hit instead deals 16 slashing damage. "
        "If the target is a creature and Durnan rolls a 20 on the d20 for the "
        "attack roll, the target takes an extra 14 slashing damage, and Durnan "
        "rolls another d20. On a roll of 20, he lops off one of the target's "
        "limbs, or some other part of its body if it is limbless."
    )
    parsed = structured_critical_followup(effect)

    assert parsed == {
        "kind": "critical_followup",
        "trigger_natural": 20,
        "extra_damage": 14,
        "damage_type": "slashing",
        "followup_expression": "1d20",
        "anatomical_loss_natural": 20,
        "source_excerpt": effect,
    }

    attacker = _actor("durnan")
    target = _actor("troll")
    target["sheet"]["combat"]["hp"] = {"value": 84, "max": 84, "temp": 0}
    target["sheet"]["traits"]["resistances"] = ["slashing"]
    plan = {
        "damage_expression": "2d6 + 4",
        "damage_type": "slashing",
        "additional_damage": [],
        "critical_followup": parsed,
        "target_uses_death_saves": False,
        "ruleset": "2014",
    }
    _, updated_target, result = resolve_attack_damage(
        attacker,
        target,
        plan=plan,
        attack={
            "natural": 20,
            "total": 28,
            "armor_class": 15,
            "hit": True,
            "critical": True,
            "fumble": False,
        },
        rng=_SequenceRng(3, 4, 3, 4, 20),
    )

    # The doubled 2d6 (14), +4, and fixed +14 rider are one slashing
    # instance: floor((14 + 4 + 14) / 2) = 16 after resistance.
    assert updated_target["sheet"]["combat"]["hp"]["value"] == 68
    assert result["damage"]["input_amount"] == 32
    assert result["damage"]["applied_amount"] == 16
    assert result["critical_followup"]["triggered"] is True
    assert result["critical_followup"]["followup_roll"]["total"] == 20
    assert result["critical_followup"]["anatomical_loss_triggered"] is True
    assert result["critical_followup"]["requires_dm_ruling"] is True
    assert result["critical_followup"]["ruling_requirement"] == {
        "default_resolver": "agent",
        "ruling_kind": "source_or_scene_fact",
        "reason": (
            "Determine from the target and scene facts whether the triggered "
            "anatomical loss can apply."
        ),
    }


def test_grimvault_followup_does_not_trigger_on_an_ordinary_hit() -> None:
    effect = (
        "If the target is a creature and Durnan rolls a 20 on the d20 for the "
        "attack roll, the target takes an extra 14 slashing damage, and Durnan "
        "rolls another d20. On a roll of 20, he lops off one of the target's "
        "limbs, or some other part of its body if it is limbless."
    )
    plan = {
        "damage_expression": "2d6 + 4",
        "damage_type": "slashing",
        "additional_damage": [],
        "critical_followup": structured_critical_followup(effect),
        "target_uses_death_saves": False,
        "ruleset": "2014",
    }

    _, _, result = resolve_attack_damage(
        _actor("durnan"),
        _actor("troll"),
        plan=plan,
        attack={
            "natural": 19,
            "total": 27,
            "armor_class": 15,
            "hit": True,
            "critical": False,
            "fumble": False,
        },
        rng=_SequenceRng(3, 4),
    )

    assert result["critical_followup"]["triggered"] is False
    assert result["critical_followup"]["followup_roll"] is None
    assert result["critical_followup"]["requires_dm_ruling"] is False
    assert result["critical_followup"]["ruling_requirement"] is None
    assert "on_hit_ruling" not in result
    damage_amount_after_reduction,
