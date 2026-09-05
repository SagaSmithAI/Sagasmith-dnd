"""Offline repair regression fixtures contain no published book text."""

import hashlib
import importlib.util
import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
from sagasmith_core.content_pack import loads_content_archive
from sagasmith_dnd.content_import import validate_selection_ready_artifacts
from sagasmith_dnd.content_validation import catalog_review_errors, selection_contract_errors

spec = importlib.util.spec_from_file_location(
    "repair_artificer_asi", Path(__file__).parents[1] / "scripts/repair_artificer_asi.py"
)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


def _evidence():
    body = "Synthetic ASI fixture; no reproduced source prose."
    checksum = hashlib.sha256(body.encode()).hexdigest()
    package = {
        "sources": [
            {
                "source_key": "fixture",
                "normalized_document_asset_key": "text",
                "sections": [
                    {
                        "ordinal": 1,
                        "start_offset": 0,
                        "end_offset": len(body),
                        "content_hash": checksum,
                        "chunks": [{"key": "fixture/chunk", "page_start": 1}],
                    }
                ],
            }
        ],
        "assets": [{"asset_key": "text", "checksum": checksum}],
    }
    recipe = {
        "source_key": "fixture",
        "sections": {1: checksum},
        "prose_section": 1,
        "prefix": "dnd5e.addon.asi-fixture",
        "slug": "ability-score-improvement",
    }
    return package, {checksum: body.encode()}, recipe


def test_source_bound_asi_contract_and_non_mutating_evidence():
    package, blobs, recipe = _evidence()
    before = deepcopy((package, blobs, recipe))
    description, refs = repair._source_evidence(package, blobs, recipe)
    artifact = repair._asi_artifact(recipe, description, refs)
    assert (package, blobs, recipe) == before
    assert selection_contract_errors(artifact) == catalog_review_errors(artifact) == []
    assert (
        validate_selection_ready_artifacts(
            [
                {
                    key: value
                    for key, value in artifact.items()
                    if key not in {"catalog_review", "selection_contract", "rule_definition_id"}
                }
            ]
        )
        == []
    )
    binding = artifact["selection_contract"]["schema"]["card_binding"]
    assert binding["minimum_level"] == 4
    assert binding["repeatable_selection_levels"] == [4, 8, 12, 16, 19]
    assert binding["selection_requirements"] == {
        "field": "ability_score_increases",
        "kind": "ability_score_increase",
        "allowed_distributions": [[2], [1, 1]],
        "maximum_score": 20,
    }
    assert "ability_score_increases" in artifact["selection_contract"]["schema"]["selection_fields"]
    assert artifact["source_refs"] == refs
    assert artifact["rule_refs"] == ["rule-source:fixture#chunk:fixture/chunk"]
    refs[0]["page"] = 99
    assert artifact["source_refs"][0]["page"] == 1


@pytest.mark.parametrize("tamper", ["blob", "hash", "offset", "missing", "duplicate", "chunks"])
def test_exact_source_evidence_rejects_tampering(tamper):
    package, blobs, recipe = _evidence()
    sections = package["sources"][0]["sections"]
    if tamper == "blob":
        blobs[next(iter(blobs))] += b"changed"
    elif tamper == "hash":
        sections[0]["content_hash"] = "0" * 64
    elif tamper == "offset":
        sections[0]["end_offset"] -= 1
    elif tamper == "missing":
        sections.clear()
    elif tamper == "duplicate":
        sections.append(deepcopy(sections[0]))
    elif tamper == "chunks":
        sections[0]["chunks"] = []
    with pytest.raises(ValueError):
        repair._source_evidence(package, blobs, recipe)


def test_unreviewed_input_cannot_produce_an_archive(tmp_path):
    source = tmp_path / "input.pack"
    output = tmp_path / "output.pack"
    source.write_bytes(b"not an approved archive")
    with pytest.raises(ValueError, match="exact reviewed"):
        repair.main(["--archive", str(source), "--output", str(output)])
    assert not output.exists()
    assert source.read_bytes() == b"not an approved archive"


@pytest.mark.parametrize("same_source", [True, False])
def test_cli_never_overwrites_sources_or_existing_outputs(tmp_path, same_source):
    source = tmp_path / "source.pack"
    source.write_bytes(b"original source")
    output = source if same_source else tmp_path / "output.pack"
    if not same_source:
        output.write_bytes(b"existing output")
    before = (source.read_bytes(), output.read_bytes())
    with pytest.raises(ValueError, match="new archive path"):
        repair.main(["--archive", str(source), "--output", str(output)])
    assert (source.read_bytes(), output.read_bytes()) == before


@pytest.mark.parametrize("source_sha", sorted(repair._RECIPES))
def test_optional_exact_private_archives_preserve_unrelated_content(source_sha):
    location = os.environ.get("SAGASMITH_ASI_REPAIR_LIBRARY")
    if not location:
        pytest.skip("requires explicitly supplied private ASI repair input library")
    root = Path(location).resolve()
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    entry = next(item for item in index["packages"] if item["archive_sha256"] == source_sha)
    path = (root / entry["path"]).resolve()
    assert path.is_relative_to(root)
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == source_sha
    original, source_blobs = loads_content_archive(data)
    result, report = repair.repair_archive(data)
    corrected, result_blobs = loads_content_archive(result)
    assert source_blobs == result_blobs
    assert corrected["sources"] == original["sources"]
    assert corrected["assets"] == original["assets"]
    assert corrected["dependencies"] == original["dependencies"]
    assert corrected["actors"] == original["actors"]
    assert corrected["content"]["mechanics"] == original["content"]["mechanics"]
    assert corrected["version"] != original["version"]
    assert corrected["checksum"] != original["checksum"]
    assert report["published"] is False
    assert report["source_assets_changed"] is False
    before_artifacts = {a["id"]: a for a in original["content"]["artifacts"]}
    after_artifacts = {a["id"]: a for a in corrected["content"]["artifacts"]}
    for identifier, artifact in before_artifacts.items():
        if identifier != report["artifact_id"]:
            assert after_artifacts[identifier] == artifact
    assert set(after_artifacts) == set(before_artifacts) | {report["artifact_id"]}
    assert {d["id"] for d in original["content"]["rule_definitions"]} == {
        d["id"] for d in corrected["content"]["rule_definitions"]
    }
    # The script may be rerun in memory, but never rewrites the supplied archive.
    assert repair.repair_archive(data)[0] == result
    assert path.read_bytes() == data
