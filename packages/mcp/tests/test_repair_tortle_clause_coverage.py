"""Local-only real archive proof; no private content is bundled in this suite."""

import hashlib
import importlib.util
import os
from pathlib import Path

import pytest
from sagasmith_core.content_pack import loads_content_archive
from sagasmith_dnd.content_import import validate_selection_ready_artifacts
from sagasmith_dnd.content_packages import content_definition_checksum
from sagasmith_dnd.official_expansions import load_official_expansion_lock

spec = importlib.util.spec_from_file_location(
    "tortle_clause_repair",
    Path(__file__).parents[1] / "scripts" / "repair_tortle_clause_coverage.py",
)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


def test_tortle_repair_rejects_unreviewed_archive():
    with pytest.raises(ValueError, match="exact reviewed"):
        repair.repair_archive(b"untrusted archive")


def test_real_tortle_repair_preserves_sources_and_matches_runtime_lock():
    source = os.environ.get("SAGASMITH_DND_TEST_TORTLE_SOURCE_ARCHIVE")
    if not source:
        pytest.skip("requires the authorized original Tortle 1.0.2 archive")
    path = Path(source)
    original_bytes = path.read_bytes()
    original, source_blobs = loads_content_archive(original_bytes)
    data, report = repair.repair_archive(original_bytes)
    rebuilt, rebuilt_blobs = loads_content_archive(data)
    assert path.read_bytes() == original_bytes
    assert rebuilt_blobs == source_blobs
    for field in ("sources", "assets", "actors", "dependencies"):
        assert rebuilt[field] == original[field]
    for before, after in zip(
        original["content"]["artifacts"], rebuilt["content"]["artifacts"], strict=True,
    ):
        if before["id"] != repair._PREFIX + ".species.tortle":
            assert before == after
        else:
            assert before["card"] == after["card"]
            for clause in after["rule_clauses"]:
                assert clause["source_citations"] == before["rule_clauses"][0]["source_citations"]
    assert not validate_selection_ready_artifacts(rebuilt["content"]["artifacts"])
    definition = rebuilt["content"]["rule_definitions"][0]
    assert definition["definition_checksum"] == content_definition_checksum(
        manifest=definition["manifest"], artifacts=rebuilt["content"]["artifacts"],
        mechanics=rebuilt["content"]["mechanics"],
    )
    locked = next(p for p in load_official_expansion_lock()["packages"]
                  if p["publication_id"] == "tortle2014")
    assert report["published"] is False
    assert rebuilt["version"] == locked["version"]
    assert rebuilt["checksum"] == locked["checksum"]
    assert hashlib.sha256(data).hexdigest() == locked["archive_sha256"]
