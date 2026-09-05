"""Source-free fixtures and opt-in exact private archive regression checks."""

import hashlib
import importlib.util
import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
from sagasmith_core.content_pack import loads_content_archive
from sagasmith_dnd.content_validation import catalog_review_errors, selection_contract_errors

spec = importlib.util.spec_from_file_location(
    "repair_subclass_grants", Path(__file__).parents[1] / "scripts/repair_subclass_grants.py"
)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


def test_normalization_preserves_known_grants_levels_and_original():
    card = {
        "spell_grants": [{"name": "Fixture Cantrip", "minimum_level": 2, "method": "known"}],
        "always_prepared_spells": [{"name": "Fixture Spell", "minimum_level": 3}],
    }
    before = deepcopy(card)
    result = repair._canonical_card(card)
    assert card == before
    assert "always_prepared_spells" not in result
    assert result["spell_grants"] == [
        *card["spell_grants"],
        {"name": "Fixture Spell", "minimum_level": 3, "method": "always_prepared"},
    ]


@pytest.mark.parametrize("conflict", ["method", "level", "duplicate"])
def test_conflicting_existing_grants_are_rejected(conflict):
    grant = {"name": "Fixture Spell", "minimum_level": 3, "method": "always_prepared"}
    card = {
        "spell_grants": [grant],
        "always_prepared_spells": [{"name": "Fixture Spell", "minimum_level": 3}],
    }
    if conflict == "method":
        grant["method"] = "known"
    elif conflict == "level":
        grant["minimum_level"] = 5
    else:
        card["spell_grants"].append(deepcopy(grant))
    before = deepcopy(card)
    with pytest.raises(ValueError, match="conflicting"):
        repair._canonical_card(card)
    assert card == before


def test_empty_legacy_field_does_not_invent_grants():
    assert repair._canonical_card({"always_prepared_spells": []}) == {"spell_grants": []}


def test_identical_existing_grant_is_not_duplicated():
    grant = {"name": "Fixture Spell", "minimum_level": 3, "method": "always_prepared"}
    assert repair._canonical_card(
        {
            "spell_grants": [grant],
            "always_prepared_spells": [{"name": "Fixture Spell", "minimum_level": 3}],
        }
    ) == {"spell_grants": [grant]}


@pytest.mark.parametrize(
    "legacy", [None, {}, ["bad"], [{"name": "Fixture"}], [{"name": "", "minimum_level": 3}]]
)
def test_invalid_legacy_shape_rejected(legacy):
    with pytest.raises(ValueError):
        repair._canonical_card({"always_prepared_spells": legacy})


def test_unknown_archive_is_rejected_before_output(tmp_path):
    source, output = tmp_path / "source.pack", tmp_path / "new.pack"
    source.write_bytes(b"unknown input")
    with pytest.raises(ValueError, match="exact reviewed"):
        repair.main(["--archive", str(source), "--output", str(output)])
    assert not output.exists()
    assert source.read_bytes() == b"unknown input"


@pytest.mark.parametrize("same_source", [True, False])
def test_cli_never_overwrites(tmp_path, same_source):
    source = tmp_path / "source.pack"
    source.write_bytes(b"source")
    output = source if same_source else tmp_path / "existing.pack"
    if not same_source:
        output.write_bytes(b"existing")
    before = (source.read_bytes(), output.read_bytes())
    with pytest.raises(ValueError, match="new archive path"):
        repair.main(["--archive", str(source), "--output", str(output)])
    assert (source.read_bytes(), output.read_bytes()) == before


@pytest.mark.parametrize("source_sha", sorted(repair._RECIPES))
def test_optional_exact_archives_reproduce_reviewed_outputs(source_sha):
    location = os.environ.get("SAGASMITH_SUBCLASS_REPAIR_LIBRARY")
    if not location:
        pytest.skip("requires explicitly supplied private canonical library")
    root = Path(location).resolve()
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    entry = next(item for item in index["packages"] if item["archive_sha256"] == source_sha)
    path = (root / entry["path"]).resolve()
    assert path.is_relative_to(root)
    data = path.read_bytes()
    original, blobs = loads_content_archive(data)
    output, report = repair.repair_archive(data)
    corrected, corrected_blobs = loads_content_archive(output)
    assert hashlib.sha256(data).hexdigest() == source_sha
    assert hashlib.sha256(output).hexdigest() == repair._RECIPES[source_sha]["output_sha256"]
    assert report["published"] is report["source_assets_changed"] is False
    assert blobs == corrected_blobs
    for field in ("sources", "assets", "dependencies", "actors", "content_reviews"):
        assert original[field] == corrected[field]
    old_by_id = {a["id"]: a for a in original["content"]["artifacts"]}
    assert set(old_by_id) == {a["id"] for a in corrected["content"]["artifacts"]}
    changed = 0
    for artifact in corrected["content"]["artifacts"]:
        old = old_by_id[artifact["id"]]
        if artifact != old:
            changed += 1
            assert old["kind"] == "subclass"
            assert "always_prepared_spells" not in artifact["card"]
            assert selection_contract_errors(artifact) == catalog_review_errors(artifact) == []
            assert artifact["card"] == repair._canonical_card(old["card"])
    assert changed == report["artifact_count"] == repair._RECIPES[source_sha]["count"]
    assert path.read_bytes() == data
