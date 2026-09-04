"""Context classification tests use synthetic prose or explicit private inputs."""

import hashlib
import importlib.util
import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
from sagasmith_core.content_pack import loads_content_archive
from sagasmith_dnd.content_import import author_selection_card_from_candidate
from sagasmith_dnd.content_validation import catalog_review_errors, selection_contract_errors

spec = importlib.util.spec_from_file_location(
    "repair_artificer_context", Path(__file__).parents[1] / "scripts/repair_artificer_context.py"
)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


def _feature():
    return {
        "id": "dnd5e.addon.context-fixture.feature.directory",
        "kind": "feature",
        "application_state": "selection_ready",
        "mechanical_scope": "mechanical",
        "card": {
            "name": "Fixture Directory",
            "class_name": "Artificer",
            "minimum_level": 1,
            "description": "Wrong combined description.",
        },
        "source_refs": [{"source_key": "fixture", "chunk_key": "fixture/chunk", "page": 1}],
        "rule_refs": ["rule-source:fixture#chunk:fixture/chunk"],
        "rule_clauses": [{"id": "old-mixed-clause"}],
    }


@pytest.mark.parametrize("pure", [True, False])
def test_context_is_not_selectable_and_authoring_cannot_repromote_it(pure):
    artifact = _feature()
    refs = deepcopy(artifact["source_refs"])
    if pure:
        repair._as_context(artifact, description="Synthetic directory only.", source_refs=refs)
        assert "rule_clauses" not in artifact
        assert artifact["card"]["description"] == "Synthetic directory only."
        assert artifact["semantic_resolution"]["mode"] == "descriptive"
    else:
        repair._as_context(artifact)
        assert artifact["card"]["description"] == "Wrong combined description."
        assert artifact["rule_clauses"] == [{"id": "old-mixed-clause"}]
    assert "class_name" not in artifact["card"]
    assert "minimum_level" not in artifact["card"]
    assert artifact["source_refs"] == refs
    assert selection_contract_errors(artifact) == catalog_review_errors(artifact) == []
    assert artifact["selection_contract"]["status"] == "not_applicable"
    assert artifact["selection_contract"]["materializer"] is None
    authored = author_selection_card_from_candidate({"artifact": artifact})
    assert authored["application_state"] == "catalog_only"
    assert authored["selection_applicability"] == "not_applicable"


def test_unknown_archive_and_existing_output_are_rejected_without_writes(tmp_path):
    source, output = tmp_path / "source.pack", tmp_path / "output.pack"
    source.write_bytes(b"unreviewed input")
    with pytest.raises(ValueError, match="exact reviewed"):
        repair.main(["--archive", str(source), "--output", str(output)])
    assert not output.exists()
    with pytest.raises(ValueError, match="new archive path"):
        repair.main(["--archive", str(source), "--output", str(source)])
    assert source.read_bytes() == b"unreviewed input"


@pytest.mark.parametrize("source_sha", sorted(repair._RECIPES))
def test_optional_exact_context_archive_preserves_real_options(source_sha):
    location = os.environ.get("SAGASMITH_CONTEXT_REPAIR_LIBRARY")
    if not location:
        pytest.skip("requires an explicitly supplied private context repair input library")
    root = Path(location).resolve()
    index = json.loads((root / "index.json").read_text(encoding="utf-8"))
    entry = next(p for p in index["packages"] if p["archive_sha256"] == source_sha)
    path = (root / entry["path"]).resolve()
    assert path.is_relative_to(root)
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == source_sha
    original, blobs = loads_content_archive(data)
    output, report = repair.repair_archive(data)
    corrected, output_blobs = loads_content_archive(output)
    assert output_blobs == blobs
    assert corrected["sources"] == original["sources"]
    assert corrected["assets"] == original["assets"]
    assert corrected["dependencies"] == original["dependencies"]
    assert corrected["version"] != original["version"]
    old_definitions = {d["id"]: d for d in original["content"]["rule_definitions"]}
    new_definitions = {d["id"]: d for d in corrected["content"]["rule_definitions"]}
    assert old_definitions.keys() == new_definitions.keys()
    for identifier, definition in new_definitions.items():
        assert definition["manifest"]["version"] == definition["version"]
        if identifier == repair._RECIPES[source_sha]["prefix"]:
            assert definition["version"] != old_definitions[identifier]["version"]
            assert (
                definition["definition_checksum"]
                != old_definitions[identifier]["definition_checksum"]
            )
        else:
            assert definition == old_definitions[identifier]
    before = {a["id"]: a for a in original["content"]["artifacts"]}
    after = {a["id"]: a for a in corrected["content"]["artifacts"]}
    assert before.keys() == after.keys()
    for identifier in before.keys() - set(report["changed_artifacts"]):
        assert after[identifier] == before[identifier]
    recipe = repair._RECIPES[source_sha]
    contexts = list(recipe["directories"])
    contexts += [recipe[k] for k in ("sidebar", "mixed_context") if k in recipe]
    for slug in contexts:
        artifact = after[f"{recipe['prefix']}.feature.{slug}"]
        assert artifact["application_state"] == "catalog_only"
        assert artifact["selection_contract"]["status"] == "not_applicable"
        assert "class_name" not in artifact["card"]
        assert "minimum_level" not in artifact["card"]
    if "sidebar" in recipe:
        sections = repair._sections(original, blobs, recipe)
        sidebar = after[f"{recipe['prefix']}.feature.{recipe['sidebar']}"]
        assert sidebar["card"]["description"] == sections[57][0][: recipe["sidebar_end"]]
        focus = sections[56][0] + " " + sections[57][0][recipe["sidebar_end"] :].strip()
        casting = after[f"{recipe['prefix']}.feature.{recipe['spellcasting']}"]
        assert focus in casting["card"]["description"]
        assert all(ref in casting["source_refs"] for ref in sections[56][1] + sections[57][1])
        specialist = after[f"{recipe['prefix']}.feature.{recipe['specialist']}"]
        assert specialist["card"]["name"] == "Artificer Specialist"
        assert specialist["card"]["minimum_level"] == 3
        assert specialist["application_state"] == "selection_ready"
    assert path.read_bytes() == data
