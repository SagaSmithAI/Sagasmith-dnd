"""Mechanics-only cards for standard 2014 content outside the open SRD."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

from sagasmith_dnd.content_import import audit_release_resolution_readiness
from sagasmith_dnd.content_resolution import finalize_bundled_artifact_resolutions
from sagasmith_dnd.spell_resolution import (
    SPELL_RESOLUTION_MECHANIC_ID,
    normalize_spell_resolution,
)
from sagasmith_dnd.standard_spell_ids import (
    CORE_BLADE_WARD_MECHANIC_ID,
    CORE_BLADE_WARD_SPELL_ID,
    CORE_DESTRUCTIVE_WAVE_SPELL_ID,
    CORE_WITCH_BOLT_MECHANIC_ID,
    CORE_WITCH_BOLT_SPELL_ID,
    STANDARD_2014_CONTENT_PACK_ID,
    STANDARD_2014_CONTENT_PACK_VERSION,
)

CORE_DRAGONBORN_BREATH_MECHANIC_ID = (
    "dnd5e.core.activity.dragonborn_breath_weapon"
)


def _species_spell_grant(
    name: str,
    *,
    level: int,
    classes: list[str],
    ability: str,
    minimum_level: int = 1,
    free_casts: int = 0,
    fixed_cast_level: int | None = None,
) -> dict[str, Any]:
    grant: dict[str, Any] = {
        "name": name,
        "level": level,
        "eligible_classes": classes,
        "method": "limited_use" if free_casts else "known",
        "spellcasting_ability": ability,
        "free_casts": free_casts,
        "recovers_on": "long_rest" if free_casts else None,
        "allow_slot_cast": False,
        "minimum_level": minimum_level,
        "ritual_only": False,
    }
    if fixed_cast_level is not None:
        grant["casting_overrides"] = {"fixed_cast_level": fixed_cast_level}
    return grant


def _standard_species_artifact(
    slug: str,
    name: str,
    *,
    page: int,
    grants: dict[str, Any],
    base_species: str | None = None,
    source_names: list[str] | None = None,
    mechanic_refs: list[str] | None = None,
) -> dict[str, Any]:
    artifact = {
        "id": f"{STANDARD_2014_CONTENT_PACK_ID}.species.{slug}",
        "kind": "species",
        "application_state": "selection_ready",
        "card": {
            "name": name,
            "base_species": base_species or name,
            "description": (
                "Reviewed 2014 species mechanics; the user's source printing owns "
                "the full descriptive text."
            ),
            "grants": grants,
        },
        "rule_refs": [f"book:players-handbook-2014:p{page}"],
        "mechanic_refs": list(mechanic_refs or []),
        "source_citations": [
            {
                "source": f"book:players-handbook-2014:p{page}",
                "locator": name,
            }
        ],
    }
    if source_names:
        artifact["_selection_source_names"] = source_names
    return artifact


def build_standard2014_content() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return reviewed executable facts without reproducing proprietary prose."""

    manifest, artifacts = _cached_standard2014_content()
    return deepcopy(manifest), deepcopy(artifacts)


@lru_cache(maxsize=1)
def _cached_standard2014_content() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    blade_ward = {
        "id": CORE_BLADE_WARD_SPELL_ID,
        "kind": "spell",
        "card": {
            "name": "Blade Ward",
            "level": 0,
            "classes": ["bard", "sorcerer", "warlock", "wizard"],
            "grant": {
                "source_type": "catalog",
                "source_key": "",
                "method": "unselected",
            },
            "access": {
                "known": False,
                "prepared": False,
                "ritual_available": False,
            },
            "definition": {
                "school": "abjuration",
                "casting_time": "1 action",
                "range": {"kind": "self"},
                "duration": {
                    "kind": "timed",
                    "value": 1,
                    "unit": "round",
                    "concentration": False,
                },
                "components": {
                    "verbal": True,
                    "somatic": True,
                    "material": False,
                    "material_description": "",
                },
                "effect": (
                    "Until the end of the caster's next turn, weapon attacks deal "
                    "resisted bludgeoning, piercing, and slashing damage to the caster."
                ),
            },
            "mechanic_refs": [CORE_BLADE_WARD_MECHANIC_ID],
        },
        "rule_refs": ["book:players-handbook-2014:p218-219"],
        "mechanic_refs": [CORE_BLADE_WARD_MECHANIC_ID],
        "source_citations": [
            {
                "source": "book:players-handbook-2014:p218-219",
                "locator": "Blade Ward",
            }
        ],
    }
    witch_bolt_resolution = normalize_spell_resolution(
        {
            "kind": "spell_attack",
            "targeting": {
                "mode": "creature",
                "requires_sight": False,
                "max_targets": 1,
            },
            "attack": {
                "mode": "ranged",
                "count": {"base": 1},
                "damage": {
                    "base_dice": "1d12",
                    "per_slot_dice": "1d12",
                    "slot_base_level": 1,
                    "damage_type": "lightning",
                },
            },
        }
    )
    witch_bolt = {
        "id": CORE_WITCH_BOLT_SPELL_ID,
        "kind": "spell",
        "card": {
            "name": "Witch Bolt",
            "level": 1,
            "classes": ["sorcerer", "warlock", "wizard"],
            "grant": {
                "source_type": "catalog",
                "source_key": "",
                "method": "unselected",
            },
            "access": {
                "known": False,
                "prepared": False,
                "ritual_available": False,
            },
            "definition": {
                "school": "evocation",
                "casting_time": "1 action",
                "range": {"kind": "distance", "normal_ft": 30},
                "duration": {
                    "kind": "timed",
                    "value": 1,
                    "unit": "minute",
                    "concentration": True,
                },
                "components": {
                    "verbal": True,
                    "somatic": True,
                    "material": True,
                    "material_description": "non-costly material component",
                },
                "effect": (
                    "A hit establishes a 30-foot sustained lightning tether. On later "
                    "turns the caster can spend an action for 1d12 lightning damage; "
                    "another action, excess range, or total cover ends the spell."
                ),
            },
            "resolution": witch_bolt_resolution,
            "mechanic_refs": [
                SPELL_RESOLUTION_MECHANIC_ID,
                CORE_WITCH_BOLT_MECHANIC_ID,
            ],
        },
        "rule_refs": ["book:players-handbook-2014:p289"],
        "mechanic_refs": [
            SPELL_RESOLUTION_MECHANIC_ID,
            CORE_WITCH_BOLT_MECHANIC_ID,
        ],
        "source_citations": [
            {
                "source": "book:players-handbook-2014:p289",
                "locator": "Witch Bolt",
            }
        ],
    }
    destructive_wave = {
        "id": CORE_DESTRUCTIVE_WAVE_SPELL_ID,
        "kind": "spell",
        "card": {
            "name": "Destructive Wave",
            "level": 5,
            "classes": ["paladin"],
            "grant": {
                "source_type": "catalog",
                "source_key": "",
                "method": "unselected",
            },
            "access": {
                "known": False,
                "prepared": False,
                "ritual_available": False,
            },
            "definition": {
                "school": "evocation",
                "casting_time": "1 action",
                "range": {
                    "kind": "self",
                    "area": "30-foot radius",
                },
                "duration": {
                    "kind": "instantaneous",
                    "concentration": False,
                },
                "components": {
                    "verbal": True,
                    "somatic": False,
                    "material": False,
                    "material_description": "",
                },
                "effect": (
                    "The caster chooses creatures in a 30-foot radius. Each makes "
                    "a Constitution save against thunder plus radiant or necrotic "
                    "damage and being knocked prone; success halves damage and "
                    "prevents prone."
                ),
            },
            "mechanic_refs": [],
        },
        "rule_refs": ["book:players-handbook-2014:p231"],
        "mechanic_refs": [],
        "source_citations": [
            {
                "source": "book:players-handbook-2014:p231",
                "locator": "Destructive Wave",
            }
        ],
    }
    line_ancestries = (
        ("black", "Black", "acid"),
        ("blue", "Blue", "lightning"),
        ("brass", "Brass", "fire"),
        ("bronze", "Bronze", "lightning"),
        ("copper", "Copper", "acid"),
    )
    cone_ancestries = (
        ("gold", "Gold", "fire", "dexterity"),
        ("green", "Green", "poison", "constitution"),
        ("red", "Red", "fire", "dexterity"),
        ("silver", "Silver", "cold", "constitution"),
        ("white", "White", "cold", "constitution"),
    )
    dragonborn = _standard_species_artifact(
        "dragonborn",
        "Dragonborn",
        page=34,
        mechanic_refs=[CORE_DRAGONBORN_BREATH_MECHANIC_ID],
        grants={
            "ability_score_increases": {"strength": 2, "charisma": 1},
            "size": "medium",
            "walk_speed": 30,
            "languages": ["Common", "Draconic"],
            "damage_affinity_choice": {
                "id": "draconic_ancestry",
                "options": [
                    *[
                        {
                            "id": identifier,
                            "name": name,
                            "damage_type": damage_type,
                            "save_ability": "dexterity",
                            "area": {
                                "shape": "line",
                                "length_ft": 30,
                                "width_ft": 5,
                            },
                        }
                        for identifier, name, damage_type in line_ancestries
                    ],
                    *[
                        {
                            "id": identifier,
                            "name": name,
                            "damage_type": damage_type,
                            "save_ability": save_ability,
                            "area": {"shape": "cone", "length_ft": 15},
                        }
                        for identifier, name, damage_type, save_ability in cone_ancestries
                    ],
                ],
                "resistance": True,
                "activity": {
                    "id": "breath-weapon",
                    "name": "Breath Weapon",
                    "uses": {"max": 1, "recovers_on": "short_rest"},
                    "save_dc": {
                        "base": 8,
                        "ability": "constitution",
                        "include_proficiency": True,
                    },
                    "damage_by_level": {
                        "1": "2d6",
                        "6": "3d6",
                        "11": "4d6",
                        "16": "5d6",
                    },
                },
            },
            "features": [],
            "unresolved": [],
        },
    )
    drow = _standard_species_artifact(
        "drow",
        "Drow",
        page=24,
        base_species="Elf",
        source_names=["Dark Elf (Drow)", "Drow"],
        grants={
            "ability_score_increases": {"dexterity": 2, "charisma": 1},
            "size": "medium",
            "walk_speed": 30,
            "darkvision_ft": 120,
            "languages": ["Common", "Elvish"],
            "skill_proficiencies": ["perception"],
            "weapon_proficiencies": [
                "longsword",
                "shortsword",
                "shortbow",
                "longbow",
                "rapier",
                "hand crossbow",
            ],
            "spell_grants": [
                _species_spell_grant(
                    "Dancing Lights",
                    level=0,
                    classes=["Bard", "Sorcerer", "Wizard"],
                    ability="charisma",
                ),
                _species_spell_grant(
                    "Faerie Fire",
                    level=1,
                    classes=["Bard", "Druid"],
                    ability="charisma",
                    minimum_level=3,
                    free_casts=1,
                ),
                _species_spell_grant(
                    "Darkness",
                    level=2,
                    classes=["Sorcerer", "Warlock", "Wizard"],
                    ability="charisma",
                    minimum_level=5,
                    free_casts=1,
                ),
            ],
            "features": [
                {
                    "name": "Fey Ancestry",
                    "description": "Advantage against being charmed; magic cannot cause sleep.",
                },
                {
                    "name": "Trance",
                    "description": "A four-hour trance supplies the species' long-rest sleep need.",
                },
                {
                    "name": "Sunlight Sensitivity",
                    "description": (
                        "Direct sunlight imposes disadvantage on attacks and sight-based "
                        "Wisdom (Perception) checks."
                    ),
                },
            ],
            "unresolved": [],
        },
    )
    forest_gnome = _standard_species_artifact(
        "forest-gnome",
        "Forest Gnome",
        page=37,
        base_species="Gnome",
        grants={
            "ability_score_increases": {"intelligence": 2, "dexterity": 1},
            "size": "small",
            "walk_speed": 25,
            "darkvision_ft": 60,
            "languages": ["Common", "Gnomish"],
            "spell_grants": [
                _species_spell_grant(
                    "Minor Illusion",
                    level=0,
                    classes=["Bard", "Sorcerer", "Warlock", "Wizard"],
                    ability="intelligence",
                )
            ],
            "features": [
                {
                    "name": "Gnome Cunning",
                    "description": (
                        "Advantage on Intelligence, Wisdom, and Charisma saves against magic."
                    ),
                },
                {
                    "name": "Speak with Small Beasts",
                    "description": (
                        "Communicate simple ideas through sounds and gestures with Small "
                        "or smaller beasts."
                    ),
                },
            ],
            "unresolved": [],
        },
    )
    tiefling = _standard_species_artifact(
        "tiefling",
        "Tiefling",
        page=43,
        grants={
            "ability_score_increases": {"intelligence": 1, "charisma": 2},
            "size": "medium",
            "walk_speed": 30,
            "darkvision_ft": 60,
            "languages": ["Common", "Infernal"],
            "resistances": ["fire"],
            "spell_grants": [
                _species_spell_grant(
                    "Thaumaturgy",
                    level=0,
                    classes=["Cleric"],
                    ability="charisma",
                ),
                _species_spell_grant(
                    "Hellish Rebuke",
                    level=1,
                    classes=["Warlock"],
                    ability="charisma",
                    minimum_level=3,
                    free_casts=1,
                    fixed_cast_level=2,
                ),
                _species_spell_grant(
                    "Darkness",
                    level=2,
                    classes=["Sorcerer", "Warlock", "Wizard"],
                    ability="charisma",
                    minimum_level=5,
                    free_casts=1,
                ),
            ],
            "features": [
                {
                    "name": "Hellish Resistance",
                    "description": "Resistance to fire damage.",
                }
            ],
            "unresolved": [],
        },
    )
    artifacts = finalize_bundled_artifact_resolutions(
        [
            blade_ward,
            destructive_wave,
            dragonborn,
            drow,
            forest_gnome,
            tiefling,
            witch_bolt,
        ],
        source_root=Path("."),
        source_prefix="book:",
    )
    native_mechanic_refs = sorted(
        {
            str(mechanic_ref)
            for artifact in artifacts
            for mechanic_ref in artifact.get("mechanic_refs", [])
        }
    )
    manifest = {
        "id": STANDARD_2014_CONTENT_PACK_ID,
        "version": STANDARD_2014_CONTENT_PACK_VERSION,
        "title": "D&D 5e Standard 2014 Mechanics",
        "namespace": STANDARD_2014_CONTENT_PACK_ID,
        "system_id": "dnd5e",
        "editions": ["2014"],
        "capabilities": [],
        "native_mechanic_refs": native_mechanic_refs,
        "content_kinds": ["species", "spell"],
        "resolution_policy": "build_time_complete",
        "resolution_readiness": audit_release_resolution_readiness(artifacts),
    }
    return manifest, artifacts


__all__ = ["build_standard2014_content"]
