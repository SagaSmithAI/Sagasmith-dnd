from copy import deepcopy

import pytest
from sagasmith_core.content_pack import (
    build_content_package,
    dumps_content_archive,
    loads_content_archive,
)

from sagasmith_dnd.character_schema import default_character_notes, default_character_sheet
from sagasmith_dnd.content_actors import (
    build_dnd_content_actor,
    canonicalize_dnd_content_actor,
    validate_dnd_content_actor,
)
from sagasmith_dnd.content_packages import (
    build_preset_content_package,
    validate_dnd_content_package,
)


def _legacy_actor(*, missing_intrinsic_attacks: bool = False) -> dict:
    notes = default_character_notes()
    notes["profile"]["summary"] = "Legacy inventory fixture."
    actor = build_dnd_content_actor(
        actor_id="dnd5e.example.legacy-external-items",
        version="1.0.0",
        actor_type="npc",
        name="Legacy inventory NPC",
        sheet=default_character_sheet(),
        notes=notes,
    )
    actor["sheet"]["inventory"].pop("external_items")
    if missing_intrinsic_attacks:
        actor["sheet"]["traits"].pop("intrinsic_attacks")
    return actor


@pytest.mark.parametrize("missing_intrinsic_attacks", [False, True])
def test_absent_empty_external_items_preserves_actor_and_archive(missing_intrinsic_attacks):
    actor = _legacy_actor(missing_intrinsic_attacks=missing_intrinsic_attacks)
    original = deepcopy(actor)
    assert validate_dnd_content_actor(actor) == original
    assert actor == original
    canonical = canonicalize_dnd_content_actor(actor)
    assert canonical["sheet"]["inventory"]["external_items"] == []
    assert canonical["sheet"]["traits"]["intrinsic_attacks"] == []

    package, blobs = build_preset_content_package(
        package_id="dnd5e.example.legacy-external-items",
        version="1.0.0",
        system_id="dnd5e",
        title="Legacy inventory actors",
        cards=[actor],
    )
    # The current authoring builder canonicalizes new output. Reconstruct the
    # historical archive through Core without changing its old actor payload.
    package = build_content_package(
        kind=package["kind"],
        package_id=package["id"],
        version=package["version"],
        system_id=package["system_id"],
        manifest=package["manifest"],
        dependencies=package["dependencies"],
        sources=package["sources"],
        assets=package["assets"],
        content_reviews=package["content_reviews"],
        actors=[original],
        content=package["content"],
        metadata=package["metadata"],
    )
    assert package["actors"] == [original]
    archive = dumps_content_archive(package, blobs=blobs)
    restored, restored_blobs = loads_content_archive(archive)
    assert validate_dnd_content_package(restored) == package
    assert restored["checksum"] == package["checksum"]
    assert restored_blobs == blobs


def test_missing_external_items_does_not_relax_other_canonical_fields():
    actor = _legacy_actor()
    actor["notes"]["profile"].pop("portrait_ref")
    with pytest.raises(ValueError, match="canonical sheet and notes"):
        validate_dnd_content_actor(actor)


@pytest.mark.parametrize("external_items", [None, ["forged"], [{"id": "forged"}]])
def test_explicit_invalid_external_items_are_not_treated_as_absent(external_items):
    actor = _legacy_actor()
    actor["sheet"]["inventory"]["external_items"] = external_items
    with pytest.raises(ValueError):
        validate_dnd_content_actor(actor)
