"""Repair two exact private Eberron infusion cards without changing their mechanics.

Keep the historical input and source blobs intact. This is source-card framing,
not an infusion implementation, printing upgrade, publication or save migration.
"""

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

_SOURCE_KEY = "user.rulebook.d-d-5e-eberron-rising-from-the-last-war.3129363313"
_PREFIX = "dnd5e.addon.rulebook.d-d-5e-eberron-rising-from-the-last-war.31293633134f"
_SOURCE_SHA = "1196e53ae9c706bfdb80bf0576fca0f8b0dcf0c5915790f58745d91b54281942"
_ASSET_SHA = "38daf35316b56176b2a516aa09174c76b67bd0d905b831c12e8868d04781782c"
_PACKAGE_VERSION = "1.0.8-local.infusion-source.2"
_DEFINITION_VERSION = "1.0.6-local.infusion-source.2"
_FOCUS_HEADING = "ENHANCED ARCANE Focus"
_FOURTEENTH_HEADING = "R E PLICABLE ITE M S (14T H - LEVEL ART I F I CER)"
# Titles are metadata outside the section body's hash. The archive digest binds
# both; verify them explicitly before restoring table headings to the card.
_SECTIONS = {
    404: (
        "BOOTS OF THE WINDING PATH",
        431,
        "bdfa6d55307b21c41367f319972e482c0fc56995bb9032b2dcc2ad4d1daa1828",
    ),
    416: (
        "REPLICATE MAGIC ITEM",
        443,
        "e0f1e5ac67b1c877b0360c38bcebc827e276d56aa4d2ae712adf2703fb87212d",
    ),
    417: (
        "REPLICABLE ITE M S (2N D-LEVEL ARTIFI CER)",
        444,
        "df7a30fd31c83cc6eab924d9e7a8b3cd83efe3ddc46ec9614fc544a59c4dcdfc",
    ),
    418: (
        "REPLICABLE ITE M S (6TH-LEVEL ARTIFICER)",
        445,
        "f86d78740822fcb332b319b3ccc7d211c4617f18c2109e586f131cbc422ed45a",
    ),
    419: (
        "REPLICABLE ITEM S (lOT H-lEVEL ARTI FICER)",
        446,
        "693c0d3b0fe78c7655d33e7449dcac26bfb25637f2a471cdc48f359b8e718dc7",
    ),
}


def _one(values, label):
    values = list(values)
    if len(values) != 1:
        raise ValueError(f"expected exactly one {label}")
    return values[0]


def _source_sections(package, blobs):
    source = _one((s for s in package["sources"] if s["source_key"] == _SOURCE_KEY), "source")
    asset = _one(
        (a for a in package["assets"] if a["asset_key"] == source["normalized_document_asset_key"]),
        "normalized asset",
    )
    raw = blobs.get(_ASSET_SHA)
    if (
        asset["checksum"] != _ASSET_SHA
        or raw is None
        or hashlib.sha256(raw).hexdigest() != _ASSET_SHA
    ):
        raise ValueError("normalized source asset checksum mismatch")
    document = raw.decode("utf-8")
    result = {}
    for ordinal, (title, chunk_ordinal, digest) in _SECTIONS.items():
        section = _one((s for s in source["sections"] if s["ordinal"] == ordinal), "source section")
        text = document[section["start_offset"] : section["end_offset"]]
        chunk = _one(section["chunks"], "source section chunk")
        key = f"{_SOURCE_KEY}/section-{ordinal}/chunk-{chunk_ordinal}-{digest[:16]}"
        if (
            section["title"] != title
            or section["content_hash"] != digest
            or hashlib.sha256(text.encode()).hexdigest() != digest
            or chunk["key"] != key
            or chunk["content_hash"] != digest
            or document[chunk["start_offset"] : chunk["end_offset"]] != text
        ):
            raise ValueError("reviewed infusion section/title/chunk mismatch")
        result[ordinal] = {"title": title, "text": text, "chunk_key": key}
    return result


def _rewrite_card(
    artifact,
    sections,
    *,
    name,
    old_description,
    description,
    excerpts,
    additional_sections=(),
):
    """Repair source text and references after checking the exact historical shape."""
    card = artifact["card"]
    if (
        artifact["kind"] != "feature"
        or card["name"] != name
        or card.get("minimum_level") != 2
        or card["description"] != old_description
        or [r["chunk_key"] for r in artifact["source_refs"]] != [s["chunk_key"] for s in sections]
    ):
        raise ValueError("reviewed infusion card boundary/identity mismatch")
    ruling = _one(card["ruling_requirements"], "Agent ruling requirement")
    clause = _one(artifact["rule_clauses"], "Agent rule clause")
    citation = _one(clause["source_citations"], "historical source citation")
    cited_sections = [*sections, *additional_sections]
    if (
        ruling.get("kind") != "source_bound_import_resolution"
        or ruling.get("default_resolver") != "agent"
        or clause["settlement"].get("mode") != "agent_ruling"
        or ruling["source_excerpt"] != sections[0]["text"]
        or citation.get("source") != f"rule-source:{_SOURCE_KEY}"
        or citation.get("source_ref") != {"chunk_key": sections[0]["chunk_key"]}
        or citation["source_excerpt"] != sections[0]["text"]
        or len(excerpts) != len(cited_sections)
    ):
        raise ValueError("reviewed infusion ruling/citation mismatch")
    # Citations stay exact spans of their own indexed chunk. The description and
    # Agent framing additionally retain titles, which belong to section metadata.
    if any(
        not excerpt or excerpt not in section["text"]
        for section, excerpt in zip(cited_sections, excerpts, strict=True)
    ):
        raise ValueError("infusion citation must remain an exact source span")
    selection = artifact["selection_contract"]
    if additional_sections and (
        len({s["chunk_key"] for s in cited_sections}) != len(cited_sections)
        or artifact["rule_refs"]
        != [f"rule-source:{_SOURCE_KEY}#chunk:{s['chunk_key']}" for s in sections]
        or selection["references"] != [f"rule-source-chunk:{s['chunk_key']}" for s in sections]
        or any(r.get("source_key") != _SOURCE_KEY for r in artifact["source_refs"])
    ):
        raise ValueError("reviewed infusion reference extension mismatch")
    card["description"] = description
    ruling["source_excerpt"] = description
    clause["source_citations"] = [
        {
            "source": citation["source"],
            "source_ref": {"chunk_key": section["chunk_key"]},
            "source_excerpt": excerpt,
        }
        for section, excerpt in zip(cited_sections, excerpts, strict=True)
    ]
    for section in additional_sections:
        artifact["source_refs"].append(
            {
                "source_key": _SOURCE_KEY,
                "chunk_key": section["chunk_key"],
            }
        )
        artifact["rule_refs"].append(f"rule-source:{_SOURCE_KEY}#chunk:{section['chunk_key']}")
    artifact["selection_contract"] = build_selection_contract(
        artifact,
        status=selection["status"],
        references=[
            *selection["references"],
            *(f"rule-source-chunk:{s['chunk_key']}" for s in additional_sections),
        ],
        blockers=selection["blockers"],
    )
    artifact["catalog_review"] = build_catalog_review(
        artifact,
        decisions=[
            {
                "role": "primary",
                "reviewer": "reviewed-infusion-source-boundaries-v1",
                "method": "agent",
                "checks": {
                    "identity": True,
                    "classification": True,
                    "entry_boundary": True,
                    "references": True,
                },
                "notes": "Exact private source spans and level-table headings reviewed; "
                "level 2 and mechanical fields preserved. Not full-book or gameplay acceptance.",
            }
        ],
    )


def repair_archive(data: bytes) -> tuple[bytes, dict]:
    if hashlib.sha256(data).hexdigest() != _SOURCE_SHA:
        raise ValueError("archive is not the exact reviewed infusion-source input")
    package, blobs = loads_content_archive(data)
    validate_dnd_content_package(package)
    if (
        package["id"] != _PREFIX + ".addon"
        or package["version"] != "1.0.7-local.steel-defender-lifecycle.1"
        or package["metadata"].get("distribution") != "private"
    ):
        raise ValueError("infusion-source input identity/version/distribution mismatch")
    sections = _source_sections(package, blobs)
    corrected = deepcopy(package)
    artifacts = corrected["content"]["artifacts"]
    focus = _one(
        (a for a in artifacts if a["id"] == _PREFIX + ".feature.enhanced-arcane-focus"),
        "Enhanced Arcane Focus",
    )
    mixed = sections[404]["text"]
    if mixed.count(_FOCUS_HEADING) != 1:
        raise ValueError("reviewed embedded focus heading must occur exactly once")
    focus_text = mixed[mixed.index(_FOCUS_HEADING) :]
    _rewrite_card(
        focus,
        [sections[404]],
        name="Enhanced Arcane Focus",
        old_description=mixed,
        description=focus_text,
        excerpts=[focus_text],
    )
    replicate = _one(
        (a for a in artifacts if a["id"] == _PREFIX + ".feature.replicate-magic-item"),
        "Replicate Magic Item",
    )
    tables = [sections[416], sections[417], sections[418], sections[419]]
    if tables[-1]["text"].count(_FOURTEENTH_HEADING) != 1:
        raise ValueError("reviewed embedded fourteenth-level table heading mismatch")
    description = "\n\n".join(
        [
            tables[0]["text"],
            *(f"{section['title']}\n{section['text']}" for section in tables[1:]),
        ]
    )
    _rewrite_card(
        replicate,
        tables[:3],
        name="Replicate Magic Item",
        old_description="\n\n".join(s["text"] for s in tables[:3]),
        description=description,
        excerpts=[s["text"] for s in tables],
        additional_sections=tables[3:],
    )
    errors = validate_selection_ready_artifacts([focus, replicate])
    if errors:
        raise ValueError("; ".join(errors))
    definition = _one(
        (d for d in corrected["content"]["rule_definitions"] if d["id"] == _PREFIX),
        "rule definition",
    )
    definition["version"] = definition["manifest"]["version"] = _DEFINITION_VERSION
    definition["definition_checksum"] = content_definition_checksum(
        manifest=definition["manifest"],
        artifacts=[a for a in artifacts if a["rule_definition_id"] == _PREFIX],
        mechanics=[
            m for m in corrected["content"]["mechanics"] if m.get("rule_definition_id") == _PREFIX
        ],
    )
    changed_ids = [focus["id"], replicate["id"]]
    corrected["metadata"]["local_artificer_infusion_source_repair"] = {
        "source_archive_sha256": _SOURCE_SHA,
        "normalized_asset_sha256": _ASSET_SHA,
        "changed_artifacts": changed_ids,
        "published": False,
        "source_sections": [
            {
                "ordinal": ordinal,
                "title": section["title"],
                "chunk_key": section["chunk_key"],
                "content_hash": _SECTIONS[ordinal][2],
            }
            for ordinal, section in sections.items()
        ],
    }
    rebuilt = build_content_package(
        kind=corrected["kind"],
        package_id=corrected["id"],
        version=_PACKAGE_VERSION,
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
    output = dumps_content_archive(rebuilt, blobs)
    checked, checked_blobs = loads_content_archive(output)
    if checked != rebuilt or checked_blobs != blobs:
        raise ValueError("infusion-source repair did not preserve archive roundtrip")
    return output, {
        "id": rebuilt["id"],
        "version": rebuilt["version"],
        "checksum": rebuilt["checksum"],
        "archive_sha256": hashlib.sha256(output).hexdigest(),
        "source_archive_sha256": _SOURCE_SHA,
        "changed_artifacts": changed_ids,
        "published": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        raise ValueError("output must be a new archive path")
    data, report = repair_archive(args.archive.read_bytes())
    with args.output.open("xb") as stream:
        stream.write(data)
    print(report)


if __name__ == "__main__":
    raise SystemExit(main())
