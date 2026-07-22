"""Conservative candidate extraction for user-imported D&D rule sources."""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any

from sagasmith_dnd.spell_resolution import (
    SPELL_RESOLUTION_MECHANIC_ID,
    normalize_spell_resolution,
)

_ITEM_HEADER_RE = re.compile(
    r"(?im)^(?:wondrous item|weapon|armor|potion|ring|rod|staff|wand)(?:\s*[,—-]|\s*$)"
)
_SPELL_LEVEL_RE = re.compile(
    r"(?im)^(?:\d+(?:st|nd|rd|th)[ -]level\s+[a-z]+|[a-z]+\s+cantrip)\b"
)
_STATBLOCK_LABELS = ("armor class", "hit points", "speed", "challenge")
_CLASS_NAMES = {
    "artificer",
    "barbarian",
    "bard",
    "blood hunter",
    "cleric",
    "druid",
    "fighter",
    "monk",
    "paladin",
    "ranger",
    "rogue",
    "sorcerer",
    "warlock",
    "wizard",
}
_GENERIC_TITLES = {
    "background",
    "backgrounds",
    "class",
    "class features",
    "feats",
    "magic items",
    "spells",
    "subclass",
}
_GENERIC_FEATURE_TITLES = {
    "class features",
    "equipment",
    "hit points",
    "proficiencies",
    "quick build",
}
_PAGE_HEADER_RE = re.compile(r"(?i)^(?:chapter|part|appendix)\b")


def extract_content_candidates(
    chunks: list[dict[str, Any]],
    *,
    source_title: str = "",
) -> list[dict[str, Any]]:
    """Extract review-required cards; never claim unsupported mechanics are executable."""
    sections: dict[tuple[str, ...], dict[str, Any]] = {}
    for chunk in chunks:
        content = str(chunk.get("content") or "").strip()
        heading_path = [str(item).strip() for item in chunk.get("heading_path") or []]
        title = next((item for item in reversed(heading_path) if item), "")
        chunk_id = str(chunk.get("id") or "").strip()
        if not chunk_id or not title:
            continue
        key = tuple(item.casefold() for item in heading_path)
        section = sections.setdefault(
            key,
            {
                "title": title,
                "heading_path": heading_path,
                "source_chunk_ids": [],
                "content": [],
                "page_start": None,
                "page_end": None,
            },
        )
        section["source_chunk_ids"].append(chunk_id)
        if content and content not in section["content"]:
            section["content"].append(content)
        section["page_start"] = _minimum_page(
            section.get("page_start"), chunk.get("page_start")
        )
        section["page_end"] = _maximum_page(
            section.get("page_end"), chunk.get("page_end")
        )

    own_classifications = {
        key: _classify(
            str(section["title"]),
            list(section["heading_path"]),
            "\n\n".join(section["content"]),
            source_title=source_title,
        )
        for key, section in sections.items()
    }
    candidates: list[dict[str, Any]] = []
    source_class_name = _class_name_from_source(source_title)
    for key, section in sections.items():
        descendants = [
            value
            for candidate_key, value in sections.items()
            if len(candidate_key) > len(key) and candidate_key[: len(key)] == key
        ]
        content_parts = [*section["content"]]
        source_chunk_ids = list(section["source_chunk_ids"])
        page_start = section["page_start"]
        page_end = section["page_end"]
        for descendant in descendants:
            content_parts.extend(descendant["content"])
            source_chunk_ids.extend(descendant["source_chunk_ids"])
            page_start = _minimum_page(page_start, descendant.get("page_start"))
            page_end = _maximum_page(page_end, descendant.get("page_end"))
        content = "\n\n".join(content_parts)
        classification = _classify(
            str(section["title"]),
            list(section["heading_path"]),
            content,
            source_title=source_title,
        )
        if classification is None:
            continue
        kind, signals = classification
        candidate_name = (
            source_class_name
            if kind == "class"
            and source_class_name
            and str(section["title"]).casefold() == "class features"
            else section["title"]
        )
        if kind == "class" and source_class_name:
            source_chunk_ids = [
                chunk_id
                for value in sections.values()
                for chunk_id in value["source_chunk_ids"]
            ]
        if own_classifications[key] is None and any(
            candidate_key[: len(key)] == key
            and len(candidate_key) > len(key)
            and descendant_classification is not None
            and descendant_classification[0] == kind
            for candidate_key, descendant_classification in own_classifications.items()
        ):
            # A heading-only catalog such as "Optional Spells" must not become a
            # duplicate entity merely because its descendant spell text was
            # aggregated. Entity parents such as a class still aggregate their
            # differently classified feature descendants.
            continue
        identity = "\x1f".join((kind, *key))
        candidates.append(
            {
                "id": "candidate:"
                + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20],
                "kind": kind,
                "name": candidate_name,
                "source_chunk_ids": list(dict.fromkeys(source_chunk_ids)),
                "source_heading_path": section["heading_path"],
                "page_start": page_start,
                "page_end": page_end,
                "extraction_confidence": "high" if len(signals) >= 3 else "medium",
                "extraction_signals": list(signals),
                "review_status": "pending",
                "application_state": "catalog_only",
                "execution_state": "not_compiled",
                "artifact": {
                    "kind": kind,
                    "application_state": "catalog_only",
                    "card": {"name": candidate_name, "description": content[:2000]},
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
        if kind == "spell" and card.get("resolution") is not None:
            card["resolution"] = normalize_spell_resolution(
                card["resolution"], f"candidate {candidate.get('id')} spell.resolution"
            )
            mechanic_refs = list(
                dict.fromkeys(
                    [
                        *list(value.get("mechanic_refs") or []),
                        *list(card.get("mechanic_refs") or []),
                        SPELL_RESOLUTION_MECHANIC_ID,
                    ]
                )
            )
            value["mechanic_refs"] = mechanic_refs
            card["mechanic_refs"] = mechanic_refs
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
            if card.get("resolution") is not None:
                try:
                    normalize_spell_resolution(
                        card["resolution"], f"{prefix}.card.resolution"
                    )
                except ValueError as error:
                    errors.append(str(error))
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


def _classify(
    title: str,
    heading_path: list[str],
    content: str,
    *,
    source_title: str = "",
) -> tuple[str, tuple[str, ...]] | None:
    title_folded = title.casefold().strip()
    ancestors = " ".join(heading_path[:-1]).casefold()
    sample = content[:2400]
    folded = sample.casefold()

    spell_labels = tuple(
        label
        for label in ("casting time", "range", "components", "duration")
        if re.search(rf"(?i)\b{re.escape(label)}\s*:", sample)
    )
    spell_level = bool(_SPELL_LEVEL_RE.search(sample))
    if "casting time" in spell_labels and (spell_level or len(spell_labels) >= 3):
        signals = [*spell_labels, *(["spell level"] if spell_level else [])]
        return "spell", tuple(signals)

    statblock_labels = tuple(label for label in _STATBLOCK_LABELS if label in folded)
    ability_row = all(value in folded for value in ("str", "dex", "con", "int", "wis", "cha"))
    if title_folded not in {"actions", "cha", "legendary actions"} and len(
        statblock_labels
    ) >= 3 and ability_row:
        return "statblock", (*statblock_labels, "six abilities")

    background_signals = tuple(
        label
        for label in (
            "skill proficiencies",
            "tool proficiencies",
            "languages",
            "equipment",
            "background feature",
        )
        if label in folded
    )
    if "skill proficiencies" in background_signals and (
        "background" in ancestors or len(background_signals) >= 2
    ):
        return "background", background_signals

    feat_section = bool(re.search(r"\bfeats?\b", ancestors))
    if (
        title_folded not in _GENERIC_TITLES
        and not _PAGE_HEADER_RE.match(title_folded)
        and feat_section
        and (
        "prerequisite" in folded or len(folded) >= 80
        )
    ):
        signals = ["feat section"]
        if "prerequisite" in folded:
            signals.append("prerequisite")
        return "feat", tuple(signals)

    subclass_title = bool(
        re.search(
            r"\b(?:path|college|domain|circle|oath|school|patron|origin|bloodline|"
            r"archetype|tradition)\s+of\b|\b\w+\s+domain(?:\s+features)?$",
            title_folded,
        )
    )
    subclass_section = "subclass" in ancestors or "subclasses" in ancestors
    subclass_features = "subclass features" in folded
    if title_folded not in _GENERIC_TITLES and (
        subclass_features or (subclass_title and (subclass_section or "level" in folded))
    ):
        signals = [
            *(["subclass title"] if subclass_title else []),
            *(["subclass section"] if subclass_section else []),
            *(["subclass features"] if subclass_features else []),
        ]
        return "subclass", tuple(signals)

    class_signals = [
        label
        for label in ("class features", "hit dice", "primary ability", "saving throw proficiencies")
        if label in folded
    ]
    if title_folded == "class features" and "class features" not in class_signals:
        class_signals.insert(0, "class features")
    source_class_name = _class_name_from_source(source_title).casefold()
    known_source_class = source_class_name in _CLASS_NAMES or source_class_name == "revised ranger"
    if (
        title_folded == "class features"
        and known_source_class
        and "source class" not in class_signals
    ):
        class_signals.append("source class")
    class_title = title_folded in _CLASS_NAMES or any(
        name in title_folded for name in ("artificer", "blood hunter")
    )
    class_title = class_title or (
        title_folded == "class features" and known_source_class
    )
    if class_title and "class features" in class_signals and len(class_signals) >= 2:
        return "class", tuple(class_signals)

    species_signals = tuple(
        label
        for label in ("ability score increase", "age", "alignment", "size", "speed", "languages")
        if re.search(rf"(?i)\b{re.escape(label)}\s*[.:]", sample)
    )
    if len(species_signals) >= 4:
        return "species", species_signals

    item_header = _ITEM_HEADER_RE.search(sample[:500])
    if item_header:
        signals = ["item category"]
        for label in ("rarity", "requires attunement", "charges"):
            if label in folded:
                signals.append(label)
        return "item", tuple(signals)

    feature_section = (
        "class features" in ancestors
        or "subclass features" in ancestors
        or known_source_class
    )
    level_grant = bool(re.search(r"(?i)\bat\s+\d+(?:st|nd|rd|th)\s+level\b", folded))
    if (
        title_folded not in _GENERIC_FEATURE_TITLES
        and feature_section
        and level_grant
    ):
        return "feature", ("feature section", "level grant")
    return None


def _class_name_from_source(source_title: str) -> str:
    folded = source_title.casefold()
    compact = re.sub(r"[^a-z]+", "", folded)
    if "revisedranger" in compact:
        return "Revised Ranger"
    if "bloodhunter" in compact:
        return "Blood Hunter"
    for name in sorted(_CLASS_NAMES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}(?:v\d+)?\b", folded):
            return name.title()
    return ""


def _minimum_page(left: Any, right: Any) -> int | None:
    values = [
        value
        for value in (left, right)
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    return min(values) if values else None


def _maximum_page(left: Any, right: Any) -> int | None:
    values = [
        value
        for value in (left, right)
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    return max(values) if values else None


def _artifact_id(pack_id: str, kind: str, name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
    if not slug:
        slug = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return f"{pack_id}.{kind}.{slug[:100]}"
