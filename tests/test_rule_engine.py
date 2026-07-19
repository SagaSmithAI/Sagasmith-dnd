import json
from copy import deepcopy
from pathlib import Path

import pytest

from sagasmith_dnd.activities import consume_activity
from sagasmith_dnd.character_schema import default_character_sheet, validate_character_sheet
from sagasmith_dnd.rule_engine import (
    ALLOWED_EVENTS,
    ALLOWED_OPS,
    RuleCompilationError,
    apply_rule_event,
    resolution_context,
    run_mechanic_tests,
    validate_source_bound_mechanics,
)


def _effective(mechanics):
    return {
        "edition": "2014",
        "fingerprint": "rules-fingerprint",
        "lock": [{"pack_id": "dnd5e.xgte", "options": {}}],
        "mechanics": mechanics,
    }


def test_mechanic_schema_matches_the_runtime_capability_table() -> None:
    schema = json.loads(
        (Path(__file__).parents[1] / "schemas" / "mechanic-ir-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(schema["properties"]["event"]["enum"]) == ALLOWED_EVENTS
    opcode = schema["properties"]["operations"]["items"]["properties"]["op"]
    assert set(opcode["enum"]) == ALLOWED_OPS


def test_rule_extension_settles_whitelisted_operation_with_receipt() -> None:
    sheet = default_character_sheet()
    sheet["resources"]["test"] = {
        "label": "Test",
        "value": 0,
        "max": 2,
        "recovers_on": "none",
        "source_key": "",
        "slot_level": 0,
    }
    sheet["content"]["activities"] = [
        {
            "id": "test-action",
            "name": "Test",
            "activation": {"type": "action", "cost": 1, "trigger": ""},
            "uses": {"label": "", "value": 0, "max": 0, "recovers_on": "none", "source_key": ""},
        }
    ]
    rules = resolution_context(
        _effective(
            [
                {
                    "id": "dnd5e.xgte.test.recover",
                    "event": "activity.after",
                    "operations": [
                        {"op": "resource.recover", "path": "resources.test", "amount": 1}
                    ],
                    "citations": [{"source": "local:xgte", "section": "Test"}],
                }
            ]
        )
    )
    result = consume_activity(
        validate_character_sheet(sheet), activity_id="test-action", rules=rules
    )
    assert result["sheet"]["resources"]["test"]["value"] == 1
    assert any(
        receipt["mechanic_id"] == "dnd5e.xgte.test.recover"
        for receipt in result["rule_receipts"]
    )
    assert result["ruleset_fingerprint"] == rules.fingerprint


def test_spellbook_copy_event_accepts_only_cost_and_time_modifiers() -> None:
    rules = resolution_context(
        _effective(
            [
                {
                    "id": "dnd5e.extension.copy.discount",
                    "event": "spellbook.copy.before",
                    "predicates": [
                        {"kind": "fact_equals", "key": "spell_school", "value": "illusion"}
                    ],
                    "operations": [
                        {
                            "op": "modifier.add",
                            "target": "copy_cost_percent",
                            "value": -50,
                        },
                        {
                            "op": "modifier.add",
                            "target": "copy_time_percent",
                            "value": -50,
                        },
                    ],
                    "citations": [{"source": "local:extension", "section": "Savant"}],
                }
            ]
        ),
        facts={"spell_school": "illusion"},
    )
    result = apply_rule_event({}, "spellbook.copy.before", rules)
    assert [modifier["target"] for modifier in result.modifiers] == [
        "copy_cost_percent",
        "copy_time_percent",
    ]

    invalid = {
        "id": "dnd5e.extension.copy.invalid",
        "event": "spellbook.copy.before",
        "operations": [{"op": "modifier.add", "target": "attack_bonus", "value": 1}],
        "citations": [{"source": "local:extension"}],
    }
    with pytest.raises(RuleCompilationError, match="cannot consume modifier target"):
        resolution_context(_effective([invalid]))


def test_pending_choice_is_atomic_and_unsafe_opcode_is_rejected() -> None:
    sheet = default_character_sheet()
    rules = resolution_context(
        _effective(
            [
                {
                    "id": "dnd5e.xgte.test.choice",
                    "event": "rest.before",
                    "operations": [{"op": "choice.require", "id": "choose-recovery"}],
                    "citations": [{"source": "local:xgte", "section": "Choice"}],
                }
            ]
        )
    )
    result = apply_rule_event(sheet, "rest.before", rules)
    assert result.status == "pending_choice"
    assert result.sheet == sheet

    with pytest.raises(RuleCompilationError, match="unsupported mechanic operation"):
        resolution_context(
            _effective(
                [
                    {
                        "id": "dnd5e.xgte.test.unsafe",
                        "event": "rest.after",
                        "operations": [{"op": "python.eval", "code": "pass"}],
                        "citations": [{"source": "local:xgte", "section": "Unsafe"}],
                    }
                ]
            )
        )


def test_v2_cards_keep_pack_and_mechanic_references() -> None:
    sheet = default_character_sheet()
    sheet["content"]["features"] = [
        {
            "id": "dnd5e.xgte.feature.test",
            "name": "Test",
            "pack_id": "dnd5e.xgte",
            "pack_version": "1.0.0",
            "rule_refs": ["local:xgte#test"],
            "mechanic_refs": ["dnd5e.xgte.test.recover"],
        }
    ]
    validated = validate_character_sheet(sheet)
    feature = validated["content"]["features"][0]
    assert feature["pack_id"] == "dnd5e.xgte"
    assert feature["mechanic_refs"] == ["dnd5e.xgte.test.recover"]


def test_compiler_rejects_invalid_predicates_values_and_empty_citations() -> None:
    base = {
        "id": "dnd5e.xgte.test.invalid",
        "event": "check.before",
        "operations": [{"op": "modifier.add", "target": "check_bonus", "value": 1}],
        "citations": [{"source": "local:xgte"}],
    }
    with pytest.raises(RuleCompilationError, match="unsupported predicate"):
        resolution_context(
            _effective([{**base, "predicates": [{"kind": "python.eval"}]}])
        )
    with pytest.raises(RuleCompilationError, match="modifier.add value"):
        resolution_context(
            _effective(
                [
                    {
                        **base,
                        "operations": [
                            {"op": "modifier.add", "target": "check_bonus", "value": "1"}
                        ],
                    }
                ]
            )
        )
    with pytest.raises(RuleCompilationError, match="source citation"):
        resolution_context(_effective([{**base, "citations": [{}]}]))
    with pytest.raises(RuleCompilationError, match="operations must be a list"):
        resolution_context(
            _effective([{**base, "operations": {"op": "advantage.add"}}])
        )
    with pytest.raises(RuleCompilationError, match="priority must be an integer"):
        resolution_context(_effective([{**base, "priority": "first"}]))
    with pytest.raises(RuleCompilationError, match="cannot consume modifier target"):
        resolution_context(
            _effective(
                [
                    {
                        **base,
                        "operations": [
                            {
                                "op": "modifier.add",
                                "target": "unsupported_bonus",
                                "value": 1,
                            }
                        ],
                    }
                ]
            )
        )
    with pytest.raises(RuleCompilationError, match="unsupported mechanic event"):
        resolution_context(_effective([{**base, "event": "check.after"}]))


def test_rule_tests_require_positive_coverage_for_every_mechanic() -> None:
    mechanics = [
        {
            "id": "dnd5e.xgte.first",
            "event": "rest.before",
            "operations": [{"op": "ruling.require", "id": "first"}],
            "citations": [{"source": "local:xgte"}],
        },
        {
            "id": "dnd5e.xgte.second",
            "event": "spell.before",
            "operations": [{"op": "ruling.require", "id": "second"}],
            "citations": [{"source": "local:xgte"}],
        },
    ]
    report = run_mechanic_tests(
        mechanics,
        [
            {
                "name": "only first",
                "event": "rest.before",
                "sheet": {},
                "expected_status": "pending_ruling",
            }
        ],
    )
    assert report["passed"] is False
    assert report["mechanics_uncovered"] == ["dnd5e.xgte.second"]


def test_source_bound_compiler_requires_canonical_core_document_evidence() -> None:
    mechanic = {
        "id": "dnd5e.xgte.tool_synergy.advantage",
        "event": "check.before",
        "operations": [{"op": "advantage.add"}],
        "citations": [
            {
                "source": "rule-source:xgte-2017",
                "source_id": "source-1",
                "source_key": "xgte-2017",
                "source_checksum": "a" * 64,
                "chunk_id": "chunk-1",
                "heading_path": ["Tool Proficiencies", "Tools and Skills Together"],
                "page_start": 79,
                "page_end": 79,
            }
        ],
    }

    compiled = validate_source_bound_mechanics([mechanic], source_id="source-1")
    assert compiled[0].citations[0]["chunk_id"] == "chunk-1"
    with pytest.raises(RuleCompilationError, match="requested source"):
        validate_source_bound_mechanics([mechanic], source_id="source-2")
    invalid = deepcopy(mechanic)
    invalid["citations"][0]["source_checksum"] = "not-a-checksum"
    with pytest.raises(RuleCompilationError, match="SHA-256"):
        validate_source_bound_mechanics([invalid], source_id="source-1")
