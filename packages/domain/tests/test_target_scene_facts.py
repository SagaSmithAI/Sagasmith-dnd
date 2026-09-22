import pytest

from sagasmith_dnd.combat_engine import CombatEngineError, NeedsRulingError
from sagasmith_dnd.encounter_primitives import validate_targets


def test_visibility_requires_positive_evidence():
    args = {"source_actor_id": "a", "target_ids": ["b"], "require_visible": True}
    with pytest.raises(NeedsRulingError):
        validate_targets({}, {"b": {}}, args)
    with pytest.raises(CombatEngineError, match="visible"):
        validate_targets({}, {"b": {}}, args, spatial_facts={"b": {"visible": False}})
    result = validate_targets({}, {"b": {}}, args, spatial_facts={"b": {"visible": True}})
    assert result["targets"][0]["visible"] is True


def test_agent_range_uses_facts_without_coordinates_and_grid_uses_positions():
    args = {"source_actor_id": "a", "target_ids": ["b"], "maximum_range_ft": 15}
    with pytest.raises(NeedsRulingError):
        validate_targets({}, {"b": {}}, args, positioning_mode="agent")
    result = validate_targets({}, {"b": {}}, args, positioning_mode="agent",
                              spatial_facts={"b": {"distance_ft": 15}})
    assert result["targets"][0]["distance_ft"] == 15
    with pytest.raises(CombatEngineError, match="outside"):
        validate_targets({}, {"b": {}}, args, positioning_mode="agent",
                         spatial_facts={"b": {"distance_ft": 20}})
    with pytest.raises(CombatEngineError, match="outside"):
        validate_targets({"position": {"x": 0, "y": 0}},
                         {"b": {"position": {"x": 10, "y": 0}}}, args,
                         spatial_facts={"b": {"distance_ft": 5}})


@pytest.mark.parametrize("distance", [True, -1, "5", 2.5])
def test_agent_distance_rejects_invalid_values(distance):
    with pytest.raises(CombatEngineError):
        validate_targets({}, {"b": {}}, {"source_actor_id": "a", "target_ids": ["b"],
                                         "maximum_range_ft": 15},
                         positioning_mode="agent", spatial_facts={"b": {"distance_ft": distance}})
