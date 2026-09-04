from copy import deepcopy

import pytest
from sagasmith_dnd.combat_engine import CombatEngineError, NeedsRulingError

from sagasmith_dnd_mcp.server import (
    _normalize_sleep_spatial_facts,
    _normalize_sleep_wake_spatial_facts,
)


def _declaration():
    return {
        "spatial_facts": {
            "decision_id": "sleep-area-1",
            "reason": "Both creatures are within twenty feet of the selected point.",
            "origin_description": "The center of the empty chamber",
            "campaign_revision": 7,
            "origin_in_range": True,
            "line_of_effect_clear": True,
            "affected_target_ids": ["caster", "target"],
            "excluded_actor_ids": ["outside"],
        }
    }


def _normalize(declaration):
    return _normalize_sleep_spatial_facts(
        declaration, actor_ids={"caster", "target", "outside"}, campaign_revision=7
    )


def test_sleep_area_is_complete_coordinate_free_and_keeps_caster_as_a_candidate():
    declaration = _declaration()
    before = deepcopy(declaration)
    area = _normalize(declaration)
    assert area["radius_ft"] == 20
    assert area["origin_range_ft"] == 90
    assert area["positioning_mode"] == "agent"
    assert "origin" not in area
    assert area["targets"] == [{"target_id": "caster"}, {"target_id": "target"}]
    area["spatial_facts"]["affected_target_ids"].append("mutation")
    assert declaration == before


@pytest.mark.parametrize("declaration", [None, {}])
def test_missing_sleep_area_requires_a_ruling(declaration):
    with pytest.raises(NeedsRulingError, match="20-foot area"):
        _normalize(declaration)


@pytest.mark.parametrize(
    "field,value",
    [
        ("campaign_revision", 6),
        ("campaign_revision", 7.0),
        ("campaign_revision", True),
        ("origin_in_range", False),
        ("origin_in_range", 1),
        ("line_of_effect_clear", False),
        ("line_of_effect_clear", "true"),
        ("decision_id", ""),
        ("reason", "short"),
        ("origin_description", {"x": 0, "y": 0}),
        ("affected_target_ids", ["target"]),
        ("affected_target_ids", ["caster", "target", "target"]),
        ("affected_target_ids", ["caster", "foreign"]),
        ("affected_target_ids", ["caster", 1]),
        ("affected_target_ids", "caster"),
        ("excluded_actor_ids", ["outside", "target"]),
        ("excluded_actor_ids", []),
    ],
)
def test_sleep_rejects_stale_malformed_or_incomplete_area_decisions(field, value):
    declaration = _declaration()
    declaration["spatial_facts"][field] = value
    with pytest.raises(CombatEngineError):
        _normalize(declaration)


def test_sleep_rejects_coordinates_and_engine_owned_radius_override():
    declaration = _declaration()
    declaration["origin"] = {"x": 0, "y": 0}
    with pytest.raises(CombatEngineError, match="not coordinates"):
        _normalize(declaration)
    declaration = _declaration()
    declaration["spatial_facts"]["radius_ft"] = 100
    with pytest.raises(CombatEngineError, match="complete source-bound"):
        _normalize(declaration)


def test_sleep_can_be_cast_into_an_empty_area_without_selecting_creatures():
    declaration = _declaration()
    declaration["spatial_facts"]["affected_target_ids"] = []
    declaration["spatial_facts"]["excluded_actor_ids"] = ["caster", "target", "outside"]
    assert _normalize(declaration)["targets"] == []


def _wake_declaration():
    return {
        "spatial_facts": {
            "decision_id": "wake-1",
            "reason": "The actor can reach and physically shake the sleeping target.",
            "campaign_revision": 7,
            "can_touch_target": True,
        }
    }


def test_sleep_wake_preserves_coordinate_free_contact_decision():
    payload = _wake_declaration()
    before = deepcopy(payload)
    result = _normalize_sleep_wake_spatial_facts(payload, campaign_revision=7)
    assert result == payload["spatial_facts"]
    result["reason"] = "changed"
    assert payload == before


@pytest.mark.parametrize("payload", [None, {}])
def test_sleep_wake_requires_contact_ruling(payload):
    with pytest.raises(NeedsRulingError, match="contact-range"):
        _normalize_sleep_wake_spatial_facts(payload, campaign_revision=7)


@pytest.mark.parametrize(
    "field,value",
    [
        ("decision_id", ""),
        ("decision_id", 17),
        ("reason", "short"),
        ("reason", "x" * 1001),
        ("campaign_revision", 6),
        ("campaign_revision", 7.0),
        ("campaign_revision", True),
        ("can_touch_target", False),
        ("can_touch_target", 1),
        ("can_touch_target", "true"),
        ("position", {"x": 0, "y": 0}),
    ],
)
def test_sleep_wake_rejects_malformed_stale_or_injected_facts(field, value):
    payload = _wake_declaration()
    payload["spatial_facts"][field] = value
    with pytest.raises(CombatEngineError):
        _normalize_sleep_wake_spatial_facts(payload, campaign_revision=7)


def test_sleep_wake_rejects_coordinates_outside_facts():
    payload = _wake_declaration()
    payload["position"] = {"x": 0, "y": 0}
    with pytest.raises(CombatEngineError, match="not coordinates"):
        _normalize_sleep_wake_spatial_facts(payload, campaign_revision=7)
