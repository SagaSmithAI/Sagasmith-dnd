"""Conservative candidate extraction for user-imported D&D rule sources."""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any

_SUBCLASS_WORDS = (
    "archetype",
    "domain",
    "circle",
    "college",
    "oath",
    "path",
    "tradition",
    "school",
    "patron",
    "origin",
    "bloodline",
)
_ITEM_WORDS = ("wondrous item", "weapon", "armor", "potion", "ring", "rod", "staff", "wand")


def extract_content_candidates(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract review-required cards; never claim unsupported mechanics are executable."""
    candidates: list[dict[str, Any]] = []
    for chunk in chunks:
        content = str(chunk.get("content") or "").strip()
        heading_path = [str(item).strip() for item in chunk.get("heading_path") or []]
        title = next((item for item in reversed(heading_path) if item), "")
        kind = _kind_for(title, heading_path, content)
        if not kind or not title:
            continue
        chunk_id = str(chunk.get("id") or "").strip()
        if not chunk_id:
            continue
        candidate_id = (
            "candidate:"
            + hashlib.sha256(f"{chunk_id}:{kind}:{title}".encode("utf-8")).hexdigest()[:20]
        )
        candidates.append(
            {
                "id": candidate_id,
                "kind": kind,
                "name": title,
                "source_chunk_ids": [chunk_id],
                "source_heading_path": heading_path,
                "page_start": chunk.get("page_start"),
                "page_end": chunk.get("page_end"),
                "extraction_confidence": "heuristic",
                "review_status": "pending",
                "application_state": "catalog_only",
                "execution_state": "not_compiled",
                "artifact": {
                    "kind": kind,
                    "application_state": "catalog_only",
                    "card": {"name": title, "description": content[:1200]},
                },
            }
        )
    return candidates


def compiled_artifacts_from_candidates(
    candidates: list[dict[str, Any]], *, pack_id: str
) -> list[dict[str, Any]]:
    """Turn DM-approved candidates into source-bound pack artifacts.

    `catalog_only` is intentional: it gives the agent searchable source-linked
    content without permitting an incomplete parse to alter a character sheet.
    A reviewed artifact must explicitly opt into `selection_ready`.
    """
    artifacts: list[dict[str, Any]] = []
    ids: set[str] = set()
    for candidate in candidates:
        if candidate.get("review_status") != "accepted":
            continue
        value = deepcopy(dict(candidate.get("artifact") or {}))
        kind = str(value.get("kind") or candidate.get("kind") or "").strip()
        card = dict(value.get("card") or {})
        name = str(card.get("name") or candidate.get("name") or "").strip()
        if not kind or not name:
            raise ValueError(f"accepted candidate {candidate.get('id')} needs kind and card.name")
        artifact_id = str(value.get("id") or _artifact_id(pack_id, kind, name)).strip()
        if artifact_id in ids:
            raise ValueError(f"duplicate generated artifact id: {artifact_id}")
        ids.add(artifact_id)
        chunk_ids = [str(item) for item in candidate.get("source_chunk_ids") or [] if str(item)]
        if not chunk_ids:
            raise ValueError(f"accepted candidate {candidate.get('id')} needs source_chunk_ids")
        state = str(
            value.get("application_state") or candidate.get("application_state") or "catalog_only"
        )
        if state not in {"catalog_only", "selection_ready"}:
            raise ValueError("application_state must be catalog_only or selection_ready")
        artifacts.append(
            {
                **value,
                "id": artifact_id,
                "kind": kind,
                "card": card,
                "application_state": state,
                "source_chunk_ids": chunk_ids,
            }
        )
    return artifacts


def validate_selection_ready_artifacts(artifacts: list[dict[str, Any]]) -> list[str]:
    """Check the minimum schema needed before a catalog card can mutate a sheet."""
    errors: list[str] = []
    for index, artifact in enumerate(artifacts):
        if artifact.get("application_state", "selection_ready") != "selection_ready":
            continue
        kind = str(artifact.get("kind") or "")
        card = dict(artifact.get("card") or {})
        prefix = f"artifacts[{index}]"
        if kind == "spell":
            if not isinstance(card.get("classes"), list) or not card["classes"]:
                errors.append(f"{prefix} spell needs a nonempty classes list")
            level = card.get("level")
            if not isinstance(level, int) or not 0 <= level <= 9:
                errors.append(f"{prefix} spell level must be an integer from 0 to 9")
            if not isinstance(card.get("definition"), dict):
                errors.append(f"{prefix} spell needs a structured definition")
        elif kind == "subclass":
            if not str(card.get("class_name") or "").strip():
                errors.append(f"{prefix} subclass needs class_name")
            if not isinstance(card.get("minimum_level"), int) or card["minimum_level"] < 1:
                errors.append(f"{prefix} subclass needs minimum_level >= 1")
        elif kind == "background":
            if not isinstance(card.get("background_grants"), dict):
                errors.append(f"{prefix} background needs background_grants")
        elif (
            kind == "feat"
            and "prerequisites" in card
            and not isinstance(card["prerequisites"], list)
        ):
            errors.append(f"{prefix} feat prerequisites must be a list")
    return errors


def _kind_for(title: str, heading_path: list[str], content: str) -> str | None:
    folded = " ".join([*heading_path, title, content[:600]]).casefold()
    if "casting time" in folded and ("spell" in folded or "level" in folded):
        return "spell"
    if "skill proficiencies" in folded or "background" in folded:
        return "background"
    if "feat" in folded:
        return "feat"
    if any(word in folded for word in _SUBCLASS_WORDS):
        return "subclass"
    if any(word in folded for word in _ITEM_WORDS):
        return "item"
    return None


def _artifact_id(pack_id: str, kind: str, name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    if not slug:
        slug = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return f"{pack_id}.{kind}.{slug[:100]}"
