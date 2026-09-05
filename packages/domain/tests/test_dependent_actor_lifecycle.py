from copy import deepcopy

import pytest
from test_dependent_actor_relations import relation

from sagasmith_dnd.dependent_actor_lifecycle import (
    dependent_actor_lifecycle_policy,
    validate_steel_defender_lifecycle_policy,
)
from sagasmith_dnd.dependent_actor_relations import validate_dependent_actor_relations


@pytest.mark.parametrize("owner_death", ["independent", "perish"])
def test_explicit_lifecycle_variants_are_deep_normalized(owner_death):
    policy = {"schema_version": 1, "owner_death": owner_death}
    requirement = {"owner_binding": {"relation_key": "steel_defender"}, "lifecycle_policy": policy}
    result = dependent_actor_lifecycle_policy(requirement)
    assert result == policy and result is not policy


@pytest.mark.parametrize("bad", [
    None, {}, True, "perish", {"schema_version": 1},
    {"schema_version": True, "owner_death": "perish"},
    {"schema_version": 1.0, "owner_death": "perish"},
    {"schema_version": 2, "owner_death": "perish"},
    {"schema_version": 1, "owner_death": True},
    {"schema_version": 1, "owner_death": []},
    {"schema_version": 1, "owner_death": "Perish"},
    {"schema_version": 1, "owner_death": "default"},
    {"schema_version": 1, "owner_death": "perish", "extra": True},
])
def test_ambiguous_or_malformed_lifecycle_policy_is_rejected(bad):
    with pytest.raises(ValueError, match="lifecycle_policy"):
        validate_steel_defender_lifecycle_policy(bad)


def test_no_implicit_lifecycle_authority_from_numeric_template_or_name():
    assert dependent_actor_lifecycle_policy({"name": "Steel Defender"}) is None
    with pytest.raises(ValueError, match="lifecycle_policy"):
        dependent_actor_lifecycle_policy({"owner_binding": {"relation_key": "steel_defender"}})
    with pytest.raises(ValueError, match="only for a bound Steel Defender"):
        dependent_actor_lifecycle_policy({
            "owner_binding": {"relation_key": "familiar"},
            "lifecycle_policy": {"schema_version": 1, "owner_death": "perish"},
        })


def test_relation_requires_matching_lifecycle_authorization():
    original = relation()
    binding = original["template_binding"]
    changed = deepcopy(original)
    changed["template_binding"]["lifecycle_policy"]["owner_death"] = "perish"
    with pytest.raises(ValueError, match="lifecycle_policy does not match"):
        validate_dependent_actor_relations([changed])
    for where in (binding, binding["authorization"]):
        missing = deepcopy(original)
        target = missing["template_binding"]
        if where is binding["authorization"]:
            target = target["authorization"]
        del target["lifecycle_policy"]
        with pytest.raises(ValueError, match="exactly"):
            validate_dependent_actor_relations([missing])
