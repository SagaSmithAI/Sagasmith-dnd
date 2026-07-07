"""Import Foundry dnd5e pack YAML into SagaSmith Foundry-style documents."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from sagasmith_core.foundry_documents import FoundryDocumentService


def import_foundry_pack(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    system_id: str,
    path: str,
    actor_id: str | None = None,
) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    files = [source] if source.is_file() else sorted(source.rglob("*.yml"))
    imported = []
    for file in files:
        data = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict) or not data.get("name") or not data.get("type"):
            continue
        item = _create_item(
            documents,
            campaign_id=campaign_id,
            system_id=system_id,
            actor_id=actor_id,
            source_path=file,
            data=data,
        )
        imported.append(item)
    return {"path": str(source), "imported": imported, "count": len(imported)}


def _create_item(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    system_id: str,
    actor_id: str | None,
    source_path: Path,
    data: dict[str, Any],
) -> dict[str, Any]:
    item_type = str(data.get("type") or "loot")
    system = dict(data.get("system") or {})
    item = documents.create_item(
        campaign_id=campaign_id,
        system_id=system_id,
        actor_id=actor_id,
        item_type=item_type,
        name=str(data.get("name")),
        source_key=str(data.get("_id") or source_path.as_posix()),
        img=str(data.get("img") or ""),
        system=system,
        effects=list(data.get("effects") or []),
        flags={"foundry": {"source_path": source_path.as_posix(), "_id": data.get("_id")}},
    )
    activities = []
    for key, raw in dict(system.get("activities") or {}).items():
        if not isinstance(raw, dict):
            continue
        activity = documents.create_activity(
            item_id=item.id,
            activity_type=_activity_type(item_type, raw),
            name=str(raw.get("name") or data.get("name")),
            activation=dict(raw.get("activation") or system.get("activation") or {}),
            consumption=dict(raw.get("consumption") or {}),
            duration=_duration(system, raw),
            effects=list(raw.get("effects") or []),
            range=dict(raw.get("range") or system.get("range") or {}),
            target=dict(raw.get("target") or system.get("target") or {}),
            uses=dict(raw.get("uses") or system.get("uses") or {}),
            system={
                "foundry_id": raw.get("_id") or key,
                "level": system.get("level", 0),
                "properties": list(system.get("properties") or []),
                "concentration": "concentration" in set(system.get("properties") or []),
            },
            flags={"foundry": {"activity_key": key}},
        )
        activities.append(asdict(activity))
    return {"item": asdict(item), "activities": activities}


def _activity_type(item_type: str, raw: dict[str, Any]) -> str:
    if item_type == "spell":
        return "cast"
    return str(raw.get("type") or "utility")


def _duration(system: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    duration = dict(raw.get("duration") or system.get("duration") or {})
    properties = set(system.get("properties") or [])
    if "concentration" in properties:
        duration["concentration"] = True
    return duration
