"""Shared ownership boundary for generic campaign metadata patches."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

SYSTEM_OWNED_CAMPAIGN_STATE_FIELDS = frozenset(
    {
        "advancement",
        "adventure_started",
        "adventure_started_actor_ids",
        "chase",
        "combat",
        "consumable_uses",
        "currency_spends",
        "dependent_actor_relations",
        "game_phase",
        "game_time",
        "ground_items",
        "item_spends",
        "loot_acquisitions",
        "playthrough_manifest",
        "random_stream",
        "resolution_log",
        "scene_objects",
        "scene_runtime",
        "world_effects",
        "world_time",
    }
)
SYSTEM_OWNED_CAMPAIGN_SETTING_FIELDS = frozenset({"advancement", "edition", "locale"})


def merge_reviewed_campaign_settings(
    current: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    protected = sorted(set(patch) & SYSTEM_OWNED_CAMPAIGN_SETTING_FIELDS)
    if protected:
        raise ValueError(
            "campaign update cannot write system-owned settings fields: " + ", ".join(protected)
        )
    return {**deepcopy(dict(current)), **deepcopy(dict(patch))}


def merge_reviewed_campaign_state(
    current: dict[str, Any],
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Merge narrative metadata without opening a second runtime-state writer."""

    protected = sorted(set(patch) & SYSTEM_OWNED_CAMPAIGN_STATE_FIELDS)
    if protected:
        raise ValueError(
            "campaign update cannot write system-owned state fields: " + ", ".join(protected)
        )
    normalized_patch = deepcopy(dict(patch))
    party_patch = normalized_patch.pop("party", None)
    result = {**deepcopy(dict(current)), **normalized_patch}
    if party_patch is not None:
        if not isinstance(party_patch, dict):
            raise ValueError("campaign.state.party patch must be an object")
        protected_party = sorted(set(party_patch) - {"notes"})
        if protected_party:
            raise ValueError(
                "campaign update cannot write system-owned party fields: "
                + ", ".join(protected_party)
            )
        result["party"] = {
            **deepcopy(dict(current.get("party") or {})),
            **deepcopy(party_patch),
        }
    return result
