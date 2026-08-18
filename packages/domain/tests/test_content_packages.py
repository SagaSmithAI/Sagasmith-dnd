from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest
from sagasmith_core.content_pack import (
    build_content_package,
    dumps_content_archive,
    loads_content_archive,
)
from sagasmith_core.indexed_source import rule_chunk_key

from sagasmith_dnd.character_schema import default_character_notes, default_character_sheet
from sagasmith_dnd.content_actors import build_dnd_content_actor
from sagasmith_dnd.content_packages import (
    _module_scene_metadata,
    _portrait_cache_key,
    _portrait_sources,
    _refresh_reviewed_content_hashes,
    _translate_module_refs,
    attach_actor_portraits,
    attach_auxiliary_assets,
    build_preset_content_package,
    build_rule_content_package,
    canonicalize_dnd_content_package,
    compose_addon_content_package,
    validate_dnd_content_package,
)
from sagasmith_dnd.content_validation import content_fingerprint
from sagasmith_dnd.portrait_extraction import ExtractedPortrait, PortraitInspection


def test_module_scene_metadata_isolates_dnd_profile_fields() -> None:
    metadata = _module_scene_metadata(
        {
            "visibility": "group",
            "scene_level": 2,
            "line_count": 4,
            "subsections": [{"title": "Ambush"}],
            "tags": ["combat"],
            "spatial": {"mode": "agent"},
            "profile_data": {"encounter_id": "goblin-ambush"},
            "checks": [{"ability": "wisdom"}],
            "start_line": 10,
        }
    )

    assert metadata == {
        "visibility": "group",
        "scene_level": 2,
        "line_count": 4,
        "subsections": [{"title": "Ambush"}],
        "tags": ["combat"],
        "spatial": {"mode": "agent"},
        "profile_data": {
            "encounter_id": "goblin-ambush",
            "checks": [{"ability": "wisdom"}],
        },
    }


def test_module_source_ref_error_reports_unique_one_character_candidate() -> None:
    valid_hash = "a" * 64
    submitted_hash = "b" + "a" * 63

    with pytest.raises(ValueError) as error:
        _translate_module_refs(
            {
                "source_key": "book",
                "page": 1,
                "chunk_hash": submitted_hash,
                "note": "reviewed",
            },
            source_key="book",
            chunk_hash_keys={valid_hash: "chunk-1"},
        )

    assert f"unique one-character candidate: {valid_hash}" in str(error.value)


def _rule_descriptor(
    *,
    descriptor_id: str,
    version: str,
    system_id: str,
    manifest: dict,
    artifacts: list,
    mechanics: list,
    sources: list,
    metadata: dict,
) -> dict:
    return {
        "id": descriptor_id,
        "version": version,
        "system_id": system_id,
        "manifest": manifest,
        "artifacts": artifacts,
        "mechanics": mechanics,
        "sources": sources,
        "metadata": metadata,
        "dependencies": [],
    }


def test_final_content_artifact_refreshes_attestation_hashes_idempotently() -> None:
    artifact = {
        "id": "example.feature",
        "kind": "feature",
        "card": {"name": "Example", "description": "reviewed"},
        "rule_definition_id": "example.pack",
        "catalog_review": {"reviewed_content_hash": "0" * 64},
        "selection_contract": {"reviewed_content_hash": "1" * 64},
    }

    refreshed = _refresh_reviewed_content_hashes(artifact)
    assert refreshed["catalog_review"]["reviewed_content_hash"] == (
        refreshed["selection_contract"]["reviewed_content_hash"]
    )
    assert _refresh_reviewed_content_hashes(refreshed) == refreshed
    without_join_key = {
        key: value
        for key, value in refreshed.items()
        if key != "rule_definition_id"
    }
    assert content_fingerprint(refreshed) == content_fingerprint(without_join_key)


def test_preset_duplicate_actor_names_keep_their_own_source_chunks() -> None:
    cards = []
    for index, summary in enumerate(("North gate guard.", "South gate guard."), 1):
        notes = default_character_notes()
        notes["profile"]["summary"] = summary
        cards.append(
            build_dnd_content_actor(
                actor_id=f"dnd5e.example.guard.{index}",
                version="2.0.0",
                actor_type="npc",
                name="Guard",
                summary=summary,
                sheet=default_character_sheet(),
                notes=notes,
            )
        )

    package, _blobs = build_preset_content_package(
        package_id="dnd5e.example.duplicate-guards",
        version="2.0.0",
        system_id="dnd5e",
        title="Duplicate-name guards",
        cards=cards,
    )

    expected_chunks = [
        section["chunks"][0]["key"] for section in package["sources"][0]["sections"]
    ]
    actual_chunks = [
        actor["provenance"]["source_refs"][0]["chunk_key"] for actor in package["actors"]
    ]
    assert actual_chunks == expected_chunks
    assert len(set(actual_chunks)) == 2


def test_package_rejects_unrepaired_transcription_character_in_actor_sheet() -> None:
    notes = default_character_notes()
    notes["profile"]["summary"] = "An unrepaired crea�ture."
    package, _blobs = build_preset_content_package(
        package_id="dnd5e.example.bad-transcription",
        version="2.0.0",
        system_id="dnd5e",
        title="Bad Transcription",
        cards=[
            build_dnd_content_actor(
                actor_id="dnd5e.example.bad-transcription.actor",
                version="2.0.0",
                actor_type="npc",
                name="Example Actor",
                sheet=default_character_sheet(),
                notes=notes,
            )
        ],
    )
    with pytest.raises(ValueError, match="unrepaired PDF transcription"):
        validate_dnd_content_package(package)


def test_package_validation_rejects_noncanonical_actor_but_canonicalizer_repairs_it() -> None:
    notes = default_character_notes()
    notes["profile"]["summary"] = "Legacy actor."
    card = build_dnd_content_actor(
        actor_id="dnd5e.example.legacy-actor",
        version="2.0.0",
        actor_type="npc",
        name="Legacy Actor",
        sheet=default_character_sheet(),
        notes=notes,
    )
    package, _blobs = build_preset_content_package(
        package_id="dnd5e.example.legacy-actors",
        version="2.0.0",
        system_id="dnd5e",
        title="Legacy Actors",
        cards=[card],
    )
    legacy_actor = copy.deepcopy(package["actors"][0])
    legacy_actor["notes"]["profile"].pop("portrait_ref")
    legacy = build_content_package(
        kind=package["kind"],
        package_id=package["id"],
        version=package["version"],
        system_id=package["system_id"],
        manifest=package["manifest"],
        dependencies=package["dependencies"],
        sources=package["sources"],
        assets=package["assets"],
        content_reviews=package["content_reviews"],
        actors=[legacy_actor],
        content=package["content"],
        metadata=package["metadata"],
    )

    with pytest.raises(ValueError, match="canonical sheet and notes"):
        validate_dnd_content_package(legacy)

    repaired = canonicalize_dnd_content_package(legacy)
    assert repaired["actors"][0]["notes"]["profile"]["portrait_ref"] is None
    assert validate_dnd_content_package(repaired) == repaired


def test_canonicalizer_maps_obsolete_keeper_visibility_to_restricted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = {
        "kind": "module",
        "id": "dnd5e.example.legacy-visibility",
        "version": "1.0.0",
        "system_id": "dnd5e",
        "checksum": "0" * 64,
        "manifest": {},
        "dependencies": [],
        "sources": [],
        "assets": [],
        "content_reviews": [],
        "actors": [],
        "content": {
            "scene_atlas": [{"metadata": {"visibility": "keeper"}}],
            "artifacts": [],
            "mechanics": [],
        },
        "metadata": {},
    }
    monkeypatch.setattr(
        "sagasmith_dnd.content_packages.validate_core_content_package",
        lambda value: copy.deepcopy(value),
    )
    monkeypatch.setattr(
        "sagasmith_dnd.content_packages.content_package_checksum",
        lambda _value: "1" * 64,
    )
    monkeypatch.setattr(
        "sagasmith_dnd.content_packages.build_content_package",
        lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(
        "sagasmith_dnd.content_packages.validate_dnd_content_package",
        lambda value: value,
    )

    repaired = canonicalize_dnd_content_package(package)

    assert repaired["content"]["scene_atlas"][0]["metadata"]["visibility"] == (
        "restricted"
    )


def test_finalized_module_does_not_require_party_size_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_ref = {"source_key": "book", "chunk_key": "chunk-1"}
    package = {
        "kind": "module",
        "system_id": "dnd5e",
        "metadata": {
            "agent_finalization": {
                "confirmed": True,
                "reviewer": "agent:test",
                "note": "Reviewed source; no fixed party size is required.",
            }
        },
        "content": {
            "classification": "campaign",
            "artifacts": [],
            "narrative": {"endings": [{"id": "ending-1"}]},
            "play_profile": {
                "party_size": {
                    "minimum": None,
                    "maximum": None,
                    "source_refs": [],
                },
                "starting_level": {"value": 1, "source_refs": [source_ref]},
                "expected_end_level": {"value": 5, "source_refs": [source_ref]},
                "advancement": {
                    "recommended": "milestone",
                    "modes": ["milestone"],
                    "source_refs": [source_ref],
                },
                "pregenerated_characters": {
                    "present": False,
                    "source_refs": [source_ref],
                },
            },
        },
        "actors": [],
        "assets": [],
    }
    monkeypatch.setattr(
        "sagasmith_dnd.content_packages.validate_core_content_package",
        lambda value: copy.deepcopy(value),
    )

    validated = validate_dnd_content_package(package)

    assert validated["content"]["play_profile"]["party_size"]["minimum"] is None


def test_preset_builder_repairs_reviewed_pdf_word_breaks_in_actor_cards() -> None:
    sheet = default_character_sheet()
    sheet["content"]["features"] = [
        {"name": "Escape", "description": "trans\ufffe formed\x02"}
    ]
    notes = default_character_notes()
    notes["profile"]["summary"] = "A source-backed actor."
    package, _blobs = build_preset_content_package(
        package_id="dnd5e.example.repaired-transcription",
        version="2.0.0",
        system_id="dnd5e",
        title="Repaired Transcription",
        cards=[
            build_dnd_content_actor(
                actor_id="dnd5e.example.repaired-transcription.actor",
                version="2.0.0",
                actor_type="npc",
                name="Example Actor",
                sheet=sheet,
                notes=notes,
            )
        ],
    )

    actor = package["actors"][0]
    assert actor["sheet"]["content"]["features"][0]["description"] == "transformed"
    assert actor["metadata"]["transcription_repairs"]
    validate_dnd_content_package(package)


def test_auxiliary_assets_are_distinct_from_indexed_originals(tmp_path: Path) -> None:
    package, blobs = build_preset_content_package(
        package_id="dnd5e.example.auxiliary",
        version="2.0.0",
        system_id="dnd5e",
        title="Auxiliary Example",
        cards=[
            build_dnd_content_actor(
                actor_id="dnd5e.example.auxiliary.actor",
                version="2.0.0",
                actor_type="pc",
                name="Example Actor",
                sheet=default_character_sheet(),
                notes=default_character_notes(),
            )
        ],
    )
    handout = tmp_path / "Player Handout.pdf"
    handout.write_bytes(b"%PDF-1.4\nsource-distributed handout\n")

    attached, attached_blobs = attach_auxiliary_assets(
        package,
        blobs,
        [
            {
                "path": handout,
                "kind": "player_reference",
                "logical_path": "characters/Player Handout.pdf",
            }
        ],
    )

    asset = next(item for item in attached["assets"] if item["kind"] == "player_reference")
    assert asset["metadata"] == {
        "logical_path": "characters/Player Handout.pdf",
        "relationship": "package_auxiliary",
    }
    assert asset["checksum"] in attached_blobs
    assert "readiness" not in attached
    replayed, replayed_blobs = attach_auxiliary_assets(
        attached,
        attached_blobs,
        [
            {
                "path": handout,
                "kind": "player_reference",
                "logical_path": "characters/Player Handout.pdf",
            }
        ],
    )
    assert replayed == attached
    assert replayed_blobs == attached_blobs


def test_addon_content_package_flattens_rules_and_stores_source_once() -> None:
    text = "# Options\nA hero can choose the luminous ward."
    chunk_key = rule_chunk_key("example.source", 0, 0, text)
    component = _rule_descriptor(
        descriptor_id="dnd5e.example.rules",
        version="1.0.0",
        system_id="dnd5e",
        manifest={
            "id": "dnd5e.example.rules",
            "version": "1.0.0",
            "title": "Example Rules",
            "namespace": "dnd5e.example",
            "system_id": "dnd5e",
            "editions": ["2014"],
            "dependencies": [],
            "conflicts": [],
            "capabilities": [],
        },
        artifacts=[
            {
                "id": "ward",
                "kind": "feature",
                "card": {"name": "Luminous Ward"},
                "source_citations": [
                    {
                        "source": "rule-source:example.source",
                        "source_key": "example.source",
                        "chunk_key": chunk_key,
                        "source_checksum": hashlib.sha256(text.encode()).hexdigest(),
                        "page_start": 1,
                    }
                ],
            }
        ],
        mechanics=[],
        sources=[
            {
                "source_key": "example.source",
                "title": "Example Source",
                "edition": "2014",
                "locale": "en",
                "version": "1.0.0",
                "publication_id": "example.source",
                "authority": "supplement",
                "canonical_source_key": None,
                "checksum": hashlib.sha256(text.encode()).hexdigest(),
                "metadata": {},
                "sections": [
                    {
                        "ordinal": 0,
                        "parent_ordinal": None,
                        "level": 1,
                        "title": "Options",
                        "path": ["Options"],
                        "content": text,
                        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                        "start_offset": 0,
                        "end_offset": len(text),
                        "chunks": [
                            {
                                "key": chunk_key,
                                "ordinal": 0,
                                "heading_path": ["Options"],
                                "content": text,
                                "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                                "token_count": 8,
                                "metadata": {
                                    "start_offset": 0,
                                    "end_offset": len(text),
                                    "page_start": 1,
                                    "page_end": 1,
                                },
                            }
                        ],
                    }
                ],
            }
        ],
        metadata={"distribution": "private"},
    )
    stale_chunk_key = "example.source/section-0/chunk-0-deadbeefdeadbeef"
    component["sources"][0]["sections"][0]["chunks"][0]["key"] = stale_chunk_key
    component["artifacts"][0]["source_citations"][0]["chunk_key"] = stale_chunk_key
    notes = default_character_notes()
    notes["profile"]["summary"] = "A source-backed test actor."
    card = build_dnd_content_actor(
        actor_id="dnd5e.example.actor.luminous-ward",
        version="1.0.0",
        actor_type="npc",
        name="Luminous Ward",
        sheet=default_character_sheet(),
        notes=notes,
        provenance={
            "source_refs": [
                "rule-pack:dnd5e.example.rules@1.0.0#artifact:ward"
            ]
        },
    )
    package, blobs = build_rule_content_package(
        package_id="dnd5e.example.addon",
        version="1.0.0",
        system_id="dnd5e",
        manifest={
            "id": "dnd5e.example.addon",
            "version": "1.0.0",
            "system_id": "dnd5e",
            "title": "Example Addon",
            "classification": "third_party",
            "editions": ["2014"],
            "activation": {
                "rule_policy": "branch",
                "preset_policy": "none",
                "module_policy": "none",
            },
        },
        rule_descriptors=[component],
        preset_actors=[card],
        metadata={
            "license": "CC-BY-4.0",
            "attribution": "Example author",
        },
    )
    assert package["kind"] == "addon"
    canonical_chunk_key = package["sources"][0]["sections"][0]["chunks"][0]["key"]
    assert canonical_chunk_key == rule_chunk_key("example.source", 0, 0, text)
    assert package["content"]["artifacts"][0]["source_refs"][0]["chunk_key"] == (
        canonical_chunk_key
    )
    assert package["actors"][0]["provenance"]["source_refs"][0]["chunk_key"] == (
        canonical_chunk_key
    )
    assert "content" not in package["sources"][0]["sections"][0]
    loaded, loaded_blobs = loads_content_archive(dumps_content_archive(package, blobs))
    assert loaded == package
    assert loaded_blobs == blobs
    assert canonicalize_dnd_content_package(package) == package


def test_portrait_sources_preserve_all_unique_evidence_pages() -> None:
    actor = {
        "provenance": {
            "source_refs": [
                {"source_key": "book", "page": 12},
                {"source_key": "book", "page": 13},
                {"source_key": "book", "page": 12},
                {"source_key": "book", "page": None},
            ]
        }
    }
    assert _portrait_sources(actor) == [("book", 12), ("book", 13)]


def test_portrait_cache_does_not_merge_same_name_from_different_pages() -> None:
    first = {
        "name": "Guard",
        "provenance": {
            "source_refs": [{"source_key": "book", "chunk_key": "north", "page": 12}]
        },
    }
    same_evidence = {
        "name": "guard",
        "provenance": {
            "source_refs": [{"source_key": "book", "chunk_key": "north", "page": 12}]
        },
    }
    different_variant = {
        "name": "Guard",
        "provenance": {
            "source_refs": [{"source_key": "book", "chunk_key": "south", "page": 12}]
        },
    }

    assert _portrait_cache_key(first) == _portrait_cache_key(same_evidence)
    assert _portrait_cache_key(first) != _portrait_cache_key(different_variant)


def test_portraits_attach_to_source_statblock_cards_without_runtime_instances(
    tmp_path: Path,
    monkeypatch,
) -> None:
    guide_notes = default_character_notes()
    guide_notes["profile"]["summary"] = "A source-backed template guide."
    package, blobs = build_preset_content_package(
        package_id="dnd5e.example.dynamic-template",
        version="2.0.0",
        system_id="dnd5e",
        title="Dynamic Template",
        cards=[
            build_dnd_content_actor(
                actor_id="dnd5e.example.dynamic-template.guide",
                version="2.0.0",
                actor_type="npc",
                name="Template Guide",
                sheet=default_character_sheet(),
                notes=guide_notes,
            )
        ],
    )
    source_ref = {
        **package["sources"][0]["sections"][0]["chunks"][0],
    }
    actors = copy.deepcopy(list(package["actors"]))
    actors[0]["provenance"]["source_refs"][0]["page"] = 1
    content = copy.deepcopy(dict(package["content"]))
    content["artifacts"] = [
        {
            "id": "dnd5e.example.dynamic-template.alchemical-homunculus",
            "kind": "statblock",
            "card": {"name": "Alchemical Homunculus"},
            "source_refs": [
                {
                    "source_key": package["sources"][0]["source_key"],
                    "chunk_key": source_ref["key"],
                    "page": 1,
                    "note": "Dependent actor template",
                }
            ],
        }
    ]
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
        actors=actors,
        content=content,
        metadata=package["metadata"],
    )
    portrait = ExtractedPortrait(
        content=b"reviewed-webp",
        media_type="image/webp",
        page=1,
        crop=(1.0, 2.0, 101.0, 202.0),
        confidence=0.95,
        method="test-reviewed-crop",
    )
    monkeypatch.setattr(
        "sagasmith_dnd.content_packages.PortraitExtractor.inspect",
        lambda *args, **kwargs: PortraitInspection(portrait, "extracted", True, 1, 0.95),
    )
    monkeypatch.setattr(
        "sagasmith_dnd.content_packages.PortraitExtractor.extract_reviewed_crop",
        lambda *args, **kwargs: portrait,
    )
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"unused by the mocked extractor")

    attached, attached_blobs, audit, _library = attach_actor_portraits(
        package,
        blobs,
        {package["sources"][0]["source_key"]: source_path},
        portrait_reviews={
            (
                f"{package['id']}|statblock_card|"
                "dnd5e.example.dynamic-template.alchemical-homunculus"
            ): {
                "decision": "crop",
                "source_key": package["sources"][0]["source_key"],
                "page": 1,
                "crop": [1.0, 2.0, 101.0, 202.0],
                "reviewer": "agent:visual-review",
                "note": "The exact illustrated region was verified against the page.",
            }
        },
        minimum_confidence=0.40,
    )

    card = attached["content"]["artifacts"][0]["card"]
    assert card["image"]["alt"] == "Alchemical Homunculus portrait"
    asset = next(
        item for item in attached["assets"] if item["asset_key"] == card["image"]["asset_key"]
    )
    assert asset["metadata"]["subject_type"] == "statblock_card"
    assert asset["metadata"]["review"]["reviewer"] == "agent:visual-review"
    assert asset["checksum"] in attached_blobs
    assert audit["actors"] == audit["images"] == 1
    assert audit["statblock_cards"] == audit["statblock_card_images"] == 1
    assert audit["subjects"] == audit["subject_images"] == 2
    assert audit["reviewed"][0]["decision"] == "crop"

    monkeypatch.setattr(
        "sagasmith_dnd.content_packages.PortraitExtractor.inspect",
        lambda *args, **kwargs: PortraitInspection(
            None, "no_visual_candidate", True, 0, 0.0
        ),
    )
    _unresolved, _unresolved_blobs, unresolved_audit, _library = (
        attach_actor_portraits(
            package,
            blobs,
            {package["sources"][0]["source_key"]: source_path},
            minimum_confidence=0.40,
        )
    )
    assert len(unresolved_audit["review_required"]) == 2
    assert all(
        item["review_key"].startswith(f"{package['id']}|")
        for item in unresolved_audit["review_required"]
    )


def test_addon_content_package_preserves_existing_unified_source_refs() -> None:
    text = "# Option\nA stable imported rule."
    portable_chunk_key = rule_chunk_key("example.imported", 0, 0, text)
    canonical_chunk_key = portable_chunk_key
    component = _rule_descriptor(
        descriptor_id="dnd5e.example.imported",
        version="1.0.0",
        system_id="dnd5e",
        manifest={
            "id": "dnd5e.example.imported",
            "version": "1.0.0",
            "title": "Imported Rules",
            "namespace": "dnd5e.example.imported",
            "system_id": "dnd5e",
            "editions": ["2014"],
            "dependencies": [],
            "conflicts": [],
            "capabilities": [],
        },
        artifacts=[
            {
                "id": "stable-rule",
                "kind": "feature",
                "card": {"name": "Stable Rule"},
                "source_refs": [
                    {
                        "source_key": "example.imported",
                        "chunk_key": portable_chunk_key,
                        "page": 1,
                        "note": "Imported evidence",
                    }
                ],
            }
        ],
        mechanics=[],
        sources=[
            {
                "source_key": "example.imported",
                "title": "Imported Source",
                "edition": "2014",
                "locale": "en",
                "version": "1.0.0",
                "publication_id": "example.imported",
                "authority": "supplement",
                "canonical_source_key": None,
                "checksum": hashlib.sha256(text.encode()).hexdigest(),
                "metadata": {},
                "sections": [
                    {
                        "ordinal": 0,
                        "parent_ordinal": None,
                        "level": 1,
                        "title": "Option",
                        "path": ["Option"],
                        "content": text,
                        "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                        "start_offset": 0,
                        "end_offset": len(text),
                        "chunks": [
                            {
                                "key": portable_chunk_key,
                                "ordinal": 0,
                                "heading_path": ["Option"],
                                "content": text,
                                "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                                "token_count": 5,
                                "metadata": {
                                    "start_offset": 0,
                                    "end_offset": len(text),
                                    "page_start": 1,
                                    "page_end": 1,
                                },
                            }
                        ],
                    }
                ],
            }
        ],
        metadata={"distribution": "private"},
    )

    package, _blobs = build_rule_content_package(
        package_id="dnd5e.example.imported.addon",
        version="1.0.0",
        system_id="dnd5e",
        manifest={
            "title": "Imported Addon",
            "classification": "third_party",
            "editions": ["2014"],
            "activation": {
                "rule_policy": "branch",
                "preset_policy": "none",
                "module_policy": "none",
            },
        },
        rule_descriptors=[component],
    )

    assert package["content"]["artifacts"][0]["source_refs"] == [
        {
            "source_key": "example.imported",
            "chunk_key": canonical_chunk_key,
            "page": 1,
            "note": "Imported evidence",
        }
    ]


def test_addon_composition_deduplicates_unified_component_records() -> None:
    notes = default_character_notes()
    notes["profile"]["summary"] = "A source-backed archive guard."
    card = build_dnd_content_actor(
        actor_id="dnd5e.example.actor.guard",
        version="2.0.0",
        actor_type="npc",
        name="Archive Guard",
        sheet=default_character_sheet(),
        notes=notes,
    )
    preset, preset_blobs = build_preset_content_package(
        package_id="dnd5e.example.preset",
        version="2.0.0",
        system_id="dnd5e",
        title="Example Preset",
        cards=[card],
        metadata={"license": "private", "attribution": "Test source"},
    )
    package, blobs = compose_addon_content_package(
        package_id="dnd5e.example.composed-addon",
        version="2.0.0",
        system_id="dnd5e",
        manifest={
            "title": "Composed Addon",
            "classification": "third_party",
            "editions": ["2014"],
            "activation": {
                "rule_policy": "branch",
                "preset_policy": "library",
                "module_policy": "none",
            },
        },
        components=[(preset, preset_blobs), (preset, preset_blobs)],
        metadata={"license": "private", "attribution": "Test source"},
    )

    assert package["kind"] == "addon"
    assert len(package["actors"]) == 1
    assert len(package["sources"]) == 1
    assert len(package["assets"]) == len(preset["assets"])
    assert blobs == preset_blobs


def test_addon_composition_uses_artifact_identity_for_selection_and_resolution() -> None:
    source_package, source_blobs = build_preset_content_package(
        package_id="dnd5e.example.empty-preset-source",
        version="2.0.0",
        system_id="dnd5e",
        title="Selection Source",
        cards=[
            build_dnd_content_actor(
                actor_id="dnd5e.example.selection-actor",
                version="2.0.0",
                actor_type="pc",
                name="Selection Actor",
                sheet=default_character_sheet(),
                notes=default_character_notes(),
            )
        ],
    )
    definition_checksum = "1" * 64
    core = build_content_package(
        kind="core_rules",
        package_id="dnd5e.example.selection-core",
        version="2.0.0",
        system_id="dnd5e",
        manifest={
            "title": "Selection Core",
            "classification": "official_core",
            "editions": ["2014"],
            "activation": {
                "rule_policy": "branch",
                "preset_policy": "none",
                "module_policy": "none",
            },
        },
        sources=source_package["sources"],
        assets=source_package["assets"],
        content_reviews=[],
        actors=[],
        content={
            "classification": "official_core",
            "editions": ["2014"],
            "activation": {
                "rule_policy": "branch",
                "preset_policy": "none",
                "module_policy": "none",
            },
            "conflicts": [],
            "rule_definitions": [
                {
                    "id": "dnd5e.example.selection-definition",
                    "version": "2.0.0",
                    "definition_checksum": definition_checksum,
                    "manifest": {"id": "dnd5e.example.selection-definition"},
                }
            ],
            "artifacts": [
                {
                    "id": "dnd5e.example.selection-definition.feature.choice",
                    "kind": "statblock",
                    "card": {"name": "Selection Actor"},
                    "rule_definition_id": "dnd5e.example.selection-definition",
                    "source_refs": [
                        {
                            "source_key": source_package["sources"][0]["source_key"],
                            "chunk_key": source_package["sources"][0]["sections"][0][
                                "chunks"
                            ][0]["key"],
                            "page": 1,
                            "note": "Original illustrated page",
                        }
                    ],
                }
            ],
            "mechanics": [],
        },
        metadata={"distribution": "private"},
    )
    addon, _ = compose_addon_content_package(
        package_id="dnd5e.example.selection-addon",
        version="2.0.0",
        system_id="dnd5e",
        manifest={
            "title": "Selection Addon",
            "classification": "third_party",
            "editions": ["2014"],
            "activation": {
                "rule_policy": "branch",
                "preset_policy": "none",
                "module_policy": "none",
            },
        },
        components=[
            (core, source_blobs),
            (core, source_blobs),
            (source_package, source_blobs),
        ],
    )
    assert "selection_rules" not in addon["content"]
    assert "resolutions" not in addon["content"]
    assert addon["actors"][0]["provenance"]["source_refs"][0]["page"] == 1
