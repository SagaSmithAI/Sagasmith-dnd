"""Built-in D&D core rule packs that wrap the currently verified engine behavior."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sagasmith_core.integrity import json_sha256

from sagasmith_dnd.editions import SUPPORTED_DND_EDITIONS, normalize_dnd_edition

CORE_RULE_PACK_VERSION = "1.48.0"


@dataclass(frozen=True)
class CoreBoundary:
    id: str
    editions: tuple[str, ...]
    implementation: str
    test_refs: tuple[str, ...]
    citation: str


@dataclass(frozen=True)
class BuiltinCoreRulePack:
    id: str
    version: str
    edition: str
    fingerprint: str
    boundaries: tuple[CoreBoundary, ...]

    def receipt(self, boundary_id: str, event: str) -> dict[str, Any]:
        boundary = next((item for item in self.boundaries if item.id == boundary_id), None)
        if boundary is None:
            raise KeyError(boundary_id)
        return {
            "mechanic_id": boundary.id,
            "event": event,
            "operations": [{"op": "builtin.core_provider"}],
            "citations": [{"source": boundary.citation}],
            "ruleset_fingerprint": self.fingerprint,
        }


BOUNDARIES = (
    CoreBoundary(
        "dnd5e.core.chase.sequence",
        ("2014",),
        "chase_engine.start_chase|current_chase_participant|advance_chase_turn",
        ("tests/test_chase_engine.py",),
        "rulebook:dmg2014/chapter-8/chases#beginning-a-chase",
    ),
    CoreBoundary(
        "dnd5e.core.chase.dashing",
        ("2014",),
        "chase_engine.advance_chase_turn",
        ("tests/test_chase_engine.py::test_extra_dash_uses_constitution_check_and_exhaustion",),
        "rulebook:dmg2014/chapter-8/chases#dashing",
    ),
    CoreBoundary(
        "dnd5e.core.chase.urban_complications",
        ("2014",),
        "chase_engine.advance_chase_turn",
        ("tests/test_chase_engine.py::test_urban_complication_affects_next_participant",),
        "rulebook:dmg2014/chapter-8/chases#urban-chase-complications",
    ),
    CoreBoundary(
        "dnd5e.core.chase.ending",
        ("2014",),
        "chase_engine.advance_chase_turn|end_chase",
        ("tests/test_chase_engine.py::test_module_close_transition_ends_chase",),
        "rulebook:dmg2014/chapter-8/chases#ending-a-chase",
    ),
    CoreBoundary(
        "dnd5e.core.activity.resource_accounting",
        ("2014", "2024"),
        "activities.consume_activity",
        ("tests/test_activities.py",),
        "bundled:srd/limited-use-features",
    ),
    CoreBoundary(
        "dnd5e.core.activity.turn_undead",
        ("2014",),
        "combat_engine.resolve_turn_undead_to_sheets|spend_movement|available_actions",
        ("tests/test_combat_engine.py::test_turn_undead_applies_and_enforces_turned",),
        "bundled:srd2014/02_Classes/Cleric.md#channel-divinity-turn-undead",
    ),
    CoreBoundary(
        "dnd5e.core.activity.random_save_effects",
        ("2014",),
        (
            "statblocks.gazer_eye_ray_spec|"
            "combat_engine.resolve_random_save_effects|force_move_directly_away"
        ),
        (
            "tests/test_statblocks.py::"
            "test_gazer_eye_rays_are_structured_from_the_exact_source_action",
            "tests/test_combat_engine.py::"
            "test_gazer_eye_rays_reroll_duplicates_and_resolve_each_save",
            "SagaSmith-dnd-mcp/tests/test_gazer_eye_rays_mcp.py",
        ),
        "module-source:Waterdeep-Dragon-Heist/page-204/gazer-eye-rays",
    ),
    CoreBoundary(
        "dnd5e.core.activity.source_save_effect",
        ("2014",),
        (
            "statblocks.source_save_effect_spec|"
            "combat_engine.resolve_source_save_effect|pay_multiattack_activity"
        ),
        (
            "tests/test_statblocks.py::"
            "test_intellect_devourer_actions_are_structured_from_exact_source",
            "tests/test_combat_engine.py::"
            "test_devour_intellect_resolves_damage_score_reduction_and_stun",
            "SagaSmith-dnd-mcp/tests/test_intellect_devourer_mcp.py",
        ),
        "rulebook:mm2014/page-191/intellect-devourer",
    ),
    CoreBoundary(
        "dnd5e.core.activity.source_contest_effect",
        ("2014",),
        (
            "statblocks.source_contest_effect_spec|"
            "combat_engine.resolve_source_contest_effect|"
            "core.state.ActorKnowledgeTransfer"
        ),
        (
            "tests/test_statblocks.py::"
            "test_intellect_devourer_actions_are_structured_from_exact_source",
            "tests/test_combat_engine.py::"
            "test_body_thief_wins_contest_and_adopts_body_with_source_mental_scores",
            "SagaSmith-dnd-mcp/tests/test_intellect_devourer_mcp.py::"
            "test_public_body_thief_takes_host_and_atomically_copies_knowledge",
        ),
        "rulebook:mm2014/page-191/intellect-devourer",
    ),
    CoreBoundary(
        "dnd5e.core.activity.action_surge",
        ("2014",),
        "combat_engine.settle_core_activity_effect",
        ("tests/test_combat_engine.py",),
        "bundled:srd2014/02_Classes/Fighter.md#action-surge",
    ),
    CoreBoundary(
        "dnd5e.core.activity.second_wind",
        ("2014",),
        "combat_engine.resolve_second_wind_to_sheet",
        ("tests/test_combat_engine.py",),
        "bundled:srd2014/02_Classes/Fighter.md#second-wind",
    ),
    CoreBoundary(
        "dnd5e.core.activity.cunning_action",
        ("2014",),
        "combat_engine.settle_core_activity_effect",
        ("tests/test_combat_engine.py",),
        "bundled:srd2014/02_Classes/Rogue.md#cunning-action",
    ),
    CoreBoundary(
        "dnd5e.core.activity.battle_cry",
        ("2014",),
        "combat_engine.settle_core_activity_effect",
        (
            "tests/test_combat_engine.py::"
            "test_battle_cry_grants_temporary_attack_advantage_and_bonus_attack",
            "tests/test_statblocks.py::"
            "test_orc_war_chief_standard_traits_and_multiattack_are_structured",
        ),
        "source:monster-manual-2014:p246",
    ),
    CoreBoundary(
        "dnd5e.core.activity.preserve_life",
        ("2014",),
        "combat_engine.resolve_preserve_life_to_sheets",
        ("tests/test_combat_engine.py",),
        "bundled:srd2014/02_Classes/Cleric.md#preserve-life",
    ),
    CoreBoundary(
        "dnd5e.core.ability_generation",
        ("2014", "2024"),
        "ability_generation.py",
        ("tests/test_ability_generation.py",),
        "bundled:srd/character-creation",
    ),
    CoreBoundary(
        "dnd5e.core.progression.hp_hit_dice",
        ("2014",),
        "progression.advance_single_class_level",
        ("tests/test_progression.py",),
        "bundled:srd2014/03_Characterization/Beyond_1st_Level.md",
    ),
    CoreBoundary(
        "dnd5e.core.progression.spellcasting",
        ("2014",),
        "progression.advance_single_class_level",
        ("tests/test_progression.py",),
        "bundled:srd2014/02_Classes",
    ),
    CoreBoundary(
        "dnd5e.core.progression.experience",
        ("2014", "2024"),
        "progression.award_experience",
        ("tests/test_progression.py",),
        "bundled:srd/character-creation",
    ),
    CoreBoundary(
        "dnd5e.core.armor_class.unarmored",
        ("2014", "2024"),
        "character_schema._derive_armor_class",
        ("tests/test_character_schema.py",),
        "bundled:srd/armor-class",
    ),
    CoreBoundary(
        "dnd5e.core.check.armor_stealth_disadvantage",
        ("2014", "2024"),
        "combat_engine.resolve_actor_check",
        (
            "tests/test_combat_engine.py",
            "tests/test_character_schema.py",
            "tests/test_statblocks.py",
        ),
        "bundled:srd2014/04_Equipment/Armor.md",
    ),
    CoreBoundary(
        "dnd5e.core.check.jack_of_all_trades",
        ("2014",),
        "combat_engine.resolve_actor_check|start_encounter",
        (
            "tests/test_combat_engine.py::test_2014_jack_of_all_trades_applies_only_to_unproficient_ability_checks",
            "tests/test_combat_engine.py::test_2014_jack_of_all_trades_applies_to_initiative",
        ),
        "bundled:srd2014/02_Classes/Bard.md",
    ),
    CoreBoundary(
        "dnd5e.core.check.group",
        ("2014",),
        "combat_engine.resolve_actor_group_check",
        (
            "tests/test_combat_engine.py::"
            "test_2014_group_check_succeeds_when_at_least_half_succeed",
        ),
        "bundled:srd2014/06_Gameplay/Using_Ability_Scores.md#group-checks",
    ),
    CoreBoundary(
        "dnd5e.core.weapon.reach",
        ("2014", "2024"),
        "character_schema._weapon_attacks",
        ("tests/test_character_schema.py", "tests/test_combat_engine.py"),
        "bundled:srd/weapon-properties",
    ),
    CoreBoundary(
        "dnd5e.core.initiative.tie",
        ("2014", "2024"),
        "combat_engine.start_encounter",
        ("tests/test_combat_engine.py::test_initiative_ties_require_explicit_tie_breakers",),
        "bundled:srd/initiative",
    ),
    CoreBoundary(
        "dnd5e.core.action.edition_list",
        ("2014", "2024"),
        "combat_engine.available_actions",
        ("tests/test_combat_engine.py",),
        "bundled:srd/actions",
    ),
    CoreBoundary(
        "dnd5e.core.action.multiattack_choice",
        ("2014", "2024"),
        "combat_engine.pay_attack_action",
        ("tests/test_combat_engine.py", "tests/test_statblocks.py"),
        "bundled:srd2014/10_Monsters/Monsters.md",
    ),
    CoreBoundary(
        "dnd5e.core.attack.cover",
        ("2014", "2024"),
        "combat_engine.preflight_attack",
        ("tests/test_combat_engine.py::test_half_cover_uses_the_rules_ac_bonus",),
        "bundled:srd/cover",
    ),
    CoreBoundary(
        "dnd5e.core.attack.ammunition",
        ("2014", "2024"),
        "combat_engine.preflight_attack|character_schema.consume_weapon_ammunition",
        ("tests/test_combat_engine.py", "tests/test_character_schema.py"),
        "bundled:srd/weapon-properties",
    ),
    CoreBoundary(
        "dnd5e.core.magic_ammunition.slaying",
        ("2014",),
        "combat_engine.preflight_attack",
        ("tests/test_combat_engine.py::test_slaying_ammunition_opens_source_save_damage",),
        "bundled:srd2014/09_Magic_Items/Magic_Items_Each/Arrow_of_Slaying.md",
    ),
    CoreBoundary(
        "dnd5e.core.attack.range",
        ("2014", "2024"),
        "combat_engine.preflight_attack|combat_engine._attack_range",
        ("tests/test_combat_engine.py",),
        "bundled:srd/ranged-attacks",
    ),
    CoreBoundary(
        "dnd5e.core.attack.ranged_close_combat",
        ("2014", "2024"),
        "combat_engine.preflight_attack",
        ("tests/test_combat_engine.py::test_ranged_attack_has_close_combat_disadvantage",),
        "bundled:srd/ranged-attacks-in-close-combat",
    ),
    CoreBoundary(
        "dnd5e.core.attack.unarmed_strike",
        ("2014", "2024"),
        "combat_engine.preflight_attack",
        ("tests/test_combat_engine.py",),
        "bundled:srd/melee-attacks",
    ),
    CoreBoundary(
        "dnd5e.core.attack.condition_source",
        ("2014", "2024"),
        "combat_engine.preflight_attack",
        ("tests/test_combat_engine.py",),
        "bundled:srd/conditions",
    ),
    CoreBoundary(
        "dnd5e.core.attack.help",
        ("2014", "2024"),
        "combat_engine.preflight_attack",
        ("tests/test_combat_engine.py::test_help_grants_and_then_consumes_attack_advantage",),
        "bundled:srd/help",
    ),
    CoreBoundary(
        "dnd5e.core.attack.pack_tactics",
        ("2014",),
        "combat_engine.preflight_attack",
        (
            "tests/test_combat_engine.py::"
            "test_pack_tactics_uses_a_conscious_adjacent_ally",
            "tests/test_statblocks.py::"
            "test_kobold_attack_traits_are_structured",
        ),
        "source:monster-manual-2014:p195",
    ),
    CoreBoundary(
        "dnd5e.core.attack.sunlight_sensitivity",
        ("2014",),
        "combat_engine.preflight_attack",
        (
            "tests/test_combat_engine.py::"
            "test_sunlight_sensitivity_requires_and_uses_the_environment_fact",
            "tests/test_statblocks.py::"
            "test_kobold_attack_traits_are_structured",
        ),
        "source:monster-manual-2014:p195",
    ),
    CoreBoundary(
        "dnd5e.core.attack.battle_cry",
        ("2014",),
        "combat_engine.preflight_attack",
        (
            "tests/test_combat_engine.py::"
            "test_battle_cry_grants_temporary_attack_advantage_and_bonus_attack",
        ),
        "source:monster-manual-2014:p246",
    ),
    CoreBoundary(
        "dnd5e.core.attack.sneak_attack",
        ("2014",),
        "combat_engine._sneak_attack_plan|combat_engine.resolve_attack_action",
        (
            "tests/test_combat_engine.py::"
            "test_sneak_attack_requires_card_feature_and_records_critical_bonus_damage",
            "tests/test_combat_engine.py::"
            "test_statblock_sneak_attack_uses_recorded_formula_without_rogue_levels",
            "tests/test_statblocks.py::"
            "test_spy_standard_traits_are_structured_from_their_exact_text",
        ),
        "bundled:srd2014/02_Classes/Rogue.md#sneak-attack|"
        "source:monster-manual-2014:p349",
    ),
    CoreBoundary(
        "dnd5e.core.monster.corrosive_form",
        ("2014",),
        "statblocks._corrosive_form_source_trait|combat_engine.resolve_corrosive_form_melee_hit",
        (
            "tests/test_statblocks.py::test_black_pudding_standard_traits_are_structured",
            "tests/test_combat_engine.py::test_corrosive_form_damages_attacker_and_corrodes_mundane_weapon",
        ),
        "source:monster-manual-2014:p241",
    ),
    CoreBoundary(
        "dnd5e.core.monster.heated_body",
        ("2014",),
        "statblocks._heated_body_source_trait|combat_engine.resolve_heated_body_melee_hit",
        (
            "tests/test_statblocks.py::test_salamander_standard_traits_are_structured",
            "tests/test_combat_engine.py::test_heated_body_damages_only_a_melee_attacker_within_five_feet",
        ),
        "source:monster-manual-2014:p267",
    ),
    CoreBoundary(
        "dnd5e.core.monster.heated_weapons",
        ("2014",),
        "statblocks._heated_weapons_source_trait|statblocks._parse_weapon",
        (
            "tests/test_statblocks.py::test_salamander_standard_traits_are_structured",
            "tests/test_combat_engine.py::test_versatile_weapon_retains_damage_printed_after_alternate_formula",
        ),
        "source:monster-manual-2014:p267",
    ),
    CoreBoundary(
        "dnd5e.core.monster.armor_corrosion",
        ("2014",),
        "statblocks._armor_corrosion_on_hit|combat_engine.resolve_standard_weapon_on_hit",
        (
            "tests/test_statblocks.py::test_black_pudding_standard_traits_are_structured",
            "tests/test_combat_engine.py::test_pseudopod_corrosion_reduces_and_destroys_worn_armor",
        ),
        "source:monster-manual-2014:p241",
    ),
    CoreBoundary(
        "dnd5e.core.monster.death_burst",
        ("2014",),
        "statblocks._death_burst_source_trait|combat_engine.standard_death_trigger_for_sheet",
        (
            "tests/test_statblocks.py::test_magmin_standard_mechanics_are_structured",
            "tests/test_combat_engine.py::test_magmin_death_burst_surfaces_only_on_death_transition",
        ),
        "source:monster-manual-2014:p212",
    ),
    CoreBoundary(
        "dnd5e.core.monster.ignition_ongoing_damage",
        ("2014",),
        "statblocks._ignition_ongoing_damage_on_hit|combat_engine.resolve_standard_weapon_on_hit",
        (
            "tests/test_statblocks.py::test_magmin_standard_mechanics_are_structured",
            "tests/test_combat_engine.py::test_magmin_touch_compiles_standard_ongoing_damage",
        ),
        "source:monster-manual-2014:p212",
    ),
    CoreBoundary(
        "dnd5e.core.monster.ignited_illumination",
        ("2014",),
        "statblocks._ignited_illumination_source_trait|combat_engine.settle_core_activity_effect",
        (
            "tests/test_statblocks.py::test_magmin_standard_mechanics_are_structured",
            "tests/test_combat_engine.py::test_magmin_illumination_toggles_with_a_paid_bonus_action",
        ),
        "source:monster-manual-2014:p212",
    ),
    CoreBoundary(
        "dnd5e.core.monster.split",
        ("2014",),
        "statblocks._split_source_trait|combat_engine.split_reaction_eligibility|combat_engine.execute_split_reaction",
        (
            "tests/test_statblocks.py::test_black_pudding_standard_traits_are_structured",
            "tests/test_combat_engine.py::test_black_pudding_split_uses_raw_immune_damage_trigger",
        ),
        "source:monster-manual-2014:p241",
    ),
    CoreBoundary(
        "dnd5e.core.monster.ooze_movement",
        ("2014",),
        "statblocks._amorphous_source_trait|statblocks._spider_climb_source_trait",
        ("tests/test_statblocks.py::test_black_pudding_standard_traits_are_structured",),
        "source:monster-manual-2014:p241",
    ),
    CoreBoundary(
        "dnd5e.core.attack.weapon_grip",
        ("2014", "2024"),
        "combat_engine.preflight_attack",
        (
            "tests/test_combat_engine.py::"
            "test_versatile_weapon_grip_uses_exact_alternate_damage_once",
        ),
        "bundled:srd/equipment/weapon-properties",
    ),
    CoreBoundary(
        "dnd5e.core.attack.assassinate",
        ("2014",),
        "combat_engine.preflight_attack|roll_attack_action",
        (
            "tests/test_combat_engine.py::"
            "test_assassinate_uses_authoritative_turn_and_surprise_state",
            "tests/test_statblocks.py::"
            "test_assassinate_is_structured_from_exact_text",
        ),
        "source:monster-manual-2014:assassin",
    ),
    CoreBoundary(
        "dnd5e.core.attack.weapon_hit_save_damage",
        ("2014", "2024"),
        "statblocks._saving_throw_damage_on_hit|combat_engine.resolve_attack_damage",
        (
            "tests/test_combat_engine.py::"
            "test_weapon_hit_save_damage_is_settled_inside_one_attack",
            "tests/test_statblocks.py::"
            "test_weapon_hit_save_damage_is_structured_from_exact_text",
        ),
        "bundled:srd/combat/saving-throws-and-damage",
    ),
    CoreBoundary(
        "dnd5e.core.monster.aggressive",
        ("2014",),
        "combat_engine.settle_core_activity_effect",
        (
            "tests/test_combat_engine.py::"
            "test_aggressive_grants_only_toward_visible_hostile_movement",
        ),
        "source:monster-manual-2014:p246",
    ),
    CoreBoundary(
        "dnd5e.core.check.keen_perception",
        ("2014",),
        "combat_engine.resolve_actor_check",
        (
            "tests/test_combat_engine.py::"
            "test_keen_perception_requires_and_uses_sensory_facts",
            "tests/test_statblocks.py::"
            "test_keen_perception_trait_is_structured",
        ),
        "source:monster-manual-2014:p349",
    ),
    CoreBoundary(
        "dnd5e.core.attack.source_targeting",
        ("2014", "2024"),
        "combat_engine.preflight_attack",
        (
            "tests/test_combat_engine.py::"
            "test_source_weapon_targeting_requires_eligible_size_and_effective_advantage",
        ),
        "source:reviewed-statblock-action-targeting",
    ),
    CoreBoundary(
        "dnd5e.core.attack.hidden_reveal",
        ("2014", "2024"),
        "combat_engine.resolve_attack_action",
        ("tests/test_combat_engine.py",),
        "bundled:srd/hiding",
    ),
    CoreBoundary(
        "dnd5e.core.damage.zero_hp",
        ("2014", "2024"),
        "combat_engine._apply_adjusted_damage",
        ("tests/test_combat_engine.py",),
        "bundled:srd/damage-and-healing",
    ),
    CoreBoundary(
        "dnd5e.core.damage.knockout",
        ("2014", "2024"),
        "combat_engine._apply_adjusted_damage",
        ("tests/test_combat_engine.py",),
        "bundled:srd/knocking-a-creature-out",
    ),
    CoreBoundary(
        "dnd5e.core.damage.stable_recovery",
        ("2014", "2024"),
        "lifecycle.recover_stable_creature",
        ("tests/test_lifecycle.py::test_stable_creature_recovers_one_hp_after_rolled_hours",),
        "bundled:srd/damage-and-healing",
    ),
    CoreBoundary(
        "dnd5e.core.item.healing_potion",
        ("2014", "2024"),
        "consumables.healing_potion_formula",
        ("tests/test_consumables.py::test_standard_healing_potion_is_edition_bound",),
        "bundled:srd/potion-of-healing",
    ),
    CoreBoundary(
        "dnd5e.core.movement.prone_crawl_stand",
        ("2014", "2024"),
        "combat_engine.spend_movement|stand_up",
        ("tests/test_combat_engine.py",),
        "bundled:srd/movement",
    ),
    CoreBoundary(
        "dnd5e.core.movement.grapple_source",
        ("2014", "2024"),
        "combat_engine.spend_movement",
        ("tests/test_combat_engine.py",),
        "bundled:srd/grappled",
    ),
    CoreBoundary(
        "dnd5e.core.movement.occupied_destination",
        ("2014", "2024"),
        "combat_engine.spend_movement",
        ("tests/test_combat_engine.py",),
        "bundled:srd/movement-around-other-creatures",
    ),
    CoreBoundary(
        "dnd5e.core.movement.difficult_terrain",
        ("2014", "2024"),
        "combat_engine.spend_movement",
        ("tests/test_combat_engine.py::test_explicit_path_pays_difficult_terrain_cost",),
        "bundled:srd/difficult-terrain",
    ),
    CoreBoundary(
        "dnd5e.core.reaction.opportunity_path",
        ("2014", "2024"),
        "combat_engine.spend_movement",
        ("tests/test_combat_engine.py",),
        "bundled:srd/opportunity-attacks",
    ),
    CoreBoundary(
        "dnd5e.core.reaction.post_hit_defense",
        ("2014", "2024"),
        "combat_engine.available_attack_defenses|apply_attack_ac_bonus",
        ("tests/test_combat_engine.py::test_structured_parry_opens_after_hit_and_before_damage",),
        "bundled:srd/reactions",
    ),
    CoreBoundary(
        "dnd5e.core.spell.shield",
        ("2014", "2024"),
        (
            "spells.is_core_shield_spell|available_shield_cast_options|"
            "consume_shield_reaction"
        ),
        (
            "tests/test_spells.py::test_shield_reaction_pays_slot_and_expires_at_turn_start",
            "tests/test_spells.py::"
            "test_shield_name_without_source_bound_mechanic_is_not_executable",
        ),
        "bundled:srd/shield",
    ),
    CoreBoundary(
        "dnd5e.core.spell.shield_attack_ac",
        ("2014", "2024"),
        "spells.available_shield_attack_defenses|consume_shield_reaction",
        ("tests/test_spells.py::test_shield_reaction_pays_slot_and_expires_at_turn_start",),
        "bundled:srd/shield",
    ),
    CoreBoundary(
        "dnd5e.core.spell.shield_magic_missile",
        ("2014", "2024"),
        "spells.available_shield_magic_missile_defenses|consume_shield_reaction",
        (
            "tests/test_spells.py::test_magic_missile_allocation_and_shield_trigger_are_source_bound",
        ),
        "bundled:srd/shield",
    ),
    CoreBoundary(
        "dnd5e.core.spell.shield_item_ac",
        ("2014", "2024"),
        "spells.consume_magic_item_spell_cast",
        ("tests/test_spells.py::test_magic_item_charges_cast_source_bound_defenses",),
        "bundled:srd/shield",
    ),
    CoreBoundary(
        "dnd5e.core.spell.mage_armor",
        ("2014", "2024"),
        "spells.consume_magic_item_spell_cast|character_schema._derive_armor_class",
        ("tests/test_spells.py::test_magic_item_charges_cast_source_bound_defenses",),
        "bundled:srd/mage-armor",
    ),
    CoreBoundary(
        "dnd5e.core.spell.blade_ward",
        ("2014",),
        (
            "spells.is_core_blade_ward_spell|spells.consume_spell_cast|"
            "combat_engine._adjust_damage_amount"
        ),
        (
            "tests/test_standard_content.py::"
            "test_blade_ward_resists_only_weapon_attack_bps_until_next_turn_end",
        ),
        "book:players-handbook-2014:p218-219",
    ),
    CoreBoundary(
        "dnd5e.core.spell.hypnotic_pattern",
        ("2014",),
        (
            "spells.is_core_hypnotic_pattern_spell|"
            "combat_engine.resolve_hypnotic_pattern_target|"
            "combat_engine.reconcile_effect_dependencies"
        ),
        (
            "tests/test_combat_engine.py::"
            "test_hypnotic_pattern_effect_lifecycle_preserves_other_condition_sources",
        ),
        "bundled:srd2014/07_Spells/Spells_Each/Hypnotic_Pattern.md",
    ),
    CoreBoundary(
        "dnd5e.core.spell.witch_bolt",
        ("2014",),
        (
            "spells.is_core_witch_bolt_spell|"
            "combat_engine.start_witch_bolt_tether|"
            "combat_engine.pay_witch_bolt_sustain_action"
        ),
        (
            "tests/test_standard_content.py::"
            "test_witch_bolt_uses_scaled_initial_damage_and_fixed_repeat_action",
        ),
        "book:players-handbook-2014:p289",
    ),
    CoreBoundary(
        "dnd5e.core.spell.magic_item_charges",
        ("2014", "2024"),
        "spells.consume_magic_item_spell_cast",
        ("tests/test_spells.py::test_magic_item_charges_cast_source_bound_defenses",),
        "bundled:srd/magic-items",
    ),
    CoreBoundary(
        "dnd5e.core.magic_item.charge_recovery",
        ("2014", "2024"),
        "spells.recharge_magic_item_charges",
        ("tests/test_spells.py::test_magic_item_charge_recovery_and_last_charge_check",),
        "bundled:srd/magic-items",
    ),
    CoreBoundary(
        "dnd5e.core.magic_item.last_charge",
        ("2014", "2024"),
        "spells.resolve_magic_item_last_charge",
        ("tests/test_spells.py::test_magic_item_charge_recovery_and_last_charge_check",),
        "bundled:srd/magic-items",
    ),
    CoreBoundary(
        "dnd5e.core.magic_item.damage_resistance",
        ("2014",),
        "combat_engine._damage_defense_traits",
        ("tests/test_combat_engine.py::test_attuned_magic_item_grants_damage_resistance",),
        "bundled:srd2014/09_Magic_Items/Magic_Items_Each/Ring_of_Resistance.md",
    ),
    CoreBoundary(
        "dnd5e.core.spell.magic_missile",
        ("2014", "2024"),
        (
            "spells.is_core_magic_missile_spell|magic_missile_dart_count|"
            "validate_magic_missile_allocations"
        ),
        (
            "tests/test_spells.py::"
            "test_magic_missile_allocation_and_shield_trigger_are_source_bound",
        ),
        "bundled:srd/magic-missile",
    ),
    CoreBoundary(
        "dnd5e.core.spell.magic_missile_darts",
        ("2014", "2024"),
        "spells.validate_magic_missile_allocations",
        (
            "tests/test_spells.py::test_magic_missile_allocation_and_shield_trigger_are_source_bound",
        ),
        "bundled:srd/magic-missile",
    ),
    CoreBoundary(
        "dnd5e.core.spell.structured_resolution",
        ("2014", "2024"),
        "spell_resolution.py|combat_engine.preflight_spell_attack",
        ("tests/test_spell_resolution.py", "tests/test_core_content.py"),
        "bundled:srd2014/07_Spells",
    ),
    CoreBoundary(
        "dnd5e.core.spell.raise_dead",
        ("2014",),
        "lifecycle.apply_raise_dead_to_sheet|reduce_revival_ordeal_after_long_rest",
        (
            "tests/test_lifecycle.py::"
            "test_raise_dead_restores_one_hp_and_reduces_its_ordeal_each_long_rest",
            "tests/test_combat_engine.py::"
            "test_active_roll_effects_apply_to_attacks_saves_and_ability_checks",
        ),
        "bundled:srd2014/07_Spells/Spells_Each/Raise_Dead.md",
    ),
    CoreBoundary(
        "dnd5e.core.ready.action",
        ("2014", "2024"),
        "combat_engine.trigger_readied_action|resolve_readied_action_window",
        ("tests/test_combat_engine.py",),
        "bundled:srd/ready",
    ),
    CoreBoundary(
        "dnd5e.core.save.restrained_dexterity",
        ("2014", "2024"),
        "combat_engine.resolve_actor_check",
        ("tests/test_combat_engine.py",),
        "bundled:srd/restrained",
    ),
    CoreBoundary(
        "dnd5e.core.save.magic_resistance",
        ("2014",),
        "combat_engine.resolve_actor_check",
        (
            "tests/test_combat_engine.py::"
            "test_magic_resistance_requires_source_kind_and_applies_advantage",
            "tests/test_statblocks.py::"
            "test_magic_resistance_and_evasion_are_structured_from_exact_text",
        ),
        "source:monster-manual-2014:magic-resistance",
    ),
    CoreBoundary(
        "dnd5e.core.save.evasion",
        ("2014",),
        "combat_engine.standard_save_damage_reduction",
        (
            "tests/test_combat_engine.py::"
            "test_evasion_rewrites_dexterity_save_for_half_damage",
            "tests/test_statblocks.py::"
            "test_magic_resistance_and_evasion_are_structured_from_exact_text",
        ),
        "source:monster-manual-2014:evasion",
    ),
    CoreBoundary(
        "dnd5e.core.rest.hit_dice",
        ("2014", "2024"),
        "lifecycle.roll_rest_hit_dice|apply_rest",
        ("tests/test_lifecycle.py",),
        "bundled:srd/resting",
    ),
    CoreBoundary(
        "dnd5e.core.rest.arcane_recovery",
        ("2014",),
        "lifecycle.validate_arcane_recovery_choice|apply_arcane_recovery_choice",
        ("tests/test_lifecycle.py",),
        "bundled:srd2014/02_Classes/Wizard.md",
    ),
    CoreBoundary(
        "dnd5e.core.rest.song_of_rest",
        ("2014",),
        "lifecycle.validate_song_of_rest_source|apply_rest",
        ("tests/test_lifecycle.py::test_song_of_rest_applies_once_per_eligible_creature",),
        "bundled:srd2014/02_Classes/Bard.md",
    ),
    CoreBoundary(
        "dnd5e.core.rest.natural_recovery",
        ("2014",),
        "lifecycle.validate_natural_recovery_choice|apply_natural_recovery_choice",
        ("tests/test_lifecycle.py::test_natural_recovery_is_once_per_long_rest",),
        "bundled:srd2014/02_Classes/Druid.md",
    ),
    CoreBoundary(
        "dnd5e.core.rest.sorcerous_restoration",
        ("2014",),
        "lifecycle.apply_sorcerous_restoration",
        ("tests/test_lifecycle.py::test_sorcerous_restoration_recovers_four_points",),
        "bundled:srd2014/02_Classes/Sorcerer.md",
    ),
    CoreBoundary(
        "dnd5e.core.rest.exhaustion",
        ("2014", "2024"),
        "lifecycle.apply_rest|character_schema.effective_hit_point_maximum",
        (
            "tests/test_lifecycle.py",
            "tests/test_character_schema.py::test_2014_exhaustion_halves_effective_hit_point_maximum",
        ),
        "bundled:srd/exhaustion",
    ),
    CoreBoundary(
        "dnd5e.core.rest.long_rest_timing",
        ("2014", "2024"),
        "lifecycle.record_rest_completion",
        ("tests/test_lifecycle.py::test_rest_completion_enforces_duration_and_daily_limit",),
        "bundled:srd/resting",
    ),
    CoreBoundary(
        "dnd5e.core.spell.cantrip_ritual_level",
        ("2014", "2024"),
        "spells.consume_spell_cast",
        ("tests/test_spells.py::test_cantrip_and_ritual_reject_slot_levels",),
        "bundled:srd/spellcasting",
    ),
    CoreBoundary(
        "dnd5e.core.spell.pact_magic",
        ("2014",),
        "spells.consume_spell_cast",
        ("tests/test_spells.py::test_pact_magic_uses_its_recorded_slot_level",),
        "bundled:srd/pact-magic",
    ),
    CoreBoundary(
        "dnd5e.core.spell.material_components",
        ("2014", "2024"),
        "spells.consume_spell_cast",
        ("tests/test_spells.py::test_costly_material_component_requires_dm_confirmation",),
        "bundled:srd/components",
    ),
    CoreBoundary(
        "dnd5e.core.spell.preparation",
        ("2014", "2024"),
        "spells.replace_prepared_spells",
        (
            "tests/test_spells.py::test_2024_ranger_long_rest_replaces_only_one_spell",
            "tests/test_spells.py::test_preparation_rejects_illegal_event_and_class_timing",
        ),
        "bundled:srd/preparing-spells",
    ),
    CoreBoundary(
        "dnd5e.core.spell.spellbook_copy",
        ("2014",),
        "sagasmith_dnd_mcp.server.settle_spellbook_copy",
        ("SagaSmith-dnd-mcp/tests/test_spellbook_copy_mcp.py",),
        "bundled:srd/wizard-spellbook-copying",
    ),
    CoreBoundary(
        "dnd5e.core.spell.evocation_savant",
        ("2014",),
        "sagasmith_dnd_mcp.server.settle_spellbook_copy",
        ("SagaSmith-dnd-mcp/tests/test_spellbook_copy_mcp.py",),
        "bundled:srd2014/02_Classes/Wizard.md#evocation-savant",
    ),
    CoreBoundary(
        "dnd5e.core.mcp.combat_mutation_guard",
        ("2014", "2024"),
        "sagasmith_dnd_mcp.server.require_outside_active_combat",
        ("SagaSmith-dnd-mcp/tests/test_runtime_integrity_mcp.py",),
        "runtime:mcp/action-economy-boundary",
    ),
    CoreBoundary(
        "dnd5e.core.mcp.opportunity_melee_only",
        ("2014", "2024"),
        "sagasmith_dnd_mcp.server.combat_reaction_attack",
        ("SagaSmith-dnd-mcp/tests/test_runtime_integrity_mcp.py",),
        "bundled:srd/opportunity-attacks",
    ),
    CoreBoundary(
        "dnd5e.core.mcp.reaction_defense_atomicity",
        ("2014", "2024"),
        "sagasmith_dnd_mcp.server.combat_reaction_defense",
        ("SagaSmith-dnd-mcp/tests/test_reaction_defense_mcp.py",),
        "runtime:mcp/post-hit-pre-damage-reaction",
    ),
    CoreBoundary(
        "dnd5e.core.mcp.shield_attack_reaction_atomicity",
        ("2014", "2024"),
        "sagasmith_dnd_mcp.server.combat_reaction_defense",
        ("SagaSmith-dnd-mcp/tests/test_reaction_defense_mcp.py",),
        "runtime:mcp/shield-post-hit-reaction",
    ),
    CoreBoundary(
        "dnd5e.core.mcp.magic_missile_atomicity",
        ("2014", "2024"),
        "sagasmith_dnd_mcp.server.combat_cast_spell|combat_magic_missile_defense",
        ("SagaSmith-dnd-mcp/tests/test_magic_missile_mcp.py",),
        "runtime:mcp/magic-missile-targeting-darts-shield",
    ),
    CoreBoundary(
        "dnd5e.core.mcp.save_damage_atomicity",
        ("2014", "2024"),
        (
            "sagasmith_dnd_mcp.server.combat_save_damage|"
            "combat_engine.resolve_save_damage_to_sheets"
        ),
        ("SagaSmith-dnd-mcp/tests/test_agent_save_damage_mcp.py",),
        "runtime:mcp/source-bound-save-and-damage",
    ),
    CoreBoundary(
        "dnd5e.core.mcp.duration_clock",
        ("2014", "2024"),
        "sagasmith_dnd_mcp.server.campaign_advance_effects",
        (
            "SagaSmith-dnd-mcp/tests/test_runtime_integrity_mcp.py",
            "SagaSmith-dnd-mcp/tests/test_campaign_clock_mcp.py",
            "SagaSmith-dnd-mcp/tests/test_stable_recovery_mcp.py",
            "SagaSmith-dnd-mcp/tests/test_spellbook_copy_mcp.py",
        ),
        "runtime:mcp/actor-and-world-duration-clock",
    ),
    CoreBoundary(
        "dnd5e.core.mcp.combat_spell_boundary",
        ("2014", "2024"),
        "sagasmith_dnd_mcp.server.combat_cast_spell",
        ("SagaSmith-dnd-mcp/tests/test_runtime_integrity_mcp.py",),
        "runtime:mcp/spell-action-economy",
    ),
    CoreBoundary(
        "dnd5e.core.mcp.pending_ruling_atomicity",
        ("2014", "2024"),
        "sagasmith_dnd_mcp.server",
        ("SagaSmith-dnd-mcp/tests/test_runtime_integrity_mcp.py",),
        "runtime:mcp/dm-ruling-boundary",
    ),
)


def get_core_rule_pack(edition: str | None) -> BuiltinCoreRulePack:
    raw = str(edition or "").strip()
    try:
        normalized = normalize_dnd_edition(edition, default="")
    except ValueError as exc:
        raise ValueError(f"unsupported D&D core edition: {raw or '<empty>'}") from exc
    if normalized not in SUPPORTED_DND_EDITIONS:
        raise ValueError(f"unsupported D&D core edition: {normalized}")
    boundaries = tuple(item for item in BOUNDARIES if normalized in item.editions)
    payload = {
        "id": f"dnd5e.core.{normalized}",
        "version": CORE_RULE_PACK_VERSION,
        "edition": normalized,
        "boundaries": [
            {
                "id": item.id,
                "implementation": item.implementation,
                "test_refs": item.test_refs,
                "citation": item.citation,
            }
            for item in boundaries
        ],
    }
    fingerprint = json_sha256(payload)
    return BuiltinCoreRulePack(
        id=payload["id"],
        version=CORE_RULE_PACK_VERSION,
        edition=normalized,
        fingerprint=fingerprint,
        boundaries=boundaries,
    )
