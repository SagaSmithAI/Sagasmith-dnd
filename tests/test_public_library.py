from __future__ import annotations

import copy
import hashlib

import pytest
from sagasmith_core.content_pack import (
    content_package_checksum,
    dumps_content_archive,
)

from sagasmith_dnd.character_schema import default_character_notes, default_character_sheet
from sagasmith_dnd.content_actors import build_dnd_content_actor
from sagasmith_dnd.content_packages import (
    attach_auxiliary_assets,
    build_preset_content_package,
)
from sagasmith_dnd.public_library import (
    build_content_library,
    build_public_library,
    validate_public_package,
)


def _package():
    notes = default_character_notes()
    notes["profile"]["summary"] = "A test monster."
    card = build_dnd_content_actor(
        actor_id="example.monster",
        version="2.0.0",
        actor_type="monster",
        name="Example Monster",
        sheet=default_character_sheet(),
        notes=notes,
    )
    return build_preset_content_package(
        package_id="example.public-presets",
        version="2.0.0",
        system_id="dnd5e",
        title="Public examples",
        cards=[card],
        metadata={
            "distribution": "shareable",
            "license": "CC0-1.0",
            "attribution": "Example publisher",
            "license_evidence": {
                "type": "open_license",
                "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                "source_url": "https://example.test/source",
            },
        },
    )


def test_public_library_writes_unified_index_archive_and_source_blob(tmp_path) -> None:
    package, blobs = _package()
    map_path = tmp_path / "encounter-map.png"
    map_path.write_bytes(b"a source-distributed map")
    package, blobs = attach_auxiliary_assets(
        package,
        blobs,
        [{"path": map_path, "kind": "map"}],
    )
    archive = dumps_content_archive(package, blobs)
    index = build_public_library(
        tmp_path,
        [package],
        archives={package["checksum"]: archive},
    )

    entry = index["packages"][0]
    assert index["schema"] == "sagasmith.content-library.v1"
    assert index["visibility"] == "public"
    assert index["package_format"] == "sagasmith.content-package"
    assert "map" in index["browser_asset_kinds"]
    assert entry["kind"] == "preset"
    assert entry["component_counts"]["actor_card"] == 1
    assert (tmp_path / entry["path"]).is_file()
    assert (tmp_path / entry["download_path"]).suffix == ".sagasmith-pack"
    archive_path = tmp_path / entry["download_path"]
    assert archive_path.is_file()
    assert entry["archive_size"] == len(archive)
    assert entry["archive_checksum"] == hashlib.sha256(archive).hexdigest()
    assert hashlib.sha256(archive_path.read_bytes()).hexdigest() == entry["archive_checksum"]
    normalized_asset = next(
        item for item in package["assets"] if item["kind"] == "normalized_document"
    )
    assert (tmp_path / "blobs" / "sha256" / normalized_asset["checksum"]).is_file()
    map_asset = next(item for item in package["assets"] if item["kind"] == "map")
    assert (tmp_path / "blobs" / "sha256" / map_asset["checksum"]).is_file()


def test_public_library_rejects_private_package() -> None:
    package, _blobs = _package()
    private = copy.deepcopy(package)
    private["metadata"]["distribution"] = "private"
    private["checksum"] = content_package_checksum(private)
    with pytest.raises(ValueError, match="not marked public"):
        validate_public_package(private)


def test_public_library_rejects_self_asserted_redistribution_authorization() -> None:
    package, _blobs = _package()
    invalid = copy.deepcopy(package)
    invalid["metadata"].pop("license_evidence")
    invalid["metadata"]["redistribution_authorization"] = {"confirmed": True}
    invalid["checksum"] = content_package_checksum(invalid)
    with pytest.raises(ValueError, match="no exact license evidence"):
        validate_public_package(invalid)


def test_public_library_rejects_asset_without_redistribution_rights() -> None:
    package, _blobs = _package()
    invalid = copy.deepcopy(package)
    invalid["assets"][0]["license"] = "private"
    invalid["checksum"] = content_package_checksum(invalid)
    with pytest.raises(ValueError, match="asset .* different license"):
        validate_public_package(invalid)


def test_private_library_keeps_user_supplied_content_without_claiming_public_rights(
    tmp_path,
) -> None:
    package, blobs = _package()
    package = copy.deepcopy(package)
    package["metadata"].update(
        {"distribution": "private", "license": "user-supplied"}
    )
    for asset in package["assets"]:
        asset["license"] = "user-supplied"
    package["checksum"] = content_package_checksum(package)
    archive = dumps_content_archive(package, blobs)

    index = build_content_library(
        tmp_path,
        [package],
        archives={package["checksum"]: archive},
    )

    assert index["schema"] == "sagasmith.content-library.v1"
    assert index["visibility"] == "private"
    assert index["packages"][0]["distribution"] == "private"
