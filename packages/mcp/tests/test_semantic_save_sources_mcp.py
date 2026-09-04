from copy import deepcopy

import pytest
from sagasmith_dnd.combat_engine import CombatEngineError
from sagasmith_dnd.resolution_plan import compile_resolution_plan

from sagasmith_dnd_mcp.server import _semantic_plan_save_facts


def _fixture():
    # Deliberately authored fixture, not an official-rule quotation.
    charm = "The magic bell calls for a Wisdom save against being charmed."
    fear = "The mundane mask calls for a Wisdom save against being frightened."
    citations = [
        {"source": "test:bell", "source_ref": {"chunk_id": "bell"}, "source_excerpt": charm},
        {"source": "test:mask", "source_ref": {"chunk_id": "mask"}, "source_excerpt": fear},
    ]
    plan = {
        "schema_version": 2,
        "id": "test.two-clause-save",
        "source_card_id": "test.bell-and-mask",
        "source_card_kind": "feature",
        "trigger": "action",
        "slots": {},
        "steps": [
            {
                "id": name,
                "op": "check.save",
                "args": {
                    "target_ids": ["target"],
                    "ability": "wisdom",
                    "dc": 12,
                    "source": {
                        **deepcopy(citation),
                        "save_source_kind": kind,
                        "save_effect_conditions": [condition],
                        "save_against_poison": False,
                    },
                },
            }
            for name, citation, kind, condition in (
                ("charm", citations[0], "magical_effect", "charmed"),
                ("fear", citations[1], "nonmagical_effect", "frightened"),
            )
        ],
        "citations": citations,
    }
    card = {"id": plan["source_card_id"], "description": f"{charm}\n{fear}"}
    return card, plan


def test_each_save_keeps_its_own_classification_and_original_citation():
    card, raw = _fixture()
    before = deepcopy((card, raw))
    compiled = compile_resolution_plan(raw)
    facts = _semantic_plan_save_facts(card, compiled)
    assert facts == {
        "charm": {
            "save_source_kind": "magical_effect",
            "save_effect_conditions": ["charmed"],
            "save_against_poison": False,
        },
        "fear": {
            "save_source_kind": "nonmagical_effect",
            "save_effect_conditions": ["frightened"],
            "save_against_poison": False,
        },
    }
    facts["charm"]["save_effect_conditions"].append("poisoned")
    assert (card, raw) == before
    assert compiled.steps[0]["args"]["source"]["save_effect_conditions"] == ["charmed"]


def test_another_relevant_citation_cannot_authorize_unrelated_save_source():
    card, raw = _fixture()
    # The charm citation is relevant, but the fear citation belongs to a
    # different card. Matching one plan-wide citation must not bless both.
    card["description"] = raw["citations"][0]["source_excerpt"]
    with pytest.raises(CombatEngineError, match="save step fear must cite"):
        _semantic_plan_save_facts(card, compile_resolution_plan(raw))


def test_large_excerpt_containing_card_text_does_not_reverse_card_binding():
    card, raw = _fixture()
    original = raw["citations"][0]["source_excerpt"]
    combined = original + " An unrelated curse requires a poison saving throw."
    raw["citations"][0]["source_excerpt"] = combined
    raw["steps"][0]["args"]["source"]["source_excerpt"] = combined
    with pytest.raises(CombatEngineError, match="save step charm must cite"):
        _semantic_plan_save_facts(card, compile_resolution_plan(raw))


def test_legacy_source_remains_unknown_and_card_identity_is_checked():
    card, raw = _fixture()
    for step in raw["steps"]:
        step["args"]["source"] = "Legacy source text"
    compiled = compile_resolution_plan(raw)
    assert _semantic_plan_save_facts(card, compiled) == {"charm": {}, "fear": {}}
    card["id"] = "other-card"
    with pytest.raises(CombatEngineError, match="does not match its recorded card"):
        _semantic_plan_save_facts(card, compiled)
