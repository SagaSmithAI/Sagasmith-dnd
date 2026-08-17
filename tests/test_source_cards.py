import pytest

from sagasmith_dnd.source_cards import (
    CharacterSourceCardError,
    character_activity_source_card,
    character_resolution_plan,
    character_source_card,
    persisted_standard_spell_ruling_requirement,
    source_card_evidence_texts,
    validate_persisted_standard_spell_ruling,
)


def sheet_with_cards() -> dict:
    return {
        "content": {
            "activities": [{"id": "second-wind", "description": "Recover your stamina."}],
            "features": [{"id": "feature-1", "text": "A sufficiently long feature text."}],
            "feats": [],
            "spells": [],
        },
        "inventory": {"items": [{"id": "potion", "description": "A healing potion."}]},
    }


def test_character_source_cards_are_exact_and_kind_bound() -> None:
    sheet = sheet_with_cards()
    assert character_source_card(sheet, "potion", "item")["id"] == "potion"
    card, kind = character_activity_source_card(
        sheet,
        "second-wind",
        character_type="pc",
    )
    assert card["id"] == "second-wind"
    assert kind == "activity"
    _, monster_kind = character_activity_source_card(
        sheet,
        "second-wind",
        character_type="monster",
    )
    assert monster_kind == "monster_action"
    with pytest.raises(CharacterSourceCardError, match="exactly one"):
        character_source_card(sheet, "missing", "feature")


def test_source_evidence_excludes_derived_resolution_fields() -> None:
    assert source_card_evidence_texts(
        {
            "description": "Original source wording.",
            "resolution_plan": {"text": "Do not treat this as evidence."},
            "nested": {"effect": "A second original clause."},
        }
    ) == ("original source wording.", "a second original clause.")


def test_standard_spell_ruling_is_bound_to_the_recorded_clause() -> None:
    card = {
        "id": "custom-spell",
        "pack_id": "standard",
        "rule_refs": ["rule:spell"],
        "ruling_requirements": [
            {
                "default_resolver": "agent",
                "ruling_kind": "generic_spell_effect",
                "source_excerpt": "The exact printed spell clause.",
            }
        ],
    }
    requirement = persisted_standard_spell_ruling_requirement(
        card,
        standard_pack_ids=frozenset({"standard"}),
    )
    assert requirement is not None
    ruling = validate_persisted_standard_spell_ruling(
        {
            "application_id": "application-1",
            "default_resolver": "agent",
            "ruling_kind": "generic_spell_effect",
            "decision": "Apply the printed effect to the selected creature.",
            "reason": "The target and duration match the recorded source clause.",
            "source_excerpt": "The exact printed spell clause.",
        },
        source_card=card,
        requirement=requirement,
    )
    assert ruling["source_card_id"] == "custom-spell"


def test_resolution_plan_must_belong_to_the_exact_card() -> None:
    sheet = sheet_with_cards()
    sheet["content"]["features"][0]["resolution_plan"] = {
        "schema_version": 2,
        "id": "plan-1",
        "source_card_id": "another-card",
        "source_card_kind": "feature",
        "trigger": "action",
        "steps": [],
        "slots": [],
        "citations": [],
    }
    with pytest.raises(CharacterSourceCardError):
        character_resolution_plan(sheet, "feature-1", "feature")
