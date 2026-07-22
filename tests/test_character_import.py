from __future__ import annotations

from sagasmith_core.documents import NormalizedDocument

from sagasmith_dnd.character_import import inspect_character_document


def _document(*, content: str, fields: dict[str, object], names: list[str]) -> NormalizedDocument:
    return NormalizedDocument(
        content=content,
        media_type="application/pdf",
        source_path="/campaign/pc.pdf",
        checksum="a" * 64,
        page_count=2,
        metadata={
            "form_field_count": len(names),
            "form_field_names": names,
            "populated_form_field_count": len(fields),
            "populated_form_fields": fields,
        },
    )


def test_character_document_reports_source_fields_and_missing_manual_values() -> None:
    names = [
        "Front_Character Name",
        "Front_Race",
        "Front_Level",
        "Front_Str Score",
    ]
    inspection = inspect_character_document(
        _document(
            content="##### BARBARIAN\n",
            names=names,
            fields={
                "Front_Race": "Half-Orc",
                "Front_Background": "Soldier",
                "Front_Archetype": "Ancestral Guardian",
                "Front_Save Str": "/Yes",
                "Front_Proficiency Athletics": "/Yes",
            },
        ),
        source_name="AncestralGuardianBarbarian.pdf",
    )

    assert inspection["document_kind"] == "character_sheet"
    assert inspection["status"] == "incomplete_template"
    assert inspection["ready_to_create"] is False
    assert inspection["draft"]["progression"] == {
        "class": "barbarian",
        "subclass": "Ancestral Guardian",
        "level": None,
        "species": "Half-Orc",
        "background": "Soldier",
    }
    assert "ability_scores.strength" in inspection["missing_fields"]
    assert inspection["manual_input"]["ability_scores_allowed"] is True
    assert inspection["draft"]["save_proficiencies"] == ["str"]
    assert inspection["draft"]["skill_proficiencies"] == ["Athletics"]


def test_character_document_can_be_ready_when_required_values_are_present() -> None:
    names = [
        "Front_Character Name",
        "Front_Race",
        "Front_Level",
        "Front_Str Score",
    ]
    fields = {
        "Front_Character Name": "Mira",
        "Front_Race": "Human",
        "Front_Level": "1",
        "Front_Max HP": "10",
        "Front_Str Score": "15",
        "Front_Dex Score": "14",
        "Front_Con Score": "13",
        "Front_Int Score": "12",
        "Front_Wis Score": "10",
        "Front_Cha Score": "8",
    }
    inspection = inspect_character_document(
        _document(content="##### FIGHTER\n", fields=fields, names=names),
    )

    assert inspection["status"] == "ready"
    assert inspection["ready_to_create"] is True
    assert inspection["missing_fields"] == []


def test_plaintext_six_score_rows_are_reviewable_ability_options() -> None:
    document = NormalizedDocument(
        content="9, 8, 10, 14, 8, 12\n17, 14, 16, 12, 11, 14\n",
        media_type="text/plain",
        source_path="/campaign/PCStats.txt",
        checksum="b" * 64,
    )

    inspection = inspect_character_document(document)

    assert inspection["document_kind"] == "ability_score_options"
    assert inspection["ability_score_sets"] == [
        [9, 8, 10, 14, 8, 12],
        [17, 14, 16, 12, 11, 14],
    ]
    assert inspection["manual_input"]["modes"][0] == "manual"
