import subprocess
import sys

import pytest
from sagasmith_dnd_runtime.build_identity import (
    implementation_identity,
    require_compatible_build,
    source_digest,
)


def test_implementation_change_affects_digest_without_registry_changes(tmp_path):
    source = tmp_path / "rule.py"
    source.write_bytes(b"def rule():\n    return 1\n")
    first = source_digest({"domain": tmp_path})
    source.write_bytes(b"def rule():\r\n    return 1\r\n")
    assert source_digest({"domain": tmp_path}) == first
    source.write_bytes(b"def rule():\n    return 2\n")
    assert source_digest({"domain": tmp_path}) != first


def test_unavailable_build_requires_explicit_upgrade():
    require_compatible_build(implementation_identity())
    with pytest.raises(ValueError, match="explicit"):
        require_compatible_build({"runtime_build_digest": "old", "state_schema_version": 9})


def test_first_request_uses_import_time_build_identity():
    # A fresh process avoids another test priming the old lazy first-call cache.
    subprocess.run(
        [sys.executable, "-c", "\n".join([
            "import sagasmith_dnd_runtime.build_identity as identity",
            "identity.source_digest = lambda roots: 'later-workspace-edit'",
            "assert identity.runtime_build_digest() != 'later-workspace-edit'",
            "assert len(identity.runtime_build_digest()) == 64",
        ])],
        check=True,
        capture_output=True,
        text=True,
    )
