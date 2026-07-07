"""ActiveEffect application for Foundry-style D&D documents."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any

from sagasmith_core.foundry_documents import FoundryDocumentService


def recalculate_actor_effects(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    actor_id: str,
) -> dict[str, Any]:
    actor = documents.get_actor(actor_id)
    if actor.campaign_id != campaign_id:
        raise ValueError(f"actor {actor_id} is not in campaign {campaign_id}")
    effective = deepcopy(actor.system)
    applied = []
    statuses: set[str] = set()
    for effect in documents.list_effects(campaign_id, actor_id=actor_id):
        if effect.disabled or effect.suppressed:
            continue
        for change in effect.changes:
            _apply_change(effective, dict(change))
        statuses.update(effect.statuses)
        applied.append(effect.id)
    derived = {
        **dict(actor.derived or {}),
        "effective_system": effective,
        "statuses": sorted(statuses),
        "applied_effects": applied,
    }
    updated = documents.update_actor(actor_id, derived=derived)
    message = documents.create_message(
        campaign_id=campaign_id,
        message_type="active_effects",
        speaker={"actor": actor_id, "alias": actor.name},
        actor_id=actor_id,
        deltas=[
            {
                "type": "actor_derived",
                "actor_id": actor_id,
                "after": derived,
            }
        ],
        narration_hints=[f"{actor.name}'s active effects are recalculated."],
    )
    return {
        "actor": asdict(updated),
        "effective_system": effective,
        "statuses": sorted(statuses),
        "applied_effects": applied,
        "messages": [asdict(message)],
    }


def _apply_change(target: dict[str, Any], change: dict[str, Any]) -> None:
    path = str(change.get("key") or "").strip()
    if not path:
        raise ValueError("effect change is missing key")
    mode = str(change.get("mode") or "ADD").upper()
    value = change.get("value")
    parent, key = _parent_for(target, path.split("."))
    current = parent.get(key)
    if mode == "ADD":
        parent[key] = _add(current, value)
    elif mode == "MULTIPLY":
        parent[key] = _multiply(current, value)
    elif mode == "OVERRIDE":
        parent[key] = value
    elif mode == "UPGRADE":
        parent[key] = max(_number(current), _number(value))
    elif mode == "DOWNGRADE":
        parent[key] = min(_number(current), _number(value))
    elif mode == "CUSTOM":
        parent[key] = _custom(current, value)
    else:
        raise ValueError(f"unsupported effect mode: {mode}")


def _parent_for(target: dict[str, Any], parts: list[str]) -> tuple[dict[str, Any], str]:
    if len(parts) == 1:
        return target, parts[0]
    value = target
    for part in parts[:-1]:
        child = value.get(part)
        if not isinstance(child, dict):
            child = {}
            value[part] = child
        value = child
    return value, parts[-1]


def _add(current: Any, value: Any) -> Any:
    if isinstance(current, list):
        return [*current, value]
    if isinstance(current, (int, float)) or isinstance(value, (int, float)):
        return _number(current) + _number(value)
    if current in (None, ""):
        return value
    if value in (None, ""):
        return current
    return f"{current} + {value}"


def _multiply(current: Any, value: Any) -> Any:
    return _number(current, default=1) * _number(value, default=1)


def _custom(current: Any, value: Any) -> Any:
    if current is None:
        return [value]
    if isinstance(current, list):
        return [*current, value]
    return [current, value]


def _number(value: Any, default: int = 0) -> int | float:
    if value in (None, ""):
        return default
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    try:
        return int(text)
    except ValueError:
        return float(text)
