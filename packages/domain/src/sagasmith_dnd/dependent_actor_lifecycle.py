"""Explicit reviewed lifecycle policy, separate from numeric statblock formulas."""

from __future__ import annotations

from typing import Any, Mapping


def validate_steel_defender_lifecycle_policy(value: Any) -> dict[str, Any]:
    """Never infer an owner-death rule from the creature name or formula hash."""
    if not isinstance(value, Mapping) or set(value) != {"schema_version", "owner_death"}:
        raise ValueError(
            "Steel Defender lifecycle_policy requires exactly schema_version/owner_death"
        )
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("Steel Defender lifecycle_policy schema_version must be 1")
    if not isinstance(value["owner_death"], str) or value["owner_death"] not in {
        "independent", "perish",
    }:
        raise ValueError(
            "Steel Defender lifecycle_policy owner_death must be independent or perish"
        )
    return {"schema_version": 1, "owner_death": value["owner_death"]}


def dependent_actor_lifecycle_policy(requirement: Mapping[str, Any]) -> dict[str, Any] | None:
    """Require source review before executing a bound Steel Defender lifecycle.

    Formula-only templates and archived source inputs may lack this contract;
    they do not thereby authorize either variant of the Steel Defender rule.
    """
    binding = requirement.get("owner_binding")
    relation_key = binding.get("relation_key") if isinstance(binding, Mapping) else None
    if relation_key != "steel_defender":
        if "lifecycle_policy" in requirement:
            raise ValueError("lifecycle_policy is supported only for a bound Steel Defender")
        return None
    return validate_steel_defender_lifecycle_policy(requirement.get("lifecycle_policy"))
