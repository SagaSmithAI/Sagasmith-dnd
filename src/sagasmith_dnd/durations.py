"""Declared-period duration advancement for AI-DM text play."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sagasmith_core.foundry_documents import FoundryDocumentService


DECLARED_PERIODS = {
    "turn_start",
    "turn_end",
    "round_start",
    "round_end",
    "encounter_start",
    "encounter_end",
    "short_rest",
    "long_rest",
    "scene_end",
    "declared_minute",
    "declared_hour",
    "declared_day",
}

PERIOD_ALIASES = {
    "minute": "declared_minute",
    "minutes": "declared_minute",
    "hour": "declared_hour",
    "hours": "declared_hour",
    "day": "declared_day",
    "days": "declared_day",
    "shortrest": "short_rest",
    "longrest": "long_rest",
    "turnstart": "turn_start",
    "turnend": "turn_end",
    "roundstart": "round_start",
    "roundend": "round_end",
    "sceneend": "scene_end",
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
        if not _anchor_matches(effect, duration, actor_id):
            continue
        remaining = duration.get("remaining", duration.get("value"))
        if _expires_on_period(duration, normalized_period) or remaining in (None, ""):
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
    values = [
        str(duration.get("period") or ""),
        str(duration.get("unit") or ""),
        str(duration.get("units") or ""),
        str(duration.get("type") or ""),
        str(duration.get("expiry") or ""),
        str(duration.get("expires") or ""),
    ]
    values.extend(str(item) for item in duration.get("periods") or [] if item)
    normalized = {_normalize(value) for value in values if value}
    normalized.update([_until_targets(value) for value in normalized if value.startswith("until_")])
    return _normalize(period) in normalized


def _anchor_matches(effect: Any, duration: dict[str, Any], actor_id: str | None) -> bool:
    if not actor_id:
        return True
    anchor = _normalize(str(duration.get("anchor") or duration.get("target") or ""))
    if anchor in {"self", "actor", "turn_actor", "turn_owner"}:
        return getattr(effect, "actor_id", None) == actor_id
    return True


def _expires_on_period(duration: dict[str, Any], period: str) -> bool:
    values = {
        _normalize(str(duration.get("period") or "")),
        _normalize(str(duration.get("expiry") or "")),
        _normalize(str(duration.get("expires") or "")),
    }
    return any(value.startswith("until_") and _until_targets(value) == period for value in values)


def _until_targets(value: str) -> str:
    return value.removeprefix("until_")


def _normalize(value: str) -> str:
    normalized = value.strip().replace("-", "_")
    squashed = normalized.replace("_", "")
    return PERIOD_ALIASES.get(normalized, PERIOD_ALIASES.get(squashed, normalized))
