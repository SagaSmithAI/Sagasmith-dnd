from types import SimpleNamespace

import pytest
from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.combat_engine import CombatEngineError
from sagasmith_dnd.objects import validate_object_profile
from sagasmith_dnd.traps import source_trap_profile
from sagasmith_dnd_runtime import application_support as support
from sagasmith_dnd_runtime.receipt_signing import sign_receipt
from sagasmith_dnd_runtime.services.characters import CharactersService
from sagasmith_dnd_runtime.services.spells import SpellsService
from sagasmith_dnd_runtime.services.traps import (
    TrapService,
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

    normalized = _resolve_poison_needle_spatial_facts(profile, facts, **binding)
    assert normalized["distance_inches"] == 2.5
    assert normalized["reviewed_by"] == "principal:dm"

    with pytest.raises(CombatEngineError, match="3-inch range"):
        _resolve_poison_needle_spatial_facts(profile, {**facts, "distance_inches": 3.1}, **binding)
    with pytest.raises(CombatEngineError, match="stale for the current campaign revision"):
        _resolve_poison_needle_spatial_facts(profile, {**facts, "campaign_revision": 8}, **binding)


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
    excerpt = 'Fire statue source.\ntrap_profile: {"profile_id":"srd5.1.fire_breathing_statue"}'
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
    profile = source_trap_profile({"profile_id": "srd5.1.fire_breathing_statue"}, marker)
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
    profile = source_trap_profile({"profile_id": "srd5.1.fire_breathing_statue"}, marker)
    encounter = {
        "active": True,
        "positioning_mode": "grid",
        "combatants": [{"actor_id": "actor-1", "conditions": []}],
    }
    with pytest.raises(CombatEngineError, match="exact reviewed area facts"):
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


def test_annihilated_character_update_cannot_restore_hp_or_equipment_with_body_state_unchanged():
    before_sheet = default_character_sheet()
    before_sheet["body_state"] = "annihilated"
    before_sheet["combat"]["hp"]["value"] = 0
    before_sheet["conditions"] = ["dead"]
    attempted_sheet = support.deepcopy(before_sheet)
    attempted_sheet["combat"]["hp"]["value"] = 1
    attempted_sheet["inventory"]["items"] = [
        {
            "id": "restored-kit",
            "name": "Restored Kit",
            "kind": "equipment",
            "quantity": 1,
            "weight_oz": 1,
            "attunement": "none",
        }
    ]
    service = object.__new__(CharactersService)
    before = SimpleNamespace(sheet=before_sheet)

    with pytest.raises(
        CombatEngineError,
        match="annihilated character sheet cannot be modified",
    ):
        service.update_character(
            before,
            operation="character.sheet.replace",
            sheet=attempted_sheet,
        )

    assert before_sheet["body_state"] == "annihilated"
    assert before_sheet["combat"]["hp"]["value"] == 0
    assert before_sheet["inventory"]["items"] == []


def _sphere_contact_service(target):
    service = object.__new__(TrapService)
    service.modules = SimpleNamespace(
        current_scene=lambda campaign_id, *, scope_id: {"scene_id": "scene-1"}
    )
    service.require_campaign_actor = lambda campaign_id, actor_id: None
    service.characters = SimpleNamespace(get=lambda actor_id: target)
    service.content_authority_secret = b"sphere-test-secret"
    commits = []

    def commit(campaign, state, **kwargs):
        commits.append((state, kwargs))
        return {"status": "committed", **kwargs["response_fields"]}

    service.commit_campaign_state = commit
    return service, commits


def _sphere_contact_kwargs(campaign, facts):
    return {
        "campaign": campaign,
        "trap_id": "sphere-1",
        "exact_source": "module:crypt#sphere",
        "profile": source_trap_profile(
            {"profile_id": "srd5.1.sphere_of_annihilation"},
            'trap_profile: {"profile_id":"srd5.1.sphere_of_annihilation"}',
        ),
        "initiating_actor_id": "dm-controlled-actor",
        "contact_facts": facts,
        "source_scene_id": "scene-1",
        "principal_id": "system:local",
        "branch_id": "main",
        "idempotency_key": "sphere-contact",
        "scope": "trap-state:campaign-1:main:system:local",
        "replay_payload": {"payload": {"contact_facts": facts}, "branch_id": "main"},
        "stream": SimpleNamespace(draw_count=0, receipt=lambda: None),
    }


def test_sphere_contact_runtime_atomically_annihilates_actor_and_clears_property():
    target_sheet = default_character_sheet()
    target_sheet["inventory"]["wallet"]["gp"] = 12
    target_sheet["inventory"]["items"] = [{"id": "ring", "attunement": "attuned"}]
    target_sheet["inventory"]["equipment_slots"]["ring_1"] = "ring"
    target = SimpleNamespace(
        id="actor-1", campaign_id="campaign-1", revision=4, sheet=target_sheet, notes={}
    )
    campaign = SimpleNamespace(
        id="campaign-1", revision=8, timeline_epoch=1, state={"trap_state": {}}
    )
    facts = {
        "decision_id": "contact-1",
        "reason": "The actor entered the stone mouth.",
        "scene_id": "scene-1",
        "trap_id": "sphere-1",
        "target_actor_id": "actor-1",
        "target_actor_revision": 4,
        "source_ref": "module:crypt#sphere",
        "campaign_revision": 8,
        "reviewed_by": "system:local",
        "enters_mouth": True,
    }
    service, commits = _sphere_contact_service(target)

    result = service._source_bound_sphere_contact(**_sphere_contact_kwargs(campaign, facts))

    assert result["body_state"] == "annihilated"
    assert result["annihilated"] is True
    assert len(commits) == 1
    state, kwargs = commits[0]
    update = kwargs["character_updates"][0]
    assert update.expected_revision == 4
    assert update.sheet["body_state"] == "annihilated"
    assert update.sheet["combat"]["hp"]["value"] == 0
    assert update.sheet["conditions"] == ["dead"]
    assert update.sheet["inventory"]["wallet"]["gp"] == 0
    assert update.sheet["inventory"]["items"] == []
    assert update.sheet["inventory"]["equipment_slots"]["ring_1"] is None
    assert state["trap_state"]["traps"]["sphere-1"]["status"] == "armed"
    assert kwargs["expected_campaign_revision"] == 8


def test_sphere_contact_runtime_rejects_stale_actor_or_forged_reviewer_without_commit():
    target = SimpleNamespace(
        id="actor-1",
        campaign_id="campaign-1",
        revision=4,
        sheet=default_character_sheet(),
        notes={},
    )
    campaign = SimpleNamespace(
        id="campaign-1", revision=8, timeline_epoch=1, state={"trap_state": {}}
    )
    facts = {
        "decision_id": "contact-1",
        "reason": "The actor entered the stone mouth.",
        "scene_id": "scene-1",
        "trap_id": "sphere-1",
        "target_actor_id": "actor-1",
        "target_actor_revision": 3,
        "source_ref": "module:crypt#sphere",
        "campaign_revision": 8,
        "reviewed_by": "system:local",
        "enters_mouth": True,
    }
    service, commits = _sphere_contact_service(target)

    with pytest.raises(CombatEngineError, match="actor revision is stale"):
        service._source_bound_sphere_contact(**_sphere_contact_kwargs(campaign, facts))
    assert commits == []

    forged = {**facts, "target_actor_revision": 4, "reviewed_by": "player:attacker"}
    with pytest.raises(CombatEngineError, match="authenticated campaign DM"):
        service._source_bound_sphere_contact(**_sphere_contact_kwargs(campaign, forged))
    assert commits == []


def test_sphere_contact_runtime_destroys_exact_signed_scene_object_atomically():
    object_source = {
        "module_id": "module-1",
        "scene_id": "scene-1",
        "chunk_id": "relic-chunk",
        "page_start": 1,
        "page_end": 1,
        "heading_path": ["Crypt", "Relic"],
        "content_sha256": "a" * 64,
    }
    profile = validate_object_profile(
        {
            "id": "relic-1",
            "name": "Relic",
            "scene_id": "scene-1",
            "material": "stone",
            "size": "medium",
            "resilience": "resilient",
            "armor_class": 17,
            "hit_points": 30,
        }
    )
    secret = b"sphere-test-secret"
    approval = sign_receipt(
        {
            "purpose": "source_object_profile",
            "schema_version": 1,
            "campaign_id": "campaign-1",
            "branch_id": "main",
            "profile_digest": support.json_sha256(profile),
            "source_ref": object_source,
            "reviewed_by": "system:local",
            "ruling": {"reason": "Reviewed source object statistics."},
        },
        secret,
    )
    campaign = SimpleNamespace(
        id="campaign-1",
        revision=8,
        timeline_epoch=1,
        state={
            "trap_state": {},
            "scene_objects": {
                "scene-1": {
                    "relic-1": {
                        "profile": profile,
                        "source_ref": object_source,
                        "profile_approval": approval,
                        "hit_points": 30,
                        "destroyed": False,
                    }
                }
            },
        },
    )
    service, commits = _sphere_contact_service(None)
    service.content_authority_secret = secret
    service.managed_module_source_ref = lambda *args, **kwargs: (object_source, None, None)
    facts = {
        "decision_id": "object-contact-1",
        "reason": "The relic entered the stone mouth.",
        "scene_id": "scene-1",
        "trap_id": "sphere-1",
        "target_scene_object_id": "relic-1",
        "target_scene_object_source_ref": object_source,
        "source_ref": "module:crypt#sphere",
        "campaign_revision": 8,
        "reviewed_by": "system:local",
        "enters_mouth": True,
    }

    result = service._source_bound_sphere_contact(**_sphere_contact_kwargs(campaign, facts))

    assert result["destroyed"] is True
    assert result["destruction_cause"]["kind"] == "sphere_of_annihilation_contact"
    assert len(commits) == 1
    state, kwargs = commits[0]
    destroyed = state["scene_objects"]["scene-1"]["relic-1"]
    assert destroyed["destroyed"] is True
    assert destroyed["hit_points"] == 0
    assert destroyed["destruction_cause"]["trap_id"] == "sphere-1"
    assert kwargs["character_updates"] == []
    assert kwargs["expected_campaign_revision"] == 8
