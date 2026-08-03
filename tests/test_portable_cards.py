import copy
import hashlib
from pathlib import Path

import pytest
from sagasmith_core.portable import PortableContentError, portable_checksum

from sagasmith_dnd.character_schema import (
    default_character_notes,
    default_character_sheet,
    derive_character_sheet,
)
from sagasmith_dnd.content_readiness import catalog_review_errors
from sagasmith_dnd.portable_cards import (
    SRD2014_PRESET_PACK_ID,
    SRD2024_PRESET_PACK_ID,
    actor_catalog_artifact,
    bind_actor_catalog_review,
    build_dnd_actor_card,
    build_srd2014_preset_pack,
    build_srd2024_preset_pack,
    preset_pack_catalog_definition,
    validate_dnd_actor_card,
)


def _skill_root() -> Path:
    return Path(__file__).resolve().parents[2] / "SagaSmith-dnd-skills"


def test_srd_actor_presets_compile_every_statblock_to_the_unified_card() -> None:
    pack_2014 = build_srd2014_preset_pack(_skill_root())
    pack_2024 = build_srd2024_preset_pack(_skill_root())

    assert pack_2014["id"] == SRD2014_PRESET_PACK_ID
    assert len(pack_2014["payload"]["cards"]) == 317
    assert {card["payload"]["actor_type"] for card in pack_2014["payload"]["cards"]} == {
        "npc",
        "monster",
    }
    assert pack_2024["id"] == SRD2024_PRESET_PACK_ID
    assert len(pack_2024["payload"]["cards"]) == 330
    assert all(
        validate_dnd_actor_card(card)["checksum"] == card["checksum"]
        for card in pack_2014["payload"]["cards"] + pack_2024["payload"]["cards"]
    )


def test_actionless_and_speed_zero_srd_creatures_are_valid_cards() -> None:
    cards = build_srd2014_preset_pack(_skill_root())["payload"]["cards"]
    frog = next(card for card in cards if card["payload"]["name"] == "Frog")
    shrieker = next(card for card in cards if card["payload"]["name"] == "Shrieker (Fungi)")

    assert frog["payload"]["sheet"]["inventory"]["items"] == []
    assert frog["payload"]["provenance"]["warnings"]
    assert shrieker["payload"]["sheet"]["combat"]["speed"]["walk"] == 0


def test_2024_modifier_only_source_preserves_exact_modifier_and_save() -> None:
    cards = build_srd2024_preset_pack(_skill_root())["payload"]["cards"]
    otyugh = next(card for card in cards if card["payload"]["name"] == "Otyugh")
    sheet = otyugh["payload"]["sheet"]
    derived = derive_character_sheet(sheet)

    assert derived["ability_modifiers"]["strength"] == 3
    assert derived["saving_throws"]["constitution"] == 7
    assert any(
        "canonical representatives" in note
        for note in otyugh["payload"]["provenance"]["normalization_notes"]
    )


def test_dnd_card_rejects_a_resigned_noncanonical_sheet() -> None:
    card = copy.deepcopy(
        build_srd2014_preset_pack(_skill_root())["payload"]["cards"][0]
    )
    del card["payload"]["sheet"]["inventory"]
    card["checksum"] = portable_checksum(card)

    with pytest.raises(PortableContentError, match="canonical v2"):
        validate_dnd_actor_card(card)


def test_preset_pack_projects_to_installable_actor_card_catalog() -> None:
    package = build_srd2014_preset_pack(_skill_root())
    manifest, artifacts = preset_pack_catalog_definition(package)

    assert manifest["id"] == package["id"]
    assert manifest["content_kinds"] == ["actor_card"]
    assert len(artifacts) == 317
    assert artifacts[0]["kind"] == "actor_card"
    assert artifacts[0]["card"]["portable_card"]["kind"] == "actor_card"


def test_actor_catalog_review_is_bound_inside_the_portable_card() -> None:
    source_text = "Guard, medium humanoid, lawful neutral."
    notes = default_character_notes()
    notes["profile"]["summary"] = "A source-backed guard."
    card = build_dnd_actor_card(
        portable_id="dnd5e.example.actor.guard",
        version="1.0.0",
        actor_type="npc",
        name="Guard",
        sheet=default_character_sheet(),
        notes=notes,
        provenance={
            "source_refs": ["book:example:p1"],
            "source_text": source_text,
            "source_checksum": hashlib.sha256(source_text.encode()).hexdigest(),
        },
    )
    checks = {
        "identity": True,
        "classification": True,
        "entry_boundary": True,
        "references": True,
    }
    reviewed = bind_actor_catalog_review(
        card,
        decisions=[
            {
                "role": "primary",
                "reviewer": "agent:builder",
                "method": "agent",
                "checks": checks,
                "notes": "Reviewed the exact source-backed actor card.",
            },
            {
                "role": "critic",
                "reviewer": "deterministic:actor-card-critic",
                "method": "deterministic",
                "checks": checks,
                "notes": "Verified identity, boundary, and source references.",
            },
        ],
    )

    assert reviewed["checksum"] != card["checksum"]
    assert validate_dnd_actor_card(reviewed) == reviewed
    assert catalog_review_errors(actor_catalog_artifact(reviewed)) == []
    stale = copy.deepcopy(reviewed)
    stale["payload"]["name"] = "Different Guard"
    stale["checksum"] = portable_checksum(stale)
    assert any(
        "stale" in error
        for error in catalog_review_errors(actor_catalog_artifact(stale))
    )
