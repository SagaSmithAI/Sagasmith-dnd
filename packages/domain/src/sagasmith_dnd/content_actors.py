"""D&D validation and bundled actor-card.v3 preset actors."""

from __future__ import annotations

import copy
import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from sagasmith_core.content_pack import (
    ContentPackageError,
    build_actor_card,
    validate_actor_card,
)
from sagasmith_core.text import ascii_slug

from sagasmith_dnd.actor_types import CHARACTER_TYPES
from sagasmith_dnd.character_schema import (
    default_character_notes,
    validate_character_notes,
    validate_character_sheet,
)
from sagasmith_dnd.core_content import PACK_ID as SRD2014_CONTENT_PACK_ID
from sagasmith_dnd.core_content import PACK_VERSION as SRD2014_CONTENT_PACK_VERSION
from sagasmith_dnd.core_content_2024 import PACK_ID as SRD2024_CONTENT_PACK_ID
from sagasmith_dnd.core_content_2024 import PACK_VERSION as SRD2024_CONTENT_PACK_VERSION
from sagasmith_dnd.core_content_2024 import (
    build_srd2024_content,
    parse_srd2024_monster_artifact,
)
from sagasmith_dnd.statblocks import (
    ParsedStatblock,
    finalize_imported_actor_rulings,
    parse_2014_statblock,
)

DND5E_SYSTEM_ID = "dnd5e"
SRD2014_PRESET_PACK_ID = "dnd5e.presets.srd2014"
SRD2014_PRESET_PACK_VERSION = "2.0.0"
SRD2024_PRESET_PACK_ID = "dnd5e.presets.srd2024"
SRD2024_PRESET_PACK_VERSION = "2.0.0"
ACTOR_CARD_COMPILER = "sagasmith-dnd.actor-card.v3"


def build_dnd_content_actor(
    *,
    actor_id: str,
    version: str,
    actor_type: str,
    name: str,
    sheet: Mapping[str, Any],
    notes: Mapping[str, Any],
    player_name: str | None = None,
    summary: str = "",
    provenance: Mapping[str, Any] | None = None,
    bindings: Sequence[Mapping[str, Any]] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the only supported D&D PC, NPC, or monster card."""

    normalized_type = str(actor_type).strip().casefold()
    if normalized_type not in CHARACTER_TYPES:
        raise ContentPackageError(f"unsupported D&D actor_type: {actor_type}")
    normalized_sheet = validate_character_sheet(copy.deepcopy(dict(sheet)))
    normalized_notes = validate_character_notes(
        copy.deepcopy(dict(notes)), character_type=normalized_type
    )
    card = build_actor_card(
        actor_id=actor_id,
        version=version,
        system_id=DND5E_SYSTEM_ID,
        actor_type=normalized_type,
        name=name,
        player_name=player_name,
        summary=summary,
        sheet=normalized_sheet,
        notes=normalized_notes,
        provenance=provenance,
        bindings=bindings,
        metadata=metadata,
    )
    return validate_dnd_content_actor(card)


def validate_dnd_content_actor(actor: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a package-owned v3 actor with the full D&D sheet contracts."""

    value, normalized = _normalized_dnd_content_actor(actor)
    # Immutable actor archives predate optional intrinsic attacks. Absence is
    # equivalent only to the empty default; retain the original signed payload.
    if "intrinsic_attacks" not in value["sheet"].get("traits", {}):
        if normalized["sheet"]["traits"].get("intrinsic_attacks") == []:
            normalized["sheet"]["traits"].pop("intrinsic_attacks")
    if normalized["sheet"] != value["sheet"] or normalized["notes"] != value["notes"]:
        raise ContentPackageError("D&D content actor must use canonical sheet and notes")
    return value


def _normalized_dnd_content_actor(
    actor: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    image = actor.get("image") if isinstance(actor, Mapping) else None
    image_key = str(image.get("asset_key") or "") if isinstance(image, Mapping) else ""
    value = validate_actor_card(
        actor,
        expected_system_id=DND5E_SYSTEM_ID,
        assets_by_key=({image_key: {"kind": "actor_image"}} if image_key else None),
    )
    actor_type = str(value.get("actor_type") or "").casefold()
    if actor_type not in CHARACTER_TYPES:
        raise ContentPackageError(f"unsupported D&D actor_type: {actor_type}")
    try:
        sheet = validate_character_sheet(copy.deepcopy(dict(value["sheet"])))
        notes = validate_character_notes(
            copy.deepcopy(dict(value["notes"])), character_type=actor_type
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContentPackageError(f"invalid D&D content actor: {exc}") from exc
    normalized = build_actor_card(
        actor_id=str(value["id"]),
        version=str(value["version"]),
        system_id=str(value["system_id"]),
        actor_type=actor_type,
        name=str(value["name"]),
        player_name=value.get("player_name"),
        summary=str(value.get("summary") or ""),
        sheet=sheet,
        notes=notes,
        provenance=value["provenance"],
        bindings=value["bindings"],
        image_asset_key=(image_key or None),
        image_alt=(str(image.get("alt") or "") if isinstance(image, Mapping) else ""),
        metadata=value["metadata"],
    )
    return value, normalized


def canonicalize_dnd_content_actor(actor: Mapping[str, Any]) -> dict[str, Any]:
    """Return the current deterministic D&D form of an actor-card.v3 value."""

    _value, normalized = _normalized_dnd_content_actor(actor)
    return validate_dnd_content_actor(normalized)


def content_actor_from_statblock(
    parsed: ParsedStatblock,
    *,
    actor_id: str,
    version: str,
    actor_type: str,
    edition: str,
    source_text: str,
    source_refs: Sequence[str],
    pack_id: str,
    pack_version: str,
    distribution: str = "private",
    license_name: str = "user-supplied",
    attribution: str = "",
    tags: Sequence[str] | None = None,
    semantic_fill: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile one source-bound statblock into an actor-card.v3 value."""

    notes = default_character_notes()
    notes["profile"]["summary"] = parsed.summary
    source_checksum = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    return build_dnd_content_actor(
        actor_id=actor_id,
        version=version,
        actor_type=actor_type,
        name=parsed.name,
        summary=parsed.summary,
        sheet=finalize_imported_actor_rulings(parsed.sheet),
        notes=notes,
        provenance={
            "compiler": ACTOR_CARD_COMPILER,
            "edition": edition,
            "source_refs": list(source_refs),
            "source_text": source_text,
            "source_checksum": source_checksum,
            "pack": {"id": pack_id, "version": pack_version},
            "challenge_rating": parsed.challenge_rating,
            "experience_points": parsed.experience_points,
            "warnings": list(parsed.warnings),
            "normalization_notes": list(parsed.normalization_notes),
            "semantic_fill": (copy.deepcopy(semantic_fill) if semantic_fill is not None else None),
        },
        metadata={
            "title": parsed.name,
            "edition": edition,
            "distribution": distribution,
            "license": license_name,
            "attribution": attribution,
            "tags": list(tags or [actor_type, f"dnd5e-{edition}"]),
        },
    )


def build_srd2014_preset_actors(skill_root: Path) -> list[dict[str, Any]]:
    """Return every SRD 5.1 creature statblock as actor-card.v3 values."""

    return copy.deepcopy(_cached_srd2014_preset_actors(str(skill_root.resolve())))


@lru_cache(maxsize=4)
def _cached_srd2014_preset_actors(skill_root: str) -> list[dict[str, Any]]:
    root = (
        Path(skill_root)
        / "full"
        / "skills"
        / "dnd-dm"
        / "srd"
        / "references-2014-en"
        / "10_Monsters"
        / "Monsters_Each"
    )
    if not root.is_dir():
        return []
    cards: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.md"), key=lambda item: item.name.casefold()):
        source_text = path.read_text(encoding="utf-8")
        if path.name.casefold() == "customizing_npcs_(npc).md":
            # The folder also contains this prose-only guide, which is not an actor.
            continue
        slug = ascii_slug(path.stem.replace("_", " ")) or "actor"
        source_ref = "bundled:srd2014/10_Monsters/Monsters_Each/" + path.name
        actor_type = "npc" if "(npc)" in path.stem.casefold() else "monster"
        parsed = parse_2014_statblock(
            source_text,
            source_key=f"{SRD2014_PRESET_PACK_ID}.actor.{slug}",
            rule_refs=[source_ref],
        )
        cards.append(
            content_actor_from_statblock(
                parsed,
                actor_id=f"{SRD2014_PRESET_PACK_ID}.actor.{slug}",
                version=SRD2014_PRESET_PACK_VERSION,
                actor_type=actor_type,
                edition="2014",
                source_text=source_text,
                source_refs=[source_ref],
                pack_id=SRD2014_CONTENT_PACK_ID,
                pack_version=SRD2014_CONTENT_PACK_VERSION,
                distribution="shareable",
                license_name="CC-BY-4.0",
                attribution=(
                    "Includes material from the System Reference Document 5.1 by "
                    "Wizards of the Coast LLC, licensed under CC-BY-4.0."
                ),
                tags=[actor_type, "srd", "dnd5e-2014"],
            )
        )
    if not cards:
        return []
    return cards


def build_srd2024_preset_actors(skill_root: Path) -> list[dict[str, Any]]:
    """Return every SRD 5.2.1 monster/NPC entry as actor-card.v3 values."""

    return copy.deepcopy(_cached_srd2024_preset_actors(str(skill_root.resolve())))


@lru_cache(maxsize=4)
def _cached_srd2024_preset_actors(skill_root: str) -> list[dict[str, Any]]:
    _manifest, artifacts = build_srd2024_content(Path(skill_root))
    cards: list[dict[str, Any]] = []
    for artifact in artifacts:
        if artifact.get("kind") != "monster":
            continue
        parsed = parse_srd2024_monster_artifact(artifact)
        source_text = str(dict(artifact.get("card") or {}).get("statblock_source") or "")
        actor_id = str(artifact["id"]).replace(
            f"{SRD2024_CONTENT_PACK_ID}.monster.",
            f"{SRD2024_PRESET_PACK_ID}.actor.",
        )
        cards.append(
            content_actor_from_statblock(
                parsed,
                actor_id=actor_id,
                version=SRD2024_PRESET_PACK_VERSION,
                actor_type="monster",
                edition="2024",
                source_text=source_text,
                source_refs=list(artifact.get("rule_refs") or []),
                pack_id=SRD2024_CONTENT_PACK_ID,
                pack_version=SRD2024_CONTENT_PACK_VERSION,
                distribution="shareable",
                license_name="CC-BY-4.0",
                attribution=(
                    "Includes material from the System Reference Document 5.2.1 by "
                    "Wizards of the Coast LLC, licensed under CC-BY-4.0."
                ),
                tags=["monster", "srd", "dnd5e-2024"],
            )
        )
    if not cards:
        return []
    return cards


__all__ = [
    "DND5E_SYSTEM_ID",
    "ACTOR_CARD_COMPILER",
    "SRD2014_PRESET_PACK_ID",
    "SRD2014_PRESET_PACK_VERSION",
    "SRD2024_PRESET_PACK_ID",
    "SRD2024_PRESET_PACK_VERSION",
    "build_dnd_content_actor",
    "build_srd2014_preset_actors",
    "build_srd2024_preset_actors",
    "content_actor_from_statblock",
    "canonicalize_dnd_content_actor",
    "validate_dnd_content_actor",
]
