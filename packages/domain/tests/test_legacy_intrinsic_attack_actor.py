import copy

import pytest

from sagasmith_dnd.character_schema import default_character_notes, default_character_sheet
from sagasmith_dnd.content_actors import (
    build_dnd_content_actor,
    canonicalize_dnd_content_actor,
    validate_dnd_content_actor,
)


def _legacy_actor():
    notes = default_character_notes()
    notes["profile"]["summary"] = "Legacy NPC for archive compatibility."
    actor = build_dnd_content_actor(
        actor_id="dnd5e.example.legacy-intrinsic-attack",
        version="1.0.0",
        actor_type="npc",
        name="Legacy NPC",
        sheet=default_character_sheet(),
        notes=notes,
    )
    actor["sheet"]["traits"].pop("intrinsic_attacks")
    return actor


def test_legacy_actor_accepts_absent_empty_intrinsic_attacks_without_rewriting():
    actor = _legacy_actor()
    original = copy.deepcopy(actor)
    assert validate_dnd_content_actor(actor) == original
    assert actor == original
    assert canonicalize_dnd_content_actor(actor)["sheet"]["traits"]["intrinsic_attacks"] == []


def test_legacy_intrinsic_attack_compatibility_does_not_relax_other_canonical_fields():
    actor = _legacy_actor()
    actor["notes"]["profile"].pop("portrait_ref")
    with pytest.raises(ValueError, match="canonical sheet and notes"):
        validate_dnd_content_actor(actor)


def test_explicit_invalid_intrinsic_attacks_remain_rejected():
    actor = _legacy_actor()
    actor["sheet"]["traits"]["intrinsic_attacks"] = ["forged"]
    with pytest.raises(ValueError):
        validate_dnd_content_actor(actor)
