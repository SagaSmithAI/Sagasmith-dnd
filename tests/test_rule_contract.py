from __future__ import annotations

import pytest

from sagasmith_dnd.rule_contract import (
    RuleContractError,
    compile_rule_clauses,
    rule_clause_templates,
    validate_rule_clause_coverage,
)


def _citation(excerpt: str = "You gain proficiency in the Arcana skill.") -> dict:
    return {
        "source": "rule-source:extension",
        "source_ref": {"chunk_id": "chunk:feature"},
        "source_excerpt": excerpt,
    }


def _clause(
    clause_id: str,
    *,
    scope: str,
    settlement: dict,
    excerpt: str = "You gain proficiency in the Arcana skill.",
) -> dict:
    return {
        "schema_version": 1,
        "id": clause_id,
        "title": clause_id.replace("-", " ").title(),
        "scope": scope,
        "source_citations": [_citation(excerpt)],
        "settlement": settlement,
    }


def test_static_grant_clause_covers_existing_structured_card_data() -> None:
    artifact = {
        "id": "dnd5e.extension.background.sage",
        "card": {
            "name": "Sage",
            "background_grants": {"skills": ["arcana", "history"]},
        },
    }
    clauses = compile_rule_clauses(
        [
            _clause(
                "skill-proficiencies",
                scope="mechanical",
                settlement={
                    "mode": "static_grant",
                    "grant_refs": ["card.background_grants.skills"],
                },
            )
        ]
    )

    assert (
        validate_rule_clause_coverage(
            clauses,
            artifact=artifact,
            require_mechanical_clause=True,
        )
        == []
    )
    assert compile_rule_clauses(rule_clause_templates(clauses)) == clauses


def test_mixed_rule_card_assigns_plans_kernel_rules_and_agent_judgment() -> None:
    clauses = compile_rule_clauses(
        [
            _clause(
                "saving-throw",
                scope="mechanical",
                settlement={
                    "mode": "primitive_plan",
                    "plan_ids": ["dnd5e.extension.plan.prismatic-pulse"],
                },
                excerpt=("Each creature in the pulse must make a Wisdom saving throw."),
            ),
            _clause(
                "concentration",
                scope="mechanical",
                settlement={
                    "mode": "kernel_mechanic",
                    "mechanic_refs": ["dnd5e.core.spell.concentration"],
                },
                excerpt="The effect requires concentration for up to 1 minute.",
            ),
            _clause(
                "unusual-cover",
                scope="mechanical",
                settlement={
                    "mode": "agent_ruling",
                    "default_resolver": "agent",
                    "ruling_kind": "environmental_consequence",
                    "reason": ("The source delegates unusual obstructions to the DM."),
                },
                excerpt=("The DM decides whether an unusual obstruction blocks the pulse."),
            ),
            _clause(
                "appearance",
                scope="descriptive",
                settlement={"mode": "descriptive"},
                excerpt="The pulse appears as a ring of violet sparks.",
            ),
        ]
    )

    assert (
        validate_rule_clause_coverage(
            clauses,
            artifact={"card": {"name": "Prismatic Pulse"}},
            plan_ids={"dnd5e.extension.plan.prismatic-pulse"},
            mechanic_refs={"dnd5e.core.spell.concentration"},
            require_mechanical_clause=True,
        )
        == []
    )


def test_clause_coverage_rejects_unassigned_or_missing_execution_paths() -> None:
    clauses = compile_rule_clauses(
        [
            _clause(
                "damage",
                scope="mechanical",
                settlement={
                    "mode": "primitive_plan",
                    "plan_ids": ["plan.expected"],
                },
            )
        ]
    )

    errors = validate_rule_clause_coverage(
        clauses,
        artifact={"card": {"name": "Pulse"}},
        plan_ids={"plan.unassigned"},
        mechanic_refs={"kernel.unassigned"},
        require_mechanical_clause=True,
    )

    assert "references unavailable plans: plan.expected" in "\n".join(errors)
    assert "resolution plans are not assigned" in "\n".join(errors)
    assert "mechanic refs are not assigned" in "\n".join(errors)


def test_descriptive_scope_cannot_hide_a_mechanical_settlement() -> None:
    with pytest.raises(RuleContractError, match="requires a mechanical"):
        compile_rule_clauses(
            [
                _clause(
                    "hidden-mechanic",
                    scope="descriptive",
                    settlement={
                        "mode": "kernel_mechanic",
                        "mechanic_refs": ["dnd5e.core.hidden"],
                    },
                )
            ]
        )
