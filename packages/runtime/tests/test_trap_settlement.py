from types import SimpleNamespace

import pytest
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.combat_engine import CombatEngineError
from sagasmith_dnd.traps import source_trap_profile
from sagasmith_dnd_runtime.services.spells import SpellsService
from sagasmith_dnd_runtime.services.traps import (
    _apply_falling_net_effects,
    _has_thieves_tools_proficiency,
    _require_rolling_sphere_trigger_settlement,
    _resolve_poison_needle_spatial_facts,
    _resolve_source_trap_area_targets,
    _revealed_detection_facts,
)


def test_falling_net_effects_restrain_each_target_and_prone_only_failed_saves():
    failed_sheet = default_character_sheet()
    succeeded_sheet = default_character_sheet()

    failed_result, failed_added = _apply_falling_net_effects(
        failed_sheet, strength_save_succeeded=False
    )
    succeeded_result, succeeded_added = _apply_falling_net_effects(
        succeeded_sheet, strength_save_succeeded=True
    )

    assert "restrained" in failed_result["conditions"]
    assert "prone" in failed_result["conditions"]
    assert failed_added is True
    assert "restrained" in succeeded_result["conditions"]
    assert "prone" not in succeeded_result["conditions"]
    assert succeeded_added is True
    assert "restrained" not in failed_sheet["conditions"]
    assert "restrained" not in succeeded_sheet["conditions"]


def test_falling_net_effects_preserve_preexisting_restraint_ownership():
    sheet = default_character_sheet()
    sheet["conditions"] = ["restrained"]

    updated, restrained_added = _apply_falling_net_effects(sheet, strength_save_succeeded=False)

    assert "restrained" in updated["conditions"]
    assert "prone" in updated["conditions"]
    assert restrained_added is False
    assert sheet["conditions"] == ["restrained"]


def test_locking_pit_disable_uses_normalized_thieves_tools_proficiency():
    sheet = default_character_sheet()

    assert _has_thieves_tools_proficiency(sheet) is False

    sheet["traits"]["proficiencies"]["tools"].append("Thieves' Tools")
    assert _has_thieves_tools_proficiency(sheet) is True
    sheet["traits"]["proficiencies"]["tools"] = ["THIEVES_TOOLS"]
    assert _has_thieves_tools_proficiency(sheet) is True


def test_poison_needle_runtime_requires_current_dm_bound_three_inch_spatial_facts():
    profile = source_trap_profile(
        {"profile_id": "srd5.1.poison_needle"},
        'trap_profile: {"profile_id":"srd5.1.poison_needle"}',
    )
    facts = {
        "decision_id": "needle-range-1",
        "reason": "DM reviewed the lock and target position.",
        "scene_id": "scene-1",
        "scene_revision": 4,
        "trap_id": "needle-1",
        "target_actor_id": "actor-1",
        "source_ref": "module:chest#needle",
        "campaign_revision": 9,
        "reviewed_by": "principal:dm",
        "distance_inches": 2.5,
    }
    binding = {
        "scene_id": "scene-1",
        "scene_revision": 4,
        "trap_id": "needle-1",
        "target_actor_id": "actor-1",
        "source_ref": "module:chest#needle",
        "campaign_revision": 9,
        "reviewed_by": "principal:dm",
    }

    normalized = _resolve_poison_needle_spatial_facts(
        profile, facts, **binding
    )
    assert normalized["distance_inches"] == 2.5
    assert normalized["reviewed_by"] == "principal:dm"

    with pytest.raises(CombatEngineError, match="3-inch range"):
        _resolve_poison_needle_spatial_facts(
            profile, {**facts, "distance_inches": 3.1}, **binding
        )
    with pytest.raises(CombatEngineError, match="stale for the current campaign revision"):
        _resolve_poison_needle_spatial_facts(
            profile, {**facts, "campaign_revision": 8}, **binding
        )

@pytest.mark.parametrize(
    ("profile_id", "success", "expected"),
    [
        (
            "srd5.1.fire_breathing_statue",
            True,
            ["hidden_pressure_plate", "faint_scorch_marks_on_floor_and_walls"],
        ),
        (
            "srd5.1.fire_breathing_statue",
            False,
            [],
        ),
        (
            "srd5.1.sphere_of_annihilation",
            True,
            [
                "sphere_of_annihilation_in_stone_mouth",
                "sphere_cannot_be_controlled_or_moved",
            ],
        ),
    ],
)
def test_detection_clues_are_returned_only_after_source_defined_check_success(
    profile_id, success, expected
):
    profile = source_trap_profile(
        {"profile_id": profile_id},
        f'trap_profile: {{"profile_id":"{profile_id}"}}',
    )

    assert _revealed_detection_facts(profile, [{"success": success}]) == expected


def test_rolling_sphere_missing_spatial_contract_fails_closed():
    profile = source_trap_profile(
        {"profile_id": "srd5.1.rolling_sphere"},
        'trap_profile: {"profile_id":"srd5.1.rolling_sphere"}',
    )
    fact = {
        "kind": "pressure_plate_weight",
        "scene_id": "scene-1",
        "plate_id": "sphere-plate-1",
        "weight_lb": 20,
    }

    with pytest.raises(CombatEngineError, match="no trap initiative participant") as error:
        _require_rolling_sphere_trigger_settlement(
            profile,
            fact,
            scene_id="scene-1",
            trap_id="sphere-plate-1",
        )
    assert "no state was written" in str(error.value)

    with pytest.raises(CombatEngineError, match="20 lb or greater"):
        _require_rolling_sphere_trigger_settlement(
            profile,
            {**fact, "weight_lb": 19},
            scene_id="scene-1",
            trap_id="sphere-plate-1",
        )


def test_trap_object_spell_runtime_uses_authenticated_review_and_exact_scene_revision():
    profile = {"profile_id": "srd5.1.fire_breathing_statue"}
    excerpt = (
        "Fire statue source.\n"
        "trap_profile: {\"profile_id\":\"srd5.1.fire_breathing_statue\"}"
    )
    exact_ref = '{"chunk_id":"chunk-1","module_id":"module-1","scene_id":"scene-1"}'

    class Access:
        def require_campaign(self, campaign_id, principal_id, *, roles):
            assert campaign_id == "campaign-1"
            assert roles
            if principal_id != "principal:dm":
                raise PermissionError("campaign DM role required")

    class Modules:
        def current_scene(self, campaign_id, *, scope_id):
            assert campaign_id == "campaign-1"
            assert scope_id == "party"
            return {"scene_id": "scene-1", "state_version": 4}

    service = object.__new__(SpellsService)
    service.access = Access()
    service.modules = Modules()
    service.managed_module_source_ref = lambda campaign_id, value, **kwargs: (
        exact_ref,
        {"scene_id": "scene-1"},
        {"scene": {"id": "scene-1"}, "content": excerpt},
    )
    service.managed_module_source_excerpt = lambda expanded, value, **kwargs: value
    campaign = SimpleNamespace(
        id="campaign-1",
        revision=9,
        state={"trap_state": {"traps": {}}},
    )
    declaration = {
        "trap_object_facts": {
            "decision_id": "review-1",
            "reason": "The caster can see this statue from the reviewed position.",
            "source_ref": exact_ref,
            "source_excerpt": excerpt,
            "scene_id": "scene-1",
            "scene_revision": 4,
            "trap_id": "statue-1",
            "profile_id": profile["profile_id"],
            "component": "statue",
            "actor_id": "caster-1",
            "campaign_revision": 9,
            "distance_ft": 30,
            "visible": True,
            "targetable": True,
        }
    }
    normalized = service.validate_source_trap_object_spell_facts(
        campaign,
        actor_id="caster-1",
        spell_kind="detect_magic",
        declaration=declaration,
        principal_id="principal:dm",
        cast_level=1,
    )
    assert normalized["facts"]["reviewed_by"] == "principal:dm"
    assert normalized["facts"]["source_ref"] == exact_ref
    assert normalized["facts"]["distance_ft"] == 30.0
    assert normalized["profile"]["magic_detection"]["reveals_school"] == "evocation"

    with pytest.raises(PermissionError, match="DM role"):
        service.validate_source_trap_object_spell_facts(
            campaign,
            actor_id="caster-1",
            spell_kind="detect_magic",
            declaration=declaration,
            principal_id="principal:player",
            cast_level=1,
        )

    stale_scene = {
        **declaration,
        "trap_object_facts": {**declaration["trap_object_facts"], "scene_revision": 3},
    }
    with pytest.raises(CombatEngineError, match="active party scene"):
        service.validate_source_trap_object_spell_facts(
            campaign,
            actor_id="caster-1",
            spell_kind="detect_magic",
            declaration=stale_scene,
            principal_id="principal:dm",
            cast_level=1,
        )

    outside_range = {
        **declaration,
        "trap_object_facts": {**declaration["trap_object_facts"], "distance_ft": 31},
    }
    with pytest.raises(CombatEngineError, match="outside the 30-foot"):
        service.validate_source_trap_object_spell_facts(
            campaign,
            actor_id="caster-1",
            spell_kind="detect_magic",
            declaration=outside_range,
            principal_id="principal:dm",
            cast_level=1,
        )


def test_area_target_runtime_derives_agent_targets_from_complete_bound_actor_facts():
    marker = 'trap_profile: {"profile_id":"srd5.1.fire_breathing_statue"}'
    profile = source_trap_profile(
        {"profile_id": "srd5.1.fire_breathing_statue"}, marker
    )
    encounter = {
        "active": True,
        "id": "encounter-1",
        "positioning_mode": "agent",
        "combatants": [
            {"actor_id": "actor-1", "conditions": []},
            {"actor_id": "actor-2", "conditions": []},
            {"actor_id": "dead-actor", "conditions": ["dead"]},
        ],
    }
    facts = {
        "decision_id": "area-review-1",
        "reason": "Reviewed all living combatants against the source cone.",
        "scene_id": "scene-1",
        "trap_id": "statue-1",
        "encounter_id": "encounter-1",
        "source_ref": "exact-source-ref",
        "campaign_revision": 8,
        "reviewed_by": "system:local",
        "actor_facts": [
            {"actor_id": "actor-1", "in_area": True},
            {"actor_id": "actor-2", "in_area": False},
            {"actor_id": "dead-actor", "in_area": True},
        ],
    }
    affected, normalized = _resolve_source_trap_area_targets(
        profile,
        facts,
        encounter=encounter,
        scene_id="scene-1",
        trap_id="statue-1",
        reviewed_by="system:local",
        source_ref="exact-source-ref",
        campaign_revision=8,
    )
    assert affected == ["actor-1"]
    assert normalized["affected_actor_ids"] == affected
    assert "dead-actor" not in affected
    with pytest.raises(CombatEngineError, match="campaign revision"):
        _resolve_source_trap_area_targets(
            profile,
            facts,
            encounter=encounter,
            scene_id="scene-1",
            trap_id="statue-1",
            reviewed_by="system:local",
            source_ref="exact-source-ref",
            campaign_revision=9,
        )


def test_area_target_runtime_fails_closed_without_trap_positioning_contract():
    marker = 'trap_profile: {"profile_id":"srd5.1.fire_breathing_statue"}'
    profile = source_trap_profile(
        {"profile_id": "srd5.1.fire_breathing_statue"}, marker
    )
    encounter = {
        "active": True,
        "positioning_mode": "grid",
        "combatants": [{"actor_id": "actor-1", "conditions": []}],
    }
    with pytest.raises(CombatEngineError, match="authoritative position and facing"):
        _resolve_source_trap_area_targets(
            profile,
            None,
            encounter=encounter,
            scene_id="scene-1",
            trap_id="statue-1",
            reviewed_by="system:local",
            source_ref="exact-source-ref",
            campaign_revision=8,
        )
