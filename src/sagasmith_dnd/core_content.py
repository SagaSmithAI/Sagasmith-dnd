"""Build portable, source-linked SRD content artifacts from bundled Markdown."""

from __future__ import annotations

import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from sagasmith_core.text import ascii_slug

from sagasmith_dnd.content_import import audit_release_resolution_readiness
from sagasmith_dnd.content_resolution import finalize_bundled_artifact_resolutions
from sagasmith_dnd.spell_resolution import (
    SPELL_RESOLUTION_MECHANIC_ID,
    known_spell_resolution,
)
from sagasmith_dnd.standard_feature_ids import (
    CORE_RELENTLESS_ENDURANCE_MECHANIC_ID,
)
from sagasmith_dnd.standard_spell_ids import (
    CORE_FLY_MECHANIC_ID,
    CORE_HYPNOTIC_PATTERN_MECHANIC_ID,
    CORE_INVISIBILITY_MECHANIC_ID,
)

PACK_ID = "dnd5e.content.srd2014"
PACK_VERSION = "1.21.0"

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
    artifacts = finalize_bundled_artifact_resolutions(
        _deduplicate(artifacts),
        source_root=root,
        source_prefix="bundled:srd2014/",
    )
    native_mechanic_refs = sorted(
        {
            str(mechanic_ref)
            for artifact in artifacts
            for mechanic_ref in artifact.get("mechanic_refs", [])
            if str(mechanic_ref)
        }
    )
    resolution_readiness = audit_release_resolution_readiness(artifacts)
    return (
        {
            "id": PACK_ID,
            "version": PACK_VERSION,
            "title": "D&D 5e SRD 2014 Structured Content",
            "namespace": PACK_ID,
            "system_id": "dnd5e",
            "editions": ["2014"],
            "capabilities": [],
            "native_mechanic_refs": native_mechanic_refs,
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
            "resolution_policy": "build_time_complete",
            "resolution_readiness": resolution_readiness,
        },
        artifacts,
    )


def _spells(folder: Path, spell_classes: dict[str, list[str]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in _markdown_files(folder):
        text = path.read_text(encoding="utf-8")
        name = _heading_or_stem(text, path)
        level, school = _spell_level_school(text)
        card = {
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
        }
        mechanic_refs: list[str] = []
        resolution = known_spell_resolution(name)
        if resolution is not None:
            card["resolution"] = resolution
            mechanic_refs.append(SPELL_RESOLUTION_MECHANIC_ID)
        if _name_key(name) == "shield":
            mechanic_refs.append("dnd5e.core.spell.shield")
        elif _name_key(name) == "magic-missile":
            mechanic_refs.append("dnd5e.core.spell.magic_missile")
        elif _name_key(name) == "mage-armor":
            mechanic_refs.append("dnd5e.core.spell.mage_armor")
        elif _name_key(name) == "fly":
            mechanic_refs.append(CORE_FLY_MECHANIC_ID)
        elif _name_key(name) == "invisibility":
            mechanic_refs.append(CORE_INVISIBILITY_MECHANIC_ID)
        elif _name_key(name) == "raise-dead":
            mechanic_refs.append("dnd5e.core.spell.raise_dead")
        elif _name_key(name) == "hypnotic-pattern":
            mechanic_refs.append(CORE_HYPNOTIC_PATTERN_MECHANIC_ID)
        if mechanic_refs:
            card["mechanic_refs"] = mechanic_refs
        if resolution is None and not mechanic_refs:
            card["ruling_requirements"] = [
                {
                    "kind": "effect_semantics",
                    "reason": (
                        "Apply this exact source-bound spell through the persisted "
                        "Agent-as-DM clause and public engine operations."
                    ),
                    "source_excerpt": str(card["definition"]["effect"])[:4000],
                    "default_resolver": "agent",
                    "ruling_kind": "generic_spell_effect",
                    "policy_ref": "rule_clause.v1",
                    "requires_external_input_only_for": [
                        "player_owned_choice",
                        "owner_approval",
                        "permission_escalation",
                        "missing_or_conflicting_source_review",
                    ],
                }
            ]
        result.append(
            _artifact(
                "spell",
                name,
                path,
                card,
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
        resource_structures = _class_resource_structures(class_name, text)
        sections: dict[str, tuple[str, list[str]]] = {}
        for title, body in _h3_sections_before_first_h2(text):
            key = _feature_key(title)
            if key in sections:
                sections[key][1].append(body)
            else:
                sections[key] = (title, [body])
        for feature_key, (title, bodies) in sections.items():
            body = "\n\n".join(bodies)
            unlock_levels = levels.get(feature_key)
            if not unlock_levels:
                continue
            card = {
                "name": title,
                "source_key": class_name,
                "class_name": class_name,
                "minimum_level": unlock_levels[0],
                "unlock_levels": unlock_levels,
                "description": body[:2000],
            }
            card.update(_known_feature_structure(class_name, title, body))
            card.update(resource_structures.get(feature_key, {}))
            if len(unlock_levels) > 1 and card.get("selection_requirements"):
                card["repeatable_selection_levels"] = unlock_levels
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
        subclass_floor = _SUBCLASS_LEVELS.get(class_name.casefold(), 1)
        for subclass_name, subclass_body in _subclass_sections(text):
            for title, body in _subclass_feature_sections(subclass_body):
                # Some source-authored subclass sections (for example, oath
                # tenets and spell tables) do not repeat the level in prose.
                # A missing prose match must never unlock a subclass feature
                # before the class can select that subclass.
                level = max(subclass_floor, _level_from_feature_text(body))
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
                        "spell_grants": [
                            {**grant, "method": "always_prepared"}
                            for grant in (
                                _subclass_spell_grants(body)
                                if class_name.casefold() in {"cleric", "paladin"}
                                else []
                            )
                        ],
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
    card["mechanic_refs"] = list(
        dict.fromkeys(
            str(mechanic_ref)
            for feature in grants.get("features", [])
            for mechanic_ref in feature.get("mechanic_refs", [])
            if str(mechanic_ref)
        )
    )
    artifact = _artifact("species", name, path, card)
    if grants.get("unresolved"):
        artifact["application_state"] = "catalog_only"
    return artifact


def _species_grants(name: str, traits: list[tuple[str, str]]) -> dict[str, Any]:
    grants: dict[str, Any] = {
        "ability_score_increases": {},
        "ability_choice": {"count": 0, "amount": 0, "exclude": [], "options": []},
        "size": "",
        "size_options": [],
        "walk_speed": 0,
        "swim_speed": 0,
        "darkvision_ft": 0,
        "languages": [],
        "language_choice_count": 0,
        "language_options": [],
        "allow_any_language": False,
        "skill_proficiencies": [],
        "skill_choice_count": 0,
        "skill_options": [],
        "allow_any_skill": False,
        "armor_proficiencies": [],
        "weapon_proficiencies": [],
        "tool_proficiencies": [],
        "tool_choices": [],
        "tool_choice_count": 0,
        "tool_options": [],
        "proficiency_choice_groups": [],
        "narrative_choice_groups": [],
        "tool_expertise_choice_count": 0,
        "tool_expertise_options": [],
        "allow_any_proficient_tool_expertise": False,
        "cantrip_choice": None,
        "spell_grants": [],
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
            if choices:
                grants["allow_any_language"] = True
            continue
        if key in {"keen senses", "menacing"}:
            skill = "perception" if key == "keen senses" else "intimidation"
            grants["skill_proficiencies"].append(skill)
        elif key == "skill versatility":
            grants["skill_choice_count"] = 2
            grants["allow_any_skill"] = True
        elif "weapon training" in key or key == "dwarven combat training":
            grants["weapon_proficiencies"].extend(_listed_proficiencies(body))
        elif key == "tool proficiency":
            grants["tool_choices"] = _tool_options(body)
            grants["tool_options"] = list(grants["tool_choices"])
            grants["tool_choice_count"] = 1 if grants["tool_choices"] else 0
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
        elif (
            key in {"breath weapon", "damage resistance"}
            and "draconic_ancestry" in grants["unresolved"]
        ):
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
            feature = {
                "id": f"{PACK_ID}.species-feature.{slug}-{_name_key(title)}",
                "name": title,
                "source_key": name,
                "description": body[:2000],
                "activation": {"type": "passive"},
                "mechanic_refs": [],
            }
            if name == "Half-Orc" and key == "relentless endurance":
                feature.update(
                    uses={
                        "label": title,
                        "value": 1,
                        "max": 1,
                        "recovers_on": "long_rest",
                        "source_key": name,
                        "slot_level": 0,
                        "unlimited": False,
                    },
                    choices={
                        "source_trait": {
                            "kind": "relentless_endurance",
                            "trigger": "reduced_to_zero_not_killed_outright",
                            "result_hp": 1,
                            "automatic": True,
                            "source_excerpt": body[:2000],
                        }
                    },
                    mechanic_refs=[CORE_RELENTLESS_ENDURANCE_MECHANIC_ID],
                )
            grants["features"].append(feature)
    for list_key in (
        "skill_proficiencies",
        "armor_proficiencies",
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


def _subclass_feature_sections(text: str) -> Iterable[tuple[str, str]]:
    """Read subclass features despite the SRD Oath heading-depth typo."""
    matches = list(re.finditer(r"^#{4,5}\s+(.+?)\s*$", text, re.MULTILINE))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        yield match.group(1).strip(), text[match.end() : end].strip()


def _class_feature_levels(text: str) -> dict[str, list[int]]:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        headers = _table_cells(line)
        if "Level" not in headers or "Features" not in headers:
            continue
        result: dict[str, list[int]] = {}
        row_index = index + 2
        while row_index < len(lines) and lines[row_index].lstrip().startswith("|"):
            values = _table_cells(lines[row_index])
            row = dict(zip(headers, values, strict=False))
            level_match = re.match(r"(\d+)", row.get("Level", ""))
            if level_match:
                level = int(level_match.group(1))
                feature_names: list[str] = []
                for feature in row.get("Features", "").split(","):
                    composite = re.fullmatch(
                        r"\s*(.+?)\s+and\s+(.+?)\s+improvements?\s*",
                        feature,
                        re.IGNORECASE,
                    )
                    if composite:
                        feature_names.extend([composite.group(1), composite.group(2)])
                    else:
                        feature_names.append(feature)
                for feature in feature_names:
                    key = _feature_key(feature)
                    if key and key != "-":
                        unlocks = result.setdefault(key, [])
                        if level not in unlocks:
                            unlocks.append(level)
            row_index += 1
        return result
    return {}


def _feature_key(value: str) -> str:
    normalized = re.sub(r"\s*\([^)]*\)\s*", " ", value).strip().casefold()
    normalized = re.sub(r"\s+improvement$", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return {
        # The class table abbreviates the full feature heading.
        "relentless": "relentless rage",
        # The source table uses the singular while its feature heading and
        # mechanics explicitly select two spells.
        "signature spell": "signature spells",
    }.get(normalized, normalized)


def _level_from_feature_text(body: str) -> int:
    patterns = (
        r"(?:at|by|when you reach|starting at|beginning at)\s+"
        r"(\d+)(?:st|nd|rd|th)\s+level",
        r"when you choose .+? at\s+(\d+)(?:st|nd|rd|th)\s+level",
    )
    for pattern in patterns:
        match = re.search(pattern, body, re.IGNORECASE | re.DOTALL)
        if match:
            return int(match.group(1))
    return 1


def _known_feature_structure(class_name: str, title: str, body: str) -> dict[str, Any]:
    key = (class_name.casefold(), title.casefold())
    if title.casefold() == "ability score improvement":
        return {
            "selection_requirements": {
                "field": "ability_score_increases",
                "kind": "ability_score_increase",
                "allowed_distributions": [[2], [1, 1]],
                "maximum_score": 20,
            }
        }
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
            "mechanic_refs": ["dnd5e.core.activity.second_wind"],
        }
    if key == ("fighter", "action surge"):
        return {
            "activation": {"type": "special", "cost": 0},
            "uses": {
                "label": "Action Surge",
                "value": 1,
                "max": 1,
                "recovers_on": "short_rest",
            },
            "choices": {"outcome": "take one additional action on the current turn"},
            "mechanic_refs": ["dnd5e.core.activity.action_surge"],
        }
    if key == ("wizard", "arcane recovery"):
        reset_on_long_rest = bool(
            re.search(
                r"(?:until|after)\s+you\s+finish\s+a\s+long\s+rest",
                body,
                re.IGNORECASE,
            )
        )
        return {
            "uses": {
                "label": "Arcane Recovery",
                "value": 1,
                "max": 1,
                "recovers_on": "long_rest" if reset_on_long_rest else "manual",
                "source_key": "Wizard",
            }
        }
    if key == ("rogue", "cunning action"):
        return {
            "activation": {"type": "bonus_action", "cost": 1},
            "choices": {"options": ["Dash", "Disengage", "Hide"]},
            "mechanic_refs": ["dnd5e.core.activity.cunning_action"],
        }
    if key == ("cleric", "channel divinity"):
        return {
            "activation": {"type": "action", "cost": 1},
            "resource_key": "channel_divinity",
            "mechanical_grants": {
                "resources": {
                    "channel_divinity": {
                        "label": "Channel Divinity",
                        "value": 1,
                        "max": 1,
                        "recovers_on": "short_rest",
                        "source_key": "Cleric",
                    }
                }
            },
            "choices": {"options": ["Turn Undead", "selected-domain option"]},
            "mechanic_refs": ["dnd5e.core.activity.turn_undead"],
        }
    if key == ("paladin", "channel divinity"):
        return {
            "activation": {"type": "action", "cost": 1},
            "resource_key": "channel_divinity",
            "mechanical_grants": {
                "resources": {
                    "channel_divinity": {
                        "label": "Channel Divinity",
                        "value": 1,
                        "max": 1,
                        "unlimited": False,
                        "recovers_on": "short_rest",
                        "source_key": "Paladin",
                    }
                }
            },
            "choices": {"options": [name for name, _ in _trait_paragraphs(body)]},
        }
    if key == ("cleric", "channel divinity: preserve life"):
        return {
            "activation": {"type": "action", "cost": 1},
            "resource_key": "channel_divinity",
            "choices": {
                "outcome": (
                    "restore up to five times cleric level hit points among creatures "
                    "within 30 feet, never above half maximum hit points"
                )
            },
            "mechanic_refs": ["dnd5e.core.activity.preserve_life"],
        }
    if key == ("bard", "jack of all trades"):
        return {"mechanic_refs": ["dnd5e.core.check.jack_of_all_trades"]}
    if key == ("rogue", "sneak attack"):
        return {"mechanic_refs": ["dnd5e.core.attack.sneak_attack"]}
    if key == ("rogue", "evasion"):
        return {
            "choices": {
                "source_trait": {
                    "kind": "evasion",
                    "trigger": "dexterity_save_for_half_damage",
                    "save_ability": "dexterity",
                    "ordinary_successful_save": "half",
                    "successful_save": "none",
                    "failed_save": "half",
                    "unavailable_conditions": ["incapacitated"],
                    "automatic": True,
                    "source_excerpt": body[:4000],
                }
            },
            "mechanic_refs": ["dnd5e.core.save.evasion"],
        }
    if key == ("barbarian", "unarmored defense"):
        return {
            "mechanical_grants": {
                "unarmored_formula": {
                    "base": 10,
                    "ability": "constitution",
                    "allows_shield": True,
                }
            }
        }
    if key == ("monk", "unarmored defense"):
        return {
            "mechanical_grants": {
                "unarmored_formula": {
                    "base": 10,
                    "ability": "wisdom",
                    "allows_shield": False,
                }
            }
        }
    if title.casefold() == "extra attack":
        maximums_by_class = {
            "barbarian": {5: 2},
            "fighter": {5: 2, 11: 3, 20: 4},
            "monk": {5: 2},
            "paladin": {5: 2},
            "ranger": {5: 2},
        }
        maximums = maximums_by_class.get(class_name.casefold())
        if maximums:
            return {
                "attack_scaling": {
                    "class_name": class_name,
                    "attacks_per_action_by_level": {
                        str(level): amount for level, amount in maximums.items()
                    },
                }
            }
    if key == ("sorcerer", "draconic resilience"):
        return {
            "mechanical_grants": {
                "hp_per_class_level": 1,
                "unarmored_base": 13,
            }
        }
    if title.casefold() == "fighting style":
        options = [name for name, _ in _h4_sections(body)]
        return {
            "selection_requirements": {
                "field": "option",
                "count": 1,
                "options": options,
                "requires_new_choice": True,
                "choice_uniqueness_scope": "fighting_style",
            }
        }
    if key == ("fighter", "additional fighting style"):
        return {
            "selection_requirements": {
                "field": "option",
                "count": 1,
                "options": [
                    "Archery",
                    "Defense",
                    "Dueling",
                    "Great Weapon Fighting",
                    "Protection",
                    "Two-Weapon Fighting",
                ],
                "requires_new_choice": True,
                "choice_uniqueness_scope": "fighting_style",
            }
        }
    if key == ("rogue", "expertise"):
        return {
            "selection_requirements": {
                "field": "proficiencies",
                "count": 2,
                "requires_existing_proficiency": True,
                "requires_new_expertise": True,
            }
        }
    if key == ("bard", "expertise"):
        return {
            "selection_requirements": {
                "field": "proficiencies",
                "count": 2,
                "requires_existing_proficiency": True,
                "requires_new_expertise": True,
                "skills_only": True,
            }
        }
    if key == ("bard", "bonus proficiencies"):
        return {
            "selection_requirements": {
                "field": "skills",
                "count": 3,
                "requires_untrained_skill": True,
                "grants_skill_proficiency": True,
            }
        }
    if key == ("ranger", "natural explorer"):
        return {
            "selection_requirements": {
                "field": "terrain",
                "count": 1,
                "options": [
                    "Arctic",
                    "Coast",
                    "Desert",
                    "Forest",
                    "Grassland",
                    "Mountain",
                    "Swamp",
                ],
                "requires_new_choice": True,
                "choice_uniqueness_scope": "ranger_natural_explorer",
            },
            "repeatable_selection_levels": [1, 6, 10],
        }
    if key == ("ranger", "favored enemy"):
        return {
            "selection_requirements": {
                "field": "favored_enemy",
                "kind": "favored_enemy",
                "creature_type_options": [
                    "Aberrations",
                    "Beasts",
                    "Celestials",
                    "Constructs",
                    "Dragons",
                    "Elementals",
                    "Fey",
                    "Fiends",
                    "Giants",
                    "Monstrosities",
                    "Oozes",
                    "Plants",
                    "Undead",
                ],
                "humanoid_race_count": 2,
                "language_if_spoken": True,
            },
            "repeatable_selection_levels": [1, 6, 14],
        }
    if key == ("sorcerer", "metamagic"):
        options = [name for name, _ in _h4_sections(body)]
        base = {
            "field": "options",
            "count": 2,
            "options": options,
            "requires_new_choice": True,
            "choice_uniqueness_scope": "sorcerer_metamagic",
        }
        return {
            "selection_requirements": base,
            "selection_requirements_by_level": {
                "3": base,
                "10": {**base, "count": 1},
                "17": {**base, "count": 1},
            },
            "repeatable_selection_levels": [3, 10, 17],
        }
    if key == ("warlock", "pact boon"):
        return {
            "selection_requirements": {
                "field": "option",
                "count": 1,
                "options": [name for name, _ in _h4_sections(body)],
            }
        }
    if key in {
        ("bard", "magical secrets"),
        ("bard", "additional magical secrets"),
    }:
        return {
            "selection_requirements": {
                "field": "spell_artifact_ids",
                "kind": "known_spell_grants",
                "count": 2,
                "eligible_class": "any",
                "maximum_spell_level": "available_slots",
                "source_class": "bard",
            }
        }
    if key == ("warlock", "mystic arcanum"):
        base = {
            "field": "spell_artifact_ids",
            "kind": "mystic_arcanum",
            "count": 1,
            "eligible_class": "warlock",
        }
        return {
            "selection_requirements": {**base, "spell_level": 6},
            "selection_requirements_by_level": {
                "11": {**base, "spell_level": 6},
                "13": {**base, "spell_level": 7},
                "15": {**base, "spell_level": 8},
                "17": {**base, "spell_level": 9},
            },
            "repeatable_selection_levels": [11, 13, 15, 17],
        }
    if key == ("wizard", "spell mastery"):
        return {
            "selection_requirements": {
                "field": "spell_artifact_ids",
                "kind": "spell_mastery",
                "count": 2,
                "eligible_class": "wizard",
                "required_spell_levels": [1, 2],
                "requires_spellbook": True,
                "replacement_study_minutes": 480,
            }
        }
    if key == ("wizard", "signature spells"):
        return {
            "selection_requirements": {
                "field": "spell_artifact_ids",
                "kind": "signature_spells",
                "count": 2,
                "eligible_class": "wizard",
                "required_spell_levels": [3, 3],
                "requires_spellbook": True,
            }
        }
    if key == ("warlock", "eldritch invocations"):
        options = list(_h4_sections(body))
        option_prerequisites: dict[str, dict[str, Any]] = {}
        at_will_spells: dict[str, str] = {}
        for name, option_body in options:
            prerequisite = re.search(
                r"\*Prerequisite:\s*(.+?)\*",
                option_body,
                re.IGNORECASE,
            )
            metadata: dict[str, Any] = {}
            if prerequisite:
                text = prerequisite.group(1)
                level = re.search(
                    r"(\d+)(?:st|nd|rd|th)\s+level",
                    text,
                    re.IGNORECASE,
                )
                if level:
                    metadata["minimum_level"] = int(level.group(1))
                pact = re.search(
                    r"Pact of the (Chain|Blade|Tome)",
                    text,
                    re.IGNORECASE,
                )
                if pact:
                    metadata["required_pact_boon"] = f"Pact of the {pact.group(1).title()}"
                spell = re.search(
                    r"([A-Za-z' -]+)\s+cantrip",
                    text,
                    re.IGNORECASE,
                )
                if spell:
                    metadata["required_cantrip"] = spell.group(1).strip().title()
            if metadata:
                option_prerequisites[name] = metadata
            at_will = re.search(
                r"cast\s+\*([^*]+)\*.+?at will",
                option_body,
                re.IGNORECASE | re.DOTALL,
            )
            if at_will:
                at_will_spells[name] = at_will.group(1).strip()
        base = {
            "field": "options",
            "count": 1,
            "options": [name for name, _ in options],
            "requires_new_choice": True,
            "choice_uniqueness_scope": "warlock_eldritch_invocation",
            "option_prerequisites": option_prerequisites,
            "at_will_spells": at_will_spells,
        }
        levels = [2, 5, 7, 9, 12, 15, 18]
        return {
            "selection_requirements": {**base, "count": 2},
            "selection_requirements_by_level": {
                str(level): {**base, "count": 2 if level == 2 else 1} for level in levels
            },
            "repeatable_selection_levels": levels,
        }
    if key == ("druid", "bonus cantrip"):
        return {
            "selection_requirements": {
                "field": "spell_artifact_id",
                "kind": "bonus_cantrip",
                "spell_level": 0,
                "eligible_class": "druid",
            }
        }
    if key == ("sorcerer", "dragon ancestor"):
        ancestry = {
            fields["Dragon"]: fields["Damage Type"]
            for table_name, fields in _markdown_table_rows(body)
            if table_name == "Draconic Ancestry"
            and fields.get("Dragon")
            and fields.get("Damage Type")
        }
        return {
            "selection_requirements": {
                "field": "option",
                "count": 1,
                "options": list(ancestry),
            },
            "choice_metadata": {"damage_type_by_option": ancestry},
            "mechanical_grants": {"languages": ["Draconic"]},
        }
    if class_name.casefold() == "ranger" and key[1] in {
        "hunter's prey",
        "defensive tactics",
        "multiattack",
        "superior hunter's defense",
    }:
        return {
            "selection_requirements": {
                "field": "option",
                "count": 1,
                "options": [name for name, _ in _trait_paragraphs(body)],
            }
        }
    if key == ("druid", "circle spells"):
        spell_options = _subclass_spell_options(body, table_suffix="Circle Spells")
        return {
            "selection_requirements": {
                "field": "option",
                "count": 1,
                "options": list(spell_options),
            },
            "always_prepared_spell_options": spell_options,
        }
    if key == ("cleric", "bonus proficiency") and "heavy armor" in body.casefold():
        return {"mechanical_grants": {"armor_proficiencies": ["heavy armor"]}}
    return {}


def _class_resource_structures(class_name: str, text: str) -> dict[str, dict[str, Any]]:
    """Compile deterministic class-table resource growth into portable cards."""

    rows = _primary_class_table_rows(text)
    if not rows:
        return {}
    key = class_name.casefold()
    result: dict[str, dict[str, Any]] = {}

    def add_uses(
        feature: str,
        *,
        label: str,
        maximum_by_level: dict[int, int],
        recovers_on: str,
        recovery_by_level: dict[int, str] | None = None,
        unlimited_at_level: int | None = None,
        maximum_formula: dict[str, Any] | None = None,
        activation: dict[str, Any] | None = None,
    ) -> None:
        scaling = _resource_scaling(
            target="uses",
            label=label,
            class_name=class_name,
            maximum_by_level=maximum_by_level,
            recovers_on=recovers_on,
            recovery_by_level=recovery_by_level,
            unlimited_at_level=unlimited_at_level,
            maximum_formula=maximum_formula,
        )
        initial = _initial_resource_from_scaling(scaling)
        structure: dict[str, Any] = {
            "uses": initial,
            "resource_scaling": scaling,
        }
        if activation:
            structure["activation"] = activation
        result[feature] = structure

    def add_shared(
        feature: str,
        *,
        resource_key: str,
        label: str,
        maximum_by_level: dict[int, int],
        recovers_on: str,
        recovery_requirements: dict[str, Any] | None = None,
        unlimited_at_level: int | None = None,
        maximum_formula: dict[str, Any] | None = None,
        activation: dict[str, Any] | None = None,
    ) -> None:
        scaling = _resource_scaling(
            target=resource_key,
            label=label,
            class_name=class_name,
            maximum_by_level=maximum_by_level,
            recovers_on=recovers_on,
            unlimited_at_level=unlimited_at_level,
            maximum_formula=maximum_formula,
        )
        structure = {
            "resource_key": resource_key,
            "mechanical_grants": {
                "resources": {
                    resource_key: {
                        **_initial_resource_from_scaling(scaling),
                        **(
                            {"recovery_requirements": recovery_requirements}
                            if recovery_requirements
                            else {}
                        ),
                    }
                }
            },
            "resource_scaling": scaling,
        }
        if activation:
            structure["activation"] = activation
        result[feature] = structure

    if key == "barbarian":
        rage_maximum, rage_unlimited = _numeric_column_scaling(rows, "Rages")
        add_uses(
            "rage",
            label="Rage",
            maximum_by_level=rage_maximum,
            recovers_on="long_rest",
            unlimited_at_level=rage_unlimited,
            activation={"type": "bonus_action", "cost": 1},
        )
        result["rage"]["scaling"] = _numeric_column_display_scaling(
            rows, "Rage Damage", "rage damage bonus"
        )
    elif key == "bard":
        add_uses(
            "bardic inspiration",
            label="Bardic Inspiration",
            maximum_by_level={},
            recovers_on="long_rest",
            recovery_by_level={5: "short_rest"},
            maximum_formula={
                "kind": "ability_modifier",
                "ability": "charisma",
                "minimum": 1,
                "multiplier": 1,
                "offset": 0,
            },
            activation={"type": "bonus_action", "cost": 1},
        )
        result["bardic inspiration"]["scaling"] = _feature_display_scaling(
            rows, "Bardic Inspiration", "inspiration die"
        )
        result["song of rest"] = {
            "scaling": _feature_display_scaling(rows, "Song of Rest", "rest healing die")
        }
    elif key == "cleric":
        channel_maximum = _feature_use_scaling(rows, "Channel Divinity")
        add_shared(
            "channel divinity",
            resource_key="channel_divinity",
            label="Channel Divinity",
            maximum_by_level=channel_maximum,
            recovers_on="short_rest",
            activation={"type": "action", "cost": 1},
        )
    elif key == "druid":
        add_uses(
            "wild shape",
            label="Wild Shape",
            maximum_by_level={2: 2},
            recovers_on="short_rest",
            unlimited_at_level=20,
            activation={"type": "action", "cost": 1},
        )
    elif key == "fighter":
        add_uses(
            "action surge",
            label="Action Surge",
            maximum_by_level=_feature_use_scaling(rows, "Action Surge"),
            recovers_on="short_rest",
            activation={"type": "special", "cost": 0},
        )
        add_uses(
            "indomitable",
            label="Indomitable",
            maximum_by_level=_feature_use_scaling(rows, "Indomitable"),
            recovers_on="long_rest",
            activation={"type": "special", "cost": 0},
        )
    elif key == "monk":
        ki_maximum, _ = _numeric_column_scaling(rows, "Ki Points")
        add_shared(
            "ki",
            resource_key="ki",
            label="Ki Points",
            maximum_by_level=ki_maximum,
            recovers_on="short_rest",
            recovery_requirements={
                "activity_minutes": {"meditation": 30},
            },
        )
        result["martial arts"] = {
            "scaling": _numeric_column_display_scaling(rows, "Martial Arts", "martial arts die")
        }
    elif key == "paladin":
        add_uses(
            "divine sense",
            label="Divine Sense",
            maximum_by_level={},
            recovers_on="long_rest",
            maximum_formula={
                "kind": "ability_modifier",
                "ability": "charisma",
                "minimum": 0,
                "multiplier": 1,
                "offset": 1,
            },
            activation={"type": "action", "cost": 1},
        )
        add_shared(
            "lay on hands",
            resource_key="lay_on_hands",
            label="Lay on Hands",
            maximum_by_level={},
            recovers_on="long_rest",
            maximum_formula={
                "kind": "class_level",
                "minimum": 5,
                "multiplier": 5,
                "offset": 0,
            },
            activation={"type": "action", "cost": 1},
        )
    elif key == "sorcerer":
        sorcery_maximum, _ = _numeric_column_scaling(rows, "Sorcery Points")
        add_shared(
            "font of magic",
            resource_key="sorcery_points",
            label="Sorcery Points",
            maximum_by_level=sorcery_maximum,
            recovers_on="long_rest",
        )
    return result


def _primary_class_table_rows(text: str) -> list[tuple[int, dict[str, str]]]:
    result: list[tuple[int, dict[str, str]]] = []
    for _, fields in _markdown_table_rows(text):
        if "Level" not in fields or "Features" not in fields:
            continue
        match = re.match(r"(\d+)", fields["Level"])
        if match:
            result.append((int(match.group(1)), fields))
    return result[:20]


def _resource_scaling(
    *,
    target: str,
    label: str,
    class_name: str,
    maximum_by_level: dict[int, int],
    recovers_on: str,
    recovery_by_level: dict[int, str] | None = None,
    unlimited_at_level: int | None = None,
    maximum_formula: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "target": target,
        "label": label,
        "class_name": class_name,
        "maximum_by_level": {
            str(level): maximum for level, maximum in _changes_only(maximum_by_level).items()
        },
        "recovers_on": recovers_on,
        "recovery_by_level": {
            str(level): recovery for level, recovery in (recovery_by_level or {}).items()
        },
    }
    if unlimited_at_level is not None:
        result["unlimited_at_level"] = unlimited_at_level
    if maximum_formula is not None:
        result["maximum_formula"] = maximum_formula
    return result


def _initial_resource_from_scaling(scaling: dict[str, Any]) -> dict[str, Any]:
    maximums = dict(scaling.get("maximum_by_level") or {})
    maximum = int(next(iter(maximums.values()), 1))
    formula = dict(scaling.get("maximum_formula") or {})
    if formula:
        maximum = int(formula.get("minimum", 0))
    return {
        "label": str(scaling["label"]),
        "value": maximum,
        "max": maximum,
        "unlimited": False,
        "recovers_on": str(scaling["recovers_on"]),
        "source_key": str(scaling["class_name"]),
    }


def _numeric_column_scaling(
    rows: list[tuple[int, dict[str, str]]], column: str
) -> tuple[dict[int, int], int | None]:
    maximums: dict[int, int] = {}
    unlimited_at_level: int | None = None
    for level, fields in rows:
        raw = str(fields.get(column) or "").strip()
        if raw.casefold() == "unlimited":
            unlimited_at_level = level
            continue
        match = re.fullmatch(r"[+]?(\d+)", raw)
        if match:
            maximums[level] = int(match.group(1))
    return maximums, unlimited_at_level


def _feature_use_scaling(
    rows: list[tuple[int, dict[str, str]]], feature_name: str
) -> dict[int, int]:
    result: dict[int, int] = {}
    words = {"one": 1, "two": 2, "three": 3, "four": 4}
    pattern = re.compile(
        rf"\b{re.escape(feature_name)}\s*\(([^)]+)\)",
        re.IGNORECASE,
    )
    for level, fields in rows:
        match = pattern.search(str(fields.get("Features") or ""))
        if not match:
            continue
        token = match.group(1).casefold()
        number = re.search(r"\d+", token)
        if number:
            result[level] = int(number.group())
            continue
        for word, value in words.items():
            if re.search(rf"\b{word}\b", token):
                result[level] = value
                break
    return result


def _feature_display_scaling(
    rows: list[tuple[int, dict[str, str]]], feature_name: str, description: str
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    pattern = re.compile(
        rf"\b{re.escape(feature_name)}\s*\(d(\d+)\)",
        re.IGNORECASE,
    )
    previous: int | None = None
    for level, fields in rows:
        match = pattern.search(str(fields.get("Features") or ""))
        if not match:
            continue
        value = int(match.group(1))
        if value != previous:
            result.append({"level": level, "value": value, "description": description})
            previous = value
    return result


def _numeric_column_display_scaling(
    rows: list[tuple[int, dict[str, str]]], column: str, description: str
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    previous: int | None = None
    for level, fields in rows:
        raw = str(fields.get(column) or "")
        match = re.search(r"\d+", raw)
        if not match:
            continue
        value = int(match.group())
        if value != previous:
            result.append({"level": level, "value": value, "description": description})
            previous = value
    return result


def _changes_only(values: dict[int, int]) -> dict[int, int]:
    result: dict[int, int] = {}
    previous: int | None = None
    for level, value in sorted(values.items()):
        if value != previous:
            result[level] = value
            previous = value
    return result


def _subclass_spell_grants(body: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for _, fields in _markdown_table_rows(body):
        spell_text = next(
            (value for key, value in fields.items() if key.casefold().endswith("spells")),
            "",
        )
        level_text = next(
            (value for key, value in fields.items() if key.casefold().endswith("level")),
            "",
        )
        level_match = re.match(r"(\d+)", level_text)
        if not spell_text or not level_match:
            continue
        minimum_level = int(level_match.group(1))
        for name in spell_text.split(","):
            cleaned = re.sub(r"[*_`]", "", name).strip()
            if cleaned and cleaned != "-":
                result.append({"name": cleaned, "minimum_level": minimum_level})
    return result


def _subclass_spell_options(
    body: str,
    *,
    table_suffix: str,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for table_name, fields in _markdown_table_rows(body):
        if not table_name.casefold().endswith(table_suffix.casefold()):
            continue
        option = table_name[: -len(table_suffix)].strip()
        spell_text = next(
            (value for key, value in fields.items() if key.casefold().endswith("spells")),
            "",
        )
        level_text = next(
            (value for key, value in fields.items() if key.casefold().endswith("level")),
            "",
        )
        level_match = re.match(r"(\d+)", level_text)
        if not option or not spell_text or not level_match:
            continue
        minimum_level = int(level_match.group(1))
        grants = result.setdefault(option, [])
        for name in spell_text.split(","):
            cleaned = re.sub(r"[*_`]", "", name).strip()
            if cleaned and cleaned != "-":
                grants.append({"name": cleaned, "minimum_level": minimum_level})
    return result


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
    choice = {"count": 0, "amount": 0, "exclude": sorted(fixed), "options": []}
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
                "language_options": [],
                "allow_any_language": bool(
                    language_count and "choice" in language_text.casefold()
                ),
                "tool_choice_count": 0,
                "tool_options": [],
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
    return [
        {
            "kind": "dm_review",
            "text": line.group(1).strip(),
            "default_resolver": "agent",
            "ruling_kind": "source_or_scene_fact",
        }
    ]


def _plain_label(text: str, label: str) -> str:
    match = re.search(rf"^\*\*{re.escape(label)}:\*\*\s*(.+?)\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def _leading_count(value: str) -> int:
    first = value.casefold().split(maxsplit=1)[0] if value.strip() else ""
    words = {"one": 1, "two": 2, "three": 3, "four": 4}
    return int(first) if first.isdigit() else words.get(first, 0)


def _artifact(kind: str, name: str, path: Path, card: dict[str, Any]) -> dict[str, Any]:
    slug = ascii_slug(name) or "entry"
    rel = path.as_posix().split("references-2014-en/", 1)[-1]
    if kind in {"feat", "feature", "activity"}:
        card.setdefault("activation", {"type": "passive"})
    return {
        "id": f"{PACK_ID}.{kind}.{slug}",
        "kind": kind,
        "card": card,
        "rule_refs": [f"bundled:srd2014/{rel}"],
        "mechanic_refs": list(card.get("mechanic_refs") or []),
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
    return ascii_slug(value)


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
