"""Deterministic, snapshot-friendly random streams for campaign resolution."""

from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator, Protocol

ALGORITHM = "sha256-counter-v1"
_SEED_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_UINT256_RANGE = 1 << 256


class RandomSource(Protocol):
    """Small interface consumed by the D&D dice engine."""

    def randint(self, start: int, end: int) -> int:
        """Return an integer in the inclusive interval."""


def derive_random_seed(seed_material: str) -> str:
    """Turn caller-owned seed material into a fixed, storage-safe seed."""

    value = str(seed_material)
    if not value:
        raise ValueError("random seed material must not be empty")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def initial_random_stream(seed_material: str) -> dict[str, Any]:
    """Create a normalized initial campaign random-stream document."""

    return {
        "algorithm": ALGORITHM,
        "seed": derive_random_seed(seed_material),
        "position": 0,
        "last_receipt": None,
    }


def validate_random_stream_state(value: Any) -> dict[str, Any]:
    """Validate a persisted random-stream document without advancing it."""

    if not isinstance(value, dict):
        raise ValueError("campaign.state.random_stream must be an object")
    unknown = set(value) - {"algorithm", "seed", "position", "last_receipt"}
    if unknown:
        raise ValueError(
            "campaign.state.random_stream contains unsupported fields: "
            + ", ".join(sorted(unknown))
        )
    algorithm = str(value.get("algorithm") or "")
    if algorithm != ALGORITHM:
        raise ValueError(f"unsupported random stream algorithm {algorithm!r}")
    seed = str(value.get("seed") or "").casefold()
    if not _SEED_PATTERN.fullmatch(seed):
        raise ValueError("campaign.state.random_stream.seed must be a SHA-256 hex digest")
    position = value.get("position")
    if isinstance(position, bool) or not isinstance(position, int) or position < 0:
        raise ValueError("campaign.state.random_stream.position must be a non-negative integer")
    last_receipt = value.get("last_receipt")
    if last_receipt is not None:
        if not isinstance(last_receipt, dict):
            raise ValueError("campaign.state.random_stream.last_receipt must be an object or null")
        required = {
            "algorithm",
            "seed_fingerprint",
            "position_before",
            "position_after",
            "draw_count",
            "operation",
        }
        if not required.issubset(last_receipt):
            raise ValueError("campaign.state.random_stream.last_receipt is incomplete")
        before = last_receipt["position_before"]
        after = last_receipt["position_after"]
        draw_count = last_receipt["draw_count"]
        if any(
            isinstance(item, bool) or not isinstance(item, int) or item < 0
            for item in (before, after, draw_count)
        ):
            raise ValueError("campaign.state.random_stream receipt positions must be integers")
        if after < before or draw_count != after - before or after > position:
            raise ValueError("campaign.state.random_stream.last_receipt positions are inconsistent")
        if str(last_receipt["algorithm"]) != ALGORITHM:
            raise ValueError("campaign.state.random_stream.last_receipt algorithm is invalid")
        if str(last_receipt["seed_fingerprint"]) != seed[:16]:
            raise ValueError("campaign.state.random_stream.last_receipt seed does not match")
        if not str(last_receipt["operation"] or "").strip():
            raise ValueError("campaign.state.random_stream.last_receipt operation is required")
        last_receipt = {
            "algorithm": ALGORITHM,
            "seed_fingerprint": seed[:16],
            "position_before": before,
            "position_after": after,
            "draw_count": draw_count,
            "operation": str(last_receipt["operation"]),
            "idempotency_key": str(last_receipt.get("idempotency_key") or ""),
        }
    return {
        "algorithm": ALGORITHM,
        "seed": seed,
        "position": position,
        "last_receipt": last_receipt,
    }


@dataclass
class CampaignRandomStream:
    """SHA-256 counter stream whose position can be persisted with campaign state."""

    campaign_id: str
    seed: str
    position: int
    operation: str
    idempotency_key: str = ""

    def __post_init__(self) -> None:
        normalized = validate_random_stream_state(
            {
                "algorithm": ALGORITHM,
                "seed": self.seed,
                "position": self.position,
                "last_receipt": None,
            }
        )
        self.seed = normalized["seed"]
        self.position = normalized["position"]
        self.start_position = self.position
        self.persisted_position = self.position

    @classmethod
    def from_campaign_state(
        cls,
        campaign_id: str,
        state: dict[str, Any],
        *,
        operation: str,
        idempotency_key: str = "",
    ) -> CampaignRandomStream:
        raw = state.get("random_stream")
        if raw is None:
            raw = initial_random_stream(f"sagasmith-dnd:{campaign_id}")
        normalized = validate_random_stream_state(raw)
        return cls(
            campaign_id=str(campaign_id),
            seed=normalized["seed"],
            position=normalized["position"],
            operation=str(operation),
            idempotency_key=str(idempotency_key or ""),
        )

    @property
    def draw_count(self) -> int:
        return self.position - self.start_position

    @property
    def has_unpersisted_draws(self) -> bool:
        return self.position > self.persisted_position

    def randint(self, start: int, end: int) -> int:
        if isinstance(start, bool) or isinstance(end, bool):
            raise ValueError("random interval bounds must be integers")
        if end < start:
            raise ValueError("random interval end must be greater than or equal to start")
        span = end - start + 1
        rejection_limit = _UINT256_RANGE - (_UINT256_RANGE % span)
        while True:
            counter = self.position
            self.position += 1
            digest = hashlib.sha256(
                f"{ALGORITHM}\0{self.seed}\0{counter}".encode("utf-8")
            ).digest()
            sample = int.from_bytes(digest, "big")
            if sample < rejection_limit:
                return start + sample % span

    def receipt(self) -> dict[str, Any]:
        return {
            "algorithm": ALGORITHM,
            "seed_fingerprint": self.seed[:16],
            "position_before": self.start_position,
            "position_after": self.position,
            "draw_count": self.draw_count,
            "operation": self.operation,
            "idempotency_key": self.idempotency_key,
        }

    def persisted_state(self) -> dict[str, Any]:
        return {
            "algorithm": ALGORITHM,
            "seed": self.seed,
            "position": self.position,
            "last_receipt": self.receipt(),
        }

    def mark_persisted(self) -> None:
        self.persisted_position = self.position


_ACTIVE_STREAM: ContextVar[CampaignRandomStream | None] = ContextVar(
    "sagasmith_dnd_active_random_stream",
    default=None,
)


def active_random_stream() -> CampaignRandomStream | None:
    return _ACTIVE_STREAM.get()


def active_random_source() -> RandomSource | None:
    return active_random_stream()


@contextmanager
def use_random_stream(stream: CampaignRandomStream) -> Iterator[CampaignRandomStream]:
    current = active_random_stream()
    if current is not None:
        if current.campaign_id != stream.campaign_id:
            raise ValueError("nested random streams must target the same campaign")
        yield current
        return
    token = _ACTIVE_STREAM.set(stream)
    try:
        yield stream
    finally:
        _ACTIVE_STREAM.reset(token)

