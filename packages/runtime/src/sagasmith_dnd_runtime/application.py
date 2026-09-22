"""Compose protocol-independent D&D application services and their operation registry."""

from __future__ import annotations

from . import application_support as _support
from .config import McpConfig
from .contracts import action_parameters
from .operations import DndRuntime
from .services import ApplicationServices


def __getattr__(name):
    # Read-only compatibility for existing imports of application helpers.
    return getattr(_support, name)


def create_runtime(config: McpConfig | None = None) -> DndRuntime:
    resources = _support._ServerResourceStack()
    try:
        runtime = _create_application(config, resources=resources)
        runtime._sagasmith_close = resources.close
        return runtime
    except BaseException as error:
        resources.close_after_failure(error)
        raise


def _create_application(config: McpConfig | None = None, *, resources) -> DndRuntime:
    _services = ApplicationServices()
    _services.config = config
    _services.resources = resources
    """Create one dual-era MCP server over either supported transport."""
    _services.config = _services.config or _support.McpConfig.from_environment()
    _services.storage = _support.SagaSmithStorage(_services.config)
    _services.resources.track("campaign database", _services.storage.database)
    _services.resources.track("vector store", _services.storage.vectors)
    _services.storage.migrate()
    _services.campaigns = _support.CampaignService(_services.storage.database)
    _services.characters = _support.CharacterService(_services.storage.database)
    _services.actor_lifecycle = _support.InventoryActorLifecycleService(
        _services.storage.database,
        ground_context=lambda campaign, state, actor_id: _services.ground_drop_context(
            campaign, state, actor_id
        ),
    )
    _services.branches = _support.BranchService(_services.storage.database)
    _services.continuity = _support.ContinuityService(_services.storage.database)
    _services.continuity_commits = _support.ContinuityCommitService(_services.storage.database)
    _services.events = _support.EventService(_services.storage.database)
    _services.knowledge = _support.ActorKnowledgeService(_services.storage.database)
    _services.access = _support.AccessService(_services.storage.database)
    _services.idempotency = _support.IdempotencyService(_services.storage.database)
    _services.import_jobs = _support.ImportJobService(_services.storage.database)
    _support.default_local_principal(_services.storage.database)
    _services.memories = _support.MemoryService(_services.storage.database)
    _services.subject_contexts = _support.SubjectContextService(_services.storage.database)
    _services.modules = _support.ModuleService(_services.storage.database)
    _services.rules = _support.RuleService(_services.storage.database)
    _services.rule_packs = _support.RulePackService(_services.storage.database)
    _services.addons = _support.AddonService(_services.storage.database)
    _services.rule_profiles = _support.RuleProfileService(_services.storage.database)
    _services.rule_receipts = _support.RuleReceiptService(_services.storage.database)
    _services.revisions = _support.RevisionService(_services.storage.database)
    _services.snapshots = _support.SnapshotService(_services.storage.database)
    _services.catalog = _support.SkillCatalog(
        dnd_root=_services.config.dnd_skills_dir,
        modulegen_root=_services.config.modulegen_skills_dir,
    )
    _services.native_rule_providers = _support.load_native_rule_providers()
    _services.npc_conversations = _support.ConversationStore(_services.config.npc_conversations_dir)
    _services.content_authority_secret = _support._load_or_create_content_authority_secret(
        _services.config.home / "data" / ".content-authority-key"
    )

    _services.verified_content_authority_ids = _services.bind(
        "verified_content_authority_ids", "verified_content_authority_ids"
    )

    _services.derive_character_sheet = _services.bind(
        "derive_character_sheet", "derive_character_sheet"
    )

    _services.require_no_active_npc_conversation = _services.bind(
        "require_no_active_npc_conversation", "require_no_active_npc_conversation"
    )

    _services.rule_document_options = _services.bind(
        "rule_document_options", "rule_document_options"
    )

    _services.module_document_options = _services.bind(
        "module_document_options", "module_document_options"
    )

    _services.import_page_revisions = _services.bind(
        "import_page_revisions", "import_page_revisions"
    )

    _services.profile_options_with_core_lock = _services.bind(
        "profile_options_with_core_lock", "profile_options_with_core_lock"
    )

    _services.effective_rule_context_from = _services.bind(
        "effective_rule_context_from", "effective_rule_context_from"
    )

    _services.effective_rule_context = _services.bind(
        "effective_rule_context", "effective_rule_context"
    )

    _services.campaign_rules_edition = _services.bind(
        "campaign_rules_edition", "campaign_rules_edition"
    )

    _services.statblock_content_kind = _services.bind(
        "statblock_content_kind", "statblock_content_kind"
    )

    _services.parse_edition_statblock = _services.bind(
        "parse_edition_statblock", "parse_edition_statblock"
    )

    _services.managed_module_source_ref = _services.bind(
        "managed_module_source_ref", "managed_module_source_ref"
    )

    _services.advancement_source_ref = _services.bind(
        "advancement_source_ref", "advancement_source_ref"
    )

    _services.managed_module_source_excerpt = _services.bind(
        "managed_module_source_excerpt", "managed_module_source_excerpt"
    )

    _services.validate_embedded_module_source_refs = _services.bind(
        "validate_embedded_module_source_refs", "validate_embedded_module_source_refs"
    )

    _services.validate_playthrough_source_bindings = _services.bind(
        "validate_playthrough_source_bindings", "validate_playthrough_source_bindings"
    )

    _services.attest_playthrough_progress = _services.bind(
        "attest_playthrough_progress", "attest_playthrough_progress"
    )

    _services.effective_ruleset_view_from = _services.bind(
        "effective_ruleset_view_from", "effective_ruleset_view_from"
    )

    _services.effective_ruleset_view = _services.bind(
        "effective_ruleset_view", "effective_ruleset_view"
    )

    _services.bind_native_mechanic_contract = _services.bind(
        "bind_native_mechanic_contract", "bind_native_mechanic_contract"
    )

    _services.validate_active_native_mechanic_contract = _services.bind(
        "validate_active_native_mechanic_contract", "validate_active_native_mechanic_contract"
    )

    _services.save_rule_pack_draft = _services.bind("save_rule_pack_draft", "save_rule_pack_draft")

    _services.ensure_core_content_pack = _services.bind(
        "ensure_core_content_pack", "ensure_core_content_pack"
    )

    _services.ensure_standard2014_content_pack = _services.bind(
        "ensure_standard2014_content_pack", "ensure_standard2014_content_pack"
    )

    _services.ensure_core2024_content_pack = _services.bind(
        "ensure_core2024_content_pack", "ensure_core2024_content_pack"
    )

    _services.ensure_actor_content_pack = _services.bind(
        "ensure_actor_content_pack", "ensure_actor_content_pack"
    )

    _services.ensure_core_content_pack()
    _services.ensure_standard2014_content_pack()
    _services.ensure_core2024_content_pack()
    _services.ensure_actor_content_pack(
        _support.SRD2014_PRESET_PACK_ID,
        _support.SRD2014_PRESET_PACK_VERSION,
        _support.build_srd2014_preset_actors,
        "bundled-srd2014-actor-presets",
        title="D&D 5e SRD 5.1 Actor Presets",
        edition="2014",
    )
    _services.ensure_actor_content_pack(
        _support.SRD2024_PRESET_PACK_ID,
        _support.SRD2024_PRESET_PACK_VERSION,
        _support.build_srd2024_preset_actors,
        "bundled-srd2024-actor-presets",
        title="D&D 5e SRD 5.2.1 Actor Presets",
        edition="2024",
    )

    _services.default_preset_actor_card = _services.bind(
        "default_preset_actor_card", "default_preset_actor_card"
    )

    _services.refresh_portable_resolution_plans = _services.bind(
        "refresh_portable_resolution_plans", "refresh_portable_resolution_plans"
    )

    _services.canonicalize_portable_evidence = _services.bind(
        "canonicalize_portable_evidence", "canonicalize_portable_evidence"
    )

    _services.rule_content_descriptor = _services.bind(
        "rule_content_descriptor", "rule_content_descriptor"
    )

    _services.runtime_actor_with_portrait = _services.bind(
        "runtime_actor_with_portrait", "runtime_actor_with_portrait"
    )

    _services.import_content_module_package = _services.bind(
        "import_content_module_package", "import_content_module_package"
    )

    _services.store_content_rules_package = _services.bind(
        "store_content_rules_package", "store_content_rules_package"
    )

    _services.verified_reserved_official_rule_definition = _services.bind(
        "verified_reserved_official_rule_definition", "verified_reserved_official_rule_definition"
    )

    _services.reviewed_official_runtime_artifact = _services.bind(
        "reviewed_official_runtime_artifact", "reviewed_official_runtime_artifact"
    )

    _services.import_content_rules_package = _services.bind(
        "import_content_rules_package", "import_content_rules_package"
    )

    _services.ensure_official_expansion_content_packs = _services.bind(
        "ensure_official_expansion_content_packs", "ensure_official_expansion_content_packs"
    )

    _services.official_expansion_mount = _services.ensure_official_expansion_content_packs()

    _services.managed_module_asset_bytes = _services.bind(
        "managed_module_asset_bytes", "managed_module_asset_bytes"
    )

    _services.checked_rule_facts = _services.bind("checked_rule_facts", "checked_rule_facts")

    _services.assert_snapshot_core_available = _services.bind(
        "assert_snapshot_core_available", "assert_snapshot_core_available"
    )

    _services.bundled_rule_seed_status = _services.bind(
        "bundled_rule_seed_status", "bundled_rule_seed_status"
    )

    _services.seed_bundled_rules = _services.bind("seed_bundled_rules", "seed_bundled_rules")

    if _services.config.auto_seed_rules:
        _services.seed_bundled_rules()

    _services.authoritative_phase = _services.bind("authoritative_phase", "authoritative_phase")

    _services.preparation_setup_closed = _services.bind(
        "preparation_setup_closed", "preparation_setup_closed"
    )

    _services.initial_preparation_allowed_for_new_actor = _services.bind(
        "initial_preparation_allowed_for_new_actor", "initial_preparation_allowed_for_new_actor"
    )

    _services.authorize_tool_policy = _services.bind(
        "authorize_tool_policy", "authorize_tool_policy"
    )

    _services.validate_exposure_scope = _services.bind(
        "validate_exposure_scope", "validate_exposure_scope"
    )

    _services.exposures = _support.ExposureRegistry()

    _services.allowed_tools_for_exposure = _services.bind(
        "allowed_tools_for_exposure", "allowed_tools_for_exposure"
    )

    _services.campaign_random_context = _services.bind(
        "campaign_random_context", "campaign_random_context"
    )

    _services.mcp = _support.DndRuntime()
    from sagasmith_core.nonce_store import DatabaseNonceStore

    _services.DatabaseNonceStore = DatabaseNonceStore

    _services.mcp.ports = {
        "auth_nonce_store": _services.DatabaseNonceStore(_services.storage.database),
        "validate_arguments": _support._validate_contract_arguments,
        "character_campaign": lambda actor_id: _services.characters.get(actor_id).campaign_id,
        "module_chunk_campaign": lambda chunk_id: _services.modules.expand(chunk_id)["campaign_id"],
        "exposures": _services.exposures,
        "authoritative_phase": _services.authoritative_phase,
        "allowed_tools_for_exposure": _services.allowed_tools_for_exposure,
        "validate_exposure_scope": _services.validate_exposure_scope,
        "validate_request_scope": _services.validate_request_scope,
        "transition_scope": _services.transition_scope,
        "remember_transition": _services.remember_transition,
        "committed_campaign_revision": _services.committed_campaign_revision,
        "authorize_tool_policy": _services.authorize_tool_policy,
        "campaign_random_context": _services.campaign_random_context,
        "authoritative_host_context_binding": lambda *args, **kwargs: (
            _services.authoritative_host_context_binding(*args, **kwargs)
        ),
        "access": _services.access,
    }

    _services.public_tool = _services.bind("public_tool", "public_tool")

    _services.rules_context_unset = _support.RULES_CONTEXT_UNSET
    _services.context_receipt_secret = _support.uuid4().bytes + _support.uuid4().bytes
    _services.context_receipt_ttl_ns = 60 * 60 * 1_000_000_000

    _services.receipt_principal_fingerprint = _services.bind(
        "receipt_principal_fingerprint", "receipt_principal_fingerprint"
    )

    _services.host_context_binding = _services.bind("host_context_binding", "host_context_binding")

    _services.managed_module_source_digests = _services.bind(
        "managed_module_source_digests", "managed_module_source_digests"
    )

    _services.anchored_source_digests = _services.bind(
        "anchored_source_digests", "anchored_source_digests"
    )

    _services.issue_context_receipt = _services.bind(
        "issue_context_receipt", "issue_context_receipt"
    )

    _services.verify_context_receipt = _services.bind(
        "verify_context_receipt", "verify_context_receipt"
    )

    _services.npc_turn_receipt_ttl_ns = 10 * 60 * 1_000_000_000

    _services.npc_turn_latest_event_sequence = _services.bind(
        "npc_turn_latest_event_sequence", "npc_turn_latest_event_sequence"
    )

    _services.npc_turn_actor_state = _services.bind("npc_turn_actor_state", "npc_turn_actor_state")

    _services.npc_turn_scene_projection = _services.bind(
        "npc_turn_scene_projection", "npc_turn_scene_projection"
    )

    _services.npc_turn_perception_projection = _services.bind(
        "npc_turn_perception_projection", "npc_turn_perception_projection"
    )

    _services.npc_turn_actor_projection = _services.bind(
        "npc_turn_actor_projection", "npc_turn_actor_projection"
    )

    _services.actor_memory_projection = _services.bind(
        "actor_memory_projection", "actor_memory_projection"
    )

    _services.issue_npc_turn_receipt = _services.bind(
        "issue_npc_turn_receipt", "issue_npc_turn_receipt"
    )

    _services.verify_npc_turn_receipt = _services.bind(
        "verify_npc_turn_receipt", "verify_npc_turn_receipt"
    )

    _services.bounded_evaluation_receipt_ttl_ns = 10 * 60 * 1_000_000_000

    _services.bounded_memory_epoch_digest = _services.bind(
        "bounded_memory_epoch_digest", "bounded_memory_epoch_digest"
    )

    _services.bounded_knowledge_epoch_digest = _services.bind(
        "bounded_knowledge_epoch_digest", "bounded_knowledge_epoch_digest"
    )

    _services.issue_bounded_evaluation_receipt = _services.bind(
        "issue_bounded_evaluation_receipt", "issue_bounded_evaluation_receipt"
    )

    _services.verify_bounded_evaluation_receipt = _services.bind(
        "verify_bounded_evaluation_receipt", "verify_bounded_evaluation_receipt"
    )

    _services.bounded_evaluation_bundle = _services.bind(
        "bounded_evaluation_bundle", "bounded_evaluation_bundle"
    )

    _services.character_view = _services.bind("character_view", "character_view")

    _services.public_character_view = _services.bind(
        "public_character_view", "public_character_view"
    )

    _services.canonical_character_notes = _services.bind(
        "canonical_character_notes", "canonical_character_notes"
    )

    _services.library_character_view = _services.bind(
        "library_character_view", "library_character_view"
    )

    _services.is_dm = _services.bind("is_dm", "is_dm")

    _services.normalized_advancement_mode = _services.bind(
        "normalized_advancement_mode", "normalized_advancement_mode"
    )

    _services.campaign_advancement_mode = _services.bind(
        "campaign_advancement_mode", "campaign_advancement_mode"
    )

    _services.require_character_control = _services.bind(
        "require_character_control", "require_character_control"
    )

    _services.require_outside_active_combat = _services.bind(
        "require_outside_active_combat", "require_outside_active_combat"
    )

    _services.visible_character_view = _services.bind(
        "visible_character_view", "visible_character_view"
    )

    _services.combat_actor_snapshot = _services.bind(
        "combat_actor_snapshot", "combat_actor_snapshot"
    )

    _services.encounter_actor_ids = _services.bind("encounter_actor_ids", "encounter_actor_ids")

    _services.semantic_plan_harmful_target_ids = _services.bind(
        "semantic_plan_harmful_target_ids", "semantic_plan_harmful_target_ids"
    )

    _services.require_campaign_actor = _services.bind(
        "require_campaign_actor", "require_campaign_actor"
    )

    _services.narrative_only_actor = _services.bind("narrative_only_actor", "narrative_only_actor")

    _services.combat_card_analysis = _services.bind("combat_card_analysis", "combat_card_analysis")

    _services.statblock_variant_evidence = _services.bind(
        "statblock_variant_evidence", "statblock_variant_evidence"
    )

    _services.statblock_variant_source_label = _services.bind(
        "statblock_variant_source_label", "statblock_variant_source_label"
    )

    _services.reviewed_statblock_fill_evidence = _services.bind(
        "reviewed_statblock_fill_evidence", "reviewed_statblock_fill_evidence"
    )

    _services.reviewed_statblock_fill_labels = _services.bind(
        "reviewed_statblock_fill_labels", "reviewed_statblock_fill_labels"
    )

    _services.retained_statblock_warnings = _services.bind(
        "retained_statblock_warnings", "retained_statblock_warnings"
    )

    _services.statblock_agent_fill_requirements = _services.bind(
        "statblock_agent_fill_requirements", "statblock_agent_fill_requirements"
    )

    _services.require_complete_statblock_agent_fill = _services.bind(
        "require_complete_statblock_agent_fill", "require_complete_statblock_agent_fill"
    )

    _services.require_standard_statblock_engine_support = _services.bind(
        "require_standard_statblock_engine_support", "require_standard_statblock_engine_support"
    )

    _services.is_canonical_standard_rule_source = _services.bind(
        "is_canonical_standard_rule_source", "is_canonical_standard_rule_source"
    )

    _services.is_statblock_normalization_note = _services.bind(
        "is_statblock_normalization_note", "is_statblock_normalization_note"
    )

    _services.statblock_ruling_kind = _services.bind(
        "statblock_ruling_kind", "statblock_ruling_kind"
    )

    _services.statblock_ruling_requirements = _services.bind(
        "statblock_ruling_requirements", "statblock_ruling_requirements"
    )

    _services.statblock_settlement = _services.bind("statblock_settlement", "statblock_settlement")

    _services.append_statblock_diagnostics = _services.bind(
        "append_statblock_diagnostics", "append_statblock_diagnostics"
    )

    _services.persist_source_bound_statblock = _services.bind(
        "persist_source_bound_statblock", "persist_source_bound_statblock"
    )

    _services.source_bound_statblock_notes = _services.bind(
        "source_bound_statblock_notes", "source_bound_statblock_notes"
    )

    _services.source_bound_statblock_summary = _services.bind(
        "source_bound_statblock_summary", "source_bound_statblock_summary"
    )

    _services.combat_audience_view = _services.bind("combat_audience_view", "combat_audience_view")

    _services.combat_view = _services.bind("combat_view", "combat_view")

    _services.party_public_combat_view = _services.bind(
        "party_public_combat_view", "party_public_combat_view"
    )

    _services.party_public_map_asset_content = _services.bind(
        "party_public_map_asset_content", "party_public_map_asset_content"
    )

    _services.render_combat_snapshot = _services.bind(
        "render_combat_snapshot", "render_combat_snapshot"
    )

    _services.combat_response = _services.bind("combat_response", "combat_response")

    _services.resolution_rolls = _services.bind("resolution_rolls", "resolution_rolls")

    _services.resolution_outcome = _services.bind("resolution_outcome", "resolution_outcome")

    _services.resolution_presentation_view = _services.bind(
        "resolution_presentation_view", "resolution_presentation_view"
    )

    _services.current_branch_id = _services.bind("current_branch_id", "current_branch_id")

    _services.authoritative_host_context_binding = _services.bind(
        "authoritative_host_context_binding", "authoritative_host_context_binding"
    )

    _services.active_encounter = _services.bind("active_encounter", "active_encounter")

    _services.encounter_rules_edition = _services.bind(
        "encounter_rules_edition", "encounter_rules_edition"
    )

    _services.require_encounter_combatant = _services.bind(
        "require_encounter_combatant", "require_encounter_combatant"
    )

    _services.mutation_revision = _services.bind("mutation_revision", "mutation_revision")

    _services.require_current_branch = _services.bind(
        "require_current_branch", "require_current_branch"
    )

    _services.readable_branch = _services.bind("readable_branch", "readable_branch")

    _services.readable_scene_scope = _services.bind("readable_scene_scope", "readable_scene_scope")

    _services.player_module_scene_view = _services.bind(
        "player_module_scene_view", "player_module_scene_view"
    )

    _services.require_import_job = _services.bind("require_import_job", "require_import_job")

    _services.import_candidate_view = _services.bind(
        "import_candidate_view", "import_candidate_view"
    )

    _services.import_job_view = _services.bind("import_job_view", "import_job_view")

    _services.module_draft_handle_view = _services.bind(
        "module_draft_handle_view", "module_draft_handle_view"
    )

    _services.require_write_contract = _services.bind(
        "require_write_contract", "require_write_contract"
    )

    _services.sanitize_attack_action = _services.bind(
        "sanitize_attack_action", "sanitize_attack_action"
    )

    _services.validate_current_scene_agent_ruling = _services.bind(
        "validate_current_scene_agent_ruling", "validate_current_scene_agent_ruling"
    )

    _services.character_source_card = _services.bind(
        "character_source_card", "character_source_card"
    )

    _services.character_resolution_plan = _services.bind(
        "character_resolution_plan", "character_resolution_plan"
    )

    _services.character_activity_source_card = _services.bind(
        "character_activity_source_card", "character_activity_source_card"
    )

    _services.source_card_evidence_texts = _services.bind(
        "source_card_evidence_texts", "source_card_evidence_texts"
    )

    _services.source_card_has_executable_mechanic = _services.bind(
        "source_card_has_executable_mechanic", "source_card_has_executable_mechanic"
    )

    _services.finalize_actor_sheet_rulings = _services.bind(
        "finalize_actor_sheet_rulings", "finalize_actor_sheet_rulings"
    )

    _services.unresolved_content_solution = _services.bind(
        "unresolved_content_solution", "unresolved_content_solution"
    )

    _services.persisted_standard_spell_ruling_requirement = _services.bind(
        "persisted_standard_spell_ruling_requirement", "persisted_standard_spell_ruling_requirement"
    )

    _services.validate_persisted_standard_spell_ruling = _services.bind(
        "validate_persisted_standard_spell_ruling", "validate_persisted_standard_spell_ruling"
    )

    _services.validate_authored_content_plan = _services.bind(
        "validate_authored_content_plan", "validate_authored_content_plan"
    )

    _services.sheet_with_content_solution = _services.bind(
        "sheet_with_content_solution", "sheet_with_content_solution"
    )

    _services.agent_resolution_commitment = _services.bind(
        "agent_resolution_commitment", "agent_resolution_commitment"
    )

    _services.validate_agent_resolution_commitment = _services.bind(
        "validate_agent_resolution_commitment", "validate_agent_resolution_commitment"
    )

    _services.require_agent_resolution_payment = _services.bind(
        "require_agent_resolution_payment", "require_agent_resolution_payment"
    )

    _services.agent_save_damage_commitment = _services.bind(
        "agent_save_damage_commitment", "agent_save_damage_commitment"
    )

    _services.validate_scene_save_damage_source = _services.bind(
        "validate_scene_save_damage_source", "validate_scene_save_damage_source"
    )

    _services.validate_agent_save_damage_commitment = _services.bind(
        "validate_agent_save_damage_commitment", "validate_agent_save_damage_commitment"
    )

    _services.require_agent_save_damage_payment = _services.bind(
        "require_agent_save_damage_payment", "require_agent_save_damage_payment"
    )

    _services.validate_agent_attack_context = _services.bind(
        "validate_agent_attack_context", "validate_agent_attack_context"
    )

    _services.validate_agent_movement_facts = _services.bind(
        "validate_agent_movement_facts", "validate_agent_movement_facts"
    )

    _services.sync_combatant_conditions = _services.bind(
        "sync_combatant_conditions", "sync_combatant_conditions"
    )

    _services.combatant_zero_hp_buffered = _services.bind(
        "combatant_zero_hp_buffered", "combatant_zero_hp_buffered"
    )

    _services.source_participant_rules = _services.bind(
        "source_participant_rules", "source_participant_rules"
    )

    _services.add_attack_on_hit_window = _services.bind(
        "add_attack_on_hit_window", "add_attack_on_hit_window"
    )

    _services.encounter_turn_token = _services.bind("encounter_turn_token", "encounter_turn_token")

    _services.apply_standard_spell_on_hit_mechanics = _services.bind(
        "apply_standard_spell_on_hit_mechanics", "apply_standard_spell_on_hit_mechanics"
    )

    _services.expire_standard_source_turn_effects = _services.bind(
        "expire_standard_source_turn_effects", "expire_standard_source_turn_effects"
    )

    _services.require_healing_not_prevented = _services.bind(
        "require_healing_not_prevented", "require_healing_not_prevented"
    )

    _services.consume_next_attack_advantage = _services.bind(
        "consume_next_attack_advantage", "consume_next_attack_advantage"
    )

    _services.expire_next_attack_advantage = _services.bind(
        "expire_next_attack_advantage", "expire_next_attack_advantage"
    )

    _services.reveal_attacker_to_target = _services.bind(
        "reveal_attacker_to_target", "reveal_attacker_to_target"
    )

    _services.apply_cast_visibility_ruling = _services.bind(
        "apply_cast_visibility_ruling", "apply_cast_visibility_ruling"
    )

    _services.add_concentration_window = _services.bind(
        "add_concentration_window", "add_concentration_window"
    )

    _services.require_no_blocking_pending = _services.bind(
        "require_no_blocking_pending", "require_no_blocking_pending"
    )

    _services.require_combat_spell_turn_legal = _services.bind(
        "require_combat_spell_turn_legal", "require_combat_spell_turn_legal"
    )

    _services.record_combat_spell_cast = _services.bind(
        "record_combat_spell_cast", "record_combat_spell_cast"
    )

    _services.post_hit_attack_defenses = _services.bind(
        "post_hit_attack_defenses", "post_hit_attack_defenses"
    )

    _services.magic_missile_shield_defenses = _services.bind(
        "magic_missile_shield_defenses", "magic_missile_shield_defenses"
    )

    _services.combat_coordinates = _services.bind("combat_coordinates", "combat_coordinates")

    _services.combat_distance = _services.bind("combat_distance", "combat_distance")

    _services.reconcile_actor_witch_bolt_concentration = _services.bind(
        "reconcile_actor_witch_bolt_concentration", "reconcile_actor_witch_bolt_concentration"
    )

    _services.validate_spell_creature_target = _services.bind(
        "validate_spell_creature_target", "validate_spell_creature_target"
    )

    _services.normalize_single_target_declaration = _services.bind(
        "normalize_single_target_declaration", "normalize_single_target_declaration"
    )

    _services.normalize_area_declaration = _services.bind(
        "normalize_area_declaration", "normalize_area_declaration"
    )

    _services.normalize_hypnotic_pattern_declaration = _services.bind(
        "normalize_hypnotic_pattern_declaration", "normalize_hypnotic_pattern_declaration"
    )

    _services.advance_spell_attack_resolution = _services.bind(
        "advance_spell_attack_resolution", "advance_spell_attack_resolution"
    )

    _services.validate_magic_missile_targets = _services.bind(
        "validate_magic_missile_targets", "validate_magic_missile_targets"
    )

    _services.settle_magic_missile_damage = _services.bind(
        "settle_magic_missile_damage", "settle_magic_missile_damage"
    )

    _services.record_character_revision = _services.bind(
        "record_character_revision", "record_character_revision"
    )

    _services.narrative_followup_for_mutation = _services.bind(
        "narrative_followup_for_mutation", "narrative_followup_for_mutation"
    )

    _services._dependent_actor_materialization = _services.bind(
        "_dependent_actor_materialization", "_dependent_actor_materialization"
    )

    _services._dependent_actor_refresh_receipt = _services.bind(
        "_dependent_actor_refresh_receipt", "_dependent_actor_refresh_receipt"
    )

    _services._activity_source_identity = _services.bind(
        "_activity_source_identity", "_activity_source_identity"
    )

    _services._verified_steel_defender_relation = _services.bind(
        "_verified_steel_defender_relation", "_verified_steel_defender_relation"
    )

    _services._steel_defender_turn_contracts = _services.bind(
        "_steel_defender_turn_contracts", "_steel_defender_turn_contracts"
    )

    _services.require_combat_actor_or_steel_defender_owner_control = _services.bind(
        "require_combat_actor_or_steel_defender_owner_control",
        "require_combat_actor_or_steel_defender_owner_control",
    )

    _services._verified_steel_defender_repair_activity = _services.bind(
        "_verified_steel_defender_repair_activity", "_verified_steel_defender_repair_activity"
    )

    _services._verified_steel_defender_deflect_activity = _services.bind(
        "_verified_steel_defender_deflect_activity", "_verified_steel_defender_deflect_activity"
    )

    _services._prepare_steel_defender_deflect = _services.bind(
        "_prepare_steel_defender_deflect", "_prepare_steel_defender_deflect"
    )

    _services._refresh_owner_dependents = _services.bind(
        "_refresh_owner_dependents", "_refresh_owner_dependents"
    )

    _services.update_character = _services.bind("update_character", "update_character")

    _services.reconcile_actor_effect_dependencies = _services.bind(
        "reconcile_actor_effect_dependencies", "reconcile_actor_effect_dependencies"
    )

    _services.reconcile_completed_item_attunements = _services.bind(
        "reconcile_completed_item_attunements", "reconcile_completed_item_attunements"
    )

    _services.validate_inventory_custody_update = _services.bind(
        "validate_inventory_custody_update", "validate_inventory_custody_update"
    )

    _services.ground_drop_context = _services.bind("ground_drop_context", "ground_drop_context")

    _services.reconcile_unconscious_inventory = _services.bind(
        "reconcile_unconscious_inventory", "reconcile_unconscious_inventory"
    )

    _services.reconcile_steel_defender_deaths = _services.bind(
        "reconcile_steel_defender_deaths", "reconcile_steel_defender_deaths"
    )

    _services.commit_campaign_state = _services.bind(
        "commit_campaign_state", "commit_campaign_state"
    )

    _services.party_sheet = _services.bind("party_sheet", "party_sheet")

    _services.party_state = _services.bind("party_state", "party_state")

    _services.party_view_from_state = _services.bind(
        "party_view_from_state", "party_view_from_state"
    )

    _services.advance_state_game_time = _services.bind(
        "advance_state_game_time", "advance_state_game_time"
    )

    _services.advance_world_effect_clocks = _services.bind(
        "advance_world_effect_clocks", "advance_world_effect_clocks"
    )

    _services.completed_spell_cast_ticks = _services.bind(
        "completed_spell_cast_ticks", "completed_spell_cast_ticks"
    )

    _services.inventory_item_for_receipt = _services.bind(
        "inventory_item_for_receipt", "inventory_item_for_receipt"
    )

    _services.replay_idempotent = _services.bind("replay_idempotent", "replay_idempotent")

    _services.remember_idempotent = _services.bind("remember_idempotent", "remember_idempotent")

    _services.require_resolved_short_rest_hit_dice = _services.bind(
        "require_resolved_short_rest_hit_dice", "require_resolved_short_rest_hit_dice"
    )

    _services.require_engine_owned_character_state = _services.bind(
        "require_engine_owned_character_state", "require_engine_owned_character_state"
    )

    _services.storage_status = _services.public_tool()(
        _services.bind("storage_status", "storage_status")
    )

    _services.server_capabilities = _services.public_tool()(
        _services.bind("server_capabilities", "server_capabilities")
    )

    _services.storage_migrate = _services.public_tool()(
        _services.bind("storage_migrate", "storage_migrate")
    )

    _services.rule_seed_status = _services.public_tool()(
        _services.bind("rule_seed_status", "rule_seed_status")
    )

    _services.rule_seed_bundled = _services.public_tool()(
        _services.bind("rule_seed_bundled", "rule_seed_bundled")
    )

    _services.system_list = _services.public_tool()(_services.bind("system_list", "system_list"))

    _services.campaign_create = _services.public_tool()(
        _services.bind("campaign_create", "campaign_create")
    )

    _services.campaign_list = _services.bind("campaign_list", "campaign_list")

    _services.campaign_audience_view = _services.bind(
        "campaign_audience_view", "campaign_audience_view"
    )

    _services.campaign_get = _services.bind("campaign_get", "campaign_get")

    _services.game_phase_get = _services.bind("game_phase_get", "game_phase_get")

    _services.game_phase_set = _services.bind("game_phase_set", "game_phase_set")

    _services.import_job_get = _services.bind("import_job_get", "import_job_get")

    _services.import_job_list = _services.bind("import_job_list", "import_job_list")

    _services.rule_import_job_create = _services.bind(
        "rule_import_job_create", "rule_import_job_create"
    )

    _services.rule_import_job_inspect = _services.bind(
        "rule_import_job_inspect", "rule_import_job_inspect"
    )

    _services.rule_import_job_ingest = _support._agent_ruling_boundary(
        _services.bind("rule_import_job_ingest", "rule_import_job_ingest")
    )

    _services.validate_rule_candidate_execution_evidence = _services.bind(
        "validate_rule_candidate_execution_evidence", "validate_rule_candidate_execution_evidence"
    )

    _services.rule_content_candidates_extract = _services.bind(
        "rule_content_candidates_extract", "rule_content_candidates_extract"
    )

    _services.rule_content_candidates_augment = _services.bind(
        "rule_content_candidates_augment", "rule_content_candidates_augment"
    )

    _services.import_job_review_candidates = _services.bind(
        "import_job_review_candidates", "import_job_review_candidates"
    )

    _services.import_job_finalize_candidates = _services.bind(
        "import_job_finalize_candidates", "import_job_finalize_candidates"
    )

    _services.campaign_member_grant = _services.bind(
        "campaign_member_grant", "campaign_member_grant"
    )

    _services.campaign_member_revoke = _services.bind(
        "campaign_member_revoke", "campaign_member_revoke"
    )

    _services.actor_grant = _services.bind("actor_grant", "actor_grant")

    _services.campaign_update = _services.bind("campaign_update", "campaign_update")

    _services.campaign_advancement_configure = _services.bind(
        "campaign_advancement_configure", "campaign_advancement_configure"
    )

    _services.campaign_experience_award = _services.bind(
        "campaign_experience_award", "campaign_experience_award"
    )

    _services.campaign_loot_acquire = _services.bind(
        "campaign_loot_acquire", "campaign_loot_acquire"
    )

    _services.campaign_currency_spend = _services.bind(
        "campaign_currency_spend", "campaign_currency_spend"
    )

    _services.campaign_item_spend = _services.bind("campaign_item_spend", "campaign_item_spend")

    _services.campaign_consumable_use = _services.bind(
        "campaign_consumable_use", "campaign_consumable_use"
    )

    _services.reviewed_chase_source = _services.bind(
        "reviewed_chase_source", "reviewed_chase_source"
    )

    _services.chase_start = _support._agent_ruling_boundary(
        _services.bind("chase_start", "chase_start")
    )

    _services.chase_query = _services.bind("chase_query", "chase_query")

    _services.chase_take_turn = _support._agent_ruling_boundary(
        _services.bind("chase_take_turn", "chase_take_turn")
    )

    _services.chase_end = _services.bind("chase_end", "chase_end")

    _services.combat_start = _services.public_tool()(
        _support._agent_ruling_boundary(_services.bind("combat_start", "combat_start"))
    )

    _services.combat_join = _services.public_tool()(
        _support._agent_ruling_boundary(_services.bind("combat_join", "combat_join"))
    )

    _services.combat_status = _services.bind("combat_status", "combat_status")

    _services.combat_available_actions = _services.bind(
        "combat_available_actions", "combat_available_actions"
    )

    _services.combat_preflight_attack = _services.public_tool()(
        _support._agent_ruling_boundary(
            _services.bind("combat_preflight_attack", "combat_preflight_attack")
        )
    )

    _services.combat_resolve_attack = _services.public_tool()(
        _support._agent_ruling_boundary(
            _services.bind("combat_resolve_attack", "combat_resolve_attack")
        )
    )

    _services.combat_on_hit_ruling = _support._agent_ruling_boundary(
        _services.bind("combat_on_hit_ruling", "combat_on_hit_ruling")
    )

    _services.combat_end_turn = _services.public_tool()(
        _support._agent_ruling_boundary(_services.bind("combat_end_turn", "combat_end_turn"))
    )

    _services.campaign_world_effect_change = _services.bind(
        "campaign_world_effect_change", "campaign_world_effect_change"
    )

    _services.campaign_clock_set = _services.bind("campaign_clock_set", "campaign_clock_set")

    _services.campaign_advance_effects = _services.bind(
        "campaign_advance_effects", "campaign_advance_effects"
    )

    _services.combat_reaction_attack = _services.public_tool()(
        _support._agent_ruling_boundary(
            _services.bind("combat_reaction_attack", "combat_reaction_attack")
        )
    )

    _services.combat_reaction_defense = _support._agent_ruling_boundary(
        _services.bind("combat_reaction_defense", "combat_reaction_defense")
    )

    _services.combat_move = _support._agent_ruling_boundary(
        _services.bind("combat_move", "combat_move")
    )

    _services.combat_stand = _services.bind("combat_stand", "combat_stand")

    _services.combat_common_action = _services.public_tool()(
        _services.bind("combat_common_action", "combat_common_action")
    )

    _services.combat_magic_missile_defense = _support._agent_ruling_boundary(
        _services.bind("combat_magic_missile_defense", "combat_magic_missile_defense")
    )

    _services.combat_reactions = _services.bind("combat_reactions", "combat_reactions")

    _services.combat_cast_spell = _services.public_tool()(
        _support._agent_ruling_boundary(_services.bind("combat_cast_spell", "combat_cast_spell"))
    )

    _services.combat_ready_spell = _support._agent_ruling_boundary(
        _services.bind("combat_ready_spell", "combat_ready_spell")
    )

    _services.combat_readied_spell_trigger = _services.bind(
        "combat_readied_spell_trigger", "combat_readied_spell_trigger"
    )

    _services.combat_readied_spell_resolve = _support._agent_ruling_boundary(
        _services.bind("combat_readied_spell_resolve", "combat_readied_spell_resolve")
    )

    _services.combat_readied_action_trigger = _services.bind(
        "combat_readied_action_trigger", "combat_readied_action_trigger"
    )

    _services.combat_readied_action_resolve = _support._agent_ruling_boundary(
        _services.bind("combat_readied_action_resolve", "combat_readied_action_resolve")
    )

    _services.combat_use_official_item = _services.public_tool()(
        _support._agent_ruling_boundary(
            _services.bind("combat_use_official_item", "combat_use_official_item")
        )
    )

    _services.combat_use_activity = _services.public_tool()(
        _support._agent_ruling_boundary(
            _services.bind("combat_use_activity", "combat_use_activity")
        )
    )

    _services.combat_resolve_hide = _services.public_tool()(
        _support._agent_ruling_boundary(
            _services.bind("combat_resolve_hide", "combat_resolve_hide")
        )
    )

    _services.character_check = _support._agent_ruling_boundary(
        _services.bind("_character_check_v1", "character_check")
    )

    _services.character_source_feature = _services.bind(
        "character_source_feature", "character_source_feature"
    )

    _services.character_heroic_inspiration_reroll = _services.bind(
        "character_heroic_inspiration_reroll", "character_heroic_inspiration_reroll"
    )

    _services.character_group_check = _support._agent_ruling_boundary(
        _services.bind("character_group_check", "character_group_check")
    )

    _services.character_contest = _support._agent_ruling_boundary(
        _services.bind("character_contest", "character_contest")
    )

    _services.character_source_object_attack = _services.bind(
        "character_source_object_attack", "character_source_object_attack"
    )

    _services.combat_check = _services.public_tool()(
        _support._agent_ruling_boundary(_services.bind("combat_check", "combat_check"))
    )

    _services.combat_concentration_check = _services.public_tool()(
        _support._agent_ruling_boundary(
            _services.bind("combat_concentration_check", "combat_concentration_check")
        )
    )

    _services.combat_source_stabilize = _support._agent_ruling_boundary(
        _services.bind("combat_source_stabilize", "combat_source_stabilize")
    )

    _services.combat_resolution_plan = _support._agent_ruling_boundary(
        _services.bind("combat_resolution_plan", "combat_resolution_plan")
    )

    _services.combat_save_damage = _services.bind("combat_save_damage", "combat_save_damage")

    _services.combat_apply_damage = _support._agent_ruling_boundary(
        _services.bind("combat_apply_damage", "combat_apply_damage")
    )

    _services.combat_apply_fall = _support._agent_ruling_boundary(
        _services.bind("combat_apply_fall", "combat_apply_fall")
    )

    _services.combat_heal = _support._agent_ruling_boundary(
        _services.bind("combat_heal", "combat_heal")
    )

    _services.combat_choice_open = _services.bind("combat_choice_open", "combat_choice_open")

    _services.combat_choice_resolve = _services.bind(
        "combat_choice_resolve", "combat_choice_resolve"
    )

    _services.branch_list = _services.bind("branch_list", "branch_list")

    _services.branch_compare = _services.bind("branch_compare", "branch_compare")

    _services.branch_create = _services.bind("branch_create", "branch_create")

    _services.branch_checkout = _services.bind("branch_checkout", "branch_checkout")

    _services.snapshot_create = _services.public_tool()(
        _services.bind("snapshot_create", "snapshot_create")
    )

    _services.snapshot_list = _services.bind("snapshot_list", "snapshot_list")

    _services.snapshot_restore = _services.public_tool()(
        _services.bind("snapshot_restore", "snapshot_restore")
    )

    _services.snapshot_core_lock = _services.bind("snapshot_core_lock", "snapshot_core_lock")

    _services.snapshot_restore_core_upgrade = _services.bind(
        "snapshot_restore_core_upgrade", "snapshot_restore_core_upgrade"
    )

    _services.snapshot_verify = _services.bind("snapshot_verify", "snapshot_verify")

    _services.snapshot_lineage = _services.bind("snapshot_lineage", "snapshot_lineage")

    _services.snapshot_regenerate_recap = _services.bind(
        "snapshot_regenerate_recap", "snapshot_regenerate_recap"
    )

    _services.character_create = _support._agent_ruling_boundary(
        _services.bind("character_create", "character_create")
    )

    _services.character_list = _services.bind("character_list", "character_list")

    _services.character_library_list = _services.bind(
        "character_library_list", "character_library_list"
    )

    _services.character_instantiate = _support._agent_ruling_boundary(
        _services.bind("character_instantiate", "character_instantiate")
    )

    _services.character_build = _support._agent_ruling_boundary(
        _services.bind("character_build", "character_build")
    )

    _services.character_get = _services.bind("character_get", "character_get")

    _services.update_sheet = _support._agent_ruling_boundary(
        _services.bind("update_sheet", "update_sheet")
    )

    _services.character_sheet_replace = _services.public_tool()(
        _support._agent_ruling_boundary(
            _services.bind("character_sheet_replace", "character_sheet_replace")
        )
    )

    _services.character_wallet_adjust = _services.bind(
        "character_wallet_adjust", "character_wallet_adjust"
    )

    _services.character_inventory_add = _services.bind(
        "character_inventory_add", "character_inventory_add"
    )

    _services.character_inventory_update = _services.bind(
        "character_inventory_update", "character_inventory_update"
    )

    _services.character_inventory_remove = _services.bind(
        "character_inventory_remove", "character_inventory_remove"
    )

    _services.character_inventory_equip = _services.bind(
        "character_inventory_equip", "character_inventory_equip"
    )

    _services.character_inventory_recharge = _services.bind(
        "character_inventory_recharge", "character_inventory_recharge"
    )

    _services.character_ammunition_consume = _services.bind(
        "character_ammunition_consume", "character_ammunition_consume"
    )

    _services.ground_inventory_settlement = _support._agent_ruling_boundary(
        _services.bind("ground_inventory_settlement", "ground_inventory_settlement")
    )

    _services.character_inventory_transfer = _services.bind(
        "character_inventory_transfer", "character_inventory_transfer"
    )

    _services.character_effect_add = _services.bind("character_effect_add", "character_effect_add")

    _services.character_effect_remove = _services.bind(
        "character_effect_remove", "character_effect_remove"
    )

    _services.character_source_state_initialize = _services.bind(
        "character_source_state_initialize", "character_source_state_initialize"
    )
    _services.character_source_traits_apply = _services.bind(
        "character_source_traits_apply", "character_source_traits_apply"
    )
    _services.character_statblock_proficiency_sync = _services.bind(
        "character_statblock_proficiency_sync", "character_statblock_proficiency_sync"
    )

    _services.campaign_stable_recovery = _support._agent_ruling_boundary(
        _services.bind("campaign_stable_recovery", "campaign_stable_recovery")
    )

    _services.character_stand = _services.bind("character_stand", "character_stand")

    _services.character_knock_prone = _services.bind(
        "character_knock_prone", "character_knock_prone"
    )

    _services.character_level_advance = _services.bind(
        "character_level_advance", "character_level_advance"
    )

    _services.character_class_resources_synchronize = _services.bind(
        "character_class_resources_synchronize", "character_class_resources_synchronize"
    )

    _services.character_level_advancement_plan = _services.bind(
        "character_level_advancement_plan", "character_level_advancement_plan"
    )

    _services.character_cast_spell = _support._agent_ruling_boundary(
        _services.bind("character_cast_spell", "character_cast_spell")
    )

    _services.campaign_party_rest = _support._agent_ruling_boundary(
        _services.bind("campaign_party_rest", "campaign_party_rest")
    )

    _services.campaign_short_rest_hit_die = _support._agent_ruling_boundary(
        _services.bind("campaign_short_rest_hit_die", "campaign_short_rest_hit_die")
    )

    _services.character_use_activity = _services.bind(
        "character_use_activity", "character_use_activity"
    )

    _services.character_resource_set = _services.bind(
        "character_resource_set", "character_resource_set"
    )

    _services.character_exhaustion_set = _services.bind(
        "character_exhaustion_set", "character_exhaustion_set"
    )

    _services.character_apply_damage = _services.bind(
        "character_apply_damage", "character_apply_damage"
    )

    _services.character_apply_healing = _services.bind(
        "character_apply_healing", "character_apply_healing"
    )

    _services.character_make_death_save = _services.bind(
        "character_make_death_save", "character_make_death_save"
    )

    _services.character_stabilize = _services.bind("character_stabilize", "character_stabilize")

    _services.character_breathing_transition = _services.bind(
        "character_breathing_transition", "character_breathing_transition"
    )

    _services.character_apply_raise_dead = _services.bind(
        "character_apply_raise_dead", "character_apply_raise_dead"
    )

    _services.character_spell_prepare = _services.bind(
        "_character_spell_prepare_v1", "character_spell_prepare"
    )

    _services.character_spell_prepare_list = _services.bind(
        "character_spell_prepare_list", "character_spell_prepare_list"
    )

    _services.character_ability_apply = _services.public_tool()(
        _services.bind("character_ability_apply", "character_ability_apply")
    )

    _services.party_show = _services.bind("party_show", "party_show")

    _services.party_inventory_add = _services.bind("party_inventory_add", "party_inventory_add")

    _services.party_inventory_remove = _services.bind(
        "party_inventory_remove", "party_inventory_remove"
    )

    _services.party_inventory_transfer = _services.bind(
        "party_inventory_transfer", "party_inventory_transfer"
    )

    _services.party_wallet_adjust = _services.bind("party_wallet_adjust", "party_wallet_adjust")

    _services.party_wallet_transfer = _services.bind(
        "party_wallet_transfer", "party_wallet_transfer"
    )

    _services.settle_campaign_randomness = _services.bind(
        "settle_campaign_randomness", "settle_campaign_randomness"
    )

    _services.dnd_dice_roll = _services.public_tool()(
        _services.bind("dnd_dice_roll", "dnd_dice_roll")
    )

    _services.dnd_check = _services.public_tool()(_services.bind("dnd_check", "dnd_check"))

    _services.dnd_ability_roll = _services.public_tool()(
        _services.bind("dnd_ability_roll", "dnd_ability_roll")
    )

    _services.character_update = _services.bind("character_update", "character_update")

    _services.memory_list = _services.bind("memory_list", "memory_list")

    _services.memory_search = _services.bind("memory_search", "memory_search")

    _services.validate_actor_knowledge_source_audience = _services.bind(
        "validate_actor_knowledge_source_audience", "validate_actor_knowledge_source_audience"
    )

    _services.validate_continuity_knowledge_source_audiences = _services.bind(
        "validate_continuity_knowledge_source_audiences",
        "validate_continuity_knowledge_source_audiences",
    )

    _services.event_add = _services.bind("event_add", "event_add")

    _services.event_list = _services.bind("event_list", "event_list")

    _services.actor_knowledge_add = _services.bind("actor_knowledge_add", "actor_knowledge_add")

    _services.actor_knowledge_revise = _services.bind(
        "actor_knowledge_revise", "actor_knowledge_revise"
    )

    _services.actor_knowledge_list = _services.bind("actor_knowledge_list", "actor_knowledge_list")

    _services.actor_knowledge_search = _services.bind(
        "actor_knowledge_search", "actor_knowledge_search"
    )

    _services.state_idempotency_receipt = _services.bind(
        "state_idempotency_receipt", "state_idempotency_receipt"
    )

    _services.state_history = _services.bind("state_history", "state_history")

    _services.state_undo = _services.bind("state_undo", "state_undo")

    _services.state_redo = _services.bind("state_redo", "state_redo")

    _services.combat_map_patch = _services.public_tool()(
        _services.bind("combat_map_patch", "combat_map_patch")
    )

    _services.combat_end = _services.public_tool()(_services.bind("combat_end", "combat_end"))

    _services.continuity_context = _services.public_tool()(
        _services.bind("continuity_context", "continuity_context")
    )

    _services.npc_conversation_require_fresh = _services.bind(
        "npc_conversation_require_fresh", "npc_conversation_require_fresh"
    )

    _services.npc_conversation_private_context = _services.bind(
        "npc_conversation_private_context", "npc_conversation_private_context"
    )

    _services.npc_conversation_context_lock = _services.bind(
        "npc_conversation_context_lock", "npc_conversation_context_lock"
    )

    _services.npc_conversation_open_impl = _services.bind(
        "npc_conversation_open_impl", "npc_conversation_open_impl"
    )

    _services.npc_conversation_status_impl = _services.bind(
        "npc_conversation_status_impl", "npc_conversation_status_impl"
    )

    _services.npc_conversation_list_impl = _services.bind(
        "npc_conversation_list_impl", "npc_conversation_list_impl"
    )

    _services.npc_conversation_ingest_impl = _services.bind(
        "npc_conversation_ingest_impl", "npc_conversation_ingest_impl"
    )

    _services.npc_activation_claim_impl = _services.bind(
        "npc_activation_claim_impl", "npc_activation_claim_impl"
    )

    _services.npc_proposal_submit_impl = _services.bind(
        "npc_proposal_submit_impl", "npc_proposal_submit_impl"
    )

    _services.npc_activation_cancel_impl = _services.bind(
        "npc_activation_cancel_impl", "npc_activation_cancel_impl"
    )

    _services.npc_conversation_publish_impl = _services.bind(
        "npc_conversation_publish_impl", "npc_conversation_publish_impl"
    )

    _services.npc_conversation_close_impl = _services.bind(
        "npc_conversation_close_impl", "npc_conversation_close_impl"
    )

    _services.npc_conversation_abort_impl = _services.bind(
        "npc_conversation_abort_impl", "npc_conversation_abort_impl"
    )

    _services.npc_conversation = _services.public_tool()(
        _services.bind("npc_conversation", "npc_conversation")
    )

    _services.npc_conversation_transport = _services.public_tool()(
        _services.bind("npc_conversation_transport", "npc_conversation_transport")
    )

    _services.bounded_evaluation = _services.public_tool()(
        _services.bind("bounded_evaluation", "bounded_evaluation")
    )

    _services.continuity_diagnostics = _services.bind(
        "continuity_diagnostics", "continuity_diagnostics"
    )

    _services.continuity_commit = _services.bind("continuity_commit", "continuity_commit")

    _services.module_import_job_create = _services.bind(
        "module_import_job_create", "module_import_job_create"
    )

    _services.module_import_job_inspect = _services.bind(
        "module_import_job_inspect", "module_import_job_inspect"
    )

    _services.module_import_job_validate = _services.bind(
        "module_import_job_validate", "module_import_job_validate"
    )

    _services.module_import_job_import = _services.bind(
        "module_import_job_import", "module_import_job_import"
    )

    _services.module_write = _services.bind("module_write", "module_write")

    _services.module_list = _services.bind("module_list", "module_list")

    _services.module_index = _services.bind("module_index", "module_index")

    _services.module_expand = _services.public_tool()(
        _services.bind("module_expand", "module_expand")
    )

    _services.module_assets = _services.bind("module_assets", "module_assets")

    _services.module_asset_attach = _services.bind("module_asset_attach", "module_asset_attach")

    _services.module_pdf_asset = _services.bind("module_pdf_asset", "module_pdf_asset")

    _services.module_statblock_ocr_recover = _services.bind(
        "module_statblock_ocr_recover", "module_statblock_ocr_recover"
    )

    _services.module_content_review = _services.bind(
        "module_content_review", "module_content_review"
    )

    _services.module_content_candidates = _services.bind(
        "module_content_candidates", "module_content_candidates"
    )

    _services.module_read_scene = _services.bind("module_read_scene", "module_read_scene")

    _services.module_scene_preflight = _services.bind(
        "module_scene_preflight", "module_scene_preflight"
    )

    _services.module_current = _services.bind("module_current", "module_current")

    _services.module_progress_index = _services.bind(
        "module_progress_index", "module_progress_index"
    )

    _services.module_set_progress = _services.public_tool()(
        _services.bind("module_set_progress", "module_set_progress")
    )

    _services.module_search = _services.public_tool()(
        _services.bind("module_search", "module_search")
    )

    _services.campaign_rule_source_ids = _services.bind(
        "campaign_rule_source_ids", "campaign_rule_source_ids"
    )

    _services.rule_search = _services.public_tool()(_services.bind("rule_search", "rule_search"))

    _services.rule_expand = _services.public_tool()(_services.bind("rule_expand", "rule_expand"))

    _services.rule_document_stage = _services.bind("rule_document_stage", "rule_document_stage")

    _services.rule_document_page_render = _services.bind(
        "rule_document_page_render", "rule_document_page_render"
    )

    _services.rapidocr_providers: dict[tuple[str, float], _support.RapidOcrProvider] = {}
    _services.rapidocr_layout_cache: dict[
        tuple[str, int, int, str, float, int], _support.OcrPageLayout
    ] = {}

    _services.cached_rapidocr_layout = _services.bind(
        "cached_rapidocr_layout", "cached_rapidocr_layout"
    )

    _services.local_ocr_page_evidence = _services.bind(
        "local_ocr_page_evidence", "local_ocr_page_evidence"
    )

    _services.staged_transcription_evidence = _services.bind(
        "staged_transcription_evidence", "staged_transcription_evidence"
    )

    _services.submit_import_text_review = _services.bind(
        "submit_import_text_review", "submit_import_text_review"
    )

    _services.local_rule_catalog_identity_evidence = _services.bind(
        "local_rule_catalog_identity_evidence", "local_rule_catalog_identity_evidence"
    )

    _services.recover_pdf_statblock_layout = _services.bind(
        "recover_pdf_statblock_layout", "recover_pdf_statblock_layout"
    )

    _services.rule_statblock_ocr_recover = _services.bind(
        "rule_statblock_ocr_recover", "rule_statblock_ocr_recover"
    )

    _services.rule_statblock_catalog_recover = _services.bind(
        "rule_statblock_catalog_recover", "rule_statblock_catalog_recover"
    )

    _services.validate_agent_text_statblock_review = _services.bind(
        "validate_agent_text_statblock_review", "validate_agent_text_statblock_review"
    )

    _services.validate_indexed_statblock_review = _services.bind(
        "validate_indexed_statblock_review", "validate_indexed_statblock_review"
    )

    _services.rule_statblock_review = _services.bind(
        "rule_statblock_review", "rule_statblock_review"
    )

    _services.rule_statblock_review_from_base = _services.bind(
        "rule_statblock_review_from_base", "rule_statblock_review_from_base"
    )

    _services.rule_pack_draft_from_source = _services.bind(
        "rule_pack_draft_from_source", "rule_pack_draft_from_source"
    )

    _services.rule_import_job_compile = _services.bind(
        "rule_import_job_compile", "rule_import_job_compile"
    )

    _services.rule_pack_install = _services.bind("rule_pack_install", "rule_pack_install")

    _services.rule_pack_list = _services.bind("rule_pack_list", "rule_pack_list")

    _services.rule_pack_inspect = _services.bind("rule_pack_inspect", "rule_pack_inspect")

    _services.rule_pack_remove = _services.bind("rule_pack_remove", "rule_pack_remove")

    _services.campaign_rule_profile_get = _services.bind(
        "campaign_rule_profile_get", "campaign_rule_profile_get"
    )

    _services.campaign_rule_profile_set = _services.bind(
        "campaign_rule_profile_set", "campaign_rule_profile_set"
    )

    _services.campaign_core_relock = _services.bind("campaign_core_relock", "campaign_core_relock")

    _services.campaign_rule_pack_set = _services.bind(
        "campaign_rule_pack_set", "campaign_rule_pack_set"
    )

    _services.campaign_rule_pack_remove = _services.bind(
        "campaign_rule_pack_remove", "campaign_rule_pack_remove"
    )

    _services.campaign_rules_explain = _services.bind(
        "campaign_rules_explain", "campaign_rules_explain"
    )

    _services.campaign_rule_receipts = _services.bind(
        "campaign_rule_receipts", "campaign_rule_receipts"
    )

    _services.available_content_artifacts = _services.bind(
        "available_content_artifacts", "available_content_artifacts"
    )

    _services.source_scoped_content_matches = _services.bind(
        "source_scoped_content_matches", "source_scoped_content_matches"
    )

    _services.selected_progression_content_source = _services.bind(
        "selected_progression_content_source", "selected_progression_content_source"
    )

    _services.progression_feature_source_matches = _services.bind(
        "progression_feature_source_matches", "progression_feature_source_matches"
    )

    _services.content_runtime_context = _services.bind(
        "content_runtime_context", "content_runtime_context"
    )

    _services.trusted_watchers_eye_binding = _services.bind(
        "trusted_watchers_eye_binding", "trusted_watchers_eye_binding"
    )

    _services.watchers_eye_feature_card = _services.bind(
        "watchers_eye_feature_card", "watchers_eye_feature_card"
    )

    _services.executable_watchers_eye_feature = _services.bind(
        "executable_watchers_eye_feature", "executable_watchers_eye_feature"
    )

    _services.hydrate_class_prepared_spell_cards = _services.bind(
        "hydrate_class_prepared_spell_cards", "hydrate_class_prepared_spell_cards"
    )

    _services.hydrate_statblock_spellcasting = _services.bind(
        "hydrate_statblock_spellcasting", "hydrate_statblock_spellcasting"
    )

    _services.hydrate_statblock_variant_spells = _services.bind(
        "hydrate_statblock_variant_spells", "hydrate_statblock_variant_spells"
    )

    _services.hydrate_magic_item_spell_artifacts = _services.bind(
        "hydrate_magic_item_spell_artifacts", "hydrate_magic_item_spell_artifacts"
    )

    _services.settle_magic_item_last_charge = _services.bind(
        "settle_magic_item_last_charge", "settle_magic_item_last_charge"
    )

    _services.materialize_species_features = _services.bind(
        "materialize_species_features", "materialize_species_features"
    )

    _services.refresh_level_unlocked_species_features = _services.bind(
        "refresh_level_unlocked_species_features", "refresh_level_unlocked_species_features"
    )

    _services.refresh_level_unlocked_subclass_spells = _services.bind(
        "refresh_level_unlocked_subclass_spells", "refresh_level_unlocked_subclass_spells"
    )

    _services.level_advancement_content_context = _services.bind(
        "level_advancement_content_context", "level_advancement_content_context"
    )

    _services.current_class_feature_follow_up = _services.bind(
        "current_class_feature_follow_up", "current_class_feature_follow_up"
    )

    _services.content_catalog_list = _services.bind("content_catalog_list", "content_catalog_list")

    _services.spend_exact_wallet_payment = _services.bind(
        "spend_exact_wallet_payment", "spend_exact_wallet_payment"
    )

    _services.settle_spellbook_copy = _services.bind(
        "settle_spellbook_copy", "settle_spellbook_copy"
    )

    _services.character_content_apply = _services.bind(
        "_character_content_apply_v1", "character_content_apply"
    )

    _services.skill_list = _services.bind("skill_list", "skill_list")

    _services.skill_read = _services.bind("skill_read", "skill_read")

    _services.skill_asset_list = _services.bind("skill_asset_list", "skill_asset_list")

    _services.skill_asset_read = _services.bind("skill_asset_read", "skill_asset_read")

    _services.skill_resource = _services.mcp.resource("sagasmith://skill/{skill_id}")(
        _services.bind("skill_resource", "skill_resource")
    )

    _services.bootstrap_resource = _services.mcp.resource(
        "sagasmith://bootstrap",
        name="SagaSmith zero-knowledge bootstrap",
        description="Compact host-independent startup and recovery workflow.",
        mime_type="text/markdown",
    )(_services.bind("bootstrap_resource", "bootstrap_resource"))

    _services.skill_asset_index_resource = _services.mcp.resource(
        "sagasmith://skills/assets",
        name="SagaSmith skill asset index",
        description="Installed reference, template, and data asset ids.",
        mime_type="text/markdown",
    )(_services.bind("skill_asset_index_resource", "skill_asset_index_resource"))

    _services.skill_overview_resource = _services.mcp.resource(
        "sagasmith://skills/overview",
        name="SagaSmith D&D skill overview",
        description="Installed D&D and module-generation skill document ids.",
        mime_type="text/markdown",
    )(_services.bind("skill_overview_resource", "skill_overview_resource"))

    _services.skill_asset_resource = _services.mcp.resource("sagasmith://asset/{resource_id}")(
        _services.bind("skill_asset_resource", "skill_asset_resource")
    )

    _services.delegation_resource = _services.mcp.resource(
        "sagasmith://delegation",
        name="SagaSmith bounded delegation contract",
        description="Host-neutral algorithm for evaluating signed domain bundles.",
        mime_type="text/markdown",
    )(_services.bind("delegation_resource", "delegation_resource"))

    _services.delegated_subagent = _services.mcp.prompt()(
        _services.bind("delegated_subagent", "delegated_subagent")
    )

    _services.dnd_dm = _services.mcp.prompt()(_services.bind("dnd_dm", "dnd_dm"))

    _services.module_generator = _services.mcp.prompt()(
        _services.bind("module_generator", "module_generator")
    )

    # The public MCP contract intentionally exposes domain facades rather than
    # one tool per storage operation.  These facades call the mature, narrowly
    # validated operations above; they must not reimplement writes or weaken
    # revision, idempotency, access, or combat guards.
    _services.character_content_apply_impl = _services.character_content_apply
    _services.character_spell_prepare_impl = _services.character_spell_prepare
    _services.campaign_clock_set_impl = _services.campaign_clock_set
    _services.campaign_advance_effects_impl = _services.campaign_advance_effects
    _services.character_check_impl = _services.character_check

    _services.facade_payload = _services.bind("facade_payload", "facade_payload")

    _services.facade_bool = _services.bind("facade_bool", "facade_bool")

    _services.required_boolean = _services.bind("required_boolean", "required_boolean")

    _services.required = _services.bind("required", "required")

    _services.require_facade_phase = _services.bind("require_facade_phase", "require_facade_phase")

    _services.optional_datetime = _services.bind("optional_datetime", "optional_datetime")

    _services.facade_result = _services.bind("facade_result", "facade_result")

    _services.facade_render_result = _services.bind("facade_render_result", "facade_render_result")

    _services.character_check = _services.public_tool()(
        _services.bind("_character_check_v2", "character_check")
    )

    _services.chase = _services.public_tool()(_services.bind("chase", "chase"))

    _services.export_module_pack = _services.bind("export_module_pack", "export_module_pack")

    _services.module_query = _services.public_tool()(_services.bind("module_query", "module_query"))

    _services._content_pack_actor_presets = _services.bind(
        "_content_pack_actor_presets", "_content_pack_actor_presets"
    )

    _services._content_pack_module_archive = _services.bind(
        "_content_pack_module_archive", "_content_pack_module_archive"
    )

    _services._content_pack_actor_preset_list = _services.bind(
        "_content_pack_actor_preset_list", "_content_pack_actor_preset_list"
    )

    _services._content_pack_actor_preset_detail = _services.bind(
        "_content_pack_actor_preset_detail", "_content_pack_actor_preset_detail"
    )

    _services._campaign_official_addon_catalog = _services.bind(
        "_campaign_official_addon_catalog", "_campaign_official_addon_catalog"
    )

    _services._require_campaign_addon_visible = _services.bind(
        "_require_campaign_addon_visible", "_require_campaign_addon_visible"
    )

    _services._content_pack_addons = _services.bind("_content_pack_addons", "_content_pack_addons")

    _services._content_pack_addon = _services.bind("_content_pack_addon", "_content_pack_addon")

    _services._content_pack_export_rule = _services.bind(
        "_content_pack_export_rule", "_content_pack_export_rule"
    )

    _services.rulebook_draft = _services.public_tool()(
        _services.bind("rulebook_draft", "rulebook_draft")
    )

    _services.module_draft = _services.public_tool()(_services.bind("module_draft", "module_draft"))

    _services.campaign_addon_set = _services.bind("campaign_addon_set", "campaign_addon_set")

    _services.content_pack = _services.public_tool()(_services.bind("content_pack", "content_pack"))

    _services.campaign_rules = _services.public_tool()(
        _services.bind("campaign_rules", "campaign_rules")
    )

    _services.character_query = _services.public_tool()(
        _support._agent_ruling_boundary(_services.bind("character_query", "character_query"))
    )

    _services.dependent_actor_source_text = _services.bind(
        "dependent_actor_source_text", "dependent_actor_source_text"
    )

    _services.dependent_actor_numeric_parameters = _services.bind(
        "dependent_actor_numeric_parameters", "dependent_actor_numeric_parameters"
    )

    _services.addon_actor_instantiate = _services.public_tool()(
        _services.bind("addon_actor_instantiate", "addon_actor_instantiate")
    )

    _services.character_create_from = _services.public_tool()(
        _services.bind("character_create_from", "character_create_from")
    )

    _services.character_metadata_update = _services.public_tool()(
        _services.bind("character_metadata_update", "character_metadata_update")
    )

    _services.character_content_apply = _services.public_tool()(
        _services.bind("_character_content_apply_v2", "character_content_apply")
    )

    _services.inventory_change = _services.public_tool()(
        _services.bind("inventory_change", "inventory_change")
    )

    _services.inventory_transfer = _services.public_tool()(
        _services.bind("inventory_transfer", "inventory_transfer")
    )

    _services.wallet_change = _services.public_tool()(
        _services.bind("wallet_change", "wallet_change")
    )

    _services.character_state_change = _services.public_tool()(
        _services.bind("character_state_change", "character_state_change")
    )

    _services.character_revive_steel_defender = _support._agent_ruling_boundary(
        _services.bind("character_revive_steel_defender", "character_revive_steel_defender")
    )

    _services.character_action = _services.public_tool()(
        _services.bind("character_action", "character_action")
    )

    _services.character_spell_prepare = _services.public_tool()(
        _services.bind("_character_spell_prepare_v2", "character_spell_prepare")
    )

    _services.resolution_presentation = _services.public_tool()(
        _services.bind("resolution_presentation", "resolution_presentation")
    )

    _services.campaign_query = _services.public_tool()(
        _services.bind("campaign_query", "campaign_query")
    )

    _services.campaign_change = _services.public_tool()(
        _services.bind("campaign_change", "campaign_change")
    )
    _services.environment_change = _services.public_tool()(
        _services.bind("environment_change", "environment_change")
    )

    _services.playthrough_runtime_projection = _services.bind(
        "playthrough_runtime_projection", "playthrough_runtime_projection"
    )

    _services.sync_playthrough_manifest = _services.bind(
        "sync_playthrough_manifest", "sync_playthrough_manifest"
    )

    _services.playthrough_path_value = _services.bind(
        "playthrough_path_value", "playthrough_path_value"
    )

    _services.compare_playthrough_value = _services.bind(
        "compare_playthrough_value", "compare_playthrough_value"
    )

    _services.verify_playthrough_ending = _services.bind(
        "verify_playthrough_ending", "verify_playthrough_ending"
    )

    _services.playthrough_manifest = _services.public_tool()(
        _services.bind("playthrough_manifest", "playthrough_manifest")
    )

    _services.access_grant = _services.public_tool()(_services.bind("access_grant", "access_grant"))

    _services.access_revoke = _services.public_tool()(
        _services.bind("access_revoke", "access_revoke")
    )

    _services.campaign_event = _services.public_tool()(
        _services.bind("campaign_event", "campaign_event")
    )

    _services.memory_query = _services.public_tool()(_services.bind("memory_query", "memory_query"))

    _services.memory_change = _services.public_tool()(
        _services.bind("memory_change", "memory_change")
    )

    _services.actor_knowledge_query = _services.public_tool()(
        _services.bind("actor_knowledge_query", "actor_knowledge_query")
    )

    _services.actor_knowledge_change = _services.public_tool()(
        _services.bind("actor_knowledge_change", "actor_knowledge_change")
    )

    _services.branch_query = _services.public_tool()(_services.bind("branch_query", "branch_query"))

    _services.branch_change = _services.public_tool()(
        _services.bind("branch_change", "branch_change")
    )

    _services.snapshot_query = _services.public_tool()(
        _services.bind("snapshot_query", "snapshot_query")
    )

    _services.state_revision = _services.public_tool()(
        _services.bind("state_revision", "state_revision")
    )

    _services.combat_query = _services.public_tool()(_services.bind("combat_query", "combat_query"))

    _services.combat_movement = _services.public_tool()(
        _services.bind("combat_movement", "combat_movement")
    )

    _services.combat_hp_change = _services.public_tool()(
        _services.bind("combat_hp_change", "combat_hp_change")
    )

    _services.content_solution = _services.public_tool()(
        _services.bind("content_solution", "content_solution")
    )

    _services.combat_choice = _services.public_tool()(
        _services.bind("combat_choice", "combat_choice")
    )

    _services.combat_ready = _services.public_tool()(_services.bind("combat_ready", "combat_ready"))

    _services.skill_query = _services.public_tool()(_services.bind("skill_query", "skill_query"))

    _services.game_phase = _services.public_tool()(_services.bind("game_phase", "game_phase"))

    _services.registered_tools = _services.mcp._tool_manager.list_tools()
    _support.validate_profile_coverage(
        [*(tool.name for tool in _services.registered_tools), "exposure"]
    )
    for _services.registered_tool in _services.registered_tools:
        _services.parameters = _support.deepcopy(_services.registered_tool.parameters)
        _services.properties = _services.parameters.get("properties") or {}
        for _services.parameter_name, _services.parameter_schema in _services.properties.items():
            _services.parameter_schema.setdefault(
                "description",
                _support._parameter_description(
                    _services.registered_tool.name, _services.parameter_name
                ),
            )
            if _services.parameter_name in {"limit", "top_k", "conversation_limit"}:
                _services.parameter_schema.update({"minimum": 1, "maximum": 100})
            elif _services.parameter_name == "offset":
                _services.parameter_schema.update({"minimum": 0, "maximum": 100_000})
            elif _services.parameter_name in {"query", "name", "label", "identifier"}:
                _services.parameter_schema.setdefault("maxLength", 256)
            elif _services.parameter_name == "cursor":
                _services.parameter_schema.setdefault("maxLength", 1024)
            elif _services.parameter_name.endswith("_id") or _services.parameter_name in {
                "action",
                "kind",
                "idempotency_key",
            }:
                _services.parameter_schema.setdefault("maxLength", 256)
            if _services.parameter_schema.get("type") == "array":
                _services.parameter_schema.setdefault("maxItems", _support._MAX_COLLECTION_ITEMS)
        if _services.registered_tool.name == "module_draft":
            _services.parameters = _support._module_draft_parameters(_services.parameters)
        _services.registered_tool.parameters = action_parameters(
            _services.registered_tool.name, _services.parameters
        )
        _services.registered_tool.__dict__.pop("output_schema", None)
        _services.registered_tool.fn_metadata.output_schema = _support._tool_output_schema(
            _services.registered_tool.name
        )
        if _services.registered_tool.annotations is None:
            _services.read_only = any(
                marker in _services.registered_tool.name
                for marker in ("query", "search", "status", "list", "expand", "capabilities")
            )
            _services.registered_tool.annotations = _support.ToolAnnotations(
                read_only_hint=_services.read_only,
                destructive_hint=any(
                    marker in _services.registered_tool.name
                    for marker in ("remove", "revoke", "restore", "end")
                ),
                idempotent_hint=_services.read_only or "idempotency_key" in _services.properties,
                open_world_hint=False,
            )
        _services.registered_tool.meta = {
            **dict(_services.registered_tool.meta or {}),
            "sagasmith_domain_context": "sagasmith-dnd",
        }
        if "expected_campaign_revision" in _services.properties:
            _services.registered_tool.meta["sagasmith_campaign_revision_argument"] = (
                "expected_campaign_revision"
            )
        elif "campaign_id" in _services.properties and "expected_revision" in _services.properties:
            _services.registered_tool.meta["sagasmith_campaign_revision_argument"] = (
                "expected_revision"
            )
        if _services.registered_tool.name == "campaign_query":
            _services.registered_tool.meta["sagasmith_context_sync"] = True

    if _services.config.local_authority:
        from .local_session import LocalSession
        from .tool_profiles import LOCAL_DAILY_TOOLS, policy_for_tool

        _services.mcp.local_session = LocalSession(_services.mcp, _services)
        for operation in _services.mcp.list_tools():
            operation.meta["sagasmith_local_authority"] = True
            policy = policy_for_tool(operation.name)
            operation.meta["sagasmith_local_daily"] = operation.name in LOCAL_DAILY_TOOLS
            operation.meta["sagasmith_phases"] = sorted(policy.phases) if policy else []
    return _services.mcp
