from copy import deepcopy
from types import SimpleNamespace

import pytest
from sagasmith_dnd.combat_engine import CombatEngineError, NeedsRulingError
from sagasmith_dnd_runtime.services.presentation import PresentationService


def fixture():
    encounter = {"id": "encounter", "scene_id": "scene", "positioning_mode": "agent"}
    evidence = {"encounter_id": "encounter", "scene_id": "scene", "campaign_revision": 4,
                "steps": {"target": {"source_actor_id": "a", "targets": {
                    "b": {"visible": True, "distance_ft": 10},
                }}}}
    bound = SimpleNamespace(agent_ruling={"target_facts": evidence}, steps=[{
        "id": "target", "op": "target.validate", "args": {
            "source_actor_id": "a", "target_ids": ["b"], "require_visible": True,
            "maximum_range_ft": 15,
        },
    }])
    service = PresentationService()
    service.campaigns = SimpleNamespace(get=lambda _: SimpleNamespace(revision=4))
    service.require_campaign_actor = lambda *args: None
    service.require_encounter_combatant = lambda *args, **kwargs: {}
    return service, encounter, bound


@pytest.mark.parametrize("field,value", [("encounter_id", "other"), ("scene_id", "other"),
                                        ("campaign_revision", 3), ("campaign_revision", True)])
def test_scene_facts_reject_stale_or_wrong_binding(field, value):
    service, encounter, bound = fixture()
    bound.agent_ruling["target_facts"][field] = value
    with pytest.raises(CombatEngineError, match="current scene/revision"):
        service.validate_resolution_target_facts("campaign", encounter, bound, "a")


def test_paid_revision_is_only_allowed_for_settlement_and_not_after_another_write():
    service, encounter, bound = fixture()
    service.validate_resolution_target_facts("campaign", encounter, bound, "a")
    service.campaigns.get = lambda _: SimpleNamespace(revision=5)
    service.validate_resolution_target_facts("campaign", encounter, bound, "a",
                                            allow_paid_revision=True)
    service.campaigns.get = lambda _: SimpleNamespace(revision=6)
    with pytest.raises(CombatEngineError):
        service.validate_resolution_target_facts("campaign", encounter, bound, "a",
                                                allow_paid_revision=True)


@pytest.mark.parametrize("field", ["visible", "distance_ft"])
def test_missing_facts_stop_before_payment(field):
    service, encounter, bound = fixture()
    del bound.agent_ruling["target_facts"]["steps"]["target"]["targets"]["b"][field]
    with pytest.raises(NeedsRulingError, match="scene-bound"):
        service.validate_resolution_target_facts("campaign", encounter, bound, "a")


def test_facts_cannot_change_the_bound_source_or_targets():
    service, encounter, bound = fixture()
    original = deepcopy(bound.agent_ruling)
    facts = bound.agent_ruling["target_facts"]["steps"]["target"]
    facts["source_actor_id"] = "other"
    with pytest.raises(CombatEngineError, match="source and targets"):
        service.validate_resolution_target_facts("campaign", encounter, bound, "a")
    bound.agent_ruling = original
    bound.agent_ruling["target_facts"]["steps"]["target"]["targets"]["other"] = {"visible": True}
    with pytest.raises(CombatEngineError, match="outside the bound step"):
        service.validate_resolution_target_facts("campaign", encounter, bound, "a")
