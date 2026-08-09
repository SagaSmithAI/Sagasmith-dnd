import json
from copy import deepcopy
from pathlib import Path

import pytest

from sagasmith_dnd.activities import consume_activity
from sagasmith_dnd.character_schema import (
    default_character_sheet,
    derive_character_sheet,
    validate_character_sheet,
)
from sagasmith_dnd.rule_engine import (
    ALLOWED_EVENTS,
    ALLOWED_OPS,
    RuleCompilationError,
    RuleEventRulingRequiredError,
    apply_rule_event,
    nested_ruling_kind,
    resolution_context,
    rule_event_ruling_kind,
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


def test_nested_ruling_kind_uses_one_owner_priority_across_envelopes() -> None:
    assert (
        nested_ruling_kind(
            {
                "status": "pending_ruling",
                "result": {
                    "pending_rulings": [
                        {"ruling_kind": "owner_approval"},
                        {"ruling_kind": "player_owned_choice"},
                    ]
                },
            }
        )
        == "player_owned_choice"
    )
    assert nested_ruling_kind({"status": "pending_choice"}) == "player_owned_choice"
    assert nested_ruling_kind({}, fallback="not-a-ruling-kind") == "agent_dm_adjudication"


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
            "uses": {
                "label": "",
                "value": 0,
                "max": 0,
                "unlimited": True,
                "recovers_on": "none",
                "source_key": "",
            },
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
        receipt["mechanic_id"] == "dnd5e.xgte.test.recover" for receipt in result["rule_receipts"]
    )
    assert result["ruleset_fingerprint"] == rules.fingerprint


def test_rule_extension_resource_operations_preserve_unlimited_counters() -> None:
    sheet = default_character_sheet()
    sheet["resources"]["at_will"] = {
        "label": "At Will",
        "value": 0,
        "max": 0,
        "unlimited": True,
        "recovers_on": "none",
        "source_key": "Test",
        "slot_level": 0,
    }
    rules = resolution_context(
        _effective(
            [
                {
                    "id": "dnd5e.extension.at-will",
                    "event": "rest.after",
                    "operations": [
                        {
                            "op": "resource.spend",
                            "path": "resources.at_will",
                            "amount": 1,
                        },
                        {
                            "op": "resource.recover",
                            "path": "resources.at_will",
                            "amount": 1,
                        },
                    ],
                    "citations": [{"source": "local:extension", "section": "At Will"}],
                }
            ]
        )
    )

    result = apply_rule_event(sheet, "rest.after", rules)

    assert result.sheet["resources"]["at_will"] == sheet["resources"]["at_will"]


def test_rule_extension_healing_uses_the_canonical_zero_hp_transition() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 0, "max": 10, "temp": 0}
    sheet["combat"]["death_saves"] = {"successes": 1, "failures": 2}
    sheet["conditions"] = ["Stable", "UNCONSCIOUS"]
    rules = resolution_context(
        _effective(
            [
                {
                    "id": "dnd5e.extension.heal",
                    "event": "rest.after",
                    "operations": [{"op": "hp.heal", "amount": 3}],
                    "citations": [{"source": "local:extension", "section": "Healing"}],
                }
            ]
        )
    )

    result = apply_rule_event(sheet, "rest.after", rules)

    assert result.sheet["combat"]["hp"]["value"] == 3
    assert result.sheet["combat"]["death_saves"] == {"successes": 0, "failures": 0}
    assert result.sheet["conditions"] == []


def test_healing_does_not_remove_unconscious_owned_by_an_active_effect() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 0, "max": 10, "temp": 0}
    sheet["conditions"] = ["stable", "unconscious"]
    sheet["effects"] = [
        {
            "id": "magical-sleep",
            "name": "Magical Sleep",
            "kind": "timed_conditions",
            "source": "spell:sleep",
            "active": True,
            "duration": {"period": "round", "remaining": 10},
            "changes": [{"path": "conditions", "mode": "add", "value": "unconscious"}],
        }
    ]
    rules = resolution_context(
        _effective(
            [
                {
                    "id": "dnd5e.extension.heal",
                    "event": "rest.after",
                    "operations": [{"op": "hp.heal", "amount": 3}],
                    "citations": [{"source": "local:extension", "section": "Healing"}],
                }
            ]
        )
    )

    result = apply_rule_event(validate_character_sheet(sheet), "rest.after", rules)

    assert result.sheet["combat"]["hp"]["value"] == 3
    assert result.sheet["conditions"] == ["unconscious"]


def test_rule_extension_healing_obeys_effective_maximum_and_death() -> None:
    exhausted = default_character_sheet()
    exhausted["combat"]["hp"] = {"value": 1, "max": 37, "temp": 0}
    exhausted["combat"]["exhaustion"] = 4
    rules = resolution_context(
        _effective(
            [
                {
                    "id": "dnd5e.extension.heal",
                    "event": "rest.after",
                    "operations": [{"op": "hp.heal", "amount": 100}],
                    "citations": [{"source": "local:extension", "section": "Healing"}],
                }
            ]
        )
    )

    assert apply_rule_event(exhausted, "rest.after", rules).sheet["combat"]["hp"]["value"] == 18
    dead = default_character_sheet()
    dead["conditions"] = ["dead"]
    with pytest.raises(ValueError, match="dead actor"):
        apply_rule_event(dead, "rest.after", rules)


def test_rule_extension_condition_changes_share_immunity_and_effect_ownership() -> None:
    immune = default_character_sheet()
    immune["traits"]["condition_immunities"] = ["Frightened"]
    add_rules = resolution_context(
        _effective(
            [
                {
                    "id": "dnd5e.extension.frighten",
                    "event": "rest.after",
                    "operations": [{"op": "condition.add", "id": "FRIGHTENED"}],
                    "citations": [{"source": "local:extension", "section": "Condition"}],
                }
            ]
        )
    )
    assert apply_rule_event(immune, "rest.after", add_rules).sheet["conditions"] == []

    sourced = default_character_sheet()
    sourced["conditions"] = ["frightened"]
    sourced["effects"] = [
        {
            "id": "fear",
            "name": "Fear",
            "kind": "timed_conditions",
            "active": True,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [{"path": "conditions", "mode": "add", "value": "frightened"}],
        }
    ]
    remove_rules = resolution_context(
        _effective(
            [
                {
                    "id": "dnd5e.extension.calm",
                    "event": "rest.after",
                    "operations": [{"op": "condition.remove", "id": "frightened"}],
                    "citations": [{"source": "local:extension", "section": "Condition"}],
                }
            ]
        )
    )
    assert apply_rule_event(sourced, "rest.after", remove_rules).sheet["conditions"] == [
        "frightened"
    ]


def test_rule_extension_effects_share_condition_projection_and_cleanup() -> None:
    effect = {
        "kind": "timed_conditions",
        "changes": [{"path": "conditions", "mode": "add", "value": "frightened"}],
    }
    add_rules = resolution_context(
        _effective(
            [
                {
                    "id": "dnd5e.extension.fear-effect",
                    "event": "rest.after",
                    "operations": [{"op": "effect.add", "id": "fear", "effect": effect}],
                    "citations": [{"source": "local:extension", "section": "Fear"}],
                }
            ]
        )
    )
    remove_rules = resolution_context(
        _effective(
            [
                {
                    "id": "dnd5e.extension.end-fear",
                    "event": "rest.after",
                    "operations": [{"op": "effect.remove", "id": "fear"}],
                    "citations": [{"source": "local:extension", "section": "Fear"}],
                }
            ]
        )
    )

    immune = default_character_sheet()
    immune["traits"]["condition_immunities"] = ["frightened"]
    assert apply_rule_event(immune, "rest.after", add_rules).sheet["conditions"] == []

    affected = apply_rule_event(default_character_sheet(), "rest.after", add_rules).sheet
    assert affected["conditions"] == ["frightened"]
    assert apply_rule_event(affected, "rest.after", remove_rules).sheet["conditions"] == []

    with pytest.raises(RuleCompilationError, match="effect does not exist"):
        apply_rule_event(default_character_sheet(), "rest.after", remove_rules)


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
    assert result.pending == (
        {
            "mechanic_id": "dnd5e.xgte.test.choice",
            "op": "choice.require",
            "id": "choose-recovery",
            "default_resolver": "external_input",
            "ruling_kind": "player_owned_choice",
        },
    )

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


def test_rule_event_defaults_rulings_to_agent_adjudication() -> None:
    rules = resolution_context(
        _effective(
            [
                {
                    "id": "dnd5e.xgte.test.ruling",
                    "event": "rest.before",
                    "operations": [{"op": "ruling.require", "id": "weather"}],
                    "citations": [{"source": "local:xgte", "section": "Weather"}],
                }
            ]
        )
    )

    result = apply_rule_event({}, "rest.before", rules)

    assert result.status == "pending_ruling"
    assert result.pending == (
        {
            "mechanic_id": "dnd5e.xgte.test.ruling",
            "op": "ruling.require",
            "id": "weather",
            "default_resolver": "agent",
            "ruling_kind": "agent_dm_adjudication",
        },
    )

    with pytest.raises(RuleCompilationError, match="invalid ruling_kind"):
        resolution_context(
            _effective(
                [
                    {
                        "id": "dnd5e.xgte.test.invalid-ruling",
                        "event": "rest.before",
                        "operations": [
                            {
                                "op": "ruling.require",
                                "id": "weather",
                                "ruling_kind": "ask_someone",
                            }
                        ],
                        "citations": [{"source": "local:xgte"}],
                    }
                ]
            )
        )


def test_character_rule_pauses_preserve_agent_and_external_ownership() -> None:
    agent_rules = resolution_context(
        _effective(
            [
                {
                    "id": "dnd5e.xgte.test.character-ruling",
                    "event": "character.validate",
                    "operations": [{"op": "ruling.require", "id": "form-fits"}],
                    "citations": [{"source": "local:xgte", "section": "Forms"}],
                }
            ]
        )
    )

    with pytest.raises(RuleEventRulingRequiredError) as raised:
        validate_character_sheet(default_character_sheet(), rules=agent_rules)

    assert raised.value.event == "character.validate"
    assert raised.value.missing == ("dnd5e.xgte.test.character-ruling",)
    assert raised.value.ruling_kind == "agent_dm_adjudication"
    assert raised.value.requirements[0]["default_resolver"] == "agent"

    choice_rules = resolution_context(
        _effective(
            [
                {
                    "id": "dnd5e.xgte.test.character-choice",
                    "event": "character.validate",
                    "operations": [{"op": "choice.require", "id": "choose-form"}],
                    "citations": [{"source": "local:xgte", "section": "Forms"}],
                }
            ]
        )
    )
    with pytest.raises(RuleEventRulingRequiredError) as choice:
        validate_character_sheet(default_character_sheet(), rules=choice_rules)
    assert choice.value.ruling_kind == "player_owned_choice"


def test_multiple_ruling_requirements_use_the_canonical_priority() -> None:
    pending = [
        {"ruling_kind": "module_specific_procedure"},
        {"ruling_kind": "missing_or_conflicting_source_review"},
        {"ruling_kind": "player_owned_choice"},
    ]

    assert rule_event_ruling_kind("pending_ruling", pending) == "player_owned_choice"


def test_derived_rule_pauses_publish_structured_agent_requirements() -> None:
    rules = resolution_context(
        _effective(
            [
                {
                    "id": "dnd5e.xgte.test.derive-ruling",
                    "event": "character.derive",
                    "operations": [
                        {
                            "op": "ruling.require",
                            "id": "environmental-ac",
                            "ruling_kind": "environmental_consequence",
                        }
                    ],
                    "citations": [{"source": "local:xgte", "section": "Weather"}],
                }
            ]
        )
    )

    derived = derive_character_sheet(default_character_sheet(), rules=rules)

    assert derived["unresolved_rules"] == ["dnd5e.xgte.test.derive-ruling"]
    assert derived["ruling_requirements"] == [
        {
            "mechanic_id": "dnd5e.xgte.test.derive-ruling",
            "reason": "environmental-ac",
            "default_resolver": "agent",
            "ruling_kind": "environmental_consequence",
        }
    ]


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
        resolution_context(_effective([{**base, "predicates": [{"kind": "python.eval"}]}]))
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
        resolution_context(_effective([{**base, "operations": {"op": "advantage.add"}}]))
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
