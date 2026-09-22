"""Repair exact local Tortle clause/provider metadata without publishing content."""

from __future__ import annotations

import argparse
import hashlib
from copy import deepcopy
from pathlib import Path

from sagasmith_core.content_pack import (
    build_content_package,
    dumps_content_archive,
    loads_content_archive,
)
from sagasmith_dnd.content_import import validate_selection_ready_artifacts
from sagasmith_dnd.content_packages import content_definition_checksum, validate_dnd_content_package
from sagasmith_dnd.content_validation import build_catalog_review, build_selection_contract
from sagasmith_dnd.core_rule_pack import get_core_rule_pack

_SOURCE_SHA = "37c46087480dc91db14f65b746d994c8b549bc1b48b26f35c25e6a88d7207c02"
_SOURCE_CHECKSUM = "b5010b3860ee25ea80aa0ae703644e985ddc418ef81e26f658cceb8e12b3872e"
_PREFIX = "dnd5e.addon.rulebook.d-d-5e-the-tortle-package.e3234de670da"
_PACKAGE_VERSION = "1.0.3-local.clause-coverage.1"
_DEFINITION_VERSION = "1.0.2"
_MECHANICS = ["dnd5e.core.ac.tortle_natural_armor", "dnd5e.core.activity.tortle_shell_defense"]


def repair_archive(data: bytes) -> tuple[bytes, dict]:
    if hashlib.sha256(data).hexdigest() != _SOURCE_SHA:
        raise ValueError("archive is not the exact reviewed Tortle input")
    package, blobs = loads_content_archive(data)
    validate_dnd_content_package(package)
    if (package["id"] != _PREFIX + ".addon" or package["version"] != "1.0.2"
            or package["checksum"] != _SOURCE_CHECKSUM
            or package["metadata"].get("distribution") != "private"):
        raise ValueError("Tortle source identity/version/distribution mismatch")
    corrected = deepcopy(package)
    artifacts = corrected["content"]["artifacts"]
    species = next(a for a in artifacts if a["id"] == _PREFIX + ".species.tortle")
    clauses = species["rule_clauses"]
    if (species["mechanic_refs"] != _MECHANICS or len(clauses) != 1
            or clauses[0]["settlement"]["mode"] != "agent_ruling"
            or len(clauses[0]["source_citations"]) != 2):
        raise ValueError("Tortle source clause shape changed")
    citations = deepcopy(clauses[0]["source_citations"])
    # The existing exact source spans contain both traits. Keep those citations
    # unchanged and retain Agent adjudication for the remaining species text.
    for title, mechanic in zip(("Natural Armor", "Shell Defense"), _MECHANICS, strict=True):
        clauses.append({
            "schema_version": 1, "id": "tortle-" + title.lower().replace(" ", "-"),
            "title": title, "scope": "mechanical", "source_citations": deepcopy(citations),
            "settlement": {"mode": "kernel_mechanic", "mechanic_refs": [mechanic]},
        })
    clauses[0]["settlement"]["reason"] = (
        "Remaining source-specific Tortle clauses retain Agent adjudication. "
        "Natural Armor and Shell Defense are covered by their explicit kernel clauses."
    )
    selection = species["selection_contract"]
    species["selection_contract"] = build_selection_contract(
        species, status=selection["status"], materializer=selection["materializer"],
        references=selection["references"], blockers=selection["blockers"],
    )
    species["catalog_review"] = build_catalog_review(
        species, decisions=deepcopy(species["catalog_review"]["decisions"]), status="approved",
    )
    errors = validate_selection_ready_artifacts(artifacts)
    if errors:
        raise ValueError("; ".join(errors))
    definition = next(d for d in corrected["content"]["rule_definitions"] if d["id"] == _PREFIX)
    definition["version"] = definition["manifest"]["version"] = _DEFINITION_VERSION
    manifest = definition["manifest"]
    manifest["native_mechanic_refs"] = list(_MECHANICS)
    core = get_core_rule_pack("2014")
    manifest["native_provider_locks"] = [{
        "id": core.id, "version": core.version, "edition": core.edition,
        "fingerprint": core.fingerprint, "mechanic_refs": list(_MECHANICS),
    }]
    manifest["resolution_policy"] = "compiled_or_agent"
    manifest.pop("resolution_readiness", None)
    definition["definition_checksum"] = content_definition_checksum(
        manifest=manifest, artifacts=[a for a in artifacts if a["rule_definition_id"] == _PREFIX],
        mechanics=[m for m in corrected["content"]["mechanics"]
                   if m.get("rule_definition_id") == _PREFIX],
    )
    corrected["metadata"]["local_tortle_clause_repair"] = {
        "source_archive_sha256": _SOURCE_SHA, "published": False,
        "changed_artifacts": [species["id"]], "source_blobs_changed": False,
    }
    rebuilt = build_content_package(
        kind=corrected["kind"], package_id=corrected["id"], version=_PACKAGE_VERSION,
        system_id=corrected["system_id"], manifest=corrected["manifest"],
        dependencies=corrected["dependencies"], sources=corrected["sources"],
        assets=corrected["assets"], content_reviews=corrected["content_reviews"],
        actors=corrected["actors"], content=corrected["content"], metadata=corrected["metadata"],
    )
    validate_dnd_content_package(rebuilt)
    output = dumps_content_archive(rebuilt, blobs)
    checked, checked_blobs = loads_content_archive(output)
    if checked != rebuilt or checked_blobs != blobs:
        raise ValueError("Tortle repair changed source blobs or archive roundtrip")
    return output, {
        "id": rebuilt["id"], "version": rebuilt["version"], "checksum": rebuilt["checksum"],
        "archive_sha256": hashlib.sha256(output).hexdigest(), "archive_size": len(output),
        "source_archive_sha256": _SOURCE_SHA, "published": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    data, report = repair_archive(args.archive.read_bytes())
    with args.output.open("xb") as stream:
        stream.write(data)
    print(report)


if __name__ == "__main__":
    main()
