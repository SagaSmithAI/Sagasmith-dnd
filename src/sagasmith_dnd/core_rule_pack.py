"""Built-in D&D core rule packs that wrap the currently verified engine behavior."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

CORE_RULE_PACK_VERSION = "1.9.0"


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
        "dnd5e.core.activity.resource_accounting",
        ("2014", "2024"),
        "activities.consume_activity",
        ("tests/test_activities.py",),
        "bundled:srd/limited-use-features",
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
        "dnd5e.core.armor_class.unarmored",
        ("2014", "2024"),
        "character_schema._derive_armor_class",
        ("tests/test_character_schema.py",),
        "bundled:srd/armor-class",
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
        "dnd5e.core.attack.cover",
        ("2014", "2024"),
        "combat_engine.preflight_attack",
        ("tests/test_combat_engine.py::test_half_cover_uses_the_rules_ac_bonus",),
        "bundled:srd/cover",
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
        ("tests/test_spells.py::test_magic_missile_allocation_and_shield_trigger_are_source_bound",),
        "bundled:srd/shield",
    ),
    CoreBoundary(
        "dnd5e.core.spell.magic_missile_darts",
        ("2014", "2024"),
        "spells.validate_magic_missile_allocations",
        ("tests/test_spells.py::test_magic_missile_allocation_and_shield_trigger_are_source_bound",),
        "bundled:srd/magic-missile",
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
        "dnd5e.core.rest.hit_dice",
        ("2014", "2024"),
        "lifecycle.apply_rest",
        ("tests/test_lifecycle.py",),
        "bundled:srd/resting",
    ),
    CoreBoundary(
        "dnd5e.core.rest.exhaustion",
        ("2014", "2024"),
        "lifecycle.apply_rest",
        ("tests/test_lifecycle.py",),
        "bundled:srd/exhaustion",
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
            "tests/test_spells.py::test_2024_ranger_replaces_one_prepared_spell_on_long_rest",
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
    normalized = str(edition or "").strip()
    if normalized not in {"2014", "2024"}:
        raise ValueError(f"unsupported D&D core edition: {normalized or '<empty>'}")
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
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return BuiltinCoreRulePack(
        id=payload["id"],
        version=CORE_RULE_PACK_VERSION,
        edition=normalized,
        fingerprint=fingerprint,
        boundaries=boundaries,
    )
