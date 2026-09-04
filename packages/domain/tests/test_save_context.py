from copy import deepcopy

import pytest

from sagasmith_dnd.resolution_plan import (
    ResolutionPlanCompilationError,
    bind_resolution_plan,
    compile_resolution_plan,
    execute_resolution_plan,
    resolution_plan_template,
)
from sagasmith_dnd.save_context import validated_save_source_facts

SOURCE = "custom:test-save-source"
SOURCE_REF = {"path": SOURCE, "line_start": 1, "line_end": 20}
EXCERPT = "Custom source records the target's Wisdom saving throw effect."
CITATIONS = ({"source": SOURCE, "source_ref": SOURCE_REF, "source_excerpt": EXCERPT},)


def _source(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "source": SOURCE,
        "source_ref": deepcopy(SOURCE_REF),
        "source_excerpt": EXCERPT,
        "save_source_kind": "spell",
        "save_effect_conditions": ["charmed", "incapacitated"],
        "save_against_poison": False,
    }
    value.update(overrides)
    return value


def _plan(source: object = None, *, source_card_kind: str = "spell") -> dict[str, object]:
    args: dict[str, object] = {
        "target_ids": ["target"],
        "ability": "wisdom",
        "dc": 14,
    }
    if source is not None:
        args["source"] = source
    return {
        "schema_version": 2,
        "id": "hypnotic-save",
        "source_card_id": "spell.hypnotic_pattern",
        "source_card_kind": source_card_kind,
        "trigger": "action",
        "slots": {},
        "steps": [{"id": "save", "op": "check.save", "args": args}],
        "citations": [deepcopy(item) for item in CITATIONS],
    }


def test_legacy_source_is_unchanged_and_valid_source_returns_only_internal_facts() -> None:
    assert validated_save_source_facts(None, citations=CITATIONS, source_card_kind="spell") == {}
    assert (
        validated_save_source_facts("legacy source", citations=CITATIONS, source_card_kind="spell")
        == {}
    )
    original = _source()
    facts = validated_save_source_facts(original, citations=CITATIONS, source_card_kind="spell")
    assert facts == {
        "save_source_kind": "spell",
        "save_effect_conditions": ["charmed", "incapacitated"],
        "save_against_poison": False,
    }
    assert original == _source()
    facts["save_effect_conditions"].append("frightened")
    assert original["save_effect_conditions"] == ["charmed", "incapacitated"]


def test_compile_validates_without_storing_redundant_context_and_roundtrips() -> None:
    plan = compile_resolution_plan(_plan(_source()))
    step = plan.steps[0]
    assert "save_context" not in step
    legacy = compile_resolution_plan(_plan())
    assert compile_resolution_plan(resolution_plan_template(plan)) == plan
    assert "save_context" not in legacy.steps[0]
    assert plan.fingerprint != legacy.fingerprint


def test_bound_execution_keeps_validated_source_object_for_runtime() -> None:
    plan = compile_resolution_plan(_plan(_source()))
    bound = bind_resolution_plan(plan, {})
    seen: list[dict[str, object]] = []

    class Runtime:
        def begin(self, _plan: object) -> None:
            return None

        def execute(
            self,
            opcode: str,
            arguments: dict[str, object],
            *,
            step_id: str,
            prior_results: dict[str, object],
        ) -> dict[str, object]:
            assert opcode == "check.save"
            assert step_id == "save"
            assert not prior_results
            seen.append(arguments)
            return {"success": True}

        def commit(self) -> None:
            return None

        def rollback(self) -> None:
            return None

    result = execute_resolution_plan(bound, Runtime())
    assert seen[0]["source"] == _source()
    assert result.status == "committed"


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {**_source(), "extra": True},
        {**_source(), "source_ref": {}},
        {**_source(), "source_excerpt": "short"},
        {**_source(), "save_source_kind": "poison"},
        {**_source(), "save_effect_conditions": "charmed"},
        {**_source(), "save_effect_conditions": ["charmed", "charmed"]},
        {**_source(), "save_effect_conditions": ["poison"]},
        {**_source(), "save_against_poison": 0},
        {**_source(), "source_ref": {"$slot": "source"}},
        {**_source(), "source_ref": {"nested": {"$result": "save"}}},
    ],
)
def test_malformed_source_facts_fail_during_compile(bad: dict[str, object]) -> None:
    with pytest.raises(ResolutionPlanCompilationError):
        compile_resolution_plan(_plan(bad))


def test_source_must_match_one_citation_without_cross_citation_composition() -> None:
    second = {
        "source": "pack:other",
        "source_ref": {"path": "pack:other", "page": 2},
        "source_excerpt": "A separate source with enough text for validation.",
    }
    citations = (*CITATIONS, second)
    mixed = _source(source="pack:other", source_ref=SOURCE_REF)
    with pytest.raises(ValueError, match="exact plan citation"):
        validated_save_source_facts(mixed, citations=citations, source_card_kind="feature")


@pytest.mark.parametrize("source_kind", ["magical_effect", "nonmagical_effect"])
def test_spell_card_requires_spell_source_kind(source_kind: str) -> None:
    with pytest.raises(ValueError, match="source_kind=spell"):
        validated_save_source_facts(
            _source(save_source_kind=source_kind),
            citations=CITATIONS,
            source_card_kind="spell",
        )


def test_compile_wraps_mismatched_source_and_preserves_legacy_text() -> None:
    with pytest.raises(ResolutionPlanCompilationError, match="exact plan citation"):
        compile_resolution_plan(_plan(_source(source="other source")))
    assert "save_context" not in compile_resolution_plan(_plan("agent supplied text")).steps[0]
