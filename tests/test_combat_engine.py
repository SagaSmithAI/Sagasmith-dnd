import random

import pytest

from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.combat_engine import (
    NeedsRulingError,
    add_choice_window,
    apply_attack_ac_bonus,
    apply_damage_parts_to_sheet,
    apply_damage_to_sheet,
    apply_healing_to_sheet,
    arm_readied_spell,
    available_actions,
    available_attack_defenses,
    available_reactions,
    current_combatant,
    end_turn,
    pay_activity_activation,
    pay_attack_action,
    preflight_attack,
    queue_combatant,
    resolve_actor_check,
    resolve_attack_action,
    resolve_attack_damage,
    resolve_choice_window,
    resolve_common_action,
    resolve_death_save_to_sheet,
    resolve_readied_spell_window,
    roll_attack_action,
    spend_movement,
    stabilize_sheet,
    start_encounter,
    trigger_readied_spell,
)
from sagasmith_dnd.engine import resolve_check, roll_d20


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


def test_bandit_captain_multiattack_preserves_recorded_weapon_composition() -> None:
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
            "name": "Multiattack",
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
    assert captain["derived"]["attacks_per_action"] == 3
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


def test_initiative_ties_require_explicit_tie_breakers() -> None:
    with pytest.raises(NeedsRulingError, match="tie_breaker"):
        start_encounter(
            [{**_actor("a"), "initiative": 10}, {**_actor("b"), "initiative": 10}]
        )


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
    save = resolve_death_save_to_sheet(exhausted["sheet"], bonus=-2, rng=random.Random(7))
    assert save["natural"] == 11
    assert save["total"] == 9
    assert save["failures"] == 1


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


def test_death_save_persists_nat20_recovery() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["combat"]["hp"]["value"] = 0
    actor["sheet"]["conditions"] = ["unconscious"]
    result = resolve_death_save_to_sheet(actor["sheet"], rng=random.Random(5))
    assert result["outcome"] == "revived"
    assert result["sheet"]["combat"]["hp"]["value"] == 1
    assert "unconscious" not in result["sheet"]["conditions"]


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

    with pytest.raises(NeedsRulingError, match="tie_breaker"):
        queue_combatant(encounter, {**_actor("ally"), "initiative": 10})


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
