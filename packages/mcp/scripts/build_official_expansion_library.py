"""Build the shipped official-expansion lock from locally supplied private inputs.

Offline, exact-hash repair composition. No downloads, uploads, save migration,
automatic activation, or overwriting existing directories. Partial outputs on
failure are retained for diagnosis and must not be used as an activated library.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import repair_artificer_asi
import repair_artificer_context
import repair_artificer_starting_equipment
import repair_steel_defender_citation
import repair_steel_defender_owner_binding
import repair_subclass_grants
from sagasmith_core.content_pack import loads_content_archive
from sagasmith_dnd.official_expansions import (
    CONTENT_LIBRARY_INDEX_SCHEMA,
    load_official_expansion_lock,
    verify_official_expansion_library,
)

_STEPS = {
    "subclass_grants": repair_subclass_grants.repair_archive,
    "artificer_asi": repair_artificer_asi.repair_archive,
    "artificer_context": repair_artificer_context.repair_archive,
    "steel_defender_citation": repair_steel_defender_citation.repair_archive,
    "steel_defender_owner_binding": repair_steel_defender_owner_binding.repair_archive,
    "artificer_starting_equipment": repair_artificer_starting_equipment.repair_archive,
}


def _source_path(root: Path, entry: dict) -> Path:
    path = (root / entry["path"]).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError("source archive path must be a file inside the supplied library")
    return path


def _repair(data: bytes, target: dict) -> tuple[bytes, list[dict]]:
    recipe = target.get("local_repair")
    source_sha = recipe["source_archive_sha256"] if recipe else target["archive_sha256"]
    if hashlib.sha256(data).hexdigest() != source_sha:
        raise ValueError("source archive does not match the shipped repair input")
    steps = recipe["steps"] if recipe else []
    if recipe and (not isinstance(steps, list) or not steps):
        raise ValueError("local repair steps must be a nonempty list")
    reports = []
    for name in steps:
        if not isinstance(name, str) or name not in _STEPS:
            raise ValueError("unknown local repair step")
        data, report = _STEPS[name](data)
        reports.append({"step": name, **report})
    if hashlib.sha256(data).hexdigest() != target["archive_sha256"]:
        raise ValueError("composed archive does not match the shipped target lock")
    return data, reports


def _write_new(path: Path, data: bytes):
    with path.open("xb") as stream:
        stream.write(data)


def build_library(source: Path, output: Path) -> dict:
    source = source.resolve(strict=True)
    if output.exists() or output.is_symlink():
        raise ValueError("output must be a new library directory")
    output = output.resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("source and output libraries must not overlap")
    lock = load_official_expansion_lock()
    index = json.loads((source / "index.json").read_text(encoding="utf-8"))
    if index.get("schema") != CONTENT_LIBRARY_INDEX_SCHEMA:
        raise ValueError("source library uses an unsupported index schema")
    # Check every input before creating any output. Do not trust index paths or
    # checksums as authority: the shipped lock binds the required input digest.
    inputs = []
    for target in [*lock["packages"], *lock["support_packages"]]:
        matches = [entry for entry in index["packages"] if entry["id"] == target["id"]]
        if len(matches) != 1:
            raise ValueError("source index must contain exactly one entry per locked package")
        entry = matches[0]
        path = _source_path(source, entry)
        expected = target.get("local_repair", {}).get(
            "source_archive_sha256", target["archive_sha256"]
        )
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("source archive does not match the shipped repair input")
        inputs.append((target, entry, path))
    output.mkdir(parents=True, exist_ok=False)
    (output / "packages").mkdir()
    new_index = {
        "schema": CONTENT_LIBRARY_INDEX_SCHEMA,
        "generated_on": lock["generated_on"],
        "packages": [],
    }
    repairs = []
    for target, entry, path in inputs:
        # Recheck on read; preflight is not an authorization cache.
        data, reports = _repair(path.read_bytes(), target)
        package, _ = loads_content_archive(data)
        if any(package[field] != target[field] for field in ("id", "version", "checksum")):
            raise ValueError("composed package identity differs from the shipped lock")
        filename = f"{package['checksum'][:12]}-{package['id']}-{package['version']}.sagasmith-pack"
        destination = output / "packages" / filename
        if destination.resolve().parent != (output / "packages").resolve():
            raise ValueError("invalid output archive filename")
        replacement = deepcopy(entry)
        replacement.update(
            version=package["version"],
            checksum=package["checksum"],
            archive_sha256=target["archive_sha256"],
            archive_size=len(data),
            path="packages/" + filename,
            provided_rule_definitions=[
                {"id": d["id"], "version": d["version"], "checksum": d["definition_checksum"]}
                for d in package["content"]["rule_definitions"]
            ],
        )
        _write_new(destination, data)
        new_index["packages"].append(replacement)
        repairs.extend(reports)
    _write_new(output / "index.json", (json.dumps(new_index, indent=2) + "\n").encode())
    _write_new(
        output / "official-expansions.lock.json", (json.dumps(lock, indent=2) + "\n").encode()
    )
    verified = verify_official_expansion_library(output)
    report = {
        "published": False,
        "activated": False,
        "source_archives_changed": False,
        "source_repository": lock["source_repository"],
        "source_commit": lock["source_commit"],
        "source_commit_role": lock["source_commit_role"],
        "repairs": repairs,
        "verification": verified,
    }
    # Written only after the default, shipped-lock verification succeeds.
    _write_new(output / "repair-report.json", (json.dumps(report, indent=2) + "\n").encode())
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-library", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(build_library(args.source_library, args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
