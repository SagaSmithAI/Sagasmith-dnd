import pytest

from sagasmith_dnd.content_import import (
    _candidate_class_name,
    _classify,
    _merge_extracted_candidates,
    _rulebook_statblock_candidates,
    _spell_class_index,
    _spell_class_mentions,
    _spell_text_and_field_continuations,
)
from sagasmith_dnd.parsing_rule_registry import (
    PARSING_RULE_REGISTRY,
    registered_parsing_rule,
    validate_parsing_rule_registry,
)
from sagasmith_dnd.statblocks import _repair_layout_ocr_text


def test_parsing_rule_registry_is_complete_and_unique() -> None:
    validate_parsing_rule_registry()


def test_high_risk_heuristics_remain_legacy_candidates() -> None:
    rules = {rule.rule_id: rule for rule in PARSING_RULE_REGISTRY}
    high_risk_rule_ids = {
        "dnd.background.reject_generic_title",
        "dnd.class.bound_by_ordered_anchors",
        "dnd.feature.bind_nearest_subclass",
        "dnd.feature.merge_by_source_overlap",
        "dnd.feature.owner_from_free_text_class_mention",
        "dnd.heading_path.split_by_occurrence",
        "dnd.heading.merge_adjacent_option_fragments",
        "dnd.item.merge_adjacent_suffix_continuation",
        "dnd.ocr.repair_named_entry_text",
        "dnd.species.bound_by_order_and_languages",
        "dnd.spell.assign_class_by_column_order",
        "dnd.statblock.name_from_preceding_window",
        "dnd.statblock.infer_dependent_owner_from_context",
        "dnd.subclass.merge_table_by_edit_distance",
    }
    assert high_risk_rule_ids <= set(rules)
    assert {rules[rule_id].category for rule_id in high_risk_rule_ids} == {
        "legacy_candidate"
    }
    assert {rules[rule_id].confidence for rule_id in high_risk_rule_ids} == {"rejected"}


def test_registry_covers_every_allowed_rule_category() -> None:
    assert {rule.category for rule in PARSING_RULE_REGISTRY} == {
        "document_invariant",
        "dnd_grammar",
        "ruleset_vocabulary",
        "source_review",
        "legacy_candidate",
    }


def test_only_rejected_rules_remain_legacy_candidates() -> None:
    assert {
        rule.confidence for rule in PARSING_RULE_REGISTRY if rule.category == "legacy_candidate"
    } == {"rejected"}


def test_executable_nontrivial_rules_are_bound_to_active_registry_entries() -> None:
    functions = {
        _candidate_class_name,
        _classify,
        _merge_extracted_candidates,
        _repair_layout_ocr_text,
        _rulebook_statblock_candidates,
        _spell_class_index,
        _spell_class_mentions,
        _spell_text_and_field_continuations,
    }
    active_rule_ids = {
        rule.rule_id
        for rule in PARSING_RULE_REGISTRY
        if rule.category != "legacy_candidate" and rule.confidence != "rejected"
    }
    assert {
        function.__parsing_rule_id__  # type: ignore[attr-defined]
        for function in functions
    } <= active_rule_ids


def test_registry_binding_rejects_missing_and_retired_rules() -> None:
    with pytest.raises(ValueError, match="unregistered parsing rule"):
        registered_parsing_rule("missing.rule")
    with pytest.raises(ValueError, match="retired parsing rule"):
        registered_parsing_rule("dnd.class.bound_by_ordered_anchors")
