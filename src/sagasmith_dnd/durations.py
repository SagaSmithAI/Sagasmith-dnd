"""Declared-period duration advancement for AI-DM text play."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sagasmith_core.foundry_documents import FoundryDocumentService


DECLARED_PERIODS = {
    "turn_start",
    "turn_end",
    "round_start",
    "encounter_start",
    "encounter_end",
    "short_rest",
    "long_rest",
    "scene_end",
    "declared_minute",
    "declared_hour",
    "declared_day",
}


def advance_effect_durations(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    period: str,
    actor_id: str | None = None,
) -> dict[str, Any]:
    normalized_period = _normalize(period)
    if normalized_period not in DECLARED_PERIODS:
        raise ValueError(f"unsupported duration period: {period}")
    effects = documents.list_effects(campaign_id, actor_id=actor_id)
    advanced = []
    expired = []
    for effect in effects:
        duration = dict(effect.duration or {})
        if not _matches(duration, normalized_period):
            continue
        remaining = duration.get("remaining", duration.get("value"))
        if remaining in (None, ""):
            removed = documents.delete_effect(effect.id)
            expired.append(asdict(removed))
            continue
        remaining = int(remaining) - 1
        if remaining <= 0:
            removed = documents.delete_effect(effect.id)
            expired.append(asdict(removed))
        else:
            duration["remaining"] = remaining
            updated = documents.update_effect(effect.id, duration=duration)
            advanced.append(asdict(updated))
    message = documents.create_message(
        campaign_id=campaign_id,
        message_type="time",
        speaker={"system": "runtime"},
        deltas=[
            {
                "type": "duration_advance",
                "period": normalized_period,
                "advanced": [item["id"] for item in advanced],
                "expired": [item["id"] for item in expired],
            }
        ],
        narration_hints=[f"{period} durations advance."],
    )
    return {
        "period": normalized_period,
        "advanced": advanced,
        "expired": expired,
        "messages": [asdict(message)],
    }


def _matches(duration: dict[str, Any], period: str) -> bool:
    values = {
        str(duration.get("period") or ""),
        str(duration.get("unit") or ""),
        str(duration.get("units") or ""),
    }
    normalized = {_normalize(value) for value in values if value}
    return _normalize(period) in normalized


def _normalize(value: str) -> str:
    return value.strip().replace("-", "_")
