import hashlib

import pytest
from sagasmith_core.portable import build_module_pack, build_preset_pack, portable_checksum

from sagasmith_dnd.character_schema import default_character_notes, default_character_sheet
from sagasmith_dnd.portable_cards import build_dnd_actor_card
from sagasmith_dnd.public_library import build_public_library, validate_public_package


def _monster_card():
    notes = default_character_notes()
    notes["profile"]["summary"] = "A test monster."
    return build_dnd_actor_card(
        portable_id="example.monster",
        version="1.0.0",
        actor_type="monster",
        name="Example Monster",
        sheet=default_character_sheet(),
        notes=notes,
    )


def test_public_library_writes_index_and_package(tmp_path) -> None:
    package = build_preset_pack(
        portable_id="example.public-presets",
        version="1.0.0",
        system_id="dnd5e",
        cards=[_monster_card()],
        metadata={
            "title": "Public examples",
            "distribution": "shareable",
            "license": "CC0-1.0",
        },
    )
    index = build_public_library(tmp_path, [package])

    assert index["schema"] == "sagasmith.public-content-library.v1"
    assert index["packages"][0]["component_counts"] == {
        "actor_card": 1,
        "monster": 1,
    }
    assert (tmp_path / index["packages"][0]["path"]).is_file()


def test_public_library_rejects_private_package() -> None:
    private = build_preset_pack(
        portable_id="example.private-presets",
        version="1.0.0",
        system_id="dnd5e",
        cards=[_monster_card()],
        metadata={"distribution": "private", "license": "user-supplied"},
    )
    with pytest.raises(ValueError, match="not marked public"):
        validate_public_package(private)

    import copy

    mislabeled = copy.deepcopy(private)
    mislabeled["metadata"]["distribution"] = "public"
    mislabeled["checksum"] = portable_checksum(mislabeled)
    with pytest.raises(ValueError, match="no redistributable license"):
        validate_public_package(mislabeled)


def test_public_library_rejects_private_nested_card() -> None:
    card = _monster_card()
    card["metadata"]["distribution"] = "private"
    card["checksum"] = portable_checksum(card)
    package = build_preset_pack(
        portable_id="example.mislabeled-presets",
        version="1.0.0",
        system_id="dnd5e",
        cards=[card],
        metadata={"distribution": "shareable", "license": "CC0-1.0"},
    )
    with pytest.raises(ValueError, match="not marked public"):
        validate_public_package(package)


def test_public_library_indexes_module_scenes_assets_and_actors(tmp_path) -> None:
    content = "# Arrival\nA public test scene."
    chunk_content = "A public test scene."
    package = build_module_pack(
        portable_id="example.public-module",
        version="1.0.0",
        system_id="dnd5e",
        source={
            "source_key": "example.public-module",
            "title": "Public Module",
            "parser_profile": "test",
            "parser_version": "1",
            "metadata": {},
        },
        document={
            "media_type": "text/markdown",
            "content": content,
            "checksum": hashlib.sha256(content.encode()).hexdigest(),
        },
        scene_atlas=[
            {
                "stable_key": "arrival",
                "title": "Arrival",
                "chapter_ordinal": 0,
                "scene_ordinal": 0,
                "chapter": "Arrival",
                "scene_type": "scene",
                "page_start": None,
                "page_end": None,
                "headings": ["Arrival"],
                "keywords": [],
                "metadata": {},
                "content": chunk_content,
                "content_checksum": hashlib.sha256(chunk_content.encode()).hexdigest(),
                "chunks": [
                    {
                        "ordinal": 0,
                        "heading_path": ["Arrival"],
                        "content": chunk_content,
                        "start_offset": 0,
                        "end_offset": len(chunk_content),
                        "metadata": {},
                        "content_hash": hashlib.sha256(chunk_content.encode()).hexdigest(),
                    }
                ],
            }
        ],
        actors=[_monster_card()],
        metadata={"distribution": "public", "license": "CC0-1.0"},
    )
    index = build_public_library(tmp_path, [package])
    assert index["packages"][0]["component_counts"] == {
        "scene": 1,
        "asset": 0,
        "actor_card": 1,
    }
