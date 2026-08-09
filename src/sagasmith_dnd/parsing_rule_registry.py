"""Auditable registry for non-trivial D&D parsing rules.

This registry describes parser behavior; it does not make a rule valid merely
because the rule is listed. ``legacy_candidate`` entries remain frozen until
cross-source evidence either justifies a narrower rule or moves the behavior to
a source review fixture.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar

_ParserCallable = TypeVar("_ParserCallable", bound=Callable[..., object])

RULE_CATEGORIES = frozenset(
    {
        "document_invariant",
        "dnd_grammar",
        "ruleset_vocabulary",
        "source_review",
        "legacy_candidate",
    }
)


@dataclass(frozen=True, slots=True)
class ParsingRule:
    rule_id: str
    owner_layer: str
    category: str
    description: str
    evidence: tuple[str, ...]
    affected_formats: tuple[str, ...]
    known_counterexamples: tuple[str, ...]
    confidence: str
    fallback: str
    tests: tuple[str, ...]
    introduced_by: str


def _retired_heuristic(
    rule_id: str,
    description: str,
    counterexample: str,
    *tests: str,
) -> ParsingRule:
    """Describe a removed inference without making it executable behavior."""

    return ParsingRule(
        rule_id=rule_id,
        owner_layer="sagasmith-dnd",
        category="legacy_candidate",
        description=description,
        evidence=("Historic source-specific failures motivated this heuristic.",),
        affected_formats=("pdf-text", "pdf-ocr"),
        known_counterexamples=(counterexample,),
        confidence="rejected",
        fallback="Preserve source chunks and require an exact replayable source-review decision.",
        tests=tests or ("tests/test_parser_strict_boundaries.py",),
        introduced_by="working-tree:2026-08-09-strict-convergence",
    )


PARSING_RULE_REGISTRY = (
    ParsingRule(
        rule_id="core.layout.preserve_physical_page_spans",
        owner_layer="sagasmith-core",
        category="document_invariant",
        description="Preserve physical page markers and exact source spans during normalization.",
        evidence=(
            "Page membership and source offsets are directly recoverable from document evidence.",
        ),
        affected_formats=("pdf-text", "pdf-ocr", "markdown", "plain-text"),
        known_counterexamples=(
            "Documents without physical pages cannot supply a PDF page span and retain their "
            "native single-source boundary instead.",
        ),
        confidence="high",
        fallback="Keep the original source boundary and mark unavailable page evidence absent.",
        tests=("sagasmith-core/tests/test_state_documents.py",),
        introduced_by="working-tree:2026-08-09",
    ),
    ParsingRule(
        rule_id="core.layout.order_columns_by_geometry",
        owner_layer="sagasmith-core",
        category="document_invariant",
        description="Recover independent PDF columns from positioned text geometry.",
        evidence=("Text block coordinates provide system-neutral reading-order evidence.",),
        affected_formats=("pdf-text", "pdf-ocr"),
        known_counterexamples=(
            "Decorative or overlapping layouts can remain ambiguous even when coordinates exist.",
        ),
        confidence="high",
        fallback=(
            "Preserve blocks and coordinates and require layout review when order is ambiguous."
        ),
        tests=("sagasmith-core/tests/test_pdf_rendering.py",),
        introduced_by="working-tree:2026-08-09",
    ),
    ParsingRule(
        rule_id="core.ocr.normalize_character_damage",
        owner_layer="sagasmith-core",
        category="document_invariant",
        description=(
            "Normalize bounded character-encoding and OCR damage without inferring meaning."
        ),
        evidence=("Character substitutions and invalid code points are observable text damage.",),
        affected_formats=("pdf-text", "pdf-ocr", "plain-text"),
        known_counterexamples=(
            "A valid uncommon glyph can resemble OCR damage and must remain untouched without a "
            "bounded replacement rule.",
        ),
        confidence="medium",
        fallback="Retain raw text and emit a review marker when a replacement is not provable.",
        tests=("sagasmith-core/tests/test_state_documents.py",),
        introduced_by="working-tree:2026-08-09",
    ),
    ParsingRule(
        rule_id="core.layout.detect_repeated_page_margins",
        owner_layer="sagasmith-core",
        category="document_invariant",
        description="Identify repeated page-margin lines without interpreting document semantics.",
        evidence=("The same normalized line recurs at a consistent physical page margin.",),
        affected_formats=("pdf-text", "pdf-ocr"),
        known_counterexamples=("A repeated section title can legitimately occur near page edges.",),
        confidence="medium",
        fallback="Preserve the line when repetition and position do not jointly support removal.",
        tests=("sagasmith-core/tests/test_pdf_rendering.py",),
        introduced_by="working-tree:2026-08-09-strict-convergence",
    ),
    ParsingRule(
        rule_id="core.layout.recover_visual_headings",
        owner_layer="sagasmith-core",
        category="document_invariant",
        description=(
            "Recover heading candidates from font, geometry, spacing, and bookmark evidence."
        ),
        evidence=("Typography and page geometry directly distinguish a displayed text block.",),
        affected_formats=("pdf-text", "pdf-ocr"),
        known_counterexamples=("Decorative callouts can share heading typography.",),
        confidence="medium",
        fallback="Retain the block as ordinary text when layout signals conflict.",
        tests=("sagasmith-core/tests/test_pdf_rendering.py",),
        introduced_by="working-tree:2026-08-09-strict-convergence",
    ),
    ParsingRule(
        rule_id="core.ocr.select_quality_fallback",
        owner_layer="sagasmith-core",
        category="document_invariant",
        description="Select OCR text only when observable quality improves over the source layer.",
        evidence=(
            "Character validity, text density, and layout coverage are directly measurable.",
        ),
        affected_formats=("pdf-text", "pdf-ocr"),
        known_counterexamples=(
            "A fluent OCR result can still replace a rare valid glyph incorrectly.",
        ),
        confidence="medium",
        fallback="Keep raw text and expose the suspect page for review.",
        tests=("sagasmith-core/tests/test_pdf_rendering.py",),
        introduced_by="working-tree:2026-08-09-strict-convergence",
    ),
    ParsingRule(
        rule_id="core.layout.reflow_positioned_text",
        owner_layer="sagasmith-core",
        category="document_invariant",
        description=(
            "Reflow positioned lines while retaining physical source spans and coordinates."
        ),
        evidence=("Line baselines, bounding boxes, and spacing provide local ordering evidence.",),
        affected_formats=("pdf-text", "pdf-ocr"),
        known_counterexamples=(
            "Overlapping tables and illustrations can make reading order ambiguous.",
        ),
        confidence="medium",
        fallback=(
            "Preserve positioned blocks and require layout review rather than semantic guessing."
        ),
        tests=("sagasmith-core/tests/test_pdf_rendering.py",),
        introduced_by="working-tree:2026-08-09-strict-convergence",
    ),
    ParsingRule(
        rule_id="dnd.statblock.official_field_grammar",
        owner_layer="sagasmith-dnd",
        category="dnd_grammar",
        description="Recognize versioned D&D statblock fields and validate their formal structure.",
        evidence=("Published 5e statblocks use a stable set of named mechanical fields.",),
        affected_formats=("pdf-text", "pdf-ocr", "markdown"),
        known_counterexamples=(
            "Narrative prose can quote several field labels without being a complete statblock.",
        ),
        confidence="high",
        fallback="Emit a catalog-only candidate or source fragment; do not compile an actor.",
        tests=("tests/test_statblocks.py", "tests/test_content_import.py"),
        introduced_by="working-tree:2026-08-09",
    ),
    ParsingRule(
        rule_id="dnd.ruleset.versioned_parsing_vocabulary",
        owner_layer="sagasmith-dnd",
        category="ruleset_vocabulary",
        description="Use edition-pinned D&D class, field, item, and subclass-parent vocabulary.",
        evidence=("These terms are defined by the 2014 or 2024 ruleset, not by one source book.",),
        affected_formats=("pdf-text", "pdf-ocr", "markdown"),
        known_counterexamples=(
            "Third-party classes and future rules revisions can use vocabulary outside a "
            "pinned set.",
        ),
        confidence="high",
        fallback="Keep unknown terms as review-required source content.",
        tests=("tests/test_parsing_vocabulary.py",),
        introduced_by="working-tree:2026-08-09",
    ),
    ParsingRule(
        rule_id="source.review.replay_exact_selectors",
        owner_layer="SagaSmith-dnd-mcp",
        category="source_review",
        description=(
            "Replay source-specific split, merge, correction, acceptance, and rejection decisions "
            "through exact selectors."
        ),
        evidence=(
            "Reviewed fixtures retain page, heading, content, and checksum-bound provenance.",
        ),
        affected_formats=("normalized-catalog",),
        known_counterexamples=(
            "A changed source edition or extraction checksum can invalidate an otherwise exact "
            "selector."
        ),
        confidence="high",
        fallback="Fail closed and request a new source review decision.",
        tests=("SagaSmith-dnd-mcp/tests/test_regression_rulebooks_driver.py",),
        introduced_by="working-tree:2026-08-09",
    ),
    ParsingRule(
        rule_id="dnd.content.explicit_heading_entity_candidates",
        owner_layer="sagasmith-dnd",
        category="dnd_grammar",
        description="Create review candidates from explicit D&D headings and field evidence.",
        evidence=("The source span itself names the entity and contains kind-specific fields.",),
        affected_formats=("pdf-text", "pdf-ocr", "markdown"),
        known_counterexamples=("Quoted examples can reproduce a complete-looking field group.",),
        confidence="medium",
        fallback="Keep the candidate catalog-only and require source review.",
        tests=("tests/test_content_import.py",),
        introduced_by="working-tree:2026-08-09-strict-convergence",
    ),
    ParsingRule(
        rule_id="dnd.content.merge_same_structural_identity",
        owner_layer="sagasmith-dnd",
        category="dnd_grammar",
        description="Merge chunks only when kind, normalized identity, and heading context agree.",
        evidence=("A repeated structural identity can span multiple extracted source chunks.",),
        affected_formats=("pdf-text", "pdf-ocr", "markdown"),
        known_counterexamples=(
            "A source can print two distinct entries with the same identity path.",
        ),
        confidence="medium",
        fallback=(
            "Use exact source-review additions or split decisions for repeated printed entries."
        ),
        tests=("tests/test_content_import.py",),
        introduced_by="working-tree:2026-08-09-strict-convergence",
    ),
    ParsingRule(
        rule_id="dnd.spell.explicit_list_ownership",
        owner_layer="sagasmith-dnd",
        category="dnd_grammar",
        description="Assign spell-list membership only from an explicit unique class label.",
        evidence=("The current heading or list section directly names one D&D class.",),
        affected_formats=("pdf-text", "pdf-ocr", "markdown"),
        known_counterexamples=("Multi-column pages can flatten several class labels together.",),
        confidence="high",
        fallback="Leave ambiguous list ownership for review without assigning a class.",
        tests=("tests/test_content_import.py",),
        introduced_by="working-tree:2026-08-09-strict-convergence",
    ),
    ParsingRule(
        rule_id="dnd.spell.adjacent_explicit_field_continuation",
        owner_layer="sagasmith-dnd",
        category="dnd_grammar",
        description=(
            "Attach an adjacent spell chunk only when it starts with a missing formal field."
        ),
        evidence=("The continuation begins with the exact missing D&D spell field label.",),
        affected_formats=("pdf-text", "pdf-ocr"),
        known_counterexamples=("A new spell can begin after a malformed prior spell field group.",),
        confidence="medium",
        fallback="Keep both chunks separately reviewable when the field boundary is incomplete.",
        tests=("tests/test_content_import.py",),
        introduced_by="working-tree:2026-08-09-strict-convergence",
    ),
    ParsingRule(
        rule_id="dnd.spell.explicit_declaration_ownership",
        owner_layer="sagasmith-dnd",
        category="dnd_grammar",
        description=(
            "Assign spell classes from prose that explicitly states membership in "
            "named spell lists."
        ),
        evidence=("The source sentence directly names the spell or its exact ancestor group.",),
        affected_formats=("pdf-text", "pdf-ocr", "markdown"),
        known_counterexamples=(
            "A quoted declaration can describe an example rather than a catalog entry.",
        ),
        confidence="medium",
        fallback=(
            "Keep classes empty and require source review when the declaration scope is ambiguous."
        ),
        tests=("tests/test_content_import.py",),
        introduced_by="working-tree:2026-08-09-strict-convergence",
    ),
    ParsingRule(
        rule_id="dnd.statblock.scope_complete_field_group",
        owner_layer="sagasmith-dnd",
        category="dnd_grammar",
        description="Scope a statblock using a complete local group of official fields.",
        evidence=(
            "Creature core fields and all six ability fields are directly present in scope.",
        ),
        affected_formats=("pdf-text", "pdf-ocr", "markdown"),
        known_counterexamples=(
            "A surrounding rules table can contain the same labels as examples.",
        ),
        confidence="medium",
        fallback="Retain a catalog-only candidate and require identity or boundary review.",
        tests=("tests/test_content_import.py", "tests/test_statblocks.py"),
        introduced_by="working-tree:2026-08-09-strict-convergence",
    ),
    ParsingRule(
        rule_id="dnd.ocr.bounded_schema_field_repairs",
        owner_layer="sagasmith-dnd",
        category="dnd_grammar",
        description="Repair OCR only inside formal D&D field and formula positions.",
        evidence=("The surrounding field grammar uniquely constrains the damaged token.",),
        affected_formats=("pdf-ocr",),
        known_counterexamples=("The same glyph sequence in narrative prose can be intentional.",),
        confidence="medium",
        fallback="Preserve the raw token and require source review.",
        tests=("tests/test_statblocks.py", "tests/test_content_import.py"),
        introduced_by="working-tree:2026-08-09-strict-convergence",
    ),
    ParsingRule(
        rule_id="dnd.heading_path.split_by_occurrence",
        owner_layer="sagasmith-dnd",
        category="legacy_candidate",
        description=(
            "Treat non-contiguous chunks with the same exact heading path as separate "
            "semantic sections. The automatic split has been disabled because physical "
            "occurrence does not prove entity identity."
        ),
        evidence=(
            "PHB contains repeated exact heading paths that represent distinct printed entries.",
        ),
        affected_formats=("pdf-text", "pdf-ocr"),
        known_counterexamples=(
            "DMG repeats exact heading paths for continuations and gains four source fragments "
            "when occurrence implies semantic identity.",
        ),
        confidence="rejected",
        fallback="Preserve source spans and require a source-bound split/merge review decision.",
        tests=(
            "tests/test_content_import.py::"
            "test_same_named_features_require_review_when_flat_outline_loses_ownership",
        ),
        introduced_by="working-tree:2026-08-09",
    ),
    ParsingRule(
        rule_id="dnd.feature.merge_by_source_overlap",
        owner_layer="sagasmith-dnd",
        category="legacy_candidate",
        description=(
            "Merge same-named feature candidates only when their source chunk identifiers overlap. "
            "The automatic split has been disabled because chunk overlap is a layout artifact, "
            "not semantic identity evidence."
        ),
        evidence=("Flat PDF outlines can omit subclass ownership for repeated feature names.",),
        affected_formats=("pdf-text", "pdf-ocr"),
        known_counterexamples=(
            "A single feature split across disjoint source chunks has no overlap even though it is "
            "one printed entity.",
        ),
        confidence="rejected",
        fallback="Keep the candidates review-required and replay a source-bound merge decision.",
        tests=("tests/test_content_import.py",),
        introduced_by="working-tree:2026-08-09",
    ),
    ParsingRule(
        rule_id="dnd.feature.bind_nearest_subclass",
        owner_layer="sagasmith-dnd",
        category="legacy_candidate",
        description=(
            "Bind an unreviewed feature to the nearest preceding subclass in the same heading "
            "container. The automatic binding has been disabled."
        ),
        evidence=(
            "Some flattened class chapters place subclass features after a sibling "
            "subclass heading.",
        ),
        affected_formats=("pdf-text", "pdf-ocr"),
        known_counterexamples=(
            "Columns, sidebars, and repeated containers can put unrelated prose after a subclass "
            "without an ownership span.",
        ),
        confidence="rejected",
        fallback=(
            "Leave subclass_name absent until an exact source-bound review supplies ownership."
        ),
        tests=(
            "tests/test_content_import.py::"
            "test_feature_does_not_bind_nearest_subclass_in_flat_source_container",
        ),
        introduced_by="working-tree:2026-08-09",
    ),
    ParsingRule(
        rule_id="dnd.statblock.infer_dependent_owner_from_context",
        owner_layer="sagasmith-dnd",
        category="legacy_candidate",
        description=(
            "Infer a dependent statblock owner from neighboring catalog candidates. The "
            "automatic inference has been disabled."
        ),
        evidence=(
            "Some summoned creature templates name no owner inside the flattened statblock body.",
        ),
        affected_formats=("pdf-text", "pdf-ocr"),
        known_counterexamples=(
            "Adjacent class material can belong to a different option after column flattening.",
        ),
        confidence="rejected",
        fallback="Emit owner_selection and require a reviewed exact source binding.",
        tests=("tests/test_content_import.py", "tests/test_statblocks.py"),
        introduced_by="working-tree:2026-08-09",
    ),
    ParsingRule(
        rule_id="dnd.background.reject_generic_title",
        owner_layer="sagasmith-dnd",
        category="legacy_candidate",
        description=(
            "Reject background candidates solely because their title is generic. The title-only "
            "rejection has been disabled; affirmative background structure is still required."
        ),
        evidence=(
            "Generic section labels in class and NPC-building chapters produced false backgrounds.",
        ),
        affected_formats=("pdf-text", "pdf-ocr"),
        known_counterexamples=(
            "A published background can have a short generic display title; title alone is not "
            "source evidence.",
        ),
        confidence="rejected",
        fallback="Keep the candidate review-required and decide it in the source fixture.",
        tests=("tests/test_content_import.py",),
        introduced_by="working-tree:2026-08-09",
    ),
    _retired_heuristic(
        "dnd.heading.merge_adjacent_option_fragments",
        "Join adjacent empty and populated feat headings into one inferred identity; disabled.",
        "Adjacent headings can be separate options after column or page flattening.",
    ),
    _retired_heuristic(
        "dnd.class.bound_by_ordered_anchors",
        "Build class entities from ordered Class Features anchors and fixed lookback "
        "windows; disabled.",
        "Flattened sibling sections do not prove where one class ends and the next begins.",
    ),
    _retired_heuristic(
        "dnd.species.bound_by_order_and_languages",
        "Build species and subrace entities from source order and a Languages stop "
        "marker; disabled.",
        "Languages can be absent, reordered, or followed by additional traits in other layouts.",
    ),
    _retired_heuristic(
        "dnd.item.merge_adjacent_suffix_continuation",
        "Merge an adjacent feature into an item by title suffix or running chapter "
        "header; disabled.",
        "A same-page suffix heading can be an independent rule or unrelated column content.",
    ),
    _retired_heuristic(
        "dnd.subclass.merge_table_by_edit_distance",
        "Merge a subclass spell table into a nearby identity by one-edit name "
        "similarity; disabled.",
        "Near-identical names in one container do not prove a shared semantic identity.",
    ),
    _retired_heuristic(
        "dnd.spell.assign_class_by_column_order",
        "Assign spell-list classes by printed class order and repeated level resets; disabled.",
        "Column flattening can interleave lists or reset levels for a reason unrelated "
        "to ownership.",
    ),
    _retired_heuristic(
        "dnd.statblock.name_from_preceding_window",
        "Infer an unnamed statblock identity from prose in the preceding twenty chunks; disabled.",
        "Same-page prose can refer to another statblock or example after extraction reordering.",
    ),
    _retired_heuristic(
        "dnd.feature.owner_from_free_text_class_mention",
        "Infer class ownership from any class mention in title or the first prose "
        "window; disabled.",
        "Rules prose routinely mentions other classes without assigning ownership.",
    ),
    _retired_heuristic(
        "dnd.ocr.repair_named_entry_text",
        "Repair punctuation or text by matching one named feature, spell, creature, "
        "or subclass; disabled.",
        "An entry-specific correction has no cross-source grammar and belongs in source review.",
        "tests/test_statblocks.py::test_entry_specific_ocr_punctuation_is_left_for_source_review",
    ),
)


def registered_parsing_rule(rule_id: str) -> Callable[[_ParserCallable], _ParserCallable]:
    """Bind executable parser behavior to one active audited registry entry."""

    rules = {rule.rule_id: rule for rule in PARSING_RULE_REGISTRY}
    rule = rules.get(rule_id)
    if rule is None:
        raise ValueError(f"unregistered parsing rule: {rule_id}")
    if rule.category == "legacy_candidate" or rule.confidence == "rejected":
        raise ValueError(f"retired parsing rule cannot be executable: {rule_id}")

    def bind(function: _ParserCallable) -> _ParserCallable:
        setattr(function, "__parsing_rule_id__", rule_id)
        return function

    return bind


def validate_parsing_rule_registry() -> None:
    """Fail closed when registry entries are incomplete or ambiguous."""

    seen: set[str] = set()
    for rule in PARSING_RULE_REGISTRY:
        if not rule.rule_id or rule.rule_id in seen:
            raise ValueError(f"duplicate or empty parsing rule id: {rule.rule_id!r}")
        seen.add(rule.rule_id)
        if rule.category not in RULE_CATEGORIES:
            raise ValueError(f"unsupported parsing rule category: {rule.category!r}")
        if not rule.owner_layer or not rule.description or not rule.confidence:
            raise ValueError(f"incomplete parsing rule: {rule.rule_id}")
        if not rule.evidence or not rule.affected_formats:
            raise ValueError(f"parsing rule has no evidence or formats: {rule.rule_id}")
        if not rule.known_counterexamples or not rule.fallback or not rule.tests:
            raise ValueError(
                f"parsing rule has no counterexample, fallback, or tests: {rule.rule_id}"
            )
        if not rule.introduced_by:
            raise ValueError(f"parsing rule has no introduction provenance: {rule.rule_id}")
