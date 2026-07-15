"""Build portable, source-linked SRD content artifacts from bundled Markdown."""

from __future__ import annotations

import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

PACK_ID = "dnd5e.content.srd2014"
PACK_VERSION = "1.0.0"


def build_srd2014_content(skill_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest, artifacts = _cached_srd2014_content(str(skill_root.resolve()))
    return deepcopy(manifest), deepcopy(artifacts)


@lru_cache(maxsize=4)
def _cached_srd2014_content(skill_root: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = Path(skill_root) / "full" / "skills" / "dnd-dm" / "srd" / "references-2014-en"
    if not root.is_dir():
        return {}, []
    artifacts: list[dict[str, Any]] = []
    artifacts.extend(_spells(root / "07_Spells"))
    artifacts.extend(_simple_files(root / "01_Races", "species"))
    artifacts.extend(_simple_files(root / "02_Classes", "class"))
    artifacts.extend(_sections(root / "02_Classes", "subclass", _subclass_sections))
    artifacts.extend(_sections(root / "03_Characterization", "background", _h2_sections))
    artifacts.extend(_sections(root / "05_Feats", "feat", _h2_sections))
    artifacts.extend(_simple_files(root / "04_Equipment", "item"))
    artifacts.extend(_simple_files(root / "09_Magic_Items", "item"))
    return (
        {
            "id": PACK_ID,
            "version": PACK_VERSION,
            "title": "D&D 5e SRD 2014 Structured Content",
            "namespace": PACK_ID,
            "system_id": "dnd5e",
            "editions": ["2014"],
            "capabilities": [],
            "content_kinds": [
                "class",
                "subclass",
                "species",
                "background",
                "feat",
                "spell",
                "item",
            ],
        },
        _deduplicate(artifacts),
    )


def _spells(folder: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in _markdown_files(folder):
        text = path.read_text(encoding="utf-8")
        name = _heading_or_stem(text, path)
        level, school = _spell_level_school(text)
        result.append(
            _artifact(
                "spell",
                name,
                path,
                {
                    "name": name,
                    "level": level,
                    "grant": {"source_type": "catalog", "source_key": PACK_ID, "method": "known"},
                    "access": {
                        "known": True,
                        "prepared": False,
                        "ritual_available": "ritual" in text.casefold(),
                    },
                    "definition": {
                        "school": school,
                        "casting_time": _label(text, "Casting Time") or "1 action",
                        "range": _range(_label(text, "Range")),
                        "duration": _duration(_label(text, "Duration")),
                        "components": _components(_label(text, "Components")),
                        "effect": _body_after_metadata(text),
                    },
                },
            )
        )
    return result


def _simple_files(folder: Path, kind: str) -> list[dict[str, Any]]:
    return [
        _artifact(
            kind,
            _heading_or_stem(path.read_text(encoding="utf-8"), path),
            path,
            {"name": _heading_or_stem(path.read_text(encoding="utf-8"), path)},
        )
        for path in _markdown_files(folder)
    ]


def _sections(folder: Path, kind: str, extractor: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in _markdown_files(folder):
        text = path.read_text(encoding="utf-8")
        for title, body in extractor(text):
            card: dict[str, Any] = {"name": title, "description": body[:1200]}
            if kind == "background":
                card["background_grants"] = {
                    "feature": "",
                    "languages": [],
                    "tools": [],
                    "choices": [],
                }
            result.append(_artifact(kind, title, path, card))
    return result


def _h2_sections(text: str) -> Iterable[tuple[str, str]]:
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", text, re.MULTILINE))
    for index, match in enumerate(matches):
        title = match.group(1).strip()
        if title.casefold() in {"backgrounds", "feats"}:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        yield title, text[match.end() : end].strip()


def _subclass_sections(text: str) -> Iterable[tuple[str, str]]:
    marker = re.search(
        r"^##\s+.+(?:Archetypes|Domains|Circles|Colleges|Oaths|Traditions|Schools|Patrons|Origins|Bloodlines).*$",
        text,
        re.MULTILINE | re.IGNORECASE,
    )
    if not marker:
        return []
    tail = text[marker.end() :]
    matches = list(re.finditer(r"^###\s+(.+?)\s*$", tail, re.MULTILINE))
    result = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(tail)
        result.append((match.group(1).strip(), tail[match.end() : end].strip()))
    return result


def _artifact(kind: str, name: str, path: Path, card: dict[str, Any]) -> dict[str, Any]:
    slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-") or "entry"
    rel = path.as_posix().split("references-2014-en/", 1)[-1]
    if kind in {"feat", "feature", "activity"}:
        card.setdefault("activation", {"type": "passive"})
    return {
        "id": f"{PACK_ID}.{kind}.{slug}",
        "kind": kind,
        "card": card,
        "rule_refs": [f"bundled:srd2014/{rel}"],
        "source_citations": [{"source": f"bundled:srd2014/{rel}"}],
    }


def _deduplicate(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    result = []
    for value in values:
        identifier = str(value["id"])
        if identifier in seen:
            # Duplicate display titles are retained with a deterministic source suffix.
            identifier = f"{identifier}-{len(seen)}"
            value = {**value, "id": identifier}
        seen.add(identifier)
        result.append(value)
    return result


def _markdown_files(folder: Path) -> list[Path]:
    return (
        sorted(path for path in folder.rglob("*.md") if path.is_file()) if folder.is_dir() else []
    )


def _heading_or_stem(text: str, path: Path) -> str:
    match = re.search(r"^#{1,3}\s+(.+?)\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else path.stem.replace("_", " ")


def _spell_level_school(text: str) -> tuple[int, str]:
    match = re.search(r"\*([^*]+)\*", text)
    value = match.group(1).casefold() if match else ""
    if "cantrip" in value:
        return 0, value.replace("cantrip", "").strip()
    level = re.search(r"(\d+)(?:st|nd|rd|th)-level\s+(.+)", value)
    return (int(level.group(1)), level.group(2).strip()) if level else (0, "")


def _label(text: str, label: str) -> str:
    match = re.search(rf"\*\*{re.escape(label)}:\*\*\s*([^\n]+)", text, re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _range(value: str) -> dict[str, Any]:
    folded = value.casefold()
    if folded == "self":
        return {"kind": "self"}
    if folded == "touch":
        return {"kind": "touch"}
    match = re.search(r"(\d+)\s*feet", folded)
    return {"kind": "distance", "normal_ft": int(match.group(1))} if match else {"kind": "special"}


def _duration(value: str) -> dict[str, Any]:
    folded = value.casefold()
    concentration = "concentration" in folded
    if "instantaneous" in folded:
        return {"kind": "instantaneous", "concentration": concentration}
    match = re.search(r"(\d+)\s*(round|minute|hour|day)", folded)
    return (
        {
            "kind": "timed",
            "value": int(match.group(1)),
            "unit": match.group(2),
            "concentration": concentration,
        }
        if match
        else {"kind": "special", "unit": "special", "concentration": concentration}
    )


def _components(value: str) -> dict[str, Any]:
    tokens = {item.strip().casefold()[:1] for item in value.split(",") if item.strip()}
    return {
        "verbal": "v" in tokens,
        "somatic": "s" in tokens,
        "material": "m" in tokens,
        "material_description": value,
    }


def _body_after_metadata(text: str) -> str:
    parts = text.split("\n\n")
    return "\n\n".join(parts[2:])[:4000]
