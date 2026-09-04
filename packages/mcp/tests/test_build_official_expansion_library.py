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
