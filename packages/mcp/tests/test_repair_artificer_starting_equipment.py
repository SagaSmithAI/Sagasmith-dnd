import hashlib
import importlib.util
import os
from pathlib import Path

import pytest

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
    assert report["version"] == repair._PACKAGE_VERSION
    assert output != data
    assert Path(path).read_bytes() == data
