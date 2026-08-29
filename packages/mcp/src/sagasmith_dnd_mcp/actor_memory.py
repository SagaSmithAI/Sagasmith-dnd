"""Deterministic, actor-scoped long-term memory selection.

This module deliberately contains no persistence or portrayal logic.  It turns
already-authorized actor state, ActorKnowledge, and actor-visible events into a
bounded context shared by PC and NPC callers.  In particular, it never derives
or proposes an actor intent.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

MEMORY_TRACKS = ("identity", "motivational", "semantic", "episodic")

_MOTIVATIONAL_PREDICATES = frozenset(
    {
        "bond",
        "commitment",
        "desire",
        "drive",
        "duty",
        "fear",
        "flaw",
        "goal",
        "ideal",
        "motivation",
        "objective",
        "promise",
        "relationship",
        "relationship_to",
    }
)
_TOKEN_RE = re.compile(r"[\w:-]+", re.UNICODE)


@dataclass(frozen=True)
class ActorMemoryItem:
    """One selected memory with stable evidence and ranking metadata."""

    track: str
    source: str
    basis_ref: str
    content: str
    refs: tuple[str, ...]
    record: dict[str, Any]
    score: int
    signals: dict[str, int]
    cost_chars: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "basis_ref": self.basis_ref,
            "source": self.source,
            "content": self.content,
            "refs": list(self.refs),
            "record": deepcopy(self.record),
            "score": self.score,
            "signals": dict(self.signals),
        }


@dataclass(frozen=True)
class ActorMemoryContext:
    """A bounded four-track memory view for either a PC or an NPC."""

    identity: tuple[ActorMemoryItem, ...]
    motivational: tuple[ActorMemoryItem, ...]
    semantic: tuple[ActorMemoryItem, ...]
    episodic: tuple[ActorMemoryItem, ...]
    diagnostics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            track: [item.as_dict() for item in getattr(self, track)]
            for track in MEMORY_TRACKS
        } | {"diagnostics": deepcopy(self.diagnostics)}


@dataclass(frozen=True)
class _Candidate:
    track: str
    source: str
    basis_ref: str
    content: str
    refs: tuple[str, ...]
    record: dict[str, Any]
    identity_key: str
    recency_marker: float
    confidence: int
    salience: int


def select_actor_memory_context(
    *,
    actor_state: Mapping[str, Any] | Iterable[Mapping[str, Any] | Any] | Any,
    actor_knowledge: Iterable[Mapping[str, Any] | Any],
    events: Iterable[Mapping[str, Any] | Any],
    current_refs: Iterable[str] = (),
    query: str = "",
    budget_chars: int = 8_000,
) -> ActorMemoryContext:
    """Select a deterministic, bounded memory view from authorized inputs.

    ``actor_state`` accepts either one character projection, a sequence of
    ``actor_state`` facts, or a projection containing ``state_facts``/``facts``.
    Inputs may be mappings or dataclass instances such as the existing Core info
    records.  The result is presentation-neutral and therefore cannot decide a
    PC's (or NPC's) next intent.
    """

    if isinstance(budget_chars, bool) or not isinstance(budget_chars, int):
        raise ValueError("budget_chars must be an integer")
    if budget_chars < 0:
        raise ValueError("budget_chars must not be negative")

    normalized_refs = tuple(sorted({_text(ref) for ref in current_refs if _text(ref)}))
    event_records = [_record(item, "events[]") for item in events]
    event_recency = {
        _text(item.get("id")): _recency_marker(item)
        for item in event_records
        if _text(item.get("id"))
    }

    candidates: list[_Candidate] = []
    for item in _actor_state_records(actor_state):
        candidates.append(_state_candidate(item))
    for raw in actor_knowledge:
        item = _record(raw, "actor_knowledge[]")
        candidates.append(_knowledge_candidate(item, event_recency))
    for item in event_records:
        candidates.append(_event_candidate(item))

    query_terms = tuple(sorted(set(_TOKEN_RE.findall(query.casefold()))))
    unique, duplicates_dropped = _deduplicate(candidates)
    recency_ranks = _recency_ranks(unique)
    ranked: list[tuple[tuple[Any, ...], _Candidate, dict[str, int], int]] = []
    current_ref_set = set(normalized_refs)
    for candidate in unique:
        exact_ref_matches = len(current_ref_set.intersection(candidate.refs))
        query_score = _query_score(query, query_terms, candidate)
        signals = {
            "exact_ref_matches": exact_ref_matches,
            "query": query_score,
            "recency": recency_ranks[candidate.basis_ref],
            "confidence": candidate.confidence,
            "salience": candidate.salience,
        }
        score = (
            exact_ref_matches * 1_000_000
            + query_score * 10_000
            + candidate.salience * 1_000
            + candidate.confidence * 100
            + signals["recency"]
        )
        rank = (
            -exact_ref_matches,
            -query_score,
            -candidate.salience,
            -candidate.confidence,
            -signals["recency"],
            MEMORY_TRACKS.index(candidate.track),
            candidate.basis_ref,
            _canonical(candidate.record),
        )
        ranked.append((rank, candidate, signals, score))
    ranked.sort(key=lambda item: item[0])

    selected: dict[str, list[ActorMemoryItem]] = {track: [] for track in MEMORY_TRACKS}
    used_chars = 0
    omitted_for_budget = 0
    selection_order: list[dict[str, Any]] = []
    for _, candidate, signals, score in ranked:
        cost = _item_cost(candidate, signals, score)
        if used_chars + cost > budget_chars:
            omitted_for_budget += 1
            continue
        item = ActorMemoryItem(
            track=candidate.track,
            source=candidate.source,
            basis_ref=candidate.basis_ref,
            content=candidate.content,
            refs=candidate.refs,
            record=deepcopy(candidate.record),
            score=score,
            signals=dict(signals),
            cost_chars=cost,
        )
        selected[candidate.track].append(item)
        used_chars += cost
        selection_order.append(
            {
                "basis_ref": candidate.basis_ref,
                "track": candidate.track,
                "score": score,
                "signals": dict(signals),
                "cost_chars": cost,
            }
        )

    candidate_counts = {
        track: sum(candidate.track == track for candidate in unique) for track in MEMORY_TRACKS
    }
    selected_counts = {track: len(selected[track]) for track in MEMORY_TRACKS}
    diagnostics = {
        "strategy": "exact_refs_lexical_recency_confidence_salience_v1",
        "budget_chars": budget_chars,
        "used_chars": used_chars,
        "remaining_chars": budget_chars - used_chars,
        "candidate_count": len(candidates),
        "deduplicated_count": len(unique),
        "duplicates_dropped": duplicates_dropped,
        "selected_count": sum(selected_counts.values()),
        "omitted_for_budget": omitted_for_budget,
        "current_refs": list(normalized_refs),
        "query_terms": list(query_terms),
        "track_candidates": candidate_counts,
        "track_selected": selected_counts,
        "selection_order": selection_order,
    }
    return ActorMemoryContext(
        identity=tuple(selected["identity"]),
        motivational=tuple(selected["motivational"]),
        semantic=tuple(selected["semantic"]),
        episodic=tuple(selected["episodic"]),
        diagnostics=diagnostics,
    )


def _actor_state_records(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, Mapping) or is_dataclass(value):
        item = _record(value, "actor_state")
        nested: list[dict[str, Any]] = []
        for field in ("state_facts", "facts"):
            raw = item.pop(field, None)
            if raw is None:
                continue
            if not isinstance(raw, (list, tuple)):
                raise ValueError(f"actor_state.{field} must be a list")
            nested.extend(_record(entry, f"actor_state.{field}[]") for entry in raw)
        # A Core actor_state fact is already one record; a character projection
        # becomes the stable identity record alongside any nested facts.
        return ([item] if item else []) + nested
    if isinstance(value, (str, bytes)):
        raise ValueError("actor_state must be an object or iterable of objects")
    try:
        return [_record(item, "actor_state[]") for item in value]
    except TypeError as exc:
        raise ValueError("actor_state must be an object or iterable of objects") from exc


def _record(value: Any, field: str) -> dict[str, Any]:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object or dataclass instance")
    return deepcopy(dict(value))


def _state_candidate(item: dict[str, Any]) -> _Candidate:
    predicate = _text(item.get("predicate")).casefold().replace("-", "_")
    kind = _text(item.get("kind")).casefold().replace("-", "_")
    track = (
        "motivational"
        if predicate in _MOTIVATIONAL_PREDICATES
        or kind in {"commitment", "goal", "motivation", "relationship"}
        else "identity"
    )
    content = _text(item.get("content")) or _text(item.get("summary")) or _canonical(item)
    item_id = _text(item.get("id"))
    revision_id = _text(item.get("revision_id") or item.get("revision"))
    if kind == "actor_state" or item.get("fact_key"):
        basis_ref = _revisioned_ref("fact", item_id, revision_id, item)
        identity_key = f"fact:{_text(item.get('fact_key')) or item_id or _digest(item)}"
        source = "actor_state_fact"
    else:
        basis_ref = _revisioned_ref("actor", item_id, revision_id, item)
        identity_key = f"actor:{item_id or _digest(item)}"
        source = "actor_state"
    return _Candidate(
        track=track,
        source=source,
        basis_ref=basis_ref,
        content=content,
        refs=_extract_refs(item, source),
        record=item,
        identity_key=identity_key,
        recency_marker=_recency_marker(item),
        confidence=_bounded_signal(item.get("confidence"), default=5),
        salience=_salience(item, default=5 if source == "actor_state" else 3),
    )


def _knowledge_candidate(
    item: dict[str, Any], event_recency: Mapping[str, float]
) -> _Candidate:
    item_id = _text(item.get("id"))
    revision_id = _text(item.get("revision_id"))
    source_event_id = _text(item.get("source_event_id"))
    recency = _recency_marker(item)
    if not recency and source_event_id:
        recency = event_recency.get(source_event_id, 0.0)
    return _Candidate(
        track="semantic",
        source="actor_knowledge",
        basis_ref=_revisioned_ref("knowledge", item_id, revision_id, item),
        content=_text(item.get("proposition")) or _canonical(item),
        refs=_extract_refs(item, "actor_knowledge"),
        record=item,
        identity_key=(
            f"knowledge:{_text(item.get('actor_id'))}:"
            f"{_text(item.get('knowledge_key')) or item_id or _digest(item)}"
        ),
        recency_marker=recency,
        confidence=_bounded_signal(item.get("confidence"), default=3),
        salience=_salience(item, default=3),
    )


def _event_candidate(item: dict[str, Any]) -> _Candidate:
    item_id = _text(item.get("id"))
    return _Candidate(
        track="episodic",
        source="event",
        basis_ref=f"event:{item_id}" if item_id else f"event:{_digest(item)}",
        content=(
            _text(item.get("retrieval_text"))
            or _text(item.get("summary"))
            or _canonical(item)
        ),
        refs=_extract_refs(item, "event"),
        record=item,
        identity_key=f"event:{item_id or _digest(item)}",
        recency_marker=_recency_marker(item),
        confidence=_bounded_signal(item.get("confidence"), default=3),
        salience=_salience(item, default=3),
    )


def _revisioned_ref(prefix: str, item_id: str, revision_id: str, item: dict[str, Any]) -> str:
    stable_id = item_id or _digest(item)
    return f"{prefix}:{stable_id}:{revision_id}" if revision_id else f"{prefix}:{stable_id}"


def _extract_refs(item: dict[str, Any], source: str) -> tuple[str, ...]:
    refs: set[str] = set()

    def visit(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, Mapping):
            for raw_key, nested in value.items():
                key = str(raw_key)
                child_path = path + (key,)
                if source == "actor_knowledge" and child_path == ("actor_id",):
                    continue
                if key.endswith("_ref"):
                    ref = _text(nested)
                    if ref:
                        refs.add(ref)
                elif key.endswith("_refs") and isinstance(nested, (list, tuple, set)):
                    refs.update(_text(ref) for ref in nested if _text(ref))
                elif key in {"actor_id", "speaker_actor_id"}:
                    actor_id = _text(nested)
                    if actor_id:
                        refs.add(f"actor:{actor_id}")
                elif key == "actor_ids" and isinstance(nested, (list, tuple, set)):
                    refs.update(
                        f"actor:{_text(actor_id)}"
                        for actor_id in nested
                        if _text(actor_id)
                    )
                elif key == "scene_id":
                    scene_id = _text(nested)
                    if scene_id:
                        refs.add(f"scene:{scene_id}")
                elif key in {"event_id", "source_event_id"}:
                    event_id = _text(nested)
                    if event_id:
                        refs.add(f"event:{event_id}")
                visit(nested, child_path)
        elif isinstance(value, (list, tuple)):
            for index, nested in enumerate(value):
                visit(nested, path + (str(index),))

    visit(item)
    return tuple(sorted(refs))


def _deduplicate(candidates: list[_Candidate]) -> tuple[list[_Candidate], int]:
    by_identity: dict[str, _Candidate] = {}
    for candidate in candidates:
        current = by_identity.get(candidate.identity_key)
        if current is None or _dedupe_preference(candidate) > _dedupe_preference(current):
            by_identity[candidate.identity_key] = candidate

    by_content: dict[tuple[str, str], _Candidate] = {}
    for candidate in sorted(by_identity.values(), key=lambda item: item.basis_ref):
        key = (candidate.track, " ".join(candidate.content.casefold().split()))
        current = by_content.get(key)
        if current is None or _dedupe_preference(candidate) > _dedupe_preference(current):
            by_content[key] = candidate
    result = sorted(by_content.values(), key=lambda item: item.basis_ref)
    return result, len(candidates) - len(result)


def _dedupe_preference(candidate: _Candidate) -> tuple[Any, ...]:
    return (
        candidate.recency_marker,
        candidate.salience,
        candidate.confidence,
        candidate.basis_ref,
        _canonical(candidate.record),
    )


def _recency_ranks(candidates: list[_Candidate]) -> dict[str, int]:
    markers = sorted({candidate.recency_marker for candidate in candidates})
    if not markers:
        return {}
    if len(markers) == 1:
        by_marker = {markers[0]: 0 if markers[0] == 0 else 100}
    else:
        by_marker = {
            marker: round(index * 100 / (len(markers) - 1))
            for index, marker in enumerate(markers)
        }
    return {candidate.basis_ref: by_marker[candidate.recency_marker] for candidate in candidates}


def _query_score(query: str, query_terms: tuple[str, ...], candidate: _Candidate) -> int:
    normalized_query = " ".join(query.casefold().split())
    if not normalized_query:
        return 0
    haystack = " ".join(
        (
            candidate.content.casefold(),
            candidate.basis_ref.casefold(),
            " ".join(ref.casefold() for ref in candidate.refs),
            _canonical(candidate.record).casefold(),
        )
    )
    phrase = 50 if normalized_query in haystack else 0
    term_hits = sum(term in haystack for term in query_terms)
    coverage = round(term_hits * 50 / max(1, len(query_terms)))
    return min(100, phrase + coverage)


def _recency_marker(item: Mapping[str, Any]) -> float:
    for field in ("updated_at", "created_at"):
        raw = item.get(field)
        if raw:
            try:
                return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError, OverflowError):
                pass
    for field in ("sequence", "revision", "state_version"):
        raw = item.get(field)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw)
    return 0.0


def _salience(item: Mapping[str, Any], *, default: int) -> int:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), Mapping) else {}
    payload = item.get("payload") if isinstance(item.get("payload"), Mapping) else {}
    for value in (
        item.get("salience"),
        item.get("importance"),
        metadata.get("salience"),
        metadata.get("importance"),
        payload.get("salience"),
        payload.get("importance"),
    ):
        if value is not None:
            return _bounded_signal(value, default=default)
    return default


def _bounded_signal(value: Any, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return max(0, min(5, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def _item_cost(candidate: _Candidate, signals: dict[str, int], score: int) -> int:
    rendered = {
        "basis_ref": candidate.basis_ref,
        "source": candidate.source,
        "content": candidate.content,
        "refs": list(candidate.refs),
        "record": candidate.record,
        "score": score,
        "signals": signals,
    }
    return len(_canonical(rendered))


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:16]


def _text(value: Any) -> str:
    return str(value or "").strip()
