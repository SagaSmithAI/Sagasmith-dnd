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
    CORE_WITCH_BOLT_MECHANIC_ID,
    CORE_WITCH_BOLT_SPELL_ID,
    STANDARD_2014_CONTENT_PACK_ID,
    STANDARD_2014_CONTENT_PACK_VERSION,
)


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
    artifacts = finalize_bundled_artifact_resolutions(
        [blade_ward, witch_bolt],
        source_root=Path("."),
        source_prefix="book:",
    )
    native_mechanic_refs = sorted(
        {
            str(mechanic_ref)
            for artifact in artifacts
            for mechanic_ref in artifact["mechanic_refs"]
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
        "content_kinds": ["spell"],
        "resolution_policy": "build_time_complete",
        "resolution_readiness": audit_release_resolution_readiness(artifacts),
    }
    return manifest, artifacts


__all__ = ["build_standard2014_content"]
