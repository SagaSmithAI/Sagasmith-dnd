"""Offline composition safety checks; no commercial archive fixtures."""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from sagasmith_dnd.official_expansions import load_official_expansion_lock

scripts = Path(__file__).parents[1] / "scripts"
spec = importlib.util.spec_from_file_location(
    "official_library_builder", scripts / "build_official_expansion_library.py"
)
builder = importlib.util.module_from_spec(spec)
with patch.object(sys, "path", [str(scripts), *sys.path]):
    spec.loader.exec_module(builder)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def test_composition_binds_input_steps_and_final_digest(monkeypatch):
    monkeypatch.setitem(
        builder._STEPS, "fixture", lambda data: (data + b" repaired", {"published": False})
    )
    target = {
        "archive_sha256": sha(b"source repaired"),
        "local_repair": {"source_archive_sha256": sha(b"source"), "steps": ["fixture"]},
    }
    assert builder._repair(b"source", target) == (
        b"source repaired",
        [{"step": "fixture", "published": False}],
    )
    with pytest.raises(ValueError, match="repair input"):
        builder._repair(b"tampered", target)
    target["archive_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="target lock"):
        builder._repair(b"source", target)


@pytest.mark.parametrize("steps", [[], "subclass_grants", ["unknown"], [None]])
def test_unknown_or_invalid_steps_fail_closed(steps):
    with pytest.raises(ValueError):
        builder._repair(
            b"source",
            {
                "archive_sha256": sha(b"source"),
                "local_repair": {"source_archive_sha256": sha(b"source"), "steps": steps},
            },
        )


def test_unchanged_archives_still_require_exact_hash():
    assert builder._repair(b"source", {"archive_sha256": sha(b"source")}) == (b"source", [])
    with pytest.raises(ValueError):
        builder._repair(b"changed", {"archive_sha256": sha(b"source")})


def test_shipped_recipes_are_executable_and_explicitly_local():
    lock = load_official_expansion_lock()
    assert "Canonical input lineage only" in lock["source_commit_role"]
    targets = [p for p in lock["packages"] if "local_repair" in p]
    assert len(targets) == 5
    modules = {
        "subclass_grants": builder.repair_subclass_grants,
        "artificer_asi": builder.repair_artificer_asi,
        "artificer_context": builder.repair_artificer_context,
    }
    for target in targets:
        recipe = target["local_repair"]
        digest = recipe["source_archive_sha256"]
        assert digest in modules[recipe["steps"][0]]._RECIPES
        assert digest != target["archive_sha256"]
        assert "-local." in target["version"]
        assert all(step in builder._STEPS for step in recipe["steps"])


def test_eberron_repairs_are_ordered_and_exact_hash_bound():
    lock = load_official_expansion_lock()
    target = next(
        package for package in lock["packages"] if package["publication_id"] == "erlw2014"
    )
    assert target["local_repair"]["steps"][-3:] == [
        "steel_defender_owner_binding", "artificer_starting_equipment",
        "steel_defender_lifecycle_policy",
    ]
    recipe = next(iter(builder.repair_steel_defender_citation._RECIPES.values()))
    assert recipe["version"] == "1.0.4-local.steel-defender-citation.1"
    assert recipe["definition_version"] == "1.0.2-local.steel-defender-citation.1"
    assert [
        evidence["chunk_key"].split("/section-", 1)[1].split("/", 1)[0]
        for evidence in recipe["evidence"]
    ] == ["393", "398", "399", "400"]
    assert all(len(evidence["chunk_sha256"]) == 64 for evidence in recipe["evidence"])
    with pytest.raises(ValueError, match="exact reviewed"):
        builder.repair_steel_defender_citation.repair_archive(b"wrong archive")

    owner = builder.repair_steel_defender_owner_binding
    assert owner._SOURCE_SHA == "6c045a44eba3e231d4e65897c1617f6543df7f85f5ceaa16e008c69dd01d2f09"
    assert owner._CHUNK_KEY.endswith("/section-390/chunk-417-e7d3b85f277baaaa")
    assert owner._PACKAGE_VERSION == "1.0.5-local.steel-defender-owner-binding.1"
    assert owner._DEFINITION_VERSION == "1.0.3-local.steel-defender-owner-binding.1"
    with pytest.raises(ValueError, match="exact reviewed"):
        owner.repair_archive(b"wrong archive")
    equipment = builder.repair_artificer_starting_equipment
    assert equipment._PACKAGE_VERSION == "1.0.6-local.starting-equipment.1"
    assert equipment._CHUNK_KEY.endswith("/section-324/chunk-349-a6c7c7a1e1a8b94f")
    assert equipment._SOURCE_SHA == (
        "bfd0d326c22abef2c3bb5770c6683a1d82ce522ea4aa4ddc2dba01750a14c122"
    )
    lifecycle = builder.repair_steel_defender_lifecycle_policy
    assert lifecycle._SOURCE_SHA == (
        "6189467c53b675d39cc3eddd8de74932040ee2e2a9024a187b9dfd80f76b8cd4"
    )
    assert lifecycle._PACKAGE_VERSION == target["version"]
    assert lifecycle._DEFINITION_VERSION == "1.0.5-local.steel-defender-lifecycle.1"
    assert lifecycle._CHUNK_KEY == owner._CHUNK_KEY
    with pytest.raises(ValueError, match="exact reviewed"):
        lifecycle.repair_archive(b"wrong archive")


@pytest.mark.parametrize("relative", ["../outside.pack", "missing.pack"])
def test_source_archive_must_exist_inside_library(tmp_path, relative):
    source = tmp_path / "library"
    source.mkdir()
    (tmp_path / "outside.pack").write_bytes(b"outside")
    with pytest.raises(ValueError, match="inside"):
        builder._source_path(source.resolve(), {"path": relative})


def test_source_and_output_cannot_overlap_or_overwrite(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="new library"):
        builder.build_library(source, source)
    with pytest.raises(ValueError, match="overlap"):
        builder.build_library(source, source / "output")
    assert list(source.iterdir()) == []


def test_bad_source_fails_preflight_without_creating_output(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "output"
    (source / "bad.pack").write_bytes(b"untrusted archive")
    (source / "index.json").write_text(
        json.dumps(
            {
                "schema": builder.CONTENT_LIBRARY_INDEX_SCHEMA,
                "packages": [{"id": "fixture", "path": "bad.pack"}],
            }
        )
    )
    monkeypatch.setattr(
        builder,
        "load_official_expansion_lock",
        lambda: {
            "packages": [{"id": "fixture", "archive_sha256": "0" * 64}],
            "support_packages": [],
        },
    )
    with pytest.raises(ValueError, match="repair input"):
        builder.build_library(source, output)
    assert not output.exists()
    assert (source / "bad.pack").read_bytes() == b"untrusted archive"
