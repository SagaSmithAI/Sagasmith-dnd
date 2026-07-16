"""Build portable, source-linked SRD content artifacts from bundled Markdown."""

from __future__ import annotations

import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

PACK_ID = "dnd5e.content.srd2014"
PACK_VERSION = "1.2.0"

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
    artifacts.extend(_species(root / "01_Races" / "Races_Each"))
    artifacts.extend(_classes(root / "02_Classes"))
    artifacts.extend(_class_features(root / "02_Classes"))
    artifacts.extend(_subclasses(root / "02_Classes"))
    artifacts.extend(_subclass_features(root / "02_Classes"))
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
                "feature",
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


def _classes(folder: Path) -> list[dict[str, Any]]:
    """Catalog base classes without pretending a prose card can build a character."""
    result = _simple_files(folder, "class")
    for artifact in result:
        artifact["application_state"] = "catalog_only"
    return result


def _class_features(folder: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in _markdown_files(folder):
        text = path.read_text(encoding="utf-8")
        class_name = _heading_or_stem(text, path)
        levels = _class_feature_levels(text)
        for title, body in _h3_sections_before_first_h2(text):
            minimum_level = levels.get(_feature_key(title))
            if minimum_level is None:
                continue
            card = {
                "name": title,
                "source_key": class_name,
                "class_name": class_name,
                "minimum_level": minimum_level,
                "description": body[:2000],
            }
            card.update(_known_feature_structure(class_name, title, body))
            result.append(
                _artifact(
                    "feature",
                    f"{class_name} {title}",
                    path,
                    card,
                )
            )
    return result


def _subclass_features(folder: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in _markdown_files(folder):
        text = path.read_text(encoding="utf-8")
        class_name = _heading_or_stem(text, path)
        for subclass_name, subclass_body in _subclass_sections(text):
            for title, body in _h4_sections(subclass_body):
                level = _level_from_feature_text(body)
                card = {
                    "name": title,
                    "source_key": subclass_name,
                    "class_name": class_name,
                    "subclass_name": subclass_name,
                    "minimum_level": level,
                    "description": body[:2000],
                }
                card.update(_known_feature_structure(class_name, title, body))
                result.append(
                    _artifact(
                        "feature",
                        f"{subclass_name} {title}",
                        path,
                        card,
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


def _species(folder: Path) -> list[dict[str, Any]]:
    """Compile complete SRD species variants and retain unresolved cards as catalog-only."""
    result: list[dict[str, Any]] = []
    for path in _markdown_files(folder):
        text = path.read_text(encoding="utf-8")
        base_name = _heading_or_stem(text, path)
        subraces = list(_h2_sections(text))
        base_body = text
        first_h2 = re.search(r"^##\s+", text, re.MULTILINE)
        if first_h2:
            base_body = text[: first_h2.start()]
        base_traits = _trait_paragraphs(base_body)
        if subraces:
            base_artifact = _species_artifact(base_name, base_name, base_traits, path)
            base_artifact["application_state"] = "catalog_only"
            result.append(base_artifact)
            for subrace_name, subrace_body in subraces:
                result.append(
                    _species_artifact(
                        subrace_name,
                        base_name,
                        [*base_traits, *_trait_paragraphs(subrace_body)],
                        path,
                    )
                )
        else:
            result.append(_species_artifact(base_name, base_name, base_traits, path))
    return result


def _species_artifact(
    name: str,
    base_species: str,
    traits: list[tuple[str, str]],
    path: Path,
) -> dict[str, Any]:
    grants = _species_grants(name, traits)
    card = {
        "name": name,
        "base_species": base_species,
        "description": "\n\n".join(f"{title}. {body}" for title, body in traits)[:4000],
        "grants": grants,
    }
    artifact = _artifact("species", name, path, card)
    if grants.get("unresolved"):
        artifact["application_state"] = "catalog_only"
    return artifact


def _species_grants(name: str, traits: list[tuple[str, str]]) -> dict[str, Any]:
    grants: dict[str, Any] = {
        "ability_score_increases": {},
        "ability_choice": {"count": 0, "amount": 0, "exclude": []},
        "size": "",
        "walk_speed": 0,
        "darkvision_ft": 0,
        "languages": [],
        "language_choice_count": 0,
        "skill_proficiencies": [],
        "skill_choice_count": 0,
        "weapon_proficiencies": [],
        "tool_proficiencies": [],
        "tool_choices": [],
        "cantrip_choice": None,
        "resistances": [],
        "hp_per_level": 0,
        "features": [],
        "unresolved": [],
    }
    slug = _name_key(name)
    for title, body in traits:
        key = title.casefold()
        if key == "ability score increase":
            fixed, choice = _ability_increases(body)
            for ability, amount in fixed.items():
                grants["ability_score_increases"][ability] = (
                    int(grants["ability_score_increases"].get(ability, 0)) + amount
                )
            if choice["count"]:
                grants["ability_choice"] = choice
            continue
        if key == "size":
            size = re.search(r"Your size is\s+(Tiny|Small|Medium|Large)", body, re.IGNORECASE)
            grants["size"] = size.group(1).title() if size else ""
            continue
        if key == "speed":
            speed = re.search(r"walking speed is\s+(\d+)\s+feet", body, re.IGNORECASE)
            grants["walk_speed"] = int(speed.group(1)) if speed else 0
            continue
        if key == "darkvision":
            distance = re.search(r"within\s+(\d+)\s+feet", body, re.IGNORECASE)
            grants["darkvision_ft"] = int(distance.group(1)) if distance else 60
            continue
        if key == "languages" or key == "extra language":
            languages, choices = _language_grants(body)
            grants["languages"] = list(dict.fromkeys([*grants["languages"], *languages]))
            grants["language_choice_count"] += choices
            continue
        if key in {"keen senses", "menacing"}:
            skill = "perception" if key == "keen senses" else "intimidation"
            grants["skill_proficiencies"].append(skill)
        elif key == "skill versatility":
            grants["skill_choice_count"] = 2
        elif "weapon training" in key or key == "dwarven combat training":
            grants["weapon_proficiencies"].extend(_listed_proficiencies(body))
        elif key == "tool proficiency":
            grants["tool_choices"] = _tool_options(body)
        elif key == "tinker":
            grants["tool_proficiencies"].append("tinker's tools")
        elif key == "dwarven resilience":
            grants["resistances"].append("poison")
        elif key == "hellish resistance":
            grants["resistances"].append("fire")
        elif key == "dwarven toughness":
            grants["hp_per_level"] = 1
        elif key == "cantrip":
            grants["cantrip_choice"] = {"class": "wizard", "level": 0}
        elif key == "draconic ancestry":
            grants["unresolved"].append("draconic_ancestry")
        elif key in {"breath weapon", "damage resistance"} and "draconic_ancestry" in grants[
            "unresolved"
        ]:
            grants["unresolved"].append(_name_key(title))
        elif key == "infernal legacy":
            grants["unresolved"].append("level_granted_species_spells")
        if key not in {
            "age",
            "alignment",
            "size",
            "speed",
            "darkvision",
            "languages",
            "extra language",
            "ability score increase",
        }:
            grants["features"].append(
                {
                    "id": f"{PACK_ID}.species-feature.{slug}-{_name_key(title)}",
                    "name": title,
                    "source_key": name,
                    "description": body[:2000],
                    "activation": {"type": "passive"},
                }
            )
    for list_key in (
        "skill_proficiencies",
        "weapon_proficiencies",
        "tool_proficiencies",
        "resistances",
        "unresolved",
    ):
        grants[list_key] = list(dict.fromkeys(grants[list_key]))
    return grants


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


def _h3_sections_before_first_h2(text: str) -> Iterable[tuple[str, str]]:
    end = re.search(r"^##\s+", text, re.MULTILINE)
    head = text[: end.start()] if end else text
    matches = list(re.finditer(r"^###\s+(.+?)\s*$", head, re.MULTILINE))
    for index, match in enumerate(matches):
        section_end = matches[index + 1].start() if index + 1 < len(matches) else len(head)
        yield match.group(1).strip(), head[match.end() : section_end].strip()


def _h4_sections(text: str) -> Iterable[tuple[str, str]]:
    matches = list(re.finditer(r"^####\s+(.+?)\s*$", text, re.MULTILINE))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        yield match.group(1).strip(), text[match.end() : end].strip()


def _class_feature_levels(text: str) -> dict[str, int]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        headers = _table_cells(line)
        if "Level" not in headers or "Features" not in headers:
            continue
        result: dict[str, int] = {}
        row_index = index + 2
        while row_index < len(lines) and lines[row_index].lstrip().startswith("|"):
            values = _table_cells(lines[row_index])
            row = dict(zip(headers, values, strict=False))
            level_match = re.match(r"(\d+)", row.get("Level", ""))
            if level_match:
                level = int(level_match.group(1))
                for feature in row.get("Features", "").split(","):
                    key = _feature_key(feature)
                    if key and key != "-":
                        result.setdefault(key, level)
            row_index += 1
        return result
    return {}


def _feature_key(value: str) -> str:
    normalized = re.sub(r"\s*\([^)]*\)\s*", " ", value).strip().casefold()
    normalized = re.sub(r"\s+improvement$", "", normalized)
    return re.sub(r"\s+", " ", normalized)


def _level_from_feature_text(body: str) -> int:
    patterns = (
        r"(?:at|when you reach|starting at|beginning at)\s+(\d+)(?:st|nd|rd|th)\s+level",
        r"when you choose .+? at\s+(\d+)(?:st|nd|rd|th)\s+level",
    )
    for pattern in patterns:
        match = re.search(pattern, body, re.IGNORECASE | re.DOTALL)
        if match:
            return int(match.group(1))
    return 1


def _known_feature_structure(class_name: str, title: str, body: str) -> dict[str, Any]:
    key = (class_name.casefold(), title.casefold())
    if key == ("fighter", "second wind"):
        return {
            "activation": {"type": "bonus_action", "cost": 1},
            "uses": {
                "label": "Second Wind",
                "value": 1,
                "max": 1,
                "recovers_on": "short_rest",
            },
            "choices": {"outcome": "roll 1d10 + fighter level, then apply healing"},
        }
    if title.casefold() == "fighting style":
        options = [name for name, _ in _h4_sections(body)]
        return {
            "selection_requirements": {
                "field": "option",
                "count": 1,
                "options": options,
            }
        }
    if key == ("rogue", "expertise"):
        return {
            "selection_requirements": {
                "field": "proficiencies",
                "count": 2,
                "requires_existing_proficiency": True,
            }
        }
    return {}


def _trait_paragraphs(text: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"^\*\*\*(.+?)\*\*\*\.\s*", text, re.MULTILINE))
    result = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        result.append((match.group(1).strip(), text[match.end() : end].strip()))
    return result


def _ability_increases(body: str) -> tuple[dict[str, int], dict[str, Any]]:
    abilities = ("Strength", "Dexterity", "Constitution", "Intelligence", "Wisdom", "Charisma")
    fixed: dict[str, int] = {}
    if re.search(r"ability scores each increase by\s+1", body, re.IGNORECASE):
        fixed = {ability.casefold(): 1 for ability in abilities}
    else:
        ability_pattern = (
            r"(Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma) "
            r"score increases by\s+(\d+)"
        )
        for ability, amount in re.findall(
            ability_pattern,
            body,
            re.IGNORECASE,
        ):
            fixed[ability.casefold()] = int(amount)
    choice = {"count": 0, "amount": 0, "exclude": sorted(fixed)}
    choice_match = re.search(
        r"(one|two|three|\d+) other ability scores? of your choice increase by\s+(\d+)",
        body,
        re.IGNORECASE,
    )
    if choice_match:
        choice["count"] = _leading_count(choice_match.group(1))
        choice["amount"] = int(choice_match.group(2))
    return fixed, choice


def _language_grants(body: str) -> tuple[list[str], int]:
    match = re.search(r"speak, read, and write\s+(.+?)(?:\.|$)", body, re.IGNORECASE)
    if not match:
        return [], 0
    value = match.group(1)
    choices = len(re.findall(r"one (?:extra )?language of your choice", value, re.IGNORECASE))
    value = re.sub(
        r",?\s*and\s+one (?:extra )?language of your choice|one (?:extra )?language of your choice",
        "",
        value,
        flags=re.IGNORECASE,
    )
    names = [
        item.strip().title()
        for item in re.split(r",|\band\b", value, flags=re.IGNORECASE)
        if item.strip()
    ]
    return names, choices


def _listed_proficiencies(body: str) -> list[str]:
    match = re.search(r"proficiency with\s+(.+?)(?:\.|$)", body, re.IGNORECASE)
    if not match:
        return []
    return [
        re.sub(r"^(?:the|or)\s+", "", item.strip(), flags=re.IGNORECASE).casefold()
        for item in re.split(r",|\band\b|\bor\b", match.group(1), flags=re.IGNORECASE)
        if item.strip()
    ]


def _tool_options(body: str) -> list[str]:
    match = re.search(r"choice:\s*(.+?)(?:\.|$)", body, re.IGNORECASE)
    return _listed_proficiencies(f"proficiency with {match.group(1)}.") if match else []


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
