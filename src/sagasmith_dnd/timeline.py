"""Declared in-world time and duration resolution for AI-run D&D sessions.

The AI DM declares elapsed fictional time.  This service owns the clock and
derives all elapsed-time period events; model latency never participates.
"""

from __future__ import annotations

import re
import uuid
from copy import deepcopy
from typing import Any

from sagasmith_core.database import Database
from sagasmith_core.models import ActiveEffect, Campaign, GameMessage, SceneRegion
from sqlalchemy import func, select

_ISO_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)
_PERIOD_ALIASES = {
    "minute": "declared_minute",
    "minutes": "declared_minute",
    "hour": "declared_hour",
    "hours": "declared_hour",
    "day": "declared_day",
    "days": "declared_day",
    "turnstart": "turn_start",
    "turnend": "turn_end",
    "roundstart": "round_start",
    "roundend": "round_end",
    "sceneend": "scene_end",
    "shortrest": "short_rest",
    "longrest": "long_rest",
}
_EVENT_PERIODS = {
    "turn_start",
    "turn_end",
    "round_start",
    "round_end",
    "encounter_start",
    "encounter_end",
    "short_rest",
    "long_rest",
    "scene_end",
}


class TimelineConflictError(RuntimeError):
    """Raised when a declaration was made from an obsolete campaign revision."""


def parse_elapsed(value: str) -> int:
    """Parse a constrained ISO-8601 duration into in-world seconds."""

    match = _ISO_DURATION.fullmatch(value.strip().upper())
    if not match:
        raise ValueError("--elapsed must use ISO-8601 form such as PT10M, PT1H, or P1D")
    parts = {name: int(match.group(name) or 0) for name in ("days", "hours", "minutes", "seconds")}
    total = (
        parts["days"] * 86_400 + parts["hours"] * 3_600 + parts["minutes"] * 60 + parts["seconds"]
    )
    if total <= 0:
        raise ValueError("--elapsed must be greater than zero")
    return total


class TimelineService:
    """Advance clock, Effect, and Region state in one database transaction."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def status(self, campaign_id: str) -> dict[str, Any]:
        with self.database.transaction() as session:
            campaign = self._campaign(session, campaign_id)
            return self._clock(dict(campaign.state or {}))

    def preview(self, *, campaign_id: str, elapsed: str) -> dict[str, Any]:
        seconds = parse_elapsed(elapsed)
        with self.database.transaction() as session:
            campaign = self._campaign(session, campaign_id)
            clock = self._clock(dict(campaign.state or {}))
            periods = self._elapsed_periods(
                clock["elapsed_seconds"], clock["elapsed_seconds"] + seconds
            )
            return {
                "clock": clock,
                "elapsed": elapsed,
                "elapsed_seconds": seconds,
                "periods": periods,
                "effect_ids": [
                    row.id
                    for row in session.scalars(
                        select(ActiveEffect).where(ActiveEffect.campaign_id == campaign_id)
                    )
                ],
                "region_ids": [
                    row.id
                    for row in session.scalars(
                        select(SceneRegion).where(SceneRegion.campaign_id == campaign_id)
                    )
                ],
            }

    def declare(
        self,
        *,
        campaign_id: str,
        elapsed: str,
        reason: str,
        intent_id: str,
        expected_revision: int | None = None,
        scene_id: str | None = None,
    ) -> dict[str, Any]:
        seconds = parse_elapsed(elapsed)
        if not intent_id.strip():
            raise ValueError("--intent-id is required")
        with self.database.transaction() as session:
            campaign = self._campaign(session, campaign_id)
            if expected_revision is not None and campaign.revision != expected_revision:
                raise TimelineConflictError(
                    f"campaign revision changed: expected {expected_revision}, found {campaign.revision}"
                )
            state = deepcopy(dict(campaign.state or {}))
            clock = self._clock(state)
            for entry in clock["entries"]:
                if entry.get("intent_id") == intent_id:
                    receipt = deepcopy(dict(entry.get("receipt") or {}))
                    receipt["idempotent"] = True
                    return receipt

            before_seconds = clock["elapsed_seconds"]
            after_seconds = before_seconds + seconds
            periods = [
                {"period": "clock", "count": 1},
                *self._elapsed_periods(before_seconds, after_seconds),
            ]
            resolution = self._resolve_periods(
                session,
                campaign_id=campaign_id,
                periods=periods,
                actor_id=None,
                before_seconds=before_seconds,
                after_seconds=after_seconds,
            )
            clock.update(
                {
                    "elapsed_seconds": after_seconds,
                    "declarations": int(clock.get("declarations", 0)) + 1,
                    "last_reason": reason,
                    "last_scene_id": scene_id or "",
                }
            )
            receipt = {
                "status": "completed",
                "idempotent": False,
                "intent_id": intent_id,
                "elapsed": elapsed,
                "elapsed_seconds": seconds,
                "reason": reason,
                "clock": self._public_clock(clock),
                "periods": resolution["periods"],
                "effects": resolution["effects"],
                "regions": resolution["regions"],
                "pending": [],
            }
            clock["entries"] = [
                *clock["entries"],
                {"intent_id": intent_id, "receipt": deepcopy(receipt)},
            ][-100:]
            state["timeline"] = clock
            campaign.state = state
            campaign.revision += 1
            message = self._message(
                session,
                campaign_id=campaign_id,
                deltas=[{"type": "time.declare", "intent_id": intent_id, **resolution}],
                narration_hints=[f"{elapsed} of declared in-world time elapsed: {reason}"],
                flags={"timeline": {"intent_id": intent_id, "elapsed_seconds": seconds}},
            )
            receipt["message_id"] = message.id
            clock["entries"][-1]["receipt"] = deepcopy(receipt)
            campaign.state = state
            return receipt

    def emit_period(
        self,
        *,
        campaign_id: str,
        period: str,
        actor_id: str | None = None,
        elapsed_seconds: int = 0,
    ) -> dict[str, Any]:
        """Emit a combat/rest/scene event through the same duration resolver."""

        normalized = self._normalize_period(period)
        if normalized not in _EVENT_PERIODS:
            raise ValueError(f"unsupported timeline event period: {period}")
        with self.database.transaction() as session:
            campaign = self._campaign(session, campaign_id)
            state = deepcopy(dict(campaign.state or {}))
            clock = self._clock(state)
            before_seconds = clock["elapsed_seconds"]
            after_seconds = before_seconds + max(0, int(elapsed_seconds))
            periods = [{"period": normalized, "count": 1}]
            periods.extend(self._elapsed_periods(before_seconds, after_seconds))
            resolution = self._resolve_periods(
                session,
                campaign_id=campaign_id,
                periods=periods,
                actor_id=actor_id,
                before_seconds=before_seconds,
                after_seconds=after_seconds,
            )
            clock["elapsed_seconds"] = after_seconds
            state["timeline"] = clock
            campaign.state = state
            campaign.revision += 1
            message = self._message(
                session,
                campaign_id=campaign_id,
                deltas=[{"type": "time.emit", "period": normalized, **resolution}],
                narration_hints=[f"Timeline event: {normalized}."],
                flags={"timeline": {"period": normalized, "actor_id": actor_id or ""}},
            )
            return {
                "period": normalized,
                "clock": self._public_clock(clock),
                "effects": resolution["effects"],
                "regions": resolution["regions"],
                "periods": resolution["periods"],
                "message_id": message.id,
            }

    @staticmethod
    def _campaign(session, campaign_id: str) -> Campaign:
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            raise LookupError(f"campaign not found: {campaign_id}")
        return campaign

    @staticmethod
    def _clock(state: dict[str, Any]) -> dict[str, Any]:
        value = deepcopy(dict(state.get("timeline") or {}))
        value.setdefault("elapsed_seconds", 0)
        value.setdefault("declarations", 0)
        value.setdefault("entries", [])
        return value

    @staticmethod
    def _public_clock(clock: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in clock.items() if key != "entries"}

    @staticmethod
    def _elapsed_periods(before_seconds: int, after_seconds: int) -> list[dict[str, int | str]]:
        result = []
        for period, unit in (
            ("declared_minute", 60),
            ("declared_hour", 3_600),
            ("declared_day", 86_400),
        ):
            count = after_seconds // unit - before_seconds // unit
            if count:
                result.append({"period": period, "count": count})
        return result

    def _resolve_periods(
        self,
        session,
        *,
        campaign_id: str,
        periods: list[dict[str, Any]],
        actor_id: str | None,
        before_seconds: int,
        after_seconds: int,
    ) -> dict[str, Any]:
        effects = list(
            session.scalars(select(ActiveEffect).where(ActiveEffect.campaign_id == campaign_id))
        )
        regions = list(
            session.scalars(select(SceneRegion).where(SceneRegion.campaign_id == campaign_id))
        )
        effect_changes = {"advanced": [], "expired": []}
        region_changes = {"advanced": [], "expired": []}
        applied_periods = []
        for item in periods:
            period = self._normalize_period(str(item["period"]))
            count = max(1, int(item.get("count", 1)))
            period_effects = self._advance_rows(
                effects,
                period=period,
                count=count,
                actor_id=actor_id,
                before_seconds=before_seconds,
                after_seconds=after_seconds,
                kind="effect",
                session=session,
            )
            period_regions = self._advance_rows(
                regions,
                period=period,
                count=count,
                actor_id=actor_id,
                before_seconds=before_seconds,
                after_seconds=after_seconds,
                kind="region",
                session=session,
            )
            for target, values in (
                (effect_changes, period_effects),
                (region_changes, period_regions),
            ):
                target["advanced"].extend(values["advanced"])
                target["expired"].extend(values["expired"])
            applied_periods.append({"period": period, "count": count})
        return {"periods": applied_periods, "effects": effect_changes, "regions": region_changes}

    def _advance_rows(
        self,
        rows: list[Any],
        *,
        period: str,
        count: int,
        actor_id: str | None,
        before_seconds: int,
        after_seconds: int,
        kind: str,
        session,
    ) -> dict[str, list[dict[str, Any]]]:
        advanced: list[dict[str, Any]] = []
        expired: list[dict[str, Any]] = []
        for row in list(rows):
            duration = dict(row.duration or {})
            if self._expires_by_clock(duration, before_seconds, after_seconds):
                expired.append({"id": row.id, "period": period, "name": row.name})
                session.delete(row)
                rows.remove(row)
                continue
            if not self._duration_matches(duration, period):
                continue
            if kind == "effect" and not self._anchor_matches(row, duration, actor_id):
                continue
            if self._expires_on_event(duration, period):
                expired.append({"id": row.id, "period": period, "name": row.name})
                session.delete(row)
                rows.remove(row)
                continue
            remaining = duration.get("remaining", duration.get("value"))
            if remaining in (None, ""):
                if period in _EVENT_PERIODS:
                    expired.append({"id": row.id, "period": period, "name": row.name})
                    session.delete(row)
                    rows.remove(row)
                continue
            updated = int(remaining) - count
            if updated <= 0:
                expired.append({"id": row.id, "period": period, "name": row.name})
                session.delete(row)
                rows.remove(row)
                continue
            duration["remaining"] = updated
            row.duration = duration
            advanced.append(
                {"id": row.id, "period": period, "remaining": updated, "name": row.name}
            )
        return {"advanced": advanced, "expired": expired}

    @classmethod
    def _duration_matches(cls, duration: dict[str, Any], period: str) -> bool:
        values = [
            str(duration.get(key) or "")
            for key in ("period", "unit", "units", "type", "expiry", "expires")
        ]
        values.extend(str(value) for value in duration.get("periods") or [] if value)
        normalized = {cls._normalize_period(value) for value in values if value}
        normalized.update(
            value.removeprefix("until_") for value in list(normalized) if value.startswith("until_")
        )
        return period in normalized

    @classmethod
    def _expires_on_event(cls, duration: dict[str, Any], period: str) -> bool:
        values = {
            cls._normalize_period(str(duration.get(key) or ""))
            for key in ("period", "expiry", "expires")
        }
        return any(
            value.startswith("until_") and value.removeprefix("until_") == period
            for value in values
        )

    @staticmethod
    def _anchor_matches(row: Any, duration: dict[str, Any], actor_id: str | None) -> bool:
        if not actor_id:
            return True
        anchor = TimelineService._normalize_period(
            str(duration.get("anchor") or duration.get("target") or "")
        )
        if anchor in {"self", "actor", "turn_actor", "turn_owner"}:
            return getattr(row, "actor_id", None) == actor_id
        return True

    @staticmethod
    def _expires_by_clock(
        duration: dict[str, Any], before_seconds: int, after_seconds: int
    ) -> bool:
        expires_at = duration.get("expires_at")
        if expires_at not in (None, ""):
            return before_seconds < int(expires_at) <= after_seconds
        return False

    @staticmethod
    def _normalize_period(value: str) -> str:
        normalized = value.strip().lower().replace("-", "_")
        return _PERIOD_ALIASES.get(
            normalized, _PERIOD_ALIASES.get(normalized.replace("_", ""), normalized)
        )

    @staticmethod
    def _message(
        session,
        *,
        campaign_id: str,
        deltas: list[dict[str, Any]],
        narration_hints: list[str],
        flags: dict[str, Any],
    ) -> GameMessage:
        sequence = (
            session.scalar(
                select(func.max(GameMessage.sequence)).where(GameMessage.campaign_id == campaign_id)
            )
            or 0
        ) + 1
        message = GameMessage(
            id=str(uuid.uuid4()),
            campaign_id=campaign_id,
            sequence=sequence,
            message_type="timeline",
            speaker={"system": "runtime"},
            deltas=deltas,
            narration_hints=narration_hints,
            flags=flags,
        )
        session.add(message)
        session.flush()
        return message
