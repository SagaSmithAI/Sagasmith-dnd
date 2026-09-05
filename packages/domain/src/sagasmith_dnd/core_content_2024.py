"""Build edition-pinned SRD 5.2.1 content from the bundled Markdown source."""

from __future__ import annotations

import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from sagasmith_core.text import ascii_slug

from sagasmith_dnd.content_import import audit_release_semantic_validation
from sagasmith_dnd.content_resolution import finalize_bundled_artifact_resolutions
from sagasmith_dnd.parsing_vocabulary import DND5E_2024_CLASS_NAMES as _CLASS_NAMES
from sagasmith_dnd.spell_resolution import (
    SPELL_RESOLUTION_MECHANIC_ID,
    known_2024_spell_resolution,
)

PACK_ID = "dnd5e.content.srd2024"
PACK_VERSION = "1.3.0"

_CLASS_FEATURE_TABLE_HEADINGS = {
    *(f"{class_name} Features" for class_name in _CLASS_NAMES),
    *(f"{class_name} Class Features" for class_name in _CLASS_NAMES),
}
_CLASS_FILES = (
    "DND5eSRD_019-035.md",
    "DND5eSRD_036-046.md",
    "DND5eSRD_047-063.md",
    "DND5eSRD_064-076.md",
    "DND5eSRD_077-086.md",
)
_SPELL_FILES = (
    "DND5eSRD_104-120.md",
    "DND5eSRD_121-137.md",
    "DND5eSRD_138-154.md",
    "DND5eSRD_155-175.md",
)
_MAGIC_ITEM_FILES = (
    "DND5eSRD_204-229.md",
    "DND5eSRD_230-252.md",
    "DND5eSRD_253-272.md",
)
_MONSTER_FILES = (
    "DND5eSRD_253-272.md",
    "DND5eSRD_273-292.md",
    "DND5eSRD_293-312.md",
    "DND5eSRD_313-332.md",
    "DND5eSRD_333-364.md",
)


def build_srd2024_content(skill_root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return a portable SRD 5.2.1 manifest and source-linked leaf artifacts."""

    manifest, artifacts = _cached_srd2024_content(str(skill_root.resolve()))
    return deepcopy(manifest), deepcopy(artifacts)


def parse_srd2024_monster_artifact(artifact: dict[str, Any]) -> Any:
    """Hydrate one catalog monster while preserving its exact SRD source refs."""

    if str(artifact.get("kind") or "") != "monster":
        raise ValueError("artifact must be an SRD 5.2.1 monster")
    card = dict(artifact.get("card") or {})
    if str(card.get("edition") or "") != "2024":
        raise ValueError("monster artifact must use the 2024 edition")
    from sagasmith_dnd.statblocks import parse_2024_statblock

    return parse_2024_statblock(
        str(card.get("statblock_source") or ""),
        source_key=str(artifact.get("id") or ""),
        rule_refs=list(artifact.get("rule_refs") or []),
        name=str(card.get("name") or "") or None,
    )


@lru_cache(maxsize=4)
def _cached_srd2024_content(skill_root: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    root = Path(skill_root) / "full" / "skills" / "dnd-dm" / "srd" / "references"
    if not root.is_dir():
        return {}, []

    artifacts: list[dict[str, Any]] = []
    artifacts.extend(_class_content(root))
    origins = root / "DND5eSRD_077-086.md"
    artifacts.extend(_backgrounds(origins))
    artifacts.extend(_species(origins))
    equipment = root / "DND5eSRD_087-103.md"
    artifacts.extend(_feats(equipment))
    artifacts.extend(_tools(equipment))
    artifacts.extend(_equipment(equipment))
    artifacts.extend(_spells(root))
    artifacts.extend(_magic_items(root))
    artifacts.extend(_monsters(root))
    artifacts = finalize_bundled_artifact_resolutions(
        _deduplicate(artifacts),
        source_root=root,
        source_prefix="bundled:srd2024/",
    )
    native_mechanic_refs = sorted(
        {
            str(ref)
            for artifact in artifacts
            for ref in artifact.get("mechanic_refs", [])
            if str(ref)
        }
    )
    semantic_validation = audit_release_semantic_validation(artifacts)
    return (
        {
            "id": PACK_ID,
            "version": PACK_VERSION,
            "title": "D&D 5e SRD 5.2.1 (2024) Structured Content",
            "namespace": PACK_ID,
            "system_id": "dnd5e",
            "editions": ["2024"],
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
                "monster",
            ],
            "resolution_policy": "build_time_complete",
            "semantic_validation": semantic_validation,
        },
        artifacts,
    )


def _class_content(root: Path) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    features: dict[tuple[str, str, str], dict[str, Any]] = {}
    seen_classes: set[str] = set()
    current_class = ""
    current_subclass = ""
    for filename in _CLASS_FILES:
        path = root / filename
        text = path.read_text(encoding="utf-8")
        headings = [
            match
            for match in re.finditer(
                r"^(#{1,4})\s+(.+?)\s*$",
                text,
                re.MULTILINE,
            )
            if not re.fullmatch(r"Page\s+\d+", match.group(2), re.IGNORECASE)
            and match.group(2).strip() not in _CLASS_FEATURE_TABLE_HEADINGS
        ]
        for index, heading in enumerate(headings):
            title = heading.group(2).strip()
            if title in _CLASS_NAMES:
                current_class = title
                current_subclass = ""
                if title not in seen_classes:
                    end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
                    artifacts.append(
                        _artifact(
                            "class",
                            title,
                            path,
                            heading.start(),
                            {
                                "name": title,
                                "description": _clean_source(text[heading.end() : end])[:2000],
                            },
                            application_state="catalog_only",
                        )
                    )
                    seen_classes.add(title)
                continue
            subclass = re.fullmatch(r"(?:.+?\s+)?Subclass:\s*(.+)", title, re.IGNORECASE)
            if subclass and current_class:
                current_subclass = subclass.group(1).strip()
                end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
                artifacts.append(
                    _artifact(
                        "subclass",
                        current_subclass,
                        path,
                        heading.start(),
                        {
                            "name": current_subclass,
                            "class_name": current_class,
                            "minimum_level": 3,
                            "description": _clean_source(text[heading.end() : end])[:2000],
                        },
                    )
                )
                continue
            level_feature = re.fullmatch(r"Level\s+(\d+):\s*(.+)", title, re.IGNORECASE)
            if level_feature is None or not current_class:
                continue
            level = int(level_feature.group(1))
            feature_name = level_feature.group(2).strip()
            if feature_name.casefold().endswith(" subclass"):
                current_subclass = ""
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            body = _clean_source(text[heading.end() : end])
            owner = current_subclass or current_class
            key = (current_class, current_subclass, feature_name)
            existing = features.get(key)
            if existing is None:
                card: dict[str, Any] = {
                    "name": feature_name,
                    "source_key": owner,
                    "class_name": current_class,
                    "minimum_level": level,
                    "unlock_levels": [level],
                    "description": body[:4000],
                }
                if current_subclass:
                    card["subclass_name"] = current_subclass
                card.update(_known_feature_structure(current_class, feature_name, body))
                features[key] = _artifact(
                    "feature",
                    f"{owner} {feature_name}",
                    path,
                    heading.start(),
                    card,
                    application_state=(
                        "catalog_only" if feature_name.casefold().endswith(" subclass") else "ready"
                    ),
                )
            else:
                card = existing["card"]
                if level not in card["unlock_levels"]:
                    card["unlock_levels"].append(level)
                    card["unlock_levels"].sort()
                card["minimum_level"] = min(card["minimum_level"], level)
                if body and body not in card["description"]:
                    card["description"] = f"{card['description']}\n\n{body}"[:4000]
    artifacts.extend(features.values())
    invocations = _eldritch_invocations(root / "DND5eSRD_064-076.md")
    invocation_parent = features.get(("Warlock", "", "Eldritch Invocations"))
    if invocation_parent is not None:
        invocation_parent["card"].update(_invocation_selection_structure(invocations))
    artifacts.extend(invocations)
    artifacts.extend(_metamagic_options(root / "DND5eSRD_064-076.md"))
    return artifacts


def _eldritch_invocations(path: Path) -> list[dict[str, Any]]:
    """Extract every source option that the Warlock feature can select."""

    text = path.read_text(encoding="utf-8")
    region = _between(
        text,
        "## Eldritch Invocation Options",
        "## Warlock Spell List",
    )
    region_offset = text.find(region)
    artifacts: list[dict[str, Any]] = []
    for name, raw_body, offset in _sections(region, 3):
        if re.fullmatch(r"Page\s+\d+", name, re.IGNORECASE):
            continue
        body = _clean_source(raw_body)
        prerequisite_match = re.search(
            r"(?m)^\*Prerequisite:\s*(.+?)\*\s*$",
            body,
        )
        prerequisite = prerequisite_match.group(1).strip() if prerequisite_match else ""
        level_match = re.search(
            r"\bLevel\s+(\d+)\+\s+Warlock\b",
            prerequisite,
            re.IGNORECASE,
        )
        repeatable = bool(re.search(r"(?m)^\*\*Repeatable\.\*\*", body))
        card: dict[str, Any] = {
            "name": name,
            "source_key": "Warlock",
            "class_name": "Warlock",
            "feature_subtype": "eldritch_invocation",
            "minimum_level": int(level_match.group(1)) if level_match else 1,
            "prerequisite_text": prerequisite,
            "repeatable": repeatable,
            "description": body[:4000],
            "ruling_requirements": [
                {
                    "kind": "feature_semantics",
                    "reason": (
                        "Use the exact invocation text as Agent context and settle "
                        "through public engine primitives."
                    ),
                    "source_excerpt": body[:4000],
                    "default_resolver": "agent",
                    "ruling_kind": "source_or_scene_fact",
                }
            ],
        }
        artifacts.append(
            _artifact(
                "feature",
                f"Warlock {name}",
                path,
                region_offset + offset,
                card,
            )
        )
    return artifacts


def _invocation_selection_structure(
    invocations: list[dict[str, Any]],
) -> dict[str, Any]:
    increments = {1: 1, 2: 2, 5: 2, 7: 1, 9: 1, 12: 1, 15: 1, 18: 1}
    options = [str(item["card"]["name"]) for item in invocations]
    artifact_ids = {str(item["card"]["name"]): str(item["id"]) for item in invocations}
    prerequisites: dict[str, dict[str, Any]] = {}
    for item in invocations:
        card = dict(item["card"])
        prerequisite: dict[str, Any] = {"minimum_level": int(card.get("minimum_level", 1) or 1)}
        required_invocation = re.search(
            r",\s*([^,]+?)\s+Invocation\b",
            str(card.get("prerequisite_text") or ""),
            re.IGNORECASE,
        )
        if required_invocation:
            prerequisite["required_invocation"] = required_invocation.group(1).strip()
        prerequisites[str(card["name"])] = prerequisite

    def requirement(count: int) -> dict[str, Any]:
        return {
            "field": "eldritch_invocations",
            "kind": "eldritch_invocations_2024",
            "count": count,
            "options": options,
            "option_artifact_ids": artifact_ids,
            "option_prerequisites": prerequisites,
            "repeatable_options": [
                str(item["card"]["name"]) for item in invocations if item["card"].get("repeatable")
            ],
        }

    return {
        "selection_requirements": requirement(1),
        "selection_requirements_by_level": {
            str(level): requirement(count) for level, count in increments.items()
        },
        "unlock_levels": list(increments),
        "repeatable_selection_levels": list(increments),
    }


def _metamagic_options(path: Path) -> list[dict[str, Any]]:
    """Expose each 2024 Metamagic option as bounded source context."""

    text = path.read_text(encoding="utf-8")
    region = _between(text, "## Metamagic Options", "## Sorcerer Spell List")
    region_offset = text.find(region)
    artifacts: list[dict[str, Any]] = []
    for name, raw_body, offset in _sections(region, 3):
        if re.fullmatch(r"Page\s+\d+", name, re.IGNORECASE):
            continue
        body = _clean_source(raw_body)
        cost_match = re.search(r"\*Cost:\s*(\d+)\s+Sorcery Points?\*", body)
        cost = int(cost_match.group(1)) if cost_match else None
        card: dict[str, Any] = {
            "name": name,
            "source_key": "Sorcerer",
            "class_name": "Sorcerer",
            "feature_subtype": "metamagic_option",
            "minimum_level": 2,
            "description": body[:4000],
            "resource_key": "sorcery_points",
            "ruling_requirements": _feature_ruling_requirements(body),
        }
        if cost is not None:
            card["choices"] = {"sorcery_point_cost": cost}
        artifacts.append(
            _artifact(
                "feature",
                f"Sorcerer Metamagic {name}",
                path,
                region_offset + offset,
                card,
                application_state="catalog_only",
            )
        )
    return artifacts


def _known_feature_structure(class_name: str, name: str, body: str) -> dict[str, Any]:
    key = (class_name.casefold(), name.casefold())
    if name.casefold() == "ability score improvement":
        levels = {
            "fighter": [4, 6, 8, 12, 14, 16],
            "rogue": [4, 8, 10, 12, 16],
        }.get(class_name.casefold(), [4, 8, 12, 16])
        return {
            "selection_requirements": {
                "field": "feat_choice",
                "kind": "feat_grant",
                "allowed_categories": ["origin", "general"],
            },
            "selection_requirements_by_level": {
                str(level): {
                    "field": "feat_choice",
                    "kind": "feat_grant",
                    "allowed_categories": ["origin", "general"],
                }
                for level in levels
            },
            "unlock_levels": levels,
            "repeatable_selection_levels": levels,
        }
    if name.casefold() == "epic boon":
        return {
            "selection_requirements": {
                "field": "feat_choice",
                "kind": "feat_grant",
                "allowed_categories": ["epic boon", "origin", "general"],
            }
        }
    if name.casefold() == "weapon mastery":
        counts = {"barbarian": 2, "fighter": 3, "paladin": 2, "ranger": 2, "rogue": 2}
        return {
            "selection_requirements": {
                "field": "weapon_masteries",
                "kind": "weapon_mastery",
                "count": counts.get(class_name.casefold(), 1),
                "eligible": "simple_or_martial_weapons_with_a_mastery_property",
                "replace_on": "long_rest",
            },
            "mechanic_refs": ["dnd5e.core.weapon.mastery"],
        }
    if key == ("barbarian", "rage"):
        return _shared_resource_structure(
            class_name="Barbarian",
            body=body,
            resource_key="rage",
            label="Rage",
            maximum_by_level={1: 2, 3: 3, 6: 4, 12: 5, 17: 6},
            recovers_on="short_rest",
            recovery_amounts={"short_rest": 1, "long_rest": "all"},
            activation={"type": "bonus_action", "cost": 1},
        )
    if key == ("bard", "bardic inspiration"):
        structure = _shared_resource_structure(
            class_name="Bard",
            body=body,
            resource_key="bardic_inspiration",
            label="Bardic Inspiration",
            maximum_formula={
                "kind": "ability_modifier",
                "ability": "charisma",
                "minimum": 1,
                "multiplier": 1,
                "offset": 0,
            },
            recovers_on="long_rest",
            recovery_by_level={5: "short_rest"},
            activation={"type": "bonus_action", "cost": 1},
        )
        structure["scaling"] = [
            {"level": 1, "value": 6, "description": "Bardic Inspiration die d6"},
            {"level": 5, "value": 8, "description": "Bardic Inspiration die d8"},
            {"level": 10, "value": 10, "description": "Bardic Inspiration die d10"},
            {"level": 15, "value": 12, "description": "Bardic Inspiration die d12"},
        ]
        return structure
    if key == ("druid", "wild shape"):
        return _shared_resource_structure(
            class_name="Druid",
            body=body,
            resource_key="wild_shape",
            label="Wild Shape",
            maximum_by_level={2: 2, 6: 3, 17: 4},
            recovers_on="short_rest",
            recovery_amounts={"short_rest": 1, "long_rest": "all"},
            activation={"type": "bonus_action", "cost": 1},
        )
    if key == ("fighter", "second wind"):
        structure = _shared_resource_structure(
            class_name="Fighter",
            body=body,
            resource_key="second_wind",
            label="Second Wind",
            maximum_by_level={1: 2, 4: 3, 10: 4},
            recovers_on="short_rest",
            recovery_amounts={"short_rest": 1, "long_rest": "all"},
            activation={"type": "bonus_action", "cost": 1},
        )
        structure.pop("ruling_requirements")
        structure["choices"] = {"outcome": "roll 1d10 + fighter level, then apply healing"}
        structure["mechanic_refs"] = ["dnd5e.core.activity.second_wind"]
        return structure
    if key == ("fighter", "action surge"):
        structure = _shared_resource_structure(
            class_name="Fighter",
            body=body,
            resource_key="action_surge",
            label="Action Surge",
            maximum_by_level={2: 1, 17: 2},
            recovers_on="short_rest",
            activation={"type": "special", "cost": 0},
        )
        structure.pop("ruling_requirements")
        structure["choices"] = {
            "outcome": "take one additional Action except the Magic action",
            "once_per_turn": True,
        }
        structure["mechanic_refs"] = ["dnd5e.core.activity.action_surge"]
        return structure
    if key == ("fighter", "indomitable"):
        return _local_resource_structure(
            class_name="Fighter",
            body=body,
            label="Indomitable",
            maximum_by_level={9: 1, 13: 2, 17: 3},
            recovers_on="long_rest",
            activation={"type": "special", "cost": 0},
        )
    if key == ("wizard", "arcane recovery"):
        return {
            "uses": {
                "label": "Arcane Recovery",
                "value": 1,
                "max": 1,
                "recovers_on": "long_rest",
                "source_key": "Wizard",
            },
            "mechanic_refs": ["dnd5e.core.rest.arcane_recovery"],
        }
    if key == ("bard", "jack of all trades"):
        return {
            "mechanic_refs": ["dnd5e.core.check.jack_of_all_trades"],
        }
    if key == ("bard", "magical discoveries"):
        return {
            "selection_requirements": {
                "field": "spell_artifact_ids",
                "kind": "known_spell_grants",
                "count": 2,
                "eligible_classes": ["cleric", "druid", "wizard"],
                "source_class": "Bard",
                "always_prepared": True,
            },
            "ruling_requirements": _feature_ruling_requirements(body),
        }
    if key in {("bard", "expertise"), ("rogue", "expertise")}:
        levels = [2, 9] if key[0] == "bard" else [1, 6]
        requirements = {
            "field": "proficiencies",
            "count": 2,
            "requires_existing_proficiency": True,
            "requires_new_expertise": True,
            "skills_only": True,
        }
        return {
            "selection_requirements": requirements,
            "selection_requirements_by_level": {str(level): dict(requirements) for level in levels},
            "unlock_levels": levels,
            "repeatable_selection_levels": levels,
        }
    if key == ("ranger", "expertise"):
        return {
            "selection_requirements": {
                "field": "proficiencies",
                "count": 2,
                "requires_existing_proficiency": True,
                "requires_new_expertise": True,
                "skills_only": True,
            }
        }
    if key == ("barbarian", "primal knowledge"):
        return {
            "selection_requirements": {
                "field": "skills",
                "count": 1,
                "options": [
                    "Animal Handling",
                    "Athletics",
                    "Intimidation",
                    "Nature",
                    "Perception",
                    "Survival",
                ],
                "requires_untrained_skill": True,
                "grants_skill_proficiency": True,
            },
            "ruling_requirements": _feature_ruling_requirements(body),
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
    if key == ("cleric", "divine order"):
        return _agent_choice_structure(
            body,
            field="divine_order",
            options=["Protector", "Thaumaturge"],
        )
    if key == ("cleric", "blessed strikes"):
        return _agent_choice_structure(
            body,
            field="blessed_strikes",
            options=["Divine Strike", "Potent Spellcasting"],
        )
    if key == ("druid", "primal order"):
        return _agent_choice_structure(
            body,
            field="primal_order",
            options=["Magician", "Warden"],
        )
    if key == ("druid", "elemental fury"):
        return _agent_choice_structure(
            body,
            field="elemental_fury",
            options=["Potent Spellcasting", "Primal Strike"],
        )
    if key == ("druid", "circle of the land spells"):
        return {
            "selection_requirements": {
                "field": "land_type",
                "count": 1,
                "options": ["Arid", "Polar", "Temperate", "Tropical"],
                "replacement_study_minutes": 480,
            },
            "always_prepared_spell_options": {
                "Arid": [
                    {"name": "Blur", "minimum_level": 3},
                    {"name": "Burning Hands", "minimum_level": 3},
                    {"name": "Fire Bolt", "minimum_level": 3},
                    {"name": "Fireball", "minimum_level": 5},
                    {"name": "Blight", "minimum_level": 7},
                    {"name": "Wall of Stone", "minimum_level": 9},
                ],
                "Polar": [
                    {"name": "Fog Cloud", "minimum_level": 3},
                    {"name": "Hold Person", "minimum_level": 3},
                    {"name": "Ray of Frost", "minimum_level": 3},
                    {"name": "Sleet Storm", "minimum_level": 5},
                    {"name": "Ice Storm", "minimum_level": 7},
                    {"name": "Cone of Cold", "minimum_level": 9},
                ],
                "Temperate": [
                    {"name": "Misty Step", "minimum_level": 3},
                    {"name": "Shocking Grasp", "minimum_level": 3},
                    {"name": "Sleep", "minimum_level": 3},
                    {"name": "Lightning Bolt", "minimum_level": 5},
                    {"name": "Freedom of Movement", "minimum_level": 7},
                    {"name": "Tree Stride", "minimum_level": 9},
                ],
                "Tropical": [
                    {"name": "Acid Splash", "minimum_level": 3},
                    {"name": "Ray of Sickness", "minimum_level": 3},
                    {"name": "Web", "minimum_level": 3},
                    {"name": "Stinking Cloud", "minimum_level": 5},
                    {"name": "Polymorph", "minimum_level": 7},
                    {"name": "Insect Plague", "minimum_level": 9},
                ],
            },
            "ruling_requirements": _feature_ruling_requirements(body),
        }
    if name.casefold() in {"fighting style", "additional fighting style"}:
        class_options = {
            "paladin": ["Blessed Warrior"],
            "ranger": ["Druidic Warrior"],
        }
        return _agent_choice_structure(
            body,
            field="fighting_style",
            options=[
                "Archery",
                "Defense",
                "Great Weapon Fighting",
                "Two-Weapon Fighting",
                *class_options.get(class_name.casefold(), []),
            ],
            requires_new_choice=True,
            choice_uniqueness_scope="fighting_style",
        )
    if key == ("ranger", "deft explorer"):
        return {
            "selection_requirements": {
                "field": "expertise_skill",
                "count": 1,
                "requires_existing_proficiency": True,
                "requires_new_expertise": True,
                "skills_only": True,
            },
            "ruling_requirements": [
                {
                    **_feature_ruling_requirements(body)[0],
                    "reason": (
                        "The Expertise choice is engine-validated; record the two "
                        "player-chosen languages through the ordinary actor language "
                        "workflow before play."
                    ),
                }
            ],
        }
    if key == ("ranger", "hunter's prey"):
        return _agent_choice_structure(
            body,
            field="hunter_prey",
            options=["Colossus Slayer", "Horde Breaker"],
        )
    if key == ("ranger", "defensive tactics"):
        return _agent_choice_structure(
            body,
            field="defensive_tactic",
            options=["Escape the Horde", "Multiattack Defense"],
        )
    if key == ("sorcerer", "elemental affinity"):
        return _agent_choice_structure(
            body,
            field="damage_type",
            options=["Acid", "Cold", "Fire", "Lightning", "Poison"],
        )
    if key == ("warlock", "fiendish resilience"):
        return _agent_choice_structure(
            body,
            field="damage_type",
            options=[
                "Acid",
                "Bludgeoning",
                "Cold",
                "Fire",
                "Lightning",
                "Necrotic",
                "Piercing",
                "Poison",
                "Psychic",
                "Radiant",
                "Slashing",
                "Thunder",
            ],
        )
    if key == ("wizard", "scholar"):
        return {
            "selection_requirements": {
                "field": "proficiencies",
                "count": 1,
                "options": [
                    "Arcana",
                    "History",
                    "Investigation",
                    "Medicine",
                    "Nature",
                    "Religion",
                ],
                "requires_existing_proficiency": True,
                "requires_new_expertise": True,
                "skills_only": True,
            }
        }
    if key == ("wizard", "evocation savant"):
        return {
            "selection_requirements": {
                "field": "spell_artifact_ids",
                "kind": "known_spell_grants",
                "count": 2,
                "eligible_class": "wizard",
                "maximum_spell_level": 2,
                "schools": ["Evocation"],
                "grant_method": "spellbook",
            }
        }
    if key == ("rogue", "sneak attack"):
        return {
            "mechanical_grants": {
                "sneak_attack": {
                    "dice_by_rogue_level": "ceil(level/2)d6",
                    "once_per_turn": True,
                    "requires_finesse_or_ranged": True,
                }
            },
            "mechanic_refs": ["dnd5e.core.attack.sneak_attack"],
        }
    if key == ("rogue", "cunning action"):
        return {
            "activation": {"type": "bonus_action", "cost": 1},
            "choices": {"options": ["Dash", "Disengage", "Hide"]},
            "mechanic_refs": ["dnd5e.core.activity.cunning_action"],
        }
    if key == ("rogue", "thieves' cant"):
        return {
            "selection_requirements": {
                "field": "language",
                "kind": "language_grant",
                "count": 1,
                "options": [
                    "Abyssal",
                    "Celestial",
                    "Common",
                    "Common Sign Language",
                    "Deep Speech",
                    "Draconic",
                    "Druidic",
                    "Dwarvish",
                    "Elvish",
                    "Giant",
                    "Gnomish",
                    "Goblin",
                    "Halfling",
                    "Infernal",
                    "Orc",
                    "Primordial",
                    "Sylvan",
                    "Undercommon",
                ],
            },
            "mechanical_grants": {"languages": ["Thieves' Cant"]},
        }
    if name.casefold() == "evasion" and class_name.casefold() in {"monk", "rogue"}:
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
    if key == ("cleric", "channel divinity"):
        structure = _shared_resource_structure(
            class_name="Cleric",
            body=body,
            resource_key="channel_divinity",
            label="Channel Divinity",
            maximum_by_level={2: 2, 6: 3, 18: 4},
            recovers_on="short_rest",
            recovery_amounts={"short_rest": 1, "long_rest": "all"},
            activation={"type": "action", "cost": 1},
        )
        structure.pop("ruling_requirements")
        structure["choices"] = {
            "options": ["Divine Spark", "Turn Undead"],
            "action_kind": "magic",
        }
        structure["mechanic_refs"] = [
            "dnd5e.core.activity.divine_spark",
            "dnd5e.core.activity.turn_undead",
        ]
        return structure
    if key == ("cleric", "preserve life"):
        return {
            "activation": {"type": "action", "cost": 1},
            "resource_key": "channel_divinity",
            "choices": {
                "action_kind": "magic",
                "outcome": (
                    "restore up to five times Cleric level Hit Points among Bloodied "
                    "creatures within 30 feet, never above half maximum Hit Points"
                ),
            },
            "mechanic_refs": ["dnd5e.core.activity.preserve_life"],
        }
    if key == ("cleric", "sear undead"):
        return {
            "mechanic_refs": ["dnd5e.core.activity.sear_undead"],
        }
    if key == ("monk", "monk's focus"):
        return _shared_resource_structure(
            class_name="Monk",
            body=body,
            resource_key="focus_points",
            label="Focus Points",
            maximum_formula={
                "kind": "class_level",
                "minimum": 2,
                "multiplier": 1,
                "offset": 0,
            },
            recovers_on="short_rest",
        )
    if key == ("monk", "uncanny metabolism"):
        return _local_resource_structure(
            class_name="Monk",
            body=body,
            label="Uncanny Metabolism",
            maximum_by_level={2: 1},
            recovers_on="long_rest",
            activation={"type": "special", "cost": 0},
        )
    if key == ("paladin", "lay on hands"):
        return _shared_resource_structure(
            class_name="Paladin",
            body=body,
            resource_key="lay_on_hands",
            label="Lay On Hands",
            maximum_formula={
                "kind": "class_level",
                "minimum": 5,
                "multiplier": 5,
                "offset": 0,
            },
            recovers_on="long_rest",
            activation={"type": "bonus_action", "cost": 1},
        )
    if key == ("paladin", "channel divinity"):
        structure = _shared_resource_structure(
            class_name="Paladin",
            body=body,
            resource_key="channel_divinity",
            label="Channel Divinity",
            maximum_by_level={3: 2, 11: 3},
            recovers_on="short_rest",
            recovery_amounts={"short_rest": 1, "long_rest": "all"},
        )
        structure["choices"] = {
            "options": ["Divine Sense"],
            "save_dc": "paladin_spell_save_dc",
        }
        return structure
    if key == ("ranger", "favored enemy"):
        return _shared_resource_structure(
            class_name="Ranger",
            body=body,
            resource_key="favored_enemy_hunters_mark",
            label="Favored Enemy (Hunter's Mark)",
            maximum_by_level={1: 2, 5: 3, 9: 4, 13: 5, 17: 6},
            recovers_on="long_rest",
        )
    if key == ("ranger", "tireless"):
        return _local_resource_structure(
            class_name="Ranger",
            body=body,
            label="Tireless Temporary Hit Points",
            maximum_formula={
                "kind": "ability_modifier",
                "ability": "wisdom",
                "minimum": 1,
                "multiplier": 1,
                "offset": 0,
            },
            recovers_on="long_rest",
            activation={"type": "action", "cost": 1},
        )
    if key == ("ranger", "nature's veil"):
        return _local_resource_structure(
            class_name="Ranger",
            body=body,
            label="Nature's Veil",
            maximum_formula={
                "kind": "ability_modifier",
                "ability": "wisdom",
                "minimum": 1,
                "multiplier": 1,
                "offset": 0,
            },
            recovers_on="long_rest",
            activation={"type": "bonus_action", "cost": 1},
        )
    if key == ("rogue", "stroke of luck"):
        return _local_resource_structure(
            class_name="Rogue",
            body=body,
            label="Stroke of Luck",
            maximum_by_level={20: 1},
            recovers_on="short_rest",
            activation={"type": "special", "cost": 0},
        )
    if key == ("sorcerer", "innate sorcery"):
        return _local_resource_structure(
            class_name="Sorcerer",
            body=body,
            label="Innate Sorcery",
            maximum_by_level={1: 2},
            recovers_on="long_rest",
            activation={"type": "bonus_action", "cost": 1},
        )
    if key == ("sorcerer", "font of magic"):
        return _shared_resource_structure(
            class_name="Sorcerer",
            body=body,
            resource_key="sorcery_points",
            label="Sorcery Points",
            maximum_formula={
                "kind": "class_level",
                "minimum": 2,
                "multiplier": 1,
                "offset": 0,
            },
            recovers_on="long_rest",
        )
    if key == ("sorcerer", "sorcerous restoration"):
        structure = _local_resource_structure(
            class_name="Sorcerer",
            body=body,
            label="Sorcerous Restoration",
            maximum_by_level={5: 1},
            recovers_on="long_rest",
            activation={"type": "special", "cost": 0},
        )
        structure.pop("ruling_requirements")
        structure["mechanic_refs"] = ["dnd5e.core.rest.sorcerous_restoration"]
        return structure
    if key == ("sorcerer", "metamagic"):
        options = [
            "Careful Spell",
            "Distant Spell",
            "Empowered Spell",
            "Extended Spell",
            "Heightened Spell",
            "Quickened Spell",
            "Seeking Spell",
            "Subtle Spell",
            "Transmuted Spell",
            "Twinned Spell",
        ]

        def requirements(count: int) -> dict[str, Any]:
            return {
                "field": "options",
                "count": count,
                "options": options,
                "requires_new_choice": True,
                "choice_uniqueness_scope": "sorcerer_metamagic",
            }

        return {
            "selection_requirements": requirements(2),
            "selection_requirements_by_level": {
                "2": requirements(2),
                "10": requirements(2),
                "17": requirements(2),
            },
            "unlock_levels": [2, 10, 17],
            "repeatable_selection_levels": [2, 10, 17],
            "ruling_requirements": _feature_ruling_requirements(body),
        }
    if key == ("warlock", "pact magic"):
        return {
            "mechanic_refs": ["dnd5e.core.spell.pact_magic"],
        }
    if key == ("warlock", "mystic arcanum"):
        levels = {11: 6, 13: 7, 15: 8, 17: 9}

        def arcanum_requirement(spell_level: int) -> dict[str, Any]:
            return {
                "field": "spell_artifact_ids",
                "kind": "mystic_arcanum",
                "count": 1,
                "spell_level": spell_level,
                "eligible_class": "warlock",
            }

        return {
            "selection_requirements": arcanum_requirement(6),
            "selection_requirements_by_level": {
                str(level): arcanum_requirement(spell_level)
                for level, spell_level in levels.items()
            },
            "unlock_levels": list(levels),
            "repeatable_selection_levels": list(levels),
        }
    if key == ("warlock", "eldritch invocations"):
        # The parent requirements depend on the separately extracted option
        # cards and are attached by _class_content after that extraction.
        return {}
    if key == ("wizard", "spell mastery"):
        return {
            "selection_requirements": {
                "field": "spell_artifact_ids",
                "kind": "spell_mastery",
                "count": 2,
                "required_spell_levels": [1, 2],
                "casting_times": ["Action"],
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
                "required_spell_levels": [3, 3],
                "requires_spellbook": True,
            }
        }
    normalized_name = name.casefold()
    if normalized_name in {"extra attack", "two extra attacks", "three extra attacks"}:
        maximums_by_class = {
            "barbarian": {5: 2},
            "fighter": {5: 2, 11: 3, 20: 4},
            "monk": {5: 2},
            "paladin": {5: 2},
            "ranger": {5: 2},
        }
        maximums = maximums_by_class.get(class_name.casefold())
        if maximums is None:
            return {
                "ruling_requirements": [
                    {
                        "kind": "feature_semantics",
                        "reason": "Extra Attack variant requires exact source review.",
                        "source_excerpt": body[:4000],
                        "default_resolver": "agent",
                        "ruling_kind": "source_or_scene_fact",
                    }
                ]
            }
        return {
            "attack_scaling": {
                "class_name": class_name,
                "attacks_per_action_by_level": {
                    str(level): amount for level, amount in maximums.items()
                },
            },
            "mechanic_refs": ["dnd5e.core.progression.extra_attack"],
        }
    return {"ruling_requirements": _feature_ruling_requirements(body)}


def _feature_ruling_requirements(body: str) -> list[dict[str, Any]]:
    return [
        {
            "kind": "feature_semantics",
            "reason": (
                "Use the exact feature text as Agent context and settle through "
                "public engine primitives."
            ),
            "source_excerpt": body[:4000],
            "default_resolver": "agent",
            "ruling_kind": "source_or_scene_fact",
        }
    ]


def _agent_choice_structure(
    body: str,
    *,
    field: str,
    options: list[str],
    requires_new_choice: bool = False,
    choice_uniqueness_scope: str = "",
) -> dict[str, Any]:
    """Record an exact source-defined choice while leaving its effect to Agent ruling."""

    requirements: dict[str, Any] = {
        "field": field,
        "count": 1,
        "options": options,
    }
    if requires_new_choice:
        requirements["requires_new_choice"] = True
    if choice_uniqueness_scope:
        requirements["choice_uniqueness_scope"] = choice_uniqueness_scope
    return {
        "selection_requirements": requirements,
        "ruling_requirements": _feature_ruling_requirements(body),
    }


def _resource_scaling_structure(
    *,
    target: str,
    class_name: str,
    label: str,
    maximum_by_level: dict[int, int] | None = None,
    maximum_formula: dict[str, Any] | None = None,
    recovers_on: str,
    recovery_by_level: dict[int, str] | None = None,
    recovery_amounts: dict[str, int | str] | None = None,
) -> dict[str, Any]:
    scaling: dict[str, Any] = {
        "target": target,
        "label": label,
        "class_name": class_name,
        "maximum_by_level": {
            str(level): maximum for level, maximum in (maximum_by_level or {}).items()
        },
        "recovers_on": recovers_on,
        "recovery_by_level": {
            str(level): recovery for level, recovery in (recovery_by_level or {}).items()
        },
    }
    if maximum_formula is not None:
        scaling["maximum_formula"] = dict(maximum_formula)
    if recovery_amounts:
        scaling["recovery_amounts"] = dict(recovery_amounts)
    return scaling


def _initial_resource(scaling: dict[str, Any]) -> dict[str, Any]:
    maximums = dict(scaling.get("maximum_by_level") or {})
    maximum = int(next(iter(maximums.values()), 1))
    formula = dict(scaling.get("maximum_formula") or {})
    if formula:
        maximum = int(formula.get("minimum", 0) or 0)
    resource: dict[str, Any] = {
        "label": str(scaling["label"]),
        "value": maximum,
        "max": maximum,
        "recovers_on": str(scaling["recovers_on"]),
        "source_key": str(scaling["class_name"]),
    }
    recovery_amounts = dict(scaling.get("recovery_amounts") or {})
    if recovery_amounts:
        resource["recovery_amounts"] = recovery_amounts
    return resource


def _shared_resource_structure(
    *,
    class_name: str,
    body: str,
    resource_key: str,
    label: str,
    maximum_by_level: dict[int, int] | None = None,
    maximum_formula: dict[str, Any] | None = None,
    recovers_on: str,
    recovery_by_level: dict[int, str] | None = None,
    recovery_amounts: dict[str, int | str] | None = None,
    activation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scaling = _resource_scaling_structure(
        target=resource_key,
        class_name=class_name,
        label=label,
        maximum_by_level=maximum_by_level,
        maximum_formula=maximum_formula,
        recovers_on=recovers_on,
        recovery_by_level=recovery_by_level,
        recovery_amounts=recovery_amounts,
    )
    result: dict[str, Any] = {
        "resource_key": resource_key,
        "mechanical_grants": {"resources": {resource_key: _initial_resource(scaling)}},
        "resource_scaling": scaling,
        "ruling_requirements": _feature_ruling_requirements(body),
    }
    if activation is not None:
        result["activation"] = dict(activation)
    return result


def _local_resource_structure(
    *,
    class_name: str,
    body: str,
    label: str,
    maximum_by_level: dict[int, int] | None = None,
    maximum_formula: dict[str, Any] | None = None,
    recovers_on: str,
    recovery_by_level: dict[int, str] | None = None,
    recovery_amounts: dict[str, int | str] | None = None,
    activation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scaling = _resource_scaling_structure(
        target="uses",
        class_name=class_name,
        label=label,
        maximum_by_level=maximum_by_level,
        maximum_formula=maximum_formula,
        recovers_on=recovers_on,
        recovery_by_level=recovery_by_level,
        recovery_amounts=recovery_amounts,
    )
    result: dict[str, Any] = {
        "uses": _initial_resource(scaling),
        "resource_scaling": scaling,
        "ruling_requirements": _feature_ruling_requirements(body),
    }
    if activation is not None:
        result["activation"] = dict(activation)
    return result


def _backgrounds(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    region = _between(text, "### Background Descriptions", "## Character Species")
    result: list[dict[str, Any]] = []
    for name, body, offset in _sections(region, 4):
        skills = _split_and_commas(_label(body, "Skill Proficiencies"))
        abilities = _split_and_commas(_label(body, "Ability Scores"))
        feat_label = re.sub(r"\s*\(see .+", "", _label(body, "Feat"), flags=re.I)
        feat_match = re.fullmatch(r"(.+?)\s*\(([^)]+)\)", feat_label)
        feat_name = feat_match.group(1).strip() if feat_match else feat_label
        feat_preset = {"source_class": feat_match.group(2).strip()} if feat_match else {}
        tool_text = _label(body, "Tool Proficiency")
        tool_choices = (
            ["Dice", "Dragonchess", "Playing Cards", "Three-Dragon Ante"]
            if "gaming set" in tool_text.casefold() and "choose" in tool_text.casefold()
            else []
        )
        fixed_tools = [] if tool_choices else [tool_text]
        result.append(
            _artifact(
                "background",
                name,
                path,
                text.find(region) + offset,
                {
                    "name": name,
                    "ability_score_choices": [item.casefold() for item in abilities],
                    "skill_proficiencies": [item.casefold() for item in skills],
                    "background_grants": {
                        "feature": feat_label,
                        "equipment_item_ids": [],
                        "languages": [],
                        "tools": fixed_tools,
                        "choices": {
                            "ability_score_options": [item.casefold() for item in abilities],
                            "allowed_ability_score_distributions": [[2, 1], [1, 1, 1]],
                            "maximum_ability_score": 20,
                            "origin_feat_name": feat_name,
                            "origin_feat_preset": feat_preset,
                            "tool_choice_count": 1 if tool_choices else 0,
                            "tool_options": tool_choices,
                            "equipment_packages": _background_equipment_packages(name),
                            "equipment_description": _label(body, "Equipment"),
                        },
                    },
                    "description": _clean_source(body)[:3000],
                },
            )
        )
    return result


def _background_equipment_packages(name: str) -> dict[str, dict[str, Any]]:
    def item(
        artifact_name: str,
        quantity: int = 1,
        display_name: str = "",
    ) -> dict[str, Any]:
        return {
            "artifact_id": f"{PACK_ID}.item.{_slug(artifact_name)}",
            "quantity": quantity,
            **({"display_name": display_name} if display_name else {}),
        }

    package_a: dict[str, list[dict[str, Any]]] = {
        "Acolyte": [
            item("Calligrapher's Supplies"),
            item("Book", display_name="Book (prayers)"),
            item("Holy Symbol"),
            item("Parchment", 10),
            item("Robe"),
        ],
        "Criminal": [
            item("Dagger", 2),
            item("Thieves' Tools"),
            item("Crowbar"),
            item("Pouch", 2),
            item("Clothes, Traveler's", display_name="Traveler's Clothes"),
        ],
        "Sage": [
            item("Quarterstaff"),
            item("Calligrapher's Supplies"),
            item("Book", display_name="Book (history)"),
            item("Parchment", 8),
            item("Robe"),
        ],
        "Soldier": [
            item("Spear"),
            item("Shortbow"),
            item("Arrows", 20),
            {"selected_tool": True, "quantity": 1},
            item("Healer's Kit"),
            item("Quiver"),
            item("Clothes, Traveler's", display_name="Traveler's Clothes"),
        ],
    }
    starting_gold = {"Acolyte": 8, "Criminal": 16, "Sage": 8, "Soldier": 14}
    if name not in package_a:
        return {}
    return {
        "A": {"items": package_a[name], "wallet": {"gp": starting_gold[name]}},
        "B": {"items": [], "wallet": {"gp": 50}},
    }


def _species(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    region = text[text.find("### Species Descriptions") :]
    result: list[dict[str, Any]] = []
    for name, body, offset in _sections(region, 4):
        playable_species = {
            "Dragonborn",
            "Dwarf",
            "Elf",
            "Gnome",
            "Goliath",
            "Halfling",
            "Human",
            "Orc",
            "Tiefling",
        }
        if name not in playable_species:
            continue
        size_text = _label(body, "Size")
        sizes = [
            size.casefold() for size in ("Tiny", "Small", "Medium", "Large") if size in size_text
        ]
        speed = re.search(r"(\d+)\s+feet", _label(body, "Speed"), re.IGNORECASE)
        traits = _bold_traits(body)
        grants: dict[str, Any] = {
            "ability_score_increases": {},
            "ability_choice": {"count": 0, "amount": 0, "exclude": [], "options": []},
            "size": sizes[0] if len(sizes) == 1 else "",
            "size_choices": sizes,
            "size_options": sizes if len(sizes) > 1 else [],
            "walk_speed": int(speed.group(1)) if speed else 0,
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
        for trait_name, trait_body in traits:
            folded = trait_name.casefold()
            if folded == "darkvision":
                distance = re.search(r"(\d+)\s+feet", trait_body, re.IGNORECASE)
                grants["darkvision_ft"] = int(distance.group(1)) if distance else 60
            elif folded == "dwarven resilience":
                grants["resistances"].append("poison")
            elif folded == "dwarven toughness":
                grants["hp_per_level"] = 1
            elif folded == "skillful":
                grants["skill_choice_count"] = 1
                grants["allow_any_skill"] = True
            grants["features"].append(
                {
                    "id": f"{PACK_ID}.species-feature.{_slug(name)}-{_slug(trait_name)}",
                    "name": trait_name,
                    "source_key": name,
                    "description": trait_body[:3000],
                    "activation": {"type": _activation(trait_body), "cost": 0},
                    **(
                        {"choices": {"grant_heroic_inspiration_on": "long_rest"}}
                        if name == "Human" and trait_name == "Resourceful"
                        else {}
                    ),
                    "ruling_requirements": [
                        {
                            "kind": "species_trait_semantics",
                            "source_excerpt": trait_body[:3000],
                            "default_resolver": "agent",
                            "ruling_kind": "source_or_scene_fact",
                        }
                    ],
                }
            )
        result.append(
            _artifact(
                "species",
                name,
                path,
                text.find(region) + offset,
                {
                    "name": name,
                    "base_species": name,
                    "description": _clean_source(body)[:4000],
                    "grants": grants,
                },
            )
        )
    return result


def _feats(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    region = _between(text, "# Feats", "# Equipment")
    category = ""
    result: list[dict[str, Any]] = []
    headings = list(re.finditer(r"^(#{2,3})\s+(.+?)\s*$", region, re.MULTILINE))
    for index, heading in enumerate(headings):
        depth, name = len(heading.group(1)), heading.group(2).strip()
        if depth == 2:
            category = name
            continue
        if not category.endswith("Feats"):
            continue
        end = headings[index + 1].start() if index + 1 < len(headings) else len(region)
        body = _clean_source(region[heading.end() : end])
        prerequisite = _italic_prerequisite(body)
        card: dict[str, Any] = {
            "name": name,
            "category": category.removesuffix(" Feats").casefold(),
            "description": body[:4000],
            "ruling_requirements": [
                {
                    "kind": "feat_semantics",
                    "source_excerpt": body[:4000],
                    "default_resolver": "agent",
                    "ruling_kind": "source_or_scene_fact",
                }
            ],
        }
        card.update(_known_feat_structure(name))
        source_prerequisite = prerequisite or _feat_heading_prerequisite(body)
        if source_prerequisite:
            card["prerequisites"] = _structured_feat_prerequisites(source_prerequisite)
        card["repeatable"] = bool(re.search(r"(?m)^\*\*Repeatable\.\*\*", body))
        result.append(_artifact("feat", name, path, text.find(region) + heading.start(), card))
    return result


def _equipment(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    result: list[dict[str, Any]] = []
    for table_name, row, offset in _table_rows(text):
        name_field = next(
            (
                field
                for field in ("Name", "Armor", "Item", "Type", "Focus", "Symbol", "Ship")
                if str(row.get(field) or "").strip()
            ),
            "",
        )
        name = _equipment_name(str(row.get(name_field) or ""))
        category_rows = (
            "armor (1 minute to don or doff)",
            "armor (5 minutes to don and 1 minute to doff)",
            "armor (10 minutes to don and 5 minutes to doff)",
            "shield (utilize action to don or doff)",
        )
        allowed_tables = {
            "simple melee weapons",
            "simple ranged weapons",
            "martial melee weapons",
            "martial ranged weapons",
            "armor",
            "adventuring gear table",
            "ammunition",
            "arcane focuses",
            "druidic focuses",
            "holy symbols",
            "mounts and other animals",
            "tack, harness, and drawn vehicles",
            "airborne and waterborne vehicles",
        }
        if (
            not name
            or table_name.casefold() not in allowed_tables
            or name.casefold().endswith(category_rows)
            or str(row.get(name_field) or "").strip().startswith("**")
        ):
            continue
        properties = {_slug(key).replace("-", "_"): value for key, value in row.items()}
        card: dict[str, Any] = {
            "name": name,
            "category": table_name,
            "properties": properties,
            "inventory_template": _inventory_template(
                name,
                table_name=table_name,
                properties=properties,
            ),
        }
        mastery = str(row.get("Mastery") or "").strip().casefold()
        mastery_ids = {"cleave", "graze", "nick", "push", "sap", "slow", "topple", "vex"}
        if mastery and mastery in mastery_ids:
            card["mechanics"] = {"mastery": mastery}
            card["mechanic_refs"] = ["dnd5e.core.weapon.mastery"]
        result.append(_artifact("item", name, path, offset, card))
    return result


def _tools(path: Path) -> list[dict[str, Any]]:
    """Extract each SRD tool and variant as a source-linked inventory option."""

    text = path.read_text(encoding="utf-8")
    start = text.find("## Tools")
    end = text.find("## Adventuring Gear", start)
    if start < 0 or end < 0:
        return []
    region = text[start:end]
    headings = list(re.finditer(r"^####\s+(.+?)\s+\(([^)]+)\)\s*$", region, re.MULTILINE))
    result: list[dict[str, Any]] = []
    for index, heading in enumerate(headings):
        block_end = headings[index + 1].start() if index + 1 < len(headings) else len(region)
        body = _clean_source(region[heading.end() : block_end])
        name = _equipment_name(heading.group(1))
        cost = heading.group(2).strip()
        properties = {
            "cost": cost,
            "ability": _inline_label(body, "Ability"),
            "weight": _inline_label(body, "Weight"),
            "utilize": _inline_label(body, "Utilize"),
            "craft": _inline_label(body, "Craft"),
        }
        card = {
            "name": name,
            "category": "Tool",
            "properties": {key: value for key, value in properties.items() if value},
            "description": body[:4000],
            "inventory_template": _inventory_template(
                name,
                table_name="Tool",
                properties=properties,
            ),
        }
        result.append(_artifact("item", name, path, start + heading.start(), card))
        variants = _inline_label(body, "Variants")
        for variant in re.finditer(r"(?:^|,\s*)([^,()]+?)\s*\(([^)]+)\)", variants):
            variant_name = _equipment_name(variant.group(1)).title()
            variant_details = [part.strip() for part in variant.group(2).split(",")]
            variant_properties = {
                "parent_tool": name,
                "cost": variant_details[0] if variant_details else "",
                "weight": variant_details[1] if len(variant_details) > 1 else "",
            }
            result.append(
                _artifact(
                    "item",
                    variant_name,
                    path,
                    start + heading.start(),
                    {
                        "name": variant_name,
                        "category": "Tool Variant",
                        "properties": {
                            key: value for key, value in variant_properties.items() if value
                        },
                        "description": body[:1200],
                        "inventory_template": _inventory_template(
                            variant_name,
                            table_name="Tool Variant",
                            properties=variant_properties,
                        ),
                    },
                )
            )
    return result


def _equipment_name(value: str) -> str:
    return value.strip().strip("*").replace("’", "'").replace("–", "-").replace("—", "-")


def _inventory_template(
    name: str,
    *,
    table_name: str,
    properties: dict[str, Any],
) -> dict[str, Any]:
    """Translate a source table row into the actor inventory schema when exact."""

    table = table_name.casefold()
    weight_oz = _weight_ounces(str(properties.get("weight") or ""))
    price_cp = _price_copper(str(properties.get("cost") or ""))
    template: dict[str, Any] = {
        "name": name,
        "kind": "equipment",
        "quantity": 1,
        "weight_oz": weight_oz,
        "price_cp": price_cp,
        "description": "",
        "source_key": f"{PACK_ID}.item.{_slug(name)}",
        "equipped": False,
        "identified": True,
        "attunement": "none",
        "condition": "normal",
        "uses": {},
        "charges": {},
        "mechanics": {},
    }
    if "weapon" in table:
        damage = re.match(
            r"\s*(\d+d\d+)\s+([A-Za-z]+)",
            str(properties.get("damage") or ""),
        )
        property_text = str(properties.get("properties") or "")
        property_names = [
            re.sub(r"\s*\(.+\)$", "", item).strip()
            for item in property_text.split(",")
            if item.strip()
        ]
        ranged = "ranged weapons" in table
        range_match = re.search(r"(?:Range\s+)?(\d+)\s*/\s*(\d+)", property_text, re.I)
        versatile = re.search(r"Versatile\s*\((\d+d\d+)\)", property_text, re.I)
        template["kind"] = "weapon"
        template["mechanics"] = {
            "category": "martial" if "martial" in table else "simple",
            "attack_type": "ranged" if ranged else "melee",
            "attack_ability": "dexterity" if ranged else "strength",
            "damage_formula": damage.group(1) if damage else "",
            "damage_type": damage.group(2).casefold() if damage else "",
            "versatile_damage_formula": versatile.group(1) if versatile else "",
            "properties": property_names,
            "normal_range_ft": int(range_match.group(1)) if ranged and range_match else 0,
            "long_range_ft": int(range_match.group(2)) if ranged and range_match else 0,
            "thrown_normal_range_ft": (
                int(range_match.group(1))
                if not ranged and "thrown" in property_text.casefold() and range_match
                else 0
            ),
            "thrown_long_range_ft": (
                int(range_match.group(2))
                if not ranged and "thrown" in property_text.casefold() and range_match
                else 0
            ),
            "proficient": False,
            "mastery": str(properties.get("mastery") or "").casefold(),
        }
    elif table == "armor":
        ac_text = str(properties.get("armor_class_ac") or "")
        if name.casefold() == "shield":
            template["kind"] = "shield"
            bonus = re.search(r"\+(\d+)", ac_text)
            template["mechanics"] = {"ac_bonus": int(bonus.group(1)) if bonus else 0}
        else:
            base = re.match(r"(\d+)", ac_text)
            dexterity_mode = "none"
            dexterity_max: int | None = None
            if "dex modifier" in ac_text.casefold():
                maximum = re.search(r"max\s+(\d+)", ac_text, re.I)
                dexterity_mode = "max" if maximum else "full"
                dexterity_max = int(maximum.group(1)) if maximum else None
            mechanics: dict[str, Any] = {
                "base_ac": int(base.group(1)) if base else 10,
                "dexterity_mode": dexterity_mode,
                "stealth_disadvantage": (
                    str(properties.get("stealth") or "").casefold() == "disadvantage"
                ),
            }
            if dexterity_max is not None:
                mechanics["dexterity_max"] = dexterity_max
            template["kind"] = "armor"
            template["mechanics"] = mechanics
    elif table == "ammunition":
        amount = int(str(properties.get("amount") or "1"))
        template["kind"] = "ammunition"
        template["quantity"] = amount
        template["weight_oz"] = weight_oz / amount if amount else 0
    return template


def _weight_ounces(value: str) -> int:
    fraction_match = re.search(r"(\d+)\s*/\s*(\d+)\s*lb", value.casefold())
    if fraction_match is not None:
        numerator = int(fraction_match.group(1))
        denominator = int(fraction_match.group(2))
        return int(numerator / denominator * 16) if denominator else 0
    match = re.search(r"(\d+)?\s*([\u00bc\u00bd\u00be])?\s*lb", value.casefold())
    if match is None:
        return 0
    whole = int(match.group(1) or 0)
    fraction = {"¼": 0.25, "½": 0.5, "¾": 0.75}.get(match.group(2) or "", 0)
    return int((whole + fraction) * 16)


def _price_copper(value: str) -> int:
    match = re.search(r"([\d,]+)\s*(CP|SP|EP|GP|PP)\b", value, re.I)
    if match is None:
        return 0
    multiplier = {"CP": 1, "SP": 10, "EP": 50, "GP": 100, "PP": 1000}
    return int(match.group(1).replace(",", "")) * multiplier[match.group(2).upper()]


def _spells(root: Path) -> list[dict[str, Any]]:
    candidates: list[tuple[Path, re.Match[str], str]] = []
    for filename in _SPELL_FILES:
        path = root / filename
        text = path.read_text(encoding="utf-8")
        for heading in re.finditer(r"^#{2,3}\s+(.+?)\s*$", text, re.MULTILINE):
            following = text[heading.end() :]
            metadata = re.match(r"\s*\*([^*\n]+)\*", following)
            if metadata and _spell_metadata(metadata.group(1)) is not None:
                candidates.append((path, heading, text))
    result: list[dict[str, Any]] = []
    for index, (path, heading, text) in enumerate(candidates):
        end = len(text)
        if index + 1 < len(candidates) and candidates[index + 1][0] == path:
            end = candidates[index + 1][1].start()
        block = text[heading.end() : end]
        metadata_match = re.match(r"\s*\*([^*\n]+)\*", block)
        assert metadata_match is not None
        parsed = _spell_metadata(metadata_match.group(1))
        assert parsed is not None
        level, school, classes = parsed
        name = heading.group(1).strip()
        effect = _spell_effect(block)
        card: dict[str, Any] = {
            "name": name,
            "level": level,
            "classes": classes,
            "grant": {"source_type": "catalog", "source_key": "", "method": "unselected"},
            "access": {
                "known": False,
                "prepared": False,
                "ritual_available": "ritual" in (_label(block, "Casting Time").casefold()),
            },
            "definition": {
                "school": school,
                "casting_time": _label(block, "Casting Time") or "Action",
                "range": _range(_label(block, "Range")),
                "duration": _duration(_label(block, "Duration")),
                "components": _components(_label(block, "Components")),
                "effect": effect,
            },
        }
        mechanic_refs: list[str] = []
        resolution = known_2024_spell_resolution(name)
        if resolution is not None:
            card["resolution"] = resolution
            mechanic_refs.append(SPELL_RESOLUTION_MECHANIC_ID)
        exact_engine = {
            "shield": "dnd5e.core.spell.shield",
            "magic-missile": "dnd5e.core.spell.magic_missile",
            "mage-armor": "dnd5e.core.spell.mage_armor",
            "fly": "dnd5e.core.spell.fly",
            "invisibility": "dnd5e.core.spell.invisibility",
            "hypnotic-pattern": "dnd5e.core.spell.hypnotic_pattern",
        }.get(_slug(name))
        if exact_engine:
            mechanic_refs.append(exact_engine)
        if mechanic_refs:
            card["mechanic_refs"] = mechanic_refs
        if not mechanic_refs:
            card["ruling_requirements"] = [
                {
                    "kind": "effect_semantics",
                    "reason": (
                        "Apply the SRD 5.2.1 spell through its persisted "
                        "Agent-as-DM clause and public engine operations."
                    ),
                    "source_excerpt": effect,
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
        result.append(_artifact("spell", name, path, heading.start(), card))
    return result


def _magic_items(root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for filename in _MAGIC_ITEM_FILES:
        path = root / filename
        text = path.read_text(encoding="utf-8")
        stop = text.find("# Monsters")
        if stop >= 0:
            text = text[:stop]
        headings = list(re.finditer(r"^#{2,3}\s+(.+?)\s*$", text, re.MULTILINE))
        for index, heading in enumerate(headings):
            block_start = heading.end()
            metadata = re.match(r"\s*\*([^*\n]+)\*", text[block_start:])
            if metadata is None or not _looks_like_magic_item(metadata.group(1)):
                continue
            end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
            body = _clean_source(text[block_start:end])
            result.append(
                _artifact(
                    "item",
                    heading.group(1).strip(),
                    path,
                    heading.start(),
                    {
                        "name": heading.group(1).strip(),
                        "category": metadata.group(1).strip(),
                        "description": body[:4000],
                        "ruling_requirements": [
                            {
                                "kind": "magic_item_semantics",
                                "source_excerpt": body[:4000],
                                "default_resolver": "agent",
                                "ruling_kind": "source_or_scene_fact",
                            }
                        ],
                    },
                )
            )
    return result


def _monsters(root: Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    segments: list[tuple[Path, int, int, str]] = []
    combined_parts: list[str] = []
    cursor = 0
    for filename in _MONSTER_FILES:
        path = root / filename
        text = path.read_text(encoding="utf-8")
        combined_parts.append(text)
        segments.append((path, cursor, cursor + len(text), text))
        cursor += len(text)
        combined_parts.append("\n")
        cursor += 1
    combined = "".join(combined_parts)
    matches = list(
        re.finditer(
            (
                r"(?m)^(?:> )?#{1,3} (?P<name>[^\n]+)\n(?:> )?\*"
                r"(?P<identity>(?:Tiny|Small|Medium|Large|Huge|Gargantuan)"
                r"\s+[^\n]+)\*"
            ),
            combined,
            re.IGNORECASE,
        )
    )
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(combined)
        block = combined[match.start() : end].strip()
        starting_segment = next(
            segment for segment in segments if segment[1] <= match.start() < segment[2]
        )
        path, segment_start, _segment_end, text = starting_segment
        artifact = _artifact(
            "monster",
            match.group("name").strip(),
            path,
            match.start() - segment_start,
            {
                "name": match.group("name").strip(),
                "edition": "2024",
                "identity": match.group("identity").strip(),
                "statblock_source": block,
                "ruling_requirements": [
                    {
                        "kind": "statblock_semantics",
                        "reason": (
                            "Import core combat facts; unresolved unique actions "
                            "default to Agent DM ruling."
                        ),
                        "source_excerpt": block[:4000],
                        "default_resolver": "agent",
                        "ruling_kind": "generic_statblock_effect",
                    }
                ],
            },
            application_state="source_bound",
        )
        continuation_refs: list[str] = []
        for continuation_path, continuation_start, continuation_end, continuation_text in segments:
            if continuation_start >= end or continuation_end <= match.start():
                continue
            local_offset = max(0, match.start() - continuation_start)
            line = continuation_text.count("\n", 0, local_offset) + 1
            continuation_refs.append(f"bundled:srd2024/{continuation_path.name}#L{line}")
        artifact["rule_refs"] = continuation_refs
        artifact["source_citations"] = [
            {"source": source, "locator": source.rsplit("#L", 1)[-1]}
            for source in continuation_refs
        ]
        result.append(artifact)
    return result


def _artifact(
    kind: str,
    name: str,
    path: Path,
    offset: int,
    card: dict[str, Any],
    *,
    application_state: str = "ready",
) -> dict[str, Any]:
    line = path.read_text(encoding="utf-8").count("\n", 0, max(0, offset)) + 1
    source = f"bundled:srd2024/{path.name}#L{line}"
    if kind in {"feature", "feat"}:
        card.setdefault("activation", {"type": "passive", "cost": 0})
    artifact = {
        "id": f"{PACK_ID}.{kind}.{_slug(name)}",
        "kind": kind,
        "card": card,
        "rule_refs": [source],
        "mechanic_refs": list(card.get("mechanic_refs") or []),
        "source_citations": [{"source": source, "locator": f"line {line}"}],
    }
    if application_state != "ready":
        artifact["application_state"] = application_state
    return artifact


def _deduplicate(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, int] = {}
    result: list[dict[str, Any]] = []
    for value in values:
        identifier = str(value["id"])
        duplicate = seen.get(identifier, 0)
        seen[identifier] = duplicate + 1
        if duplicate:
            value = {**value, "id": f"{identifier}-{duplicate + 1}"}
        result.append(value)
    return result


def _slug(value: str) -> str:
    return ascii_slug(value) or "entry"


def _clean_source(value: str) -> str:
    value = re.sub(r"(?m)^#{1,4}\s+Page\s+\d+\s*$", "", value)
    value = re.sub(r"(?m)^<?br>?\s*$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"(?m)^<!--\s*Page\s+\d+\s*-->$", "", value)
    value = re.sub(r"(?m)^System Reference Document 5\.2\.1\s*$", "", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _between(text: str, start: str, end: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        return ""
    end_index = text.find(end, start_index + len(start))
    return text[start_index + len(start) : end_index if end_index >= 0 else len(text)]


def _sections(text: str, depth: int) -> Iterable[tuple[str, str, int]]:
    matches = list(re.finditer(rf"^#{{{depth}}}\s+(.+?)\s*$", text, re.MULTILINE))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        yield match.group(1).strip(), text[match.end() : end].strip(), match.start()


def _label(text: str, label: str) -> str:
    match = re.search(rf"(?m)^\*\*{re.escape(label)}:\*\*\s*(.+?)\s*$", text)
    return match.group(1).strip() if match else ""


def _inline_label(text: str, label: str) -> str:
    match = re.search(
        rf"\*\*{re.escape(label)}:\*\*\s*(.+?)(?=\s+\*\*[A-Za-z ]+:\*\*|\n|$)",
        text,
    )
    return match.group(1).strip() if match else ""


def _split_and_commas(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"\s+and\s+|,", value) if item.strip()]


def _bold_traits(text: str) -> list[tuple[str, str]]:
    matches = list(re.finditer(r"(?m)^\*\*([^*\n]+?)\.\*\*\s*", text))
    return [
        (
            match.group(1).strip(),
            _clean_source(
                text[
                    match.end() : (
                        matches[index + 1].start() if index + 1 < len(matches) else len(text)
                    )
                ]
            ),
        )
        for index, match in enumerate(matches)
    ]


def _activation(text: str) -> str:
    folded = text.casefold()
    if "as a bonus action" in folded:
        return "bonus_action"
    if "as a reaction" in folded or "take a reaction" in folded:
        return "reaction"
    if "as an action" in folded or "take the magic action" in folded:
        return "action"
    return "passive"


def _italic_prerequisite(text: str) -> str:
    match = re.search(r"(?im)^\*Prerequisite:\s*(.+?)\*\s*$", text)
    return match.group(1).strip() if match else ""


def _feat_heading_prerequisite(text: str) -> str:
    heading = re.match(r"^\*[^\n]*\(Prerequisite:\s*([^)]+)\)\*\s*$", text.splitlines()[0])
    return heading.group(1).strip() if heading else ""


def _structured_feat_prerequisites(text: str) -> list[dict[str, Any]]:
    """Parse the bounded prerequisite grammar used by the bundled SRD feats."""

    result: list[dict[str, Any]] = []
    level = re.search(r"Level\s+(\d+)\+", text, re.IGNORECASE)
    if level:
        result.append({"kind": "level_minimum", "minimum": int(level.group(1))})
    ability = re.search(
        r"((?:Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma)"
        r"(?:\s+or\s+(?:Strength|Dexterity|Constitution|Intelligence|Wisdom|Charisma))*)"
        r"\s+(\d+)\+",
        text,
        re.IGNORECASE,
    )
    if ability:
        result.append(
            {
                "kind": "ability_any_minimum",
                "abilities": [
                    item.casefold()
                    for item in re.split(r"\s+or\s+", ability.group(1), flags=re.IGNORECASE)
                ],
                "minimum": int(ability.group(2)),
            }
        )
    for feature in re.findall(r"([A-Za-z][A-Za-z ]+?)\s+Feature", text):
        result.append(
            {
                "kind": "feature_required",
                "feature": feature.strip(),
            }
        )
    if not result:
        result.append(
            {
                "kind": "dm_review",
                "text": text,
                "default_resolver": "agent",
                "ruling_kind": "source_or_scene_fact",
            }
        )
    return result


def _ability_increase_requirement(
    *,
    options: list[str],
    amount: int,
    maximum_score: int,
) -> dict[str, Any]:
    return {
        "field": "ability_score_increases",
        "kind": "ability_score_increase",
        "allowed_distributions": [[amount]],
        "ability_options": options,
        "maximum_score": maximum_score,
    }


def _known_feat_structure(name: str) -> dict[str, Any]:
    normalized = name.casefold()
    abilities = [
        "strength",
        "dexterity",
        "constitution",
        "intelligence",
        "wisdom",
        "charisma",
    ]
    if normalized == "ability score improvement":
        return {
            "selection_requirements": {
                "field": "ability_score_increases",
                "kind": "ability_score_increase",
                "allowed_distributions": [[2], [1, 1]],
                "ability_options": abilities,
                "maximum_score": 20,
            }
        }
    if normalized == "magic initiate":
        return {
            "selection_requirements": {
                "field": "magic_initiate",
                "kind": "magic_initiate",
                "source_class_options": ["Cleric", "Druid", "Wizard"],
                "spellcasting_ability_options": [
                    "intelligence",
                    "wisdom",
                    "charisma",
                ],
                "cantrip_count": 2,
                "level_1_spell_count": 1,
                "requires_new_choice": True,
                "choice_uniqueness_scope": "magic_initiate_spell_list",
            }
        }
    if normalized == "skilled":
        return {
            "selection_requirements": {
                "field": "proficiencies",
                "kind": "proficiency_grants",
                "count": 3,
                "skill_options": [
                    "Acrobatics",
                    "Animal Handling",
                    "Arcana",
                    "Athletics",
                    "Deception",
                    "History",
                    "Insight",
                    "Intimidation",
                    "Investigation",
                    "Medicine",
                    "Nature",
                    "Perception",
                    "Performance",
                    "Persuasion",
                    "Religion",
                    "Sleight of Hand",
                    "Stealth",
                    "Survival",
                ],
                "tool_options": [
                    "Alchemist's Supplies",
                    "Brewer's Supplies",
                    "Calligrapher's Supplies",
                    "Carpenter's Tools",
                    "Cartographer's Tools",
                    "Cobbler's Tools",
                    "Cook's Utensils",
                    "Glassblower's Tools",
                    "Jeweler's Tools",
                    "Leatherworker's Tools",
                    "Mason's Tools",
                    "Painter's Supplies",
                    "Potter's Tools",
                    "Smith's Tools",
                    "Tinker's Tools",
                    "Weaver's Tools",
                    "Woodcarver's Tools",
                    "Disguise Kit",
                    "Forgery Kit",
                    "Dice",
                    "Dragonchess",
                    "Playing Cards",
                    "Three-Dragon Ante",
                    "Herbalism Kit",
                    "Bagpipes",
                    "Drum",
                    "Dulcimer",
                    "Flute",
                    "Horn",
                    "Lute",
                    "Lyre",
                    "Pan Flute",
                    "Shawm",
                    "Viol",
                    "Navigator's Tools",
                    "Poisoner's Kit",
                    "Thieves' Tools",
                ],
            }
        }
    if normalized == "grappler":
        return {
            "selection_requirements": _ability_increase_requirement(
                options=["strength", "dexterity"],
                amount=1,
                maximum_score=20,
            )
        }
    if normalized == "boon of irresistible offense":
        return {
            "selection_requirements": _ability_increase_requirement(
                options=["strength", "dexterity"],
                amount=1,
                maximum_score=30,
            )
        }
    if normalized == "boon of spell recall":
        return {
            "selection_requirements": _ability_increase_requirement(
                options=["intelligence", "wisdom", "charisma"],
                amount=1,
                maximum_score=30,
            )
        }
    if normalized.startswith("boon of "):
        return {
            "selection_requirements": _ability_increase_requirement(
                options=abilities,
                amount=1,
                maximum_score=30,
            )
        }
    return {}


def _table_rows(text: str) -> Iterable[tuple[str, dict[str, str], int]]:
    lines = text.splitlines(keepends=True)
    table_name = ""
    offset = 0
    index = 0
    while index < len(lines):
        heading = re.match(r"^#{3,4}\s+(.+?)\s*$", lines[index])
        if heading:
            table_name = heading.group(1).strip()
        if (
            lines[index].lstrip().startswith("|")
            and index + 1 < len(lines)
            and re.match(r"^\s*\|(?:\s*:?-+:?\s*\|)+\s*$", lines[index + 1])
        ):
            headers = _table_cells(lines[index])
            index += 2
            offset += len(lines[index - 2]) + len(lines[index - 1])
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                values = _table_cells(lines[index])
                if values and any(values):
                    padded = [*values, *([""] * max(0, len(headers) - len(values)))]
                    yield table_name, dict(zip(headers, padded, strict=False)), offset
                offset += len(lines[index])
                index += 1
            continue
        offset += len(lines[index])
        index += 1


def _table_cells(line: str) -> list[str]:
    return [item.strip() for item in line.strip().strip("|").split("|")]


def _spell_metadata(value: str) -> tuple[int, str, list[str]] | None:
    level = re.fullmatch(r"Level\s+(\d+)\s+(.+?)\s+\(([^)]+)\)", value, re.IGNORECASE)
    if level:
        classes = [item.strip().casefold() for item in level.group(3).split(",")]
        return int(level.group(1)), level.group(2).strip().casefold(), classes
    cantrip = re.fullmatch(r"(.+?)\s+Cantrip\s+\(([^)]+)\)", value, re.IGNORECASE)
    if cantrip:
        classes = [item.strip().casefold() for item in cantrip.group(2).split(",")]
        return 0, cantrip.group(1).strip().casefold(), classes
    return None


def _spell_effect(block: str) -> str:
    duration = re.search(r"(?mi)^\*\*Duration:\*\*\s*[^\n]+\n?", block)
    body = block[duration.end() :] if duration else block
    return _clean_source(body)[:4000]


def _range(value: str) -> dict[str, Any]:
    folded = value.casefold()
    if folded == "self" or folded.startswith("self ("):
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
    if match:
        return {
            "kind": "timed",
            "value": int(match.group(1)),
            "unit": match.group(2),
            "concentration": concentration,
        }
    return {"kind": "special", "unit": "special", "concentration": concentration}


def _components(value: str) -> dict[str, Any]:
    prefix = value.split("(", 1)[0]
    tokens = {item.strip().casefold()[:1] for item in prefix.split(",") if item.strip()}
    return {
        "verbal": "v" in tokens,
        "somatic": "s" in tokens,
        "material": "m" in tokens,
        "material_description": value,
    }


def _looks_like_magic_item(value: str) -> bool:
    folded = value.casefold()
    return any(
        marker in folded
        for marker in (
            "armor",
            "potion",
            "ring",
            "rod",
            "scroll",
            "staff",
            "wand",
            "weapon",
            "wondrous item",
        )
    ) and any(
        rarity in folded
        for rarity in ("common", "uncommon", "rare", "legendary", "artifact", "varies")
    )


__all__ = [
    "PACK_ID",
    "PACK_VERSION",
    "build_srd2024_content",
    "parse_srd2024_monster_artifact",
]
