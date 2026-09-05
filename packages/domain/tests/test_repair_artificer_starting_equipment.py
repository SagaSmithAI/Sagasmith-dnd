import copy
import json
import zipfile
from pathlib import Path

import pytest

from sagasmith_dnd.repair_artificer_starting_equipment import (
    ARTIFICER_ID,
    SOURCE_CHUNK_SHA256,
    SOURCE_VERSION,
    TARGET_VERSION,
    repair_artificer_starting_equipment,
)

PACK = Path(
    r"D:/repo/repostew/Sagasmith-dnd-audit-evidence-v4/"
    r"v5-character-completeness/home/artifacts/content-packages/"
    r"a8e07ad0a75f-dnd5e.addon.rulebook.d-d-5e-eberron-rising-from-the-last-war.31293633134f.addon.sagasmith-pack"
)


def package() -> dict:
    with zipfile.ZipFile(PACK) as archive:
        value = json.loads(archive.read("package.sagasmith.json"))
    value["version"] = SOURCE_VERSION
    return value


def test_repair_adds_exact_contract_and_rebinds_metadata() -> None:
    result = repair_artificer_starting_equipment(package())
    artifact = next(x for x in result["content"]["artifacts"] if x["id"] == ARTIFICER_ID)
    contract = artifact["card"]["starting_equipment"]
    assert contract["choices"][0]["count"] == 2
    assert contract["choices"][0]["allow_duplicates"] is True
    assert contract["gold_alternative"] == {
        "dice": "5d4", "multiplier": 10, "denomination": "gp", "replaces_background_equipment": True
    }
    assert result["version"] == TARGET_VERSION
    assert (
        result["metadata"]["local_artificer_starting_equipment_repair"]["source_chunk_sha256"]
        == SOURCE_CHUNK_SHA256
    )
    assert any(SOURCE_CHUNK_SHA256 in ref["chunk_key"] for ref in artifact["source_refs"])


def test_rejects_wrong_binding_without_mutating_input() -> None:
    value = package()
    before = copy.deepcopy(value)
    with pytest.raises(ValueError):
        repair_artificer_starting_equipment(value, source_archive_sha256="0" * 64)
    assert value == before


@pytest.mark.parametrize("field", ["id", "version"])
def test_rejects_wrong_package_identity(field: str) -> None:
    value = package()
    value[field] = "wrong"
    with pytest.raises(ValueError):
        repair_artificer_starting_equipment(value)
