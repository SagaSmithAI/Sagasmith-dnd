"""Source-bound materialization for reviewed official magic-item weapons.

The official addon archives intentionally keep their source cards portable and
rights-aware.  This module binds only the exact reviewed artifact fingerprints
to deterministic inventory mechanics that the runtime already understands.  It
never infers a weapon profile from prose or from an item name.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from sagasmith_dnd.content_validation import content_fingerprint

EBERRON_ITEM_PACK_ID = (
    "dnd5e.addon.rulebook.d-d-5e-eberron-rising-from-the-last-war.31293633134f"
)
ARCANE_PROPULSION_ARM_ID = EBERRON_ITEM_PACK_ID + ".item.arcane-propulsion-arm"
ARMBLADE_ID = EBERRON_ITEM_PACK_ID + ".item.armblade"
DYRRN_TENTACLE_WHIP_ID = EBERRON_ITEM_PACK_ID + ".item.dyrrn-s-tentacle-whip"

# These are content fingerprints of the reviewed source cards, not checksums of
# the private archive.  The archive verifier proves that the runtime artifact
# has this exact portable content before this table is consulted.
_REVIEWED_ITEM_HASHES = {
    ARCANE_PROPULSION_ARM_ID: "5eccc1ccc3aa113aa5c1ead43fe85f34164233e58e6d33930512f882a499dc9d",
    ARMBLADE_ID: "93afc530d64175dc6468b7ffd203d8cd95dd4ce63160430b6c02c34e5113b22c",
    DYRRN_TENTACLE_WHIP_ID: "cbc616dac77a7887a8fe4b19fcfb517bbd2bb189134e5d08d5f5989941e6910e",
}
BOUND_OFFICIAL_ITEM_IDS = frozenset(_REVIEWED_ITEM_HASHES)


def is_bound_official_item_id(pack_id: str, artifact_id: str) -> bool:
    """Whether an identity is reserved for a reviewed official materializer."""

    return pack_id == EBERRON_ITEM_PACK_ID and artifact_id in BOUND_OFFICIAL_ITEM_IDS


def reviewed_official_item_hash(artifact_id: str) -> str | None:
    """Return the pinned source-card hash for one executable official item."""

    return _REVIEWED_ITEM_HASHES.get(str(artifact_id))


def official_item_profile(
    pack_id: str,
    artifact: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Return the reviewed profile metadata for one exact official artifact."""

    artifact_id = str(artifact.get("id") or "")
    if not is_bound_official_item_id(pack_id, artifact_id):
        return None
    if str(artifact.get("kind") or "") != "item":
        return None
    if content_fingerprint(artifact) != _REVIEWED_ITEM_HASHES[artifact_id]:
        return None
    return {
        "artifact_id": artifact_id,
        "reviewed_content_hash": _REVIEWED_ITEM_HASHES[artifact_id],
        "selection_fields": ("base_weapon_artifact_id",) if artifact_id == ARMBLADE_ID else (),
        "materializer": "dnd5e.character.inventory_item.v1",
        "qualification": (
            "missing_hand_or_arm"
            if artifact_id == ARCANE_PROPULSION_ARM_ID
            else "warforged"
            if artifact_id == ARMBLADE_ID
            else "any"
        ),
    }


def materialize_official_item_template(
    pack_id: str,
    artifact: Mapping[str, Any],
    *,
    base_weapon_template: Mapping[str, Any] | None = None,
    pack_version: str | None = None,
) -> dict[str, Any] | None:
    """Build one executable template from an exact reviewed source card.

    ``None`` means the artifact is not one of the bound profiles, or that an
    Armblade base weapon choice was not supplied.  The caller remains
    responsible for validating the selected core artifact and for adding the
    item to the character transactionally.
    """

    profile = official_item_profile(pack_id, artifact)
    if profile is None:
        return None
    card = dict(artifact.get("card") or {})
    description = str(card.get("description") or "")
    artifact_id = profile["artifact_id"]
    if artifact_id == ARMBLADE_ID:
        if base_weapon_template is None:
            return None
        base = copy.deepcopy(dict(base_weapon_template))
        if base.get("kind") != "weapon":
            return None
        mechanics = copy.deepcopy(dict(base.get("mechanics") or {}))
        mechanics.update(
            {
                "magical": True,
                "magic_bonus": 0,
                "official_item": {
                    "kind": "armblade",
                    "state": "extended",
                    "qualification": "warforged",
                    "toggle_activation": "bonus_action",
                    "occupies_hand_when_extended": True,
                    "inseparable_while_attuned": True,
                },
            }
        )
        return {
            **base,
            "name": "Armblade",
            "source_key": (
                f"{pack_id}@{pack_version}:{artifact_id}"
                if pack_version
                else f"{pack_id}@unknown:{artifact_id}"
            ),
            "description": description,
            "kind": "weapon",
            "quantity": 1,
            "attunement": "required",
            "mechanics": mechanics,
        }
    if artifact_id == ARCANE_PROPULSION_ARM_ID:
        return {
            "name": "Arcane Propulsion Arm",
            "kind": "weapon",
            "quantity": 1,
            "source_key": (
                f"{pack_id}@{pack_version}:{artifact_id}"
                if pack_version
                else f"{pack_id}@unknown:{artifact_id}"
            ),
            "description": description,
            "attunement": "required",
            "mechanics": {
                "category": "other",
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d8",
                "damage_type": "force",
                "properties": ["thrown"],
                "normal_range_ft": 0,
                "long_range_ft": 0,
                "thrown_normal_range_ft": 20,
                "thrown_long_range_ft": 60,
                "proficient": True,
                "magical": True,
                "magic_bonus": 0,
                "official_item": {
                    "kind": "arcane_propulsion_arm",
                    "state": "attached",
                    "qualification": "missing_hand_or_arm",
                    "return_on_throw": True,
                    "remove_action": True,
                },
            },
        }
    if artifact_id == DYRRN_TENTACLE_WHIP_ID:
        return {
            "name": "Dyrrn's Tentacle Whip",
            "kind": "weapon",
            "quantity": 1,
            "source_key": (
                f"{pack_id}@{pack_version}:{artifact_id}"
                if pack_version
                else f"{pack_id}@unknown:{artifact_id}"
            ),
            "description": description,
            "attunement": "required",
            "mechanics": {
                "category": "martial",
                "attack_type": "melee",
                "attack_ability": "dexterity",
                "damage_formula": "1d4",
                "damage_type": "slashing",
                "additional_damage": [
                    {"damage_formula": "1d6", "damage_type": "psychic", "damage_bonus": 0}
                ],
                "on_hit_effect": (
                    "When you roll a 20 on the d20 for an attack roll with this weapon, "
                    "the target is stunned until the end of its next turn."
                ),
                "properties": ["finesse", "reach"],
                "reach_ft": 10,
                "proficient": False,
                "magical": True,
                "magic_bonus": 2,
                "official_item": {
                    "kind": "dyrrn_tentacle_whip",
                    "state": "drawn",
                    "qualification": "any",
                    "disadvantage_against_species": ["aberration"],
                    "natural_20_stun": True,
                    "stun_duration": "target_end_next_turn",
                    "sheath_draw_activation": "bonus_action",
                    "cursed_attunement": True,
                },
            },
        }
    return None


__all__ = [
    "ARCANE_PROPULSION_ARM_ID",
    "ARMBLADE_ID",
    "BOUND_OFFICIAL_ITEM_IDS",
    "DYRRN_TENTACLE_WHIP_ID",
    "EBERRON_ITEM_PACK_ID",
    "is_bound_official_item_id",
    "materialize_official_item_template",
    "official_item_profile",
    "reviewed_official_item_hash",
]
