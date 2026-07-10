"""Structured periodic Region resolution for scenes and measured templates."""

from __future__ import annotations

from typing import Any

from sagasmith_core import FoundryDocumentService, MapService

from sagasmith_dnd.damage import apply_actor_damage
from sagasmith_dnd.engine import roll
from sagasmith_dnd.rolls import roll_actor_d20
from sagasmith_dnd.spatial import contains_region_shape


def resolve_region_periods(
    documents: FoundryDocumentService,
    maps: MapService,
    *,
    campaign_id: str,
    period: str,
    count: int = 1,
    actor_id: str | None = None,
) -> dict[str, Any]:
    """Resolve region triggers for the declared period against current tokens."""

    results = []
    for _ in range(max(1, int(count))):
        for scene in maps.list_scenes(campaign_id):
            for region in maps.list_regions(scene.id):
                for trigger in _matching_triggers(region.metadata, period):
                    for token in maps.list_tokens(scene.id):
                        if not token.actor_id or (actor_id and token.actor_id != actor_id):
                            continue
                        if not contains_region_shape(region.shape, token.x, token.y):
                            continue
                        result = _resolve_trigger(
                            documents,
                            campaign_id=campaign_id,
                            region=region,
                            token=token,
                            trigger=trigger,
                        )
                        if result:
                            results.append(result)
    return {"period": period, "results": results}


def _matching_triggers(metadata: dict[str, Any], period: str) -> list[dict[str, Any]]:
    values = metadata.get("triggers") or []
    if isinstance(values, dict):
        values = [values]
    matches = []
    aliases = {"turn_start_inside": "turn_start", "turn_end_inside": "turn_end"}
    for value in values:
        if not isinstance(value, dict):
            continue
        event = str(value.get("event") or value.get("period") or "").strip().lower()
        if aliases.get(event, event) == period:
            matches.append(dict(value))
    return matches


def _resolve_trigger(
    documents: FoundryDocumentService,
    *,
    campaign_id: str,
    region,
    token,
    trigger: dict[str, Any],
) -> dict[str, Any] | None:
    damage_expression = str(trigger.get("damage") or "")
    save = dict(trigger.get("save") or {})
    save_result = None
    if save:
        save_result = roll_actor_d20(
            documents,
            campaign_id=campaign_id,
            actor_id=token.actor_id,
            roll_type="save",
            ability=str(save.get("ability") or "dex"),
            dc=int(save.get("dc") or 10),
            source=region.name,
        )
    if not damage_expression:
        return {
            "region_id": region.id,
            "token_id": token.id,
            "actor_id": token.actor_id,
            "save": save_result["roll"] if save_result else None,
        }
    rolled = roll(damage_expression)
    amount = rolled.total
    if save_result and save_result["roll"].get("success"):
        outcome = str(save.get("on_success") or save.get("onSave") or "half").lower()
        amount = 0 if outcome in {"none", "negate"} else amount // 2
    damage_result = apply_actor_damage(
        documents,
        campaign_id=campaign_id,
        actor_id=token.actor_id,
        amount=amount,
        damage_type=str(trigger.get("damage_type") or trigger.get("damageType") or ""),
        source=region.name,
    )
    return {
        "region_id": region.id,
        "token_id": token.id,
        "actor_id": token.actor_id,
        "save": save_result["roll"] if save_result else None,
        "damage": damage_result["damage"],
        "roll": {"expression": rolled.expression, "total": rolled.total},
    }
