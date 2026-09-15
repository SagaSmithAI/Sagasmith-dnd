"""Implementation identity separate from rules-content fingerprints."""

import hashlib
import json
from functools import lru_cache
from pathlib import Path

import sagasmith_core
import sagasmith_dnd


def source_digest(roots: dict[str, Path]) -> str:
    digest = hashlib.sha256()
    for label, root in sorted(roots.items()):
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            name = f"{label}/{path.relative_to(root).as_posix()}".encode()
            content = path.read_bytes().replace(b"\r\n", b"\n")
            digest.update(len(name).to_bytes(8, "big") + name)
            digest.update(len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def runtime_build_digest() -> str:
    return source_digest(
        {
            "domain": Path(sagasmith_dnd.__file__).parent,
            "runtime": Path(__file__).parent,
            "core": Path(sagasmith_core.__file__).parent,
        }
    )


def implementation_identity() -> dict[str, str | int]:
    return {"runtime_build_digest": runtime_build_digest(), "state_schema_version": 9}


def require_compatible_build(identity: dict) -> None:
    if identity != implementation_identity():
        raise ValueError(
            "runtime implementation build is unavailable; historical data is read-only "
            "until an explicit rule-profile upgrade is performed"
        )


def identity_json() -> str:
    return json.dumps(implementation_identity(), sort_keys=True, separators=(",", ":"))
