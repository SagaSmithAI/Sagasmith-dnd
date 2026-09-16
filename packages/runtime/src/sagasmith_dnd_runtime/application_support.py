"""MCP surface for the SagaSmith D&D runtime and bundled skill packs."""

from __future__ import annotations

import base64
import binascii
import hashlib
import importlib
import inspect
import json
import math
import os
import re
import secrets
import time
import unicodedata
from collections import Counter
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime
from functools import lru_cache, wraps
from pathlib import Path
from typing import Annotated, Any, Callable, Iterable, Literal, Mapping, TypeVar
from uuid import NAMESPACE_URL, uuid4, uuid5

from pydantic import Field, StrictBool
from sagasmith_core import (
    DOCUMENT_NORMALIZER_VERSION,
    AccessService,
    ActorKnowledgeService,
    ActorKnowledgeTransfer,
    AddonService,
    BranchService,
    CampaignService,
    CharacterService,
    CharacterStateUpdate,
    ContinuityCommitService,
    ContinuityService,
    EventService,
    IdempotencyService,
    IdempotencyWrite,
    ImportJobService,
    InitialActorGrant,
    MemoryService,
    ModuleService,
    OcrPageLayout,
    PdfTextLayoutProvider,
    RapidOcrProvider,
    RevisionService,
    RulePackService,
    RuleProfileService,
    RuleReceiptService,
    RuleService,
    SnapshotService,
    SubjectContextService,
    apply_document_page_revisions,
    default_local_principal,
    extract_pdf_page_text,
    file_sha256,
    normalize_document,
    normalized_document_page_text,
    ocr_layout_text,
    render_pdf_page,
    validate_subject_context_fact,
)
from sagasmith_core.access import (
    CAMPAIGN_DM_ROLES,
    LOCAL_SYSTEM_PRINCIPAL_ID,
    AccessDeniedError,
)
from sagasmith_core.characters import CharacterInfo
from sagasmith_core.context_anchors import normalize_context_entity_ref
from sagasmith_core.idempotency import request_hash
from sagasmith_core.integrity import canonical_json, json_sha256
from sagasmith_core.models import CampaignEvent
from sagasmith_core.modules import (
    EXACT_MODULE_SOURCE_FIELD_ORDER,
    EXACT_MODULE_SOURCE_FIELDS,
    MANAGED_MODULE_SOURCE_FIELDS,
    MarkdownModuleParser,
    canonical_heading_path,
    clean_source_evidence_text,
)
from sagasmith_core.modules import (
    normalize_source_evidence_text as _normalize_source_evidence_text,
)
from sagasmith_core.rule_packs import RulePackError, RulesetUnavailableError
from sagasmith_core.systems import SystemRegistry
from sagasmith_core.text import ascii_slug, compact_ascii_key
from sagasmith_core.visibility import (
    ACTOR_KNOWLEDGE_DISCLOSURE_SCOPES,
    PLAYER_MODULE_VISIBILITY_SCOPES,
    PLAYER_OWNED_ACTOR_DISCLOSURE_SCOPES,
)
from sagasmith_dnd import source_cards
from sagasmith_dnd.abilities import SKILL_ABILITIES
from sagasmith_dnd.ability_generation import (
    apply_ability_generation,
    apply_pending_rolled_ability_generation,
    begin_rolled_ability_generation,
    roll_ability_scores,
)
from sagasmith_dnd.activities import (
    ActivityError,
    consume_activity,
    recharge_activities_at_turn_start,
)
from sagasmith_dnd.activity_identity import is_multiattack_source_name
from sagasmith_dnd.actor_inventory_transfer import transfer_actor_inventory_item
from sagasmith_dnd.actor_types import (
    NON_PLAYER_CHARACTER_TYPES,
    require_agent_decidable_character_type,
)
from sagasmith_dnd.breathing import (
    advance_breathing_rounds,
    begin_holding_breath,
    restore_breathing,
)
from sagasmith_dnd.bundled_rules import (
    build_bundled_rule_sources,
    bundled_rule_corpus_inventory,
)
from sagasmith_dnd.campaign_state import (
    merge_reviewed_campaign_settings,
    merge_reviewed_campaign_state,
)
from sagasmith_dnd.character_import import inspect_character_document
from sagasmith_dnd.character_schema import (
    CHARACTER_SPELL_CARD_FIELDS,
    EBERRON_ARTIFICER_BATTLE_READY_FEATURE_ID,
    EBERRON_ARTIFICER_BATTLE_READY_PACK_ID,
    add_effect,
    add_inventory_item,
    adjust_wallet,
    attune_inventory_item,
    consume_weapon_ammunition,
    consume_weapon_limited_use,
    default_character_notes,
    default_character_sheet,
    equip_inventory_item,
    receive_inventory_item,
    remove_effect,
    remove_inventory_item,
    set_exhaustion_level,
    set_resource_value,
    set_spell_prepared,
    update_inventory_item,
    use_official_item_action,
    validate_character_notes,
    validate_character_sheet,
    validate_party_state,
    validate_world_effect,
)
from sagasmith_dnd.character_schema import (
    derive_character_sheet as derive_domain_character_sheet,
)
from sagasmith_dnd.chase_engine import (
    CHASE_BOUNDARY_IDS,
    CHASE_MANUAL_OUTCOME_STATUSES,
    ChaseManualOutcomeStatus,
    advance_chase_turn,
    current_chase_participant,
    end_chase,
    start_chase,
)
from sagasmith_dnd.combat_engine import (
    ABILITY_CHECK_KINDS,
    ACTOR_CHECK_KINDS,
    STEEL_DEFENDER_TURN_KIND,
    CombatEngineError,
    NeedsRulingError,
    active_hypnotic_pattern_effect_ids,
    add_choice_window,
    apply_attack_ac_bonus,
    apply_concentration_result,
    apply_damage_parts_to_sheet,
    apply_damage_to_sheet,
    apply_healing_to_sheet,
    apply_official_item_effect_to_encounter,
    apply_weapon_mastery_to_encounter,
    arm_readied_spell,
    available_actions,
    available_attack_defenses,
    available_reactions,
    can_see,
    charmed_social_check_advantage,
    consume_task_help,
    consume_weapon_mastery_attack_effects,
    current_combatant,
    damage_amount_after_reduction,
    death_save_due,
    emerge_tortle_shell_defense,
    end_concentration_for_incapacitating_conditions,
    end_hypnotic_pattern_effects,
    end_turn,
    enter_tortle_shell_defense,
    force_move_directly_away,
    newly_ended_witch_bolt_tethers,
    pay_activity_activation,
    pay_attack_action,
    pay_legendary_action,
    pay_official_item_activation,
    pay_witch_bolt_sustain_action,
    preflight_attack,
    preflight_spell_attack,
    queue_combatant,
    reconcile_dodge_lifecycle,
    reconcile_effect_dependencies,
    reconcile_readied_spells,
    reconcile_tortle_shell_defense_projection,
    reconcile_witch_bolt_concentration,
    reconcile_witch_bolt_range,
    record_death_save_turn_start,
    require_death_save_eligibility,
    require_harmful_targeting_allowed,
    resolve_actor_check,
    resolve_actor_contest,
    resolve_actor_group_check,
    resolve_attack_damage,
    resolve_choice_window,
    resolve_common_action,
    resolve_death_save_to_sheet,
    resolve_divine_spark_to_sheet,
    resolve_fall_to_sheet,
    resolve_hypnotic_pattern_target,
    resolve_lay_on_hands_to_sheets,
    resolve_preserve_life_to_sheets,
    resolve_readied_action_window,
    resolve_readied_spell_window,
    resolve_save_damage_to_sheets,
    resolve_second_wind_to_sheet,
    resolve_turn_undead_to_sheets,
    roll_attack_action,
    settle_core_activity_effect,
    settle_hide,
    source_speed_multiplier,
    source_spell_resolution,
    spend_movement,
    stabilize_sheet,
    stand_up,
    standard_save_damage_reduction,
    start_encounter,
    start_witch_bolt_tether,
    timed_condition_sources,
    trigger_readied_action,
    trigger_readied_spell,
)
from sagasmith_dnd.conditions import (
    DEATH_SAVE_SETTLED_CONDITIONS,
    INCAPACITATING_STATE_IDS,
    STANDARD_BINARY_CONDITION_IDS,
    apply_condition_change,
    condition_ids,
    reconcile_ended_effect_conditions,
)
from sagasmith_dnd.consumables import HEALING_POTION_MECHANIC_ID, healing_potion_formula
from sagasmith_dnd.content_actors import (
    SRD2014_PRESET_PACK_ID,
    SRD2014_PRESET_PACK_VERSION,
    SRD2024_PRESET_PACK_ID,
    SRD2024_PRESET_PACK_VERSION,
    build_dnd_content_actor,
    build_srd2014_preset_actors,
    build_srd2024_preset_actors,
    validate_dnd_content_actor,
)
from sagasmith_dnd.content_import import (
    artifact_with_direct_resolution,
    audit_release_semantic_validation,
    candidate_draft_issues,
    compiled_artifacts_from_candidates,
    extract_content_inventory,
    module_statblock_review_candidates,
    normalize_2014_statblock_candidate,
    validate_selection_ready_artifacts,
)
from sagasmith_dnd.content_packages import (
    build_module_content_package,
    build_preset_content_package,
    build_rule_content_package,
    content_actor_catalog_definition,
    content_definition_checksum,
    validate_dnd_content_package,
    validate_module_pack_decisions,
)
from sagasmith_dnd.content_solution import (
    ContentSolutionError,
    build_content_solution,
)
from sagasmith_dnd.content_validation import (
    build_catalog_review,
    build_selection_contract,
    catalog_review_errors,
    content_fingerprint,
    selection_contract_errors,
    selection_input_errors,
    selection_schema_for_artifact,
)
from sagasmith_dnd.core_content import PACK_ID as CORE_CONTENT_PACK_ID
from sagasmith_dnd.core_content import PACK_VERSION as CORE_CONTENT_PACK_VERSION
from sagasmith_dnd.core_content import build_srd2014_content
from sagasmith_dnd.core_content_2024 import PACK_ID as CORE_2024_CONTENT_PACK_ID
from sagasmith_dnd.core_content_2024 import (
    PACK_VERSION as CORE_2024_CONTENT_PACK_VERSION,
)
from sagasmith_dnd.core_content_2024 import build_srd2024_content
from sagasmith_dnd.core_rule_pack import get_core_rule_pack
from sagasmith_dnd.dependent_actor_lifecycle import dependent_actor_lifecycle_policy
from sagasmith_dnd.dependent_actor_refresh import (
    STEEL_DEFENDER_RELATION_KEY,
    STEEL_DEFENDER_REVIEWED_EXPRESSION_HASH,
    materialize_dependent_actor_owner_scaling,
    refresh_dependent_actor_sheet,
)
from sagasmith_dnd.dependent_actor_relations import validate_dependent_actor_relations
from sagasmith_dnd.document_layout import DND5E_DOCUMENT_LAYOUT_PROFILE
from sagasmith_dnd.editions import DEFAULT_CAMPAIGN_EDITION, normalize_dnd_edition
from sagasmith_dnd.engine import resolve_check, roll
from sagasmith_dnd.external_custody import validate_external_inventory_custody
from sagasmith_dnd.game_time import (
    FIXED_GAME_TIME_PERIODS,
    NARRATIVE_GAME_TIME_PERIODS,
    TICKS_PER_MINUTE,
    advance_game_time,
    anchor_world_time,
    calendar_minute_point,
    game_time_ticks,
    rules_day_from_ticks,
)
from sagasmith_dnd.ground_transfer import drop_held_items, pickup_ground_item
from sagasmith_dnd.held_items import held_item_roots
from sagasmith_dnd.heroic_inspiration import reroll_recorded_d20_result
from sagasmith_dnd.item_attunement_ownership import complete_item_attunement_ownership
from sagasmith_dnd.lifecycle import (
    LONG_REST_MINIMUM_MINUTES,
    advance_effect_durations,
    advance_elapsed_effect_durations,
    advance_elapsed_world_effect_durations,
    advance_source_turn_effect_durations,
    advance_world_effect_durations,
    allows_trance_rest,
    apply_raise_dead_to_sheet,
    apply_rest,
    apply_short_rest_hit_die_choice,
    expire_combat_bound_effects,
    initialize_source_state,
    knock_prone_outside_combat,
    minimum_rest_minutes,
    record_rest_completion,
    recover_stable_creature,
    stand_outside_combat,
    validate_arcane_recovery_choice,
    validate_initial_rest_hit_dice_requests,
    validate_natural_recovery_choice,
    validate_rest_activity_minutes,
    validate_rest_eligibility,
    validate_rest_schedule,
    validate_song_of_rest_source,
    validate_sorcerous_restoration_choice,
)
from sagasmith_dnd.module_profile import DndModuleProfile
from sagasmith_dnd.official_expansions import (
    installed_official_definition_matches,
    matching_official_expansion_dependency_rebinds,
    official_expansion_catalog,
    official_expansion_dependency_rebinds,
    official_expansion_support_catalog,
    resolve_official_expansion_archives,
    resolve_official_expansion_support_archives,
)
from sagasmith_dnd.official_item_materialization import (
    ARCANE_PROPULSION_ARM_ID,
    ARMBLADE_ID,
    DYRRN_TENTACLE_WHIP_ID,
    EBERRON_ITEM_PACK_ID,
    is_bound_official_item_id,
    materialize_official_item_template,
    materialized_item_binding_hash,
    official_item_profile,
    reviewed_official_item_hash,
)
from sagasmith_dnd.playthrough import (
    playthrough_source_bindings,
    validate_playthrough_manifest,
    validate_playthrough_transition,
    validate_source_defined_ending_condition,
)
from sagasmith_dnd.progression import (
    advance_single_class_level,
    apply_constitution_score_hit_point_change,
    apply_per_level_hit_point_bonus,
    award_experience,
    experience_status,
    initialize_base_class,
    profile_spell_selection_status,
    synchronize_class_feature_resources,
)
from sagasmith_dnd.random_stream import (
    CampaignRandomStream,
    active_random_stream,
    initial_random_stream,
    use_random_stream,
    validate_random_stream_state,
)
from sagasmith_dnd.resolution_plan import (
    BoundResolutionPlan,
    ResolutionPlanBindingError,
    ResolutionPlanCompilationError,
    ResolutionPlanExecutionError,
    bind_resolution_plan,
    compile_resolution_plan,
    execute_resolution_plan,
    require_resolution_plan_trigger,
    resolution_plan_contract,
    resolution_plan_template,
)
from sagasmith_dnd.resources import mutate_bounded_resource
from sagasmith_dnd.retrieval import DND5E_QUERY_HINTS
from sagasmith_dnd.rule_engine import (
    AGENT_RULING_KIND_ORDER,
    EXTERNAL_RULING_KIND_ORDER,
    EXTERNAL_RULING_KINDS,
    PENDING_RULE_RESULT_STATUSES,
    RULING_KINDS,
    ResolutionContext,
    RuleCompilationError,
    RuleEventRulingRequiredError,
    apply_rule_event,
    compile_mechanics,
    context_with_facts,
    core_receipts,
    nested_ruling_kind,
    resolution_context,
    run_mechanic_tests,
    validate_source_bound_mechanics,
)
from sagasmith_dnd.rule_providers import load_native_rule_providers
from sagasmith_dnd.save_context import validated_save_source_facts
from sagasmith_dnd.sleep import resolve_sleep_targets, wake_sleep_effects
from sagasmith_dnd.spatial import (
    BattleMapError,
    compile_battle_map,
    compile_battle_map_template,
    normalize_combat_grid_source_refs,
    normalize_combat_grid_template,
    normalize_combat_grid_templates,
    normalize_party_public_map_asset,
    patch_battle_map,
    validate_position,
)
from sagasmith_dnd.spell_resolution import (
    SPELL_RESOLUTION_MECHANIC_ID,
    audit_spell_resolution_paths,
    effective_spell_resolution,
    scaled_roll_expression,
    spell_attack_count,
)
from sagasmith_dnd.spells import (
    CORE_MAGIC_ITEM_LAST_CHARGE_MECHANIC_ID,
    CORE_MAGIC_ITEM_RECHARGE_MECHANIC_ID,
    SLOT_PAYMENT_ECONOMIES,
    apply_core_fly_effects,
    apply_core_invisibility_effects,
    available_shield_attack_defenses,
    available_shield_magic_missile_defenses,
    consume_magic_item_spell_cast,
    consume_readied_spell,
    consume_shield_reaction,
    consume_spell_cast,
    end_concentration_effects,
    end_tether_concentrations,
    fly_target_limit,
    invisibility_target_limit,
    is_core_fly_spell,
    is_core_hypnotic_pattern_spell,
    is_core_invisibility_spell,
    is_core_magic_missile_spell,
    is_core_witch_bolt_spell,
    magic_item_spell_card,
    recharge_magic_item_charges,
    reconcile_source_effect_dependencies,
    replace_prepared_spells,
    resolve_magic_item_last_charge,
    validate_magic_missile_allocations,
    validate_spell_grant,
)
from sagasmith_dnd.standard_content import (
    CORE_DRAGONBORN_BREATH_MECHANIC_ID,
    build_standard2014_content,
)
from sagasmith_dnd.standard_feature_ids import (
    CORE_DWARVEN_RESILIENCE_MECHANIC_ID,
    CORE_FEY_ANCESTRY_MECHANIC_ID,
    CORE_GNOME_CUNNING_MECHANIC_ID,
    CORE_HALFLING_BRAVE_MECHANIC_ID,
    CORE_ORC_AGGRESSIVE_MECHANIC_ID,
    CORE_RELENTLESS_ENDURANCE_MECHANIC_ID,
    CORE_TORTLE_NATURAL_ARMOR_MECHANIC_ID,
    CORE_TORTLE_SHELL_DEFENSE_MECHANIC_ID,
    CORE_UNCANNY_DODGE_MECHANIC_ID,
    CORE_WATCHERS_EYE_MECHANIC_ID,
    TORTLE_NATURAL_ARMOR_ARTIFACT_ID,
    TORTLE_NATURAL_ARMOR_AUTHORITY_KEY,
    TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_CHECKSUM,
    TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
    TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
    TORTLE_NATURAL_ARMOR_LEGACY_PACK_ID,
    TORTLE_NATURAL_ARMOR_LEGACY_PACK_VERSIONS,
    TORTLE_NATURAL_ARMOR_SOURCE_RULE_REF_PREFIX,
)
from sagasmith_dnd.standard_spell_ids import (
    CORE_BLADE_WARD_MECHANIC_ID,
    CORE_FLY_MECHANIC_ID,
    CORE_FLY_SPELL_IDS,
    CORE_HYPNOTIC_PATTERN_MECHANIC_ID,
    CORE_HYPNOTIC_PATTERN_SPELL_IDS,
    CORE_INVISIBILITY_MECHANIC_ID,
    CORE_INVISIBILITY_SPELL_IDS,
    CORE_MENDING_MECHANIC_ID,
    CORE_MENDING_SPELL_ID,
    CORE_SLEEP_MECHANIC_ID,
    CORE_SLEEP_SPELL_ID,
    CORE_WITCH_BOLT_MECHANIC_ID,
    STANDARD_2014_CONTENT_PACK_ID,
    STANDARD_2014_CONTENT_PACK_VERSION,
)
from sagasmith_dnd.starting_equipment import (
    apply_starting_equipment,
    normalize_starting_equipment_contract,
    normalize_starting_equipment_selection,
)
from sagasmith_dnd.statblock_ocr import recover_2014_pdf_statblock_layout
from sagasmith_dnd.statblock_spells import (
    hydrate_statblock_spellcasting as hydrate_dnd_statblock_spellcasting,
)
from sagasmith_dnd.statblocks import (
    OCR_STATBLOCK_RECOVERY_VERSION,
    StatblockImportError,
    apply_dependent_actor_template_variant,
    apply_reviewed_statblock_fill,
    apply_statblock_variant,
    dependent_actor_owner_binding,
    dependent_actor_template_solution_errors,
    discover_2014_statblock_names_from_layout,
    discover_2014_statblock_slots_from_layout,
    effective_statblock_rating,
    finalize_imported_actor_rulings,
    is_2014_statblock_identity_line,
    legendary_action_spec,
    materialize_parameterized_statblock_source,
    parameterized_statblock_requirements,
    parse_2014_statblock,
    parse_2014_statblock_template_preview,
    parse_2024_statblock,
    recover_2014_statblock_from_ocr,
)
from sagasmith_dnd.steel_defender import (
    STEEL_DEFENDER_DEFLECT_ATTACK_MECHANIC_ID,
    SteelDefenderError,
    apply_deflect_attack_to_plan,
    begin_steel_defender_revival,
    bind_steel_defender_runtime_mechanics,
    complete_steel_defender_revival,
    consume_deflect_attack_reaction,
    kill_steel_defender_when_owner_dies,
    mending_steel_defender,
    repair_steel_defender,
    validate_deflect_attack_eligibility,
)
from sagasmith_dnd.system import DND5E
from sagasmith_dnd.vocabulary import (
    ADVANCEMENT_MODES,
    COMBAT_OUTCOME_STATUSES,
    DAMAGE_TYPES,
    DENOMINATION_CP_VALUES,
    DENOMINATIONS,
    INVENTORY_OWNER_SCOPES,
    PLAYER_GAMEPLAY_VISIBILITY_SCOPES,
    REST_TYPES,
    WEAPON_HAND_SLOTS,
)
from sqlalchemy.exc import NoResultFound

from sagasmith_dnd_runtime.actor_inventory_lifecycle import InventoryActorLifecycleService
from sagasmith_dnd_runtime.actor_memory import select_actor_memory_context
from sagasmith_dnd_runtime.bounded_evaluations import (
    BOUNDED_EVALUATION_PURPOSES,
    BOUNDED_EVALUATION_SCHEMA_VERSION,
    BOUNDED_OUTPUT_CONTRACTS,
    normalize_bounded_proposal,
    validate_bounded_proposal_refs,
)
from sagasmith_dnd_runtime.build_identity import (
    implementation_identity,
    require_compatible_build,
)
from sagasmith_dnd_runtime.config import McpConfig
from sagasmith_dnd_runtime.exposure import Exposure, ExposureError, ExposureRegistry
from sagasmith_dnd_runtime.npc_conversations import (
    ACTIVE_CONVERSATION_STATUSES,
    NPC_CONVERSATION_CONTRACT,
    NPC_CONVERSATION_SCHEMA_VERSION,
    ConversationStore,
    normalize_audience_facts,
)
from sagasmith_dnd_runtime.npc_turns import (
    NPC_NARRATIVE_ACTION_KINDS,
    NPC_TURN_BUNDLE_SCHEMA_VERSION,
    NPC_TURN_PURPOSES,
    NPC_TURN_SCHEMA_VERSION,
    accepted_proposal_deltas,
    normalize_npc_stimulus,
    normalize_npc_turn_proposal,
    validate_npc_basis_refs,
    validate_npc_targets,
)
from sagasmith_dnd_runtime.operations import (
    DndRuntime,
    Image,
    RenderResult,
)
from sagasmith_dnd_runtime.operations import (
    OperationError as ToolError,
)
from sagasmith_dnd_runtime.operations import (
    OperationHints as ToolAnnotations,
)
from sagasmith_dnd_runtime.random_state import (
    RandomStateMutationService as StateMutationService,
)
from sagasmith_dnd_runtime.random_state import (
    bind_idempotency_request,
)
from sagasmith_dnd_runtime.receipt_signing import sign_receipt, verify_receipt_signature
from sagasmith_dnd_runtime.skills import SkillCatalog
from sagasmith_dnd_runtime.storage import SagaSmithStorage
from sagasmith_dnd_runtime.tool_profiles import (
    CORE_TOOLS,
    PROFILE_COMBAT,
    PROFILE_LOBBY,
    PROFILE_PLAY,
    campaign_phase,
    policy_for_tool,
    tool_catalog,
    tools_for_phase,
    validate_profile_coverage,
)


def _strict_boolean(value: Any, field: str) -> bool:
    """Reject truthy/falsy stand-ins at every public rules boundary."""

    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def _render_combat_png(*args: Any, **kwargs: Any) -> tuple[dict[str, Any], bytes]:
    """Load Pillow-backed combat presentation only when that tool is called."""

    try:
        from sagasmith_dnd_runtime.combat_render import render_combat_png
    except ModuleNotFoundError as exc:
        if (exc.name or "").partition(".")[0] != "PIL":
            raise
        raise RuntimeError(
            'Combat image rendering requires `pip install "sagasmith-dnd-mcp[images]"`'
        ) from exc
    return render_combat_png(*args, **kwargs)


def _image_properties(path: str | Path) -> tuple[int, int, str]:
    """Inspect reviewed map dimensions and MIME without a startup dependency."""

    try:
        from PIL import Image as PillowImage
    except ModuleNotFoundError as exc:
        if (exc.name or "").partition(".")[0] != "PIL":
            raise
        raise RuntimeError(
            'Party-public map review requires `pip install "sagasmith-dnd-mcp[images]"`'
        ) from exc
    try:
        with PillowImage.open(path) as image:
            width, height = int(image.width), int(image.height)
            media_type = {
                "JPEG": "image/jpeg",
                "PNG": "image/png",
                "WEBP": "image/webp",
            }.get(str(image.format or ""))
            if media_type is None:
                raise ValueError("unsupported party-public raster image format")
            image.verify()
            return width, height, media_type
    except (PillowImage.DecompressionBombError, OSError, ValueError) as exc:
        raise ValueError("party_public_map_asset must reference a valid raster image") from exc


def _preload_optional_pdf_runtime() -> None:
    """Warm an installed PDF runtime without making it a text-host dependency."""

    try:
        importlib.import_module("pypdfium2")
    except ModuleNotFoundError as exc:
        if exc.name != "pypdfium2":
            raise


@lru_cache(maxsize=8)
def _render_immutable_pdf_page_cached(
    source_path: str,
    page_number: int,
    scale: float,
    source_checksum: str,
    modified_ns: int,
    size: int,
) -> Any:
    """Render one immutable evidence page once for adjacent card reviews."""

    del modified_ns, size
    rendered = render_pdf_page(Path(source_path), page_number, scale=scale)
    if rendered.source_checksum != source_checksum:
        raise RuntimeError("rulebook PDF no longer matches its staged checksum")
    return rendered


def _render_immutable_pdf_page(
    source_path: Path,
    page_number: int,
    *,
    scale: float,
    source_checksum: str,
) -> Any:
    stat = source_path.stat()
    return _render_immutable_pdf_page_cached(
        str(source_path.resolve()),
        page_number,
        scale,
        source_checksum,
        stat.st_mtime_ns,
        stat.st_size,
    )


def _intrinsic_attack_provenance(sheet: Mapping[str, Any] | None) -> Any:
    """Return authoritative anatomy attack projections from one actor card."""

    value = dict(sheet or {})
    traits = value.get("traits")
    if not isinstance(traits, Mapping):
        return None
    projection = traits.get("intrinsic_attacks", [])
    return [] if projection is None else deepcopy(projection)


def _reject_new_intrinsic_attack_provenance(sheet: Mapping[str, Any] | None) -> None:
    projection = _intrinsic_attack_provenance(sheet)
    if projection not in (None, []):
        raise ValueError(
            "intrinsic attack provenance can be created only by character_content_apply"
        )


def _battle_ready_provenance(sheet: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Return official Battle Ready cards whose creation is server-authorized."""

    content = dict(dict(sheet or {}).get("content") or {})
    return [
        deepcopy(item)
        for item in content.get("features", [])
        if isinstance(item, Mapping)
        and item.get("id") == EBERRON_ARTIFICER_BATTLE_READY_FEATURE_ID
        and item.get("pack_id") == EBERRON_ARTIFICER_BATTLE_READY_PACK_ID
    ]


def _reject_new_battle_ready_provenance(sheet: Mapping[str, Any] | None) -> None:
    if _battle_ready_provenance(sheet):
        raise ValueError(
            "Battle Ready provenance can be created only by source content application"
        )


def _require_preserved_battle_ready_provenance(
    current: Mapping[str, Any], replacement: Mapping[str, Any]
) -> None:
    if _battle_ready_provenance(current) != _battle_ready_provenance(replacement):
        raise ValueError(
            "character mutation cannot add, remove, or alter authoritative Battle Ready provenance"
        )


def _official_item_provenance(sheet: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Return bound official item selections with their immutable item binding."""

    value = dict(sheet or {})
    content = dict(value.get("content") or {})
    items = {
        str(item.get("id") or ""): item
        for item in dict(value.get("inventory") or {}).get("items", [])
        if isinstance(item, Mapping)
    }
    result: list[dict[str, Any]] = []
    for selection in content.get("selections", []):
        if not isinstance(selection, Mapping):
            continue
        artifact_id = str(selection.get("artifact_id") or "")
        if selection.get("kind") != "item" or not is_bound_official_item_id(
            str(selection.get("pack_id") or ""), artifact_id
        ):
            continue
        recorded = dict(selection.get("selection") or {})
        item_id = str(recorded.get("inventory_item_id") or "")
        item = items.get(item_id)
        contract = (
            dict(dict(item.get("mechanics") or {}).get("official_item") or {}) if item else {}
        )
        qualification: dict[str, Any] = {}
        if contract.get("kind") == "armblade":
            qualification["species"] = str(
                dict(value.get("progression") or {}).get("species") or ""
            )
        elif contract.get("kind") == "arcane_propulsion_arm":
            anatomy = dict(dict(value.get("traits") or {}).get("anatomy") or {})
            qualification = {
                "functional_arms": int(anatomy.get("functional_arms", 2) or 0),
                "functional_hands": int(anatomy.get("functional_hands", 2) or 0),
            }
        result.append(
            {
                "selection": deepcopy(dict(selection)),
                "item_id": item_id,
                "official_item_state": str(
                    dict(dict(item.get("mechanics") or {}).get("official_item") or {}).get("state")
                    or ""
                )
                if item is not None
                else "",
                "attunement": str(item.get("attunement") or "") if item is not None else "",
                "qualification": qualification,
                "materialized_item_hash": (
                    materialized_item_binding_hash(item) if item is not None else ""
                ),
            }
        )
    return result


def _reject_new_official_item_provenance(sheet: Mapping[str, Any] | None) -> None:
    if _official_item_provenance(sheet):
        raise ValueError("official item provenance can be created only by character_content_apply")


def _require_preserved_official_item_provenance(
    current: Mapping[str, Any], replacement: Mapping[str, Any]
) -> None:
    before = _official_item_provenance(current)
    after = _official_item_provenance(replacement)
    if before != after:
        raise ValueError(
            "character mutation cannot add, remove, or alter authoritative official item provenance"
        )


def _require_preserved_intrinsic_attack_provenance(
    current: Mapping[str, Any], replacement: Mapping[str, Any]
) -> None:
    if _intrinsic_attack_provenance(current) != _intrinsic_attack_provenance(replacement):
        raise ValueError(
            "character mutation cannot add, remove, or alter authoritative "
            "intrinsic attack provenance"
        )


def _tortle_natural_armor_provenance(sheet: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return caller-writable records that can authorize the Tortle AC exception."""

    value = dict(sheet or {})
    content = dict(value.get("content") or {})

    def privileged_selection(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        refs = item.get("rule_refs")
        return (
            item.get("artifact_id") == TORTLE_NATURAL_ARMOR_ARTIFACT_ID
            or item.get("pack_id") == TORTLE_NATURAL_ARMOR_LEGACY_PACK_ID
            or (
                isinstance(refs, list)
                and any(
                    isinstance(ref, str)
                    and ref.startswith(TORTLE_NATURAL_ARMOR_SOURCE_RULE_REF_PREFIX)
                    for ref in refs
                )
            )
        )

    def privileged_feature(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        mechanic_refs = item.get("mechanic_refs")
        trait = dict(dict(item.get("choices") or {}).get("source_trait") or {})
        return (
            (
                isinstance(mechanic_refs, list)
                and CORE_TORTLE_NATURAL_ARMOR_MECHANIC_ID in mechanic_refs
            )
            or trait.get("kind") == "tortle_natural_armor"
            or (trait.get("effect_source") == TORTLE_NATURAL_ARMOR_ARTIFACT_ID)
        )

    def privileged_effect(item: Any) -> bool:
        return isinstance(item, dict) and item.get("source") == TORTLE_NATURAL_ARMOR_ARTIFACT_ID

    selections = [
        deepcopy(item) for item in content.get("selections", []) if privileged_selection(item)
    ]
    features = [deepcopy(item) for item in content.get("features", []) if privileged_feature(item)]
    effects = [deepcopy(item) for item in value.get("effects", []) if privileged_effect(item)]
    if not selections and not features and not effects:
        return {}
    return {
        "species": str(dict(value.get("progression") or {}).get("species") or ""),
        "selections": selections,
        "features": features,
        "effects": effects,
    }


def _reserved_official_definition_owners() -> dict[str, str]:
    owners = {
        str(item["definition_id"]): str(item["package_id"])
        for item in official_expansion_dependency_rebinds()
    }
    for support in official_expansion_support_catalog():
        for definition in support.get("provided_rule_definitions", []):
            if isinstance(definition, dict):
                owners[str(definition.get("id") or "")] = str(support["id"])
    return owners


def _validate_unreserved_rule_definition_identity(pack_id: str) -> None:
    owner = _reserved_official_definition_owners().get(str(pack_id))
    if owner is not None:
        raise ValueError(f"rule definition {pack_id} is reserved for official package {owner}")


def _validate_reserved_official_artifact_identities(
    *,
    definition_id: str,
    artifacts: Iterable[Mapping[str, Any]],
    package_id: str | None = None,
) -> None:
    owners = {
        TORTLE_NATURAL_ARMOR_ARTIFACT_ID: (
            TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
            TORTLE_NATURAL_ARMOR_LEGACY_PACK_ID,
        )
    }
    for artifact in artifacts:
        nested = artifact.get("artifact")
        artifact_ids = {
            str(artifact.get("id") or ""),
            str(nested.get("id") or "") if isinstance(nested, dict) else "",
        }
        for artifact_id in artifact_ids:
            owner = owners.get(artifact_id)
            if owner is None:
                continue
            owner_package_id, owner_definition_id = owner
            if definition_id != owner_definition_id or (
                package_id is not None and package_id != owner_package_id
            ):
                raise ValueError(
                    f"content artifact {artifact_id} is reserved for official package "
                    f"{owner_package_id} definition {owner_definition_id}"
                )


def _validate_reserved_official_package_identity(package: Mapping[str, Any]) -> None:
    """Reject archives that occupy a locked official package or definition identity."""

    package_id = str(package.get("id") or "")
    package_version = str(package.get("version") or "")
    package_checksum = str(package.get("checksum") or "")
    locked_packages = {
        str(item["id"]): dict(item)
        for item in (*official_expansion_catalog(), *official_expansion_support_catalog())
    }
    definition_owners = _reserved_official_definition_owners()
    locked = locked_packages.get(package_id)
    if locked is not None and (
        package_version != str(locked["version"]) or package_checksum != str(locked["checksum"])
    ):
        raise ValueError(
            "content package occupies a reserved official identity without its locked checksum"
        )
    definitions = list(dict(package.get("content") or {}).get("rule_definitions") or [])
    for definition in definitions:
        if not isinstance(definition, dict):
            continue
        definition_id = str(definition.get("id") or "")
        owner = definition_owners.get(definition_id)
        if owner is not None and owner != package_id:
            raise ValueError(
                f"rule definition {definition_id} is reserved for official package {owner}"
            )
    artifacts = list(dict(package.get("content") or {}).get("artifacts") or [])
    for definition in definitions:
        if not isinstance(definition, dict):
            continue
        definition_id = str(definition.get("id") or "")
        _validate_reserved_official_artifact_identities(
            definition_id=definition_id,
            package_id=package_id,
            artifacts=(
                artifact
                for artifact in artifacts
                if isinstance(artifact, dict)
                and str(artifact.get("rule_definition_id") or "") == definition_id
            ),
        )


def _verified_tortle_natural_armor_authority(
    *,
    pack_id: str,
    pack_version: str,
    artifact_id: str,
    provenance: Mapping[str, Any],
    archive_definition_verified: bool = False,
) -> dict[str, str] | None:
    """Bind the privileged armor exception to the immutable official outer archive."""

    if artifact_id != TORTLE_NATURAL_ARMOR_ARTIFACT_ID:
        return None
    locked = next(
        (
            dict(item)
            for item in official_expansion_catalog("2014")
            if item["id"] == TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID
        ),
        None,
    )
    content_definition = dict(provenance.get("content_definition") or {})
    authority = {
        "package_id": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
        "package_version": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
        "package_checksum": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_CHECKSUM,
    }
    valid = (
        locked is not None
        and str(locked.get("version") or "") == authority["package_version"]
        and str(locked.get("checksum") or "") == authority["package_checksum"]
        and pack_id == TORTLE_NATURAL_ARMOR_LEGACY_PACK_ID
        and pack_version in TORTLE_NATURAL_ARMOR_LEGACY_PACK_VERSIONS
        and str(content_definition.get("package_id") or "") == authority["package_id"]
        and str(content_definition.get("package_version") or "") == authority["package_version"]
        and str(content_definition.get("package_checksum") or "") == authority["package_checksum"]
        and archive_definition_verified
    )
    if not valid:
        raise RulesetUnavailableError(
            "Tortle Natural Armor requires the immutable official expansion archive"
        )
    return authority


def _verified_content_authority_ids(
    sheet: Mapping[str, Any], *, character_id: str | None, secret: bytes
) -> frozenset[str]:
    """Verify actor-bound server capabilities embedded in official content records."""

    if not character_id:
        return frozenset()
    verified: set[str] = set()
    for item in dict(sheet.get("content") or {}).get("selections", []):
        if not isinstance(item, dict) or item.get("artifact_id") != (
            TORTLE_NATURAL_ARMOR_ARTIFACT_ID
        ):
            continue
        raw_authority = dict(item.get("selection") or {}).get(TORTLE_NATURAL_ARMOR_AUTHORITY_KEY)
        if not isinstance(raw_authority, Mapping):
            continue
        authority = dict(raw_authority)
        if not isinstance(authority.get("authorization"), Mapping):
            continue
        authority_id = str(authority.get("authority_id") or "")
        try:
            payload = verify_receipt_signature(
                authority.get("authorization"),
                secret,
                missing_error="content authority signature is missing",
                invalid_error="content authority signature is invalid",
            )
        except ValueError:
            continue
        if payload == {
            "schema_version": 1,
            "purpose": "official_content_authority",
            "character_id": character_id,
            "artifact_id": TORTLE_NATURAL_ARMOR_ARTIFACT_ID,
            "package_id": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
            "package_version": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
            "package_checksum": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_CHECKSUM,
            "authority_id": authority_id,
        }:
            verified.add(authority_id)
    return frozenset(verified)


def _reject_new_tortle_natural_armor_provenance(sheet: Mapping[str, Any] | None) -> None:
    if _tortle_natural_armor_provenance(sheet):
        raise ValueError(
            "Tortle Natural Armor provenance can be created only by character_content_apply"
        )


def _require_preserved_tortle_natural_armor_provenance(
    current: Mapping[str, Any], replacement: Mapping[str, Any]
) -> None:
    if _tortle_natural_armor_provenance(current) != _tortle_natural_armor_provenance(replacement):
        raise ValueError(
            "character sheet replacement cannot add, remove, or alter authoritative "
            "Tortle Natural Armor provenance"
        )


def _active_scag_bladesong_effects(value: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        deepcopy(effect)
        for effect in value.get("effects", [])
        if isinstance(effect, dict)
        and effect.get("active")
        and dict(effect.get("metadata") or {}).get("scag_bladesong") is True
    ]


def _reject_new_scag_bladesong_state(sheet: Mapping[str, Any] | None) -> None:
    if _active_scag_bladesong_effects(sheet or {}):
        raise ValueError(
            "active SCAG Bladesong can be created only by the source-bound combat activity"
        )


def _require_preserved_scag_bladesong_state(
    current: Mapping[str, Any], replacement: Mapping[str, Any]
) -> None:
    """Prevent sheet ingress from manufacturing or erasing Bladesong."""
    before = _active_scag_bladesong_effects(current)
    after = _active_scag_bladesong_effects(replacement)
    if bool(before) != bool(after) or (before and before != after):
        raise ValueError(
            "character sheet replacement cannot add, remove, or alter active SCAG Bladesong"
        )


def _character_spell_card(catalog_card: dict[str, Any]) -> dict[str, Any]:
    """Project a rule-catalog spell into the persistable character-card schema."""
    return {
        key: deepcopy(value)
        for key, value in catalog_card.items()
        if key in CHARACTER_SPELL_CARD_FIELDS
    }


SECOND_WIND_ACTIVITY_IDS = frozenset(
    {
        "dnd5e.content.srd2014.feature.fighter-second-wind",
        "dnd5e.content.srd2024.feature.fighter-second-wind",
    }
)
ENGINE_OWNED_STANDARD_ACTIVITY_IDS = frozenset(
    {
        "dnd5e.content.srd2014.feature.cleric-channel-divinity",
        "dnd5e.content.srd2014.feature.fighter-action-surge",
        "dnd5e.content.srd2014.feature.fighter-second-wind",
        "dnd5e.content.srd2014.feature.life-domain-channel-divinity-preserve-life",
        "dnd5e.content.srd2014.feature.rogue-cunning-action",
        "dnd5e.content.srd2024.feature.cleric-channel-divinity",
        "dnd5e.content.srd2024.feature.fighter-action-surge",
        "dnd5e.content.srd2024.feature.fighter-second-wind",
        "dnd5e.content.srd2024.feature.life-domain-preserve-life",
        "dnd5e.content.srd2024.feature.rogue-cunning-action",
        "dnd5e.addon.rulebook.d-d-5e-sword-coast-adventurer-s-guide.16e6a243ef0a.feature.bladesong",
    }
)

COMBAT_MUTATION_LOCK_ID = "dnd5e:combat"
COMBAT_MUTATION_LOCK = {
    "id": COMBAT_MUTATION_LOCK_ID,
    "domains": ["rule_profile", "rule_pack_activation", "addon_activation"],
    "reason": "active D&D combat",
}


def _with_combat_mutation_lock(
    state: dict[str, Any],
    *,
    active: bool,
) -> dict[str, Any]:
    """Add or remove the authoritative ruleset lock for the combat lifecycle."""

    value = deepcopy(state)
    raw_locks = value.get("mutation_locks", [])
    if not isinstance(raw_locks, list) or any(not isinstance(item, dict) for item in raw_locks):
        raise ValueError("campaign state mutation_locks must be an array of objects")
    locks = [
        deepcopy(item) for item in raw_locks if str(item.get("id") or "") != COMBAT_MUTATION_LOCK_ID
    ]
    if active:
        locks.append(deepcopy(COMBAT_MUTATION_LOCK))
    if locks:
        value["mutation_locks"] = locks
    else:
        value.pop("mutation_locks", None)
    return value


ENGINE_SETTLED_CARD_MECHANIC_IDS = frozenset(
    {
        "dnd5e.core.action.multiattack_choice",
        "dnd5e.core.activity.action_surge",
        "dnd5e.core.activity.cunning_action",
        CORE_ORC_AGGRESSIVE_MECHANIC_ID,
        "dnd5e.core.activity.divine_spark",
        CORE_DRAGONBORN_BREATH_MECHANIC_ID,
        "dnd5e.core.activity.legendary_action",
        "dnd5e.core.activity.preserve_life",
        "dnd5e.core.activity.second_wind",
        "dnd5e.core.activity.turn_undead",
        "dnd5e.core.item.healing_potion",
        CORE_RELENTLESS_ENDURANCE_MECHANIC_ID,
        CORE_WATCHERS_EYE_MECHANIC_ID,
        CORE_DWARVEN_RESILIENCE_MECHANIC_ID,
        CORE_FEY_ANCESTRY_MECHANIC_ID,
        CORE_GNOME_CUNNING_MECHANIC_ID,
        CORE_HALFLING_BRAVE_MECHANIC_ID,
        "dnd5e.core.magic_ammunition.slaying",
        CORE_BLADE_WARD_MECHANIC_ID,
        CORE_FLY_MECHANIC_ID,
        "dnd5e.core.spell.mage_armor",
        "dnd5e.core.spell.magic_missile",
        "dnd5e.core.spell.raise_dead",
        "dnd5e.core.spell.shield",
        "dnd5e.core.spell.structured_resolution",
        CORE_SLEEP_MECHANIC_ID,
        CORE_MENDING_MECHANIC_ID,
        CORE_WITCH_BOLT_MECHANIC_ID,
        "dnd5e.addon.rulebook.d-d-5e-sword-coast-adventurer-s-guide.16e6a243ef0a.feature.bladesong",
        "dnd5e.addon.rulebook.d-d-5e-sword-coast-adventurer-s-guide.16e6a243ef0a.feature.song-of-defense",
        "dnd5e.addon.rulebook.d-d-5e-sword-coast-adventurer-s-guide.16e6a243ef0a.feature.song-of-victory",
    }
)

SCAG_OFFICIAL_ADDON_ID = (
    "dnd5e.addon.rulebook.d-d-5e-sword-coast-adventurer-s-guide.16e6a243ef0a.addon"
)
SCAG_RULE_PACK_ID = "dnd5e.addon.rulebook.d-d-5e-sword-coast-adventurer-s-guide.16e6a243ef0a"
SCAG_BLADE_SINGING_SUBCLASS_ID = SCAG_RULE_PACK_ID + ".subclass.bladesinging"
SCAG_BLADE_SINGING_FEATURE_IDS = {
    SCAG_RULE_PACK_ID + ".feature.training-in-war-and-song",
    SCAG_RULE_PACK_ID + ".feature.bladesong",
    SCAG_RULE_PACK_ID + ".feature.extra-attack",
    SCAG_RULE_PACK_ID + ".feature.song-of-defense",
    SCAG_RULE_PACK_ID + ".feature.song-of-victory",
}
EBERRON_RIGHT_TOOL_FOR_JOB_FEATURE_SUFFIX = ".feature.the-right-tool-for-the-job"
RIGHT_TOOL_FOR_JOB_MECHANIC_ID = "dnd5e.character.right_tool_for_job.v1"
RIGHT_TOOL_FOR_JOB_METADATA_KEY = "right_tool_for_job"
RIGHT_TOOL_FOR_JOB_ARTISAN_TOOL_NAMES = frozenset(
    {
        "alchemist's supplies",
        "brewer's supplies",
        "calligrapher's supplies",
        "carpenter's tools",
        "cartographer's tools",
        "cobbler's tools",
        "cook's utensils",
        "glassblower's tools",
        "jeweler's tools",
        "leatherworker's tools",
        "mason's tools",
        "painter's supplies",
        "potter's tools",
        "smith's tools",
        "tinker's tools",
        "weaver's tools",
        "woodcarver's tools",
    }
)


def _semantic_plan_save_facts(
    source_card: dict[str, Any], compiled_plan: Any
) -> dict[str, dict[str, Any]]:
    """Bind each save's static classification to that card's own effect text.

    Plan-wide citation relevance is insufficient: an unrelated citation must
    not classify another clause's save. Interpretation remains authored Pack
    evidence; this boundary checks identity and does not infer rules from prose.
    """
    if str(source_card.get("id") or "") != compiled_plan.source_card_id:
        raise CombatEngineError("save source plan does not match its recorded card")
    evidence_texts = source_cards.source_card_evidence_texts(source_card)
    result: dict[str, dict[str, Any]] = {}
    for step in compiled_plan.steps:
        if step["op"] != "check.save":
            continue
        source = step["args"].get("source")
        try:
            facts = validated_save_source_facts(
                source,
                citations=compiled_plan.citations,
                source_card_kind=compiled_plan.source_card_kind,
            )
        except ValueError as error:
            raise CombatEngineError(f"save step {step['id']} source: {error}") from error
        if facts:
            excerpt = _normalize_source_evidence_text(source["source_excerpt"])
            if not any(excerpt in evidence for evidence in evidence_texts):
                raise CombatEngineError(
                    f"save step {step['id']} must cite its recorded card effect"
                )
        result[step["id"]] = facts
    return result


def _normalize_sleep_spatial_facts(
    declaration: dict[str, Any] | None,
    *,
    actor_ids: set[str],
    campaign_revision: int,
) -> dict[str, Any]:
    """Validate the DM's coordinate-free decision for the exact 2014 Sleep area."""
    if declaration is None or declaration == {}:
        raise NeedsRulingError(
            "Sleep requires a reviewed 20-foot area and 90-foot origin-range decision",
            missing=("sleep.spatial_facts",),
            ruling_kind="agent_dm_adjudication",
        )
    if not isinstance(declaration, dict) or set(declaration) != {"spatial_facts"}:
        raise CombatEngineError(
            "Agent Sleep declaration requires only spatial_facts, not coordinates"
        )
    facts = declaration["spatial_facts"]
    fields = {
        "decision_id",
        "reason",
        "origin_description",
        "campaign_revision",
        "origin_in_range",
        "line_of_effect_clear",
        "affected_target_ids",
        "excluded_actor_ids",
    }
    if not isinstance(facts, dict) or set(facts) != fields:
        raise CombatEngineError(
            "Sleep spatial_facts requires the complete source-bound area decision"
        )
    normalized = deepcopy(facts)
    for field, minimum, maximum in (
        ("decision_id", 1, 100),
        ("reason", 10, 1000),
        ("origin_description", 10, 500),
    ):
        value = facts[field]
        if not isinstance(value, str) or not minimum <= len(" ".join(value.split())) <= maximum:
            raise CombatEngineError(f"Sleep spatial fact {field} must be bounded non-empty text")
        normalized[field] = " ".join(value.split())
    if (
        type(facts["campaign_revision"]) is not int
        or facts["campaign_revision"] != campaign_revision
    ):
        raise CombatEngineError("Sleep spatial facts do not match the current campaign revision")
    for field in ("origin_in_range", "line_of_effect_clear"):
        if facts[field] is not True:
            raise CombatEngineError(f"Sleep requires an affirmative boolean {field} decision")
    for field in ("affected_target_ids", "excluded_actor_ids"):
        values = facts[field]
        if (
            not isinstance(values, list)
            or any(not isinstance(item, str) or not item for item in values)
            or len(set(values)) != len(values)
            or set(values) - actor_ids
        ):
            raise CombatEngineError(f"Sleep {field} must contain unique current living actor IDs")
    affected = set(facts["affected_target_ids"])
    excluded = set(facts["excluded_actor_ids"])
    if affected & excluded or affected | excluded != actor_ids:
        raise CombatEngineError(
            "Sleep area decision must account for every living actor exactly once"
        )
    return {
        "shape": "sphere",
        "radius_ft": 20,
        "origin_range_ft": 90,
        "positioning_mode": "agent",
        "spatial_facts": normalized,
        "targets": [{"target_id": target_id} for target_id in facts["affected_target_ids"]],
    }


def _normalize_sleep_wake_spatial_facts(
    payload: dict[str, Any] | None, *, campaign_revision: int
) -> dict[str, Any]:
    """Bind the DM's touch/reach judgment to this shake action's state snapshot."""
    if payload is None or payload == {}:
        raise NeedsRulingError(
            "shaking a sleeper awake requires a reviewed contact-range decision",
            missing=("shake_sleep.spatial_facts",),
            ruling_kind="agent_dm_adjudication",
        )
    if not isinstance(payload, dict) or set(payload) != {"spatial_facts"}:
        raise CombatEngineError("Agent shake_sleep accepts only spatial_facts, not coordinates")
    facts = payload["spatial_facts"]
    if not isinstance(facts, dict) or set(facts) != {
        "decision_id",
        "reason",
        "campaign_revision",
        "can_touch_target",
    }:
        raise CombatEngineError("shake_sleep requires a complete contact-range decision")
    normalized = deepcopy(facts)
    for field, minimum, maximum in (("decision_id", 1, 100), ("reason", 10, 1000)):
        value = facts[field]
        if not isinstance(value, str) or not minimum <= len(" ".join(value.split())) <= maximum:
            raise CombatEngineError(f"shake_sleep {field} must be bounded non-empty text")
        normalized[field] = " ".join(value.split())
    if (
        type(facts["campaign_revision"]) is not int
        or facts["campaign_revision"] != campaign_revision
    ):
        raise CombatEngineError(
            "shake_sleep spatial facts must match the current campaign revision"
        )
    if facts["can_touch_target"] is not True:
        raise CombatEngineError("shake_sleep requires an affirmative boolean can_touch_target")
    return normalized


def _structured_spell_save_facts(
    spell: dict[str, Any], resolution: dict[str, Any]
) -> dict[str, Any]:
    """Classify the exact native spell clause, never its display name or damage type."""
    facts: dict[str, Any] = {"save_source_kind": "spell"}
    spell_id = str(spell.get("id") or "")
    nonpoison_spell_ids = {
        f"dnd5e.content.srd2014.spell.{slug}"
        for slug in ("sacred-flame", "fireball", "lightning-bolt")
    }
    if spell_id in nonpoison_spell_ids and resolution == effective_spell_resolution(
        {"id": spell_id}
    ):
        facts.update(save_against_poison=False, save_effect_conditions=[])
    return facts


SCAG_WATCHERS_EYE_BACKGROUND_IDS = frozenset(
    {
        (
            "dnd5e.addon.rulebook.d-d-5e-sword-coast-adventurer-s-guide."
            "16e6a243ef0a.background.city-watch"
        ),
        (
            "dnd5e.addon.rulebook.d-d-5e-sword-coast-adventurer-s-guide."
            "16e6a243ef0a.background.investigator"
        ),
    }
)
WATCHERS_EYE_CAPABILITIES = frozenset(
    {
        "local_law",
        "local_criminal_activity",
        "watch_outpost",
    }
)
WATCHERS_EYE_FACT_METADATA_KEY = "dnd5e_watchers_eye"
WATCHERS_EYE_FACT_SCHEMA_VERSION = 1
WATCHERS_EYE_FEATURE_NAME = "Watcher's Eye"
WATCHERS_EYE_NARRATIVE_SCHEMA = "sagasmith.dnd.narrative-capability.v1"


def _watchers_eye_source_binding(artifact: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return exact source-review binding for the two SCAG background cards."""

    artifact_id = str(artifact.get("id") or "")
    if artifact_id not in SCAG_WATCHERS_EYE_BACKGROUND_IDS:
        return None
    if str(artifact.get("kind") or "") != "background":
        return None
    if str(artifact.get("application_state") or "") != "selection_ready":
        return None
    if str(artifact.get("execution_state") or "") != "ruling_ready":
        return None
    raw_selection_contract = artifact.get("selection_contract")
    raw_catalog_review = artifact.get("catalog_review")
    if raw_selection_contract is not None or raw_catalog_review is not None:
        if selection_contract_errors(artifact):
            return None
        selection_contract = dict(raw_selection_contract or {})
        catalog_review = dict(raw_catalog_review or {})
        reviewed_content_hash = str(selection_contract.get("reviewed_content_hash") or "")
        if (
            selection_contract.get("status") != "ready"
            or selection_contract.get("materializer") != "dnd5e.character.background.v1"
            or catalog_review.get("status") != "approved"
            or reviewed_content_hash != str(catalog_review.get("reviewed_content_hash") or "")
            or len(reviewed_content_hash) != 64
        ):
            return None
    else:
        # The import boundary intentionally strips authoring attestations after
        # binding them into the immutable addon checksum and definition provenance.
        reviewed_content_hash = content_fingerprint(artifact)
    card = dict(artifact.get("card") or {})
    grants = dict(card.get("background_grants") or {})
    if str(grants.get("feature") or "") != WATCHERS_EYE_FEATURE_NAME:
        return None
    source_refs = [
        dict(item) for item in artifact.get("source_refs") or [] if isinstance(item, dict)
    ]
    feature_sources = [
        item
        for item in source_refs
        if "watcher's eye" in str(item.get("note") or "").casefold()
        and str(item.get("chunk_id") or item.get("chunk_key") or "")
    ]
    if len(feature_sources) != 1:
        return None
    chunk_key = str(feature_sources[0].get("chunk_id") or feature_sources[0].get("chunk_key"))
    rule_refs = [str(item) for item in artifact.get("rule_refs") or []]
    feature_rule_refs = [item for item in rule_refs if item.endswith(f"#chunk:{chunk_key}")]
    if len(feature_rule_refs) != 1:
        return None
    ruling_requirements = [
        dict(item) for item in card.get("ruling_requirements") or [] if isinstance(item, dict)
    ]
    if not any(
        str(item.get("ruling_kind") or "") == "agent_dm_adjudication"
        and len(str(item.get("source_excerpt") or "")) >= 100
        for item in ruling_requirements
    ):
        return None
    return {
        "artifact_id": artifact_id,
        "background_name": str(card.get("name") or artifact_id),
        "reviewed_content_hash": reviewed_content_hash,
        "rule_refs": rule_refs,
        "feature_rule_ref": feature_rule_refs[0],
    }


def has_active_owned_condition(
    encounter: dict[str, Any],
    *,
    target_id: str,
    condition: str,
) -> bool:
    """Return whether an active scene-authored effect still owns the condition."""

    normalized_condition = condition.casefold()
    return any(
        isinstance(effect, dict)
        and effect.get("active", True)
        and str(effect.get("actor_id") or "") == target_id
        and str(effect.get("condition") or "").casefold() == normalized_condition
        for effect in encounter.get("source_conditions", [])
    )


AGENT_RULING_TRANSACTION_RULES = (
    "inspect_existing_payment_before_settlement",
    "do_not_pay_twice",
    "use_public_tools_only",
    "preserve_source_revision_and_random_receipts",
    "use_combat_choice_only_for_an_owned_window",
)
_RULING_FUNCTION = TypeVar("_RULING_FUNCTION", bound=Callable[..., Any])


def _agent_ruling_policy() -> dict[str, Any]:
    """Return the machine-readable boundary shared by capabilities and calls."""

    return {
        "default_dm_resolver": "agent",
        "agent_adjudicates": list(AGENT_RULING_KIND_ORDER),
        "requires_external_input": list(EXTERNAL_RULING_KIND_ORDER),
        "transaction_rules": list(AGENT_RULING_TRANSACTION_RULES),
    }


def _ruling_resolution_for_kind(ruling_kind: str) -> dict[str, Any]:
    """Return the resolver contract for one explicitly classified boundary."""

    if ruling_kind not in RULING_KINDS:
        ruling_kind = "agent_dm_adjudication"
    if ruling_kind in EXTERNAL_RULING_KINDS:
        return {
            "default_resolver": "external_input",
            "ruling_kind": ruling_kind,
            "policy_ref": "server_capabilities.ruling_policy",
        }
    return {
        "default_resolver": "agent",
        "ruling_kind": ruling_kind,
        "policy_ref": "server_capabilities.ruling_policy",
        "requires_external_input_only_for": list(EXTERNAL_RULING_KIND_ORDER),
    }


def _agent_ruling_resolution(result: Any) -> dict[str, Any] | None:
    """Annotate a live pending ruling without pretending that it is settled."""

    if not isinstance(result, dict) or result.get("status") != "pending_ruling":
        return None
    ruling_kind = _pending_result_ruling_kind(result)
    return _ruling_resolution_for_kind(ruling_kind)


def _ruling_requirement(reason: str, ruling_kind: str) -> dict[str, Any]:
    """Describe a ruling boundary without collapsing it into free-form prose."""

    return {
        "reason": reason,
        **_ruling_resolution_for_kind(ruling_kind),
    }


def _ruling_status(status: str, ruling_kind: str) -> dict[str, Any]:
    """Attach a self-contained resolver contract to a paused adjudication."""

    result = {"status": status}
    if status == "pending_ruling":
        result.update(_ruling_resolution_for_kind(ruling_kind))
    return result


def _pending_result_ruling_kind(
    result: dict[str, Any],
    *,
    fallback: str = "agent_dm_adjudication",
) -> str:
    """Preserve a nested rule pause's owner while defaulting unclassified DM work."""
    return nested_ruling_kind(result, fallback=fallback)


def _facade_result(
    action: str,
    result: Any,
    *,
    page: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Preserve a nested domain ruling's ownership at the public facade."""

    status = result.get("status", "ok") if isinstance(result, dict) else "ok"
    response = {"status": status, "action": action, "result": result}
    if page is not None:
        response["page"] = page
        response["next_cursor"] = page.get("next_cursor")
    if status == "pending_ruling" and isinstance(result, dict):
        ruling_kind = _pending_result_ruling_kind(result)
        response.update(_ruling_resolution_for_kind(ruling_kind))
    return response


def _bounded_page(
    values: list[Any],
    *,
    scope: str,
    query: str = "",
    limit: int = 50,
    cursor: str | None = None,
    offset: int = 0,
) -> tuple[list[Any], dict[str, Any]]:
    """Filter and page one already-authorized collection with an opaque cursor.

    The cursor is a continuation name rather than a capability. It is bound to
    the tool/view/filter scope so a client cannot accidentally reuse it for a
    different authorized collection. Authorization is still rechecked before
    this helper receives any values.
    """

    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 100_000:
        raise ValueError("offset must be an integer between 0 and 100000")
    normalized_query = " ".join(str(query or "").split()).casefold()
    fingerprint = json_sha256({"scope": scope, "query": normalized_query})[:24]
    if cursor:
        if offset:
            raise ValueError("cursor and offset are mutually exclusive")
        try:
            padded = str(cursor) + "=" * (-len(str(cursor)) % 4)
            cursor_payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
            if (
                not isinstance(cursor_payload, dict)
                or cursor_payload.get("v") != 1
                or cursor_payload.get("f") != fingerprint
                or isinstance(cursor_payload.get("o"), bool)
                or not isinstance(cursor_payload.get("o"), int)
                or not 0 <= int(cursor_payload["o"]) <= 100_000
            ):
                raise ValueError
            offset = int(cursor_payload["o"])
        except (
            binascii.Error,
            UnicodeDecodeError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            raise ValueError(
                "cursor is invalid for this query; restart from the first page"
            ) from exc

    filtered = list(values)
    if normalized_query:
        filtered = [
            value
            for value in filtered
            if normalized_query
            in json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).casefold()
        ]
    page_values = filtered[offset : offset + limit]
    next_offset = offset + len(page_values)
    has_more = next_offset < len(filtered)
    next_cursor = None
    if has_more:
        raw = json.dumps(
            {"v": 1, "f": fingerprint, "o": next_offset},
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        next_cursor = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return page_values, {
        "limit": limit,
        "returned": len(page_values),
        "has_more": has_more,
        "next_cursor": next_cursor,
        "total_count": len(filtered),
    }


def _page_limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    return value


def _cursor_offset(
    *,
    scope: str,
    query: str = "",
    cursor: str | None = None,
    offset: int = 0,
) -> tuple[str, int]:
    """Decode one scope-bound continuation cursor before an authority query."""

    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 100_000:
        raise ValueError("offset must be an integer between 0 and 100000")
    normalized_query = " ".join(str(query or "").split()).casefold()
    fingerprint = json_sha256({"scope": scope, "query": normalized_query})[:24]
    if not cursor:
        return fingerprint, offset
    if offset:
        raise ValueError("cursor and offset are mutually exclusive")
    try:
        padded = str(cursor) + "=" * (-len(str(cursor)) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        if (
            not isinstance(payload, dict)
            or payload.get("v") != 1
            or payload.get("f") != fingerprint
            or isinstance(payload.get("o"), bool)
            or not isinstance(payload.get("o"), int)
            or not 0 <= int(payload["o"]) <= 100_000
        ):
            raise ValueError
        return fingerprint, int(payload["o"])
    except (
        binascii.Error,
        UnicodeDecodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        raise ValueError("cursor is invalid for this query; restart from the first page") from exc


def _encode_cursor(fingerprint: str, offset: int) -> str:
    raw = json.dumps(
        {"v": 1, "f": fingerprint, "o": offset},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _authority_page(
    values: list[Any],
    *,
    fingerprint: str,
    offset: int,
    limit: int,
    query: str = "",
    chronological_tail: bool = False,
) -> tuple[list[Any], dict[str, Any]]:
    """Build page metadata for a limit+1 authority query.

    EventService returns each newest window in chronological order, so its
    look-ahead record is at the front. RevisionService is newest-first, so its
    look-ahead record is at the end.
    """

    limit = _page_limit(limit)
    has_more = len(values) > limit
    selected = values[-limit:] if chronological_tail else values[:limit]
    normalized_query = " ".join(str(query or "").split()).casefold()
    if normalized_query:
        selected = [
            value
            for value in selected
            if normalized_query
            in json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).casefold()
        ]
    next_cursor = _encode_cursor(fingerprint, offset + limit) if has_more else None
    return selected, {
        "limit": limit,
        "returned": len(selected),
        "has_more": has_more,
        "next_cursor": next_cursor,
    }


def _needs_ruling_kind(
    error: NeedsRulingError | RuleEventRulingRequiredError,
) -> str:
    """Keep source defects out of the Agent's ordinary adjudication lane."""

    explicit_kind = str(getattr(error, "ruling_kind", "") or "agent_dm_adjudication")
    if explicit_kind != "agent_dm_adjudication":
        return explicit_kind
    message = str(error).casefold()
    missing = {str(item).casefold() for item in getattr(error, "missing", ())}
    if (
        any(item.startswith(("weapon.range:", "spell.range:")) for item in missing)
        or "unresolved rules" in message
        or "unsupported source contract" in message
    ):
        return "missing_or_conflicting_source_review"
    return explicit_kind


def _agent_ruling_boundary(function: _RULING_FUNCTION) -> _RULING_FUNCTION:
    """Convert a safe pre-commit engine boundary into a classified adjudication."""

    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        stream = active_random_stream()
        random_position_before = stream.position if stream is not None else None
        try:
            return function(*args, **kwargs)
        except (NeedsRulingError, RuleEventRulingRequiredError) as exc:
            if stream is not None and random_position_before is not None:
                stream.rewind_unpersisted(random_position_before)
            ruling_kind = _needs_ruling_kind(exc)
            resolution = _ruling_resolution_for_kind(ruling_kind)
            requirements = [
                deepcopy(item)
                for item in getattr(exc, "requirements", ())
                if isinstance(item, dict)
            ]
            return {
                "status": "pending_ruling",
                **resolution,
                "reason": str(exc),
                "missing": list(getattr(exc, "missing", ())),
                **({"ruling_requirements": requirements} if requirements else {}),
                "committed": False,
                "retry_contract": {
                    "resolver": resolution["default_resolver"],
                    "reuse_current_revision": True,
                    "use_public_tools_only": True,
                },
            }

    return wrapped  # type: ignore[return-value]


SUPPORTED_FEATURE_SELECTION_KINDS = frozenset(
    {
        "",
        "ability_score_increase",
        "bonus_cantrip",
        "feat_grant",
        "favored_enemy",
        "eldritch_invocations_2024",
        "feature_grants",
        "known_spell_grants",
        "language_grant",
        "magic_initiate",
        "mystic_arcanum",
        "proficiency_grants",
        "signature_spells",
        "spell_mastery",
    }
)
SUPPORTED_FEATURE_SELECTION_REQUIREMENT_FIELDS = frozenset(
    {
        "allowed_distributions",
        "allowed_categories",
        "ability_options",
        "always_prepared",
        "at_will_spells",
        "casting_times",
        "choice_uniqueness_scope",
        "cantrip_count",
        "count",
        "creature_type_options",
        "eligible_class",
        "eligible_classes",
        "field",
        "grants_skill_proficiency",
        "grants_language_proficiency",
        "grants_tool_proficiency",
        "grants_weapon_proficiency",
        "grant_method",
        "groups",
        "humanoid_race_count",
        "kind",
        "language_if_spoken",
        "maximum_score",
        "maximum_spell_level",
        "option_prerequisites",
        "option_artifact_ids",
        "option_subtype",
        "options",
        "replacement_study_minutes",
        "repeatable_options",
        "required_spell_levels",
        "requires_existing_proficiency",
        "requires_new_choice",
        "requires_new_expertise",
        "requires_spellbook",
        "requires_untrained_skill",
        "skills_only",
        "skill_options",
        "schools",
        "source_class_options",
        "source_class",
        "spellcasting_ability_options",
        "spell_level",
        "level_1_spell_count",
        "tool_options",
    }
)
SUPPORTED_FEATURE_OPTION_PREREQUISITE_FIELDS = frozenset(
    {"minimum_level", "required_cantrip", "required_invocation", "required_pact_boon"}
)
SUPPORTED_FEATURE_MECHANICAL_GRANTS = frozenset(
    {
        "armor_proficiencies",
        "conditional_condition_immunities",
        "condition_immunities",
        "hp_per_class_level",
        "immunities",
        "languages",
        "resources",
        "skill_proficiencies",
        "skill_proficiency_or_expertise",
        "tool_expertise_all",
        "tool_proficiencies",
        "tool_proficiency_replacement_options",
        "unarmored_base",
        "unarmored_formula",
        "weapon_proficiencies",
    }
)


def _feature_requirements_with_active_extensions(
    requirements: dict[str, Any],
    *,
    selector_card: dict[str, Any],
    candidates: list[tuple[str, str, dict[str, Any]]],
) -> dict[str, Any]:
    """Merge active addon options into a selector without changing its count."""

    result = deepcopy(requirements)
    selector_name = str(selector_card.get("name") or "").strip().casefold()
    selector_class = str(selector_card.get("class_name") or "").strip().casefold()
    selector_subclass = str(selector_card.get("subclass_name") or "").strip().casefold()
    options = [str(item) for item in result.get("options", [])]
    option_ids = {
        str(key): str(value) for key, value in dict(result.get("option_artifact_ids") or {}).items()
    }
    prerequisites = deepcopy(dict(result.get("option_prerequisites") or {}))
    at_will_spells = {
        str(key): str(value) for key, value in dict(result.get("at_will_spells") or {}).items()
    }
    changed = False
    for _pack_id, _version, artifact in candidates:
        if artifact.get("kind") != "feature":
            continue
        option_card = dict(artifact.get("card") or {})
        if str(option_card.get("feature_subtype") or "") != "selectable_option":
            continue
        extension = dict(option_card.get("extends_feature") or {})
        if not extension:
            continue
        if str(extension.get("name") or "").strip().casefold() != selector_name:
            continue
        extension_class = str(extension.get("class_name") or "").strip().casefold()
        extension_subclass = str(extension.get("subclass_name") or "").strip().casefold()
        if extension_class and extension_class != selector_class:
            continue
        if extension_subclass and extension_subclass != selector_subclass:
            continue
        option_name = str(option_card.get("name") or "").strip()
        option_id = str(artifact.get("id") or "").strip()
        if not option_name or not option_id:
            raise RulesetUnavailableError("feature option extension is missing identity")
        existing = next(
            (item for item in options if item.casefold() == option_name.casefold()),
            None,
        )
        if existing is not None:
            if option_ids.get(existing, option_id) != option_id:
                raise RulesetUnavailableError(
                    f"feature option extension conflicts with {option_name}"
                )
            continue
        options.append(option_name)
        option_ids[option_name] = option_id
        prerequisite = {
            field: deepcopy(option_card[field])
            for field in SUPPORTED_FEATURE_OPTION_PREREQUISITE_FIELDS
            if option_card.get(field) not in (None, "", [])
        }
        if prerequisite:
            prerequisites[option_name] = prerequisite
        at_will_spell = str(option_card.get("at_will_spell") or "").strip()
        if at_will_spell:
            at_will_spells[option_name] = at_will_spell
        changed = True
    if not changed:
        return result
    result["options"] = options
    result["option_artifact_ids"] = option_ids
    result["option_prerequisites"] = prerequisites
    result["at_will_spells"] = at_will_spells
    return result


def _compact_agent_evidence(value: Any) -> str:
    return compact_ascii_key(value)


def _catalog_identity_is_evidenced(name: str, evidence: str) -> bool:
    """Prove a reviewed identity across one or more split source headings.

    PDF layout can split a printed title such as ``Channel Divinity: Twilight
    Sanctuary`` into sibling headings.  A contiguous character check then
    fails because their shared parent heading is repeated between the parts.
    The caller places the leaf headings next to each other before their full
    paths, which restores the printed title without ignoring arbitrary words.
    """

    def normalized(value: str) -> str:
        return "".join(character for character in value.casefold() if character.isalnum())

    normalized_name = normalized(name)
    normalized_evidence = "".join(
        character for character in evidence.casefold() if character.isalnum()
    )
    if normalized_name and normalized_name in normalized_evidence:
        return True

    evidence_tokens = [
        normalized(token) for token in re.findall(r"[A-Za-z0-9]+", evidence) if normalized(token)
    ]
    name_words = max(1, len(re.findall(r"[A-Za-z0-9]+", name)))
    if len(normalized_name) >= 4:
        for start in range(len(evidence_tokens)):
            for width in range(max(1, name_words - 1), name_words + 3):
                candidate = "".join(evidence_tokens[start : start + width])
                if abs(len(candidate) - len(normalized_name)) <= 1 and (
                    _bounded_edit_distance(candidate, normalized_name, limit=1) <= 1
                ):
                    return True

    variant = re.fullmatch(r"\s*(.+?)\s*\(([^()]+)\)\s*", name)
    if variant is None:
        return False
    base_name = normalized(variant.group(1))
    qualifier = normalized(variant.group(2))
    if not base_name or not qualifier or base_name not in normalized_evidence:
        return False
    if qualifier in normalized_evidence:
        return True

    # Some source-approved variants are explicit compositions of independently
    # printed alternatives (for example ``Feral + Winged``).  Treat ``+`` as a
    # strict conjunction: the base identity and every complete qualifier must
    # be evidenced by the same bounded source selection.
    compound_parts = [
        normalized(part) for part in re.split(r"\s+\+\s+", variant.group(2)) if normalized(part)
    ]
    if len(compound_parts) > 1 and all(
        len(part) >= 3 and part in normalized_evidence for part in compound_parts
    ):
        return True

    # Printed qualifiers in compact tables are often split at one mistaken
    # glyph (for example ``Vadalis`` -> ``Vada I is``).  Keep this fallback
    # bounded to a variant whose base identity is already exact, one edit, and
    # at most two additional OCR fragments.
    qualifier_words = max(1, len(re.findall(r"[A-Za-z0-9]+", variant.group(2))))
    for start in range(len(evidence_tokens)):
        for width in range(max(1, qualifier_words - 1), qualifier_words + 3):
            candidate = "".join(evidence_tokens[start : start + width])
            if (
                abs(len(candidate) - len(qualifier)) <= 1
                and _bounded_edit_distance(
                    candidate,
                    qualifier,
                    limit=1,
                )
                <= 1
            ):
                return True
    return False


def _select_catalog_ocr_identity_evidence(
    name: str,
    observations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Select checksum-bound OCR evidence without persisting full page text."""

    for observation in observations:
        text = str(observation.get("text") or "")
        if not text or not _catalog_identity_is_evidenced(name, text):
            continue
        return {
            key: observation[key]
            for key in (
                "provider",
                "profile",
                "model",
                "scale",
                "page_number",
                "text_sha256",
            )
            if key in observation
        }
    return None


def _bounded_edit_distance(left: str, right: str, *, limit: int) -> int:
    """Return an exact small edit distance without paying for unbounded OCR text."""

    if abs(len(left) - len(right)) > limit:
        return limit + 1
    previous = list(range(len(right) + 1))
    for left_index, left_character in enumerate(left, start=1):
        current = [left_index]
        row_minimum = left_index
        for right_index, right_character in enumerate(right, start=1):
            value = min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (0 if left_character == right_character else 1),
            )
            current.append(value)
            row_minimum = min(row_minimum, value)
        if row_minimum > limit:
            return limit + 1
        previous = current
    return previous[-1]


def _compact_transcription_key(value: Any) -> str:
    """Keep Unicode letters/digits while ignoring OCR layout punctuation."""

    return "".join(
        character.casefold()
        for character in unicodedata.normalize("NFKC", str(value or ""))
        if character.isalnum()
    )


_TRANSCRIPTION_NUMBER_WORDS = {
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
    "hundred",
    "thousand",
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "half",
    "quarter",
    "once",
    "twice",
    "thrice",
    "single",
    "double",
    "triple",
    "both",
}


def _transcription_numeric_semantics(value: Any) -> list[str]:
    """Keep written quantities that a digit-only guard would otherwise miss."""

    words = re.findall(
        r"[^\W_]+",
        unicodedata.normalize("NFKC", str(value or "")).casefold(),
        re.UNICODE,
    )
    return [word for word in words if word in _TRANSCRIPTION_NUMBER_WORDS]


def _bounded_ocr_heading_equivalent(left: str, right: str) -> bool:
    """Match one source heading after at most one OCR glyph substitution."""

    left_key = compact_ascii_key(left)
    right_key = compact_ascii_key(right)
    if left_key == right_key:
        return True
    return bool(
        min(len(left_key), len(right_key)) >= 4
        and abs(len(left_key) - len(right_key)) <= 1
        and _bounded_edit_distance(left_key, right_key, limit=1) <= 1
    )


def _ocr_heading_confusable_key(value: Any) -> str:
    """Fold glyphs that OCR commonly confuses in all-cap statblock headings."""

    return compact_ascii_key(value).translate(str.maketrans({"0": "o", "1": "i", "l": "i"}))


def _noisy_ocr_heading_equivalent(left: str, right: str) -> bool:
    """Match a visibly corrupt heading to one same-page reviewed identity."""

    if not re.search(r"[^A-Za-z '\-]", left):
        return False

    def variants(value: str) -> set[str]:
        words = re.findall(r"[A-Za-z0-9]+", value)
        result = {_ocr_heading_confusable_key(value)}
        if len(words) > 1 and len(words[0]) == 1:
            result.add(_ocr_heading_confusable_key(" ".join(words[1:])))
        for index, word in enumerate(words):
            if word.casefold() == "in" and index > 0:
                result.add(_ocr_heading_confusable_key(" ".join(words[:index])))
        return {item for item in result if len(item) >= 7}

    return any(
        abs(len(left_key) - len(right_key)) <= 2
        and _bounded_edit_distance(left_key, right_key, limit=2) <= 2
        for left_key in variants(left)
        for right_key in variants(right)
    )


def _bundled_mm2014_actor_card(
    *,
    name: str,
    edition: str,
    publication_id: str,
    cards: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve one MM 2014 OCR heading to a unique bundled SRD actor card.

    This is deliberately publication-bound. A supplement can redefine a
    creature under the same name and must therefore provide its own reviewed
    card; only the 2014 Monster Manual may reuse the corresponding SRD 5.1
    implementation.
    """

    if edition != "2014" or publication_id != "mm2014":
        return None
    source_key = _ocr_heading_confusable_key(name)
    matches = [
        card
        for card in cards
        if _bounded_ocr_heading_equivalent(
            source_key,
            _ocr_heading_confusable_key(dict(card.get("payload") or {}).get("name")),
        )
    ]
    return deepcopy(matches[0]) if len(matches) == 1 else None


def _valid_statblock_heading(value: Any) -> bool:
    """Reject OCR debris before it can become a portable actor identity."""

    heading = " ".join(str(value or "").split())
    if compact_ascii_key(heading) in {
        "charactername",
        "creaturename",
        "monstername",
    }:
        return False
    return bool(
        len(compact_ascii_key(heading)) >= 2
        and sum(character.isalnum() for character in heading) >= 2
        and "\ufffd" not in heading
    )


def _statblock_mechanical_identity(parsed: Any) -> str:
    """Hash name-independent printed mechanics for OCR duplicate recovery."""

    sheet = dict(parsed.sheet)
    combat = dict(sheet.get("combat") or {})
    content = dict(sheet.get("content") or {})
    inventory = dict(sheet.get("inventory") or {})
    signature = {
        "challenge_rating": str(parsed.challenge_rating or ""),
        "experience_points": parsed.experience_points,
        "abilities": {
            name: dict(value).get("score")
            for name, value in dict(sheet.get("abilities") or {}).items()
        },
        "hp": dict(combat.get("hp") or {}).get("max"),
        "ac": dict(combat.get("ac") or {}).get("override"),
        "speed": dict(combat.get("speed") or {}),
        "items": [
            {
                "name": str(item.get("name") or ""),
                "kind": str(item.get("kind") or ""),
                "mechanics": {
                    key: dict(item.get("mechanics") or {}).get(key)
                    for key in (
                        "attack_type",
                        "attack_bonus_override",
                        "damage_formula",
                        "damage_bonus_override",
                        "damage_type",
                        "normal_range_ft",
                        "long_range_ft",
                        "reach_ft",
                    )
                },
            }
            for item in inventory.get("items") or []
            if isinstance(item, dict)
            and str(item.get("kind") or "") in {"weapon", "natural_weapon"}
        ],
        "features": [
            str(item.get("name") or "")
            for section in ("activities", "features", "feats", "spells")
            for item in content.get(section) or []
            if isinstance(item, dict)
        ],
    }
    return hashlib.sha256(canonical_json(signature).encode("utf-8")).hexdigest()


def _select_preferred_statblock_reviews(
    reviews: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Choose one strongest source transcription per page and OCR-equivalent name."""

    mode_rank = {
        "visual": 5,
        "agent_text": 4,
        "layout_ocr": 3,
        "layout_text": 2,
        "indexed_text": 1,
    }

    def heading_key(review: dict[str, Any]) -> str:
        heading = re.search(
            r"(?m)^#{1,6}\s+(.+?)\s*$",
            str(review.get("normalized_content") or ""),
        )
        return (
            compact_ascii_key(heading.group(1))
            if heading is not None and _valid_statblock_heading(heading.group(1))
            else str(review.get("id") or "")
        )

    def has_valid_heading(review: dict[str, Any]) -> bool:
        heading = re.search(
            r"(?m)^#{1,6}\s+(.+?)\s*$",
            str(review.get("normalized_content") or ""),
        )
        return heading is not None and _valid_statblock_heading(heading.group(1))

    def preference(review: dict[str, Any]) -> tuple[Any, ...]:
        observation = str(review.get("observation") or "")
        recovery_version = re.search(r"(?i)layout OCR v(\d+)\b", observation)
        heading = re.search(
            r"(?m)^#{1,6}\s+(.+?)\s*$",
            str(review.get("normalized_content") or ""),
        )
        heading_text = heading.group(1) if heading is not None else ""
        return (
            mode_rank.get(str(review.get("review_mode") or ""), 0),
            1 if "Agent named structural statblock slot" in observation else 0,
            int(recovery_version.group(1)) if recovery_version else 0,
            1 if review.get("agent_statblock_fill") is not None else 0,
            1
            if re.search(
                r"(?i)\b(?:category|level|rank|type)\s+\d+\b",
                heading_text,
            )
            else 0,
            len(str(review.get("normalized_content") or "")),
            str(review.get("id") or ""),
        )

    superseded_ids = {
        str(item.get("derived_from_review_id") or "")
        for item in reviews
        if isinstance(item, dict) and str(item.get("derived_from_review_id") or "")
    }
    ranked = sorted(
        (
            dict(item)
            for item in reviews
            if isinstance(item, dict)
            and str(item.get("id") or "") not in superseded_ids
            and has_valid_heading(item)
        ),
        key=preference,
        reverse=True,
    )
    selected: list[dict[str, Any]] = []
    selected_names: dict[int, list[str]] = {}
    for review in ranked:
        page_number = int(review.get("page_number") or 0)
        name = heading_key(review)
        if any(
            _bounded_ocr_heading_equivalent(name, selected_name)
            for selected_name in selected_names.get(page_number, [])
        ):
            continue
        selected.append(review)
        selected_names.setdefault(page_number, []).append(name)
    return sorted(
        selected,
        key=lambda review: (
            int(review.get("page_number") or 0),
            heading_key(review),
            hashlib.sha256(str(review.get("normalized_content") or "").encode("utf-8")).hexdigest(),
        ),
    )


def _portable_statblock_review_audit(
    review: dict[str, Any],
    *,
    reviewed_name: str,
    basis: str,
) -> dict[str, Any]:
    """Describe an excluded source review without leaking a local database id."""

    source_text = str(review.get("normalized_content") or "").strip()
    return {
        "source_review_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "page_number": int(review.get("page_number") or 0),
        "reviewed_name": reviewed_name,
        "basis": basis,
    }


def _canonical_statblock_artifact_for_review(
    reviewed_name: str,
    page_artifacts: list[tuple[str, str]],
) -> tuple[str, str] | None:
    """Return one catalog-reviewed identity for an OCR review on the same page.

    The matching tiers intentionally stay conservative: an ambiguous page never
    rewrites the immutable review heading, even when more than one candidate is
    approximately equivalent.
    """

    matchers = (
        lambda candidate: compact_ascii_key(candidate) == compact_ascii_key(reviewed_name),
        lambda candidate: _bounded_ocr_heading_equivalent(candidate, reviewed_name),
        lambda candidate: _noisy_ocr_heading_equivalent(reviewed_name, candidate),
    )
    for matcher in matchers:
        matches = [item for item in page_artifacts if matcher(item[0])]
        if len(matches) == 1:
            return matches[0]
    return None


def _canonical_statblock_artifact_for_mechanics(
    reviewed_identity: str,
    page_artifacts: list[tuple[str, str, str]],
) -> tuple[str, str] | None:
    """Resolve a reviewed transcription to one same-page catalog card by mechanics."""

    matches = [
        (name, artifact_id)
        for identity, name, artifact_id in page_artifacts
        if identity == reviewed_identity
    ]
    return matches[0] if len(matches) == 1 else None


def _claim_catalog_artifact_for_source_review(
    canonical_artifact: tuple[str, str] | None,
    claimed_artifact_ids: set[str],
) -> str:
    """Return the audit disposition for one review-to-catalog projection."""

    if canonical_artifact is None:
        return "not_in_accepted_catalog"
    artifact_id = canonical_artifact[1]
    if artifact_id in claimed_artifact_ids:
        return "duplicate_review_for_catalog_artifact"
    claimed_artifact_ids.add(artifact_id)
    return "accepted"


def _catalog_statblock_text_superseding_source_review(
    artifact: dict[str, Any] | None,
    source_review_checksum: str,
) -> str:
    """Return a separately reviewed catalog boundary that replaces an OCR review.

    Catalog review may correct an entry boundary after page OCR has produced a
    mechanically parseable but over-wide statblock.  Only a self-consistent,
    source-bound evidence checksum may supersede the immutable lower-stage
    review; ordinary catalog projections with the same text do not.
    """

    if not isinstance(artifact, dict) or artifact.get("kind") != "statblock":
        return ""
    card = dict(artifact.get("card") or {})
    source_text = str(card.get("normalized_content") or "").strip()
    evidence = card.get("review_evidence")
    if not source_text or not isinstance(evidence, dict):
        return ""
    catalog_checksum = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    if catalog_checksum != str(evidence.get("normalized_content_sha256") or ""):
        return ""
    if catalog_checksum == source_review_checksum:
        return ""
    return source_text


def _reviewed_statblock_variants(card: dict[str, Any]) -> list[dict[str, str]]:
    """Validate full source-reviewed actor variants carried by one statblock card."""

    raw_variants = card.get("statblock_variants", [])
    if not isinstance(raw_variants, list) or len(raw_variants) > 32:
        raise ValueError("statblock_variants must be an array of at most 32 variants")
    variants: list[dict[str, str]] = []
    names: set[str] = set()
    checksums: set[str] = set()
    for index, raw_variant in enumerate(raw_variants):
        if not isinstance(raw_variant, dict):
            raise ValueError(f"statblock_variants[{index}] must be an object")
        unsupported = set(raw_variant) - {"name", "normalized_content"}
        if unsupported:
            raise ValueError(
                f"statblock_variants[{index}] has unsupported fields: {sorted(unsupported)}"
            )
        name = " ".join(str(raw_variant.get("name") or "").split())
        source_text = str(raw_variant.get("normalized_content") or "").strip()
        if not _valid_statblock_heading(name) or not source_text:
            raise ValueError(
                f"statblock_variants[{index}] requires a valid name and normalized_content"
            )
        heading = re.search(r"(?m)^#{1,6}\s+(.+?)\s*$", source_text)
        if heading is None or compact_ascii_key(heading.group(1)) != compact_ascii_key(name):
            raise ValueError(f"statblock_variants[{index}] heading must match its reviewed name")
        name_key = compact_ascii_key(name)
        checksum = hashlib.sha256(source_text.encode()).hexdigest()
        if name_key in names or checksum in checksums:
            raise ValueError("statblock_variants must have distinct names and source text")
        names.add(name_key)
        checksums.add(checksum)
        variants.append({"name": name, "normalized_content": source_text})
    return variants


def _artifact_source_pages(artifact: dict[str, Any]) -> set[int]:
    pages: set[int] = set()
    for citation in artifact.get("source_citations") or []:
        page_start = int(citation.get("page_start") or 0)
        page_end = int(citation.get("page_end") or page_start)
        if page_start <= 0 or page_end < page_start:
            continue
        pages.update(range(page_start, page_end + 1))
    return pages


def _index_statblock_source_chunks(
    chunks: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Build one reusable evidence index for a whole rule source."""

    by_id: dict[str, dict[str, Any]] = {}
    by_heading_entries: dict[str, dict[str, dict[str, Any]]] = {}
    for position, chunk in enumerate(chunks):
        chunk_id = str(chunk.get("id") or "")
        if chunk_id:
            by_id[chunk_id] = chunk
        identity = chunk_id or f"position:{position}"
        for heading in chunk.get("heading_path") or []:
            heading_key = compact_ascii_key(str(heading))
            if heading_key:
                by_heading_entries.setdefault(heading_key, {})[identity] = chunk
    return by_id, {
        heading: list(entries.values()) for heading, entries in by_heading_entries.items()
    }


def _artifact_statblock_source_chunks(
    artifact: dict[str, Any],
    *,
    chunks_by_id: dict[str, dict[str, Any]],
    chunks_by_heading: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Select bounded evidence without rescanning the full document catalog."""

    cited_ids = list(
        dict.fromkeys(
            str(citation.get("chunk_id") or "")
            for citation in artifact.get("source_citations") or []
            if str(citation.get("chunk_id") or "")
        )
    )
    cited = [chunks_by_id[chunk_id] for chunk_id in cited_ids if chunk_id in chunks_by_id]
    if cited:
        return cited
    name = compact_ascii_key(str(dict(artifact.get("card") or {}).get("name") or ""))
    return list(chunks_by_heading.get(name, []))


def _cached_rapidocr_provider(
    providers: dict[tuple[str, float], RapidOcrProvider],
    *,
    model_type: str,
    scale: float,
    cache_dir: Path,
) -> RapidOcrProvider:
    """Reuse one lazy OCR engine for every page of the same model profile."""

    key = (str(model_type), round(float(scale), 3))
    return providers.setdefault(
        key,
        RapidOcrProvider(
            scale=key[1],
            model_type=key[0],
            cache_dir=cache_dir,
        ),
    )


def _ocr_fact_key(value: Any) -> str:
    """Fold harmless OCR diacritics before independent fact comparison."""

    return compact_ascii_key(unicodedata.normalize("NFKD", str(value or "")))


def _merge_statblock_discoveries(
    primary: list[dict[str, Any]],
    *,
    primary_provider: str,
    secondary: list[dict[str, Any]],
    secondary_provider: str,
) -> list[tuple[dict[str, Any], str]]:
    """Union independent page discoveries without hiding OCR-only siblings."""

    result = [
        (dict(item), primary_provider)
        for item in primary
        if _valid_statblock_heading(item.get("name"))
    ]
    seen = {compact_ascii_key(item["name"]) for item, _provider in result}
    for raw in secondary:
        item = dict(raw)
        if not _valid_statblock_heading(item.get("name")):
            continue
        name_key = compact_ascii_key(item["name"])
        if name_key in seen:
            continue
        result.append((item, secondary_provider))
        seen.add(name_key)
    return result


def _source_statblock_hint_additions(
    discoveries: list[dict[str, Any]],
    source_names: list[str],
    *,
    layout_blocks: list[dict[str, Any]],
) -> list[str]:
    """Use lower-authority headings only for unclaimed structural cards.

    Artwork captions can share a page with real statblocks. Indexed source
    hints therefore must not create an extra card once the positioned text
    layer has already paired every size/type identity with a heading.
    """

    identity_count = sum(
        is_2014_statblock_identity_line(str(block.get("text") or "")) for block in layout_blocks
    )
    discovered_names = [str(item.get("name") or "") for item in discoveries]
    missing_slots = max(0, identity_count - len(discovered_names))
    if missing_slots == 0:
        return []
    additions: list[str] = []
    for source_name in source_names:
        if any(
            _bounded_ocr_heading_equivalent(source_name, discovered_name)
            for discovered_name in discovered_names
        ):
            continue
        additions.append(source_name)
        if len(additions) >= missing_slots:
            break
    return additions


def _statblock_ocr_discovery_needed(
    discoveries: list[dict[str, Any]],
    *,
    layout_blocks: list[dict[str, Any]],
    usable_catalog_count: int,
) -> bool:
    """Detect an empty or partially paired text layer that needs OCR discovery."""

    def core_label(text: Any, label: str) -> bool:
        value = " ".join(str(text or "").split())
        patterns = {
            # These variants are deliberately limited to observed display-font
            # substitutions.  They only decide whether a page merits OCR; they
            # never supply a mechanical field value.
            "armor_class": r"(?i)^(?:armor|armar)\s+class\b",
            "hit_points": r"(?i)^(?:hit|hil)\s+poin(?:t|l)s?\b",
            "speed": r"(?i)^speed\b",
        }
        return bool(re.match(patterns[label], value))

    identity_count = sum(
        is_2014_statblock_identity_line(str(block.get("text") or "")) for block in layout_blocks
    )
    armor_count = sum(core_label(block.get("text"), "armor_class") for block in layout_blocks)
    hit_point_count = sum(core_label(block.get("text"), "hit_points") for block in layout_blocks)
    speed_count = sum(core_label(block.get("text"), "speed") for block in layout_blocks)
    # Decorated card titles and size/type identities are often image glyphs
    # even when the rest of a PDF is searchable. Repeated AC/HP/Speed cores are
    # therefore independent evidence of sibling cards that still need names.
    structural_card_count = max(
        identity_count,
        min(armor_count, hit_point_count, speed_count),
    )
    paired_count = len(discoveries) + max(0, usable_catalog_count)
    return paired_count == 0 or structural_card_count > paired_count


def _project_recovered_statblock_candidates(
    candidates: list[dict[str, Any]],
    recovered_candidates: list[dict[str, Any]],
    *,
    complete_pages: set[int] | None = None,
) -> list[dict[str, Any]]:
    """Project reviewed cards without discarding unrelated cards on partial pages.

    A catalog recovery can prove every structural slot on a page, in which case
    its reviewed cards replace every extracted statblock on that page.  A later
    Agent-named slot review is narrower: the rest of that page may still contain
    usable cards.  On those partial pages replace only one same-name or
    mechanically identical extracted card; otherwise append the reviewed card
    and leave the ambiguous extraction for the explicit catalog review gate.
    """

    complete = set(complete_pages or ())
    recovered_by_page: dict[int, list[dict[str, Any]]] = {}
    recovered_identities: dict[str, str] = {}
    for candidate in recovered_candidates:
        page_number = int(candidate.get("page_start") or 0)
        recovered_by_page.setdefault(page_number, []).append(candidate)
        content = str(
            dict(dict(candidate.get("artifact") or {}).get("card") or {}).get("normalized_content")
            or candidate.get("normalized_content")
            or ""
        ).strip()
        try:
            parsed = parse_2014_statblock_template_preview(
                content,
                source_key=str(candidate.get("id") or "recovered-statblock"),
                rule_refs=[],
            )
        except (StatblockImportError, ValueError):
            continue
        recovered_identities[str(candidate.get("id") or "")] = _statblock_mechanical_identity(
            parsed
        )
    fully_recovered_pages = complete.intersection(recovered_by_page)

    retained: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get("kind") != "statblock":
            retained.append(candidate)
            continue
        start = int(candidate.get("page_start") or 0)
        end = int(candidate.get("page_end") or 0)
        candidate_pages = set(range(start, end + 1))
        if candidate_pages.intersection(fully_recovered_pages):
            continue
        page_reviews = [
            review
            for page_number in candidate_pages
            for review in recovered_by_page.get(page_number, [])
        ]
        if not page_reviews:
            retained.append(candidate)
            continue
        name_matches = [
            review
            for review in page_reviews
            if _bounded_ocr_heading_equivalent(
                str(candidate.get("name") or ""),
                str(review.get("name") or ""),
            )
        ]
        if len(name_matches) == 1:
            continue
        content = str(
            dict(dict(candidate.get("artifact") or {}).get("card") or {}).get("normalized_content")
            or candidate.get("normalized_content")
            or ""
        ).strip()
        try:
            parsed = parse_2014_statblock_template_preview(
                content,
                source_key=str(candidate.get("id") or "catalog-statblock"),
                rule_refs=[],
            )
        except (StatblockImportError, ValueError):
            retained.append(candidate)
            continue
        identity = _statblock_mechanical_identity(parsed)
        identity_matches = [
            review
            for review in page_reviews
            if recovered_identities.get(str(review.get("id") or "")) == identity
        ]
        if len(identity_matches) != 1:
            retained.append(candidate)
    return [*retained, *recovered_candidates]


_STATBLOCK_INDEX_ENTRY_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z][A-Za-z0-9 '&(),\-]{1,100}?)"
    r"\s*\.{3,}\s*(\d{1,4})\b"
)


def _statblock_index_recovery_hints(
    chunks: list[dict[str, Any]],
    catalog_statblocks: list[dict[str, Any]],
    *,
    page_count: int,
) -> dict[str, Any]:
    """Resolve a printed statblock index to physical PDF pages without guessing offset."""

    entries: list[tuple[str, int]] = []
    seen_entries: set[tuple[str, int]] = set()
    for chunk in chunks:
        headings = [compact_ascii_key(item) for item in chunk.get("heading_path") or []]
        if not any("indexofstatblocks" in heading for heading in headings):
            continue
        for match in _STATBLOCK_INDEX_ENTRY_RE.finditer(str(chunk.get("content") or "")):
            name = " ".join(match.group(1).split()).strip(" .")
            printed_page = int(match.group(2))
            key = (compact_ascii_key(name), printed_page)
            if not _valid_statblock_heading(name) or key in seen_entries:
                continue
            seen_entries.add(key)
            entries.append((name, printed_page))
    if not entries:
        return {
            "entry_count": 0,
            "page_offset": None,
            "offset_support": 0,
            "by_page": {},
        }

    offsets: Counter[int] = Counter()
    for index_name, printed_page in entries:
        matches = [
            candidate
            for candidate in catalog_statblocks
            if _bounded_ocr_heading_equivalent(
                index_name,
                str(candidate.get("name") or ""),
            )
        ]
        if len(matches) != 1:
            continue
        physical_page = matches[0].get("page_start")
        if isinstance(physical_page, bool) or not isinstance(physical_page, int):
            continue
        offset = physical_page - printed_page
        if -20 <= offset <= 20:
            offsets[offset] += 1
    if not offsets:
        return {
            "entry_count": len(entries),
            "page_offset": None,
            "offset_support": 0,
            "by_page": {},
        }
    ranked_offsets = offsets.most_common()
    page_offset, support = ranked_offsets[0]
    tied = len(ranked_offsets) > 1 and ranked_offsets[1][1] == support
    if support < 3 or tied:
        return {
            "entry_count": len(entries),
            "page_offset": None,
            "offset_support": support,
            "by_page": {},
        }
    by_page: dict[int, list[str]] = {}
    for name, printed_page in entries:
        physical_page = printed_page + page_offset
        if 1 <= physical_page <= page_count:
            by_page.setdefault(physical_page, []).append(name)
    return {
        "entry_count": len(entries),
        "page_offset": page_offset,
        "offset_support": support,
        "by_page": by_page,
    }


_STATBLOCK_STRUCTURAL_HEADING_KEYS = {
    "actions",
    "reactions",
    "legendaryactions",
    "mythicactions",
    "bonusactions",
    "str",
    "dex",
    "con",
    "int",
    "wis",
    "cha",
    "bestiary",
    "daelkyr",
    "dinosaurs",
    "genericnpcs",
    "homunculi",
    "livingspells",
    "overlords",
    "quori",
    "valenaranimals",
}


def _source_statblock_recovery_hints(
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Derive exact-page identities from indexed statblock core chunks.

    This path is deliberately text-only. It lets a visionless Agent recover a
    digital rulebook without running neural OCR over pages whose indexed text
    already proves the card identity and its AC/HP/Speed core.
    """

    by_page: dict[int, list[str]] = {}
    seen_names: set[str] = set()
    for chunk in chunks:
        folded = " ".join(str(chunk.get("content") or "").split()).casefold()
        if not all(label in folded for label in ("armor class", "hit points", "speed")):
            continue
        page_number = chunk.get("page_start")
        if isinstance(page_number, bool) or not isinstance(page_number, int):
            continue
        name = ""
        candidate_headings: list[str] = []
        for raw_heading in list(chunk.get("heading_path") or []):
            heading = " ".join(str(raw_heading).split()).strip()
            heading_key = compact_ascii_key(heading)
            if (
                not heading_key
                or heading_key in _STATBLOCK_STRUCTURAL_HEADING_KEYS
                or heading_key.startswith("chapter")
                or re.match(r"^ch(?:apter)?\d", heading_key)
                or not _valid_statblock_heading(heading)
            ):
                continue
            candidate_headings.append(heading)
        if candidate_headings:
            name = candidate_headings[-1]
            # PDF bookmarks can split a single decorated identity over two
            # consecutive heading nodes (for example, "GITHYANKI SUPREME" /
            # "COMMANDER"). Rejoin a one-token suffix only when its immediate
            # parent is itself a multi-token display heading; broad one-token
            # section labels such as DEVILS or DROW remain untouched.
            if (
                len(name.split()) == 1
                and len(candidate_headings) >= 2
                and len(candidate_headings[-2].split()) >= 2
                and name.upper() == name
                and candidate_headings[-2].upper() == candidate_headings[-2]
            ):
                name = f"{candidate_headings[-2]} {name}"
        if not name:
            continue
        name_key = compact_ascii_key(name)
        if name_key in seen_names:
            continue
        seen_names.add(name_key)
        names = by_page.setdefault(page_number, [])
        if not any(_bounded_ocr_heading_equivalent(name, item) for item in names):
            names.append(name)
    return {
        "entry_count": sum(len(items) for items in by_page.values()),
        "by_page": by_page,
    }


def _agent_evidence_supports_fact(
    fact: str,
    evidence: str,
    *,
    max_edits: int = 2,
) -> bool:
    """Allow bounded OCR glyph repairs while preserving every numeric token."""

    def normalize_ocr_artifacts(value: str) -> str:
        # Common layout-OCR artifacts inside a mechanically complete token.
        # These repairs are deliberately contextual: arbitrary standalone
        # digits remain untouched and are still compared exactly below.
        return re.sub(r"reacl1(?=\d+ft)", "reach", value)

    # Text layers and OCR engines commonly render a slash inside a numeric
    # weapon range as ``f`` (``150f600``).  Compact evidence has already
    # removed the real slash, so discard only this digit-bounded impostor.
    fact = re.sub(r"(?<=\d)f(?=\d)", "", fact)
    evidence = re.sub(r"(?<=\d)f(?=\d)", "", evidence)
    fact = normalize_ocr_artifacts(fact)
    evidence = normalize_ocr_artifacts(evidence)
    if not fact:
        return True
    if fact in evidence:
        return True
    if len(fact) < 12 or not evidence:
        return False

    anchor_width = min(16, max(6, len(fact) // 8))
    anchor_offsets = list(
        dict.fromkeys(
            max(0, min(len(fact) - anchor_width, offset))
            for offset in (
                0,
                len(fact) // 5,
                (len(fact) * 2) // 5,
                (len(fact) * 3) // 5,
                (len(fact) * 4) // 5,
                len(fact) - anchor_width,
            )
        )
    )
    candidate_starts: set[int] = set()
    for offset in anchor_offsets:
        anchor = fact[offset : offset + anchor_width]
        search_from = 0
        while anchor and len(candidate_starts) < 100:
            position = evidence.find(anchor, search_from)
            if position < 0:
                break
            for adjustment in range(-max_edits, max_edits + 1):
                candidate_starts.add(position - offset + adjustment)
            search_from = position + 1

    for start in sorted(candidate_starts):
        if start < 0:
            continue
        for length_adjustment in range(-max_edits, max_edits + 1):
            candidate_length = len(fact) + length_adjustment
            if candidate_length < 1 or start + candidate_length > len(evidence):
                continue
            candidate = evidence[start : start + candidate_length]
            comparable_fact = list(fact)
            comparable_candidate = list(candidate)
            if len(comparable_fact) == len(comparable_candidate):
                for index, (fact_character, candidate_character) in enumerate(
                    zip(comparable_fact, comparable_candidate)
                ):
                    pair = {fact_character, candidate_character}
                    if pair <= {"1", "l", "i"} and "1" in pair:
                        comparable_fact[index] = "1"
                        comparable_candidate[index] = "1"
                    elif pair <= {"0", "o"} and "0" in pair:
                        comparable_fact[index] = "0"
                        comparable_candidate[index] = "0"
                    else:
                        embedded_glyphs = {
                            "3": "e",
                            "4": "a",
                            "5": "s",
                            "7": "t",
                            "8": "b",
                            "9": "o",
                        }
                        candidate_is_glyph = (
                            candidate_character in embedded_glyphs
                            and fact_character == embedded_glyphs[candidate_character]
                            and index > 0
                            and index + 1 < len(comparable_candidate)
                            and comparable_candidate[index - 1].isalpha()
                            and comparable_candidate[index + 1].isalpha()
                        )
                        fact_is_glyph = (
                            fact_character in embedded_glyphs
                            and candidate_character == embedded_glyphs[fact_character]
                            and index > 0
                            and index + 1 < len(comparable_fact)
                            and comparable_fact[index - 1].isalpha()
                            and comparable_fact[index + 1].isalpha()
                        )
                        if candidate_is_glyph or fact_is_glyph:
                            normalized_character = (
                                fact_character if candidate_is_glyph else candidate_character
                            )
                            comparable_fact[index] = normalized_character
                            comparable_candidate[index] = normalized_character
            normalized_fact = "".join(comparable_fact)
            normalized_candidate = "".join(comparable_candidate)
            if re.findall(r"\d+", normalized_candidate) != re.findall(r"\d+", normalized_fact):
                continue
            if (
                _bounded_edit_distance(
                    normalized_fact,
                    normalized_candidate,
                    limit=max_edits,
                )
                <= max_edits
            ):
                return True
    return False


def _has_source_defined_positional_targeting(value: Any) -> bool:
    """Recognize attacks whose source replaces a numeric range with a position."""

    text = _normalize_source_evidence_text(value)
    return bool(
        re.search(
            r"\b(?:target|creature)\b.{0,80}\bdirectly\s+"
            r"(?:below|beneath|above)\b",
            text,
        )
    )


def _source_contains_narrative_name(*, name: str, content: str) -> bool:
    """Match an exact name or a two-part name split by a short source appositive."""

    normalized_name = _normalize_source_evidence_text(name)
    normalized_content = _normalize_source_evidence_text(content)
    if normalized_name in normalized_content:
        return True

    name_parts = re.findall(r"\w+", normalized_name, flags=re.UNICODE)
    if len(name_parts) != 2:
        return False
    split_name = re.compile(
        rf"(?<!\w){re.escape(name_parts[0])}(?!\w)"
        rf"(.{{1,80}}?)"
        rf"(?<!\w){re.escape(name_parts[1])}(?!\w)",
        flags=re.DOTALL,
    )
    return split_name.search(normalized_content) is not None


PHB2014_STANDARD_LANGUAGES = (
    "Common",
    "Dwarvish",
    "Elvish",
    "Giant",
    "Gnomish",
    "Goblin",
    "Halfling",
    "Orc",
)
PHB2014_RESTRICTED_LANGUAGES = (
    "Abyssal",
    "Celestial",
    "Deep Speech",
    "Draconic",
    "Druidic",
    "Infernal",
    "Primordial",
    "Sylvan",
    "Thieves' Cant",
    "Undercommon",
)
PHB2014_TOOL_PROFICIENCIES = (
    "Alchemist's Supplies",
    "Brewer's Supplies",
    "Calligrapher's Supplies",
    "Carpenter's Tools",
    "Cartographer's Tools",
    "Cobbler's Tools",
    "Cook's Utensils",
    "Disguise Kit",
    "Forgery Kit",
    "Glassblower's Tools",
    "Herbalism Kit",
    "Jeweler's Tools",
    "Leatherworker's Tools",
    "Mason's Tools",
    "Navigator's Tools",
    "Painter's Supplies",
    "Poisoner's Kit",
    "Potter's Tools",
    "Smith's Tools",
    "Thieves' Tools",
    "Tinker's Tools",
    "Vehicles (Land)",
    "Vehicles (Water)",
    "Weaver's Tools",
    "Woodcarver's Tools",
)
BACKGROUND_AUTHORITY_SELECTION_KEY = "_background_authority"
CLASS_EQUIPMENT_AUTHORITY_KEY = "_class_equipment_authority"
BACKGROUND_MATERIALIZATION_KEY = "_background_materialization"
SPECIES_MATERIALIZATION_KEY = "_species_materialization"
SPECIES_AUTHORITY_SELECTION_KEY = "_species_authority"


def _class_equipment_records(sheet: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(record)
        for record in dict(sheet.get("content") or {}).get("selections", [])
        if record.get("kind") == "class"
        and (
            "starting_equipment" in dict(record.get("selection") or {})
            or "starting_equipment_result" in dict(record.get("selection") or {})
            or CLASS_EQUIPMENT_AUTHORITY_KEY in dict(record.get("selection") or {})
        )
    ]


def _class_equipment_authority_payload(
    record: Mapping[str, Any], *, character_id: str
) -> dict[str, Any]:
    selection = deepcopy(dict(record.get("selection") or {}))
    selection.pop(CLASS_EQUIPMENT_AUTHORITY_KEY, None)
    return {
        "schema_version": 1,
        "purpose": "class_starting_equipment",
        "character_id": character_id,
        "artifact_id": record["artifact_id"],
        "pack_id": record["pack_id"],
        "pack_version": record["pack_version"],
        "class_name": record["name"],
        "selection_checksum": json_sha256(selection),
    }


def _require_authoritative_class_equipment(
    sheet: Mapping[str, Any],
    *,
    character_id: str | None,
    secret: bytes,
    current_sheet: Mapping[str, Any] | None = None,
) -> None:
    records = _class_equipment_records(sheet)
    previous = _class_equipment_records(current_sheet or {})
    if previous and not records:
        raise ValueError("class starting-equipment authority cannot be removed")
    if not records:
        return
    if character_id is None or len(records) != 1:
        raise ValueError("class starting equipment requires one actor-bound selection")
    record = records[0]
    signature = dict(record.get("selection") or {}).get(CLASS_EQUIPMENT_AUTHORITY_KEY)
    payload = verify_receipt_signature(
        signature,
        secret,
        missing_error="class starting-equipment authority is missing",
        invalid_error="class starting-equipment authority is invalid",
    )
    if payload != _class_equipment_authority_payload(record, character_id=character_id):
        raise ValueError("class starting-equipment authority does not match the selection")
    if not any(
        str(item.get("name") or "").casefold() == str(record["name"]).casefold()
        for item in dict(sheet.get("progression") or {}).get("classes", [])
    ):
        raise ValueError("class starting-equipment selection requires its source class")


def _class_gold_replaces_background(sheet: Mapping[str, Any]) -> bool:
    return any(
        dict(dict(record.get("selection") or {}).get("starting_equipment_result") or {}).get(
            "replaces_background_equipment"
        )
        is True
        for record in _class_equipment_records(sheet)
    )


def _load_or_create_content_authority_secret(path: Path) -> bytes:
    """Load the durable server key used for actor-bound content receipts."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        value = path.read_bytes()
        if len(value) != 32:
            raise RuntimeError("content authority key is invalid")
        return value
    value = secrets.token_bytes(32)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
            return value
        except FileExistsError:
            published = path.read_bytes()
            if len(published) != 32:
                raise RuntimeError("content authority key is invalid")
            return published
    finally:
        temporary.unlink(missing_ok=True)


def _background_selection_records(sheet: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in dict(sheet.get("content") or {}).get("selections", [])
        if isinstance(item, Mapping) and str(item.get("kind") or "").casefold() == "background"
    ]


def _content_projection_snapshot(sheet: Mapping[str, Any], kind: str) -> dict[str, Any]:
    """Capture the actor fields a source selection may project.

    The snapshot is kept in the server-authored selection receipt.  Replacement
    uses it as a compare-and-swap guard: only values that still have the exact
    source projection are removed, while unrelated later grants remain intact.
    """

    progression = dict(sheet.get("progression") or {})
    traits = dict(sheet.get("traits") or {})
    proficiencies = dict(traits.get("proficiencies") or {})
    inventory = dict(sheet.get("inventory") or {})
    combat = dict(sheet.get("combat") or {})
    content = dict(sheet.get("content") or {})
    snapshot: dict[str, Any] = {
        "kind": kind,
        "progression": {
            "background": deepcopy(progression.get("background") or ""),
            "background_grants": deepcopy(progression.get("background_grants") or {}),
            "species": deepcopy(progression.get("species") or ""),
            "species_grants": deepcopy(progression.get("species_grants") or {}),
        },
        "abilities": {
            str(name): int(dict(value).get("score", 0) or 0)
            for name, value in dict(sheet.get("abilities") or {}).items()
            if isinstance(value, Mapping)
        },
        "skills": {
            str(name): str(dict(value).get("proficiency") or "none")
            for name, value in dict(sheet.get("skills") or {}).items()
            if isinstance(value, Mapping)
        },
        "combat": {
            "hp": deepcopy(combat.get("hp") or {}),
            "speed": deepcopy(combat.get("speed") or {}),
        },
        "traits": {
            "size": deepcopy(traits.get("size") or ""),
            "languages": deepcopy(traits.get("languages") or []),
            "intrinsic_attacks": deepcopy(traits.get("intrinsic_attacks") or []),
            "resistances": deepcopy(traits.get("resistances") or []),
            "immunities": deepcopy(traits.get("immunities") or []),
            "condition_immunities": deepcopy(traits.get("condition_immunities") or []),
            "senses": deepcopy(traits.get("senses") or {}),
            "proficiencies": {
                field: deepcopy(proficiencies.get(field) or [])
                for field in ("armor", "weapons", "tools", "tool_expertise")
            },
        },
        "inventory": {
            "items": deepcopy(inventory.get("items") or []),
            "wallet": deepcopy(inventory.get("wallet") or {}),
            "equipment_slots": deepcopy(inventory.get("equipment_slots") or {}),
        },
        "resources": deepcopy(dict(sheet.get("resources") or {})),
        "content": {
            section: deepcopy(content.get(section) or [])
            for section in ("spells", "features", "feats", "activities")
        },
        "effects": deepcopy(sheet.get("effects") or []),
    }
    return snapshot


def _content_projection_receipt(
    before: Mapping[str, Any], after: Mapping[str, Any], *, kind: str
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": kind,
        # Keep the signed receipt shallow enough for the public MCP argument
        # schema when callers later submit the complete character card.
        "before": json.dumps(
            _content_projection_snapshot(before, kind),
            sort_keys=True,
            separators=(",", ":"),
        ),
        "after": json.dumps(
            _content_projection_snapshot(after, kind),
            sort_keys=True,
            separators=(",", ":"),
        ),
    }


def _projection_list_items(before: list[Any], after: list[Any]) -> list[Any]:
    """Return one copy of every list member newly projected by a grant."""

    remaining = deepcopy(before)
    added: list[Any] = []
    for value in after:
        try:
            index = next(index for index, item in enumerate(remaining) if item == value)
        except StopIteration:
            added.append(deepcopy(value))
        else:
            remaining.pop(index)
    return added


def _projection_remove_exact_list(current: list[Any], *, added: list[Any], label: str) -> None:
    for value in added:
        try:
            index = next(index for index, item in enumerate(current) if item == value)
        except StopIteration as error:
            raise ValueError(f"{label} changed or left source custody") from error
        current.pop(index)


def _projection_content_items(
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, tuple[dict[str, Any], dict[str, Any]]]]:
    before_by_id = {
        str(item.get("id")): item
        for item in before
        if isinstance(item, Mapping) and str(item.get("id") or "")
    }
    created: list[dict[str, Any]] = []
    changed: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for item in after:
        if not isinstance(item, Mapping):
            continue
        item_id = str(item.get("id") or "")
        if not item_id or item_id not in before_by_id:
            created.append(deepcopy(dict(item)))
        elif dict(before_by_id[item_id]) != dict(item):
            changed[item_id] = (deepcopy(dict(before_by_id[item_id])), deepcopy(dict(item)))
    return created, changed


def _remove_content_projection(
    sheet: dict[str, Any], record: Mapping[str, Any], *, kind: str
) -> None:
    """Atomically remove one prior background/species projection.

    Every owned value is checked against the server-authored after snapshot
    before it is removed.  This makes spent, transferred, manually changed, or
    forged projections fail without changing the candidate sheet.
    """

    key = BACKGROUND_MATERIALIZATION_KEY if kind == "background" else SPECIES_MATERIALIZATION_KEY
    raw = dict(record.get("selection") or {}).get(key)
    if not isinstance(raw, Mapping) or int(raw.get("schema_version", 0) or 0) != 1:
        raise ValueError(f"{kind} replacement requires a source materialization receipt")
    try:
        raw_before = raw.get("before")
        raw_after = raw.get("after")
        before = (
            deepcopy(dict(raw_before))
            if isinstance(raw_before, Mapping)
            else json.loads(str(raw_before or "{}"))
        )
        after = (
            deepcopy(dict(raw_after))
            if isinstance(raw_after, Mapping)
            else json.loads(str(raw_after or "{}"))
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"{kind} replacement receipt is invalid") from error
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError(f"{kind} replacement receipt is invalid")
    if before.get("kind") != kind or after.get("kind") != kind:
        raise ValueError(f"{kind} replacement receipt is invalid")

    current = _content_projection_snapshot(sheet, kind)
    before_progression = dict(before.get("progression") or {})
    after_progression = dict(after.get("progression") or {})
    current_progression = dict(current.get("progression") or {})
    progression_field = "background" if kind == "background" else "species"
    grants_field = "background_grants" if kind == "background" else "species_grants"
    if current_progression.get(grants_field) != after_progression.get(grants_field):
        raise ValueError(f"{kind} grants changed or left source custody")
    current_progression[progression_field] = before_progression.get(progression_field, "")
    current_progression[grants_field] = deepcopy(before_progression.get(grants_field) or {})
    sheet["progression"][progression_field] = current_progression[progression_field]
    sheet["progression"][grants_field] = current_progression[grants_field]

    current_abilities = dict(current.get("abilities") or {})
    before_abilities = dict(before.get("abilities") or {})
    after_abilities = dict(after.get("abilities") or {})
    for name, after_score in after_abilities.items():
        before_score = int(before_abilities.get(name, after_score) or 0)
        delta = int(after_score or 0) - before_score
        if not delta:
            continue
        value = int(current_abilities.get(name, after_score) or 0)
        if value < int(after_score or 0):
            raise ValueError(f"{kind} ability projection changed or left source custody")
        sheet["abilities"][name]["score"] = value - delta

    current_skills = dict(current.get("skills") or {})
    before_skills = dict(before.get("skills") or {})
    after_skills = dict(after.get("skills") or {})
    for name, after_value in after_skills.items():
        before_value = str(before_skills.get(name, after_value) or "none")
        if before_value == after_value:
            continue
        if str(current_skills.get(name) or "none") != str(after_value):
            raise ValueError(f"{kind} skill projection changed or left source custody")
        sheet["skills"][name]["proficiency"] = before_value

    current_combat = dict(current.get("combat") or {})
    before_combat = dict(before.get("combat") or {})
    after_combat = dict(after.get("combat") or {})
    for field in ("hp", "speed"):
        if before_combat.get(field) == after_combat.get(field):
            continue
        if current_combat.get(field) != after_combat.get(field):
            raise ValueError(f"{kind} combat projection changed or left source custody")
        sheet["combat"][field] = deepcopy(before_combat.get(field) or {})

    current_traits = dict(current.get("traits") or {})
    before_traits = dict(before.get("traits") or {})
    after_traits = dict(after.get("traits") or {})
    for field in (
        "languages",
        "intrinsic_attacks",
        "resistances",
        "immunities",
        "condition_immunities",
    ):
        current_list = list(current_traits.get(field) or [])
        before_list = list(before_traits.get(field) or [])
        after_list = list(after_traits.get(field) or [])
        _projection_remove_exact_list(
            current_list,
            added=_projection_list_items(before_list, after_list),
            label=f"{kind} {field}",
        )
        sheet["traits"][field] = current_list
    if before_traits.get("size") != after_traits.get("size"):
        if current_traits.get("size") != after_traits.get("size"):
            raise ValueError(f"{kind} size projection changed or left source custody")
        sheet["traits"]["size"] = deepcopy(before_traits.get("size") or "")
    if before_traits.get("senses") != after_traits.get("senses"):
        if current_traits.get("senses") != after_traits.get("senses"):
            raise ValueError(f"{kind} senses projection changed or left source custody")
        sheet["traits"]["senses"] = deepcopy(before_traits.get("senses") or {})
    before_prof = dict(before_traits.get("proficiencies") or {})
    after_prof = dict(after_traits.get("proficiencies") or {})
    for field in ("armor", "weapons", "tools", "tool_expertise"):
        current_list = list(dict(current_traits.get("proficiencies") or {}).get(field) or [])
        added = _projection_list_items(
            list(before_prof.get(field) or []), list(after_prof.get(field) or [])
        )
        _projection_remove_exact_list(current_list, added=added, label=f"{kind} {field}")
        sheet["traits"]["proficiencies"][field] = current_list

    current_inventory = dict(current.get("inventory") or {})
    before_inventory = dict(before.get("inventory") or {})
    after_inventory = dict(after.get("inventory") or {})
    current_items = list(current_inventory.get("items") or [])
    created_items, changed_items = _projection_content_items(
        list(before_inventory.get("items") or []), list(after_inventory.get("items") or [])
    )
    for item in created_items:
        _projection_remove_exact_list(current_items, added=[item], label=f"{kind} inventory")
    current_item_by_id = {
        str(item.get("id")): item for item in current_items if isinstance(item, Mapping)
    }
    for item_id, (old_item, after_item) in changed_items.items():
        if current_item_by_id.get(item_id) != after_item:
            raise ValueError(f"{kind} inventory projection changed or left source custody")
        current_items[current_items.index(after_item)] = old_item
    sheet["inventory"]["items"] = current_items
    before_wallet = dict(before_inventory.get("wallet") or {})
    after_wallet = dict(after_inventory.get("wallet") or {})
    current_wallet = dict(sheet["inventory"].get("wallet") or {})
    for denomination, after_amount in after_wallet.items():
        before_amount = int(before_wallet.get(denomination, after_amount) or 0)
        delta = int(after_amount or 0) - before_amount
        if not delta:
            continue
        current_amount = int(current_wallet.get(denomination, 0) or 0)
        if current_amount < int(after_amount or 0):
            raise ValueError(f"{kind} wallet projection changed or left source custody")
        current_wallet[denomination] = current_amount - delta
    sheet["inventory"]["wallet"] = current_wallet
    if before_inventory.get("equipment_slots") != after_inventory.get("equipment_slots"):
        if sheet["inventory"].get("equipment_slots") != after_inventory.get("equipment_slots"):
            raise ValueError(f"{kind} equipment projection changed or left source custody")
        sheet["inventory"]["equipment_slots"] = deepcopy(
            before_inventory.get("equipment_slots") or {}
        )

    before_resources = dict(before.get("resources") or {})
    after_resources = dict(after.get("resources") or {})
    current_resources = dict(sheet.get("resources") or {})
    for key, value in after_resources.items():
        if key not in before_resources:
            if current_resources.get(key) != value:
                raise ValueError(f"{kind} resource projection changed or left source custody")
            current_resources.pop(key, None)
        elif before_resources[key] != value:
            if current_resources.get(key) != value:
                raise ValueError(f"{kind} resource projection changed or left source custody")
            current_resources[key] = deepcopy(before_resources[key])
    sheet["resources"] = current_resources

    for section in ("spells", "features", "feats", "activities"):
        before_items = list(dict(before.get("content") or {}).get(section) or [])
        after_items = list(dict(after.get("content") or {}).get(section) or [])
        created, changed = _projection_content_items(before_items, after_items)
        current_items = list(sheet["content"].get(section) or [])
        for item in created:
            _projection_remove_exact_list(current_items, added=[item], label=f"{kind} {section}")
        by_id = {str(item.get("id")): item for item in current_items if isinstance(item, Mapping)}
        for item_id, (old_item, after_item) in changed.items():
            if by_id.get(item_id) != after_item:
                raise ValueError(f"{kind} {section} projection changed or left source custody")
            current_items[current_items.index(after_item)] = old_item
        sheet["content"][section] = current_items

    before_effects = list(before.get("effects") or [])
    after_effects = list(after.get("effects") or [])
    current_effects = list(sheet.get("effects") or [])
    _projection_remove_exact_list(
        current_effects,
        added=_projection_list_items(before_effects, after_effects),
        label=f"{kind} effects",
    )
    sheet["effects"] = current_effects


def _has_background_grants(sheet: Mapping[str, Any]) -> bool:
    progression = dict(sheet.get("progression") or {})
    grants = dict(progression.get("background_grants") or {})
    return bool(
        str(grants.get("feature") or "").strip()
        or list(grants.get("equipment_item_ids") or [])
        or list(grants.get("languages") or [])
        or list(grants.get("spell_list_expansion") or [])
        or list(grants.get("tools") or [])
        or dict(grants.get("choices") or {})
    )


def _species_selection_records(sheet: Mapping[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in dict(sheet.get("content") or {}).get("selections", [])
        if isinstance(item, Mapping) and str(item.get("kind") or "").casefold() == "species"
    ]


def _has_species_grants(sheet: Mapping[str, Any] | None) -> bool:
    progression = dict((sheet or {}).get("progression") or {})
    grants = dict(progression.get("species_grants") or {})
    return any(value not in (None, "", [], {}, False, 0) for value in grants.values())


def _species_authority_payload(
    sheet: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    character_id: str,
    authority_id: str,
) -> dict[str, Any]:
    selection = deepcopy(dict(record.get("selection") or {}))
    selection.pop(SPECIES_AUTHORITY_SELECTION_KEY, None)
    progression = dict(sheet.get("progression") or {})
    return {
        "schema_version": 1,
        "purpose": "species_selection_authority",
        "authority_id": authority_id,
        "character_id": character_id,
        "artifact_id": str(record.get("artifact_id") or ""),
        "pack_id": str(record.get("pack_id") or ""),
        "pack_version": str(record.get("pack_version") or ""),
        "species": str(progression.get("species") or ""),
        "species_grants_checksum": json_sha256(dict(progression.get("species_grants") or {})),
        "selection_checksum": json_sha256(selection),
    }


def _require_authoritative_species_state(
    candidate_sheet: Mapping[str, Any],
    *,
    character_id: str | None,
    secret: bytes,
    current_sheet: Mapping[str, Any] | None = None,
) -> None:
    """Reject forged, removed, or altered official species materialization."""

    records = _species_selection_records(candidate_sheet)
    traits = dict(candidate_sheet.get("traits") or {})
    intrinsic_attacks = list(traits.get("intrinsic_attacks") or [])
    meaningful = bool(_has_species_grants(candidate_sheet) or intrinsic_attacks or records)
    current_traits = dict((current_sheet or {}).get("traits") or {})
    current_protected = bool(
        current_sheet
        and (
            _has_species_grants(current_sheet)
            or list(current_traits.get("intrinsic_attacks") or [])
            or _species_selection_records(current_sheet)
        )
    )
    if current_protected and not (meaningful or records):
        raise ValueError("authoritative species state cannot be removed by sheet ingress")
    if not meaningful and not records:
        return
    if not intrinsic_attacks:
        # Legacy actor archives may carry a source species projection without
        # the post-#182 authority receipt.  Preserve those ordinary species
        # grants; intrinsic anatomy must still enter through content apply.
        return
    if character_id is None:
        raise ValueError(
            "species grants can be created only by character_content_apply after actor creation; "
            "pre-materialized species state is not accepted"
        )
    if len(records) != 1:
        raise ValueError("authoritative species state requires one species selection receipt")
    record = records[0]
    raw_authority = dict(record.get("selection") or {}).get(SPECIES_AUTHORITY_SELECTION_KEY)
    if not isinstance(raw_authority, Mapping):
        raise ValueError("species selection authority receipt is missing")
    authority = dict(raw_authority)
    authority_id = str(authority.get("authority_id") or "")
    if not authority_id or not isinstance(authority.get("authorization"), Mapping):
        raise ValueError("species selection authority receipt is invalid")
    try:
        payload = verify_receipt_signature(
            authority["authorization"],
            secret,
            missing_error="species selection authority signature is missing",
            invalid_error="species selection authority signature is invalid",
        )
    except ValueError as error:
        raise ValueError("species selection authority receipt is invalid") from error
    expected = _species_authority_payload(
        candidate_sheet,
        record,
        character_id=character_id,
        authority_id=authority_id,
    )
    if payload != expected:
        raise ValueError("species selection authority receipt does not match the actor state")
    artifact_id = str(record.get("artifact_id") or "")
    pack_id = str(record.get("pack_id") or "")
    pack_version = str(record.get("pack_version") or "")
    for attack in traits.get("intrinsic_attacks") or []:
        source = dict(dict(attack).get("source") or {})
        if (
            source.get("artifact_id") != artifact_id
            or source.get("pack_id") != pack_id
            or source.get("pack_version") != pack_version
        ):
            raise ValueError("species intrinsic attack does not match its source selection")


def _background_authority_payload(
    sheet: Mapping[str, Any],
    record: Mapping[str, Any],
    *,
    character_id: str,
    authority_id: str,
) -> dict[str, Any]:
    selection = deepcopy(dict(record.get("selection") or {}))
    selection.pop(BACKGROUND_AUTHORITY_SELECTION_KEY, None)
    progression = dict(sheet.get("progression") or {})
    return {
        "schema_version": 1,
        "purpose": "background_selection_authority",
        "authority_id": authority_id,
        "character_id": character_id,
        "artifact_id": str(record.get("artifact_id") or ""),
        "pack_id": str(record.get("pack_id") or ""),
        "pack_version": str(record.get("pack_version") or ""),
        "background": str(progression.get("background") or ""),
        "background_grants_checksum": json_sha256(dict(progression.get("background_grants") or {})),
        "selection_checksum": json_sha256(selection),
    }


def _require_authoritative_background_state(
    candidate_sheet: Mapping[str, Any],
    *,
    character_id: str | None,
    secret: bytes,
    current_sheet: Mapping[str, Any] | None = None,
) -> None:
    """Reject forged, removed, or altered official background materialization."""

    _require_authoritative_class_equipment(
        candidate_sheet, character_id=character_id, secret=secret, current_sheet=current_sheet
    )

    records = _background_selection_records(candidate_sheet)
    meaningful = _has_background_grants(candidate_sheet)
    current_protected = bool(
        current_sheet
        and (_has_background_grants(current_sheet) or _background_selection_records(current_sheet))
    )
    if current_protected and not (meaningful or records):
        raise ValueError("authoritative background state cannot be removed by sheet ingress")
    if not meaningful and not records:
        return
    if character_id is None:
        raise ValueError(
            "background grants can be created only by character_content_apply after actor creation"
        )
    if len(records) != 1:
        raise ValueError("authoritative background state requires one background selection receipt")
    record = records[0]
    raw_authority = dict(record.get("selection") or {}).get(BACKGROUND_AUTHORITY_SELECTION_KEY)
    if not isinstance(raw_authority, Mapping):
        raise ValueError("background selection authority receipt is missing")
    authority = dict(raw_authority)
    authority_id = str(authority.get("authority_id") or "")
    if not authority_id or not isinstance(authority.get("authorization"), Mapping):
        raise ValueError("background selection authority receipt is invalid")
    try:
        payload = verify_receipt_signature(
            authority["authorization"],
            secret,
            missing_error="background selection authority signature is missing",
            invalid_error="background selection authority signature is invalid",
        )
    except ValueError as error:
        raise ValueError("background selection authority receipt is invalid") from error
    expected = _background_authority_payload(
        candidate_sheet,
        record,
        character_id=character_id,
        authority_id=authority_id,
    )
    if payload != expected:
        raise ValueError("background selection authority receipt does not match the actor state")

    progression = dict(candidate_sheet.get("progression") or {})
    grants = dict(progression.get("background_grants") or {})
    choices = dict(grants.get("choices") or {})
    skills = dict(candidate_sheet.get("skills") or {})
    for skill in choices.get("effective_skills", []):
        skill_key = str(skill).casefold().replace(" ", "_")
        if str(dict(skills.get(skill_key) or {}).get("proficiency") or "none") == "none":
            raise ValueError("background selection receipt is missing a granted skill")
    known_languages = {
        str(item).casefold()
        for item in dict(candidate_sheet.get("traits") or {}).get("languages", [])
    }
    if any(str(item).casefold() not in known_languages for item in grants.get("languages", [])):
        raise ValueError("background selection receipt is missing a granted language")
    known_tools = {
        str(item).casefold()
        for item in dict(dict(candidate_sheet.get("traits") or {}).get("proficiencies") or {}).get(
            "tools", []
        )
    }
    if any(str(item).casefold() not in known_tools for item in grants.get("tools", [])):
        raise ValueError("background selection receipt is missing a granted tool")


class _PendingProficiencyReplacementsError(ValueError):
    def __init__(self, kind: str, duplicates: list[str]) -> None:
        self.kind = kind
        self.duplicates = duplicates
        message = f"{kind} proficiency replacements are required for: {', '.join(duplicates)}"
        super().__init__(message)


def _resolve_duplicate_proficiencies(
    source_values: Any,
    *,
    existing_values: set[str],
    replacements: Any,
    options: Mapping[str, str],
    kind: str,
) -> tuple[list[str], dict[str, str]]:
    if not isinstance(source_values, list):
        raise RulesetUnavailableError(f"background {kind} grants are not executable")
    normalized_sources = [" ".join(str(item).split()) for item in source_values]
    if any(not item for item in normalized_sources):
        raise RulesetUnavailableError(f"background {kind} grants must be non-empty")
    seen = set(existing_values)
    collision_counts: dict[str, int] = {}
    collisions: list[tuple[int, str, str]] = []
    for index, item in enumerate(normalized_sources):
        source_key = item.casefold()
        if source_key in seen:
            collision_counts[source_key] = collision_counts.get(source_key, 0) + 1
            collision_number = collision_counts[source_key]
            receipt_key = (
                source_key if collision_number == 1 else f"{source_key}#{collision_number}"
            )
            collisions.append((index, item, receipt_key))
        seen.add(source_key)
    duplicate_sources = [item for _index, item, _receipt_key in collisions]
    duplicate_prompts = [
        item if receipt_key == item.casefold() else f"{item} ({receipt_key})"
        for _index, item, receipt_key in collisions
    ]
    expected_replacement_keys = {receipt_key for _index, _item, receipt_key in collisions}
    if replacements is None:
        if duplicate_sources:
            raise _PendingProficiencyReplacementsError(kind, duplicate_prompts)
        normalized_replacements: dict[str, str] = {}
    else:
        if not isinstance(replacements, dict):
            raise ValueError(f"background {kind}_replacements must be an object")
        normalized_replacements = {}
        for raw_source, raw_replacement in replacements.items():
            source = " ".join(str(raw_source).split()).casefold()
            replacement = " ".join(str(raw_replacement).split())
            if not source or not replacement or source in normalized_replacements:
                raise ValueError(
                    f"background {kind}_replacements must contain distinct non-empty names"
                )
            replacement_key = replacement.casefold()
            if replacement_key not in options:
                raise ValueError(f"background {kind} replacement is not an allowed {kind}")
            normalized_replacements[source] = options[replacement_key]
        if set(normalized_replacements) != expected_replacement_keys:
            raise ValueError(
                f"background {kind}_replacements must answer exactly the duplicate grants"
            )
    replacements_by_index = {
        index: normalized_replacements[receipt_key] for index, _item, receipt_key in collisions
    }
    effective = [
        replacements_by_index.get(index, options.get(item.casefold(), item))
        for index, item in enumerate(normalized_sources)
    ]
    effective_keys = [item.casefold() for item in effective]
    if len(set(effective_keys)) != len(effective_keys):
        raise ValueError(f"background effective {kind} proficiencies must be distinct")
    if any(item in existing_values for item in effective_keys):
        raise ValueError(f"background {kind} replacement is already proficient")
    return effective, normalized_replacements


def _reviewed_tool_options(
    candidates: list[tuple[str, str, dict[str, Any]]],
) -> dict[str, str]:
    values = {
        item.casefold(): item
        for item in (
            *PHB2014_TOOL_PROFICIENCIES,
            "Dice Set",
            "Dragonchess Set",
            "Gaming Set",
            "Playing Card Set",
            "Three-Dragon Ante Set",
            "Musical Instrument",
        )
    }
    for _pack_id, _version, artifact in candidates:
        card = dict(artifact.get("card") or {})
        containers = [
            dict(card.get("background_grants") or {}),
            dict(card.get("class_definition") or {}),
            dict(card.get("grants") or {}),
            dict(card.get("mechanical_grants") or {}),
        ]
        for container in containers:
            choices = dict(container.get("choices") or {})
            for field in (
                "tools",
                "tool_proficiencies",
                "tool_options",
                "tool_choices",
            ):
                raw_items = [
                    *list(container.get(field) or []),
                    *list(choices.get(field) or []),
                ]
                for raw_item in raw_items:
                    item = " ".join(str(raw_item).split())
                    if item:
                        values.setdefault(item.casefold(), item)
    return values


def _campaign_language_options(settings: Mapping[str, Any]) -> dict[str, str]:
    raw_catalog = settings.get("language_catalog")
    if raw_catalog is None:
        raw_allowed: Any = list(PHB2014_STANDARD_LANGUAGES)
    elif isinstance(raw_catalog, Mapping) and set(raw_catalog) == {"allowed_languages"}:
        raw_allowed = raw_catalog.get("allowed_languages")
    else:
        raise RulesetUnavailableError(
            "campaign language_catalog must contain exactly allowed_languages"
        )
    if not isinstance(raw_allowed, list) or len(raw_allowed) > 100:
        raise RulesetUnavailableError("campaign allowed language catalog is invalid")
    options: dict[str, str] = {}
    for raw_item in raw_allowed:
        item = " ".join(str(raw_item).split())
        if not item or len(item) > 100 or item.casefold() in options:
            raise RulesetUnavailableError(
                "campaign allowed languages must be distinct non-empty names"
            )
        options[item.casefold()] = item
    return options


def _validated_background_languages(
    raw_languages: Any,
    *,
    count: int,
    fixed: Any,
    existing: Any,
    campaign_id: str,
    campaign_revision: int,
    campaign_settings: Mapping[str, Any],
    authorization: Any,
    principal_id: str,
    principal_is_dm: bool,
) -> tuple[list[str], list[str], dict[str, Any] | None]:
    fixed_values = _validated_distinct_choices(
        fixed,
        count=len(fixed) if isinstance(fixed, list) else 0,
        label="fixed background language",
    )
    selected = _validated_distinct_choices(
        raw_languages,
        count=count,
        label="background language",
    )
    existing_map = {
        str(item).casefold(): str(item)
        for item in (existing if isinstance(existing, list) else [])
        if str(item).strip()
    }
    fixed_keys = {item.casefold() for item in fixed_values}
    selected_keys = {item.casefold() for item in selected}
    if fixed_keys & selected_keys:
        raise ValueError("background language cannot duplicate a fixed grant")
    if (fixed_keys | selected_keys) & set(existing_map):
        raise ValueError("background languages must be new to the character")
    catalog = _campaign_language_options(campaign_settings)
    restricted = {item.casefold() for item in PHB2014_RESTRICTED_LANGUAGES}
    needs_authorization = [
        item for item in selected if item.casefold() not in catalog or item.casefold() in restricted
    ]
    authorization_receipt: dict[str, Any] | None = None
    if needs_authorization:
        if not principal_is_dm:
            raise PermissionError(
                "campaign-external, exotic, and secret background languages require the DM"
            )
        if not isinstance(authorization, dict) or set(authorization) != {
            "languages",
            "reason",
        }:
            raise ValueError("language_authorization requires exactly languages and reason")
        authorized = _validated_distinct_choices(
            authorization.get("languages"),
            count=len(needs_authorization),
            label="authorized background language",
        )
        if {item.casefold() for item in authorized} != {
            item.casefold() for item in needs_authorization
        }:
            raise ValueError("language_authorization must cover exactly the restricted selections")
        reason = " ".join(str(authorization.get("reason") or "").split())
        if not reason or len(reason) > 1000:
            raise ValueError("language_authorization reason must contain 1 to 1000 characters")
        authorization_receipt = {
            "schema_version": 1,
            "kind": "dm_language_authorization",
            "campaign_id": campaign_id,
            "campaign_revision": campaign_revision,
            "principal_id": principal_id,
            "languages": sorted(needs_authorization, key=str.casefold),
            "reason": reason,
            "catalog_checksum": json_sha256(catalog),
        }
    elif authorization is not None:
        raise ValueError("language_authorization is accepted only for restricted language choices")
    normalized_selected = [catalog.get(item.casefold(), item) for item in selected]
    return normalized_selected, [*fixed_values, *normalized_selected], authorization_receipt


def _validated_distinct_choices(value: Any, *, count: int, label: str) -> list[str]:
    if value is None:
        values: list[Any] = []
    elif isinstance(value, list):
        values = value
    else:
        raise ValueError(f"{label} must be a list")
    normalized = [str(item).strip() for item in values]
    if len(normalized) != count or any(not item for item in normalized):
        raise ValueError(f"{label} requires exactly {count} choices")
    if len({item.casefold() for item in normalized}) != len(normalized):
        raise ValueError(f"{label} choices must be distinct")
    return normalized


def _validated_additive_choices(
    value: Any,
    *,
    count: int,
    label: str,
    fixed: Any,
    options: Any,
    allow_unlisted: bool = False,
    allow_fixed_duplicates: bool = False,
) -> tuple[list[str], list[str]]:
    """Validate a choice grant without replacing or duplicating fixed grants."""

    fixed_values = _validated_distinct_choices(
        fixed,
        count=len(fixed) if isinstance(fixed, list) else 0,
        label=f"fixed {label}",
    )
    selected = _validated_distinct_choices(value, count=count, label=label)
    fixed_keys = {item.casefold() for item in fixed_values}
    if not allow_fixed_duplicates and fixed_keys.intersection(item.casefold() for item in selected):
        raise ValueError(f"{label} cannot duplicate a fixed grant")

    if not isinstance(options, list):
        raise RulesetUnavailableError(f"{label} options are not executable")
    option_map: dict[str, str] = {}
    for raw_option in options:
        option = str(raw_option).strip()
        if not option:
            raise RulesetUnavailableError(f"{label} options contain an empty value")
        option_key = option.casefold()
        if option_key in option_map:
            raise RulesetUnavailableError(f"{label} options must be distinct")
        option_map[option_key] = option
    if count and not option_map and not allow_unlisted:
        raise RulesetUnavailableError(
            f"{label} requires reviewed options or an explicit unrestricted choice"
        )
    if option_map:
        if any(item.casefold() not in option_map for item in selected):
            raise ValueError(f"{label} is not one of the allowed options")
        selected = [option_map[item.casefold()] for item in selected]
    combined = [*fixed_values, *selected]
    return selected, combined


def _validate_group_limited_choices(selected: list[str], *, groups: Any, label: str) -> None:
    """Enforce reviewed category caps without weakening the flat option list."""

    if not isinstance(groups, list):
        raise RulesetUnavailableError(f"{label} option groups are not executable")
    selected_keys = [item.casefold() for item in selected]
    covered: set[str] = set()
    for raw_group in groups:
        if not isinstance(raw_group, dict):
            raise RulesetUnavailableError(f"{label} option group is not executable")
        group_options = {
            str(item).strip().casefold()
            for item in raw_group.get("options", [])
            if str(item).strip()
        }
        maximum = int(raw_group.get("maximum", 0) or 0)
        if sum(item in group_options for item in selected_keys) > maximum:
            raise ValueError(
                f"{label} choices exceed the reviewed group limit: {raw_group.get('id') or 'group'}"
            )
        covered.update(group_options)
    if any(item not in covered for item in selected_keys):
        raise RulesetUnavailableError(f"{label} option groups do not cover a selected option")


def _append_selected_proficiencies(selected: list[str], *, target: list[str], label: str) -> None:
    known = {str(item).casefold() for item in target}
    for item in selected:
        if item.casefold() in known:
            raise ValueError(f"feature {label} choice is already proficient")
        target.append(item)
        known.add(item.casefold())


def _apply_skill_proficiency_or_expertise(sheet: dict[str, Any], proficiencies: Any) -> None:
    if not isinstance(proficiencies, list):
        raise RulesetUnavailableError(
            "feature skill proficiency-or-expertise grants are not executable"
        )
    for raw_skill in proficiencies:
        skill_key = str(raw_skill).strip().casefold().replace(" ", "_")
        skill = sheet["skills"].get(skill_key)
        if skill is None:
            raise ValueError(f"feature references an unknown skill: {raw_skill}")
        skill["proficiency"] = "proficient" if skill.get("proficiency") == "none" else "expertise"


def _apply_fixed_skill_proficiency(sheet: dict[str, Any], skill_name: str, *, source: str) -> None:
    skill = sheet["skills"].get(skill_name)
    if skill is None:
        raise ValueError(f"{source} references an unknown skill: {skill_name}")
    if skill.get("proficiency") in {"none", "half"}:
        skill["proficiency"] = "proficient"


def _materialize_feature_proficiency_groups(
    sheet: dict[str, Any], *, value: Any, groups: Any
) -> dict[str, list[str]]:
    if not isinstance(groups, list) or not groups:
        raise RulesetUnavailableError("feature proficiency groups are not executable")
    if not isinstance(value, dict):
        raise ValueError("feature proficiency choices must be an object")
    group_map: dict[str, dict[str, Any]] = {}
    for raw_group in groups:
        if not isinstance(raw_group, dict):
            raise RulesetUnavailableError("feature proficiency group is not executable")
        group_id = str(raw_group.get("id") or "").strip()
        if not group_id or group_id.casefold() in group_map:
            raise RulesetUnavailableError(
                "feature proficiency group ids must be distinct and non-empty"
            )
        group_map[group_id.casefold()] = raw_group
    supplied = {str(key).casefold(): raw for key, raw in value.items()}
    if set(supplied) != set(group_map):
        raise ValueError("feature proficiency choices must answer every reviewed group")

    result: dict[str, list[str]] = {}
    for group_key, group in group_map.items():
        group_id = str(group["id"])
        selected = _validated_distinct_choices(
            supplied[group_key],
            count=int(group.get("count", 0) or 0),
            label=f"feature proficiency group {group_id}",
        )
        option_map = {
            str(item).strip().casefold(): str(item).strip()
            for item in group.get("options", [])
            if str(item).strip()
        }
        if len(option_map) != len(group.get("options", [])):
            raise RulesetUnavailableError(
                f"feature proficiency group {group_id} options must be distinct"
            )
        if not option_map and group.get("allow_unlisted") is not True:
            raise RulesetUnavailableError(
                f"feature proficiency group {group_id} needs reviewed options"
            )
        if option_map and any(item.casefold() not in option_map for item in selected):
            raise ValueError(f"feature proficiency group {group_id} has an unavailable option")
        normalized = [option_map.get(item.casefold(), item) for item in selected]
        kind = str(group.get("kind") or "").casefold()
        if kind in {"skill", "skill_expertise"}:
            for item in normalized:
                skill_key = item.casefold().replace(" ", "_")
                skill = sheet["skills"].get(skill_key)
                if skill is None:
                    raise ValueError("feature proficiency group names an unknown skill")
                if kind == "skill" and skill.get("proficiency") != "none":
                    raise ValueError("feature proficiency group skill is already proficient")
                if kind == "skill_expertise" and skill.get("proficiency") == "none":
                    raise ValueError("feature proficiency group expertise requires proficiency")
                if kind == "skill_expertise" and skill.get("proficiency") == "expertise":
                    raise ValueError("feature proficiency group skill already has expertise")
                skill["proficiency"] = "expertise" if kind == "skill_expertise" else "proficient"
        else:
            targets = {
                "language": sheet["traits"]["languages"],
                "tool": sheet["traits"]["proficiencies"]["tools"],
                "weapon": sheet["traits"]["proficiencies"]["weapons"],
            }
            target = targets.get(kind)
            if target is None:
                raise RulesetUnavailableError(
                    f"feature proficiency group {group_id} kind is unsupported"
                )
            _append_selected_proficiencies(normalized, target=target, label=kind)
        result[group_id] = normalized
    return result


def _validated_species_ability_choices(
    value: Any,
    *,
    requirement: dict[str, Any],
    valid_abilities: Any,
) -> list[str]:
    selected = [
        item.casefold()
        for item in _validated_distinct_choices(
            value,
            count=int(requirement.get("count", 0) or 0),
            label="species abilities",
        )
    ]
    ability_names = {str(item).casefold() for item in valid_abilities}
    if any(item not in ability_names for item in selected):
        raise ValueError("species ability choice is not a valid ability")
    excluded = {str(item).casefold() for item in requirement.get("exclude", [])}
    if excluded.intersection(selected):
        raise ValueError("species ability choice cannot use an excluded ability")
    raw_options = requirement.get("options", [])
    if not isinstance(raw_options, list):
        raise RulesetUnavailableError("species ability choice options are not executable")
    options = {str(item).strip().casefold() for item in raw_options if str(item).strip()}
    if options and any(item not in options for item in selected):
        raise ValueError("species ability choice is not one of the allowed options")
    return selected


def _validated_species_proficiency_choices(
    value: Any,
    *,
    groups: Any,
) -> dict[str, list[dict[str, str]]]:
    if not isinstance(groups, list):
        raise RulesetUnavailableError("species proficiency choice groups are not executable")
    if not groups:
        if value not in (None, {}):
            raise ValueError("species does not accept proficiency_choices")
        return {}
    if not isinstance(value, dict):
        raise ValueError("species proficiency_choices must be an object")
    group_map: dict[str, dict[str, Any]] = {}
    for raw_group in groups:
        if not isinstance(raw_group, dict):
            raise RulesetUnavailableError("species proficiency choice group is not executable")
        group_id = str(raw_group.get("id") or "").strip()
        group_key = group_id.casefold()
        if not group_id or group_key in group_map:
            raise RulesetUnavailableError("species proficiency choice group ids must be distinct")
        group_map[group_key] = raw_group
    supplied = {str(key).casefold(): raw for key, raw in value.items()}
    if set(supplied) != set(group_map):
        raise ValueError("species proficiency_choices must answer every choice group")
    result: dict[str, list[dict[str, str]]] = {}
    used: set[tuple[str, str]] = set()
    for group_key, group in group_map.items():
        group_id = str(group["id"])
        count = int(group.get("count", 0) or 0)
        selected = supplied[group_key]
        if not isinstance(selected, list) or len(selected) != count:
            raise ValueError(
                f"species proficiency choice {group_id} requires exactly {count} choices"
            )
        option_map: dict[tuple[str, str], dict[str, str]] = {}
        for raw_option in group.get("options") or []:
            if not isinstance(raw_option, dict):
                raise RulesetUnavailableError(
                    f"species proficiency choice {group_id} has an invalid option"
                )
            kind = str(raw_option.get("kind") or "").strip().casefold()
            name = str(raw_option.get("name") or "").strip()
            key = (kind, name.casefold().replace(" ", "_") if kind == "skill" else name.casefold())
            if kind not in {"language", "skill", "tool", "weapon"} or not name:
                raise RulesetUnavailableError(
                    f"species proficiency choice {group_id} has an invalid option"
                )
            if key in option_map:
                raise RulesetUnavailableError(
                    f"species proficiency choice {group_id} options must be distinct"
                )
            option_map[key] = {"kind": kind, "name": name}
        normalized: list[dict[str, str]] = []
        for raw_selected in selected:
            if not isinstance(raw_selected, dict) or set(raw_selected) != {"kind", "name"}:
                raise ValueError(
                    f"species proficiency choice {group_id} must use kind/name objects"
                )
            kind = str(raw_selected.get("kind") or "").strip().casefold()
            name = str(raw_selected.get("name") or "").strip()
            key = (kind, name.casefold().replace(" ", "_") if kind == "skill" else name.casefold())
            if key not in option_map:
                raise ValueError(f"species proficiency choice {group_id} is not an allowed option")
            if key in used:
                raise ValueError("species proficiency choices must be distinct")
            used.add(key)
            normalized.append(deepcopy(option_map[key]))
        result[group_id] = normalized
    return result


def _validated_narrative_choices(
    value: Any,
    *,
    groups: Any,
) -> dict[str, list[str]]:
    if not isinstance(groups, list):
        raise RulesetUnavailableError("narrative choice groups are not executable")
    if not groups:
        if value not in (None, {}):
            raise ValueError("content does not accept feature_choices")
        return {}
    if not isinstance(value, dict):
        raise ValueError("feature_choices must be an object")
    group_map: dict[str, dict[str, Any]] = {}
    for raw_group in groups:
        if not isinstance(raw_group, dict):
            raise RulesetUnavailableError("narrative choice group is not executable")
        group_id = str(raw_group.get("id") or "").strip()
        group_key = group_id.casefold()
        if not group_id or group_key in group_map:
            raise RulesetUnavailableError("narrative choice group ids must be distinct")
        group_map[group_key] = raw_group
    supplied = {str(key).casefold(): raw for key, raw in value.items()}
    if set(supplied) != set(group_map):
        raise ValueError("feature_choices must answer every narrative choice group")
    result: dict[str, list[str]] = {}
    for group_key, group in group_map.items():
        group_id = str(group["id"])
        selected = _validated_distinct_choices(
            supplied[group_key],
            count=int(group.get("count", 0) or 0),
            label=f"feature choice {group_id}",
        )
        option_map = {
            str(item).strip().casefold(): str(item).strip()
            for item in group.get("options", [])
            if str(item).strip()
        }
        if len(option_map) != len(group.get("options", [])):
            raise RulesetUnavailableError(
                f"feature choice {group_id} options must be distinct and non-empty"
            )
        if any(item.casefold() not in option_map for item in selected):
            raise ValueError(f"feature choice {group_id} is not an allowed option")
        result[group_id] = [option_map[item.casefold()] for item in selected]
    return result


def _subclass_spell_grants(card: dict[str, Any]) -> list[dict[str, Any]]:
    """Return explicit subclass spell-access grants."""

    raw_grants = card.get("spell_grants") or []
    if not isinstance(raw_grants, list):
        raise RulesetUnavailableError("subclass spell grants are not executable")
    if any(not isinstance(item, dict) for item in raw_grants):
        raise RulesetUnavailableError("subclass spell grants are not structured")
    grants = deepcopy(list(raw_grants))
    names = [str(item.get("name") or "").strip().casefold() for item in grants]
    if any(not name for name in names) or len(names) != len(set(names)):
        raise RulesetUnavailableError("subclass spell grants are empty or duplicated")
    if any(item.get("method") not in {"always_prepared", "known", "spellbook"} for item in grants):
        raise RulesetUnavailableError("subclass spell grant method is not executable")
    return grants


def _rule_payload_settled_mechanic_ids(payload: dict[str, Any]) -> set[str]:
    """Return only mechanics proven by this pack's compiled/native definition."""

    manifest = dict(payload.get("manifest") or {})
    local = {
        str(item.get("id") or "")
        for item in payload.get("mechanics") or []
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    native = {str(item) for item in manifest.get("native_mechanic_refs") or [] if str(item)}
    if not native:
        return local
    editions = [str(item) for item in manifest.get("editions") or [] if str(item)]
    locks = list(manifest.get("native_provider_locks") or [])
    verified = bool(editions)
    for edition in editions:
        try:
            core_pack = get_core_rule_pack(edition)
        except ValueError:
            verified = False
            break
        expected = {
            "id": core_pack.id,
            "version": core_pack.version,
            "edition": core_pack.edition,
            "fingerprint": core_pack.fingerprint,
            "mechanic_refs": sorted(native),
        }
        matching = [
            dict(item)
            for item in locks
            if isinstance(item, dict) and str(item.get("edition") or "") == edition
        ]
        if (
            len(matching) != 1
            or matching[0] != expected
            or not native <= {boundary.id for boundary in core_pack.boundaries}
        ):
            verified = False
            break
    return local | (native if verified else set())


def _strip_artifact_authoring_state(
    artifacts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep Pack artifacts executable; draft review history lives in Pack metadata."""

    portable: list[dict[str, Any]] = []
    for raw_artifact in artifacts:
        artifact = deepcopy(raw_artifact)
        artifact.pop("catalog_review", None)
        artifact.pop("selection_contract", None)
        portable.append(artifact)
    return portable


def _rebind_verified_official_review(
    artifact: Mapping[str, Any],
    reviewed_artifact: Mapping[str, Any],
) -> dict[str, Any]:
    """Project archive review onto an ALREADY archive-verified runtime artifact.

    The caller must prove the entire installed definition against its locked
    archive first. This helper does not establish archive identity or equivalence.
    No installed payload, package version, or historical receipt is rewritten.
    """
    value = _strip_artifact_authoring_state([dict(artifact)])[0]
    if artifact.get("id") != reviewed_artifact.get("id") or artifact.get(
        "kind"
    ) != reviewed_artifact.get("kind"):
        raise RulesetUnavailableError("official review artifact identity mismatch")
    source_contract = reviewed_artifact.get("selection_contract")
    source_review = reviewed_artifact.get("catalog_review")
    if source_contract is None and source_review is None:
        if str(value.get("application_state") or "selection_ready") == "selection_ready":
            raise RulesetUnavailableError(
                "official selection-ready artifact requires an archive review"
            )
        return value
    errors = [
        *selection_contract_errors(reviewed_artifact),
        *catalog_review_errors(reviewed_artifact),
    ]
    if errors or dict(source_review or {}).get("status") != "approved":
        raise RulesetUnavailableError("official archive has no valid approved selection review")
    contract = dict(source_contract)
    rebound = build_selection_contract(
        value,
        status=str(contract["status"]),
        materializer=contract["materializer"],
        schema=None if contract["status"] == "ready" else contract["schema"],
        references=contract["references"],
        blockers=contract["blockers"],
    )
    if contract["status"] == "ready" and (
        rebound["schema"]["selection_fields"] != contract["schema"]["selection_fields"]
    ):
        raise RulesetUnavailableError(
            "official runtime selection fields differ from archive review"
        )
    value["selection_contract"] = rebound
    value["catalog_review"] = build_catalog_review(
        value,
        decisions=source_review["decisions"],
        status=source_review["status"],
    )
    return value


def _auth_receipt_revision(value: Any) -> int | str | None:
    if not isinstance(value, dict):
        return None
    for key in ("campaign_revision", "revision", "new_revision", "to_revision"):
        revision = value.get(key)
        if isinstance(revision, (int, str)) and not isinstance(revision, bool):
            return revision
    for nested in value.values():
        if isinstance(nested, dict) and (revision := _auth_receipt_revision(nested)) is not None:
            return revision
    return None


_MAX_ARGUMENT_BYTES = 262_144
_MAX_COLLECTION_ITEMS = 1_000
_PARAMETER_DESCRIPTIONS: dict[str, str] = {
    "action": "Exact operation supported by this facade.",
    "actor_id": "Authoritative campaign actor identifier.",
    "audience": "Audience scope used to filter private campaign information.",
    "branch_id": "Authoritative timeline branch identifier.",
    "budget_chars": "Maximum characters in the returned context bundle.",
    "by_principal_id": (
        "Authenticated writer principal; modern requests bind it from Host delegation."
    ),
    "campaign_id": "Authoritative campaign identifier.",
    "character_id": "Authoritative player character or NPC identifier.",
    "cursor": "Opaque continuation cursor returned by the preceding response.",
    "dc": "Bounded D&D difficulty class used by the authoritative check.",
    "expected_branch_id": "Branch guard that must match the current authoritative branch.",
    "expected_campaign_revision": "Campaign revision guard used to reject stale mutations.",
    "expected_revision": "Authority revision guard used to reject stale mutations.",
    "exposure_handle": "Opaque owner-bound, expiring catalog-guidance handle.",
    "idempotency_key": "Stable business-operation key reused unchanged across retries.",
    "limit": "Maximum records to return in this bounded page (1 through 100).",
    "offset": "Non-negative bounded compatibility offset; prefer opaque cursors where available.",
    "payload": "Operation-specific bounded JSON object described by the selected action.",
    "principal_id": "Caller hint overwritten by process binding or signed Host delegation.",
    "query": "Case-insensitive bounded search text.",
    "top_k": "Maximum ranked matches to return (1 through 100).",
}


def _parameter_description(tool_name: str, parameter_name: str) -> str:
    return _PARAMETER_DESCRIPTIONS.get(
        parameter_name,
        f"Bounded {parameter_name.replace('_', ' ')} value accepted by {tool_name}.",
    )


def _validate_contract_arguments(arguments: Mapping[str, Any]) -> None:
    try:
        size = len(json.dumps(arguments, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("tool arguments must be a bounded JSON object") from exc
    if size > _MAX_ARGUMENT_BYTES:
        raise ValueError(f"tool arguments exceed the {_MAX_ARGUMENT_BYTES}-byte request limit")

    def visit(value: Any, *, depth: int = 0) -> None:
        if depth > 12:
            raise ValueError("tool arguments exceed the maximum nesting depth of 12")
        if isinstance(value, Mapping):
            if len(value) > _MAX_COLLECTION_ITEMS:
                raise ValueError("tool argument object has too many fields")
            for nested in value.values():
                visit(nested, depth=depth + 1)
        elif isinstance(value, list | tuple):
            if len(value) > _MAX_COLLECTION_ITEMS:
                raise ValueError("tool argument collection exceeds 1000 items")
            for nested in value:
                visit(nested, depth=depth + 1)
        elif isinstance(value, str) and len(value) > 65_536:
            raise ValueError("tool argument string exceeds 65536 characters")

    visit(arguments)
    for field_name, value in arguments.items():
        if not isinstance(value, str):
            continue
        if (
            field_name.endswith("_id")
            or field_name
            in {"action", "kind", "idempotency_key", "query", "name", "label", "identifier"}
        ) and len(value) > 256:
            raise ValueError(f"{field_name} must not exceed 256 characters")
    for field_name in ("limit", "top_k", "conversation_limit"):
        if field_name not in arguments:
            continue
        value = arguments[field_name]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
            raise ValueError(f"{field_name} must be an integer between 1 and 100")
    if "offset" in arguments:
        offset = arguments["offset"]
        if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 100_000:
            raise ValueError("offset must be an integer between 0 and 100000")


def _tool_output_schema(tool_name: str) -> dict[str, Any]:
    from .result_contracts import tool_output_schema

    return tool_output_schema(tool_name)


def _module_draft_parameters(parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Advertise the Module Pack authoring workflow as a bounded action contract."""

    schema = deepcopy(dict(parameters))
    properties = dict(schema.get("properties") or {})
    payload_schema = dict(properties.get("payload") or {})
    payload_schema["description"] = (
        "Action-specific Module Pack payload; inspect the matching action branch in allOf. "
        "Server-issued job_id/module_id values come from start/get, source_ref receipts come "
        "verbatim from evidence, package edits are complete replacements, and finalize needs "
        "the stable Agent-selected pack_id plus explicit confirmation."
    )
    properties["payload"] = payload_schema
    properties["expected_revision"]["description"] = (
        "Import-job revision returned by module_draft, not the campaign revision. Pass the "
        "latest value for guarded edits; finalization rechecks the current job revision."
    )
    schema["properties"] = properties

    source_ref = {
        "type": "object",
        "description": (
            "Copy this real source receipt verbatim from module_draft(evidence); do not infer "
            "or retype its chunk_hash."
        ),
        "required": ["source_key", "page", "chunk_hash", "note"],
        "properties": {
            "source_key": {
                "type": "string",
                "minLength": 1,
                "maxLength": 512,
                "description": "Source key from the returned evidence receipt.",
            },
            "page": {
                "type": ["integer", "null"],
                "minimum": 1,
                "description": "One-based source page, or null for generated Markdown.",
            },
            "chunk_hash": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
                "description": "Lowercase SHA-256 returned by evidence as source_ref.chunk_hash.",
            },
            "note": {
                "type": "string",
                "minLength": 1,
                "maxLength": 2000,
                "description": "Evidence note returned with or retained from the receipt.",
            },
        },
        "additionalProperties": False,
    }
    source_refs = {
        "type": "array",
        "minItems": 1,
        "maxItems": 128,
        "items": deepcopy(source_ref),
        "description": "One or more exact module_draft(evidence) receipts.",
    }
    optional_source_refs = {
        "type": "array",
        "maxItems": 128,
        "items": deepcopy(source_ref),
        "description": (
            "Party-size receipts; use an empty array only when both bounds are null because "
            "the source gives no party-size advice."
        ),
    }
    party_size = {
        "type": "object",
        "description": (
            "Source-backed party range. If the source gives no advice, set minimum/maximum "
            "both to null and source_refs to an empty array."
        ),
        "required": ["minimum", "maximum", "source_refs"],
        "properties": {
            "minimum": {"type": ["integer", "null"], "minimum": 1, "maximum": 20},
            "maximum": {"type": ["integer", "null"], "minimum": 1, "maximum": 20},
            "source_refs": optional_source_refs,
        },
        "oneOf": [
            {
                "properties": {
                    "minimum": {"type": "integer", "minimum": 1, "maximum": 20},
                    "maximum": {"type": "integer", "minimum": 1, "maximum": 20},
                    "source_refs": source_refs,
                }
            },
            {
                "properties": {
                    "minimum": {"type": "null"},
                    "maximum": {"type": "null"},
                    "source_refs": {"type": "array", "maxItems": 0},
                }
            },
        ],
        "additionalProperties": False,
    }
    cited_level = {
        "type": "object",
        "required": ["value", "source_refs"],
        "properties": {
            "value": {"type": "integer", "minimum": 1, "maximum": 20},
            "source_refs": source_refs,
        },
        "additionalProperties": False,
    }
    advancement = {
        "type": "object",
        "description": (
            "Source-reviewed advancement modes; recommended must be one member of modes and "
            "unknown is not finalizable."
        ),
        "required": ["modes", "recommended", "source_refs"],
        "properties": {
            "modes": {
                "type": "array",
                "minItems": 1,
                "maxItems": 16,
                "items": {"type": "string", "minLength": 1, "maxLength": 80},
                "examples": [["milestone"], ["xp"]],
            },
            "recommended": {
                "type": "string",
                "minLength": 1,
                "maxLength": 80,
                "examples": ["milestone"],
            },
            "source_refs": source_refs,
        },
        "additionalProperties": False,
    }
    pregenerated = {
        "type": "object",
        "description": (
            "Required review of whether complete module pregenerated characters exist and "
            "are applicable; an explicit reviewed absence is valid."
        ),
        "required": ["available", "applicability", "source_refs"],
        "properties": {
            "available": {"type": "boolean"},
            "applicability": {"type": "string", "minLength": 1, "maxLength": 2000},
            "source_refs": source_refs,
        },
        "additionalProperties": False,
    }
    play_profile = {
        "type": "object",
        "description": (
            "D&D play envelope. Level, advancement, and pregenerated-character review always "
            "need real evidence; party-size advice alone is optional and may be omitted."
        ),
        "required": [
            "starting_level",
            "expected_end_level",
            "advancement",
            "pregenerated_characters",
        ],
        "properties": {
            "party_size": party_size,
            "starting_level": deepcopy(cited_level),
            "expected_end_level": deepcopy(cited_level),
            "advancement": advancement,
            "pregenerated_characters": pregenerated,
        },
        "additionalProperties": False,
    }
    manifest = {
        "type": "object",
        "description": (
            "Complete Module Pack manifest replacement. content_summary is an object whose "
            "counts are recomputed by the server."
        ),
        "required": [
            "title",
            "classification",
            "compatibility",
            "play_profile",
            "continuity",
            "activation",
            "content_summary",
        ],
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 500},
            "classification": {
                "enum": ["adventure", "campaign", "emergent_seed", "emergent_episode"],
                "description": "Source-reviewed D&D Module Pack classification.",
            },
            "compatibility": {
                "type": "object",
                "required": ["editions", "required_capabilities"],
                "properties": {
                    "editions": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 2,
                        "uniqueItems": True,
                        "items": {"enum": ["2014", "2024"]},
                    },
                    "required_capabilities": {
                        "type": "array",
                        "maxItems": 64,
                        "items": {"type": "string", "minLength": 1, "maxLength": 128},
                        "examples": [["module_pack_v2"]],
                    },
                },
                "additionalProperties": False,
            },
            "play_profile": play_profile,
            "continuity": {
                "type": "object",
                "required": ["series_id", "order", "continues_from", "state_policy"],
                "properties": {
                    "series_id": {"type": ["string", "null"], "maxLength": 256},
                    "order": {"type": ["integer", "null"], "minimum": 0},
                    "continues_from": {"type": ["string", "null"], "maxLength": 256},
                    "state_policy": {"type": "object", "maxProperties": 128},
                },
                "additionalProperties": False,
            },
            "activation": {
                "type": "object",
                "required": ["mode", "default_active"],
                "properties": {
                    "mode": {"const": "campaign_attach"},
                    "default_active": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            "content_summary": {"type": "object", "maxProperties": 64},
        },
        "additionalProperties": False,
    }
    catalogs = {
        "type": "object",
        "description": "Complete replacements for supplied native D&D catalog arrays.",
        "minProperties": 1,
        "properties": {
            field: {
                "type": "array",
                "maxItems": 1000,
                "items": {"type": "object", "maxProperties": 128},
            }
            for field in ("items", "encounters", "hazards", "handouts", "mechanics")
        },
        "additionalProperties": False,
    }
    narrative = {
        "type": "object",
        "description": "Structured narrative dossiers and source-defined endings.",
        "required": ["dossiers", "endings"],
        "properties": {
            "dossiers": {
                "type": "array",
                "maxItems": 256,
                "items": {"type": "object", "maxProperties": 128},
            },
            "endings": {
                "type": "array",
                "maxItems": 128,
                "items": {"type": "object", "maxProperties": 128},
            },
        },
        "additionalProperties": False,
    }
    dependencies = {
        "type": "array",
        "description": "Immutable Pack dependencies with exact identity and checksum.",
        "maxItems": 128,
        "items": {
            "type": "object",
            "required": ["kind", "id", "version", "checksum", "optional"],
            "properties": {
                "kind": {"enum": ["addon", "module", "preset", "core_rules"]},
                "id": {"type": "string", "minLength": 1, "maxLength": 300},
                "version": {"type": "string", "minLength": 1, "maxLength": 100},
                "checksum": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "optional": {"type": "boolean"},
            },
            "additionalProperties": False,
        },
    }
    decision_properties = {
        "manifest": manifest,
        "catalogs": catalogs,
        "narrative": narrative,
        "dependencies": dependencies,
        "metadata": {"type": "object", "maxProperties": 256},
        "version": {"type": "string", "minLength": 1, "maxLength": 100},
    }
    package_edit = {
        "type": "object",
        "required": ["job_id", "operation"],
        "properties": {
            "job_id": {
                "type": "string",
                "minLength": 1,
                "maxLength": 256,
                "description": "Server-issued import-job id from start/get.",
            },
            "operation": {"const": "package"},
            "note": {"type": "string", "maxLength": 2000},
            **decision_properties,
        },
        "anyOf": [{"required": [field]} for field in decision_properties],
        "additionalProperties": False,
    }
    other_edit = {
        "type": "object",
        "required": ["operation"],
        "properties": {
            "job_id": {"type": "string", "minLength": 1, "maxLength": 256},
            "module_id": {"type": "string", "minLength": 1, "maxLength": 256},
            "operation": {
                "enum": [
                    "advance",
                    "source_text",
                    "content",
                    "statblock",
                    "asset",
                    "actor",
                    "combat_grid",
                ]
            },
        },
        "anyOf": [{"required": ["job_id"]}, {"required": ["module_id"]}],
        "additionalProperties": True,
        "maxProperties": 128,
    }
    confirmation = {
        "type": "object",
        "description": "Explicit Agent editorial decision before immutable finalization.",
        "required": ["confirmed", "note"],
        "properties": {
            "confirmed": {"const": True},
            "note": {"type": "string", "minLength": 1, "maxLength": 2000},
        },
        "additionalProperties": False,
    }

    schema["allOf"] = [
        {
            "if": {"properties": {"action": {"const": "start"}}, "required": ["action"]},
            "then": {
                "required": ["payload", "idempotency_key"],
                "properties": {
                    "payload": {
                        "oneOf": [
                            {
                                "type": "object",
                                "required": ["source_path"],
                                "properties": {
                                    "source_path": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 65536,
                                    },
                                    "title": {"type": "string", "maxLength": 500},
                                    "source_key": {"type": "string", "maxLength": 512},
                                },
                                "additionalProperties": False,
                            },
                            {
                                "type": "object",
                                "required": ["name", "content"],
                                "properties": {
                                    "name": {"type": "string", "minLength": 1, "maxLength": 512},
                                    "content": {
                                        "type": "string",
                                        "minLength": 1,
                                        "maxLength": 65536,
                                        "description": (
                                            "Complete UTF-8 Markdown module source with "
                                            "meaningful ATX headings so scenes and evidence "
                                            "chunks are indexable."
                                        ),
                                    },
                                    "title": {"type": "string", "maxLength": 500},
                                    "source_key": {"type": "string", "maxLength": 512},
                                },
                                "additionalProperties": False,
                            },
                        ]
                    }
                },
            },
        },
        {
            "if": {"properties": {"action": {"const": "get"}}, "required": ["action"]},
            "then": {
                "properties": {
                    "payload": {
                        "type": ["object", "null"],
                        "properties": {
                            "job_id": {"type": "string", "minLength": 1, "maxLength": 256},
                            "view": {"enum": ["full", "package"]},
                            "query": {"type": "string", "maxLength": 200},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                            "cursor": {"type": "string", "maxLength": 1024},
                            "offset": {"type": "integer", "minimum": 0, "maximum": 100000},
                        },
                        "additionalProperties": False,
                    }
                }
            },
        },
        {
            "if": {"properties": {"action": {"const": "evidence"}}, "required": ["action"]},
            "then": {
                "required": ["payload"],
                "properties": {
                    "payload": {
                        "type": "object",
                        "properties": {
                            "job_id": {"type": "string", "minLength": 1, "maxLength": 256},
                            "module_id": {"type": "string", "minLength": 1, "maxLength": 256},
                            "kind": {"enum": ["chunks", "page"]},
                            "query": {"type": "string", "maxLength": 200},
                            "scene_id": {"type": "string", "maxLength": 256},
                            "page_number": {"type": "integer", "minimum": 1},
                            "scale": {"type": "number", "exclusiveMinimum": 0},
                            "include_ocr_text": {"type": "boolean"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                            "cursor": {"type": "string", "maxLength": 1024},
                            "offset": {"type": "integer", "minimum": 0, "maximum": 100000},
                        },
                        "anyOf": [{"required": ["job_id"]}, {"required": ["module_id"]}],
                        "additionalProperties": False,
                    }
                },
            },
        },
        {
            "if": {"properties": {"action": {"const": "edit"}}, "required": ["action"]},
            "then": {
                "required": ["payload", "idempotency_key"],
                "properties": {"payload": {"oneOf": [package_edit, other_edit]}},
                "allOf": [
                    {
                        "if": {
                            "properties": {
                                "payload": {
                                    "type": "object",
                                    "properties": {"operation": {"const": "advance"}},
                                    "required": ["operation"],
                                }
                            }
                        },
                        "else": {"required": ["expected_revision"]},
                    }
                ],
            },
        },
        {
            "if": {"properties": {"action": {"const": "finalize"}}, "required": ["action"]},
            "then": {
                "required": ["payload", "idempotency_key"],
                "properties": {
                    "payload": {
                        "type": "object",
                        "required": ["job_id", "pack_id", "confirmation"],
                        "properties": {
                            "job_id": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 256,
                                "description": "Server-issued import-job id from start/get.",
                            },
                            "pack_id": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 300,
                                "pattern": "^[A-Za-z0-9][A-Za-z0-9._:-]*$",
                                "description": (
                                    "Stable portable Agent-selected Pack identity, for example "
                                    "dnd5e.module.the-lantern-below; it is not a job/module id."
                                ),
                            },
                            "confirmation": confirmation,
                            "include_package": {"type": "boolean"},
                            **decision_properties,
                        },
                        "additionalProperties": False,
                    }
                },
            },
        },
    ]
    return schema


class _ServerResourceStack:
    """Own initialization resources until they transfer to a completed server."""

    def __init__(self) -> None:
        self._callbacks: list[tuple[str, Callable[[], Any]]] = []

    def track(self, label: str, resource: Any) -> Any:
        for method_name in ("close", "dispose"):
            callback = getattr(resource, method_name, None)
            if callable(callback):
                self._callbacks.append((label, callback))
                break
        return resource

    def close(self) -> None:
        callbacks, self._callbacks = self._callbacks, []
        errors: list[BaseException] = []
        for label, callback in reversed(callbacks):
            try:
                callback()
            except BaseException as error:
                error.add_note(f"while closing SagaSmith server resource: {label}")
                errors.append(error)
        if errors:
            raise BaseExceptionGroup("SagaSmith server resource cleanup failed", errors)

    def close_after_failure(self, initialization_error: BaseException) -> None:
        try:
            self.close()
        except BaseException as cleanup_error:
            diagnostics: list[BaseException] = []
            if initialization_error.__cause__ is not None:
                diagnostics.append(initialization_error.__cause__)
            if isinstance(cleanup_error, BaseExceptionGroup):
                diagnostics.extend(cleanup_error.exceptions)
            else:
                diagnostics.append(cleanup_error)
            initialization_error.__cause__ = BaseExceptionGroup(
                "SagaSmith server initialization diagnostics",
                diagnostics,
            )
            initialization_error.__suppress_context__ = True
            initialization_error.add_note(
                "One or more initialized server resources also failed to close; "
                "cleanup diagnostics are chained as the cause."
            )


RULES_CONTEXT_UNSET = object()

__all__ = [
    "ABILITY_CHECK_KINDS",
    "ACTIVE_CONVERSATION_STATUSES",
    "ACTOR_CHECK_KINDS",
    "ACTOR_KNOWLEDGE_DISCLOSURE_SCOPES",
    "ADVANCEMENT_MODES",
    "AGENT_RULING_KIND_ORDER",
    "ARCANE_PROPULSION_ARM_ID",
    "ARMBLADE_ID",
    "AccessDeniedError",
    "AccessService",
    "ActivityError",
    "ActorKnowledgeService",
    "ActorKnowledgeTransfer",
    "AddonService",
    "Annotated",
    "Any",
    "BOUNDED_EVALUATION_PURPOSES",
    "BOUNDED_EVALUATION_SCHEMA_VERSION",
    "BOUNDED_OUTPUT_CONTRACTS",
    "BattleMapError",
    "BoundResolutionPlan",
    "BranchService",
    "CAMPAIGN_DM_ROLES",
    "CHARACTER_SPELL_CARD_FIELDS",
    "CHASE_BOUNDARY_IDS",
    "CHASE_MANUAL_OUTCOME_STATUSES",
    "COMBAT_OUTCOME_STATUSES",
    "CORE_2024_CONTENT_PACK_ID",
    "CORE_2024_CONTENT_PACK_VERSION",
    "CORE_BLADE_WARD_MECHANIC_ID",
    "CORE_CONTENT_PACK_ID",
    "CORE_CONTENT_PACK_VERSION",
    "CORE_DRAGONBORN_BREATH_MECHANIC_ID",
    "CORE_DWARVEN_RESILIENCE_MECHANIC_ID",
    "CORE_FEY_ANCESTRY_MECHANIC_ID",
    "CORE_FLY_MECHANIC_ID",
    "CORE_FLY_SPELL_IDS",
    "CORE_GNOME_CUNNING_MECHANIC_ID",
    "CORE_HALFLING_BRAVE_MECHANIC_ID",
    "CORE_HYPNOTIC_PATTERN_MECHANIC_ID",
    "CORE_HYPNOTIC_PATTERN_SPELL_IDS",
    "CORE_INVISIBILITY_MECHANIC_ID",
    "CORE_INVISIBILITY_SPELL_IDS",
    "CORE_MAGIC_ITEM_LAST_CHARGE_MECHANIC_ID",
    "CORE_MAGIC_ITEM_RECHARGE_MECHANIC_ID",
    "CORE_MENDING_MECHANIC_ID",
    "CORE_MENDING_SPELL_ID",
    "CORE_ORC_AGGRESSIVE_MECHANIC_ID",
    "CORE_RELENTLESS_ENDURANCE_MECHANIC_ID",
    "CORE_SLEEP_MECHANIC_ID",
    "CORE_SLEEP_SPELL_ID",
    "CORE_TOOLS",
    "CORE_TORTLE_NATURAL_ARMOR_MECHANIC_ID",
    "CORE_TORTLE_SHELL_DEFENSE_MECHANIC_ID",
    "CORE_UNCANNY_DODGE_MECHANIC_ID",
    "CORE_WATCHERS_EYE_MECHANIC_ID",
    "CORE_WITCH_BOLT_MECHANIC_ID",
    "Callable",
    "CampaignEvent",
    "CampaignRandomStream",
    "CampaignService",
    "CharacterInfo",
    "CharacterService",
    "CharacterStateUpdate",
    "ChaseManualOutcomeStatus",
    "CombatEngineError",
    "ContentSolutionError",
    "ContinuityCommitService",
    "ContinuityService",
    "ConversationStore",
    "Counter",
    "DAMAGE_TYPES",
    "DEATH_SAVE_SETTLED_CONDITIONS",
    "DEFAULT_CAMPAIGN_EDITION",
    "DENOMINATIONS",
    "DENOMINATION_CP_VALUES",
    "DND5E",
    "DND5E_DOCUMENT_LAYOUT_PROFILE",
    "DND5E_QUERY_HINTS",
    "DOCUMENT_NORMALIZER_VERSION",
    "DYRRN_TENTACLE_WHIP_ID",
    "DndModuleProfile",
    "DndRuntime",
    "EBERRON_ARTIFICER_BATTLE_READY_FEATURE_ID",
    "EBERRON_ARTIFICER_BATTLE_READY_PACK_ID",
    "EBERRON_ITEM_PACK_ID",
    "EXACT_MODULE_SOURCE_FIELDS",
    "EXACT_MODULE_SOURCE_FIELD_ORDER",
    "EXTERNAL_RULING_KINDS",
    "EXTERNAL_RULING_KIND_ORDER",
    "EventService",
    "Exposure",
    "ExposureError",
    "ExposureRegistry",
    "FIXED_GAME_TIME_PERIODS",
    "Field",
    "HEALING_POTION_MECHANIC_ID",
    "INCAPACITATING_STATE_IDS",
    "INVENTORY_OWNER_SCOPES",
    "IdempotencyService",
    "IdempotencyWrite",
    "Image",
    "ImportJobService",
    "InitialActorGrant",
    "InventoryActorLifecycleService",
    "Iterable",
    "LOCAL_SYSTEM_PRINCIPAL_ID",
    "LONG_REST_MINIMUM_MINUTES",
    "Literal",
    "MANAGED_MODULE_SOURCE_FIELDS",
    "Mapping",
    "MarkdownModuleParser",
    "McpConfig",
    "MemoryService",
    "ModuleService",
    "NAMESPACE_URL",
    "NARRATIVE_GAME_TIME_PERIODS",
    "NON_PLAYER_CHARACTER_TYPES",
    "NPC_CONVERSATION_CONTRACT",
    "NPC_CONVERSATION_SCHEMA_VERSION",
    "NPC_NARRATIVE_ACTION_KINDS",
    "NPC_TURN_BUNDLE_SCHEMA_VERSION",
    "NPC_TURN_PURPOSES",
    "NPC_TURN_SCHEMA_VERSION",
    "NeedsRulingError",
    "NoResultFound",
    "OCR_STATBLOCK_RECOVERY_VERSION",
    "OcrPageLayout",
    "PENDING_RULE_RESULT_STATUSES",
    "PLAYER_GAMEPLAY_VISIBILITY_SCOPES",
    "PLAYER_MODULE_VISIBILITY_SCOPES",
    "PLAYER_OWNED_ACTOR_DISCLOSURE_SCOPES",
    "PROFILE_COMBAT",
    "PROFILE_LOBBY",
    "PROFILE_PLAY",
    "Path",
    "PdfTextLayoutProvider",
    "REST_TYPES",
    "RULING_KINDS",
    "RapidOcrProvider",
    "RenderResult",
    "ResolutionContext",
    "ResolutionPlanBindingError",
    "ResolutionPlanCompilationError",
    "ResolutionPlanExecutionError",
    "RevisionService",
    "RuleCompilationError",
    "RuleEventRulingRequiredError",
    "RulePackError",
    "RulePackService",
    "RuleProfileService",
    "RuleReceiptService",
    "RuleService",
    "RulesetUnavailableError",
    "SKILL_ABILITIES",
    "SLOT_PAYMENT_ECONOMIES",
    "SPELL_RESOLUTION_MECHANIC_ID",
    "SRD2014_PRESET_PACK_ID",
    "SRD2014_PRESET_PACK_VERSION",
    "SRD2024_PRESET_PACK_ID",
    "SRD2024_PRESET_PACK_VERSION",
    "STANDARD_2014_CONTENT_PACK_ID",
    "STANDARD_2014_CONTENT_PACK_VERSION",
    "STANDARD_BINARY_CONDITION_IDS",
    "STEEL_DEFENDER_DEFLECT_ATTACK_MECHANIC_ID",
    "STEEL_DEFENDER_RELATION_KEY",
    "STEEL_DEFENDER_REVIEWED_EXPRESSION_HASH",
    "STEEL_DEFENDER_TURN_KIND",
    "SagaSmithStorage",
    "SkillCatalog",
    "SnapshotService",
    "StatblockImportError",
    "StateMutationService",
    "SteelDefenderError",
    "StrictBool",
    "SubjectContextService",
    "SystemRegistry",
    "TICKS_PER_MINUTE",
    "TORTLE_NATURAL_ARMOR_ARTIFACT_ID",
    "TORTLE_NATURAL_ARMOR_AUTHORITY_KEY",
    "TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_CHECKSUM",
    "TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID",
    "TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION",
    "TORTLE_NATURAL_ARMOR_LEGACY_PACK_ID",
    "TORTLE_NATURAL_ARMOR_LEGACY_PACK_VERSIONS",
    "TORTLE_NATURAL_ARMOR_SOURCE_RULE_REF_PREFIX",
    "ToolAnnotations",
    "ToolError",
    "TypeVar",
    "WEAPON_HAND_SLOTS",
    "_normalize_source_evidence_text",
    "accepted_proposal_deltas",
    "active_hypnotic_pattern_effect_ids",
    "active_random_stream",
    "add_choice_window",
    "add_effect",
    "add_inventory_item",
    "adjust_wallet",
    "advance_breathing_rounds",
    "advance_chase_turn",
    "advance_effect_durations",
    "advance_elapsed_effect_durations",
    "advance_elapsed_world_effect_durations",
    "advance_game_time",
    "advance_single_class_level",
    "advance_source_turn_effect_durations",
    "advance_world_effect_durations",
    "allows_trance_rest",
    "anchor_world_time",
    "apply_ability_generation",
    "apply_attack_ac_bonus",
    "apply_concentration_result",
    "apply_condition_change",
    "apply_constitution_score_hit_point_change",
    "apply_core_fly_effects",
    "apply_core_invisibility_effects",
    "apply_damage_parts_to_sheet",
    "apply_damage_to_sheet",
    "apply_deflect_attack_to_plan",
    "apply_dependent_actor_template_variant",
    "apply_document_page_revisions",
    "apply_healing_to_sheet",
    "apply_official_item_effect_to_encounter",
    "apply_pending_rolled_ability_generation",
    "apply_per_level_hit_point_bonus",
    "apply_raise_dead_to_sheet",
    "apply_rest",
    "apply_reviewed_statblock_fill",
    "apply_rule_event",
    "apply_short_rest_hit_die_choice",
    "apply_starting_equipment",
    "apply_statblock_variant",
    "apply_weapon_mastery_to_encounter",
    "arm_readied_spell",
    "artifact_with_direct_resolution",
    "ascii_slug",
    "asdict",
    "attune_inventory_item",
    "audit_release_semantic_validation",
    "audit_spell_resolution_paths",
    "available_actions",
    "available_attack_defenses",
    "available_reactions",
    "available_shield_attack_defenses",
    "available_shield_magic_missile_defenses",
    "award_experience",
    "base64",
    "begin_holding_breath",
    "begin_rolled_ability_generation",
    "begin_steel_defender_revival",
    "binascii",
    "bind_idempotency_request",
    "bind_resolution_plan",
    "bind_steel_defender_runtime_mechanics",
    "build_bundled_rule_sources",
    "build_catalog_review",
    "build_content_solution",
    "build_dnd_content_actor",
    "build_module_content_package",
    "build_preset_content_package",
    "build_rule_content_package",
    "build_selection_contract",
    "build_srd2014_content",
    "build_srd2014_preset_actors",
    "build_srd2024_content",
    "build_srd2024_preset_actors",
    "build_standard2014_content",
    "bundled_rule_corpus_inventory",
    "calendar_minute_point",
    "campaign_phase",
    "can_see",
    "candidate_draft_issues",
    "canonical_heading_path",
    "canonical_json",
    "catalog_review_errors",
    "charmed_social_check_advantage",
    "clean_source_evidence_text",
    "compact_ascii_key",
    "compile_battle_map",
    "compile_battle_map_template",
    "compile_mechanics",
    "compile_resolution_plan",
    "compiled_artifacts_from_candidates",
    "complete_item_attunement_ownership",
    "complete_steel_defender_revival",
    "condition_ids",
    "consume_activity",
    "consume_deflect_attack_reaction",
    "consume_magic_item_spell_cast",
    "consume_readied_spell",
    "consume_shield_reaction",
    "consume_spell_cast",
    "consume_task_help",
    "consume_weapon_ammunition",
    "consume_weapon_limited_use",
    "consume_weapon_mastery_attack_effects",
    "content_actor_catalog_definition",
    "content_definition_checksum",
    "content_fingerprint",
    "context_with_facts",
    "core_receipts",
    "current_chase_participant",
    "current_combatant",
    "damage_amount_after_reduction",
    "datetime",
    "death_save_due",
    "deepcopy",
    "default_character_notes",
    "default_character_sheet",
    "default_local_principal",
    "dependent_actor_lifecycle_policy",
    "dependent_actor_owner_binding",
    "dependent_actor_template_solution_errors",
    "derive_domain_character_sheet",
    "discover_2014_statblock_names_from_layout",
    "discover_2014_statblock_slots_from_layout",
    "drop_held_items",
    "effective_spell_resolution",
    "effective_statblock_rating",
    "emerge_tortle_shell_defense",
    "end_chase",
    "end_concentration_effects",
    "end_concentration_for_incapacitating_conditions",
    "end_hypnotic_pattern_effects",
    "end_tether_concentrations",
    "end_turn",
    "enter_tortle_shell_defense",
    "equip_inventory_item",
    "execute_resolution_plan",
    "experience_status",
    "expire_combat_bound_effects",
    "extract_content_inventory",
    "extract_pdf_page_text",
    "file_sha256",
    "finalize_imported_actor_rulings",
    "fly_target_limit",
    "force_move_directly_away",
    "game_time_ticks",
    "get_core_rule_pack",
    "hashlib",
    "healing_potion_formula",
    "held_item_roots",
    "hydrate_dnd_statblock_spellcasting",
    "implementation_identity",
    "importlib",
    "initial_random_stream",
    "initialize_base_class",
    "initialize_source_state",
    "inspect",
    "inspect_character_document",
    "installed_official_definition_matches",
    "invisibility_target_limit",
    "is_2014_statblock_identity_line",
    "is_bound_official_item_id",
    "is_core_fly_spell",
    "is_core_hypnotic_pattern_spell",
    "is_core_invisibility_spell",
    "is_core_magic_missile_spell",
    "is_core_witch_bolt_spell",
    "is_multiattack_source_name",
    "json",
    "json_sha256",
    "kill_steel_defender_when_owner_dies",
    "knock_prone_outside_combat",
    "legendary_action_spec",
    "load_native_rule_providers",
    "lru_cache",
    "magic_item_spell_card",
    "matching_official_expansion_dependency_rebinds",
    "materialize_dependent_actor_owner_scaling",
    "materialize_official_item_template",
    "materialize_parameterized_statblock_source",
    "materialized_item_binding_hash",
    "math",
    "mending_steel_defender",
    "merge_reviewed_campaign_settings",
    "merge_reviewed_campaign_state",
    "minimum_rest_minutes",
    "module_statblock_review_candidates",
    "mutate_bounded_resource",
    "nested_ruling_kind",
    "newly_ended_witch_bolt_tethers",
    "normalize_2014_statblock_candidate",
    "normalize_audience_facts",
    "normalize_bounded_proposal",
    "normalize_combat_grid_source_refs",
    "normalize_combat_grid_template",
    "normalize_combat_grid_templates",
    "normalize_context_entity_ref",
    "normalize_dnd_edition",
    "normalize_document",
    "normalize_npc_stimulus",
    "normalize_npc_turn_proposal",
    "normalize_party_public_map_asset",
    "normalize_starting_equipment_contract",
    "normalize_starting_equipment_selection",
    "normalized_document_page_text",
    "nullcontext",
    "ocr_layout_text",
    "official_expansion_catalog",
    "official_expansion_dependency_rebinds",
    "official_expansion_support_catalog",
    "official_item_profile",
    "os",
    "parameterized_statblock_requirements",
    "parse_2014_statblock",
    "parse_2014_statblock_template_preview",
    "parse_2024_statblock",
    "patch_battle_map",
    "pay_activity_activation",
    "pay_attack_action",
    "pay_legendary_action",
    "pay_official_item_activation",
    "pay_witch_bolt_sustain_action",
    "pickup_ground_item",
    "playthrough_source_bindings",
    "policy_for_tool",
    "preflight_attack",
    "preflight_spell_attack",
    "profile_spell_selection_status",
    "queue_combatant",
    "re",
    "receive_inventory_item",
    "recharge_activities_at_turn_start",
    "recharge_magic_item_charges",
    "reconcile_dodge_lifecycle",
    "reconcile_effect_dependencies",
    "reconcile_ended_effect_conditions",
    "reconcile_readied_spells",
    "reconcile_source_effect_dependencies",
    "reconcile_tortle_shell_defense_projection",
    "reconcile_witch_bolt_concentration",
    "reconcile_witch_bolt_range",
    "record_death_save_turn_start",
    "record_rest_completion",
    "recover_2014_pdf_statblock_layout",
    "recover_2014_statblock_from_ocr",
    "recover_stable_creature",
    "refresh_dependent_actor_sheet",
    "remove_effect",
    "remove_inventory_item",
    "render_pdf_page",
    "repair_steel_defender",
    "replace",
    "replace_prepared_spells",
    "request_hash",
    "require_agent_decidable_character_type",
    "require_compatible_build",
    "require_death_save_eligibility",
    "require_harmful_targeting_allowed",
    "require_resolution_plan_trigger",
    "reroll_recorded_d20_result",
    "resolution_context",
    "resolution_plan_contract",
    "resolution_plan_template",
    "resolve_actor_check",
    "resolve_actor_contest",
    "resolve_actor_group_check",
    "resolve_attack_damage",
    "resolve_check",
    "resolve_choice_window",
    "resolve_common_action",
    "resolve_death_save_to_sheet",
    "resolve_divine_spark_to_sheet",
    "resolve_fall_to_sheet",
    "resolve_hypnotic_pattern_target",
    "resolve_lay_on_hands_to_sheets",
    "resolve_magic_item_last_charge",
    "resolve_official_expansion_archives",
    "resolve_official_expansion_support_archives",
    "resolve_preserve_life_to_sheets",
    "resolve_readied_action_window",
    "resolve_readied_spell_window",
    "resolve_save_damage_to_sheets",
    "resolve_second_wind_to_sheet",
    "resolve_sleep_targets",
    "resolve_turn_undead_to_sheets",
    "restore_breathing",
    "reviewed_official_item_hash",
    "roll",
    "roll_ability_scores",
    "roll_attack_action",
    "rules_day_from_ticks",
    "run_mechanic_tests",
    "scaled_roll_expression",
    "secrets",
    "select_actor_memory_context",
    "selection_contract_errors",
    "selection_input_errors",
    "selection_schema_for_artifact",
    "set_exhaustion_level",
    "set_resource_value",
    "set_spell_prepared",
    "settle_core_activity_effect",
    "settle_hide",
    "sign_receipt",
    "source_cards",
    "source_speed_multiplier",
    "source_spell_resolution",
    "spell_attack_count",
    "spend_movement",
    "stabilize_sheet",
    "stand_outside_combat",
    "stand_up",
    "standard_save_damage_reduction",
    "start_chase",
    "start_encounter",
    "start_witch_bolt_tether",
    "synchronize_class_feature_resources",
    "time",
    "timed_condition_sources",
    "tool_catalog",
    "tools_for_phase",
    "transfer_actor_inventory_item",
    "trigger_readied_action",
    "trigger_readied_spell",
    "unicodedata",
    "update_inventory_item",
    "use_official_item_action",
    "use_random_stream",
    "uuid4",
    "uuid5",
    "validate_arcane_recovery_choice",
    "validate_bounded_proposal_refs",
    "validate_character_notes",
    "validate_character_sheet",
    "validate_deflect_attack_eligibility",
    "validate_dependent_actor_relations",
    "validate_dnd_content_actor",
    "validate_dnd_content_package",
    "validate_external_inventory_custody",
    "validate_initial_rest_hit_dice_requests",
    "validate_magic_missile_allocations",
    "validate_module_pack_decisions",
    "validate_natural_recovery_choice",
    "validate_npc_basis_refs",
    "validate_npc_targets",
    "validate_party_state",
    "validate_playthrough_manifest",
    "validate_playthrough_transition",
    "validate_position",
    "validate_profile_coverage",
    "validate_random_stream_state",
    "validate_rest_activity_minutes",
    "validate_rest_eligibility",
    "validate_rest_schedule",
    "validate_selection_ready_artifacts",
    "validate_song_of_rest_source",
    "validate_sorcerous_restoration_choice",
    "validate_source_bound_mechanics",
    "validate_source_defined_ending_condition",
    "validate_spell_grant",
    "validate_subject_context_fact",
    "validate_world_effect",
    "validated_save_source_facts",
    "verify_receipt_signature",
    "wake_sleep_effects",
    "wraps",
]
