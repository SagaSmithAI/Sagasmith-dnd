"""Canonical use accounting for structured feature, feat, and activity cards."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any

from sagasmith_dnd.engine import roll
from sagasmith_dnd.resources import mutate_bounded_resource
from sagasmith_dnd.rule_engine import ResolutionContext, apply_rule_event, core_receipts

ACTIVITY_CONTENT_SECTIONS = ("activities", "features", "feats")


class ActivityError(ValueError):
    """Raised when a declared activity cannot pay its structured cost."""


def recharge_activities_at_turn_start(
    sheet: dict[str, Any],
    *,
    rules: ResolutionContext | None = None,
    rng: Any = None,
) -> dict[str, Any]:
    """Roll each unavailable source-authored Recharge activity once.

    A monster's ``Recharge X-Y`` marker is a standard turn-start resource
    rule, not an Agent/DM decision. Only cards carrying the strict importer
    contract participate, and an available activity never consumes a random
    draw.
    """

    value = deepcopy(sheet)
    results: list[dict[str, Any]] = []
    activities = list(dict(value.get("content") or {}).get("activities", []))
    inventory_items = list(dict(value.get("inventory") or {}).get("items", []))
    recharge_cards = [
        (
            "activity",
            activity,
            dict(dict(activity.get("choices") or {}).get("recharge") or {}),
        )
        for activity in activities
    ]
    recharge_cards.extend(
        (
            "item",
            item,
            dict(dict(item.get("mechanics") or {}).get("recharge") or {}),
        )
        for item in inventory_items
        if str(item.get("kind") or "") == "weapon"
    )
    for source_card_kind, activity, recharge in recharge_cards:
        if recharge.get("kind") != "d6_turn_start":
            continue
        uses = dict(activity.get("uses") or {})
        if (
            int(uses.get("max", 0) or 0) != 1
            or int(uses.get("value", 0) or 0) not in {0, 1}
            or bool(uses.get("unlimited", False))
        ):
            raise ActivityError("Recharge activity must use one bounded card use")
        if int(uses["value"]) == 1:
            continue
        minimum = recharge.get("minimum")
        maximum = recharge.get("maximum")
        if (
            isinstance(minimum, bool)
            or not isinstance(minimum, int)
            or isinstance(maximum, bool)
            or not isinstance(maximum, int)
            or not 1 <= minimum <= maximum <= 6
        ):
            raise ActivityError("Recharge activity has an invalid d6 success range")
        recharge_roll = asdict(roll("1d6", rng=rng))
        recovered = minimum <= int(recharge_roll["total"]) <= maximum
        if recovered:
            uses["value"] = 1
            activity["uses"] = uses
        results.append(
            {
                "activity_id": str(activity.get("id") or ""),
                "source_card_kind": source_card_kind,
                "name": str(activity.get("name") or ""),
                "roll": recharge_roll,
                "minimum": minimum,
                "maximum": maximum,
                "recharged": recovered,
            }
        )
    value.setdefault("content", {})["activities"] = activities
    value.setdefault("inventory", {})["items"] = inventory_items
    return {
        "sheet": value,
        "results": results,
        "rule_receipts": (
            core_receipts(
                rules,
                ["dnd5e.core.activity.recharge"],
                "activity.recharge.turn_start",
            )
            if results
            else []
        ),
        "ruleset_fingerprint": rules.fingerprint if rules else "",
    }


def consume_activity(
    sheet: dict[str, Any], *, activity_id: str, rules: ResolutionContext | None = None
) -> dict[str, Any]:
    """Consume one recorded use without inferring a narrative effect.

    A card can point at a shared ``sheet.resources`` entry through
    ``resource_key``.  Otherwise its own ``uses`` counter is authoritative.
    A card with neither is unlimited.  The caller records targets, choices,
    checks, damage, and any Agent-as-DM ruling separately so this helper never
    invents an outcome from prose.
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
        if not bool(resource.get("unlimited", False)):
            try:
                mutate_bounded_resource(resource, amount=1, direction="spend")
            except ValueError as error:
                raise ActivityError("activity resource is exhausted") from error
            value["resources"][resource_key] = resource
            payment = {"kind": "resource", "key": resource_key, "amount": 1}
    else:
        uses = dict(activity.get("uses") or {})
        # Zero capacity is not the same thing as unlimited capacity.  Only a
        # source-authored explicit flag may make a structured counter free.
        if uses and not bool(uses.get("unlimited", False)):
            try:
                mutate_bounded_resource(uses, amount=1, direction="spend")
            except ValueError as error:
                raise ActivityError("activity uses are exhausted") from error
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
    choices = deepcopy(activity.get("choices") or {})
    manual_ruling = dict(choices.get("manual_ruling") or {})
    requires_ruling = bool(choices)
    ruling_requirement = (
        {
            "default_resolver": "agent",
            "ruling_kind": str(manual_ruling.get("kind") or "agent_dm_adjudication"),
            "source_excerpt": str(manual_ruling.get("source_excerpt") or ""),
        }
        if requires_ruling
        else None
    )
    return {
        "sheet": after.sheet,
        "activity_id": activity_id,
        "content_type": section,
        "name": activity.get("name", activity_id),
        "activation": activation,
        "payment": payment,
        "choices": choices,
        "requires_ruling": requires_ruling,
        "ruling_requirement": ruling_requirement,
        "status": "committed",
        "rule_receipts": [
            *core_receipts(rules, ["dnd5e.core.activity.resource_accounting"], "activity.consume"),
            *before.receipts,
            *after.receipts,
        ],
        "ruleset_fingerprint": rules.fingerprint if rules else "",
    }


def _find_activity(sheet: dict[str, Any], activity_id: str) -> tuple[str, dict[str, Any]]:
    for section in ACTIVITY_CONTENT_SECTIONS:
        for item in sheet.get("content", {}).get(section, []):
            if item.get("id") == activity_id:
                return section, dict(item)
    raise ActivityError("activity_id is not present on this character")
