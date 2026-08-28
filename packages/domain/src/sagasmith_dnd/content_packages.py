"""D&D builders for the unified SagaSmith content package boundary."""

from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from sagasmith_core.content_pack import (
    blob_descriptor,
    build_actor_card,
    build_content_package,
    build_source_bundle,
    content_package_checksum,
    source_ref,
)
from sagasmith_core.content_pack import (
    validate_content_package as validate_core_content_package,
)

from sagasmith_dnd.character_schema import default_character_notes
from sagasmith_dnd.content_actors import (
    canonicalize_dnd_content_actor,
    validate_dnd_content_actor,
)
from sagasmith_dnd.content_import import repair_reviewed_structured_transcription
from sagasmith_dnd.content_validation import (
    build_catalog_review,
    build_selection_contract,
    catalog_review_errors,
    content_fingerprint,
    selection_contract_errors,
)
from sagasmith_dnd.spatial import BattleMapError, normalize_combat_grid_templates
from sagasmith_dnd.statblocks import ParsedStatblock, finalize_imported_actor_rulings

if TYPE_CHECKING:
    from sagasmith_dnd.portrait_extraction import ExtractedPortrait, PortraitExtractor


def _portrait_extractor_type() -> type[PortraitExtractor]:
    """Load image support only for an explicit portrait extraction request."""

    try:
        from sagasmith_dnd.portrait_extraction import PortraitExtractor
    except ModuleNotFoundError as exc:
        if (exc.name or "").partition(".")[0] != "PIL":
            raise
        raise RuntimeError(
            "Actor portrait extraction requires `pip install \"sagasmith-dnd[images]\"`"
        ) from exc
    return PortraitExtractor


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized or "content"


def _invalid_structured_text_paths(value: Any, *, path: str) -> list[str]:
    invalid: list[str] = []
    if isinstance(value, str):
        for character in value:
            codepoint = ord(character)
            noncharacter = (
                0xFDD0 <= codepoint <= 0xFDEF
                or codepoint & 0xFFFF in {0xFFFE, 0xFFFF}
            )
            category = unicodedata.category(character)
            if (
                character == "\ufffd"
                or noncharacter
                or category in {"Co", "Cs"}
                or (category == "Cc" and character not in {"\t", "\n", "\r"})
            ):
                invalid.append(f"{path}: U+{codepoint:04X}")
                break
        return invalid
    if isinstance(value, Mapping):
        for key, item in value.items():
            invalid.extend(
                _invalid_structured_text_paths(item, path=f"{path}.{key}")
            )
        return invalid
    if isinstance(value, list):
        for index, item in enumerate(value):
            invalid.extend(
                _invalid_structured_text_paths(item, path=f"{path}[{index}]")
            )
    return invalid


def _repair_reviewed_actor_transcription(actor: Mapping[str, Any]) -> dict[str, Any]:
    repaired_actor, repairs = repair_reviewed_structured_transcription(
        actor,
        path="actor",
    )
    if repairs:
        repaired_actor.setdefault("metadata", {}).setdefault(
            "transcription_repairs", []
        ).extend(repairs)
    return repaired_actor


def _refresh_reviewed_content_hashes(artifact: Mapping[str, Any]) -> dict[str, Any]:
    """Bind attestations to the exact final portable artifact form."""

    value = copy.deepcopy(dict(artifact))
    fingerprint = content_fingerprint(value)
    for field in ("catalog_review", "selection_contract"):
        attestation = value.get(field)
        if isinstance(attestation, Mapping):
            value[field] = {
                **copy.deepcopy(dict(attestation)),
                "reviewed_content_hash": fingerprint,
            }
    return value


def validate_dnd_content_package(package: Mapping[str, Any]) -> dict[str, Any]:
    """Validate D&D presentation references layered onto the generic package."""

    value = validate_core_content_package(package)
    if value["system_id"] != "dnd5e":
        return value
    invalid_text: list[str] = []
    stale_attestations: list[str] = []
    for index, artifact in enumerate(value["content"].get("artifacts") or []):
        if isinstance(artifact, Mapping):
            invalid_text.extend(
                _invalid_structured_text_paths(
                    artifact,
                    path=f"content.artifacts[{index}]",
                )
            )
            if artifact.get("catalog_review") is not None:
                stale_attestations.extend(catalog_review_errors(artifact))
            if artifact.get("selection_contract") is not None:
                stale_attestations.extend(selection_contract_errors(artifact))
    for index, actor in enumerate(value.get("actors") or []):
        if isinstance(actor, Mapping):
            invalid_text.extend(
                _invalid_structured_text_paths(
                    actor,
                    path=f"actors[{index}]",
                )
            )
            validate_dnd_content_actor(actor)
    if invalid_text:
        preview = ", ".join(invalid_text[:8])
        suffix = f" (+{len(invalid_text) - 8} more)" if len(invalid_text) > 8 else ""
        raise ValueError(
            "structured D&D cards contain unrepaired PDF transcription characters: "
            f"{preview}{suffix}"
        )
    if stale_attestations:
        preview = "; ".join(stale_attestations[:8])
        suffix = (
            f" (+{len(stale_attestations) - 8} more)"
            if len(stale_attestations) > 8
            else ""
        )
        raise ValueError(f"D&D content attestations are stale: {preview}{suffix}")
    assets = {str(asset["asset_key"]): asset for asset in value["assets"]}
    if value["kind"] == "module":
        finalization = value["metadata"].get("agent_finalization")
        if not isinstance(finalization, Mapping) or set(finalization) != {
            "confirmed",
            "reviewer",
            "note",
        }:
            raise ValueError(
                "module content package metadata.agent_finalization must contain "
                "exactly confirmed, reviewer, and note"
            )
        if finalization["confirmed"] is not True:
            raise ValueError("module content package requires explicit Agent confirmation")
        for field in ("reviewer", "note"):
            text = str(finalization[field] or "").strip()
            if not text or len(text) > 2000:
                raise ValueError(
                    f"module content package metadata.agent_finalization.{field} "
                    "must contain 1 to 2000 characters"
                )
        profile = dict(value["content"].get("play_profile") or {})
        starting_level = dict(profile.get("starting_level") or {})
        expected_end_level = dict(profile.get("expected_end_level") or {})
        advancement = dict(profile.get("advancement") or {})
        pregenerated = dict(profile.get("pregenerated_characters") or {})
        sourced_profile_entries = (
            starting_level,
            expected_end_level,
            advancement,
            pregenerated,
        )
        if (
            starting_level.get("value") is None
            or expected_end_level.get("value") is None
            or advancement.get("recommended") in {None, "unknown"}
            or "unknown" in list(advancement.get("modes") or [])
            or any(
                not list(item.get("source_refs") or [])
                or any(
                    not isinstance(ref, Mapping)
                    for ref in item.get("source_refs") or []
                )
                for item in sourced_profile_entries
            )
        ):
            raise ValueError(
                "finalized module play_profile requires sourced level, advancement, "
                "and pregenerated-character review; party-size advice is optional"
            )
        if (
            value["content"].get("classification") == "campaign"
            and not dict(value["content"].get("narrative") or {}).get("endings")
        ):
            raise ValueError("finalized campaign module requires at least one ending")
        template_ids: set[str] = set()
        for scene_index, scene in enumerate(value["content"].get("scene_atlas") or []):
            metadata = dict(scene.get("metadata") or {})
            profile_data = dict(metadata.get("profile_data") or {})
            if "combat_grid_templates" not in profile_data:
                continue
            try:
                templates = normalize_combat_grid_templates(
                    profile_data["combat_grid_templates"]
                )
            except BattleMapError as exc:
                raise ValueError(
                    f"scene_atlas[{scene_index}] combat_grid_templates is invalid: {exc}"
                ) from exc
            if profile_data["combat_grid_templates"] != templates:
                raise ValueError(
                    f"scene_atlas[{scene_index}] combat_grid_templates is not canonical"
                )
            location_keys = {
                str(item.get("key"))
                for item in dict(metadata.get("spatial") or {}).get("locations", [])
                if isinstance(item, Mapping) and item.get("key")
            }
            for template in templates:
                if template["id"] in template_ids:
                    raise ValueError(
                        "module combat-grid template ids must be unique across the Scene Atlas"
                    )
                template_ids.add(template["id"])
                if template["location_key"] not in location_keys:
                    raise ValueError(
                        "combat-grid template location_key must belong to its Scene Atlas scene"
                    )
                asset_key = template.get("map_asset_key")
                if asset_key:
                    asset = assets.get(str(asset_key))
                    if asset is None or not str(asset.get("media_type") or "").startswith(
                        "image/"
                    ):
                        raise ValueError(
                            "combat-grid template map_asset_key must reference a packaged image"
                        )
                public_asset = template.get("party_public_map_asset")
                if public_asset:
                    asset = assets.get(str(public_asset["asset_key"]))
                    if asset is None or not str(asset.get("media_type") or "").startswith(
                        "image/"
                    ):
                        raise ValueError(
                            "combat-grid template party_public_map_asset must reference a "
                            "packaged image"
                        )
                    if str(asset.get("checksum") or "") != str(public_asset["checksum"]):
                        raise ValueError(
                            "combat-grid template party_public_map_asset checksum does not "
                            "match its packaged image"
                        )
                    if str(asset.get("media_type") or "").casefold() != str(
                        public_asset["media_type"]
                    ).casefold():
                        raise ValueError(
                            "combat-grid template party_public_map_asset media_type does not "
                            "match its packaged image"
                        )
    for artifact in value["content"].get("artifacts") or []:
        if not isinstance(artifact, Mapping) or artifact.get("kind") != "statblock":
            continue
        card = artifact.get("card")
        if not isinstance(card, Mapping) or card.get("image") is None:
            continue
        image = card["image"]
        if not isinstance(image, Mapping) or set(image) != {"asset_key", "alt"}:
            raise ValueError("statblock card image must contain exact asset_key and alt fields")
        asset_key = str(image.get("asset_key") or "")
        alt = str(image.get("alt") or "").strip()
        if not alt or len(alt) > 1000:
            raise ValueError("statblock card image alt must contain 1 to 1000 characters")
        asset = assets.get(asset_key)
        if asset is None or asset["kind"] != "actor_image":
            raise ValueError("statblock card image must reference an actor_image asset")
        def ref_identity(ref: Mapping[str, Any]) -> tuple[str, str, int | None]:
            return (
                str(ref.get("source_key") or ""),
                str(ref.get("chunk_key") or ""),
                int(ref["page"]) if isinstance(ref.get("page"), int) else None,
            )

        artifact_refs = {
            ref_identity(ref)
            for ref in artifact.get("source_refs") or []
            if isinstance(ref, Mapping)
        }
        asset_refs = {
            ref_identity(ref)
            for ref in asset.get("source_refs") or []
            if isinstance(ref, Mapping)
        }
        if not asset_refs or not asset_refs.issubset(artifact_refs):
            raise ValueError(
                "statblock card image evidence must be bound to the card source refs"
            )
    return value


def canonicalize_dnd_content_package(package: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild the exact current D&D package form without changing source evidence."""

    candidate = copy.deepcopy(dict(package))
    if candidate.get("system_id") == "dnd5e" and candidate.get("kind") == "module":
        for scene in dict(candidate.get("content") or {}).get("scene_atlas") or []:
            metadata = dict(scene.get("metadata") or {})
            if metadata.get("visibility") == "keeper":
                metadata["visibility"] = "restricted"
                scene["metadata"] = metadata
        candidate["checksum"] = content_package_checksum(candidate)
    value = validate_core_content_package(candidate)
    if value["system_id"] != "dnd5e":
        return value
    content = copy.deepcopy(dict(value["content"]))
    for scene in content.get("scene_atlas") or []:
        metadata = dict(scene.get("metadata") or {})
        profile_data = dict(metadata.get("profile_data") or {})
        if "combat_grid_templates" in profile_data:
            profile_data["combat_grid_templates"] = normalize_combat_grid_templates(
                profile_data["combat_grid_templates"]
            )
            metadata["profile_data"] = profile_data
            scene["metadata"] = metadata
    artifacts = []
    for raw_artifact in content.get("artifacts") or []:
        artifact = copy.deepcopy(dict(raw_artifact))
        card = dict(artifact.get("card") or {})
        approved_review = dict(artifact.get("catalog_review") or {}).get("status") == (
            "approved"
        )
        serialized = json.dumps(artifact, ensure_ascii=False)
        if approved_review or "\ufffd" not in serialized:
            artifact, repairs = repair_reviewed_structured_transcription(
                artifact,
                path="artifact",
                repair_identity=card.get("source_fragment") is True,
            )
            if repairs:
                card = dict(artifact.get("card") or {})
                card.setdefault("transcription_repairs", []).extend(repairs)
                artifact["card"] = card
        artifacts.append(_refresh_reviewed_content_hashes(artifact))
    content["artifacts"] = artifacts
    mechanics = list(content.get("mechanics") or [])
    definitions = []
    for raw_definition in content.get("rule_definitions") or []:
        definition = copy.deepcopy(dict(raw_definition))
        definition_id = str(definition["id"])
        definition["definition_checksum"] = content_definition_checksum(
            manifest=definition["manifest"],
            artifacts=[
                artifact
                for artifact in artifacts
                if str(artifact.get("rule_definition_id") or "") == definition_id
            ],
            mechanics=[
                mechanic
                for mechanic in mechanics
                if str(mechanic.get("rule_definition_id") or "") == definition_id
            ],
        )
        definitions.append(definition)
    if "rule_definitions" in content:
        content["rule_definitions"] = definitions
    actors = [
        canonicalize_dnd_content_actor(_repair_reviewed_actor_transcription(actor))
        for actor in value.get("actors") or []
    ]
    rebuilt = build_content_package(
        kind=str(value["kind"]),
        package_id=str(value["id"]),
        version=str(value["version"]),
        system_id=str(value["system_id"]),
        manifest=value["manifest"],
        dependencies=value["dependencies"],
        sources=value["sources"],
        assets=value["assets"],
        content_reviews=value["content_reviews"],
        actors=actors,
        content=content,
        metadata=value["metadata"],
    )
    return validate_dnd_content_package(rebuilt)


def _reconstruct_indexed_document(
    source: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]], list[str]]:
    raw_sections = list(source["sections"])
    length = max(int(section["end_offset"]) for section in raw_sections)
    document = [" "] * length
    occupied = [False] * length
    for section in raw_sections:
        start = int(section["start_offset"])
        content = str(section["content"])
        if int(section["end_offset"]) - start != len(content):
            raise ValueError("indexed section offsets do not match content")
        for offset, character in enumerate(content, start):
            if occupied[offset] and document[offset] != character:
                raise ValueError("indexed sections overlap with conflicting content")
            document[offset] = character
            occupied[offset] = True
    normalized = "".join(document)
    sections: list[dict[str, Any]] = []
    old_chunk_keys: list[str] = []
    for section in raw_sections:
        section_start = int(section["start_offset"])
        section_end = int(section["end_offset"])
        cursor = section_start
        chunks = []
        for raw_chunk in section["chunks"]:
            chunk = dict(raw_chunk)
            old_chunk_keys.append(str(chunk["key"]))
            metadata = dict(chunk.get("metadata") or {})
            start = metadata.get("start_offset")
            end = metadata.get("end_offset")
            content = str(chunk["content"])
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or start < section_start
                or end > section_end
            ):
                start = normalized.find(content, cursor, section_end)
                if start < 0:
                    start = normalized.find(content, section_start, section_end)
                if start < 0:
                    raise ValueError("indexed chunk is not contained in its section")
                end = start + len(content)
            cursor = end
            chunks.append(
                {
                    "ordinal": int(chunk["ordinal"]),
                    "heading_path": list(chunk["heading_path"]),
                    "start_offset": start,
                    "end_offset": end,
                    "token_count": int(chunk.get("token_count") or 0),
                    "page_start": metadata.get("page_start"),
                    "page_end": metadata.get("page_end"),
                    "metadata": {
                        key: value
                        for key, value in metadata.items()
                        if key
                        not in {
                            "start_offset",
                            "end_offset",
                            "page_start",
                            "page_end",
                        }
                    },
                }
            )
        sections.append(
            {
                "ordinal": int(section["ordinal"]),
                "parent_ordinal": section.get("parent_ordinal"),
                "level": int(section["level"]),
                "title": str(section["title"]),
                "path": list(section["path"]),
                "start_offset": section_start,
                "end_offset": section_end,
                "chunks": chunks,
            }
        )
    return normalized, sections, old_chunk_keys


def source_bundle_from_rule_source(
    source: Mapping[str, Any],
    *,
    license: str,
    attribution: str,
) -> tuple[dict[str, Any], dict[str, Any], bytes, dict[str, str]]:
    """Convert one indexed rule source to a single-document source bundle."""

    normalized, sections, old_chunk_keys = _reconstruct_indexed_document(source)
    raw_metadata = dict(source.get("metadata") or {})
    metadata = {
        key: copy.deepcopy(raw_metadata[key])
        for key in (
            "source_checksum",
            "media_type",
            "page_count",
            "normalizer_profile",
            "normalizer_version",
            "text_extractor",
            "text_extractor_version",
            "ocr_provider",
            "ocr_profile",
            "quality",
            "warnings",
            "text_revision_checksum",
            "text_revision_count",
            "text_revision_pages",
            "text_revisions",
        )
        if key in raw_metadata
    }
    metadata["indexed_source_checksum"] = source["checksum"]
    bundle = build_source_bundle(
        source_key=str(source["source_key"]),
        title=str(source["title"]),
        normalized_text=normalized,
        edition=str(source.get("edition") or ""),
        locale=str(source.get("locale") or ""),
        version=str(source.get("version") or ""),
        publication_id=str(source.get("publication_id") or ""),
        authority=str(source.get("authority") or ""),
        sections=sections,
        metadata=metadata,
        license=license,
        attribution=attribution,
    )
    normalized_source = bundle[0]
    new_chunk_keys = [
        str(chunk["key"])
        for section in normalized_source["sections"]
        for chunk in section["chunks"]
    ]
    if len(old_chunk_keys) != len(new_chunk_keys):
        raise ValueError("indexed source chunk count changed during normalization")
    return (*bundle, dict(zip(old_chunk_keys, new_chunk_keys, strict=True)))


def _all_chunks(sources: Sequence[Mapping[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    return [
        (str(source["source_key"]), dict(chunk))
        for source in sources
        for section in source["sections"]
        for chunk in section["chunks"]
    ]


def _citation_from_mapping(value: Mapping[str, Any], *, note: str) -> dict[str, Any] | None:
    nested = value.get("source_ref")
    nested_ref = dict(nested) if isinstance(nested, Mapping) else {}
    source_key = str(value.get("source_key") or nested_ref.get("source_key") or "").strip()
    if not source_key:
        source = str(value.get("source") or "").strip()
        if source.startswith("rule-source:"):
            source_key = source.removeprefix("rule-source:").split("#", 1)[0]
    chunk_key = str(value.get("chunk_key") or nested_ref.get("chunk_key") or "").strip()
    if not source_key or not chunk_key:
        return None
    page = value.get(
        "page_start",
        value.get("page", nested_ref.get("page_start", nested_ref.get("page"))),
    )
    if not isinstance(page, int) or page < 1:
        page = None
    return source_ref(source_key=source_key, chunk_key=chunk_key, page=page, note=note)


def _artifact_with_refs(value: Mapping[str, Any]) -> dict[str, Any]:
    artifact = copy.deepcopy(dict(value))
    citations = artifact.pop("source_citations", [])
    existing_refs = artifact.get("source_refs") or []
    refs = [copy.deepcopy(dict(ref)) for ref in existing_refs if isinstance(ref, Mapping)]
    refs.extend(
        ref
        for citation in citations
        if isinstance(citation, Mapping)
        and (ref := _citation_from_mapping(citation, note="Artifact source evidence")) is not None
    )
    artifact["source_refs"] = list(
        {
            (ref["source_key"], ref["chunk_key"], ref.get("page"), ref.get("note", "")): ref
            for ref in refs
        }.values()
    )
    reviewed_catalog = artifact.pop("catalog_review", None)
    reviewed_selection = artifact.pop("selection_contract", None)
    if isinstance(reviewed_selection, Mapping):
        artifact["selection_contract"] = build_selection_contract(
            artifact,
            status=str(reviewed_selection.get("status") or ""),
            references=list(reviewed_selection.get("references") or []),
            blockers=list(reviewed_selection.get("blockers") or []),
        )
    elif reviewed_selection is not None:
        raise ValueError("selection_contract must be an object")
    if isinstance(reviewed_catalog, Mapping):
        artifact["catalog_review"] = build_catalog_review(
            artifact,
            decisions=list(reviewed_catalog.get("decisions") or []),
            status=str(reviewed_catalog.get("status") or ""),
        )
    elif reviewed_catalog is not None:
        raise ValueError("catalog_review must be an object")
    return artifact


def _replace_chunk_keys(value: Any, key_map: Mapping[str, str]) -> Any:
    """Rebind draft chunk keys to hashes of the exact archived text slices."""

    if isinstance(value, list):
        return [_replace_chunk_keys(item, key_map) for item in value]
    if isinstance(value, str):
        result = value
        for old_key, new_key in sorted(key_map.items(), key=lambda item: -len(item[0])):
            result = result.replace(old_key, new_key)
        return result
    if not isinstance(value, Mapping):
        return copy.deepcopy(value)
    return {key: _replace_chunk_keys(item, key_map) for key, item in value.items()}


def content_definition_checksum(
    *,
    manifest: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]],
    mechanics: Sequence[Mapping[str, Any]],
) -> str:
    def native_records(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                key: copy.deepcopy(value)
                for key, value in dict(item).items()
                if key != "rule_definition_id"
            }
            for item in items
        ]

    value = {
        "manifest": copy.deepcopy(dict(manifest)),
        "artifacts": native_records(artifacts),
        "mechanics": native_records(mechanics),
    }
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _actor_page_hints(card: Mapping[str, Any]) -> list[int]:
    refs = dict(card.get("provenance") or {}).get("source_refs", [])
    pages = [
        int(value["page"])
        for value in refs
        if isinstance(value, Mapping) and isinstance(value.get("page"), int)
    ]
    return list(dict.fromkeys(pages))


def _actor_source_refs(
    card: Mapping[str, Any], sources: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    name = str(card["name"])
    normalized_name = _slug(name)
    pages = set(_actor_page_hints(card))
    candidates = []
    for source_key, chunk in _all_chunks(sources):
        heading_names = {_slug(str(item)) for item in chunk.get("heading_path") or []}
        page_start = chunk.get("page_start")
        page_end = chunk.get("page_end")
        page_match = not pages or (
            isinstance(page_start, int)
            and isinstance(page_end, int)
            and any(page_start <= page <= page_end for page in pages)
        )
        if page_match and normalized_name in heading_names:
            candidates.append((source_key, chunk))
    if not candidates and pages:
        for source_key, chunk in _all_chunks(sources):
            page_start = chunk.get("page_start")
            page_end = chunk.get("page_end")
            if (
                isinstance(page_start, int)
                and isinstance(page_end, int)
                and any(page_start <= page <= page_end for page in pages)
            ):
                candidates.append((source_key, chunk))
    result = []
    for source_key, chunk in candidates[:1]:
        result.append(
            source_ref(
                source_key=source_key,
                chunk_key=str(chunk["key"]),
                page=min(pages) if pages else chunk.get("page_start"),
                note=f"Source evidence for {name}",
            )
        )
    return result


def actor_for_package(
    card: Mapping[str, Any],
    *,
    sources: Sequence[Mapping[str, Any]],
    fallback_source_refs: Sequence[Mapping[str, Any]] = (),
    direct_source_refs: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Bind a validated actor-card.v3 to evidence owned by its package."""

    value = validate_dnd_content_actor(card)
    provenance = copy.deepcopy(dict(value.get("provenance") or {}))
    source_text = str(provenance.pop("source_text", ""))
    provenance.pop("source_refs", None)
    provenance["source_refs"] = (
        copy.deepcopy([dict(ref) for ref in direct_source_refs])
        if direct_source_refs is not None
        else _actor_source_refs(value, sources)
        or copy.deepcopy([dict(ref) for ref in fallback_source_refs])
    )
    if source_text:
        provenance["source_text_hash"] = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    return build_actor_card(
        actor_id=str(value["id"]),
        version=str(value["version"]),
        system_id=str(value["system_id"]),
        actor_type=str(value["actor_type"]),
        name=str(value["name"]),
        player_name=value.get("player_name"),
        summary=str(value.get("summary") or ""),
        sheet=dict(value["sheet"]),
        notes=dict(value["notes"]),
        provenance=provenance,
        bindings=list(value.get("bindings") or []),
        image_asset_key=(
            str(dict(value["image"])["asset_key"]) if value.get("image") else None
        ),
        image_alt=(str(dict(value["image"])["alt"]) if value.get("image") else ""),
        metadata=dict(value.get("metadata") or {}),
    )


def _actor_artifact_source_refs(
    card: Mapping[str, Any],
    artifacts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    provenance = dict(card.get("provenance") or {})
    requested_ids = {
        match.group(1)
        for value in provenance.get("source_refs") or []
        if (match := re.search(r"#artifact:([^#]+)$", str(value)))
    }
    actor_name = _slug(str(card["name"]))
    exact = [item for item in artifacts if str(item.get("id") or "") in requested_ids]
    named = [
        item
        for item in artifacts
        if _slug(str(dict(item.get("card") or {}).get("name") or "")) == actor_name
    ]
    refs = []
    for artifact in exact or named:
        refs.extend(
            copy.deepcopy(dict(ref))
            for ref in artifact.get("source_refs") or []
            if isinstance(ref, Mapping)
        )
    return list(
        {
            (ref["source_key"], ref["chunk_key"], ref.get("page"), ref.get("note", "")): ref
            for ref in refs
        }.values()
    )


def _actors_with_artifact_source_refs(
    actors: Sequence[Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    result = copy.deepcopy([dict(actor) for actor in actors])
    statblocks_by_name: dict[str, list[dict[str, Any]]] = {}
    for artifact in artifacts:
        if str(artifact.get("kind") or "") != "statblock":
            continue
        name = _slug(str(dict(artifact.get("card") or {}).get("name") or ""))
        if name:
            statblocks_by_name.setdefault(name, []).append(dict(artifact))
    for actor in result:
        provenance = copy.deepcopy(dict(actor.get("provenance") or {}))
        refs = list(provenance.get("source_refs") or [])
        if any(isinstance(ref.get("page"), int) and ref["page"] > 0 for ref in refs):
            continue
        matches = statblocks_by_name.get(_slug(str(actor.get("name") or "")), [])
        inherited = [
            copy.deepcopy(dict(ref))
            for artifact in matches
            for ref in artifact.get("source_refs") or []
            if isinstance(ref, Mapping)
        ]
        if inherited:
            provenance["source_refs"] = list(
                {
                    (
                        ref["source_key"],
                        ref["chunk_key"],
                        ref.get("page"),
                        ref.get("note", ""),
                    ): ref
                    for ref in inherited
                }.values()
            )
            actor["provenance"] = provenance
    return result


def actor_from_statblock(
    parsed: ParsedStatblock,
    *,
    actor_id: str,
    version: str,
    actor_type: str,
    edition: str,
    source_refs: Sequence[Mapping[str, Any]],
    bindings: Sequence[Mapping[str, Any]],
    compiler: str,
    source_text: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a unified actor card from the normal D&D statblock compiler."""

    notes = default_character_notes()
    notes["profile"]["summary"] = parsed.summary
    return build_actor_card(
        actor_id=actor_id,
        version=version,
        system_id="dnd5e",
        actor_type=actor_type,
        name=parsed.name,
        summary=parsed.summary,
        sheet=finalize_imported_actor_rulings(parsed.sheet),
        notes=notes,
        provenance={
            "compiler": compiler,
            "edition": edition,
            "source_refs": copy.deepcopy(list(source_refs)),
            "source_text_hash": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
            "challenge_rating": parsed.challenge_rating,
            "experience_points": parsed.experience_points,
            "warnings": list(parsed.warnings),
            "normalization_notes": list(parsed.normalization_notes),
        },
        bindings=bindings,
        metadata=metadata,
    )


def build_rule_content_package(
    *,
    package_id: str,
    version: str,
    system_id: str,
    manifest: Mapping[str, Any],
    rule_descriptors: Sequence[Mapping[str, Any]],
    preset_actors: Sequence[Mapping[str, Any]] = (),
    metadata: Mapping[str, Any] | None = None,
    dependencies: Sequence[Mapping[str, Any]] = (),
    kind: str = "addon",
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Build a unified Pack directly from stored rule descriptors and cards."""

    package_metadata = copy.deepcopy(dict(metadata or {}))
    normalized_manifest = copy.deepcopy(dict(manifest))
    normalized_manifest.update({"id": package_id, "version": version, "system_id": system_id})
    license = str(package_metadata.get("license") or "private")
    attribution = str(package_metadata.get("attribution") or "User supplied source")
    sources: list[dict[str, Any]] = []
    assets: list[dict[str, Any]] = []
    blobs: dict[str, bytes] = {}
    artifacts = []
    mechanics = []
    rule_definitions = []
    for descriptor in rule_descriptors:
        payload = dict(descriptor)
        component_chunk_keys: dict[str, str] = {}
        for raw_source in payload["sources"]:
            source, asset, blob, chunk_keys = source_bundle_from_rule_source(
                raw_source,
                license=license,
                attribution=attribution,
            )
            for old_key, new_key in chunk_keys.items():
                previous = component_chunk_keys.get(old_key)
                if previous is not None and previous != new_key:
                    raise ValueError(f"indexed chunk key is ambiguous: {old_key}")
                component_chunk_keys[old_key] = new_key
            sources.append(source)
            assets.append(asset)
            blobs[asset["checksum"]] = blob
        component_artifacts = [
            _artifact_with_refs(item)
            for raw_item in payload["artifacts"]
            for item in [_replace_chunk_keys(raw_item, component_chunk_keys)]
        ]
        component_mechanics = [
            _replace_chunk_keys(item, component_chunk_keys) for item in payload["mechanics"]
        ]
        final_component_artifacts = []
        for item in component_artifacts:
            card = dict(item.get("card") or {})
            cleaned_item, repairs = repair_reviewed_structured_transcription(
                item,
                path="artifact",
                repair_identity=card.get("source_fragment") is True,
            )
            card = dict(cleaned_item.get("card") or {})
            if repairs:
                card.setdefault("transcription_repairs", []).extend(repairs)
            final_item = {
                **cleaned_item,
                "card": card,
                "rule_definition_id": descriptor["id"],
            }
            final_component_artifacts.append(
                _refresh_reviewed_content_hashes(final_item)
            )
        final_component_mechanics = [
            {**item, "rule_definition_id": descriptor["id"]}
            for item in component_mechanics
        ]
        artifacts.extend(final_component_artifacts)
        mechanics.extend(final_component_mechanics)
        rule_definitions.append(
            {
                "id": descriptor["id"],
                "version": descriptor["version"],
                "definition_checksum": content_definition_checksum(
                    manifest=payload["manifest"],
                    artifacts=final_component_artifacts,
                    mechanics=final_component_mechanics,
                ),
                "manifest": copy.deepcopy(payload["manifest"]),
            }
        )
    actors = [
        actor_for_package(
            card,
            sources=sources,
            fallback_source_refs=_actor_artifact_source_refs(card, artifacts),
        )
        for card in preset_actors
    ]
    actors = [_repair_reviewed_actor_transcription(actor) for actor in actors]
    content = {
        "classification": normalized_manifest["classification"],
        "editions": list(normalized_manifest["editions"]),
        "activation": copy.deepcopy(normalized_manifest["activation"]),
        "conflicts": copy.deepcopy(list(normalized_manifest.get("conflicts") or [])),
        "rule_definitions": rule_definitions,
        "artifacts": artifacts,
        "mechanics": mechanics,
    }
    normalized_manifest["content_summary"] = dict(
        sorted(Counter(str(item.get("kind") or "unknown") for item in artifacts).items())
    )
    package = build_content_package(
        kind=kind,
        package_id=package_id,
        version=version,
        system_id=system_id,
        manifest=normalized_manifest,
        sources=sources,
        assets=assets,
        content_reviews=[],
        actors=actors,
        content=content,
        metadata=package_metadata,
        dependencies=dependencies,
    )
    return package, blobs


def compose_addon_content_package(
    *,
    package_id: str,
    version: str,
    system_id: str,
    manifest: Mapping[str, Any],
    components: Sequence[tuple[Mapping[str, Any], Mapping[str, bytes]]],
    metadata: Mapping[str, Any] | None = None,
    dependencies: Sequence[Mapping[str, Any]] = (),
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Flatten unified core-rules and preset archives into one addon archive."""

    if not components:
        raise ValueError("addon composition requires at least one unified component archive")
    normalized_manifest = copy.deepcopy(dict(manifest))
    normalized_manifest.update({"id": package_id, "version": version, "system_id": system_id})
    sources_by_key: dict[str, dict[str, Any]] = {}
    assets_by_key: dict[str, dict[str, Any]] = {}
    reviews_by_id: dict[str, dict[str, Any]] = {}
    actors_by_id: dict[str, dict[str, Any]] = {}
    blobs: dict[str, bytes] = {}
    dependency_values: dict[tuple[str, str, str], dict[str, Any]] = {}

    def merge_record(target: dict[str, dict[str, Any]], key: str, item: Any) -> None:
        value = copy.deepcopy(dict(item))
        previous = target.get(key)
        if previous is not None and previous != value:
            raise ValueError(f"addon component conflict for {key}")
        target[key] = value

    def merge_content_record(
        target: dict[str, dict[str, Any]],
        item: Mapping[str, Any],
        *,
        label: str,
        identity_fields: Sequence[str] = ("id",),
    ) -> None:
        item_id = next(
            (
                str(item.get(field) or "").strip()
                for field in identity_fields
                if str(item.get(field) or "").strip()
            ),
            "",
        )
        if not item_id:
            raise ValueError(f"addon {label} requires a stable id")
        merge_record(target, f"{label}:{item_id}", item)

    rule_definitions_by_id: dict[str, dict[str, Any]] = {}
    artifacts_by_id: dict[str, dict[str, Any]] = {}
    mechanics_by_id: dict[str, dict[str, Any]] = {}
    for raw_package, raw_blobs in components:
        component = validate_dnd_content_package(raw_package)
        if component["system_id"] != system_id or component["kind"] not in {
            "core_rules",
            "preset",
        }:
            raise ValueError("addon components must be same-system core_rules or preset packages")
        component_blobs = {str(key): bytes(value) for key, value in raw_blobs.items()}
        for source in component["sources"]:
            merge_record(sources_by_key, str(source["source_key"]), source)
        for asset in component["assets"]:
            merge_record(assets_by_key, str(asset["asset_key"]), asset)
            checksum = str(asset["checksum"])
            blob = component_blobs.get(checksum)
            if blob is None:
                raise ValueError(f"addon component is missing blob {checksum}")
            previous_blob = blobs.get(checksum)
            if previous_blob is not None and previous_blob != blob:
                raise ValueError(f"addon component blob conflict for {checksum}")
            blobs[checksum] = blob
        for index, review in enumerate(component["content_reviews"]):
            review_id = str(review.get("id") or f"{component['id']}.review.{index}")
            merge_record(reviews_by_id, review_id, review)
        for actor in component["actors"]:
            merge_record(actors_by_id, str(actor["id"]), actor)
        if component["kind"] == "core_rules":
            content = dict(component["content"])
            for item in content.get("rule_definitions") or []:
                merge_content_record(rule_definitions_by_id, item, label="rule definition")
            for item in content.get("artifacts") or []:
                merge_content_record(artifacts_by_id, item, label="artifact")
            for item in content.get("mechanics") or []:
                merge_content_record(mechanics_by_id, item, label="mechanic")
        for dependency in component["dependencies"]:
            identity = (
                str(dependency["kind"]),
                str(dependency["id"]),
                str(dependency["version"]),
            )
            previous = dependency_values.get(identity)
            if previous is not None and previous != dependency:
                raise ValueError(f"addon dependency conflict for {identity[1]}@{identity[2]}")
            dependency_values[identity] = copy.deepcopy(dependency)
    for dependency in dependencies:
        identity = (
            str(dependency["kind"]),
            str(dependency["id"]),
            str(dependency["version"]),
        )
        previous = dependency_values.get(identity)
        if previous is not None and previous != dependency:
            raise ValueError(f"addon dependency conflict for {identity[1]}@{identity[2]}")
        dependency_values[identity] = copy.deepcopy(dict(dependency))

    rule_definitions = list(rule_definitions_by_id.values())
    artifacts = list(artifacts_by_id.values())
    mechanics = list(mechanics_by_id.values())
    normalized_manifest["content_summary"] = dict(
        sorted(Counter(str(item.get("kind") or "unknown") for item in artifacts).items())
    )
    actors = _actors_with_artifact_source_refs(
        list(actors_by_id.values()),
        artifacts,
    )
    package = build_content_package(
        kind="addon",
        package_id=package_id,
        version=version,
        system_id=system_id,
        manifest=normalized_manifest,
        dependencies=list(dependency_values.values()),
        sources=list(sources_by_key.values()),
        assets=list(assets_by_key.values()),
        content_reviews=list(reviews_by_id.values()),
        actors=actors,
        content={
            "classification": normalized_manifest["classification"],
            "editions": list(normalized_manifest["editions"]),
            "activation": copy.deepcopy(normalized_manifest["activation"]),
            "conflicts": copy.deepcopy(list(normalized_manifest.get("conflicts") or [])),
            "rule_definitions": rule_definitions,
            "artifacts": artifacts,
            "mechanics": mechanics,
        },
        metadata=metadata,
    )
    return package, blobs


def build_preset_content_package(
    *,
    package_id: str,
    version: str,
    system_id: str,
    title: str,
    cards: Sequence[Mapping[str, Any]],
    metadata: Mapping[str, Any] | None = None,
    dependencies: Sequence[Mapping[str, Any]] = (),
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Build a preset library with one source-backed synthetic card document."""

    if not cards:
        raise ValueError("preset content package requires at least one card")
    package_metadata = copy.deepcopy(dict(metadata or {}))
    license = str(package_metadata.get("license") or "private")
    attribution = str(package_metadata.get("attribution") or "User supplied source")
    document_parts = []
    sections = []
    cursor = 0
    for ordinal, card in enumerate(cards):
        value = validate_dnd_content_actor(card)
        provenance = dict(value.get("provenance") or {})
        source_text = str(provenance.get("source_text") or "").strip()
        if not source_text:
            source_text = f"# {value['name']}\n\n{value.get('summary') or ''}".strip()
        source_text = repair_reviewed_structured_transcription(
            {"text": source_text},
            path="preset_source",
        )[0]["text"]
        if document_parts:
            document_parts.append("\n\n")
            cursor += 2
        start = cursor
        document_parts.append(source_text)
        cursor += len(source_text)
        sections.append(
            {
                "ordinal": ordinal,
                "parent_ordinal": None,
                "level": 1,
                "title": str(value["name"]),
                "path": [str(value["name"])],
                "start_offset": start,
                "end_offset": cursor,
                "chunks": [
                    {
                        "ordinal": ordinal,
                        "heading_path": [str(value["name"])],
                        "start_offset": start,
                        "end_offset": cursor,
                        "token_count": len(source_text.split()),
                        "page_start": None,
                        "page_end": None,
                        "metadata": {"actor_id": card["id"]},
                    }
                ],
            }
        )
    normalized_text = "".join(document_parts)
    source, document_asset, document_blob = build_source_bundle(
        source_key=f"{package_id}.source",
        title=f"{title} source cards",
        normalized_text=normalized_text,
        edition=str(package_metadata.get("edition") or ""),
        authority="preset",
        sections=sections,
        license=license,
        attribution=attribution,
    )
    assets = [document_asset]
    blobs = {document_asset["checksum"]: document_blob}
    actors = []
    for card, section in zip(cards, source["sections"], strict=True):
        chunk = section["chunks"][0]
        actors.append(
            actor_for_package(
                card,
                sources=[source],
                direct_source_refs=[
                    source_ref(
                        source_key=source["source_key"],
                        chunk_key=chunk["key"],
                        note=f"Source evidence for {card['name']}",
                    )
                ],
            )
        )
    actors = [_repair_reviewed_actor_transcription(actor) for actor in actors]
    package = build_content_package(
        kind="preset",
        package_id=package_id,
        version=version,
        system_id=system_id,
        manifest={
            "title": title,
            "classification": "actor_library",
            "content_summary": {"actor_card": len(actors)},
        },
        dependencies=dependencies,
        sources=[source],
        assets=assets,
        content_reviews=[],
        actors=actors,
        content={"activation": {"actor_policy": "library"}},
        metadata=package_metadata,
    )
    return package, blobs


def _module_source_bundle(
    descriptor: Mapping[str, Any],
    *,
    license: str,
    attribution: str,
) -> tuple[dict[str, Any], dict[str, Any], bytes, dict[str, str]]:
    source_value = dict(descriptor["source"])
    document = str(descriptor["document"]["content"])
    source_key = str(source_value["source_key"])
    sections = []
    chunk_hash_keys: dict[str, str] = {}
    cursor = 0
    for ordinal, scene in enumerate(descriptor["scene_atlas"]):
        scene_content = str(scene["content"])
        scene_metadata = dict(scene.get("metadata") or {})
        scene_start = scene_metadata.get("absolute_start")
        scene_end = scene_metadata.get("absolute_end")
        if not (
            isinstance(scene_start, int)
            and isinstance(scene_end, int)
            and 0 <= scene_start <= scene_end <= len(document)
        ):
            scene_start = document.find(scene_content, cursor)
            if scene_start < 0:
                scene_start = document.find(scene_content)
            scene_end = scene_start + len(scene_content) if scene_start >= 0 else -1
        if scene_start < 0:
            raise ValueError(f"module scene {scene['stable_key']} is not in its document")
        cursor = scene_end
        chunks = []
        chunk_cursor = scene_start
        for chunk_index, raw_chunk in enumerate(scene["chunks"]):
            chunk_content = str(raw_chunk["content"])
            metadata = copy.deepcopy(dict(raw_chunk.get("metadata") or {}))
            chunk_start = metadata.get("absolute_start")
            chunk_end = metadata.get("absolute_end")
            if not (
                isinstance(chunk_start, int)
                and isinstance(chunk_end, int)
                and scene_start <= chunk_start <= chunk_end <= scene_end
            ):
                chunk_start = document.find(chunk_content, chunk_cursor, scene_end)
                if chunk_start < 0:
                    chunk_start = document.find(chunk_content, scene_start, scene_end)
                chunk_end = chunk_start + len(chunk_content) if chunk_start >= 0 else -1
            if chunk_start < 0:
                raise ValueError(f"module scene {scene['stable_key']} chunk is not in its document")
            chunk_cursor = chunk_end
            old_content_hash = str(raw_chunk.get("content_hash") or "")
            content_hash = hashlib.sha256(
                document[chunk_start:chunk_end].encode("utf-8")
            ).hexdigest()
            chunk_key = (
                f"{source_key}/scene/{scene['stable_key']}/chunk/{chunk_index}-{content_hash[:24]}"
            )
            chunk_hash_keys.setdefault(old_content_hash or content_hash, chunk_key)
            chunks.append(
                {
                    "key": chunk_key,
                    "ordinal": int(raw_chunk.get("ordinal", chunk_index)),
                    "heading_path": list(raw_chunk.get("heading_path") or [scene["title"]]),
                    "start_offset": chunk_start,
                    "end_offset": chunk_end,
                    "token_count": len(document[chunk_start:chunk_end].split()),
                    "page_start": metadata.get("page_start", scene.get("page_start")),
                    "page_end": metadata.get("page_end", scene.get("page_end")),
                    "metadata": metadata,
                }
            )
        sections.append(
            {
                "ordinal": ordinal,
                "parent_ordinal": None,
                "level": 2,
                "title": str(scene["title"]),
                "path": [str(scene["chapter"]), str(scene["title"])],
                "start_offset": scene_start,
                "end_offset": scene_end,
                "chunks": chunks,
            }
        )
    source, asset, blob = build_source_bundle(
        source_key=source_key,
        title=str(source_value["title"]),
        normalized_text=document,
        edition=str(dict(source_value.get("metadata") or {}).get("edition") or ""),
        authority="module",
        sections=sections,
        metadata={
            **copy.deepcopy(dict(source_value.get("metadata") or {})),
            "parser_profile": source_value.get("parser_profile"),
            "parser_version": source_value.get("parser_version"),
        },
        license=license,
        attribution=attribution,
    )
    return source, asset, blob, chunk_hash_keys


def _module_scene_metadata(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Separate Core scene fields from D&D profile data in the portable Pack."""

    metadata = copy.deepcopy(dict(raw))
    canonical_fields = {
        "visibility",
        "scene_level",
        "line_count",
        "subsections",
        "tags",
        "spatial",
    }
    ignored_fields = {
        "absolute_end",
        "absolute_start",
        "content_checksum",
        "end_line",
        "headings",
        "keywords",
        "page_end",
        "page_start",
        "stable_key",
        "start_line",
    }
    existing_profile_data = metadata.pop("profile_data", {})
    if not isinstance(existing_profile_data, Mapping):
        raise ValueError("module scene metadata.profile_data must be an object")
    profile_data = copy.deepcopy(dict(existing_profile_data))
    profile_data.update(
        {
            key: value
            for key, value in metadata.items()
            if key not in canonical_fields | ignored_fields
        }
    )
    return {
        "visibility": metadata.get("visibility", "restricted"),
        "scene_level": metadata.get("scene_level"),
        "line_count": metadata.get("line_count"),
        "subsections": list(metadata.get("subsections") or []),
        "tags": list(metadata.get("tags") or []),
        "spatial": copy.deepcopy(dict(metadata.get("spatial") or {})),
        "profile_data": profile_data,
    }


def _translate_module_refs(
    value: Any,
    *,
    source_key: str,
    chunk_hash_keys: Mapping[str, str],
) -> Any:
    if isinstance(value, list):
        return [
            _translate_module_refs(
                item,
                source_key=source_key,
                chunk_hash_keys=chunk_hash_keys,
            )
            for item in value
        ]
    if not isinstance(value, dict):
        return copy.deepcopy(value)
    if set(value) == {"source_key", "page", "chunk_hash", "note"}:
        chunk_hash = str(value.get("chunk_hash") or "")
        chunk_key = chunk_hash_keys.get(chunk_hash)
        if chunk_key is None:
            one_character_candidates = [
                candidate
                for candidate in chunk_hash_keys
                if len(candidate) == len(chunk_hash)
                and sum(left != right for left, right in zip(candidate, chunk_hash)) == 1
            ]
            candidate_hint = (
                f"; unique one-character candidate: {one_character_candidates[0]}"
                if len(one_character_candidates) == 1
                else ""
            )
            raise ValueError(
                "module source_ref.chunk_hash does not match imported draft evidence: "
                f"{chunk_hash or '<empty>'}{candidate_hint}; copy source_ref verbatim from "
                "module_draft(evidence)"
            )
        return source_ref(
            source_key=source_key,
            chunk_key=chunk_key,
            page=value.get("page"),
            note=str(value.get("note") or ""),
        )
    result = {}
    for key, item in value.items():
        translated = _translate_module_refs(
            item,
            source_key=source_key,
            chunk_hash_keys=chunk_hash_keys,
        )
        if translated is not None:
            result[key] = translated
    return result


def build_module_content_package(
    descriptor: Mapping[str, Any],
    archive_blobs: Mapping[str, bytes],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Normalize one Core module descriptor directly into the unified v2 Pack."""

    package_metadata = {
        **copy.deepcopy(dict(descriptor.get("metadata") or {})),
        **copy.deepcopy(dict(metadata or {})),
    }
    license = str(package_metadata.get("license") or "private")
    attribution = str(package_metadata.get("attribution") or "User supplied source")
    source, document_asset, document_blob, hash_keys = _module_source_bundle(
        descriptor,
        license=license,
        attribution=attribution,
    )
    assets = [document_asset]
    blobs = {document_asset["checksum"]: document_blob}
    original_asset_keys = []
    for raw_asset in descriptor.get("assets") or []:
        checksum = str(raw_asset["checksum"])
        raw = archive_blobs[checksum]
        asset_metadata = dict(raw_asset.get("metadata") or {})
        kind = str(asset_metadata.get("asset_kind") or "source_asset")
        portable_asset_key = str(
            asset_metadata.get("content_asset_key") or raw_asset["asset_key"]
        ).strip()
        if not portable_asset_key:
            raise ValueError("module asset content_asset_key must not be empty")
        asset = blob_descriptor(
            asset_key=portable_asset_key,
            kind=kind,
            name=str(raw_asset["name"]),
            media_type=str(raw_asset["media_type"]),
            content=raw,
            license=license,
            attribution=attribution,
            metadata=asset_metadata,
        )
        assets.append(asset)
        blobs[asset["checksum"]] = raw
        if asset["media_type"] == "application/pdf":
            original_asset_keys.append(asset["asset_key"])
    source["original_asset_keys"] = original_asset_keys
    translated_manifest = _translate_module_refs(
        descriptor["manifest"],
        source_key=source["source_key"],
        chunk_hash_keys=hash_keys,
    )
    scenes = []
    for raw_scene, source_section in zip(
        descriptor["scene_atlas"], source["sections"], strict=True
    ):
        scene = {
            key: copy.deepcopy(value)
            for key, value in raw_scene.items()
            if key not in {"content", "content_checksum", "chunks"}
        }
        scene_metadata = _module_scene_metadata(scene.get("metadata") or {})
        profile_data = dict(scene_metadata.get("profile_data") or {})
        if "combat_grid_templates" in profile_data:
            translated_templates = _translate_module_refs(
                profile_data["combat_grid_templates"],
                source_key=source["source_key"],
                chunk_hash_keys=hash_keys,
            )
            profile_data["combat_grid_templates"] = normalize_combat_grid_templates(
                translated_templates
            )
            scene_metadata["profile_data"] = profile_data
        scene["metadata"] = scene_metadata
        scene["source_span"] = {
            "source_key": source["source_key"],
            "start_offset": source_section["start_offset"],
            "end_offset": source_section["end_offset"],
        }
        scene["source_refs"] = [
            source_ref(
                source_key=source["source_key"],
                chunk_key=chunk["key"],
                page=chunk.get("page_start"),
                note=f"Scene source for {raw_scene['title']}",
            )
            for chunk in source_section["chunks"]
        ]
        scenes.append(scene)
    actors = [
        actor_for_package(
            card,
            sources=[source],
        )
        for card in descriptor.get("actors") or []
    ]
    actors = [_repair_reviewed_actor_transcription(actor) for actor in actors]
    reviews = []
    for index, raw_review in enumerate(descriptor.get("content_reviews") or []):
        evidence = dict(raw_review.get("evidence") or {})
        refs = [
            source_ref(
                source_key=source["source_key"],
                chunk_key=hash_keys[chunk_hash],
                page=None,
                note="Reviewed module content evidence",
            )
            for chunk_hash in evidence.get("chunk_hashes") or []
            if chunk_hash in hash_keys
        ]
        reviews.append(
            {
                "id": f"review.{index}.{_slug(str(raw_review['content_key']))}",
                "kind": str(raw_review["content_kind"]),
                "status": "accepted",
                "target": {
                    "scene_key": raw_review["scene_key"],
                    "content_key": raw_review["content_key"],
                },
                "normalized_content": raw_review["normalized_content"],
                "evidence": {
                    "asset_key": evidence.get("asset_key"),
                    "page": evidence.get("page"),
                },
                "source_refs": refs,
                "review": {
                    "reviewer": evidence.get("reviewer"),
                    "observation": evidence.get("observation"),
                },
                "metadata": copy.deepcopy(dict(raw_review.get("metadata") or {})),
            }
        )
    content = {
        "classification": translated_manifest["classification"],
        "compatibility": translated_manifest["compatibility"],
        "play_profile": translated_manifest["play_profile"],
        "continuity": translated_manifest["continuity"],
        "activation": translated_manifest["activation"],
        "scene_atlas": scenes,
        "catalogs": _translate_module_refs(
            descriptor["catalogs"],
            source_key=source["source_key"],
            chunk_hash_keys=hash_keys,
        ),
        "narrative": _translate_module_refs(
            descriptor["narrative"],
            source_key=source["source_key"],
            chunk_hash_keys=hash_keys,
        ),
    }
    package_result = build_content_package(
        kind="module",
        package_id=str(descriptor["id"]),
        version=str(descriptor["version"]),
        system_id=str(descriptor["system_id"]),
        manifest=translated_manifest,
        dependencies=[
            copy.deepcopy(dict(item)) for item in descriptor.get("dependencies") or []
        ],
        sources=[source],
        assets=assets,
        content_reviews=reviews,
        actors=actors,
        content=content,
        metadata=package_metadata,
    )
    return validate_dnd_content_package(package_result), blobs


def _rebuild_package(
    package: Mapping[str, Any],
    *,
    sources: Sequence[Mapping[str, Any]] | None = None,
    assets: Sequence[Mapping[str, Any]] | None = None,
    actors: Sequence[Mapping[str, Any]] | None = None,
    content: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return validate_dnd_content_package(build_content_package(
        kind=str(package["kind"]),
        package_id=str(package["id"]),
        version=str(package["version"]),
        system_id=str(package["system_id"]),
        manifest=package["manifest"],
        dependencies=package["dependencies"],
        sources=sources if sources is not None else package["sources"],
        assets=assets if assets is not None else package["assets"],
        content_reviews=package["content_reviews"],
        actors=actors if actors is not None else package["actors"],
        content=content if content is not None else package["content"],
        metadata=package["metadata"],
    ))


def attach_content_actors(
    package: Mapping[str, Any],
    blobs: Mapping[str, bytes],
    actors: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Attach validated detached actors without placing images in runtime state."""

    combined = [*copy.deepcopy(list(package["actors"])), *copy.deepcopy(list(actors))]
    identities = [str(actor["id"]) for actor in combined]
    if len(identities) != len(set(identities)):
        raise ValueError("content actor ids must be unique inside a package")
    return (
        _rebuild_package(package, actors=combined),
        {str(key): bytes(value) for key, value in blobs.items()},
    )


def attach_source_originals(
    package: Mapping[str, Any],
    blobs: Mapping[str, bytes],
    source_paths: Mapping[str, str | Path],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Attach licensed original documents to matching source bundles."""

    assets = copy.deepcopy(list(package["assets"]))
    sources = copy.deepcopy(list(package["sources"]))
    next_blobs = {str(key): bytes(value) for key, value in blobs.items()}
    license = str(package["metadata"].get("license") or "private")
    attribution = str(package["metadata"].get("attribution") or "User supplied source")
    for source in sources:
        path_value = source_paths.get(str(source["source_key"]))
        if path_value is None:
            continue
        path = Path(path_value)
        content = path.read_bytes()
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        asset = blob_descriptor(
            asset_key=f"source.{_slug(str(source['source_key']))}.original",
            kind="original_document",
            name=path.name,
            media_type=media_type,
            content=content,
            license=license,
            attribution=attribution,
            metadata={"source_key": source["source_key"]},
        )
        assets.append(asset)
        next_blobs.setdefault(asset["checksum"], content)
        source["original_asset_keys"] = [
            *source["original_asset_keys"],
            asset["asset_key"],
        ]
    return _rebuild_package(package, sources=sources, assets=assets), next_blobs


def attach_auxiliary_assets(
    package: Mapping[str, Any],
    blobs: Mapping[str, bytes],
    entries: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Attach source-distributed maps, handouts, and reference documents.

    Auxiliary assets are intentionally distinct from ``original_document``:
    the latter is the exact document normalized into one source bundle, while
    these files accompany the package without pretending that their contents
    were indexed or used as mechanical evidence.
    """

    assets = copy.deepcopy(list(package["assets"]))
    next_blobs = {str(key): bytes(value) for key, value in blobs.items()}
    license = str(package["metadata"].get("license") or "private")
    attribution = str(package["metadata"].get("attribution") or "User supplied source")
    existing_by_key = {str(asset["asset_key"]): asset for asset in assets}
    for index, raw_entry in enumerate(entries):
        entry = dict(raw_entry)
        allowed = {
            "path",
            "kind",
            "logical_path",
            "name",
            "metadata",
            "source_refs",
        }
        unexpected = set(entry) - allowed
        if unexpected:
            raise ValueError(f"auxiliary asset {index} has unexpected fields: {sorted(unexpected)}")
        path_value = entry.get("path")
        kind = " ".join(str(entry.get("kind") or "").split())
        if path_value is None or not kind:
            raise ValueError(f"auxiliary asset {index} requires path and kind")
        path = Path(path_value)
        if not path.is_file():
            raise FileNotFoundError(path)
        logical_path = str(entry.get("logical_path") or path.name).replace("\\", "/").strip("/")
        if not logical_path or ".." in Path(logical_path).parts:
            raise ValueError(f"auxiliary asset {index} has an invalid logical_path")
        content = path.read_bytes()
        checksum = hashlib.sha256(content).hexdigest()
        asset_key = f"auxiliary.{_slug(logical_path)}.{checksum[:16]}"
        descriptor = blob_descriptor(
            asset_key=asset_key,
            kind=kind,
            name=str(entry.get("name") or path.name),
            media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
            content=content,
            license=license,
            attribution=attribution,
            source_refs=copy.deepcopy(list(entry.get("source_refs") or [])),
            metadata={
                "logical_path": logical_path,
                "relationship": "package_auxiliary",
                **copy.deepcopy(dict(entry.get("metadata") or {})),
            },
        )
        existing = existing_by_key.get(asset_key)
        if existing is not None:
            if existing != descriptor or next_blobs.get(checksum) != content:
                raise ValueError(f"conflicting auxiliary asset key: {asset_key}")
            continue
        assets.append(descriptor)
        existing_by_key[asset_key] = descriptor
        next_blobs.setdefault(descriptor["checksum"], content)
    return (
        _rebuild_package(package, assets=assets),
        next_blobs,
    )


def _portrait_sources(actor: Mapping[str, Any]) -> list[tuple[str, int]]:
    refs = dict(actor.get("provenance") or {}).get("source_refs") or []
    result = []
    for ref in refs:
        if isinstance(ref, Mapping) and isinstance(ref.get("page"), int) and int(ref["page"]) > 0:
            result.append((str(ref["source_key"]), int(ref["page"])))
    return list(dict.fromkeys(result))


def _portrait_cache_key(actor: Mapping[str, Any]) -> str:
    """Reuse a crop only when actor identity and exact evidence refs agree."""

    refs = dict(actor.get("provenance") or {}).get("source_refs") or []
    evidence = "|".join(
        f"{ref['source_key']}:{ref.get('chunk_key', '')}:{int(ref['page'])}"
        for ref in refs
        if isinstance(ref, Mapping) and isinstance(ref.get("page"), int) and int(ref["page"]) > 0
    )
    return f"{_slug(str(actor['name']))}|{evidence}"


def content_actor_catalog_definition(
    package: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Project package-owned actor cards into the installed preset catalog."""

    value = validate_dnd_content_package(package)
    editions = list(value["content"].get("editions") or [])
    if not editions:
        editions = list(value["manifest"].get("editions") or [])
    pack_id = f"{value['id']}.actors"
    artifacts = []
    for actor in value["actors"]:
        checked = validate_dnd_content_actor(actor)
        provenance = dict(checked.get("provenance") or {})
        actor_id = str(checked["id"])
        artifact_id = (
            actor_id
            if actor_id.startswith(f"{pack_id}.")
            else f"{pack_id}.actor.{hashlib.sha256(actor_id.encode()).hexdigest()[:20]}"
        )
        artifacts.append(
            {
                "id": artifact_id,
                "kind": "actor_card",
                "card": {
                    "name": checked["name"],
                    "edition": checked["sheet"]["edition"],
                    "actor_type": checked["actor_type"],
                    "content_actor": checked,
                },
                "rule_refs": list(provenance.get("source_refs") or []),
                "mechanic_refs": [],
                "source_citations": [
                    {"source": source_ref} for source_ref in provenance.get("source_refs") or []
                ],
            }
        )
    return (
        {
            "id": pack_id,
            "version": value["version"],
            "title": f"{value['manifest']['title']} Actors",
            "namespace": pack_id,
            "system_id": value["system_id"],
            "editions": editions,
            "capabilities": [],
            "native_mechanic_refs": [],
            "content_kinds": ["actor_card"],
            "license": value["metadata"].get("license"),
            "attribution": value["metadata"].get("attribution"),
        },
        artifacts,
    )


def attach_actor_portraits(
    package: Mapping[str, Any],
    blobs: Mapping[str, bytes],
    source_paths: Mapping[str, str | Path],
    *,
    portrait_library: Mapping[str, ExtractedPortrait] | None = None,
    portrait_reviews: Mapping[str, Mapping[str, Any]] | None = None,
    minimum_confidence: float = 0.18,
) -> tuple[dict[str, Any], dict[str, bytes], dict[str, Any], dict[str, ExtractedPortrait]]:
    """Attach audited art to static actors and source statblock card templates."""

    assets = copy.deepcopy(list(package["assets"]))
    actors = copy.deepcopy(list(package["actors"]))
    content = copy.deepcopy(dict(package["content"]))
    artifacts = list(content.get("artifacts") or [])
    subjects = [
        {
            "subject_type": "actor",
            "subject_id": str(actor["id"]),
            "name": str(actor["name"]),
            "source_refs": list(dict(actor.get("provenance") or {}).get("source_refs") or []),
            "target": actor,
        }
        for actor in actors
    ]
    for artifact in artifacts:
        if not isinstance(artifact, dict) or str(artifact.get("kind") or "") != "statblock":
            continue
        card = artifact.get("card")
        if not isinstance(card, dict) or not str(card.get("name") or "").strip():
            continue
        subjects.append(
            {
                "subject_type": "statblock_card",
                "subject_id": str(artifact.get("id") or ""),
                "name": str(card["name"]),
                "source_refs": list(artifact.get("source_refs") or []),
                "target": card,
            }
        )
    next_blobs = {str(key): bytes(value) for key, value in blobs.items()}
    library = dict(portrait_library or {})
    license = str(package["metadata"].get("license") or "private")
    attribution = str(package["metadata"].get("attribution") or "User supplied source")
    extracted = 0
    reused = 0
    missing = []
    review_required = []
    illustration_absent = []
    reviewed = []
    reviews = {str(key): dict(item) for key, item in dict(portrait_reviews or {}).items()}
    consumed_review_keys: set[str] = set()
    image_refs_by_cache: dict[str, dict[str, str]] = {}
    with _portrait_extractor_type()() as extractor:
        for subject in subjects:
            target = subject["target"]
            if target.get("image") is not None:
                continue
            review_key = (
                f"{package['id']}|{subject['subject_type']}|{subject['subject_id']}"
            )
            raw_review = reviews.get(review_key)
            active_review: dict[str, Any] | None = None
            normalized_name = _slug(subject["name"])
            evidence_subject = {
                "name": subject["name"],
                "provenance": {"source_refs": subject["source_refs"]},
            }
            cache_key = _portrait_cache_key(evidence_subject)
            sources = _portrait_sources(evidence_subject)
            if raw_review is not None:
                decision = str(raw_review.get("decision") or "").strip()
                reviewer = " ".join(str(raw_review.get("reviewer") or "").split())
                note = " ".join(str(raw_review.get("note") or "").split())
                if not reviewer:
                    raise ValueError(f"portrait review {review_key} requires reviewer")
                allowed = (
                    {"decision", "reviewer", "note"}
                    if decision == "illustration_absent"
                    else {"decision", "source_key", "page", "crop", "reviewer", "note"}
                )
                unknown = set(raw_review) - allowed
                if unknown:
                    raise ValueError(
                        f"portrait review {review_key} has unsupported fields: {sorted(unknown)}"
                    )
                consumed_review_keys.add(review_key)
                active_review = {
                    "review_key": review_key,
                    "decision": decision,
                    "reviewer": reviewer,
                    "note": note,
                }
                if decision == "illustration_absent":
                    entry = {
                        "review_key": review_key,
                        "subject_type": subject["subject_type"],
                        "subject_id": subject["subject_id"],
                        "name": subject["name"],
                        "sources": [
                            {"source_key": source_key, "page": page}
                            for source_key, page in sources
                        ],
                        "diagnostics": [],
                        "reason": "reviewer confirmed that the source has no usable portrait",
                        "review": active_review,
                    }
                    illustration_absent.append(entry)
                    missing.append(
                        {
                            "subject_type": subject["subject_type"],
                            "subject_id": subject["subject_id"],
                            "name": subject["name"],
                            "reason": "reviewed illustration_absent",
                        }
                    )
                    reviewed.append(active_review)
                    continue
                if decision != "crop":
                    raise ValueError(
                        f"portrait review {review_key} decision must be crop or "
                        "illustration_absent"
                    )
                source_key = str(raw_review.get("source_key") or "")
                page = raw_review.get("page")
                crop = raw_review.get("crop")
                if (
                    not isinstance(page, int)
                    or isinstance(page, bool)
                    or (source_key, page) not in sources
                    or source_key not in source_paths
                    or not isinstance(crop, list)
                    or len(crop) != 4
                ):
                    raise ValueError(
                        f"portrait review {review_key} must bind an exact cited source page "
                        "and four-value crop"
                    )
                portrait = extractor.extract_reviewed_crop(
                    source_paths[source_key],
                    page_number=page,
                    crop=tuple(crop),
                )
                library[cache_key] = portrait
                extracted += 1
                active_review.update(
                    {
                        "source_key": source_key,
                        "page": page,
                        "crop": [float(value) for value in crop],
                    }
                )
                reviewed.append(active_review)
            else:
                portrait = None
            existing_ref = image_refs_by_cache.get(cache_key)
            if raw_review is None and existing_ref is not None:
                target["image"] = {
                    "asset_key": existing_ref["asset_key"],
                    "alt": f"{subject['name']} portrait",
                }
                reused += 1
                continue
            if raw_review is None:
                portrait = library.get(cache_key)
            if portrait is None:
                inspections = [
                    extractor.inspect(
                        source_paths[source[0]],
                        name=subject["name"],
                        page_number=source[1],
                        minimum_confidence=minimum_confidence,
                    )
                    for source in sources
                    if source[0] in source_paths
                ]
                candidates = [
                    inspection.portrait
                    for inspection in inspections
                    if inspection.portrait is not None
                ]
                portrait = max(candidates, key=lambda item: item.confidence, default=None)
                if portrait is not None:
                    library.setdefault(cache_key, portrait)
                    extracted += 1
                else:
                    diagnostics = [
                        {
                            "status": inspection.status,
                            "heading_found": inspection.heading_found,
                            "candidate_count": inspection.candidate_count,
                            "best_confidence": inspection.best_confidence,
                        }
                        for inspection in inspections
                    ]
                    entry = {
                        "review_key": review_key,
                        "subject_type": subject["subject_type"],
                        "subject_id": subject["subject_id"],
                        "name": subject["name"],
                        "sources": [
                            {"source_key": source_key, "page": page} for source_key, page in sources
                        ],
                        "diagnostics": diagnostics,
                    }
                    entry["reason"] = "source image extraction requires review"
                    review_required.append(entry)
            elif portrait is not None and raw_review is None:
                reused += 1
            if portrait is None:
                missing.append(
                    {
                        "subject_type": subject["subject_type"],
                        "subject_id": subject["subject_id"],
                        "name": subject["name"],
                        "reason": (
                            "source image extraction requires review"
                            if any(
                                item["subject_type"] == subject["subject_type"]
                                and item["subject_id"] == subject["subject_id"]
                                for item in review_required
                            )
                            else "source page has no reliable actor illustration"
                        ),
                    }
                )
                continue
            refs = subject["source_refs"]
            asset = blob_descriptor(
                asset_key=(
                    f"{subject['subject_type']}.{_slug(subject['subject_id'])}.image"
                ),
                kind="actor_image",
                name=f"{normalized_name}.webp",
                media_type=portrait.media_type,
                content=portrait.content,
                license=license,
                attribution=attribution,
                source_refs=refs,
                metadata={
                    "subject_type": subject["subject_type"],
                    "subject_id": subject["subject_id"],
                    "extraction": {
                        "method": portrait.method,
                        "page": portrait.page,
                        "crop": list(portrait.crop),
                        "confidence": portrait.confidence,
                    },
                    **({"review": active_review} if active_review is not None else {}),
                },
            )
            assets.append(asset)
            next_blobs.setdefault(asset["checksum"], portrait.content)
            image_refs_by_cache[cache_key] = {
                "asset_key": asset["asset_key"],
            }
            target["image"] = {
                "asset_key": asset["asset_key"],
                "alt": f"{subject['name']} portrait",
            }
    unmatched_reviews = sorted(set(reviews) - consumed_review_keys)
    if unmatched_reviews:
        raise ValueError(
            "portrait reviews matched no package subject: " + ", ".join(unmatched_reviews)
        )
    next_package = _rebuild_package(
        package,
        assets=assets,
        actors=actors,
        content=content,
    )
    return (
        next_package,
        next_blobs,
        {
            "actors": len(actors),
            "images": sum(actor["image"] is not None for actor in actors),
            "statblock_cards": sum(
                subject["subject_type"] == "statblock_card" for subject in subjects
            ),
            "statblock_card_images": sum(
                subject["subject_type"] == "statblock_card"
                and subject["target"].get("image") is not None
                for subject in subjects
            ),
            "subjects": len(subjects),
            "subject_images": sum(
                subject["target"].get("image") is not None for subject in subjects
            ),
            "extracted": extracted,
            "reused": reused,
            "missing": missing,
            "review_required": review_required,
            "illustration_absent": illustration_absent,
            "reviewed": reviewed,
            "complete": not review_required,
        },
        library,
    )
