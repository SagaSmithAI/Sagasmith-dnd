"""Canonical reads and validations for character-bound D&D source cards."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sagasmith_dnd.resolution_plan import (
    CompiledResolutionPlan,
    ResolutionPlanCompilationError,
    compile_resolution_plan,
)


class CharacterSourceCardError(ValueError):
    """Raised when a character source-card reference is ambiguous or invalid."""


def character_source_card(
    sheet: dict[str, Any],
    source_card_id: str,
    source_card_kind: str,
) -> dict[str, Any]:
    """Resolve exactly one durable source card from a canonical character sheet."""

    collections = {
        "activity": ("activities",),
        "feature": ("features", "feats"),
        "monster_action": ("activities",),
        "spell": ("spells",),
        "trait": ("features",),
    }.get(source_card_kind)
    if source_card_kind == "item":
        matches = [
            item
            for item in dict(sheet.get("inventory") or {}).get("items", [])
            if isinstance(item, dict) and str(item.get("id") or "") == source_card_id
        ]
    elif collections is None:
        raise CharacterSourceCardError(
            "character-bound resolution plans require an activity, feature, "
            "item, monster action, spell, or trait source card"
        )
    else:
        matches = [
            item
            for collection in collections
            for item in dict(sheet.get("content") or {}).get(collection, [])
            if isinstance(item, dict) and str(item.get("id") or "") == source_card_id
        ]
    if len(matches) != 1:
        raise CharacterSourceCardError(
            "source card must resolve to exactly one recorded character card"
        )
    return deepcopy(matches[0])


def character_resolution_plan(
    sheet: dict[str, Any],
    source_card_id: str,
    source_card_kind: str,
) -> tuple[dict[str, Any], CompiledResolutionPlan]:
    """Resolve one executable plan from the exact recorded character card."""

    card = character_source_card(sheet, source_card_id, source_card_kind)
    if not isinstance(card.get("resolution_plan"), dict):
        raise CharacterSourceCardError("source card has no recorded resolution plan")
    try:
        compiled = compile_resolution_plan(card["resolution_plan"])
    except ResolutionPlanCompilationError as error:
        raise CharacterSourceCardError(f"recorded resolution plan is invalid: {error}") from error
    if (
        compiled.source_card_id != source_card_id
        or compiled.source_card_kind != source_card_kind
    ):
        raise CharacterSourceCardError("recorded resolution plan does not match its source card")
    return card, compiled


def character_activity_source_card(
    sheet: dict[str, Any],
    activity_id: str,
    *,
    character_type: str,
    non_player_character_types: frozenset[str] = frozenset({"npc", "monster"}),
) -> tuple[dict[str, Any], str]:
    """Resolve an activatable card across every canonical sheet section."""

    matches: list[tuple[dict[str, Any], str]] = []
    content = dict(sheet.get("content") or {})
    for item in content.get("activities", []):
        if isinstance(item, dict) and str(item.get("id") or "") == activity_id:
            matches.append(
                (
                    item,
                    "monster_action"
                    if character_type in non_player_character_types
                    else "activity",
                )
            )
    for section in ("features", "feats"):
        for item in content.get(section, []):
            if isinstance(item, dict) and str(item.get("id") or "") == activity_id:
                matches.append((item, "feature"))
    if len(matches) != 1:
        raise CharacterSourceCardError(
            "activity source card must resolve exactly once across "
            "activities, features, and feats"
        )
    card, source_card_kind = matches[0]
    return deepcopy(card), source_card_kind


def source_card_evidence_texts(source_card: dict[str, Any]) -> tuple[str, ...]:
    """Collect normalized original wording without treating it as executable."""

    values: list[str] = []

    def normalize(value: Any) -> str:
        return " ".join(str(value or "").split()).strip()

    def collect(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                if child_key in {"resolution_plan", "resolution_solution"}:
                    continue
                collect(child, child_key)
            return
        if isinstance(value, list):
            for child in value:
                collect(child, key)
            return
        if key in {"description", "effect", "on_hit_effect", "source_excerpt", "text"}:
            normalized = normalize(value)
            if len(normalized) >= 10:
                values.append(normalized)

    collect(source_card)
    return tuple(dict.fromkeys(values))


def persisted_standard_spell_ruling_requirement(
    source_card: dict[str, Any],
    *,
    standard_pack_ids: frozenset[str],
) -> dict[str, Any] | None:
    """Return the exact persisted Agent clause for one standard spell card."""

    if str(source_card.get("pack_id") or "") not in standard_pack_ids:
        return None
    matches = [
        dict(item)
        for item in list(source_card.get("ruling_requirements") or [])
        if isinstance(item, dict)
        and item.get("default_resolver") == "agent"
        and item.get("ruling_kind") == "generic_spell_effect"
        and len(str(item.get("source_excerpt") or "").strip()) >= 10
    ]
    return matches[0] if len(matches) == 1 else None


def validate_persisted_standard_spell_ruling(
    raw_ruling: Any,
    *,
    source_card: dict[str, Any],
    requirement: dict[str, Any],
) -> dict[str, Any]:
    """Validate Agent judgment against the immutable standard-card clause."""

    if not isinstance(raw_ruling, dict):
        raise CharacterSourceCardError("standard spell agent_ruling must be an object")
    allowed = {
        "application_id",
        "default_resolver",
        "ruling_kind",
        "decision",
        "reason",
        "source_excerpt",
    }
    application_id = str(raw_ruling.get("application_id") or "").strip()
    decision = " ".join(str(raw_ruling.get("decision") or "").split())
    reason = " ".join(str(raw_ruling.get("reason") or "").split())
    source_excerpt = " ".join(str(raw_ruling.get("source_excerpt") or "").split())
    recorded_excerpt = str(requirement.get("source_excerpt") or "")
    if (
        set(raw_ruling) - allowed
        or not application_id
        or raw_ruling.get("default_resolver") != "agent"
        or raw_ruling.get("ruling_kind") != "generic_spell_effect"
        or not 10 <= len(decision) <= 1000
        or not 10 <= len(reason) <= 500
        or source_excerpt != " ".join(recorded_excerpt.split())
    ):
        raise CharacterSourceCardError(
            "standard spell agent_ruling requires the exact persisted "
            "source-card clause, application_id, decision, and reason"
        )
    return {
        "application_id": application_id,
        "default_resolver": "agent",
        "ruling_kind": "generic_spell_effect",
        "decision": decision,
        "reason": reason,
        "source_excerpt": recorded_excerpt,
        "source_card_id": str(source_card.get("id") or ""),
        "rule_refs": [str(item) for item in source_card.get("rule_refs") or [] if str(item)],
    }


__all__ = [
    "CharacterSourceCardError",
    "character_activity_source_card",
    "character_resolution_plan",
    "character_source_card",
    "persisted_standard_spell_ruling_requirement",
    "source_card_evidence_texts",
    "validate_persisted_standard_spell_ruling",
]
