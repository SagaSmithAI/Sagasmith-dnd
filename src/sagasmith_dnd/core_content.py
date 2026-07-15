"""Build portable, source-linked SRD content artifacts from bundled Markdown."""

from __future__ import annotations

import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

PACK_ID = "dnd5e.content.srd2014"
PACK_VERSION = "1.1.0"

_SUBCLASS_LEVELS = {
    "barbarian": 3,
    "bard": 3,
    "cleric": 1,
    "druid": 2,
    "fighter": 3,
    "monk": 3,
    "paladin": 3,
    "ranger": 3,
    "rogue": 3,
    "sorcerer": 1,
    "warlock": 1,
    "wizard": 2,
}


def build_srd2014_content(skill_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest, artifacts = _cached_srd2014_content(str(skill_root.resolve()))
    return deepcopy(manifest), deepcopy(artifacts)


@lru_cache(maxsize=4)
def _cached_srd2014_content(skill_root: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = Path(skill_root) / "full" / "skills" / "dnd-dm" / "srd" / "references-2014-en"
    if not root.is_dir():
        return {}, []
    artifacts: list[dict[str, Any]] = []
    spell_classes = _spell_class_lists(root / "07_Spells" / "Spell_Lists.md")
    artifacts.extend(_spells(root / "07_Spells" / "Spells_Each", spell_classes))
    artifacts.extend(_simple_files(root / "01_Races" / "Races_Each", "species"))
    artifacts.extend(_simple_files(root / "02_Classes", "class"))
    artifacts.extend(_subclasses(root / "02_Classes"))
    artifacts.extend(
        _sections_from_paths(
            [root / "03_Characterization" / "Backgrounds.md"],
            "background",
            _h2_sections,
        )
    )
    artifacts.extend(_sections(root / "05_Feats", "feat", _h2_sections))
    artifacts.extend(_equipment_items(root / "04_Equipment"))
    artifacts.extend(_simple_files(root / "09_Magic_Items" / "Magic_Items_Each", "item"))
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


def _spells(folder: Path, spell_classes: dict[str, list[str]]) -> list[dict[str, Any]]:
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
                    "classes": list(spell_classes.get(_name_key(name), [])),
                    "grant": {
                        "source_type": "catalog",
                        "source_key": "",
                        "method": "unselected",
                    },
                    "access": {
                        "known": False,
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
    result: list[dict[str, Any]] = []
    for path in _markdown_files(folder):
        text = path.read_text(encoding="utf-8")
        name = _heading_or_stem(text, path)
        result.append(
            _artifact(
                kind,
                name,
                path,
                {"name": name, "description": _description(text)},
            )
        )
    return result


def _sections(folder: Path, kind: str, extractor: Any) -> list[dict[str, Any]]:
    return _sections_from_paths(_markdown_files(folder), kind, extractor)


def _sections_from_paths(paths: Iterable[Path], kind: str, extractor: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for title, body in extractor(text):
            card: dict[str, Any] = {"name": title, "description": body[:1200]}
            if kind == "background":
                card.update(_background_fields(body))
            if kind == "feat":
                prerequisites = _feat_prerequisites(body)
                if prerequisites:
                    card["prerequisites"] = prerequisites
            result.append(_artifact(kind, title, path, card))
    return result


def _subclasses(folder: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in _markdown_files(folder):
        text = path.read_text(encoding="utf-8")
        class_name = _heading_or_stem(text, path)
        for title, body in _subclass_sections(text):
            result.append(
                _artifact(
                    "subclass",
                    title,
                    path,
                    {
                        "name": title,
                        "class_name": class_name,
                        "minimum_level": _SUBCLASS_LEVELS.get(class_name.casefold(), 1),
                        "description": body[:1200],
                    },
                )
            )
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
        r"^##\s+.+(?:Archetypes|Domains|Circles|Colleges|Oaths|Paths|Traditions|Schools|Patrons|Origins|Bloodlines).*$",
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


def _spell_class_lists(path: Path) -> dict[str, list[str]]:
    if not path.is_file():
        return {}
    current = ""
    result: dict[str, set[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^##\s+(.+?)\s+Spells\s*$", line, re.IGNORECASE)
        if heading:
            current = heading.group(1).strip().casefold()
            continue
        entry = re.match(r"^[-*]\s+(.+?)\s*$", line)
        if current and entry:
            name = re.sub(r"\[\[|\]\]", "", entry.group(1)).strip()
            result.setdefault(_name_key(name), set()).add(current)
    return {key: sorted(values) for key, values in result.items()}


def _equipment_items(folder: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    sources = {
        "Adventuring_Gear.md": {"Adventuring Gear"},
        "Armor.md": {"Armor"},
        "Tools.md": {"Tools"},
        "Trade_Goods.md": {"Cost of Trade Goods"},
        "Transportation.md": {
            "Mounts and Other Animals",
            "Tack, Harness, and Drawn Vehicles",
            "Waterborne Vehicles",
        },
        "Weapons.md": {"Weapons"},
    }
    for name, allowed_tables in sources.items():
        path = folder / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for table_name, fields in _markdown_table_rows(text):
            if table_name not in allowed_tables:
                continue
            item_name = next(iter(fields.values()), "").strip()
            if not item_name or item_name.startswith("**"):
                continue
            result.append(
                _artifact(
                    "item",
                    item_name,
                    path,
                    {
                        "name": item_name,
                        "category": path.stem.replace("_", " "),
                        "table": table_name,
                        "properties": {
                            _name_key(key).replace("-", "_"): value for key, value in fields.items()
                        },
                    },
                )
            )
    return result


def _markdown_table_rows(text: str) -> Iterable[tuple[str, dict[str, str]]]:
    lines = text.splitlines()
    table_name = ""
    index = 0
    while index < len(lines):
        marker = re.match(r"^\*\*Table-\s*(.+?)\*\*\s*$", lines[index], re.IGNORECASE)
        if marker:
            table_name = marker.group(1).strip()
        if (
            lines[index].lstrip().startswith("|")
            and index + 1 < len(lines)
            and re.match(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$", lines[index + 1])
        ):
            headers = _table_cells(lines[index])
            index += 2
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                values = _table_cells(lines[index])
                if values and any(values):
                    padded = [*values, *([""] * max(0, len(headers) - len(values)))]
                    yield table_name, dict(zip(headers, padded, strict=False))
                index += 1
            continue
        index += 1


def _table_cells(line: str) -> list[str]:
    return [item.strip() for item in line.strip().strip("|").split("|")]


def _background_fields(body: str) -> dict[str, Any]:
    skills = [
        item.strip().casefold()
        for item in (_plain_label(body, "Skill Proficiencies") or "").split(",")
        if item.strip()
    ]
    tools = [
        item.strip()
        for item in (_plain_label(body, "Tool Proficiencies") or "").split(",")
        if item.strip() and item.strip().casefold() != "none"
    ]
    language_text = _plain_label(body, "Languages") or ""
    language_count = _leading_count(language_text)
    feature = re.search(r"^###\s+Feature:\s*(.+?)\s*$", body, re.MULTILINE | re.IGNORECASE)
    equipment = _plain_label(body, "Equipment") or ""
    return {
        "skill_proficiencies": skills,
        "background_grants": {
            "feature": feature.group(1).strip() if feature else "",
            "languages": [],
            "tools": tools,
            "choices": {
                "language_count": language_count,
                "equipment_description": equipment,
            },
        },
    }


def _feat_prerequisites(body: str) -> list[dict[str, Any]]:
    line = re.search(r"^\*Prerequisite:\s*(.+?)\*\s*$", body, re.MULTILINE | re.IGNORECASE)
    if not line:
        return []
    ability = re.fullmatch(
        r"(Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma)\s+(\d+)\s+or\s+higher",
        line.group(1).strip(),
        re.IGNORECASE,
    )
    if ability:
        return [
            {
                "kind": "ability_minimum",
                "ability": ability.group(1).casefold(),
                "minimum": int(ability.group(2)),
            }
        ]
    return [{"kind": "dm_review", "text": line.group(1).strip()}]


def _plain_label(text: str, label: str) -> str:
    match = re.search(rf"^\*\*{re.escape(label)}:\*\*\s*(.+?)\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _leading_count(value: str) -> int:
    first = value.casefold().split(maxsplit=1)[0] if value.strip() else ""
    words = {"one": 1, "two": 2, "three": 3, "four": 4}
    return int(first) if first.isdigit() else words.get(first, 0)


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


def _name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def _description(text: str) -> str:
    return re.sub(r"^#{1,6}\s+.+?\s*$", "", text, count=1, flags=re.MULTILINE).strip()[:1200]


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
