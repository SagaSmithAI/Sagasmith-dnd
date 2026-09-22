"""Authorize source review and check casting facts before preview/time/RNG."""

from typing import Any

from sagasmith_dnd.combat_engine import NeedsRulingError
from sagasmith_dnd.spell_components import preflight_spell_components


def preflight(
    services: Any,
    *,
    campaign_id: str,
    principal_id: str,
    sheet: dict[str, Any],
    spell: dict[str, Any],
    component_ruling: dict[str, Any] | None = None,
    feature_cast_source: str | None = None,
    source_item_id: str | None = None,
) -> dict[str, Any]:
    ruling = dict(component_ruling or {})
    if any(key in ruling for key in ("source_components", "source_components_confirmed", "source")):
        if not services.is_dm(campaign_id, principal_id):
            raise NeedsRulingError(
                "missing source components require Agent-as-DM source review",
                missing=("source_components",),
            )
    item = next(
        (
            item
            for item in sheet.get("inventory", {}).get("items", [])
            if item.get("id") == source_item_id
        ),
        {},
    )
    return preflight_spell_components(
        sheet,
        spell,
        component_ruling=component_ruling,
        feature_cast_source=feature_cast_source,
        item_componentless=bool(source_item_id)
        and dict(item.get("mechanics", {}).get("spellcasting") or {}).get("components_required")
        is False,
    )
