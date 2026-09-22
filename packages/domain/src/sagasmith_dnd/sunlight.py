"""Source-specific Sunlight Sensitivity; scene meaning stays outside the engine."""

from __future__ import annotations

from typing import Any

SUNLIGHT_MECHANIC = "dnd5e.core.trait.sunlight_sensitivity"


def sunlight_trait(sheet: dict[str, Any]) -> dict[str, Any] | None:
    from .combat_engine import NeedsRulingError

    if sheet.get("edition") != "2014":
        return None
    matches = []
    for feature in sheet.get("content", {}).get("features", []):
        trait = dict(feature.get("choices", {}).get("source_trait") or {})
        if (
            trait.get("kind") == "sunlight_sensitivity"
            or str(feature.get("name", "")).casefold() == "sunlight sensitivity"
        ):
            if (
                SUNLIGHT_MECHANIC not in feature.get("mechanic_refs", [])
                or trait.get("automatic") is not True
                or trait.get("scope") != "self_or_subject"
                or not (
                    str(trait.get("source_excerpt") or "").strip()
                    or trait.get("source_ref") == "book:players-handbook-2014:p24"
                )
            ):
                raise NeedsRulingError(
                    "Sunlight Sensitivity needs its exact source mechanic",
                    missing=("sunlight_sensitivity.source",),
                )
            matches.append(trait)
    if len(matches) > 1:
        raise NeedsRulingError(
            "multiple Sunlight Sensitivity sources need reconciliation",
            missing=("sunlight_sensitivity.source",),
        )
    return matches[0] if matches else None


def sunlight_disadvantage(
    sheet: dict[str, Any],
    context: dict[str, Any] | None,
    *,
    actor_id: str,
    subject_id: str | None = None,
    perception: bool = False,
) -> bool | None:
    """None means no trait. Facts must already be authorized by the Runtime."""
    from .combat_engine import NeedsRulingError

    trait = sunlight_trait(sheet)
    if trait is None:
        return None
    facts = dict((context or {}).get("facts") or {})
    subject = dict(facts.get("subject") or {})
    if (
        facts.get("actor_id") != actor_id
        or not subject.get("id")
        or (subject_id is not None and subject["id"] != subject_id)
    ):
        raise NeedsRulingError(
            "Sunlight Sensitivity requires a current source-bound observer and subject ruling",
            missing=("sunlight.subject", "sunlight.source_ruling"),
        )
    if perception:
        if type(facts.get("relies_on_sight")) is not bool:
            raise NeedsRulingError(
                "Perception must identify whether this check relies on sight",
                missing=("sunlight.relies_on_sight",),
            )
        if not facts["relies_on_sight"]:
            return False
    required = ["actor_in_direct_sunlight", "subject_in_direct_sunlight"]
    missing = [f"sunlight.{key}" for key in required if type(facts.get(key)) is not bool]
    if missing:
        raise NeedsRulingError(
            "Sunlight Sensitivity requires explicit direct-sunlight facts",
            missing=missing,
        )
    return any(facts[key] for key in required)
