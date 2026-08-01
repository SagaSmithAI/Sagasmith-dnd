"""D&D validation and bundled portable actor-card preset packs."""

from __future__ import annotations

import copy
import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from sagasmith_core.portable import (
    PortableContentError,
    build_actor_card,
    build_preset_pack,
    validate_actor_card,
    validate_preset_pack,
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
from sagasmith_dnd.statblocks import ParsedStatblock, parse_2014_statblock

DND5E_SYSTEM_ID = "dnd5e"
SRD2014_PRESET_PACK_ID = "dnd5e.presets.srd2014"
SRD2014_PRESET_PACK_VERSION = "1.0.0"
SRD2024_PRESET_PACK_ID = "dnd5e.presets.srd2024"
SRD2024_PRESET_PACK_VERSION = "1.0.0"
PORTABLE_CARD_COMPILER = "sagasmith-dnd.portable-card.v1"


def build_dnd_actor_card(
    *,
    portable_id: str,
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
    dependencies: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a canonical D&D PC, NPC, or monster card using one format."""

    normalized_type = str(actor_type).strip().casefold()
    if normalized_type not in CHARACTER_TYPES:
        raise PortableContentError(f"unsupported D&D actor_type: {actor_type}")
    normalized_sheet = validate_character_sheet(copy.deepcopy(dict(sheet)))
    normalized_notes = validate_character_notes(
        copy.deepcopy(dict(notes)), character_type=normalized_type
    )
    card = build_actor_card(
        portable_id=portable_id,
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
        dependencies=dependencies,
    )
    return validate_dnd_actor_card(card)


def validate_dnd_actor_card(card: Mapping[str, Any]) -> dict[str, Any]:
    """Validate checksums plus the full D&D v2 sheet and notes contracts."""

    value = validate_actor_card(card, expected_system_id=DND5E_SYSTEM_ID)
    payload = value["payload"]
    actor_type = str(payload["actor_type"]).casefold()
    if actor_type not in CHARACTER_TYPES:
        raise PortableContentError(f"unsupported D&D actor_type: {actor_type}")
    try:
        sheet = validate_character_sheet(copy.deepcopy(payload["sheet"]))
        notes = validate_character_notes(
            copy.deepcopy(payload["notes"]), character_type=actor_type
        )
    except ValueError as exc:
        raise PortableContentError(f"invalid D&D actor card: {exc}") from exc
    if sheet != payload["sheet"]:
        raise PortableContentError("D&D actor card sheet must use the canonical v2 form")
    if notes != payload["notes"]:
        raise PortableContentError("D&D actor card notes must use the canonical v2 form")
    return value


def actor_card_from_statblock(
    parsed: ParsedStatblock,
    *,
    portable_id: str,
    version: str,
    actor_type: str,
    edition: str,
    source_text: str,
    source_refs: Sequence[str],
    pack_id: str,
    pack_version: str,
) -> dict[str, Any]:
    """Compile one source-bound statblock into a fully portable actor card."""

    notes = default_character_notes()
    notes["profile"]["summary"] = parsed.summary
    source_checksum = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    return build_dnd_actor_card(
        portable_id=portable_id,
        version=version,
        actor_type=actor_type,
        name=parsed.name,
        summary=parsed.summary,
        sheet=parsed.sheet,
        notes=notes,
        provenance={
            "compiler": PORTABLE_CARD_COMPILER,
            "edition": edition,
            "source_refs": list(source_refs),
            "source_text": source_text,
            "source_checksum": source_checksum,
            "pack": {"id": pack_id, "version": pack_version},
            "challenge_rating": parsed.challenge_rating,
            "experience_points": parsed.experience_points,
            "warnings": list(parsed.warnings),
            "normalization_notes": list(parsed.normalization_notes),
        },
        metadata={
            "title": parsed.name,
            "edition": edition,
            "license": "CC-BY-4.0",
            "tags": [actor_type, "srd", f"dnd5e-{edition}"],
        },
        dependencies=[
            {
                "kind": "content_pack",
                "id": pack_id,
                "version": pack_version,
                "optional": False,
            }
        ],
    )


def build_srd2014_preset_pack(skill_root: Path) -> dict[str, Any]:
    """Return every SRD 5.1 creature statblock as a validated actor-card pack."""

    return copy.deepcopy(_cached_srd2014_preset_pack(str(skill_root.resolve())))


@lru_cache(maxsize=4)
def _cached_srd2014_preset_pack(skill_root: str) -> dict[str, Any]:
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
        return {}
    cards: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.md"), key=lambda item: item.name.casefold()):
        source_text = path.read_text(encoding="utf-8")
        if path.name.casefold() == "customizing_npcs_(npc).md":
            # The folder also contains this prose-only guide, which is not an actor.
            continue
        slug = ascii_slug(path.stem.replace("_", " ")) or "actor"
        source_ref = (
            "bundled:srd2014/10_Monsters/Monsters_Each/" + path.name
        )
        actor_type = "npc" if "(npc)" in path.stem.casefold() else "monster"
        parsed = parse_2014_statblock(
            source_text,
            source_key=f"{SRD2014_PRESET_PACK_ID}.actor.{slug}",
            rule_refs=[source_ref],
        )
        cards.append(
            actor_card_from_statblock(
                parsed,
                portable_id=f"{SRD2014_PRESET_PACK_ID}.actor.{slug}",
                version=SRD2014_PRESET_PACK_VERSION,
                actor_type=actor_type,
                edition="2014",
                source_text=source_text,
                source_refs=[source_ref],
                pack_id=SRD2014_CONTENT_PACK_ID,
                pack_version=SRD2014_CONTENT_PACK_VERSION,
            )
        )
    if not cards:
        return {}
    return build_preset_pack(
        portable_id=SRD2014_PRESET_PACK_ID,
        version=SRD2014_PRESET_PACK_VERSION,
        system_id=DND5E_SYSTEM_ID,
        cards=cards,
        metadata={
            "title": "D&D 5e SRD 5.1 Actor Presets",
            "edition": "2014",
            "license": "CC-BY-4.0",
            "attribution": (
                "Includes material from the System Reference Document 5.1 by "
                "Wizards of the Coast LLC, licensed under CC-BY-4.0."
            ),
            "content_kinds": ["npc", "monster"],
        },
        dependencies=[
            {
                "kind": "content_pack",
                "id": SRD2014_CONTENT_PACK_ID,
                "version": SRD2014_CONTENT_PACK_VERSION,
                "optional": False,
            }
        ],
    )


def build_srd2024_preset_pack(skill_root: Path) -> dict[str, Any]:
    """Return every SRD 5.2.1 monster/NPC entry as a validated actor-card pack."""

    return copy.deepcopy(_cached_srd2024_preset_pack(str(skill_root.resolve())))


@lru_cache(maxsize=4)
def _cached_srd2024_preset_pack(skill_root: str) -> dict[str, Any]:
    _manifest, artifacts = build_srd2024_content(Path(skill_root))
    cards: list[dict[str, Any]] = []
    for artifact in artifacts:
        if artifact.get("kind") != "monster":
            continue
        parsed = parse_srd2024_monster_artifact(artifact)
        source_text = str(dict(artifact.get("card") or {}).get("statblock_source") or "")
        portable_id = str(artifact["id"]).replace(
            f"{SRD2024_CONTENT_PACK_ID}.monster.",
            f"{SRD2024_PRESET_PACK_ID}.actor.",
        )
        cards.append(
            actor_card_from_statblock(
                parsed,
                portable_id=portable_id,
                version=SRD2024_PRESET_PACK_VERSION,
                actor_type="monster",
                edition="2024",
                source_text=source_text,
                source_refs=list(artifact.get("rule_refs") or []),
                pack_id=SRD2024_CONTENT_PACK_ID,
                pack_version=SRD2024_CONTENT_PACK_VERSION,
            )
        )
    if not cards:
        return {}
    return build_preset_pack(
        portable_id=SRD2024_PRESET_PACK_ID,
        version=SRD2024_PRESET_PACK_VERSION,
        system_id=DND5E_SYSTEM_ID,
        cards=cards,
        metadata={
            "title": "D&D 5e SRD 5.2.1 Actor Presets",
            "edition": "2024",
            "license": "CC-BY-4.0",
            "attribution": (
                "Includes material from the System Reference Document 5.2.1 by "
                "Wizards of the Coast LLC, licensed under CC-BY-4.0."
            ),
            "content_kinds": ["npc", "monster"],
        },
        dependencies=[
            {
                "kind": "content_pack",
                "id": SRD2024_CONTENT_PACK_ID,
                "version": SRD2024_CONTENT_PACK_VERSION,
                "optional": False,
            }
        ],
    )


def preset_pack_catalog_definition(
    package: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Project a portable preset pack into the existing installed catalog."""

    value = validate_preset_pack(package, expected_system_id=DND5E_SYSTEM_ID)
    metadata = dict(value["metadata"])
    artifacts = []
    for card in value["payload"]["cards"]:
        payload = card["payload"]
        provenance = dict(payload.get("provenance") or {})
        artifacts.append(
            {
                "id": card["id"],
                "kind": "actor_card",
                "card": {
                    "name": payload["name"],
                    "edition": provenance.get("edition"),
                    "actor_type": payload["actor_type"],
                    "portable_card": card,
                },
                "rule_refs": list(provenance.get("source_refs") or []),
                "mechanic_refs": [],
                "source_citations": [
                    {"source": source_ref}
                    for source_ref in provenance.get("source_refs") or []
                ],
            }
        )
    return (
        {
            "id": value["id"],
            "version": value["version"],
            "title": metadata.get("title") or value["id"],
            "namespace": value["id"],
            "system_id": value["system_id"],
            "editions": [str(metadata.get("edition") or "")],
            "capabilities": [],
            "native_mechanic_refs": [],
            "content_kinds": ["actor_card"],
            "license": metadata.get("license"),
            "attribution": metadata.get("attribution"),
        },
        artifacts,
    )


__all__ = [
    "DND5E_SYSTEM_ID",
    "PORTABLE_CARD_COMPILER",
    "SRD2014_PRESET_PACK_ID",
    "SRD2014_PRESET_PACK_VERSION",
    "SRD2024_PRESET_PACK_ID",
    "SRD2024_PRESET_PACK_VERSION",
    "actor_card_from_statblock",
    "build_dnd_actor_card",
    "build_srd2014_preset_pack",
    "build_srd2024_preset_pack",
    "preset_pack_catalog_definition",
    "validate_dnd_actor_card",
]
