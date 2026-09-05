"""Validation for source-bound saving-throw context in semantic plans."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_SAVE_SOURCE_KINDS = frozenset({"spell", "magical_effect", "nonmagical_effect"})
_SAVE_CONDITIONS = frozenset(
    {
        "blinded",
        "charmed",
        "deafened",
        "frightened",
        "grappled",
        "incapacitated",
        "invisible",
        "paralyzed",
        "petrified",
        "poisoned",
        "prone",
        "restrained",
        "stunned",
        "unconscious",
    }
)
_EXPECTED_KEYS = frozenset(
    {
        "source",
        "source_ref",
        "source_excerpt",
        "save_source_kind",
        "save_effect_conditions",
        "save_against_poison",
    }
)


def _contains_reference(value: Any) -> bool:
    if isinstance(value, dict):
        return any(
            (isinstance(key, str) and key.startswith("$")) or _contains_reference(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_reference(item) for item in value)
    return False


def validated_save_source_facts(
    value: Any,
    *,
    citations: tuple[dict[str, Any], ...] | list[dict[str, Any]],
    source_card_kind: str,
) -> dict[str, Any]:
    """Validate typed save facts and bind them to one exact plan citation.

    Legacy plans may omit ``source`` or keep it as text; those plans remain
    executable but provide no typed facts.
    """

    if value is None or isinstance(value, str):
        return {}
    if not isinstance(value, dict):
        raise ValueError("check.save source must be an object, string, or null")
    if set(value) != _EXPECTED_KEYS:
        raise ValueError("check.save source has unsupported or missing fields")
    if _contains_reference(value):
        raise ValueError("check.save source cannot contain slot or result references")

    source = value["source"]
    source_ref = value["source_ref"]
    source_excerpt = (
        " ".join(str(value["source_excerpt"]).split())
        if isinstance(value["source_excerpt"], str)
        else ""
    )
    source_kind = value["save_source_kind"]
    conditions = value["save_effect_conditions"]
    poison = value["save_against_poison"]
    if not isinstance(source, str) or not source.strip():
        raise ValueError("check.save source must be non-empty text")
    if not isinstance(source_ref, dict) or not source_ref:
        raise ValueError("check.save source_ref must be a non-empty object")
    if not 10 <= len(source_excerpt) <= 4000:
        raise ValueError("check.save source_excerpt must contain 10 to 4000 characters")
    if not isinstance(source_kind, str) or source_kind.strip().casefold() not in _SAVE_SOURCE_KINDS:
        raise ValueError("check.save save_source_kind is unsupported")
    if not isinstance(conditions, list) or any(
        not isinstance(item, str) or not item.strip() for item in conditions
    ):
        raise ValueError("check.save save_effect_conditions must be a list of strings")
    normalized_conditions = [item.strip().casefold() for item in conditions]
    if any(item not in _SAVE_CONDITIONS for item in normalized_conditions):
        raise ValueError("check.save save_effect_conditions contains an unsupported condition")
    if len(normalized_conditions) != len(set(normalized_conditions)):
        raise ValueError("check.save save_effect_conditions cannot contain duplicates")
    if type(poison) is not bool:
        raise ValueError("check.save save_against_poison must be a boolean")
    if source_card_kind == "spell" and source_kind.strip().casefold() != "spell":
        raise ValueError("spell source cards require save_source_kind=spell")

    normalized_source = source.strip()
    matching = any(
        normalized_source == str(citation.get("source") or "").strip()
        and source_ref == citation.get("source_ref")
        and source_excerpt == " ".join(str(citation.get("source_excerpt") or "").split())
        for citation in citations
    )
    if not matching:
        raise ValueError("check.save source facts must match one exact plan citation")
    return {
        "save_source_kind": source_kind.strip().casefold(),
        "save_effect_conditions": deepcopy(normalized_conditions),
        "save_against_poison": poison,
    }


__all__ = ["validated_save_source_facts"]
