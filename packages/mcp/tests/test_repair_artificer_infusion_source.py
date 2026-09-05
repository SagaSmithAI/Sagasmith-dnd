"""Synthetic framing checks and opt-in exact private source acceptance."""

import hashlib
import importlib.util
import os
from copy import deepcopy
from pathlib import Path

import pytest
from sagasmith_core.content_pack import loads_content_archive
from sagasmith_dnd.content_validation import (
    build_catalog_review,
    build_selection_contract,
    catalog_review_errors,
    selection_contract_errors,
)

spec = importlib.util.spec_from_file_location(
    "repair_artificer_infusion_source",
    Path(__file__).parents[1] / "scripts/repair_artificer_infusion_source.py",
)
repair = importlib.util.module_from_spec(spec)
spec.loader.exec_module(repair)


def _fixture():
    body = "Synthetic unrelated prefix. Synthetic focus rule."
    section = {"chunk_key": "fixture/chunk", "title": "Synthetic heading", "text": body}
    artifact = {
        "id": "fixture.feature.focus",
        "kind": "feature",
        "application_state": "selection_ready",
        "mechanical_scope": "mechanical",
        "execution_state": "ruling_ready",
        "card": {
            "name": "Synthetic Focus",
            "description": body,
            "class_name": "Artificer",
            "minimum_level": 2,
            "mechanical_grants": {},
            "selection_requirements": {},
            "ruling_requirements": [
                {
                    "kind": "source_bound_import_resolution",
                    "default_resolver": "agent",
                    "source_excerpt": body,
                }
            ],
        },
        "source_refs": [{"source_key": repair._SOURCE_KEY, "chunk_key": section["chunk_key"]}],
        "rule_refs": ["rule-source:fixture"],
        "rule_clauses": [
            {
                "schema_version": 1,
                "id": "fixture-rule",
                "title": "Synthetic Focus",
                "scope": "mechanical",
                "settlement": {"mode": "agent_ruling"},
                "source_citations": [
                    {
                        "source": f"rule-source:{repair._SOURCE_KEY}",
                        "source_ref": {"chunk_key": section["chunk_key"]},
                        "source_excerpt": body,
                    }
                ],
            }
        ],
    }
    artifact["selection_contract"] = build_selection_contract(
        artifact,
        status="ready",
        references=artifact["rule_refs"],
    )
    artifact["catalog_review"] = build_catalog_review(
        artifact,
        decisions=[
            {
                "role": "primary",
                "reviewer": "synthetic test",
                "method": "deterministic",
                "checks": {
                    "identity": True,
                    "classification": True,
                    "entry_boundary": True,
                    "references": True,
                },
                "notes": "Synthetic fixture.",
            }
        ],
    )
    return artifact, section


def _rewrite(artifact, section, excerpt="Synthetic focus rule."):
    repair._rewrite_card(
        artifact,
        [section],
        name="Synthetic Focus",
        old_description=section["text"],
        description=excerpt,
        excerpts=[excerpt],
    )


def test_synthetic_framing_preserves_level_and_rebinds_all_evidence():
    artifact, section = _fixture()
    before = deepcopy(artifact)
    _rewrite(artifact, section)
    assert artifact["card"]["minimum_level"] == 2
    assert artifact["card"]["description"] == "Synthetic focus rule."
    assert artifact["card"]["ruling_requirements"][0]["source_excerpt"] == "Synthetic focus rule."
    assert (
        artifact["rule_clauses"][0]["source_citations"][0]["source_excerpt"]
        == "Synthetic focus rule."
    )
    assert artifact["source_refs"] == before["source_refs"]
    assert artifact["rule_refs"] == before["rule_refs"]
    assert selection_contract_errors(artifact) == catalog_review_errors(artifact) == []
    assert artifact["selection_contract"]["schema"] == before["selection_contract"]["schema"]
    assert (
        artifact["selection_contract"]["reviewed_content_hash"]
        != before["selection_contract"]["reviewed_content_hash"]
    )


@pytest.mark.parametrize("invalid_reference", [False, True])
def test_missing_source_extension_preserves_old_refs_and_requires_exact_binding(invalid_reference):
    artifact, section = _fixture()
    artifact["rule_refs"] = [f"rule-source:{repair._SOURCE_KEY}#chunk:{section['chunk_key']}"]
    artifact["selection_contract"] = build_selection_contract(
        artifact,
        status="ready",
        references=[f"rule-source-chunk:{section['chunk_key']}"],
    )
    extra = {
        "chunk_key": "fixture/later-chunk",
        "title": "Synthetic higher level",
        "text": "Synthetic later source table.",
    }
    if invalid_reference:
        artifact["selection_contract"]["references"] = ["unrelated"]
    before = deepcopy(artifact)
    description = section["text"] + "\n\n" + extra["title"] + "\n" + extra["text"]

    def rewrite():
        repair._rewrite_card(
            artifact,
            [section],
            name="Synthetic Focus",
            old_description=section["text"],
            description=description,
            excerpts=[section["text"], extra["text"]],
            additional_sections=[extra],
        )

    if invalid_reference:
        with pytest.raises(ValueError, match="reference extension mismatch"):
            rewrite()
        assert artifact == before
        return
    rewrite()
    assert artifact["source_refs"] == [
        *before["source_refs"],
        {
            "source_key": repair._SOURCE_KEY,
            "chunk_key": extra["chunk_key"],
        },
    ]
    assert artifact["rule_refs"] == [
        *before["rule_refs"],
        f"rule-source:{repair._SOURCE_KEY}#chunk:{extra['chunk_key']}",
    ]
    assert artifact["selection_contract"]["references"] == [
        *before["selection_contract"]["references"],
        f"rule-source-chunk:{extra['chunk_key']}",
    ]
    assert [c["source_excerpt"] for c in artifact["rule_clauses"][0]["source_citations"]] == [
        section["text"],
        extra["text"],
    ]
    assert artifact["card"]["description"] == description
    assert artifact["card"]["ruling_requirements"][0]["source_excerpt"] == description
    assert artifact["selection_contract"]["schema"] == before["selection_contract"]["schema"]
    assert selection_contract_errors(artifact) == catalog_review_errors(artifact) == []


@pytest.mark.parametrize("change", ["level", "description", "ref", "citation", "ruling", "mode"])
def test_unexpected_card_or_citation_fails_without_partial_rewrite(change):
    artifact, section = _fixture()
    if change == "level":
        artifact["card"]["minimum_level"] = 6
    elif change == "description":
        artifact["card"]["description"] += " altered"
    elif change == "ref":
        artifact["source_refs"][0]["chunk_key"] = "other/chunk"
    elif change == "citation":
        artifact["rule_clauses"][0]["source_citations"][0]["source_ref"] = {"chunk_key": "other"}
    elif change == "ruling":
        artifact["card"]["ruling_requirements"][0]["source_excerpt"] += " altered"
    else:
        artifact["rule_clauses"][0]["settlement"]["mode"] = "mechanic"
    before = deepcopy(artifact)
    with pytest.raises(ValueError, match="reviewed infusion"):
        _rewrite(artifact, section)
    assert artifact == before


def test_replacement_cannot_invent_a_source_excerpt():
    artifact, section = _fixture()
    before = deepcopy(artifact)
    with pytest.raises(ValueError, match="exact source span"):
        _rewrite(artifact, section, "Invented mechanics not in the source.")
    assert artifact == before


@pytest.mark.parametrize("change", [None, "blob", "title", "chunk", "offset"])
def test_source_body_and_metadata_are_both_verified(monkeypatch, change):
    body = "Synthetic table with a bounded entry."
    digest = hashlib.sha256(body.encode()).hexdigest()
    key = f"{repair._SOURCE_KEY}/section-1/chunk-2-{digest[:16]}"
    monkeypatch.setattr(repair, "_ASSET_SHA", digest)
    monkeypatch.setattr(repair, "_SECTIONS", {1: ("Synthetic level table", 2, digest)})
    section = {
        "ordinal": 1,
        "title": "Synthetic level table",
        "content_hash": digest,
        "start_offset": 0,
        "end_offset": len(body),
        "chunks": [
            {"key": key, "content_hash": digest, "start_offset": 0, "end_offset": len(body)}
        ],
    }
    package = {
        "sources": [
            {
                "source_key": repair._SOURCE_KEY,
                "normalized_document_asset_key": "source",
                "sections": [section],
            }
        ],
        "assets": [{"asset_key": "source", "checksum": digest}],
    }
    blobs = {digest: body.encode()}
    if change == "blob":
        blobs[digest] += b" changed"
    elif change == "title":
        section["title"] = "Different level table"
    elif change == "chunk":
        section["chunks"][0]["key"] = "wrong/chunk"
    elif change == "offset":
        section["chunks"][0]["start_offset"] = 1
    if change is None:
        assert repair._source_sections(package, blobs)[1]["text"] == body
    else:
        with pytest.raises(ValueError, match="mismatch"):
            repair._source_sections(package, blobs)


def test_wrong_bytes_and_existing_output_are_not_written(tmp_path):
    source, output = tmp_path / "input.pack", tmp_path / "output.pack"
    source.write_bytes(b"not the exact input")
    with pytest.raises(ValueError, match="exact reviewed"):
        repair.main(["--archive", str(source), "--output", str(output)])
    assert not output.exists()
    with pytest.raises(ValueError, match="new archive path"):
        repair.main(["--archive", str(source), "--output", str(source)])
    assert source.read_bytes() == b"not the exact input"


def test_private_exact_repair_changes_only_two_cards_and_preserves_source():
    raw = os.environ.get("SAGASMITH_ARTIFICER_INFUSION_SOURCE_INPUT")
    if not raw:
        pytest.skip("requires explicitly supplied private exact infusion-source input")
    path = Path(raw)
    data = path.read_bytes()
    original, blobs = loads_content_archive(data)
    before = {a["id"]: a for a in original["content"]["artifacts"]}
    focus_id = repair._PREFIX + ".feature.enhanced-arcane-focus"
    replicate_id = repair._PREFIX + ".feature.replicate-magic-item"
    focus_before, replicate_before = before[focus_id], before[replicate_id]
    assert "boots" in focus_before["card"]["description"].lower()
    assert "teleport" in focus_before["card"]["ruling_requirements"][0]["source_excerpt"].lower()
    sections = repair._source_sections(original, blobs)
    titles = [sections[ordinal]["title"] for ordinal in (417, 418, 419)]
    assert all(title not in replicate_before["card"]["description"] for title in titles)
    assert repair._FOURTEENTH_HEADING not in replicate_before["card"]["description"]
    assert sections[419]["chunk_key"] not in {
        ref["chunk_key"] for ref in replicate_before["source_refs"]
    }
    output, report = repair.repair_archive(data)
    assert repair.repair_archive(data) == (output, report)
    changed, changed_blobs = loads_content_archive(output)
    assert changed_blobs == blobs
    for field in ("sources", "assets", "actors", "dependencies", "content_reviews"):
        assert changed[field] == original[field]
    after = {a["id"]: a for a in changed["content"]["artifacts"]}
    assert before.keys() == after.keys()
    assert {key for key in before if before[key] != after[key]} == {focus_id, replicate_id}
    focus = after[focus_id]
    text = focus["card"]["description"]
    assert text == repair._FOCUS_HEADING + sections[404]["text"].split(repair._FOCUS_HEADING, 1)[1]
    assert "boots" not in text.lower() and "teleport" not in text.lower()
    assert focus["card"]["minimum_level"] == 2
    replicate = after[replicate_id]
    text = replicate["card"]["description"]
    assert text == "\n\n".join(
        [
            sections[416]["text"],
            *(sections[n]["title"] + "\n" + sections[n]["text"] for n in (417, 418, 419)),
        ]
    )
    assert text.index(titles[0]) < text.index(sections[417]["text"]) < text.index(titles[1])
    assert text.index(titles[1]) < text.index(titles[2]) < text.index(repair._FOURTEENTH_HEADING)
    assert text.count(repair._FOURTEENTH_HEADING) == 1
    for identifier in (focus_id, replicate_id):
        artifact, old = after[identifier], before[identifier]
        assert selection_contract_errors(artifact) == catalog_review_errors(artifact) == []
        if identifier == focus_id:
            assert artifact["source_refs"] == old["source_refs"]
            assert artifact["rule_refs"] == old["rule_refs"]
            assert (
                artifact["selection_contract"]["references"]
                == (old["selection_contract"]["references"])
            )
        else:
            added_key = sections[419]["chunk_key"]
            assert artifact["source_refs"] == [
                *old["source_refs"],
                {
                    "source_key": repair._SOURCE_KEY,
                    "chunk_key": added_key,
                },
            ]
            assert artifact["rule_refs"] == [
                *old["rule_refs"],
                f"rule-source:{repair._SOURCE_KEY}#chunk:{added_key}",
            ]
            assert artifact["selection_contract"]["references"] == [
                *old["selection_contract"]["references"],
                f"rule-source-chunk:{added_key}",
            ]
        assert artifact["semantic_resolution"] == old["semantic_resolution"]
        assert (
            artifact["card"]["ruling_requirements"][0]["source_excerpt"]
            == artifact["card"]["description"]
        )
        for key in old["card"].keys() - {"description", "ruling_requirements"}:
            assert artifact["card"][key] == old["card"][key]
        assert artifact["selection_contract"]["schema"] == old["selection_contract"]["schema"]
        for citation in artifact["rule_clauses"][0]["source_citations"]:
            section = next(
                s
                for s in sections.values()
                if s["chunk_key"] == citation["source_ref"]["chunk_key"]
            )
            assert citation["source_excerpt"] in section["text"]
    assert len(replicate["rule_clauses"][0]["source_citations"]) == 4
    assert report["archive_sha256"] == hashlib.sha256(output).hexdigest()
    assert path.read_bytes() == data
    with pytest.raises(ValueError, match="exact reviewed"):
        repair.repair_archive(output)
