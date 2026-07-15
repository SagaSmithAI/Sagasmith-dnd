"""Canonical use accounting for structured feature, feat, and activity cards."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sagasmith_dnd.rule_engine import ResolutionContext, apply_rule_event, core_receipts


class ActivityError(ValueError):
    """Raised when a declared activity cannot pay its structured cost."""


def consume_activity(
    sheet: dict[str, Any], *, activity_id: str, rules: ResolutionContext | None = None
) -> dict[str, Any]:
    """Consume one recorded use without inferring a narrative effect.

    A card can point at a shared ``sheet.resources`` entry through
    ``resource_key``.  Otherwise its own ``uses`` counter is authoritative.
    A card with neither is unlimited.  The caller records targets, choices,
    checks, damage, and any DM ruling separately so this helper never invents
    an outcome from prose.
    """
    before = apply_rule_event(sheet, "activity.before", rules)
    if before.status != "committed":
        return {
            "sheet": deepcopy(sheet),
            "activity_id": activity_id,
            "status": before.status,
            "rule_receipts": list(before.receipts),
            "pending": list(before.pending),
        }
    value = before.sheet
    section, activity = _find_activity(value, activity_id)
    activation = dict(activity.get("activation") or {})
    activation_type = str(activation.get("type") or "passive")
    if activation_type == "passive":
        raise ActivityError("passive content cannot be activated")
    resource_key = str(activity.get("resource_key") or "")
    payment: dict[str, Any] | None = None
    if resource_key:
        resource = dict(value.get("resources", {}).get(resource_key) or {})
        if not resource:
            raise ActivityError("activity resource_key does not exist on this character")
        if int(resource.get("value", 0) or 0) < 1:
            raise ActivityError("activity resource is exhausted")
        resource["value"] = int(resource["value"]) - 1
        value["resources"][resource_key] = resource
        payment = {"kind": "resource", "key": resource_key, "amount": 1}
    else:
        uses = dict(activity.get("uses") or {})
        if int(uses.get("max", 0) or 0) > 0:
            if int(uses.get("value", 0) or 0) < 1:
                raise ActivityError("activity uses are exhausted")
            uses["value"] = int(uses["value"]) - 1
            activity["uses"] = uses
            payment = {"kind": "card_uses", "amount": 1}
    value["content"][section] = [
        activity if item.get("id") == activity_id else item
        for item in value["content"].get(section, [])
    ]
    after = apply_rule_event(value, "activity.after", rules)
    if after.status != "committed":
        return {
            "sheet": deepcopy(sheet),
            "activity_id": activity_id,
            "content_type": section,
            "name": activity.get("name", activity_id),
            "activation": activation,
            "payment": None,
            "status": after.status,
            "rule_receipts": [*before.receipts, *after.receipts],
            "pending": list(after.pending),
        }
    return {
        "sheet": after.sheet,
        "activity_id": activity_id,
        "content_type": section,
        "name": activity.get("name", activity_id),
        "activation": activation,
        "payment": payment,
        "choices": deepcopy(activity.get("choices") or {}),
        "requires_ruling": bool(activity.get("choices")),
        "status": "committed",
        "rule_receipts": [
            *core_receipts(
                rules, ["dnd5e.core.activity.resource_accounting"], "activity.consume"
            ),
            *before.receipts,
            *after.receipts,
        ],
        "ruleset_fingerprint": rules.fingerprint if rules else "",
    }


def _find_activity(sheet: dict[str, Any], activity_id: str) -> tuple[str, dict[str, Any]]:
    for section in ("activities", "features", "feats"):
        for item in sheet.get("content", {}).get(section, []):
            if item.get("id") == activity_id:
                return section, dict(item)
    raise ActivityError("activity_id is not present on this character")
