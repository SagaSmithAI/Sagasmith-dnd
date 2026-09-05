import hashlib
import importlib.util
import os
from pathlib import Path

import pytest
from sagasmith_core.content_pack import loads_content_archive
from sagasmith_dnd.content_packages import validate_dnd_content_package
from sagasmith_dnd.content_validation import selection_contract_errors

spec = importlib.util.spec_from_file_location(
    "repair_artificer_starting_equipment",
    Path(__file__).parents[1] / "scripts/repair_artificer_starting_equipment.py",
)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


def test_wrong_bytes_rejected_before_any_package_change():
    with pytest.raises(ValueError, match="exact reviewed"):
        repair.repair_archive(b"wrong archive")


def test_private_exact_archive_roundtrip_when_explicitly_provided():
    path = os.environ.get("SAGASMITH_ARTIFICER_STARTING_EQUIPMENT_INPUT")
    if not path:
        pytest.skip("requires explicitly supplied private exact starting-equipment archive")
    data = Path(path).read_bytes()
    assert hashlib.sha256(data).hexdigest() == repair._SOURCE_SHA
    output, report = repair.repair_archive(data)
    original, original_blobs = loads_content_archive(data)
    changed, changed_blobs = loads_content_archive(output)
    validate_dnd_content_package(changed)
    assert original_blobs == changed_blobs
    assert original["sources"] == changed["sources"]
    assert original["assets"] == changed["assets"]
    before = {a["id"]: a for a in original["content"]["artifacts"]}
    after = {a["id"]: a for a in changed["content"]["artifacts"]}
    class_id = repair._PREFIX + ".class.artificer"
    assert {key for key in before if before[key] != after[key]} == {class_id}
    artifact = after[class_id]
    assert selection_contract_errors(artifact) == []
    contract = artifact["card"]["class_definition"]["starting_equipment"]
    assert "starting_equipment" not in artifact["card"]
    assert contract["choices"][0]["count"] == 2
    assert contract["choices"][0]["allow_duplicates"] is True
    assert len(set(contract["choices"][0]["options"])) == 14
    assert contract["gold_alternative"] == {
        "dice": "5d4", "multiplier": 10, "denomination": "gp",
        "replaces_background_equipment": True,
    }
    equipment_ref = next(
        ref for ref in artifact["source_refs"] if ref["chunk_key"] == repair._CHUNK_KEY
    )
    # This archive does not establish an exact page for the equipment chunk.
    assert "page" not in equipment_ref
    assert "starting_equipment" in artifact["selection_contract"]["schema"]["selection_fields"]
    for definition in changed["content"]["rule_definitions"]:
        assert definition["version"] == definition["manifest"]["version"]
    assert report["version"] == repair._PACKAGE_VERSION
    assert output != data
    assert Path(path).read_bytes() == data
