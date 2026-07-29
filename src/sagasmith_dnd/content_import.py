"""Conservative candidate extraction for user-imported D&D rule sources."""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any

from sagasmith_core.text import ascii_slug

from sagasmith_dnd.abilities import ABILITY_LABELS
from sagasmith_dnd.spell_resolution import (
    SPELL_RESOLUTION_MECHANIC_ID,
    normalize_spell_resolution,
)
from sagasmith_dnd.statblocks import StatblockImportError, parse_2014_statblock

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
_STATBLOCK_FIELD_LABELS = (
    "Saving Throws",
    "Skills",
    "Damage Vulnerabilities",
    "Damage Resistances",
    "Damage Immunities",
    "Condition Immunities",
    "Senses",
    "Languages",
    "Challenge",
)
_CREATURE_CORE_RE = re.compile(
    r"(?is)^\s*(?P<identity>(?:Tiny|Small|Medium|Large|Huge|Gargantuan)\s+.+?)"
    r"\s+Armor Class\s+(?P<armor>.+?)"
    r"\s+Hit Points\s+(?P<hit_points>.+?)"
    r"\s+Speed\s+(?P<speed>.+?)\s*$"
)
_ENTRY_START_RE = re.compile(
    r"(?P<prefix>^|(?<=\.)[ \t]+|\n+)"
    r"(?P<name>[A-Z][A-Za-z0-9'’/-]*(?:\s+(?:[A-Z][A-Za-z0-9'’/-]*|"
    r"and|or|of|the|a|an))*(?:\s+\([^.\n]{1,60}\))?)\.\s+"
    r"(?=[A-Z*])"
)
_MECHANICAL_ENTRY_START_RE = re.compile(
    r"(?P<prefix>^|(?<=\.)[ \t]+|\n+)"
    r"(?P<name>[A-Z][^.\n]{0,99}?)\.\s+"
    r"(?=(?:\*?)(?:Melee|Ranged|Melee or Ranged)\s+"
    r"(?:Weapon|Spell)\s+Attack:)",
    re.IGNORECASE,
)


def _canonical_source_heading(value: str) -> str:
    """Ignore layout-OCR spacing and punctuation inside a source heading."""

    return "".join(character for character in value.casefold() if character.isalnum())


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


def module_statblock_review_candidates(
    chunks: list[dict[str, Any]],
    *,
    source_title: str = "",
) -> list[dict[str, Any]]:
    """Build review-only executable statblocks from source-proven module chunks.

    Candidates that retain ambiguous OCR or omit a required combat fact remain
    visible with a manual-review error. They are never silently repaired.
    """
    del source_title
    candidates: list[dict[str, Any]] = []
    scoped_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for ordered in _ordered_chunks_by_scene(chunks):
        roots: list[int] = []
        for index, chunk in enumerate(ordered):
            content = str(chunk.get("content") or "").strip()
            path = [
                str(item).strip()
                for item in chunk.get("heading_path") or []
                if str(item).strip()
            ]
            if not path or _CREATURE_CORE_RE.match(content) is None:
                continue
            roots.append(index)
        for root_index, start in enumerate(roots):
            end = roots[root_index + 1] if root_index + 1 < len(roots) else len(ordered)
            root = ordered[start]
            path = [
                str(item).strip()
                for item in root.get("heading_path") or []
                if str(item).strip()
            ]
            key = tuple(item.casefold() for item in path)
            scene_id = str(root.get("scene_id") or "")
            scoped = ordered[start:end]
            identity = "\x1f".join(("statblock", scene_id, *key))
            candidate_id = "candidate:" + hashlib.sha256(
                identity.encode("utf-8")
            ).hexdigest()[:20]
            scoped_by_candidate[candidate_id] = scoped
            candidates.append(
                {
                    "id": candidate_id,
                    "kind": "statblock",
                    "name": path[-1],
                    "source_chunk_ids": list(
                        dict.fromkeys(
                            str(chunk.get("id") or "")
                            for chunk in scoped
                            if str(chunk.get("id") or "")
                        )
                    ),
                    "source_heading_path": path,
                    "page_start": _minimum_page_values(
                        chunk.get("page_start") for chunk in scoped
                    ),
                    "page_end": _maximum_page_values(
                        chunk.get("page_end") for chunk in scoped
                    ),
                    "extraction_confidence": "high",
                    "extraction_signals": [
                        "armor class",
                        "hit points",
                        "speed",
                        "six ability headings",
                    ],
                    "review_status": "pending",
                    "application_state": "review_only",
                    "execution_state": "not_compiled",
                }
            )
    for candidate in candidates:
        scoped = scoped_by_candidate[str(candidate["id"])]
        candidate["source_scene_ids"] = list(
            dict.fromkeys(
                str(chunk.get("scene_id") or "")
                for chunk in scoped
                if str(chunk.get("scene_id") or "")
            )
        )
        try:
            normalized = _normalize_module_statblock(str(candidate["name"]), scoped)
            parsed = parse_2014_statblock(
                normalized,
                source_key=f"module-candidate:{candidate['id']}",
            )
        except (StatblockImportError, ValueError) as error:
            candidate["review_status"] = "manual_review_required"
            candidate["execution_state"] = "blocked"
            candidate["review_error"] = str(error)
            continue
        candidate["normalized_content"] = normalized
        candidate["review_status"] = "pending"
        candidate["execution_state"] = "review_ready"
        candidate["validation"] = {
            "name": parsed.name,
            "challenge_rating": parsed.challenge_rating,
            "experience_points": parsed.experience_points,
            "warnings": list(parsed.warnings),
            "settlement": "automatic" if not parsed.warnings else "mixed",
        }
    return candidates


def normalize_2014_statblock_candidate(
    name: str,
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Recover one named 2014 statblock from deterministic text-layout chunks.

    PDF layout extraction can place a section heading before the final ability
    column, or attach all headings to the preceding creature. Select the
    requested creature by its own core heading and stop at the next creature
    core so recovery never borrows facts from an adjacent statblock.
    """

    target = name.strip()
    if not target:
        raise ValueError("statblock candidate name must not be empty")
    canonical_target = _canonical_source_heading(target)
    matches: list[tuple[list[dict[str, Any]], int]] = []
    for ordered in _ordered_chunks_by_scene(chunks):
        for index, chunk in enumerate(ordered):
            if (
                _CREATURE_CORE_RE.match(str(chunk.get("content") or "").strip())
                is None
            ):
                continue
            heading = next(
                (
                    str(value).strip()
                    for value in reversed(chunk.get("heading_path") or [])
                    if str(value).strip()
                ),
                "",
            )
            if _canonical_source_heading(heading) == canonical_target:
                matches.append((ordered, index))
    if not matches:
        raise StatblockImportError(
            f"statblock source chunks contain no creature core headed {target!r}"
        )
    if len(matches) > 1:
        raise StatblockImportError(
            f"statblock source chunks contain multiple creature cores headed {target!r}"
        )
    ordered, root_index = matches[0]
    end_index = next(
        (
            index
            for index in range(root_index + 1, len(ordered))
            if _CREATURE_CORE_RE.match(
                str(ordered[index].get("content") or "").strip()
            )
            is not None
        ),
        len(ordered),
    )
    scoped = ordered[root_index:end_index]
    normalized = _normalize_module_statblock(target, scoped)
    source_chunk_ids = list(
        dict.fromkeys(
            str(chunk.get("id") or "").strip()
            for chunk in scoped
            if str(chunk.get("id") or "").strip()
        )
    )
    return {
        "name": target,
        "normalized_content": normalized,
        "source_chunk_ids": source_chunk_ids,
    }


def _ordered_chunks_by_scene(
    chunks: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Keep scene-local ordinals from interleaving unrelated statblocks."""

    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, chunk in enumerate(chunks):
        grouped.setdefault(str(chunk.get("scene_id") or ""), []).append((index, chunk))
    return [
        [
            chunk
            for _index, chunk in sorted(
                indexed,
                key=lambda item: (
                    item[1].get("ordinal")
                    if isinstance(item[1].get("ordinal"), int)
                    else item[0],
                    item[0],
                ),
            )
        ]
        for indexed in grouped.values()
    ]


def _normalize_module_statblock(name: str, chunks: list[dict[str, Any]]) -> str:
    if not chunks:
        raise StatblockImportError("statblock candidate has no source chunks")
    root = next(
        (
            chunk
            for chunk in chunks
            if _CREATURE_CORE_RE.match(str(chunk.get("content") or "").strip())
        ),
        None,
    )
    if root is None:
        raise StatblockImportError("statblock candidate has no creature core chunk")
    root_path = list(root.get("heading_path") or [])
    root_text = str(root.get("content") or "").strip()
    core_text = re.split(r"(?m)^#{2,6}\s+", root_text, maxsplit=1)[0].strip()
    core = _CREATURE_CORE_RE.match(" ".join(core_text.splitlines()))
    if core is None:
        raise StatblockImportError(
            "statblock candidate needs an unambiguous size/type, Armor Class, Hit Points, and Speed"
        )

    ability_values: dict[str, str] = {}
    detail_parts: list[str] = []
    section_parts: dict[str, list[str]] = {
        "ACTIONS": [],
        "REACTIONS": [],
        "LEGENDARY ACTIONS": [],
    }
    active_section: str | None = None
    expanded_chunks = [chunk for chunk in chunks if chunk is not root]
    heading_matches = list(re.finditer(r"(?m)^#{2,6}\s+(.+?)\s*$", root_text))
    for index, match in enumerate(heading_matches):
        end = (
            heading_matches[index + 1].start()
            if index + 1 < len(heading_matches)
            else len(root_text)
        )
        expanded_chunks.append(
            {
                "heading_path": [*root_path, match.group(1).strip()],
                "content": root_text[match.end() : end].strip(),
            }
        )
    for chunk in expanded_chunks:
        path = [str(item).strip() for item in chunk.get("heading_path") or []]
        title = path[-1].upper()
        compact_title = re.sub(r"[^A-Z]", "", title)
        canonical_section = {
            "ACTIONS": "ACTIONS",
            "REACTIONS": "REACTIONS",
            "LEGENDARYACTIONS": "LEGENDARY ACTIONS",
        }.get(compact_title)
        content = str(chunk.get("content") or "").strip()
        if canonical_section is not None:
            active_section = canonical_section
        if compact_title == "".join(ABILITY_LABELS):
            cursor = 0
            for ability in ABILITY_LABELS:
                score = re.match(
                    r"^\s*(\d+)\s*\(([+\-−]\d+)\)",
                    content[cursor:],
                )
                if score is None:
                    raise StatblockImportError(
                        "statblock combined ability score row is ambiguous"
                    )
                ability_values[ability] = f"{score.group(1)} ({score.group(2)})"
                cursor += score.end()
            tail = content[cursor:].strip()
            if tail:
                if active_section is None:
                    detail_parts.append(tail)
                else:
                    section_parts[active_section].append(tail)
            continue
        if title in ABILITY_LABELS:
            score = re.match(r"^\s*(\d+)\s*\(([+\-−]?\d+)\)(?P<tail>.*)$", content, re.S)
            if score is None:
                raise StatblockImportError(f"statblock {title} score is ambiguous")
            ability_values[title] = f"{score.group(1)} ({score.group(2).replace('−', '-')})"
            tail = score.group("tail").strip()
            if tail:
                if active_section is None:
                    detail_parts.append(tail)
                else:
                    section_parts[active_section].append(tail)
            continue
        path_sections = {
            {
                "ACTIONS": "ACTIONS",
                "REACTIONS": "REACTIONS",
                "LEGENDARYACTIONS": "LEGENDARY ACTIONS",
            }.get(re.sub(r"[^A-Z]", "", item.upper()))
            for item in path
        }
        section = next((value for value in section_parts if value in path_sections), None)
        if section is not None:
            if canonical_section != section and content:
                section_parts[section].append(f"{path[-1]}. {content}")
            elif content:
                section_parts[section].append(content)
        elif content:
            detail_parts.append(content)
    missing_abilities = [label for label in ABILITY_LABELS if label not in ability_values]
    if missing_abilities:
        raise StatblockImportError(
            "statblock candidate is missing ability scores: " + ", ".join(missing_abilities)
        )

    fields, traits = _split_statblock_details(" ".join(detail_parts))
    # Some two-column PDFs flatten the decorated Actions divider into the
    # final trait column.  Recover only the distinctive OCR form so ordinary
    # prose that happens to mention actions cannot move between sections.
    actions_boundary = re.search(
        r"(?i)(?<![A-Za-z])A\s+ctions\s+_{4,}\s*",
        traits,
    )
    if actions_boundary is not None:
        action_tail = traits[actions_boundary.end() :].strip()
        traits = traits[: actions_boundary.start()].strip()
        if action_tail:
            section_parts["ACTIONS"].insert(0, action_tail)
    rendered = [
        f"# {name}",
        "",
        f"*{core.group('identity').strip()}*",
        "",
        f"**Armor Class** {core.group('armor').strip()}",
        f"**Hit Points** {_normalize_statblock_ocr(core.group('hit_points').strip())}",
        f"**Speed** {core.group('speed').strip()}",
        "",
        "| STR | DEX | CON | INT | WIS | CHA |",
        "|---:|---:|---:|---:|---:|---:|",
        "| " + " | ".join(ability_values[label] for label in ABILITY_LABELS) + " |",
    ]
    for label in _STATBLOCK_FIELD_LABELS:
        if fields.get(label):
            rendered.append(f"**{label}** {fields[label]}")
    if traits:
        rendered.extend(
            ("", "## Traits", "", _mark_statblock_entries(_normalize_statblock_ocr(traits)))
        )
    for section, parts in section_parts.items():
        content = _trim_trailing_statblock_lore(" ".join(parts).strip())
        if content:
            rendered.extend(
                (
                    "",
                    f"## {section.title()}",
                    "",
                    _mark_statblock_entries(_normalize_statblock_ocr(content)),
                )
            )
    return "\n".join(rendered).strip() + "\n"


def _trim_trailing_statblock_lore(content: str) -> str:
    """Exclude adjacent creature lore from the final attack entry.

    Module appendices sometimes place general creature lore immediately after
    the last statblock action without a heading boundary.  Trim only a
    conservative completed mechanical clause followed by recognizable ancestry
    or habitat prose.  Mechanical continuations such as "The target is
    restrained" therefore remain part of the action.
    """

    boundary = re.search(
        r"(?is)\bdamage\.\s+"
        r"(?=[A-Z][A-Za-z'’/-]*(?:\s+\([^.\n]{1,80}\))?"
        r"\s+(?:are|is)\s+(?:a|an|the)\b)",
        content,
    )
    if boundary is not None and re.search(
        r"(?i)\bHit:\s*", content[: boundary.start()]
    ) is not None:
        return content[: boundary.start() + len("damage.")].rstrip()
    inclusion_boundary = re.search(
        r"(?is)\bdamage\.\s+(?=[A-Z][A-Za-z'鈥?-]+s?\s+include\s+"
        r"(?:members|creatures|people|warriors|servants)\b)",
        content,
    )
    if inclusion_boundary is not None and re.search(
        r"(?i)\bHit:\s*", content[: inclusion_boundary.start()]
    ) is not None:
        return content[: inclusion_boundary.start() + len("damage.")].rstrip()
    habitat_boundary = re.search(
        r"(?is)\bdamage\)\.\s+(?=Usually found\b)",
        content,
    )
    if habitat_boundary is not None and re.search(
        r"(?i)\bHit:\s*", content[: habitat_boundary.start()]
    ) is not None:
        return content[: habitat_boundary.start() + len("damage).")].rstrip()
    origin_boundary = re.search(
        r"(?is)\bdamage\.\s+(?=Ha\s+iling\s+from\s+uncivilized\s+lands\b)",
        content,
    )
    if origin_boundary is not None and re.search(
        r"(?i)\bHit:\s*", content[: origin_boundary.start()]
    ) is not None:
        return content[: origin_boundary.start() + len("damage.")].rstrip()
    return content


def _normalize_statblock_ocr(content: str) -> str:
    """Repair only context-bounded, unambiguous statblock OCR tokens."""

    def normalize(match: re.Match[str]) -> str:
        token = match.group(0)
        sides = token[2:].replace("l", "1").replace("I", "1")
        if not sides.isdigit() or int(sides) < 2:
            return token
        return f"1d{sides}"

    normalized = re.sub(
        r"(?<![A-Za-z0-9])[1lI]d[0-9lI]+(?![A-Za-z0-9])",
        normalize,
        content,
    )
    normalized = re.sub(
        r"(?i)(?<![A-Za-z0-9])(\d+)dS(?![A-Za-z0-9])",
        r"\1d8",
        normalized,
    )
    normalized = re.sub(
        r"(?i)\brange\s+(\d+)\s*f\s*(\d+)\s*ft\.",
        r"range \1/\2 ft.",
        normalized,
    )
    normalized = re.sub(
        r"(?i)\branged\s+(\d+)\s*ft\.?\s*/\s*(\d+)\s*ft\.",
        r"range \1/\2 ft.",
        normalized,
    )
    normalized = re.sub(
        r"(?i)(\([^){}\n]{1,60})\}\."
        r"(?=\s+(?:Melee|Ranged|Melee or Ranged)\s+"
        r"(?:Weapon|Spell)\s+Attack:)",
        r"\1).",
        normalized,
    )
    normalized = re.sub(
        r"\b([A-Z])\s+([a-z][A-Za-z'-]+)"
        r"(?=\.\s+(?i:Melee|Ranged|Melee or Ranged)\s+"
        r"(?i:Weapon|Spell)\s+Attack:)",
        lambda match: f"{match.group(1)}{match.group(2)}",
        normalized,
    )
    normalized = re.sub(
        r"(?i)\bM\s+elee(?=\s+Weapon\s+Attack\b)",
        "Melee",
        normalized,
    )
    normalized = re.sub(
        r"(?i)\bR\s+anged(?=\s+Weapon\s+Attack\b)",
        "Ranged",
        normalized,
    )
    for pattern, replacement in (
        (r"(?i)\bray\s+offrost\b", "ray of frost"),
        (r"(?i)\binvisihility\b", "invisibility"),
        (r"(?i)\bfaeriefire\b", "faerie fire"),
    ):
        normalized = re.sub(pattern, replacement, normalized)
    normalized = re.sub(
        r"(?i)\b(\d+)(st|nd|rd|th)[\u00b7\u2022]\s*level\b",
        r"\1\2-level",
        normalized,
    )
    return re.sub(
        r"(?i)(?<![A-Za-z0-9])[lI]\s+st\s+level"
        r"(?=\s*\(\s*\d+\s+slots?\s*\)\s*:)",
        "1st level",
        normalized,
    )


def _split_statblock_details(content: str) -> tuple[dict[str, str], str]:
    matches: list[tuple[int, int, str]] = []
    for label in _STATBLOCK_FIELD_LABELS:
        match = re.search(rf"(?i)(?<!\w){re.escape(label)}\s+", content)
        if match:
            matches.append((match.start(), match.end(), label))
    matches.sort()
    fields: dict[str, str] = {}
    traits = content[: matches[0][0]].strip() if matches else content.strip()
    for index, (start, end, label) in enumerate(matches):
        next_start = matches[index + 1][0] if index + 1 < len(matches) else len(content)
        value = content[end:next_start].strip()
        if label == "Challenge":
            challenge = re.match(r"([^\s(]+(?:\s*\([\d,]+\s+XP\))?)(?P<tail>.*)", value, re.S)
            if challenge:
                fields[label] = challenge.group(1).strip()
                tail = challenge.group("tail").strip()
                traits = " ".join(part for part in (traits, tail) if part).strip()
                continue
        fields[label] = value
    return fields, traits


def _mark_statblock_entries(content: str) -> str:
    mechanically_marked = _MECHANICAL_ENTRY_START_RE.sub(
        lambda match: (
            f"{match.group('prefix')}***{match.group('name').strip()}***. "
        ),
        content,
    )
    return _ENTRY_START_RE.sub(
        lambda match: (
            f"{match.group('prefix')}***{match.group('name').strip()}***. "
        ),
        mechanically_marked,
    )


def _minimum_page_values(values: Any) -> int | None:
    pages = [value for value in values if isinstance(value, int) and not isinstance(value, bool)]
    return min(pages) if pages else None


def _maximum_page_values(values: Any) -> int | None:
    pages = [value for value in values if isinstance(value, int) and not isinstance(value, bool)]
    return max(pages) if pages else None


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
    slug = ascii_slug(name)
    if not slug:
        slug = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
    return f"{pack_id}.{kind}.{slug[:100]}"
