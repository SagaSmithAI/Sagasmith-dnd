"""Exact-input repair tests; private commercial source is opt-in only."""

import hashlib
import importlib.util
import os
from pathlib import Path

import pytest
from sagasmith_core.content_pack import loads_content_archive
from sagasmith_dnd.content_validation import selection_contract_errors

spec = importlib.util.spec_from_file_location(
    "repair_steel_defender_lifecycle_policy",
    Path(__file__).parents[1] / "scripts/repair_steel_defender_lifecycle_policy.py",
)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


def test_wrong_bytes_rejected_before_any_package_change():
    with pytest.raises(ValueError, match="exact reviewed"):
        repair.repair_archive(b"wrong archive")


def test_private_exact_archive_changes_only_template_and_preserves_source():
    raw = os.environ.get("SAGASMITH_STEEL_DEFENDER_LIFECYCLE_INPUT")
    if not raw:
        pytest.skip("requires explicitly supplied private exact lifecycle input")
    path = Path(raw)
    data = path.read_bytes()
    output, report = repair.repair_archive(data)
    assert repair.repair_archive(data) == (output, report)
    original, original_blobs = loads_content_archive(data)
    changed, changed_blobs = loads_content_archive(output)
    assert original_blobs == changed_blobs
    assert original["sources"] == changed["sources"]
    assert original["assets"] == changed["assets"]
    before = {a["id"]: a for a in original["content"]["artifacts"]}
    after = {a["id"]: a for a in changed["content"]["artifacts"]}
    statblock_id = repair._PREFIX + ".statblock.steel-defender"
    assert set(before) == set(after)
    assert {key for key in before if before[key] != after[key]} == {statblock_id}
    artifact = after[statblock_id]
    assert selection_contract_errors(artifact) == []
    template = dict(artifact["card"]["dependent_actor_template"])
    assert template.pop("lifecycle_policy") == {"schema_version": 1, "owner_death": "independent"}
    assert template == before[statblock_id]["card"]["dependent_actor_template"]
    assert report["version"] == repair._PACKAGE_VERSION
    assert report["archive_sha256"] == hashlib.sha256(output).hexdigest()
    assert all(d["version"] == d["manifest"]["version"] == repair._DEFINITION_VERSION
               for d in changed["content"]["rule_definitions"])
    assert path.read_bytes() == data
    with pytest.raises(ValueError, match="exact reviewed"):
        repair.repair_archive(output)
