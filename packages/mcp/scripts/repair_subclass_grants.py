"""Reproduce reviewed subclass-spell repairs from three exact private archives.

No source prose or archives are distributed with this script. It writes a new
archive only; it does not migrate saves, activate content, or publish anything.
The output identities are pinned prerequisites of the Artificer repair chain.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from sagasmith_core.content_pack import (
    build_content_package,
    dumps_content_archive,
    loads_content_archive,
)
from sagasmith_dnd.content_packages import (
    content_definition_checksum,
    validate_dnd_content_package,
)
from sagasmith_dnd.content_validation import (
    build_catalog_review,
    build_selection_contract,
    catalog_review_errors,
    content_fingerprint,
    subclass_spell_grant_errors,
)

_RECIPES = {
    "19ae3b071dfd25f0133c6159190e62fe4ae480299611e8ee8893106053c5038b": {
        "version": "1.0.3-local.subclass-grants.1",
        "count": 3,
        "output_sha256": "2289aa6affedfbd4fd9cc8da13845cd468b86df0172c5c235c5b69763200c3e0",
    },
    "f5f45016482354c213f7a3c84fb68f7db96b1da890e35da3e428cd9d579dd15a": {
        "version": "1.0.3-local.subclass-grants.1",
        "count": 2,
        "output_sha256": "f454eedba5a4b75d28c4a4ae28f37e0686047505226b314c4bcc3b30a5052b97",
    },
    "1bb977b1a54f9aeb6f31413d7e8918294f2f38a353c48597287ed6ef3d111d2a": {
        "version": "1.0.5-local.subclass-grants.1",
        "count": 11,
        "output_sha256": "99de36e2c12f1e73993ba0e160456e6a6ae082a654255f38f2ec7074525c6b23",
    },
}
_SECTIONS = {
    "alchemist": (364, 365),
    "artillerist": (373, 374),
    "battle-smith": (387, 388),
    "circle-of-spores": (184, 185),
    "order-domain": (174,),
    "arcana-domain": (472,),
    "oath-of-the-crown": (528, 529),
}


def _one(values, label):
    values = list(values)
    if len(values) != 1:
        raise ValueError(f"expected exactly one {label}")
    return values[0]


def _canonical_card(original):
    card = deepcopy(original)
    legacy = card.pop("always_prepared_spells")
    if not isinstance(legacy, list):
        raise ValueError("legacy spell grants must be a list")
    grants = deepcopy(card.get("spell_grants") or [])
    for item in legacy:
        if not isinstance(item, dict) or set(item) != {"name", "minimum_level"}:
            raise ValueError("unexpected legacy spell grant fields")
        if not isinstance(item["name"], str) or not item["name"].strip():
            raise ValueError("invalid legacy spell name")
        converted = {**item, "method": "always_prepared"}
        matches = [g for g in grants if g["name"].casefold() == item["name"].casefold()]
        if matches:
            if matches != [converted]:
                raise ValueError("conflicting spell access modes or levels")
        else:
            grants.append(converted)
    card["spell_grants"] = grants
    errors = subclass_spell_grant_errors(card)
    if errors:
        raise ValueError("invalid canonical spell grants: " + "; ".join(errors))
    return card


def _attach_evidence(artifact, package, blobs):
    """Section identities are bound by the exact whole-archive input digest."""
    evidence = []
    slug = artifact["id"].rsplit(".subclass.", 1)[1]
    for ordinal in _SECTIONS.get(slug, ()):
        source_key = artifact["source_refs"][0]["source_key"]
        source = _one((s for s in package["sources"] if s["source_key"] == source_key), "source")
        section = _one((s for s in source["sections"] if s["ordinal"] == ordinal), "section")
        asset = _one(
            (
                a
                for a in package["assets"]
                if a["asset_key"] == source["normalized_document_asset_key"]
            ),
            "normalized source asset",
        )
        raw = blobs[asset["checksum"]]
        if hashlib.sha256(raw).hexdigest() != asset["checksum"]:
            raise ValueError("normalized source asset checksum mismatch")
        text = raw.decode("utf-8")[section["start_offset"] : section["end_offset"]]
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != section["content_hash"]:
            raise ValueError("source section checksum mismatch")
        if not section["chunks"]:
            raise ValueError("reviewed spell section has no chunks")
        for chunk in section["chunks"]:
            ref = f"rule-source:{source_key}#chunk:{chunk['key']}"
            if ref not in artifact["rule_refs"]:
                artifact["rule_refs"].append(ref)
            if not any(r.get("chunk_key") == chunk["key"] for r in artifact["source_refs"]):
                artifact["source_refs"].append(
                    {
                        "source_key": source_key,
                        "chunk_key": chunk["key"],
                        "page": chunk["page_start"],
                        "note": "Subclass spell clause/table evidence",
                    }
                )
            evidence.append(
                {
                    "section": ordinal,
                    "content_hash": section["content_hash"],
                    "chunk_key": chunk["key"],
                }
            )
    return evidence


def repair_archive(data: bytes) -> tuple[bytes, dict]:
    source_sha = hashlib.sha256(data).hexdigest()
    recipe = _RECIPES.get(source_sha)
    if recipe is None:
        raise ValueError("archive is not an exact reviewed subclass repair input")
    # Current D&D validation deliberately rejects the legacy input contract.
    # Generic archive integrity, exact input hash and original reviews bind it.
    package, blobs = loads_content_archive(data)
    if package["metadata"].get("distribution") != "private":
        raise ValueError("subclass repair requires a private input package")
    corrected = deepcopy(package)
    changes = []
    for artifact in corrected["content"]["artifacts"]:
        if artifact["kind"] != "subclass" or "always_prepared_spells" not in artifact["card"]:
            continue
        original_hash = content_fingerprint(artifact)
        contract = artifact["selection_contract"]
        if (
            catalog_review_errors(artifact)
            or contract["status"] != "ready"
            or contract["reviewed_content_hash"] != original_hash
        ):
            raise ValueError("legacy subclass review is not bound to the original artifact")
        artifact["card"] = _canonical_card(artifact["card"])
        evidence = _attach_evidence(artifact, corrected, blobs)
        artifact["selection_contract"] = build_selection_contract(
            artifact,
            status="ready",
            references=list(dict.fromkeys([*contract["references"], *artifact["rule_refs"]])),
        )
        artifact["catalog_review"] = build_catalog_review(
            artifact,
            decisions=[
                {
                    "role": "primary",
                    "reviewer": "local source-repair QA",
                    "method": "agent",
                    "checks": {
                        "identity": True,
                        "classification": True,
                        "entry_boundary": True,
                        "references": True,
                    },
                    "notes": "Local provisional review: canonical spell access modes, level tables "
                    "and source hashes checked; not public publication approval.",
                }
            ],
        )
        changes.append(
            {
                "artifact_id": artifact["id"],
                "source_content_hash": original_hash,
                "evidence": evidence,
            }
        )
    if len(changes) != recipe["count"]:
        raise ValueError("unexpected number of reviewed subclass repairs")
    for definition in corrected["content"]["rule_definitions"]:
        definition["version"] = "1.0.1-local.subclass-grants.1"
        definition["manifest"]["version"] = definition["version"]
        definition["definition_checksum"] = content_definition_checksum(
            manifest=definition["manifest"],
            artifacts=[
                a
                for a in corrected["content"]["artifacts"]
                if a.get("rule_definition_id") == definition["id"]
            ],
            mechanics=[
                a
                for a in corrected["content"]["mechanics"]
                if a.get("rule_definition_id") == definition["id"]
            ],
        )
    corrected["metadata"]["local_subclass_spell_repair"] = {
        "source_version": package["version"],
        "source_checksum": package["checksum"],
        "provisional": True,
        "changes": changes,
    }
    rebuilt = build_content_package(
        kind=corrected["kind"],
        package_id=corrected["id"],
        version=recipe["version"],
        system_id=corrected["system_id"],
        manifest=corrected["manifest"],
        dependencies=corrected["dependencies"],
        sources=corrected["sources"],
        assets=corrected["assets"],
        content_reviews=corrected["content_reviews"],
        actors=corrected["actors"],
        content=corrected["content"],
        metadata=corrected["metadata"],
    )
    validate_dnd_content_package(rebuilt)
    result = dumps_content_archive(rebuilt, blobs)
    checked, checked_blobs = loads_content_archive(result)
    if checked != rebuilt or checked_blobs != blobs:
        raise ValueError("repair did not round-trip without source changes")
    result_sha = hashlib.sha256(result).hexdigest()
    if result_sha != recipe["output_sha256"]:
        raise ValueError("repair differs from the reviewed immutable output")
    return result, {
        "id": rebuilt["id"],
        "version": rebuilt["version"],
        "checksum": rebuilt["checksum"],
        "archive_sha256": result_sha,
        "source_archive_sha256": source_sha,
        "artifact_count": len(changes),
        "published": False,
        "source_assets_changed": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("output must be a new archive path")
    result, report = repair_archive(args.archive.read_bytes())
    with args.output.open("xb") as stream:
        stream.write(result)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
