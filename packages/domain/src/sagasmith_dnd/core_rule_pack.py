"""Built-in D&D core rule packs that wrap the currently verified engine behavior."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sagasmith_core.integrity import json_sha256

from sagasmith_dnd.editions import SUPPORTED_DND_EDITIONS, normalize_dnd_edition

CORE_RULE_PACK_VERSION = "1.76.0"


@dataclass(frozen=True)
class CoreBoundaryEvidence:
    """Edition-specific proof for one built-in rules boundary."""

    edition: str
    citation: str
    test_refs: tuple[str, ...]


@dataclass(frozen=True)
class CoreBoundary:
    id: str
    editions: tuple[str, ...]
    implementation: str
    test_refs: tuple[str, ...]
    citation: str
    evidence: tuple[CoreBoundaryEvidence, ...] = ()

    def evidence_for(self, edition: str) -> CoreBoundaryEvidence:
        """Return exact evidence without borrowing another edition's source."""

        normalized = normalize_dnd_edition(edition)
        explicit = next(
            (item for item in self.evidence if item.edition == normalized),
            None,
        )
        if explicit is not None:
            return explicit
        if normalized not in self.editions:
            raise KeyError(f"{self.id}:{normalized}")
        if normalized == "2024":
            citation = (
                _2024_BOUNDARY_CITATIONS.get(self.id)
                or _2024_CATEGORY_CITATIONS.get(_boundary_category(self.id))
                or (self.citation if self.citation.startswith("runtime:") else "")
            )
        else:
            citation = self.citation
        if not citation:
            raise KeyError(f"{self.id}:{normalized}:citation")
        return CoreBoundaryEvidence(
            edition=normalized,
            citation=citation,
            test_refs=self.test_refs,
        )


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
            "citations": [
                {
                    "source": boundary.citation,
                    "edition": self.edition,
                }
            ],
            "ruleset_fingerprint": self.fingerprint,
        }


def _boundary_category(boundary_id: str) -> str:
    parts = str(boundary_id).split(".")
    return parts[2] if len(parts) > 2 else ""


# The 2024 pack must never inherit a 2014 locator merely because the engine
# implementation is shared. These locators point at the bundled SRD 5.2.1
# Markdown that was used to verify each generic rules family.
_2024_CATEGORY_CITATIONS = {
    "ability_generation": "bundled:srd2024/DND5eSRD_019-035.md#creating-a-character",
    "action": "bundled:srd2024/DND5eSRD_176-191.md#actions",
    "activity": "bundled:srd2024/DND5eSRD_176-191.md#limited-use",
    "armor_class": "bundled:srd2024/DND5eSRD_087-103.md#armor",
    "armor": "bundled:srd2024/DND5eSRD_087-103.md#armor-training",
    "attack": "bundled:srd2024/DND5eSRD_176-191.md#attack-roll",
    "check": "bundled:srd2024/DND5eSRD_176-191.md#ability-check",
    "damage": "bundled:srd2024/DND5eSRD_176-191.md#damage-and-healing",
    "initiative": "bundled:srd2024/DND5eSRD_176-191.md#initiative",
    "item": "bundled:srd2024/DND5eSRD_204-229.md#magic-items",
    "magic_item": "bundled:srd2024/DND5eSRD_204-229.md#magic-items",
    "movement": "bundled:srd2024/DND5eSRD_176-191.md#movement",
    "progression": "bundled:srd2024/DND5eSRD_019-035.md#level-advancement",
    "reaction": "bundled:srd2024/DND5eSRD_176-191.md#reaction",
    "ready": "bundled:srd2024/DND5eSRD_176-191.md#ready-action",
    "rest": "bundled:srd2024/DND5eSRD_176-191.md#resting",
    "save": "bundled:srd2024/DND5eSRD_176-191.md#saving-throw",
    "spell": "bundled:srd2024/DND5eSRD_104-120.md#casting-spells",
    "weapon": "bundled:srd2024/DND5eSRD_087-103.md#weapons",
}

_2024_BOUNDARY_CITATIONS = {
    "dnd5e.core.weapon.mastery": ("bundled:srd2024/DND5eSRD_087-103.md#mastery-properties"),
    "dnd5e.core.heroic_inspiration": ("bundled:srd2024/DND5eSRD_176-191.md#heroic-inspiration"),
    "dnd5e.core.activity.recharge": ("bundled:srd2024/DND5eSRD_253-272.md#limited-usage"),
    "dnd5e.core.rest.arcane_recovery": (
        "bundled:srd2024/DND5eSRD_077-086.md#level-1-arcane-recovery"
    ),
    "dnd5e.core.rest.sorcerous_restoration": (
        "bundled:srd2024/DND5eSRD_064-076.md#level-5-sorcerous-restoration"
    ),
    "dnd5e.core.progression.extra_attack": (
        "bundled:srd2024/DND5eSRD_019-035.md#level-5-extra-attack"
    ),
    "dnd5e.core.activity.turn_undead": (
        "bundled:srd2024/DND5eSRD_036-046.md#level-2-channel-divinity"
    ),
    "dnd5e.core.activity.divine_spark": (
        "bundled:srd2024/DND5eSRD_036-046.md#level-2-channel-divinity"
    ),
    "dnd5e.core.activity.sear_undead": ("bundled:srd2024/DND5eSRD_036-046.md#level-5-sear-undead"),
    "dnd5e.core.activity.preserve_life": (
        "bundled:srd2024/DND5eSRD_036-046.md#level-3-preserve-life"
    ),
    "dnd5e.core.activity.cunning_action": (
        "bundled:srd2024/DND5eSRD_047-063.md#level-2-cunning-action"
    ),
    "dnd5e.core.check.jack_of_all_trades": (
        "bundled:srd2024/DND5eSRD_019-035.md#level-2-jack-of-all-trades"
    ),
    "dnd5e.core.attack.sneak_attack": ("bundled:srd2024/DND5eSRD_047-063.md#level-1-sneak-attack"),
    "dnd5e.core.save.evasion": ("bundled:srd2024/DND5eSRD_047-063.md#level-7-evasion"),
    "dnd5e.core.spell.pact_magic": ("bundled:srd2024/DND5eSRD_064-076.md#level-1-pact-magic"),
    "dnd5e.core.spell.spellbook_copy": (
        "bundled:srd2024/DND5eSRD_077-086.md#expanding-and-replacing-a-spellbook"
    ),
    "dnd5e.core.spell.fly": ("bundled:srd2024/DND5eSRD_121-137.md#fly"),
    "dnd5e.core.spell.invisibility": ("bundled:srd2024/DND5eSRD_138-154.md#invisibility"),
    "dnd5e.core.spell.hypnotic_pattern": ("bundled:srd2024/DND5eSRD_138-154.md#hypnotic-pattern"),
    "dnd5e.core.action.multiattack_choice": ("bundled:srd2024/DND5eSRD_253-272.md#monster-actions"),
    "dnd5e.core.spell.structured_resolution": (
        "bundled:srd2024/DND5eSRD_104-120.md#casting-spells"
    ),
    "dnd5e.core.mcp.opportunity_melee_only": (
        "bundled:srd2024/DND5eSRD_176-191.md#opportunity-attacks"
    ),
    "dnd5e.core.mcp.shield_attack_reaction_atomicity": (
        "bundled:srd2024/DND5eSRD_155-175.md#shield"
    ),
    "dnd5e.core.mcp.magic_missile_atomicity": ("bundled:srd2024/DND5eSRD_138-154.md#magic-missile"),
}


BOUNDARIES = (
    CoreBoundary(
        "dnd5e.core.environment.suffocation",
        ("2014",),
        "breathing.begin_holding_breath|lifecycle.advance_effect_durations|"
        "lifecycle.advance_elapsed_effect_durations",
        ("tests/test_breathing.py", "../mcp/tests/test_breathing_mcp.py"),
        "bundled:srd2014/06_Gameplay/Adventuring.md#suffocating",
    ),
    CoreBoundary(
        "dnd5e.core.heroic_inspiration",
        ("2024",),
        (
            "heroic_inspiration.grant_heroic_inspiration|"
            "heroic_inspiration.spend_heroic_inspiration_reroll|"
            "lifecycle.apply_rest"
        ),
        ("tests/test_heroic_inspiration.py", "tests/test_lifecycle.py"),
        "bundled:srd2024/DND5eSRD_176-191.md#heroic-inspiration",
    ),
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
        ("2014", "2024"),
        "combat_engine.resolve_turn_undead_to_sheets|spend_movement|available_actions",
        (
            "packages/mcp/tests/test_turn_undead_mcp.py::"
            "test_turn_undead_preflights_then_commits_all_actors_atomically",
        ),
        "bundled:srd2014/02_Classes/Cleric.md#channel-divinity-turn-undead",
    ),
    CoreBoundary(
        "dnd5e.core.activity.divine_spark",
        ("2024",),
        "combat_engine.resolve_divine_spark_to_sheet",
        (
            "tests/test_combat_engine.py::"
            "test_2024_divine_spark_heals_or_deals_save_for_half_damage",
        ),
        "bundled:srd2024/DND5eSRD_036-046.md#level-2-channel-divinity",
    ),
    CoreBoundary(
        "dnd5e.core.activity.sear_undead",
        ("2024",),
        "combat_engine.resolve_turn_undead_to_sheets",
        (
            "tests/test_combat_engine.py::"
            "test_2024_sear_undead_shares_one_roll_without_ending_the_turn_effect",
        ),
        "bundled:srd2024/DND5eSRD_036-046.md#level-5-sear-undead",
    ),
    CoreBoundary(
        "dnd5e.core.activity.legendary_action",
        ("2014",),
        (
            "statblocks.legendary_action_spec|"
            "combat_engine.pay_legendary_action|pay_attack_action|spend_movement"
        ),
        (
            "tests/test_combat_engine.py::"
            "test_legendary_action_pool_and_weapon_followup_follow_2014_timing",
            "tests/test_portable_monster_semantics.py::"
            "test_generic_legendary_weapon_action_is_structured",
        ),
        "bundled:srd2014/10_Monsters/Monsters.md#legendary-actions",
    ),
    CoreBoundary(
        "dnd5e.core.activity.recharge",
        ("2014", "2024"),
        "activities.recharge_activities_at_turn_start",
        ("tests/test_activities.py::test_recharge_activities_roll_only_while_unavailable",),
        "rulebook:mm2014/introduction/limited-usage#recharge-x-y",
    ),
    CoreBoundary(
        "dnd5e.core.activity.dragonborn_breath_weapon",
        ("2014",),
        "combat_engine.resolve_save_damage_to_sheets",
        (
            "tests/test_standard_content.py::"
            "test_standard_2014_mechanics_pack_is_separate_from_srd_and_native",
        ),
        "book:players-handbook-2014:p34",
    ),
    CoreBoundary(
        "dnd5e.core.activity.action_surge",
        ("2014", "2024"),
        "combat_engine.settle_core_activity_effect",
        ("tests/test_combat_engine.py",),
        "bundled:srd2014/02_Classes/Fighter.md#action-surge",
    ),
    CoreBoundary(
        "dnd5e.core.activity.second_wind",
        ("2014", "2024"),
        "combat_engine.resolve_second_wind_to_sheet",
        ("tests/test_combat_engine.py",),
        "bundled:srd2014/02_Classes/Fighter.md#second-wind",
    ),
    CoreBoundary(
        "dnd5e.core.activity.cunning_action",
        ("2014", "2024"),
        "combat_engine.settle_core_activity_effect",
        ("tests/test_combat_engine.py",),
        "bundled:srd2014/02_Classes/Rogue.md#cunning-action",
    ),
    CoreBoundary(
        "dnd5e.core.activity.orc_aggressive",
        ("2014",),
        "statblocks._parse_srd_statblock|combat_engine.settle_core_activity_effect|"
        "combat_engine.spend_movement",
        (
            "tests/test_statblocks.py::test_orc_aggressive_is_a_source_bound_bonus_action",
            "tests/test_combat_engine.py::test_orc_aggressive_grants_separate_toward_only_movement",
        ),
        "bundled:srd2014/10_Monsters/Monsters_Each/Orc.md#aggressive",
    ),
    CoreBoundary(
        "dnd5e.core.activity.preserve_life",
        ("2014", "2024"),
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
        ("2014", "2024"),
        "progression.advance_single_class_level",
        ("tests/test_progression.py",),
        "bundled:srd2014/03_Characterization/Beyond_1st_Level.md",
    ),
    CoreBoundary(
        "dnd5e.core.progression.spellcasting",
        ("2014", "2024"),
        "progression.advance_single_class_level",
        ("tests/test_progression.py",),
        "bundled:srd2014/02_Classes",
    ),
    CoreBoundary(
        "dnd5e.core.progression.extra_attack",
        ("2014", "2024"),
        "progression.advance_single_class_level",
        (
            "tests/test_progression.py::"
            "test_extra_attack_scaling_uses_the_highest_class_feature_without_stacking",
        ),
        "bundled:srd2014/02_Classes#extra-attack",
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
        "dnd5e.core.armor.proficiency_and_strength",
        ("2014", "2024"),
        "character_schema._armor_proficiency_state|derive_character_sheet|"
        "combat_engine.resolve_actor_check|spells.consume_spell_cast",
        (
            "tests/test_character_schema.py::"
            "test_2014_armor_proficiency_strength_and_encumbrance_affect_derived_rules",
            "tests/test_combat_engine.py::"
            "test_nonproficient_armor_and_heavy_encumbrance_apply_check_disadvantage",
            "tests/test_spells.py::test_nonproficient_equipped_armor_blocks_spell_casting",
        ),
        "bundled:srd2014/04_Equipment/Armor.md",
    ),
    CoreBoundary(
        "dnd5e.core.encumbrance",
        ("2014",),
        "character_schema.derive_character_sheet|combat_engine.resolve_actor_check",
        (
            "tests/test_character_schema.py::"
            "test_2014_armor_proficiency_strength_and_encumbrance_affect_derived_rules",
            "tests/test_combat_engine.py::"
            "test_nonproficient_armor_and_heavy_encumbrance_apply_check_disadvantage",
        ),
        "bundled:srd2014/04_Equipment#lifting-and-carrying",
    ),
    CoreBoundary(
        "dnd5e.core.weapon.proficiency_and_finesse",
        ("2014", "2024"),
        "character_schema._weapon_attacks",
        (
            "tests/test_character_schema.py::"
            "test_weapon_attacks_derive_actor_proficiency_and_finesse_ability",
        ),
        "bundled:srd/weapon-properties",
    ),
    CoreBoundary(
        "dnd5e.core.check.jack_of_all_trades",
        ("2014", "2024"),
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
        ("tests/test_combat_engine.py::test_2014_group_check_succeeds_when_at_least_half_succeed",),
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
        "dnd5e.core.action.dodge",
        ("2014",),
        (
            "combat_engine.dodge_benefit_active|"
            "combat_engine.reconcile_dodge_lifecycle|"
            "combat_engine.encounter_dodge_save_advantage|"
            "combat_engine.resolve_actor_check"
        ),
        (
            "tests/test_combat_engine.py::test_dodge_lasts_until_start_of_next_turn_and_affects_attacks",
            "tests/test_combat_engine.py::test_dodge_lifecycle_does_not_reactivate_after_invalidating_state_ends",
            "tests/test_combat_engine.py::test_dodge_advantage_uses_authoritative_encounter_and_normalized_dexterity",
            "tests/test_combat_engine.py::test_area_save_damage_applies_dodge_per_authoritative_target",
        ),
        "bundled:srd2014/06_Gameplay/Order_of_Combat.md#dodge",
    ),
    CoreBoundary(
        "dnd5e.core.activity.tortle_shell_defense",
        ("2014",),
        (
            "combat_engine.enter_tortle_shell_defense|"
            "combat_engine.emerge_tortle_shell_defense|"
            "combat_engine.reconcile_tortle_shell_defense_projection|"
            "combat_engine.resolve_actor_check"
        ),
        (
            "tests/test_combat_engine.py::test_2014_tortle_shell_defense_settles_exact_source_effects",
            "tests/test_combat_engine.py::test_2014_tortle_shell_defense_rejects_spoofed_source",
        ),
        "rulebook:tortle-package/tortle#shell-defense",
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
        "dnd5e.core.attack.sneak_attack",
        ("2014", "2024"),
        "combat_engine._sneak_attack_plan|combat_engine.resolve_attack_action",
        (
            "tests/test_combat_engine.py::"
            "test_sneak_attack_requires_card_feature_and_records_critical_bonus_damage",
        ),
        "bundled:srd2014/02_Classes/Rogue.md#sneak-attack",
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
        "dnd5e.core.weapon.mastery",
        ("2024",),
        (
            "character_schema._normalize_item_mechanics|"
            "combat_engine.preflight_attack|combat_engine.resolve_attack_damage|"
            "combat_engine.apply_weapon_mastery_to_encounter"
        ),
        ("tests/test_combat_engine.py", "tests/test_core_content_2024.py"),
        "bundled:srd2024/DND5eSRD_087-103.md#mastery-properties",
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
        "dnd5e.core.damage.relentless_endurance",
        ("2014",),
        "combat_engine._apply_adjusted_damage|apply_hit_point_loss_to_sheet",
        (
            "tests/test_combat_engine.py::"
            "test_standard_relentless_endurance_is_core_card_bound_and_once_per_rest",
        ),
        "bundled:srd2014/01_Races/Races_Each/Half-Orc.md#relentless-endurance",
    ),
    CoreBoundary(
        "dnd5e.core.movement.dwarf_heavy_armor_speed",
        ("2014",),
        "character_schema.derive_character_sheet",
        (
            "tests/test_character_schema.py::"
            "test_2014_dwarf_heavy_armor_speed_exception_is_source_bound_and_narrow",
        ),
        "bundled:srd2014/01_Races/Races_Each/Dwarf.md#speed",
    ),
    CoreBoundary(
        "dnd5e.core.ac.tortle_natural_armor",
        ("2014",),
        "character_schema._derive_armor_class",
        (
            "tests/test_character_schema.py::"
            "test_2014_tortle_natural_armor_ignores_worn_armor_but_allows_shields",
        ),
        "rulebook:tortle-package/tortle#natural-armor",
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
        "dnd5e.core.movement.forced_and_teleport",
        ("2014", "2024"),
        "combat_engine.spend_movement",
        (
            "tests/test_combat_engine.py::"
            "test_forced_movement_and_teleport_bypass_turn_speed_and_condition_limits",
        ),
        "bundled:srd/opportunity-attacks",
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
        (
            "tests/test_combat_engine.py::"
            "test_agent_compiled_reaction_defense_opens_after_hit_and_before_damage",
        ),
        "bundled:srd/reactions",
    ),
    CoreBoundary(
        "dnd5e.core.spell.shield",
        ("2014", "2024"),
        ("spells.is_core_shield_spell|available_shield_cast_options|consume_shield_reaction"),
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
        "dnd5e.core.spell.fly",
        ("2014", "2024"),
        (
            "spells.fly_target_limit|spells.apply_core_fly_effects|"
            "spells.reconcile_source_effect_dependencies|"
            "character_schema.derive_character_sheet"
        ),
        (
            "tests/test_spells.py::test_fly_applies_willing_target_speed_and_tracks_concentration",
            "tests/test_spells.py::"
            "test_fly_upcast_target_limit_and_source_dependency_are_hard_settled",
        ),
        "bundled:srd2014/07_Spells/Spells_Each/Fly.md",
    ),
    CoreBoundary(
        "dnd5e.core.spell.invisibility",
        ("2014", "2024"),
        (
            "spells.invisibility_target_limit|"
            "spells.apply_core_invisibility_effects|"
            "spells.reconcile_source_effect_dependencies|"
            "spells._end_spell_cast_broken_invisibility|"
            "combat_engine._end_attack_broken_invisibility"
        ),
        (
            "tests/test_spells.py::"
            "test_invisibility_applies_to_explicit_targets_and_tracks_concentration",
            "tests/test_spells.py::"
            "test_upcast_invisibility_targets_end_independently_and_with_the_source",
        ),
        "bundled:srd2014/07_Spells/Spells_Each/Invisibility.md",
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
        ("2014", "2024"),
        (
            "spells.is_core_hypnotic_pattern_spell|"
            "combat_engine.resolve_hypnotic_pattern_target|"
            "combat_engine.reconcile_effect_dependencies"
        ),
        (
            "tests/test_combat_engine.py::"
            "test_hypnotic_pattern_effect_lifecycle_preserves_other_condition_sources",
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
        "dnd5e.core.save.evasion",
        ("2014", "2024"),
        "combat_engine.standard_save_damage_reduction",
        (
            "tests/test_combat_engine.py::test_evasion_rewrites_dexterity_save_for_half_damage",
            "tests/test_evasion_editions.py::"
            "test_real_evasion_artifacts_preserve_edition_specific_incapacitation",
            "tests/test_evasion_editions.py::test_real_evasion_does_not_rewrite_unrelated_saves",
            "tests/test_core_content.py::"
            "test_srd2014_content_uses_leaf_records_and_structured_eligibility",
        ),
        "bundled:srd2014/02_Classes/Rogue.md#evasion",
    ),
    CoreBoundary(
        "dnd5e.core.rest.hit_dice",
        ("2014", "2024"),
        (
            "lifecycle.roll_rest_hit_dice|apply_rest|"
            "record_rest_completion|apply_short_rest_hit_die_choice"
        ),
        (
            "tests/test_lifecycle.py::"
            "test_2014_short_rest_requires_sequential_additional_hit_dice",
            "packages/mcp/tests/test_rest_hit_dice_mcp.py::"
            "test_2014_short_rest_hit_dice_are_chosen_sequentially_across_restart",
            "packages/mcp/tests/test_rest_hit_dice_mcp.py::"
            "test_2014_short_rest_hit_die_stops_at_full_hp_and_supports_decline",
            "packages/mcp/tests/test_rest_hit_dice_mcp.py::"
            "test_2014_short_rest_hit_die_roll_and_state_rollback_together",
        ),
        "bundled:srd/resting",
    ),
    CoreBoundary(
        "dnd5e.core.rest.arcane_recovery",
        ("2014", "2024"),
        "lifecycle.validate_arcane_recovery_choice|apply_arcane_recovery_choice",
        ("tests/test_lifecycle.py",),
        "bundled:srd2014/02_Classes/Wizard.md",
    ),
    CoreBoundary(
        "dnd5e.core.rest.song_of_rest",
        ("2014",),
        (
            "lifecycle.validate_song_of_rest_source|apply_rest|"
            "apply_short_rest_hit_die_choice"
        ),
        (
            "tests/test_lifecycle.py::test_song_of_rest_applies_once_per_eligible_creature",
            "packages/mcp/tests/test_rest_hit_dice_mcp.py::"
            "test_2014_sequential_hit_die_recovers_stable_zero_hp_and_delays_song_of_rest",
        ),
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
        ("2014", "2024"),
        ("lifecycle.validate_sorcerous_restoration_choice|apply_sorcerous_restoration"),
        (
            "tests/test_lifecycle.py::test_sorcerous_restoration_recovers_four_points",
            "tests/test_lifecycle.py::"
            "test_2024_sorcerous_restoration_uses_declared_points_once_per_long_rest",
        ),
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
        ("2014", "2024"),
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
        ("2014", "2024"),
        "sagasmith_dnd_mcp.server.settle_spellbook_copy",
        ("packages/mcp/tests/test_spellbook_copy_mcp.py",),
        "bundled:srd/wizard-spellbook-copying",
    ),
    CoreBoundary(
        "dnd5e.core.spell.evocation_savant",
        ("2014",),
        "sagasmith_dnd_mcp.server.settle_spellbook_copy",
        ("packages/mcp/tests/test_spellbook_copy_mcp.py",),
        "bundled:srd2014/02_Classes/Wizard.md#evocation-savant",
    ),
    CoreBoundary(
        "dnd5e.core.narrative.watchers_eye",
        ("2014",),
        (
            "sagasmith_dnd_mcp.server._watchers_eye_source_binding|"
            "sagasmith_dnd_mcp.server.character_source_feature"
        ),
        ("packages/mcp/tests/test_scag_watchers_eye_mcp.py",),
        (
            "rule-source:user.rulebook.d-d-5e-sword-coast-adventurer-s-guide.16e6a243ef"
            "#chunk:user.rulebook.d-d-5e-sword-coast-adventurer-s-guide.16e6a243ef/"
            "section-613/chunk-787-c40f25fa340c7592"
        ),
    ),
    CoreBoundary(
        "dnd5e.core.mcp.combat_mutation_guard",
        ("2014", "2024"),
        "sagasmith_dnd_mcp.server.require_outside_active_combat",
        ("packages/mcp/tests/test_runtime_integrity_mcp.py",),
        "runtime:mcp/action-economy-boundary",
    ),
    CoreBoundary(
        "dnd5e.core.mcp.opportunity_melee_only",
        ("2014", "2024"),
        "sagasmith_dnd_mcp.server.combat_reaction_attack",
        ("packages/mcp/tests/test_runtime_integrity_mcp.py",),
        "bundled:srd/opportunity-attacks",
    ),
    CoreBoundary(
        "dnd5e.core.mcp.reaction_defense_atomicity",
        ("2014", "2024"),
        "sagasmith_dnd_mcp.server.combat_reaction_defense",
        ("packages/mcp/tests/test_reaction_defense_mcp.py",),
        "runtime:mcp/post-hit-pre-damage-reaction",
    ),
    CoreBoundary(
        "dnd5e.core.mcp.shield_attack_reaction_atomicity",
        ("2014", "2024"),
        "sagasmith_dnd_mcp.server.combat_reaction_defense",
        ("packages/mcp/tests/test_reaction_defense_mcp.py",),
        "runtime:mcp/shield-post-hit-reaction",
    ),
    CoreBoundary(
        "dnd5e.core.mcp.magic_missile_atomicity",
        ("2014", "2024"),
        "sagasmith_dnd_mcp.server.combat_cast_spell|combat_magic_missile_defense",
        ("packages/mcp/tests/test_magic_missile_mcp.py",),
        "runtime:mcp/magic-missile-targeting-darts-shield",
    ),
    CoreBoundary(
        "dnd5e.core.mcp.save_damage_atomicity",
        ("2014", "2024"),
        ("sagasmith_dnd_mcp.server.combat_save_damage|combat_engine.resolve_save_damage_to_sheets"),
        ("packages/mcp/tests/test_agent_save_damage_mcp.py",),
        "runtime:mcp/source-bound-save-and-damage",
    ),
    CoreBoundary(
        "dnd5e.core.mcp.duration_clock",
        ("2014", "2024"),
        "sagasmith_dnd_mcp.server.campaign_advance_effects",
        (
            "packages/mcp/tests/test_runtime_integrity_mcp.py",
            "packages/mcp/tests/test_campaign_clock_mcp.py",
            "packages/mcp/tests/test_stable_recovery_mcp.py",
            "packages/mcp/tests/test_spellbook_copy_mcp.py",
        ),
        "runtime:mcp/actor-and-world-duration-clock",
    ),
    CoreBoundary(
        "dnd5e.core.mcp.death_save_turn_cadence",
        ("2014",),
        "sagasmith_dnd_mcp.server.character_make_death_save|combat_check",
        ("packages/mcp/tests/test_noncombat_death_save_cadence_mcp.py",),
        "bundled:srd2014/06_Gameplay/Order_of_Combat.md#death-saving-throws",
    ),
    CoreBoundary(
        "dnd5e.core.mcp.combat_spell_boundary",
        ("2014", "2024"),
        "sagasmith_dnd_mcp.server.combat_cast_spell",
        ("packages/mcp/tests/test_runtime_integrity_mcp.py",),
        "runtime:mcp/spell-action-economy",
    ),
    CoreBoundary(
        "dnd5e.core.mcp.pending_ruling_atomicity",
        ("2014", "2024"),
        "sagasmith_dnd_mcp.server",
        ("packages/mcp/tests/test_runtime_integrity_mcp.py",),
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
    boundaries = tuple(
        CoreBoundary(
            id=item.id,
            editions=(normalized,),
            implementation=item.implementation,
            test_refs=item.evidence_for(normalized).test_refs,
            citation=item.evidence_for(normalized).citation,
            evidence=(item.evidence_for(normalized),),
        )
        for item in BOUNDARIES
        if normalized in item.editions
    )
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
                "evidence": [
                    {
                        "edition": evidence.edition,
                        "test_refs": evidence.test_refs,
                        "citation": evidence.citation,
                    }
                    for evidence in item.evidence
                ],
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
