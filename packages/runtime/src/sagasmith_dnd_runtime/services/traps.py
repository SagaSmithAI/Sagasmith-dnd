"""Source-bound trap checks and one atomic 2014 failure settlement path."""

from __future__ import annotations

from typing import Any

from sagasmith_dnd.combat_engine import resolve_fall_to_sheet
from sagasmith_dnd.conditions import effect_is_immune, effect_is_suspended_by_petrification
from sagasmith_dnd.engine import resolve_attack, roll
from sagasmith_dnd.traps import (
    apply_locking_pit_spring_disable,
    build_poison_needle_condition_effect,
    source_trap_profile,
    transition_trap_state,
    validate_falling_net_rescue_facts,
    validate_locking_pit_disable_scene_facts,
    validate_locking_pit_disable_state,
    validate_poison_needle_spatial_facts,
    validate_rolling_sphere_trigger_fact,
    validate_source_pit_depth,
    validate_source_trap_area_spatial_facts,
)

from .. import application_support as _support

_SOURCE_PIT_PROFILES = {
    "srd5.1.simple_pit",
    "srd5.1.hidden_pit",
    "srd5.1.spiked_simple_pit",
    "srd5.1.spiked_hidden_pit",
    "srd5.1.poisoned_spiked_simple_pit",
    "srd5.1.poisoned_spiked_hidden_pit",
    "srd5.1.locking_pit",
    "srd5.1.spiked_locking_pit",
    "srd5.1.poisoned_spiked_locking_pit",
}
_SOURCE_LOCKING_PIT_PROFILES = {
    "srd5.1.locking_pit",
    "srd5.1.spiked_locking_pit",
    "srd5.1.poisoned_spiked_locking_pit",
}
_SOURCE_AREA_TARGET_PROFILES = {
    "srd5.1.collapsing_roof",
    "srd5.1.falling_net",
    "srd5.1.fire_breathing_statue",
    "srd5.1.poison_darts",
    *_SOURCE_PIT_PROFILES,
}
_SOURCE_TRIGGER_FACT_KINDS = {
    "srd5.1.fire_breathing_statue": ("pressure_plate_weight", "plate_id"),
    "srd5.1.poison_darts": ("pressure_plate_weight", "plate_id"),
    "srd5.1.rolling_sphere": ("pressure_plate_weight", "plate_id"),
    "srd5.1.poison_needle": ("lock_opened", "lock_id"),
    "srd5.1.simple_pit": ("step_on_cover", "cover_id"),
    "srd5.1.hidden_pit": ("step_on_cover", "cover_id"),
    "srd5.1.spiked_simple_pit": ("step_on_cover", "cover_id"),
    "srd5.1.spiked_hidden_pit": ("step_on_cover", "cover_id"),
    "srd5.1.poisoned_spiked_simple_pit": ("step_on_cover", "cover_id"),
    "srd5.1.poisoned_spiked_hidden_pit": ("step_on_cover", "cover_id"),
    "srd5.1.locking_pit": ("step_on_cover", "cover_id"),
    "srd5.1.spiked_locking_pit": ("step_on_cover", "cover_id"),
    "srd5.1.poisoned_spiked_locking_pit": ("step_on_cover", "cover_id"),
    "srd5.1.collapsing_roof": ("knock_wedged_beam", "beam_id"),
}


def _apply_falling_net_effects(
    sheet: dict[str, Any], *, strength_save_succeeded: bool
) -> tuple[dict[str, Any], bool]:
    """Apply the net's automatic restraint and failed-save prone condition.

    Return whether this net added the restrained condition, so later escape or
    net destruction removes only the condition owned by this trap instance.
    """
    updated = _support.deepcopy(sheet)
    had_restrained = "restrained" in set(updated.get("conditions") or [])
    _support.apply_condition_change(updated, condition_id="restrained", add=True)
    restrained_added = not had_restrained and "restrained" in set(updated.get("conditions") or [])
    if not strength_save_succeeded:
        _support.apply_condition_change(updated, condition_id="prone", add=True)
    return updated, restrained_added


def _has_thieves_tools_proficiency(sheet: dict[str, Any]) -> bool:
    tool_proficiencies = {
        str(item).casefold().replace("'", "").replace("_", " ").strip()
        for item in dict(dict(sheet.get("traits") or {}).get("proficiencies") or {}).get(
            "tools", []
        )
    }
    return bool({"thieves tools", "thieves tool"} & tool_proficiencies)


def _require_rolling_sphere_trigger_settlement(
    profile: dict[str, Any],
    trigger_fact: dict[str, Any] | None,
    *,
    scene_id: str,
    trap_id: str,
) -> None:
    try:
        validate_rolling_sphere_trigger_fact(
            profile,
            trigger_fact,
            scene_id=scene_id,
            trap_id=trap_id,
        )
    except ValueError as exc:
        raise _support.CombatEngineError(str(exc)) from exc
    raise _support.CombatEngineError(
        "Rolling Sphere pressure trigger is source-bound, but activation is unresolved: "
        "the encounter has no trap initiative participant or authoritative sphere path and "
        "creature-collision settlement; no state was written"
    )


def _resolve_source_trap_area_targets(
    profile: dict[str, Any],
    spatial_facts: dict[str, Any] | None,
    *,
    encounter: dict[str, Any],
    scene_id: str,
    trap_id: str,
    reviewed_by: str,
    source_ref: str,
    campaign_revision: int,
) -> tuple[list[str], dict[str, Any]]:
    if encounter.get("active") is not True:
        raise _support.CombatEngineError(
            "source-bound area targets require a current active encounter"
        )
    positioning_mode = str(encounter.get("positioning_mode") or "agent")
    if positioning_mode not in {"agent", "grid"}:
        raise _support.CombatEngineError("trap area targeting requires Grid or Agent positioning")
    combatants = list(encounter.get("combatants") or [])
    actor_ids = [str(item.get("actor_id") or "") for item in combatants]
    eligible_actor_ids = [
        str(item.get("actor_id") or "")
        for item in combatants
        if "dead" not in {str(condition).casefold() for condition in item.get("conditions", [])}
    ]
    try:
        normalized = validate_source_trap_area_spatial_facts(
            profile,
            spatial_facts,
            scene_id=scene_id,
            trap_id=trap_id,
            encounter_id=str(encounter.get("id") or ""),
            source_ref=source_ref,
            campaign_revision=campaign_revision,
            reviewed_by=reviewed_by,
            actor_ids=actor_ids,
            eligible_actor_ids=eligible_actor_ids,
            positioning_mode=positioning_mode,
            battle_map=encounter.get("battle_map"),
            combatants=combatants,
        )
    except ValueError as exc:
        raise _support.CombatEngineError(str(exc)) from exc
    return list(normalized["affected_actor_ids"]), normalized


def _resolve_poison_needle_spatial_facts(
    profile: dict[str, Any],
    spatial_facts: dict[str, Any] | None,
    *,
    scene_id: str,
    scene_revision: int,
    trap_id: str,
    target_actor_id: str,
    source_ref: str,
    campaign_revision: int,
    reviewed_by: str,
) -> dict[str, Any]:
    try:
        return validate_poison_needle_spatial_facts(
            profile,
            spatial_facts,
            scene_id=scene_id,
            scene_revision=scene_revision,
            trap_id=trap_id,
            target_actor_id=target_actor_id,
            source_ref=source_ref,
            campaign_revision=campaign_revision,
            reviewed_by=reviewed_by,
        )
    except ValueError as exc:
        raise _support.CombatEngineError(str(exc)) from exc


def _revealed_detection_facts(
    profile: dict[str, Any], checks: list[dict[str, Any]], method: str | None = None
) -> list[str]:
    """Return only fixed source clues unlocked by successful engine checks."""
    detect = profile.get("detect")
    if isinstance(detect, dict) and method is not None:
        for spec in list(detect.get("no_roll") or []):
            if isinstance(spec, dict) and spec.get("method") == method:
                clues = spec.get("reveals_on_success")
                return (
                    list(clues)
                    if isinstance(clues, list) and all(isinstance(item, str) for item in clues)
                    else []
                )
    if not checks or any(item.get("success") is not True for item in checks):
        return []
    clues_by_ability = (
        detect.get("reveals_on_success_by_ability") if isinstance(detect, dict) else None
    )
    if len(checks) == 1 and isinstance(clues_by_ability, dict):
        ability_clues = clues_by_ability.get(str(checks[0].get("ability") or "").casefold())
        if isinstance(ability_clues, list) and all(isinstance(item, str) for item in ability_clues):
            return list(ability_clues)
    clues = detect.get("reveals_on_success") if isinstance(detect, dict) else None
    if not isinstance(clues, list) or any(not isinstance(item, str) for item in clues):
        return []
    return list(clues)


class TrapService:
    def source_bound_trap_transition(
        self,
        campaign_id: str,
        trap_id: str,
        action: str,
        source_ref: str,
        source_excerpt: str,
        profile: dict[str, Any],
        actor_id: str,
        *,
        principal_id: str = _support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
        method: str | None = None,
        target_ids: list[str] | None = None,
        area_confirmed: bool | None = None,
        trap_depth_ft: int | None = None,
        trigger_fact: dict[str, Any] | None = None,
        scene_facts: dict[str, Any] | None = None,
        spatial_facts: dict[str, Any] | None = None,
        rescue_target_id: str | None = None,
        rescue_facts: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve source-bound trap detection and explicit no-roll bypass actions.

        The request selects a fixed profile id. Rules are loaded from the domain
        SRD registry, never from caller-provided DC, damage, or effect values.
        Trigger settlement stays fail-closed until a trap's complete target,
        area, condition, and object mechanics can be applied atomically.
        """
        if action not in {
            "detect",
            "passive_detect",
            "disable",
            "bypass",
            "trigger",
            "escape",
            "rescue",
        }:
            raise _support.CombatEngineError(
                "trap action must be detect, passive_detect, disable, bypass, trigger, or escape"
            )
        try:
            normalized_profile = source_trap_profile(profile, source_excerpt)
        except ValueError as exc:
            raise _support.CombatEngineError(str(exc)) from exc
        profile_id = str(normalized_profile["profile_id"])
        area_target_action = profile_id in _SOURCE_AREA_TARGET_PROFILES and (
            action == "trigger"
            or (
                action == "disable"
                and dict(normalized_profile.get("disable") or {}).get("failed_check") == "trigger"
            )
        )
        poison_needle_range_action = profile_id == "srd5.1.poison_needle" and (
            action == "trigger"
            or (
                action == "disable"
                and dict(normalized_profile.get("disable") or {}).get("failed_check") == "trigger"
            )
        )
        if action == "bypass":
            if method not in normalized_profile["bypass_methods"]:
                raise _support.CombatEngineError(
                    "bypass method is not authorized by this source-bound trap profile"
                )
        elif action == "disable":
            disable_spec = normalized_profile.get("disable")
            if not isinstance(disable_spec, dict):
                raise _support.CombatEngineError("this source-bound trap has no disable check")
            methods = [str(disable_spec.get("tool") or "")]
            if isinstance(disable_spec.get("ability"), str):
                methods.append(disable_spec["ability"])
            alternative = disable_spec.get("no_tool_alternative")
            if isinstance(alternative, dict) and alternative.get("requires_edged_tool"):
                methods.append("edged_tool")
            if method not in methods:
                raise _support.CombatEngineError(
                    "disable method must match this source-bound trap profile"
                )
            if (
                disable_spec.get("failed_check") == "trigger"
                and not area_target_action
                and not poison_needle_range_action
                and area_confirmed is not True
            ):
                raise _support.CombatEngineError(
                    "failed disable requires an explicit confirmed source-defined area fact"
                )
            failed_area_trigger = disable_spec.get("failed_check") == "trigger"
            if target_ids is not None and (not failed_area_trigger or area_target_action):
                raise _support.CombatEngineError("disable does not accept target_ids")
        elif action == "detect":
            active_specs = list(dict(normalized_profile.get("detect") or {}).get("active") or [])
            no_roll_methods = {
                str(spec.get("method") or "")
                for spec in list(dict(normalized_profile.get("detect") or {}).get("no_roll") or [])
                if isinstance(spec, dict)
            }
            allowed_abilities = {str(spec.get("ability") or "").casefold() for spec in active_specs}
            if len(active_specs) > 1 and method is None:
                raise _support.CombatEngineError(
                    "active detection requires selecting one source-defined ability"
                )
            if (
                method is not None
                and method not in no_roll_methods
                and (method.casefold() not in allowed_abilities)
            ):
                raise _support.CombatEngineError(
                    "detection method must match an active ability or source-defined no-roll method"
                )
        elif method is not None:
            raise _support.CombatEngineError(
                "method is accepted only for trap bypass or active detection selection"
            )
        if area_target_action:
            if area_confirmed is not None:
                raise _support.CombatEngineError(
                    "source-bound area effects require reviewed spatial_facts, not area_confirmed"
                )
            if target_ids is not None:
                raise _support.CombatEngineError(
                    "source-bound area effects derive all affected actors from spatial_facts"
                )
        elif spatial_facts is not None and not poison_needle_range_action:
            raise _support.CombatEngineError(
                "spatial_facts are accepted only for source-defined area or Poison Needle "
                "range settlement"
            )
        if poison_needle_range_action:
            if area_confirmed is not None:
                raise _support.CombatEngineError(
                    "Poison Needle range requires reviewed spatial_facts, not area_confirmed"
                )
            if not isinstance(spatial_facts, dict):
                raise _support.CombatEngineError(
                    "Poison Needle requires DM-reviewed spatial_facts for its 3-inch range"
                )
        if action in {"trigger", "escape", "rescue"}:
            is_rolling_sphere_trigger = (
                action == "trigger" and profile_id == "srd5.1.rolling_sphere"
            )
            if (
                action == "trigger"
                and not is_rolling_sphere_trigger
                and not area_target_action
                and not poison_needle_range_action
                and area_confirmed is not True
            ):
                raise _support.CombatEngineError(
                    "trap trigger requires an explicit confirmed source-defined area fact"
                )
            if is_rolling_sphere_trigger and area_confirmed is not None:
                raise _support.CombatEngineError(
                    "Rolling Sphere trigger does not accept caller-confirmed area facts"
                )
            if is_rolling_sphere_trigger and target_ids is not None:
                raise _support.CombatEngineError(
                    "Rolling Sphere target selection requires its unresolved authoritative path"
                )
            if action == "escape" and (target_ids is not None or area_confirmed is not None):
                raise _support.CombatEngineError("escape does not accept target or area facts")
            if action == "rescue":
                if (
                    profile_id != "srd5.1.falling_net"
                    or not rescue_target_id
                    or not isinstance(rescue_facts, dict)
                ):
                    raise _support.CombatEngineError(
                        "Falling Net rescue requires a separate target and DM-reviewed reach facts"
                    )
            elif rescue_target_id is not None or rescue_facts is not None:
                raise _support.CombatEngineError(
                    "rescue target facts are accepted only for Falling Net rescue"
                )
            if action == "trigger" and normalized_profile["profile_id"] in _SOURCE_PIT_PROFILES:
                try:
                    validate_source_pit_depth(normalized_profile, trap_depth_ft)
                except ValueError as exc:
                    raise _support.CombatEngineError(str(exc)) from exc
            elif trap_depth_ft is not None:
                raise _support.CombatEngineError(
                    "trap_depth_ft is accepted only for source-bound pit triggers"
                )
            expected_fact = _SOURCE_TRIGGER_FACT_KINDS.get(normalized_profile["profile_id"])
            if action == "trigger" and expected_fact is not None:
                if not isinstance(trigger_fact, dict):
                    raise _support.CombatEngineError(
                        f"{normalized_profile['name']} trigger requires exact source facts"
                    )
                if (
                    normalized_profile["profile_id"] == "srd5.1.poison_needle"
                    and trigger_fact.get("kind") == "lock_pick_failed"
                ):
                    fact_kind, identity_key = "lock_pick_failed", "lock_id"
                    expected_keys = {"kind", "scene_id", identity_key, "pick_succeeded"}
                else:
                    fact_kind, identity_key = expected_fact
                    expected_keys = {"kind", "scene_id", identity_key}
                    if fact_kind == "pressure_plate_weight":
                        expected_keys.add("weight_lb")
                    elif fact_kind == "lock_opened":
                        expected_keys.add("proper_key_used")
                    elif fact_kind == "knock_wedged_beam":
                        expected_keys.add("action_spent")
                if set(trigger_fact) != expected_keys:
                    raise _support.CombatEngineError(
                        f"{normalized_profile['name']} trigger requires exact {fact_kind} facts"
                    )
                if trigger_fact.get("kind") != fact_kind:
                    raise _support.CombatEngineError(
                        f"{normalized_profile['name']} trigger fact kind must be {fact_kind}"
                    )
                component_id = trigger_fact.get(identity_key)
                if not isinstance(component_id, str) or component_id != trap_id:
                    raise _support.CombatEngineError(
                        f"{normalized_profile['name']} trigger fact must identify "
                        "this trap component"
                    )
                if fact_kind == "pressure_plate_weight":
                    weight = trigger_fact.get("weight_lb")
                    numeric_weight = not isinstance(weight, bool) and isinstance(
                        weight, (int, float)
                    )
                    is_rolling_sphere = normalized_profile["profile_id"] == "srd5.1.rolling_sphere"
                    if not numeric_weight or (
                        numeric_weight and (weight < 20 if is_rolling_sphere else weight <= 20)
                    ):
                        if is_rolling_sphere:
                            raise _support.CombatEngineError(
                                "Rolling Sphere triggers at 20 lb or greater"
                            )
                        raise _support.CombatEngineError(
                            "pressure plate trigger requires a confirmed weight greater than 20 lb"
                        )
                elif (
                    fact_kind == "lock_opened" and trigger_fact.get("proper_key_used") is not False
                ):
                    raise _support.CombatEngineError(
                        "Poison Needle triggers only when its lock opens without the proper key"
                    )
                elif (
                    fact_kind == "lock_pick_failed"
                    and trigger_fact.get("pick_succeeded") is not False
                ):
                    raise _support.CombatEngineError(
                        "Poison Needle triggers only on a failed lock-pick attempt"
                    )
                elif (
                    fact_kind == "knock_wedged_beam"
                    and trigger_fact.get("action_spent") is not True
                ):
                    raise _support.CombatEngineError(
                        "Collapsing Roof beam trigger requires confirmation that the "
                        "action was spent"
                    )
            elif action == "trigger" and expected_fact is None and trigger_fact is not None:
                raise _support.CombatEngineError(
                    "trigger_fact is not defined for this source-bound trap profile"
                )
            if action == "trigger" and normalized_profile["profile_id"] not in {
                "srd5.1.poison_darts",
                "srd5.1.falling_net",
                "srd5.1.poison_needle",
                "srd5.1.collapsing_roof",
                "srd5.1.fire_breathing_statue",
                *_SOURCE_PIT_PROFILES,
                "srd5.1.rolling_sphere",
            }:
                raise _support.CombatEngineError(
                    "this source-bound trap trigger has no complete runtime settlement"
                )
            if action == "escape" and normalized_profile["profile_id"] not in {
                "srd5.1.falling_net",
                *_SOURCE_LOCKING_PIT_PROFILES,
            }:
                raise _support.CombatEngineError(
                    "this source-bound trap has no supported escape procedure"
                )
        elif action == "disable" and trigger_fact is not None:
            raise _support.CombatEngineError(
                "trigger_fact is accepted only for direct trap trigger"
            )
        elif action not in {"disable"} and (
            target_ids is not None
            or area_confirmed is not None
            or trap_depth_ft is not None
            or trigger_fact is not None
            or scene_facts is not None
        ):
            raise _support.CombatEngineError(
                "target_ids and area_confirmed are accepted only for trap trigger"
            )
        if action == "disable" and normalized_profile["profile_id"] in _SOURCE_LOCKING_PIT_PROFILES:
            if method != "thieves_tools":
                raise _support.CombatEngineError(
                    "locking pit disable requires the source-defined thieves_tools method"
                )
            if area_confirmed is not None or trap_depth_ft is not None:
                raise _support.CombatEngineError(
                    "locking pit disable does not accept trigger area or depth facts"
                )
        elif scene_facts is not None:
            raise _support.CombatEngineError(
                "scene_facts are accepted only for source-defined locking pit disable"
            )
        self.access.require_campaign(campaign_id, principal_id, roles=_support.CAMPAIGN_DM_ROLES)
        self.require_campaign_actor(campaign_id, actor_id)
        self.require_write_contract(expected_revision, idempotency_key)
        if action == "disable" and normalized_profile["profile_id"] not in {
            "srd5.1.falling_net",
            "srd5.1.poison_needle",
            "srd5.1.collapsing_roof",
            "srd5.1.fire_breathing_statue",
            *_SOURCE_LOCKING_PIT_PROFILES,
        }:
            raise _support.CombatEngineError(
                "this source-bound trap disable settlement is unsupported"
            )
        resolved_branch = self.require_current_branch(campaign_id, branch_id)
        payload = {
            "trap_id": trap_id,
            "action": action,
            "source_ref": source_ref,
            "source_excerpt": source_excerpt,
            "profile": normalized_profile,
            "actor_id": actor_id,
            "method": method,
            "target_ids": target_ids,
            "area_confirmed": area_confirmed,
            "trap_depth_ft": trap_depth_ft,
            "trigger_fact": trigger_fact,
            "scene_facts": scene_facts,
            "spatial_facts": spatial_facts,
            "rescue_target_id": rescue_target_id,
            "rescue_facts": rescue_facts,
        }
        scope = f"trap-state:{campaign_id}:{resolved_branch}:{principal_id}"
        replay_payload = {"payload": payload, "branch_id": resolved_branch}
        replay = self.replay_idempotent(scope, idempotency_key, replay_payload)
        if replay is not None:
            return replay

        campaign = self.campaigns.get(campaign_id)
        if expected_revision is not None and campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: expected "
                f"{expected_revision}, found {campaign.revision}"
            )
        if _support.active_random_stream() is None:
            stream = _support.CampaignRandomStream.from_campaign_state(
                campaign_id,
                campaign.state,
                operation="trap.state.transition",
                idempotency_key=str(idempotency_key or ""),
                campaign_revision=campaign.revision,
            )
            with _support.use_random_stream(stream):
                return self.source_bound_trap_transition(
                    campaign_id,
                    trap_id,
                    action,
                    source_ref,
                    source_excerpt,
                    profile,
                    actor_id,
                    principal_id=principal_id,
                    expected_revision=expected_revision,
                    branch_id=resolved_branch,
                    idempotency_key=idempotency_key,
                    method=method,
                    target_ids=target_ids,
                    area_confirmed=area_confirmed,
                    trap_depth_ft=trap_depth_ft,
                    trigger_fact=trigger_fact,
                    scene_facts=scene_facts,
                    spatial_facts=spatial_facts,
                    rescue_target_id=rescue_target_id,
                    rescue_facts=rescue_facts,
                )
        stream = _support.active_random_stream()
        random_state = _support.validate_random_stream_state(
            dict(campaign.state or {}).get("random_stream")
            or _support.initial_random_stream(f"sagasmith-dnd:{campaign_id}")
        )
        if (
            stream.campaign_id != campaign_id
            or stream.seed != random_state["seed"]
            or stream.start_position != random_state["position"]
            or (
                stream.campaign_revision is not None
                and stream.campaign_revision != campaign.revision
            )
        ):
            raise _support.CombatEngineError(
                "trap checks require the current campaign random snapshot"
            )

        exact_source, _, expanded = self.managed_module_source_ref(
            campaign_id,
            source_ref,
            require_exact=True,
            require_active_module=True,
        )
        assert expanded is not None
        self.managed_module_source_excerpt(
            expanded,
            source_excerpt,
            field="trap source_excerpt",
            minimum_length=10,
        )
        if action == "trigger":
            current_traps = dict(dict(campaign.state or {}).get("trap_state") or {}).get(
                "traps", {}
            )
            current_trap = dict(dict(current_traps).get(trap_id) or {})
            if (
                normalized_profile["profile_id"] == "srd5.1.poison_darts"
                and current_trap.get("source_ref") == exact_source
                and current_trap.get("bypassed") is True
                and current_trap.get("bypass_method") == "stuff_dart_holes"
            ):
                raise _support.CombatEngineError(
                    "stuffing the dart holes prevents this trap from firing"
                )
            if (
                current_trap.get("source_ref") == exact_source
                and current_trap.get("bypassed") is True
                and current_trap.get("bypass_method") == "wedge_pressure_plate"
            ):
                raise _support.CombatEngineError(
                    "a pressure-plate wedge prevents this trap from triggering"
                )
            if (
                normalized_profile["profile_id"] in _SOURCE_PIT_PROFILES
                and current_trap.get("source_ref") == exact_source
                and current_trap.get("bypassed") is True
                and current_trap.get("bypass_method") == "wedge_cover"
            ):
                raise _support.CombatEngineError("wedging the pit cover prevents it from opening")
        actor = self.combat_actor_snapshot(actor_id)
        encounter = dict(dict(campaign.state or {}).get("combat") or {})
        ruleset = (
            self.encounter_rules_edition(campaign_id, encounter)
            if encounter
            else self.campaign_rules_edition(campaign_id)
        )
        if ruleset != "2014":
            raise _support.CombatEngineError("source-bound trap profiles require the 2014 ruleset")
        if action in {"trigger", "escape", "disable", "rescue"}:
            if (
                action == "trigger"
                and trigger_fact is not None
                and trigger_fact.get("scene_id") != str(expanded["scene"]["id"])
            ):
                raise _support.CombatEngineError(
                    "trap trigger fact scene_id does not match the source-defined scene"
                )
            normalized_needle_spatial_facts = None
            if poison_needle_range_action:
                scene_id = str(expanded["scene"]["id"])
                active_scene = self.modules.current_scene(campaign_id, scope_id="party")
                active_progress = (
                    dict(active_scene.get("progress") or {})
                    if isinstance(active_scene, dict)
                    else {}
                )
                scene_revision = (
                    active_scene.get("state_version", active_progress.get("state_version"))
                    if isinstance(active_scene, dict)
                    else None
                )
                if (
                    not isinstance(active_scene, dict)
                    or str(active_scene.get("scene_id") or "") != scene_id
                    or type(scene_revision) is not int
                ):
                    raise _support.CombatEngineError(
                        "Poison Needle spatial_facts require the active source scene and revision"
                    )
                normalized_needle_spatial_facts = _resolve_poison_needle_spatial_facts(
                    normalized_profile,
                    spatial_facts,
                    scene_id=scene_id,
                    scene_revision=scene_revision,
                    trap_id=trap_id,
                    target_actor_id=actor_id,
                    source_ref=exact_source,
                    campaign_revision=campaign.revision,
                    reviewed_by=principal_id,
                )
            if action == "trigger" and normalized_profile["profile_id"] == "srd5.1.rolling_sphere":
                _require_rolling_sphere_trigger_settlement(
                    normalized_profile,
                    trigger_fact,
                    scene_id=str(expanded["scene"]["id"]),
                    trap_id=trap_id,
                )
            normalized_area_spatial_facts = None
            if area_target_action:
                target_ids, normalized_area_spatial_facts = _resolve_source_trap_area_targets(
                    normalized_profile,
                    spatial_facts,
                    encounter=encounter,
                    scene_id=str(expanded["scene"]["id"]),
                    trap_id=trap_id,
                    reviewed_by=principal_id,
                    source_ref=exact_source,
                    campaign_revision=campaign.revision,
                )
                area_confirmed = True
            return self._source_bound_trap_effect_transition(
                campaign=campaign,
                campaign_id=campaign_id,
                trap_id=trap_id,
                action=action,
                exact_source=exact_source,
                scene_id=str(expanded["scene"]["id"]),
                normalized_profile=normalized_profile,
                actor_id=actor_id,
                rescue_target_id=rescue_target_id,
                rescue_facts=rescue_facts,
                method=method,
                target_ids=target_ids,
                area_confirmed=area_confirmed,
                trap_depth_ft=trap_depth_ft,
                trigger_fact=trigger_fact,
                scene_facts=scene_facts,
                spatial_facts=(
                    normalized_area_spatial_facts
                    if area_target_action
                    else normalized_needle_spatial_facts
                ),
                encounter=encounter,
                ruleset=ruleset,
                stream=stream,
                principal_id=principal_id,
                branch_id=resolved_branch,
                idempotency_key=idempotency_key,
                scope=scope,
                replay_payload=replay_payload,
            )
        checks: list[dict[str, Any]] = []
        no_roll_detection = None
        if action == "detect" and method is not None:
            no_roll_detection = next(
                (
                    spec
                    for spec in list(
                        dict(normalized_profile.get("detect") or {}).get("no_roll") or []
                    )
                    if isinstance(spec, dict) and spec.get("method") == method
                ),
                None,
            )
        if action in {"detect", "passive_detect"}:
            detection = normalized_profile["detect"]
            if action == "passive_detect":
                dc = detection.get("passive_dc")
                if dc is None:
                    raise _support.CombatEngineError(
                        "this source-bound trap profile has no passive detection rule"
                    )
                specs = [{"ability": "perception", "dc": dc}]
            elif no_roll_detection is not None:
                specs = []
            else:
                specs = list(detection["active"])
                if method is not None:
                    specs = [
                        spec
                        for spec in specs
                        if str(spec.get("ability") or "").casefold() == method.casefold()
                    ]
                    if not specs:
                        raise _support.CombatEngineError(
                            "detection method must match an active source-defined ability"
                        )
            for check_spec in specs:
                passive = action == "passive_detect"
                check_context = self.effective_rule_context(
                    campaign_id,
                    branch_id=resolved_branch,
                    facts={
                        "kind": "check",
                        "actor_id": actor_id,
                        "ability": check_spec["ability"],
                        "dc": check_spec["dc"],
                        "passive": passive,
                        "trap_id": trap_id,
                        "trap_source_ref": exact_source,
                        "trap_action": action,
                    },
                )
                resolved_check = _support.resolve_actor_check(
                    actor,
                    kind="check",
                    ability=check_spec["ability"],
                    dc=check_spec["dc"],
                    passive=passive,
                    encounter=encounter or None,
                    ruleset=ruleset,
                    rules=check_context,
                    rng=stream,
                )
                resolved_check = dict(resolved_check)
                resolved_check["ability"] = check_spec["ability"]
                resolved_check["passive"] = passive
                checks.append(resolved_check)
                if checks[-1].get("success") is not True:
                    break
        check = checks[-1] if checks else None

        next_state = _support.deepcopy(campaign.state)
        trap_state = dict(next_state.get("trap_state") or {})
        if action == "detect" and (
            no_roll_detection is not None
            or (checks and all(item.get("success") is True for item in checks))
        ):
            trap_state = transition_trap_state(
                trap_state,
                source_ref=exact_source,
                trap_id=trap_id,
                action="detect",
            )
        if action == "passive_detect" and check.get("success") is True:
            if len(normalized_profile["detect"]["active"]) == 1:
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="detect",
                )
            else:
                instances = dict(trap_state.get("traps") or {})
                instance = dict(instances.get(trap_id) or {})
                if instance.get("source_ref") not in (None, exact_source):
                    raise _support.CombatEngineError("trap instance is bound to a different source")
                instance.update({"source_ref": exact_source, "status": "armed", "suspected": True})
                instances[trap_id] = instance
                trap_state["traps"] = instances
        if action == "bypass":
            trap_state = transition_trap_state(
                trap_state,
                source_ref=exact_source,
                trap_id=trap_id,
                action="bypass",
                success=True,
            )
            trap_state["traps"][trap_id]["bypass_method"] = method

        revealed_facts = (
            _revealed_detection_facts(normalized_profile, checks, method)
            if action in {"detect", "passive_detect"}
            else []
        )
        result: dict[str, Any] = {"check": check, "checks": checks, "action": action}
        if revealed_facts:
            result["revealed_facts"] = revealed_facts
        character_updates: list[Any] = []
        rule_receipts = [receipt for item in checks for receipt in item.get("rule_receipts", [])]
        trap_state.setdefault("attempts", [])
        trap_state["attempts"] = [
            *list(trap_state["attempts"]),
            {
                "trap_id": trap_id,
                "source_ref": exact_source,
                "actor_id": actor_id,
                "action": action,
                "check": check,
                "checks": checks,
                **({"revealed_facts": revealed_facts} if revealed_facts else {}),
                "method": method,
                "campaign_revision": campaign.revision + 1,
            },
        ][-100:]
        next_state["trap_state"] = trap_state
        response_fields = {
            "status": "committed",
            "trap_id": trap_id,
            "action": action,
            "trap": trap_state.get("traps", {}).get(trap_id),
            "check": check,
            "checks": checks,
            **({"revealed_facts": revealed_facts} if revealed_facts else {}),
            **(
                {"failure_settlement": result["failure_settlement"]}
                if "failure_settlement" in result
                else {}
            ),
        }
        stream_receipt = stream.receipt() if stream.draw_count else None
        if stream_receipt is not None:
            response_fields["random_stream_receipt"] = stream_receipt
        return self.commit_campaign_state(
            campaign,
            next_state,
            operation="trap.state.transition",
            principal_id=principal_id,
            branch_id=resolved_branch,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=replay_payload,
            response_fields=response_fields,
            character_updates=character_updates,
            rule_receipts=rule_receipts,
            expected_campaign_revision=campaign.revision,
        )

    def _source_bound_trap_effect_transition(
        self,
        *,
        campaign: Any,
        campaign_id: str,
        trap_id: str,
        action: str,
        exact_source: str,
        scene_id: str,
        normalized_profile: dict[str, Any],
        actor_id: str,
        rescue_target_id: str | None,
        rescue_facts: dict[str, Any] | None,
        method: str | None,
        target_ids: list[str] | None,
        area_confirmed: bool | None,
        trap_depth_ft: int | None,
        trigger_fact: dict[str, Any] | None,
        scene_facts: dict[str, Any] | None,
        spatial_facts: dict[str, Any] | None,
        encounter: dict[str, Any],
        ruleset: str,
        stream: Any,
        principal_id: str,
        branch_id: str,
        idempotency_key: str,
        scope: str,
        replay_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Settle the bounded Poison Darts and Falling Net source mechanics."""
        profile_id = normalized_profile["profile_id"]
        if profile_id == "srd5.1.poison_darts" and action == "trigger":
            if (
                not isinstance(target_ids, list)
                or len(target_ids) > 20
                or any(not isinstance(item, str) or not item.strip() for item in target_ids)
                or len(set(target_ids)) != len(target_ids)
            ):
                raise _support.CombatEngineError(
                    "poison darts require reviewed spatial facts and at most 20 distinct targets"
                )
            snapshots = {}
            for target_id in target_ids:
                self.require_campaign_actor(campaign_id, target_id)
                if encounter.get("active"):
                    self.require_encounter_combatant(encounter, target_id, role="trap target")
                snapshots[target_id] = self.combat_actor_snapshot(target_id)
            next_state = _support.deepcopy(campaign.state)
            trap_state = dict(next_state.get("trap_state") or {})
            trap_state = transition_trap_state(
                trap_state,
                source_ref=exact_source,
                trap_id=trap_id,
                action="trigger",
            )
            trap_state = transition_trap_state(
                trap_state,
                source_ref=exact_source,
                trap_id=trap_id,
                action="settle",
            )
            darts_trap = dict(trap_state["traps"][trap_id])
            darts_trap["trigger_fact"] = dict(trigger_fact or {})
            darts_trap["spatial_facts"] = _support.deepcopy(spatial_facts)
            darts_trap["affected_actor_ids"] = list(target_ids)
            trap_state["traps"][trap_id] = darts_trap
            current_sheets = {
                target_id: _support.deepcopy(snapshot["sheet"])
                for target_id, snapshot in snapshots.items()
            }
            dart_results: list[dict[str, Any]] = []
            receipts: list[dict[str, Any]] = []
            concentration_events: list[tuple[str, dict[str, Any]]] = []
            for dart_index in range(1, int(normalized_profile["trigger"]["dart_count"]) + 1):
                if not target_ids:
                    dart_results.append(
                        {
                            "dart": dart_index,
                            "target_id": None,
                            "attack": {"hit": False, "reason": "no eligible creature in area"},
                        }
                    )
                    continue
                selected_id = target_ids[stream.randint(0, len(target_ids) - 1)]
                target_snapshot = snapshots[selected_id]
                target_snapshot = {**target_snapshot, "sheet": current_sheets[selected_id]}
                attack = resolve_attack(
                    armor_class=int(target_snapshot["derived"]["armor_class"]),
                    attack_bonus=int(normalized_profile["trigger"]["attack_bonus"]),
                    rng=stream,
                )
                entry: dict[str, Any] = {
                    "dart": dart_index,
                    "target_id": selected_id,
                    "attack": attack,
                }
                if attack["hit"]:
                    piercing_expression = "2d4" if attack["critical"] else "1d4"
                    piercing_roll = roll(piercing_expression, rng=stream)
                    piercing = _support.apply_damage_to_sheet(
                        current_sheets[selected_id],
                        amount=piercing_roll.total,
                        damage_type=normalized_profile["trigger"]["piercing_type"],
                        source=f"trap:{exact_source}:{trap_id}:dart:{dart_index}",
                        critical=bool(attack["critical"]),
                        ruleset=ruleset,
                        weapon_attack=True,
                        attack_facts={"kind": "trap_attack", "attack_bonus": 8},
                    )
                    current_sheets[selected_id] = piercing["sheet"]
                    piercing_public = {
                        key: value for key, value in piercing.items() if key != "sheet"
                    }
                    entry["piercing"] = {
                        "expression": piercing_expression,
                        "rolls": list(piercing_roll.rolls),
                        **piercing_public,
                    }
                    concentration_events.append((selected_id, piercing))
                    save_context = self.effective_rule_context(
                        campaign_id,
                        branch_id=branch_id,
                        facts={
                            "kind": "save_damage",
                            "actor_ids": [selected_id],
                            "ability": normalized_profile["trigger"]["save_ability"],
                            "dc": normalized_profile["trigger"]["save_dc"],
                            "trap_id": trap_id,
                            "trap_source_ref": exact_source,
                            "dart_index": dart_index,
                        },
                    )
                    poison_target = {**target_snapshot, "sheet": current_sheets[selected_id]}
                    settled = _support.resolve_save_damage_to_sheets(
                        [poison_target],
                        save_ability=normalized_profile["trigger"]["save_ability"],
                        save_dc=normalized_profile["trigger"]["save_dc"],
                        damage_expression=normalized_profile["trigger"]["poison_expression"],
                        damage_type=normalized_profile["trigger"]["poison_type"],
                        half_on_success=normalized_profile["trigger"]["half_on_success"],
                        source=f"trap:{exact_source}:{trap_id}:dart:{dart_index}:poison",
                        encounter=encounter or None,
                        ruleset=ruleset,
                        rules=save_context,
                        rng=stream,
                    )
                    current_sheets[selected_id] = settled["sheets"][selected_id]
                    poison_result = dict(settled["result"]["targets"][0])
                    entry["poison"] = poison_result
                    receipts.extend(receipt for receipt in poison_result.get("rule_receipts", []))
                    poison_damage = dict(poison_result.get("damage") or {})
                    if poison_damage:
                        concentration_events.append((selected_id, poison_damage))
                dart_results.append(entry)

            character_updates: list[Any] = []
            for target_id, snapshot in snapshots.items():
                updated_sheet = _support.validate_character_sheet(current_sheets[target_id])
                current_actor = self.characters.get(target_id)
                character_updates.append(
                    _support.CharacterStateUpdate(
                        character_id=target_id,
                        sheet=updated_sheet,
                        notes=_support.validate_character_notes(current_actor.notes),
                        expected_revision=current_actor.revision,
                    )
                )
                if encounter.get("active"):
                    for event_target, damage in concentration_events:
                        if event_target == target_id:
                            self.add_concentration_window(
                                encounter,
                                target_id,
                                damage.get("concentration"),
                                next_revision=campaign.revision + 1,
                            )
                    self.sync_combatant_conditions(encounter, target_id, updated_sheet)
                    _support.reconcile_readied_spells(encounter, target_id, updated_sheet)
            if encounter.get("active"):
                next_state["combat"] = encounter
            trap_state.setdefault("attempts", [])
            trap_state["attempts"] = [
                *list(trap_state["attempts"]),
                {
                    "trap_id": trap_id,
                    "source_ref": exact_source,
                    "actor_id": actor_id,
                    "action": action,
                    "area_confirmed": True,
                    "eligible_target_ids": target_ids,
                    "spatial_facts": _support.deepcopy(spatial_facts),
                    "darts": dart_results,
                    "campaign_revision": campaign.revision + 1,
                },
            ][-100:]
            next_state["trap_state"] = trap_state
            receipt = stream.receipt() if stream.draw_count else None
            response = {
                "status": "committed",
                "trap_id": trap_id,
                "action": action,
                "trap": trap_state["traps"][trap_id],
                "darts": dart_results,
                "eligible_target_ids": target_ids,
                "affected_actor_ids": list(target_ids),
                "spatial_facts": _support.deepcopy(spatial_facts),
            }
            if receipt is not None:
                response["random_stream_receipt"] = receipt
            return self.commit_campaign_state(
                campaign,
                next_state,
                operation="trap.state.transition",
                principal_id=principal_id,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=replay_payload,
                response_fields=response,
                character_updates=character_updates,
                rule_receipts=receipts,
                expected_campaign_revision=campaign.revision,
            )

        if profile_id == "srd5.1.poison_needle" and action in {"trigger", "disable"}:
            if target_ids is not None:
                raise _support.CombatEngineError(
                    "Poison Needle settles its single explicitly confirmed target"
                )
            self.require_campaign_actor(campaign_id, actor_id)
            if encounter.get("active"):
                self.require_encounter_combatant(encounter, actor_id, role="trap target")
            actor = self.combat_actor_snapshot(actor_id)
            disable_check = None
            if action == "disable":
                disable_spec = dict(normalized_profile.get("disable") or {})
                tool_proficiencies = {
                    str(item).casefold().replace("'", "").replace("_", " ").strip()
                    for item in dict(
                        dict(actor["sheet"].get("traits") or {}).get("proficiencies") or {}
                    ).get("tools", [])
                }
                has_tool_proficiency = bool({"thieves tools", "thieves tool"} & tool_proficiencies)
                check_context = self.effective_rule_context(
                    campaign_id,
                    branch_id=branch_id,
                    facts={
                        "kind": "check",
                        "actor_id": actor_id,
                        "ability": disable_spec["ability"],
                        "dc": disable_spec["dc"],
                        "tool": disable_spec["tool"],
                        "trap_id": trap_id,
                        "trap_source_ref": exact_source,
                        "trap_action": "disable",
                    },
                )
                disable_check = _support.resolve_actor_check(
                    actor,
                    kind="check",
                    ability=disable_spec["ability"],
                    dc=disable_spec["dc"],
                    proficient=has_tool_proficiency,
                    encounter=encounter or None,
                    ruleset=ruleset,
                    rules=check_context,
                    rng=stream,
                )
                disable_check = {
                    **disable_check,
                    "ability": disable_spec["ability"],
                    "tool": disable_spec["tool"],
                    "method": method,
                    "tool_proficient": has_tool_proficiency,
                }
                if disable_check.get("success") is True:
                    next_state = _support.deepcopy(campaign.state)
                    trap_state = dict(next_state.get("trap_state") or {})
                    trap_state = transition_trap_state(
                        trap_state,
                        source_ref=exact_source,
                        trap_id=trap_id,
                        action="disable",
                        success=True,
                    )
                    current_trap = dict(trap_state["traps"][trap_id])
                    current_trap["range_confirmed"] = True
                    current_trap["spatial_facts"] = _support.deepcopy(spatial_facts)
                    trap_state["traps"][trap_id] = current_trap
                    trap_state.setdefault("attempts", [])
                    trap_state["attempts"] = [
                        *list(trap_state["attempts"]),
                        {
                            "trap_id": trap_id,
                            "source_ref": exact_source,
                            "actor_id": actor_id,
                            "action": action,
                            "method": method,
                            "check": disable_check,
                            "spatial_facts": _support.deepcopy(spatial_facts),
                            "campaign_revision": campaign.revision + 1,
                        },
                    ][-100:]
                    next_state["trap_state"] = trap_state
                    receipt = stream.receipt() if stream.draw_count else None
                    response = {
                        "status": "committed",
                        "trap_id": trap_id,
                        "action": action,
                        "trap": current_trap,
                        "disable_check": disable_check,
                        "check": disable_check,
                        "spatial_facts": _support.deepcopy(spatial_facts),
                    }
                    if receipt is not None:
                        response["random_stream_receipt"] = receipt
                    return self.commit_campaign_state(
                        campaign,
                        next_state,
                        operation="trap.state.transition",
                        principal_id=principal_id,
                        branch_id=branch_id,
                        idempotency_key=idempotency_key,
                        scope=scope,
                        payload=replay_payload,
                        response_fields=response,
                        rule_receipts=list(disable_check.get("rule_receipts", [])),
                        expected_campaign_revision=campaign.revision,
                    )
            next_state = _support.deepcopy(campaign.state)
            trap_state = dict(next_state.get("trap_state") or {})
            if action == "disable":
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="disable",
                    success=False,
                )
            else:
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="trigger",
                )
            trap_state = transition_trap_state(
                trap_state,
                source_ref=exact_source,
                trap_id=trap_id,
                action="settle",
            )
            needle = normalized_profile["trigger"]
            piercing = _support.apply_damage_to_sheet(
                _support.deepcopy(actor["sheet"]),
                amount=needle["piercing_damage"],
                damage_type=needle["piercing_type"],
                source=f"trap:{exact_source}:{trap_id}:needle",
                ruleset=ruleset,
            )
            piercing_sheet = piercing["sheet"]
            poison_target = {
                **actor,
                "sheet": piercing_sheet,
                "derived": _support.derive_domain_character_sheet(piercing_sheet),
            }
            save_context = self.effective_rule_context(
                campaign_id,
                branch_id=branch_id,
                facts={
                    "kind": "save_damage",
                    "actor_ids": [actor_id],
                    "ability": needle["save_ability"],
                    "dc": needle["save_dc"],
                    "trap_id": trap_id,
                    "trap_source_ref": exact_source,
                    "target_within_source_defined_range": True,
                    "trap_spatial_decision_id": dict(spatial_facts or {}).get("decision_id"),
                    "target_distance_inches": dict(spatial_facts or {}).get("distance_inches"),
                },
            )
            settled = _support.resolve_save_damage_to_sheets(
                [poison_target],
                save_ability=needle["save_ability"],
                save_dc=needle["save_dc"],
                damage_expression=needle["poison_expression"],
                damage_type=needle["poison_type"],
                half_on_success=needle["poison_half_on_success"],
                source=f"trap:{exact_source}:{trap_id}:poison",
                encounter=encounter or None,
                ruleset=ruleset,
                rules=save_context,
                rng=stream,
            )
            poison = dict(settled["result"]["targets"][0])
            poison["damage_expression"] = needle["poison_expression"]
            poison["damage_roll"] = dict(settled["result"]["damage_roll"])
            updated_sheet = settled["sheets"][actor_id]
            effect = None
            effect_suppressed = None
            if poison.get("success") is False:
                effect = build_poison_needle_condition_effect(
                    profile_id=profile_id,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    target_actor_id=actor_id,
                )
                if effect_is_immune(updated_sheet, effect):
                    effect_suppressed = "immune"
                    effect = None
                elif effect_is_suspended_by_petrification(updated_sheet, effect):
                    effect_suppressed = "petrified"
                    effect = None
                else:
                    updated_sheet, _ = _support.add_effect(updated_sheet, effect)
            updated_sheet = _support.validate_character_sheet(updated_sheet)
            current_actor = self.characters.get(actor_id)
            current_trap = dict(trap_state["traps"][trap_id])
            current_trap["triggered_actor_id"] = actor_id
            current_trap["range_confirmed"] = True
            current_trap["spatial_facts"] = _support.deepcopy(spatial_facts)
            if action == "trigger":
                current_trap["trigger_fact"] = dict(trigger_fact or {})
            if effect is not None:
                current_trap["condition_effect_id"] = effect["id"]
            trap_state["traps"][trap_id] = current_trap
            trap_state.setdefault("attempts", [])
            trap_state["attempts"] = [
                *list(trap_state["attempts"]),
                {
                    "trap_id": trap_id,
                    "source_ref": exact_source,
                    "actor_id": actor_id,
                    "action": action,
                    **({"disable_check": disable_check} if disable_check is not None else {}),
                    "range_confirmed": True,
                    "spatial_facts": _support.deepcopy(spatial_facts),
                    "piercing": {key: value for key, value in piercing.items() if key != "sheet"},
                    "poison": poison,
                    "condition_effect_id": effect["id"] if effect is not None else None,
                    "condition_suppressed": effect_suppressed,
                    "campaign_revision": campaign.revision + 1,
                },
            ][-100:]
            next_state["trap_state"] = trap_state
            if encounter.get("active"):
                poison_damage = dict(poison.get("damage") or {})
                for damage in (
                    piercing,
                    poison_damage,
                ):
                    if damage:
                        self.add_concentration_window(
                            encounter,
                            actor_id,
                            damage.get("concentration"),
                            next_revision=campaign.revision + 1,
                        )
                self.sync_combatant_conditions(encounter, actor_id, updated_sheet)
                _support.reconcile_readied_spells(encounter, actor_id, updated_sheet)
                next_state["combat"] = encounter
            character_update = _support.CharacterStateUpdate(
                character_id=actor_id,
                sheet=updated_sheet,
                notes=_support.validate_character_notes(current_actor.notes),
                expected_revision=current_actor.revision,
            )
            receipt = stream.receipt() if stream.draw_count else None
            response = {
                "status": "committed",
                "trap_id": trap_id,
                "action": action,
                **({"disable_check": disable_check} if disable_check is not None else {}),
                "trap": current_trap,
                "target_id": actor_id,
                "spatial_facts": _support.deepcopy(spatial_facts),
                "piercing": {key: value for key, value in piercing.items() if key != "sheet"},
                "poison": poison,
                "condition_effect": effect,
                "condition_suppressed": effect_suppressed,
            }
            if receipt is not None:
                response["random_stream_receipt"] = receipt
            receipts = list(poison.get("rule_receipts") or [])
            return self.commit_campaign_state(
                campaign,
                next_state,
                operation="trap.state.transition",
                principal_id=principal_id,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=replay_payload,
                response_fields=response,
                character_updates=[character_update],
                rule_receipts=receipts,
                expected_campaign_revision=campaign.revision,
            )

        if profile_id == "srd5.1.collapsing_roof" and action in {"trigger", "disable"}:
            if (
                not isinstance(target_ids, list)
                or len(target_ids) > 20
                or any(not isinstance(item, str) or not item.strip() for item in target_ids)
                or len(set(target_ids)) != len(target_ids)
            ):
                raise _support.CombatEngineError(
                    "Collapsing Roof requires distinct explicitly confirmed area targets"
                )
            disable_check = None
            if action == "disable":
                disable_actor = self.combat_actor_snapshot(actor_id)
                disable_spec = dict(normalized_profile.get("disable") or {})
                tool_proficiencies = {
                    str(item).casefold().replace("'", "").replace("_", " ").strip()
                    for item in dict(
                        dict(disable_actor["sheet"].get("traits") or {}).get("proficiencies") or {}
                    ).get("tools", [])
                }
                tool_alternative = method == "edged_tool"
                has_tool_proficiency = bool({"thieves tools", "thieves tool"} & tool_proficiencies)
                check_context = self.effective_rule_context(
                    campaign_id,
                    branch_id=branch_id,
                    facts={
                        "kind": "check",
                        "actor_id": actor_id,
                        "ability": disable_spec["ability"],
                        "dc": disable_spec["dc"],
                        "tool": disable_spec["tool"],
                        "trap_id": trap_id,
                        "trap_source_ref": exact_source,
                        "trap_action": "disable",
                        "tool_alternative": method,
                    },
                )
                disable_check = _support.resolve_actor_check(
                    disable_actor,
                    kind="check",
                    ability=disable_spec["ability"],
                    dc=disable_spec["dc"],
                    proficient=has_tool_proficiency and not tool_alternative,
                    disadvantage=tool_alternative,
                    encounter=encounter or None,
                    ruleset=ruleset,
                    rules=check_context,
                    rng=stream,
                )
                disable_check = {
                    **disable_check,
                    "ability": disable_spec["ability"],
                    "tool": disable_spec["tool"],
                    "method": method,
                    "tool_proficient": has_tool_proficiency and not tool_alternative,
                }
                if disable_check.get("success") is True:
                    next_state = _support.deepcopy(campaign.state)
                    trap_state = transition_trap_state(
                        dict(next_state.get("trap_state") or {}),
                        source_ref=exact_source,
                        trap_id=trap_id,
                        action="disable",
                        success=True,
                    )
                    current_trap = dict(trap_state["traps"][trap_id])
                    trap_state.setdefault("attempts", [])
                    trap_state["attempts"] = [
                        *list(trap_state["attempts"]),
                        {
                            "trap_id": trap_id,
                            "source_ref": exact_source,
                            "actor_id": actor_id,
                            "action": action,
                            "method": method,
                            "check": disable_check,
                            "campaign_revision": campaign.revision + 1,
                        },
                    ][-100:]
                    next_state["trap_state"] = trap_state
                    receipt = stream.receipt() if stream.draw_count else None
                    response = {
                        "status": "committed",
                        "trap_id": trap_id,
                        "action": action,
                        "trap": current_trap,
                        "disable_check": disable_check,
                        "check": disable_check,
                    }
                    if receipt is not None:
                        response["random_stream_receipt"] = receipt
                    return self.commit_campaign_state(
                        campaign,
                        next_state,
                        operation="trap.state.transition",
                        principal_id=principal_id,
                        branch_id=branch_id,
                        idempotency_key=idempotency_key,
                        scope=scope,
                        payload=replay_payload,
                        response_fields=response,
                        rule_receipts=list(disable_check.get("rule_receipts", [])),
                        expected_campaign_revision=campaign.revision,
                    )
            target_snapshots = {}
            for target_id in target_ids:
                self.require_campaign_actor(campaign_id, target_id)
                if encounter.get("active"):
                    self.require_encounter_combatant(encounter, target_id, role="trap target")
                target_snapshots[target_id] = self.combat_actor_snapshot(target_id)
            next_state = _support.deepcopy(campaign.state)
            trap_state = dict(next_state.get("trap_state") or {})
            if action == "disable":
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="disable",
                    success=False,
                )
            else:
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="trigger",
                )
            trap_state = transition_trap_state(
                trap_state,
                source_ref=exact_source,
                trap_id=trap_id,
                action="settle",
            )
            trigger = normalized_profile["trigger"]
            save_context = self.effective_rule_context(
                campaign_id,
                branch_id=branch_id,
                facts={
                    "kind": "save_damage",
                    "actor_ids": list(target_ids),
                    "ability": trigger["save_ability"],
                    "dc": trigger["save_dc"],
                    "trap_id": trap_id,
                    "trap_source_ref": exact_source,
                    "area": trigger["area"],
                    "area_confirmed": True,
                    "failed_disable_check": disable_check,
                },
            )
            settled = (
                _support.resolve_save_damage_to_sheets(
                    list(target_snapshots.values()),
                    save_ability=trigger["save_ability"],
                    save_dc=trigger["save_dc"],
                    damage_expression=trigger["damage_expression"],
                    damage_type=trigger["damage_type"],
                    half_on_success=trigger["half_on_success"],
                    source=f"trap:{exact_source}:{trap_id}:collapse",
                    encounter=encounter or None,
                    ruleset=ruleset,
                    rules=save_context,
                    rng=stream,
                )
                if target_snapshots
                else {"sheets": {}, "result": {"targets": [], "damage_roll": None}}
            )
            current_trap = dict(trap_state["traps"][trap_id])
            terrain_effect = {
                "kind": "difficult_terrain",
                "effect": "rubble",
                "area": trigger["area"],
                "source_ref": exact_source,
                "trap_id": trap_id,
                "active": True,
            }
            current_trap["area_confirmed"] = True
            current_trap["affected_actor_ids"] = list(target_ids)
            current_trap["spatial_facts"] = _support.deepcopy(spatial_facts)
            if action == "trigger":
                current_trap["trigger_fact"] = dict(trigger_fact or {})
            current_trap["terrain_effects"] = [terrain_effect]
            trap_state["traps"][trap_id] = current_trap
            character_updates = []
            target_results = settled["result"]["targets"]
            receipts = [
                receipt for target in target_results for receipt in target.get("rule_receipts", [])
            ]
            for target_id in target_ids:
                updated_sheet = _support.validate_character_sheet(settled["sheets"][target_id])
                current_actor = self.characters.get(target_id)
                character_updates.append(
                    _support.CharacterStateUpdate(
                        character_id=target_id,
                        sheet=updated_sheet,
                        notes=_support.validate_character_notes(current_actor.notes),
                        expected_revision=current_actor.revision,
                    )
                )
                if encounter.get("active"):
                    target_result = next(
                        item for item in target_results if item["target_id"] == target_id
                    )
                    damage = dict(target_result.get("damage") or {})
                    if damage:
                        self.add_concentration_window(
                            encounter,
                            target_id,
                            damage.get("concentration"),
                            next_revision=campaign.revision + 1,
                        )
                    self.sync_combatant_conditions(encounter, target_id, updated_sheet)
                    _support.reconcile_readied_spells(encounter, target_id, updated_sheet)
            if encounter.get("active"):
                next_state["combat"] = encounter
            trap_state.setdefault("attempts", [])
            trap_state["attempts"] = [
                *list(trap_state["attempts"]),
                {
                    "trap_id": trap_id,
                    "source_ref": exact_source,
                    "actor_id": actor_id,
                    "action": action,
                    **({"disable_check": disable_check} if disable_check is not None else {}),
                    "area_confirmed": True,
                    "target_ids": list(target_ids),
                    "spatial_facts": _support.deepcopy(spatial_facts),
                    "save_damage": settled["result"],
                    "campaign_revision": campaign.revision + 1,
                },
            ][-100:]
            next_state["trap_state"] = trap_state
            receipt = stream.receipt() if stream.draw_count else None
            response = {
                "status": "committed",
                "trap_id": trap_id,
                "action": action,
                "trap": current_trap,
                **({"disable_check": disable_check} if disable_check is not None else {}),
                **({"check": disable_check} if disable_check is not None else {}),
                "affected_actor_ids": list(target_ids),
                "spatial_facts": _support.deepcopy(spatial_facts),
                "damage_roll": settled["result"]["damage_roll"],
                "targets": target_results,
                "terrain_effect": terrain_effect,
            }
            if receipt is not None:
                response["random_stream_receipt"] = receipt
            return self.commit_campaign_state(
                campaign,
                next_state,
                operation="trap.state.transition",
                principal_id=principal_id,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=replay_payload,
                response_fields=response,
                character_updates=character_updates,
                rule_receipts=receipts
                + (list(disable_check.get("rule_receipts", [])) if disable_check else []),
                expected_campaign_revision=campaign.revision,
            )

        if profile_id == "srd5.1.fire_breathing_statue" and action == "trigger":
            if (
                not isinstance(target_ids, list)
                or len(target_ids) > 20
                or any(not isinstance(item, str) or not item.strip() for item in target_ids)
                or len(set(target_ids)) != len(target_ids)
            ):
                raise _support.CombatEngineError(
                    "Fire-Breathing Statue requires distinct explicitly confirmed area targets"
                )
            target_snapshots = {}
            for target_id in target_ids:
                self.require_campaign_actor(campaign_id, target_id)
                if encounter.get("active"):
                    self.require_encounter_combatant(encounter, target_id, role="trap target")
                target_snapshots[target_id] = self.combat_actor_snapshot(target_id)
            next_state = _support.deepcopy(campaign.state)
            trap_state = dict(next_state.get("trap_state") or {})
            trap_state = transition_trap_state(
                trap_state,
                source_ref=exact_source,
                trap_id=trap_id,
                action="trigger",
            )
            trap_state = transition_trap_state(
                trap_state,
                source_ref=exact_source,
                trap_id=trap_id,
                action="settle",
            )
            trigger = normalized_profile["trigger"]
            save_context = self.effective_rule_context(
                campaign_id,
                branch_id=branch_id,
                facts={
                    "kind": "save_damage",
                    "actor_ids": list(target_ids),
                    "ability": trigger["save_ability"],
                    "dc": trigger["save_dc"],
                    "trap_id": trap_id,
                    "trap_source_ref": exact_source,
                    "area": trigger["area"],
                    "area_confirmed": True,
                },
            )
            settled = (
                _support.resolve_save_damage_to_sheets(
                    list(target_snapshots.values()),
                    save_ability=trigger["save_ability"],
                    save_dc=trigger["save_dc"],
                    damage_expression=trigger["damage_expression"],
                    damage_type=trigger["damage_type"],
                    half_on_success=trigger["half_on_success"],
                    source=f"trap:{exact_source}:{trap_id}:fire-breathing-statue",
                    encounter=encounter or None,
                    ruleset=ruleset,
                    rules=save_context,
                    rng=stream,
                )
                if target_snapshots
                else {"sheets": {}, "result": {"targets": [], "damage_roll": None}}
            )
            current_trap = dict(trap_state["traps"][trap_id])
            current_trap["area_confirmed"] = True
            current_trap["affected_actor_ids"] = list(target_ids)
            current_trap["spatial_facts"] = _support.deepcopy(spatial_facts)
            current_trap["trigger_fact"] = dict(trigger_fact or {})
            current_trap["area_effect"] = {
                "kind": "fire_cone",
                "area": trigger["area"],
                "source_ref": exact_source,
                "trap_id": trap_id,
                "active": False,
            }
            trap_state["traps"][trap_id] = current_trap
            character_updates = []
            target_results = settled["result"]["targets"]
            receipts = [
                receipt for target in target_results for receipt in target.get("rule_receipts", [])
            ]
            for target_id in target_ids:
                updated_sheet = _support.validate_character_sheet(settled["sheets"][target_id])
                current_actor = self.characters.get(target_id)
                character_updates.append(
                    _support.CharacterStateUpdate(
                        character_id=target_id,
                        sheet=updated_sheet,
                        notes=_support.validate_character_notes(current_actor.notes),
                        expected_revision=current_actor.revision,
                    )
                )
                if encounter.get("active"):
                    target_result = next(
                        item for item in target_results if item["target_id"] == target_id
                    )
                    damage = dict(target_result.get("damage") or {})
                    if damage:
                        self.add_concentration_window(
                            encounter,
                            target_id,
                            damage.get("concentration"),
                            next_revision=campaign.revision + 1,
                        )
                    self.sync_combatant_conditions(encounter, target_id, updated_sheet)
                    _support.reconcile_readied_spells(encounter, target_id, updated_sheet)
            if encounter.get("active"):
                next_state["combat"] = encounter
            trap_state.setdefault("attempts", [])
            trap_state["attempts"] = [
                *list(trap_state["attempts"]),
                {
                    "trap_id": trap_id,
                    "source_ref": exact_source,
                    "actor_id": actor_id,
                    "action": action,
                    "area_confirmed": True,
                    "target_ids": list(target_ids),
                    "spatial_facts": _support.deepcopy(spatial_facts),
                    "save_damage": settled["result"],
                    "campaign_revision": campaign.revision + 1,
                },
            ][-100:]
            next_state["trap_state"] = trap_state
            receipt = stream.receipt() if stream.draw_count else None
            response = {
                "status": "committed",
                "trap_id": trap_id,
                "action": action,
                "trap": current_trap,
                "affected_actor_ids": list(target_ids),
                "spatial_facts": _support.deepcopy(spatial_facts),
                "damage_roll": settled["result"]["damage_roll"],
                "targets": target_results,
                "area_effect": current_trap["area_effect"],
            }
            if receipt is not None:
                response["random_stream_receipt"] = receipt
            return self.commit_campaign_state(
                campaign,
                next_state,
                operation="trap.state.transition",
                principal_id=principal_id,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=replay_payload,
                response_fields=response,
                character_updates=character_updates,
                rule_receipts=receipts,
                expected_campaign_revision=campaign.revision,
            )

        if profile_id == "srd5.1.fire_breathing_statue" and action == "disable":
            disable_spec = dict(normalized_profile.get("disable") or {})
            if disable_spec != {"ability": "arcana", "dc": 15} or method != "arcana":
                raise _support.CombatEngineError(
                    "Fire-Breathing Statue disable requires its source-defined Arcana check"
                )
            current_trap = dict(
                dict(dict(campaign.state or {}).get("trap_state") or {})
                .get("traps", {})
                .get(trap_id, {})
            )
            if current_trap.get("source_ref") not in (None, exact_source):
                raise _support.CombatEngineError(
                    "Fire-Breathing Statue state is bound to a different source"
                )
            if current_trap.get("profile_id") not in (None, profile_id):
                raise _support.CombatEngineError(
                    "Fire-Breathing Statue state is bound to a different source profile"
                )
            if current_trap.get("status", "armed") != "armed":
                raise _support.CombatEngineError(
                    "only an armed Fire-Breathing Statue can be disabled"
                )
            actor = self.combat_actor_snapshot(actor_id)
            check_context = self.effective_rule_context(
                campaign_id,
                branch_id=branch_id,
                facts={
                    "kind": "check",
                    "actor_id": actor_id,
                    "ability": "arcana",
                    "dc": 15,
                    "trap_id": trap_id,
                    "trap_source_ref": exact_source,
                    "trap_action": "disable",
                },
            )
            disable_check = _support.resolve_actor_check(
                actor,
                kind="check",
                ability="arcana",
                dc=15,
                encounter=encounter or None,
                ruleset=ruleset,
                rules=check_context,
                rng=stream,
            )
            disable_check = {**disable_check, "ability": "arcana", "method": "arcana"}
            next_state = _support.deepcopy(campaign.state)
            trap_state = dict(next_state.get("trap_state") or {})
            if disable_check.get("success") is True:
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="disable",
                    success=True,
                )
            current_trap = dict(dict(trap_state.get("traps") or {}).get(trap_id) or {})
            current_trap.setdefault("source_ref", exact_source)
            current_trap["profile_id"] = profile_id
            current_trap.setdefault("status", "armed")
            trap_state.setdefault("traps", {})[trap_id] = current_trap
            trap_state.setdefault("attempts", [])
            trap_state["attempts"] = [
                *list(trap_state["attempts"]),
                {
                    "trap_id": trap_id,
                    "source_ref": exact_source,
                    "actor_id": actor_id,
                    "action": action,
                    "method": method,
                    "check": disable_check,
                    "campaign_revision": campaign.revision + 1,
                },
            ][-100:]
            next_state["trap_state"] = trap_state
            receipt = stream.receipt() if stream.draw_count else None
            response = {
                "status": "committed",
                "trap_id": trap_id,
                "action": action,
                "trap": current_trap,
                "disable_check": disable_check,
                "check": disable_check,
            }
            if receipt is not None:
                response["random_stream_receipt"] = receipt
            return self.commit_campaign_state(
                campaign,
                next_state,
                operation="trap.state.transition",
                principal_id=principal_id,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=replay_payload,
                response_fields=response,
                rule_receipts=list(disable_check.get("rule_receipts", [])),
                expected_campaign_revision=campaign.revision,
            )

        if profile_id in _SOURCE_PIT_PROFILES and action == "trigger":
            depth_ft = validate_source_pit_depth(normalized_profile, trap_depth_ft)
            if (
                not isinstance(target_ids, list)
                or not target_ids
                or len(target_ids) > 20
                or any(not isinstance(item, str) or not item.strip() for item in target_ids)
                or len(set(target_ids)) != len(target_ids)
            ):
                raise _support.CombatEngineError(
                    "pit trigger requires 1 to 20 distinct explicitly confirmed targets"
                )
            target_snapshots = {}
            for target_id in target_ids:
                self.require_campaign_actor(campaign_id, target_id)
                if encounter.get("active"):
                    self.require_encounter_combatant(encounter, target_id, role="trap target")
                target_snapshots[target_id] = self.combat_actor_snapshot(target_id)
            next_state = _support.deepcopy(campaign.state)
            trap_state = dict(next_state.get("trap_state") or {})
            is_locking_pit = profile_id in _SOURCE_LOCKING_PIT_PROFILES
            trap_state = transition_trap_state(
                trap_state,
                source_ref=exact_source,
                trap_id=trap_id,
                action="trigger",
                contained_actor_ids=target_ids if is_locking_pit else None,
            )
            trap_state["traps"][trap_id]["trigger_fact"] = dict(trigger_fact or {})
            if not is_locking_pit:
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="settle",
                )
            trigger = normalized_profile["trigger"]
            target_results: list[dict[str, Any]] = []
            updated_sheets: dict[str, dict[str, Any]] = {}
            receipts: list[dict[str, Any]] = []
            for target_id in target_ids:
                snapshot = target_snapshots[target_id]
                fall = resolve_fall_to_sheet(
                    _support.deepcopy(snapshot["sheet"]),
                    distance_ft=depth_ft,
                    source=f"trap:{exact_source}:{trap_id}:fall",
                    ruleset=ruleset,
                    rng=stream,
                )
                sheet = fall["sheet"]
                entry: dict[str, Any] = {
                    "target_id": target_id,
                    "fall": {key: value for key, value in fall.items() if key != "sheet"},
                }
                spike_expression = trigger.get("spike_damage_expression")
                if spike_expression:
                    spike_roll = roll(spike_expression, rng=stream)
                    spike = _support.apply_damage_to_sheet(
                        sheet,
                        amount=spike_roll.total,
                        damage_type=trigger["spike_damage_type"],
                        source=f"trap:{exact_source}:{trap_id}:spikes",
                        ruleset=ruleset,
                    )
                    sheet = spike["sheet"]
                    entry["spikes"] = {
                        "expression": spike_expression,
                        "rolls": list(spike_roll.rolls),
                        **{key: value for key, value in spike.items() if key != "sheet"},
                    }
                poison_expression = trigger.get("poison_damage_expression")
                if poison_expression:
                    poison_context = self.effective_rule_context(
                        campaign_id,
                        branch_id=branch_id,
                        facts={
                            "kind": "save_damage",
                            "actor_ids": [target_id],
                            "ability": trigger["poison_save_ability"],
                            "dc": trigger["poison_save_dc"],
                            "trap_id": trap_id,
                            "trap_source_ref": exact_source,
                            "pit_depth_ft": depth_ft,
                        },
                    )
                    poison_target = {
                        **snapshot,
                        "sheet": sheet,
                        "derived": _support.derive_domain_character_sheet(sheet),
                    }
                    settled_poison = _support.resolve_save_damage_to_sheets(
                        [poison_target],
                        save_ability=trigger["poison_save_ability"],
                        save_dc=trigger["poison_save_dc"],
                        damage_expression=poison_expression,
                        damage_type=trigger["poison_damage_type"],
                        half_on_success=trigger["poison_half_on_success"],
                        source=f"trap:{exact_source}:{trap_id}:spike-poison",
                        encounter=encounter or None,
                        ruleset=ruleset,
                        rules=poison_context,
                        rng=stream,
                    )
                    sheet = settled_poison["sheets"][target_id]
                    poison_result = dict(settled_poison["result"]["targets"][0])
                    entry["poison"] = poison_result
                    receipts.extend(poison_result.get("rule_receipts", []))
                updated_sheets[target_id] = _support.validate_character_sheet(sheet)
                target_results.append(entry)
            current_trap = dict(trap_state["traps"][trap_id])
            current_trap["area_confirmed"] = True
            current_trap["trap_depth_ft"] = depth_ft
            current_trap["affected_actor_ids"] = list(target_ids)
            current_trap["spatial_facts"] = _support.deepcopy(spatial_facts)
            if is_locking_pit:
                current_trap["contained_actor_ids"] = list(target_ids)
                current_trap["profile_id"] = profile_id
                current_trap["scene_id"] = scene_id
            trap_state["traps"][trap_id] = current_trap
            trap_state.setdefault("attempts", [])
            trap_state["attempts"] = [
                *list(trap_state["attempts"]),
                {
                    "trap_id": trap_id,
                    "source_ref": exact_source,
                    "actor_id": actor_id,
                    "action": action,
                    "area_confirmed": True,
                    "trap_depth_ft": depth_ft,
                    "target_ids": list(target_ids),
                    "spatial_facts": _support.deepcopy(spatial_facts),
                    "targets": target_results,
                    "campaign_revision": campaign.revision + 1,
                },
            ][-100:]
            next_state["trap_state"] = trap_state
            character_updates = []
            for target_id in target_ids:
                current_actor = self.characters.get(target_id)
                updated_sheet = updated_sheets[target_id]
                character_updates.append(
                    _support.CharacterStateUpdate(
                        character_id=target_id,
                        sheet=updated_sheet,
                        notes=_support.validate_character_notes(current_actor.notes),
                        expected_revision=current_actor.revision,
                    )
                )
                if encounter.get("active"):
                    target_result = next(
                        item for item in target_results if item["target_id"] == target_id
                    )
                    for damage in (
                        target_result["fall"].get("damage"),
                        target_result.get("spikes", {}).get("damage"),
                        target_result.get("poison", {}).get("damage"),
                    ):
                        if damage:
                            self.add_concentration_window(
                                encounter,
                                target_id,
                                damage.get("concentration"),
                                next_revision=campaign.revision + 1,
                            )
                    self.sync_combatant_conditions(encounter, target_id, updated_sheet)
                    _support.reconcile_readied_spells(encounter, target_id, updated_sheet)
            if encounter.get("active"):
                next_state["combat"] = encounter
            receipt = stream.receipt() if stream.draw_count else None
            response = {
                "status": "committed",
                "trap_id": trap_id,
                "action": action,
                "trap": current_trap,
                "affected_actor_ids": list(target_ids),
                "trap_depth_ft": depth_ft,
                "targets": target_results,
                "spatial_facts": _support.deepcopy(spatial_facts),
            }
            if receipt is not None:
                response["random_stream_receipt"] = receipt
            return self.commit_campaign_state(
                campaign,
                next_state,
                operation="trap.state.transition",
                principal_id=principal_id,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=replay_payload,
                response_fields=response,
                character_updates=character_updates,
                rule_receipts=receipts,
                expected_campaign_revision=campaign.revision,
            )

        if profile_id != "srd5.1.falling_net" and profile_id not in _SOURCE_LOCKING_PIT_PROFILES:
            raise _support.CombatEngineError(
                "this source-bound trap effect has no complete runtime settlement"
            )
        self.require_campaign_actor(campaign_id, actor_id)
        if encounter.get("active"):
            self.require_encounter_combatant(encounter, actor_id, role="trap target")
        actor = self.combat_actor_snapshot(actor_id)
        next_state = _support.deepcopy(campaign.state)
        trap_state = dict(next_state.get("trap_state") or {})
        current_trap = dict(dict(trap_state.get("traps") or {}).get(trap_id) or {})
        updated_sheets: dict[str, dict[str, Any]] = {}
        if action == "disable" and profile_id in _SOURCE_LOCKING_PIT_PROFILES:
            try:
                validated_scene_facts = validate_locking_pit_disable_scene_facts(
                    normalized_profile,
                    scene_facts,
                    scene_id=scene_id,
                    trap_id=trap_id,
                    actor_id=actor_id,
                )
                validate_locking_pit_disable_state(
                    trap_state,
                    profile_id=profile_id,
                    source_ref=exact_source,
                    scene_id=scene_id,
                    trap_id=trap_id,
                    actor_id=actor_id,
                )
            except ValueError as exc:
                raise _support.CombatEngineError(str(exc)) from exc
            disable_spec = normalized_profile["disable"]
            tool_proficient = _has_thieves_tools_proficiency(actor["sheet"])
            check_context = self.effective_rule_context(
                campaign_id,
                branch_id=branch_id,
                facts={
                    "kind": "check",
                    "actor_id": actor_id,
                    "ability": disable_spec["ability"],
                    "dc": disable_spec["dc"],
                    "tool": disable_spec["tool"],
                    "trap_id": trap_id,
                    "trap_source_ref": exact_source,
                    "trap_action": "disable",
                    "scene_id": scene_id,
                    "scene_facts": validated_scene_facts,
                },
            )
            disable_check = _support.resolve_actor_check(
                actor,
                kind="check",
                ability=disable_spec["ability"],
                dc=disable_spec["dc"],
                proficient=tool_proficient,
                encounter=encounter or None,
                ruleset=ruleset,
                rules=check_context,
                rng=stream,
            )
            disable_check = {
                **disable_check,
                "ability": disable_spec["ability"],
                "dc": disable_spec["dc"],
                "tool": disable_spec["tool"],
                "method": method,
                "tool_proficient": tool_proficient,
            }
            try:
                trap_state = apply_locking_pit_spring_disable(
                    trap_state,
                    profile_id=profile_id,
                    source_ref=exact_source,
                    scene_id=scene_id,
                    trap_id=trap_id,
                    actor_id=actor_id,
                    success=disable_check.get("success") is True,
                )
            except ValueError as exc:
                raise _support.CombatEngineError(str(exc)) from exc
            current_trap = dict(trap_state["traps"][trap_id])
            trap_state.setdefault("attempts", [])
            trap_state["attempts"] = [
                *list(trap_state["attempts"]),
                {
                    "trap_id": trap_id,
                    "source_ref": exact_source,
                    "actor_id": actor_id,
                    "action": action,
                    "method": method,
                    "scene_facts": validated_scene_facts,
                    "check": disable_check,
                    "campaign_revision": campaign.revision + 1,
                },
            ][-100:]
            next_state["trap_state"] = trap_state
            receipt = stream.receipt() if stream.draw_count else None
            response = {
                "status": "committed",
                "trap_id": trap_id,
                "action": action,
                "trap": current_trap,
                "disable_check": disable_check,
                "check": disable_check,
                "scene_facts": validated_scene_facts,
            }
            if receipt is not None:
                response["random_stream_receipt"] = receipt
            return self.commit_campaign_state(
                campaign,
                next_state,
                operation="trap.state.transition",
                principal_id=principal_id,
                branch_id=branch_id,
                idempotency_key=idempotency_key,
                scope=scope,
                payload=replay_payload,
                response_fields=response,
                rule_receipts=list(disable_check.get("rule_receipts") or []),
                expected_campaign_revision=campaign.revision,
            )
        if action in {"trigger", "disable"}:
            net_target_actor_ids = [actor_id]
            net_target_snapshots = {actor_id: actor}
            if profile_id == "srd5.1.falling_net":
                if target_ids is not None:
                    empty_net_release = not target_ids and action in {"trigger", "disable"}
                    if (
                        not isinstance(target_ids, list)
                        or (not target_ids and not empty_net_release)
                        or len(target_ids) > 20
                        or any(not isinstance(item, str) or not item.strip() for item in target_ids)
                        or len(set(target_ids)) != len(target_ids)
                    ):
                        raise _support.CombatEngineError(
                            "Falling Net requires distinct confirmed area targets"
                        )
                    net_target_actor_ids = list(target_ids)
                    net_target_snapshots = {}
                    for target_id in net_target_actor_ids:
                        self.require_campaign_actor(campaign_id, target_id)
                        if encounter.get("active"):
                            self.require_encounter_combatant(
                                encounter, target_id, role="Falling Net target"
                            )
                        net_target_snapshots[target_id] = self.combat_actor_snapshot(target_id)
            disable_check = None
            if action == "disable":
                disable_spec = normalized_profile.get("disable")
                if not isinstance(disable_spec, dict):
                    raise _support.CombatEngineError(
                        "falling net disable check is missing from its source profile"
                    )
                tool_proficiencies = {
                    str(item).casefold().replace("'", "").replace("_", " ").strip()
                    for item in dict(
                        dict(actor["sheet"].get("traits") or {}).get("proficiencies") or {}
                    ).get("tools", [])
                }
                has_tool_proficiency = bool({"thieves tools", "thieves tool"} & tool_proficiencies)
                tool_alternative = method == "edged_tool"
                check_context = self.effective_rule_context(
                    campaign_id,
                    branch_id=branch_id,
                    facts={
                        "kind": "check",
                        "actor_id": actor_id,
                        "ability": disable_spec["ability"],
                        "dc": disable_spec["dc"],
                        "tool": disable_spec["tool"],
                        "trap_id": trap_id,
                        "trap_source_ref": exact_source,
                        "trap_action": "disable",
                        "tool_alternative": method,
                    },
                )
                disable_check = _support.resolve_actor_check(
                    actor,
                    kind="check",
                    ability=disable_spec["ability"],
                    dc=disable_spec["dc"],
                    proficient=has_tool_proficiency and not tool_alternative,
                    disadvantage=tool_alternative,
                    encounter=encounter or None,
                    ruleset=ruleset,
                    rules=check_context,
                    rng=stream,
                )
                disable_check = {
                    **disable_check,
                    "ability": disable_spec["ability"],
                    "tool": disable_spec["tool"],
                    "method": method,
                    "tool_proficient": has_tool_proficiency and not tool_alternative,
                }
                if disable_check.get("success") is True:
                    trap_state = transition_trap_state(
                        trap_state,
                        source_ref=exact_source,
                        trap_id=trap_id,
                        action="disable",
                        success=True,
                    )
                    updated_sheet = _support.validate_character_sheet(
                        _support.deepcopy(actor["sheet"])
                    )
                    current_actor = self.characters.get(actor_id)
                    current_trap = dict(trap_state["traps"][trap_id])
                    trap_state.setdefault("attempts", [])
                    trap_state["attempts"] = [
                        *list(trap_state["attempts"]),
                        {
                            "trap_id": trap_id,
                            "source_ref": exact_source,
                            "actor_id": actor_id,
                            "action": action,
                            "method": method,
                            "check": disable_check,
                            "area_confirmed": True,
                            "campaign_revision": campaign.revision + 1,
                        },
                    ][-100:]
                    next_state["trap_state"] = trap_state
                    character_update = _support.CharacterStateUpdate(
                        character_id=actor_id,
                        sheet=updated_sheet,
                        notes=_support.validate_character_notes(current_actor.notes),
                        expected_revision=current_actor.revision,
                    )
                    receipt = stream.receipt() if stream.draw_count else None
                    response = {
                        "status": "committed",
                        "trap_id": trap_id,
                        "action": action,
                        "trap": current_trap,
                        "disable_check": disable_check,
                        "check": disable_check,
                    }
                    if receipt is not None:
                        response["random_stream_receipt"] = receipt
                    return self.commit_campaign_state(
                        campaign,
                        next_state,
                        operation="trap.state.transition",
                        principal_id=principal_id,
                        branch_id=branch_id,
                        idempotency_key=idempotency_key,
                        scope=scope,
                        payload=replay_payload,
                        response_fields=response,
                        character_updates=[character_update],
                        expected_campaign_revision=campaign.revision,
                    )
                # A failed source-defined disable transitions to trigger, then
                # the same atomic command resolves every confirmed area target.
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="disable",
                    success=False,
                )
            else:
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="trigger",
                )
            check_spec = normalized_profile["trigger"]
            target_results: list[dict[str, Any]] = []
            checks: list[dict[str, Any]] = []
            current_trap = dict(trap_state["traps"][trap_id])
            restrained_ids = list(current_trap.get("restrained_actor_ids") or [])
            trap_added_ids = list(current_trap.get("trap_added_restrained_actor_ids") or [])
            for target_id in net_target_actor_ids:
                target_snapshot = net_target_snapshots[target_id]
                check_context = self.effective_rule_context(
                    campaign_id,
                    branch_id=branch_id,
                    facts={
                        "kind": "save",
                        "actor_id": target_id,
                        "ability": check_spec["save_ability"],
                        "dc": check_spec["save_dc"],
                        "trap_id": trap_id,
                        "trap_source_ref": exact_source,
                        "target_in_source_defined_area": True,
                    },
                )
                check = _support.resolve_actor_check(
                    target_snapshot,
                    kind="save",
                    ability=check_spec["save_ability"],
                    dc=check_spec["save_dc"],
                    encounter=encounter or None,
                    ruleset=ruleset,
                    rules=check_context,
                    rng=stream,
                )
                check = {**check, "ability": check_spec["save_ability"]}
                checks.append(check)
                updated_sheet, restrained_added = _apply_falling_net_effects(
                    target_snapshot["sheet"],
                    strength_save_succeeded=check.get("success") is True,
                )
                updated_sheets[target_id] = updated_sheet
                restrained_ids.append(target_id)
                if restrained_added:
                    trap_added_ids.append(target_id)
                target_results.append(
                    {
                        "target_id": target_id,
                        "check": check,
                        "restrained": "restrained" in set(updated_sheet.get("conditions") or []),
                        "prone": "prone" in set(updated_sheet.get("conditions") or []),
                    }
                )
            check = checks[0] if checks else None
            current_trap["restrained_actor_ids"] = list(dict.fromkeys(restrained_ids))
            current_trap["trap_added_restrained_actor_ids"] = list(dict.fromkeys(trap_added_ids))
            current_trap["area_confirmed"] = True
            current_trap["profile_id"] = profile_id
            if spatial_facts is not None:
                current_trap["spatial_facts"] = _support.deepcopy(spatial_facts)
            if profile_id == "srd5.1.falling_net":
                current_trap["object_id"] = trap_id
                current_trap["scene_id"] = scene_id
            trap_state["traps"][trap_id] = current_trap
            result: dict[str, Any] = {
                "check": check,
                "checks": checks,
                "targets": target_results,
                "affected_actor_ids": list(net_target_actor_ids),
                "action": action,
                **(
                    {"spatial_facts": _support.deepcopy(spatial_facts)}
                    if spatial_facts is not None
                    else {}
                ),
                **({"disable_check": disable_check} if disable_check is not None else {}),
            }
            receipts = [
                receipt
                for target_check in checks
                for receipt in target_check.get("rule_receipts") or []
            ]
            if disable_check is not None:
                receipts.extend(disable_check.get("rule_receipts") or [])
        elif action == "escape":
            locking_pit = profile_id in _SOURCE_LOCKING_PIT_PROFILES
            trapped_actor_ids = list(
                current_trap.get("contained_actor_ids" if locking_pit else "restrained_actor_ids")
                or []
            )
            if actor_id not in trapped_actor_ids:
                if locking_pit:
                    raise _support.CombatEngineError(
                        "actor is not contained by this source-bound locking pit"
                    )
                raise _support.CombatEngineError("actor is not restrained by this falling net")
            escape = (
                {
                    "ability": "strength",
                    "dc": normalized_profile["trigger"]["escape_strength_dc"],
                }
                if locking_pit
                else normalized_profile["trigger"]["escape_check"]
            )
            check_context = self.effective_rule_context(
                campaign_id,
                branch_id=branch_id,
                facts={
                    "kind": "check",
                    "actor_id": actor_id,
                    "ability": escape["ability"],
                    "dc": escape["dc"],
                    "trap_id": trap_id,
                    "trap_source_ref": exact_source,
                },
            )
            check = _support.resolve_actor_check(
                actor,
                kind="check",
                ability=escape["ability"],
                dc=escape["dc"],
                encounter=encounter or None,
                ruleset=ruleset,
                rules=check_context,
                rng=stream,
            )
            check = {**check, "ability": escape["ability"]}
            if check.get("success") is True:
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="escape",
                    actor_id=actor_id,
                )
            current_trap = dict(trap_state["traps"][trap_id])
            updated_sheet = _support.deepcopy(actor["sheet"])
            if check.get("success") is True and actor_id in list(
                current_trap.get("trap_added_restrained_actor_ids") or []
            ):
                _support.apply_condition_change(updated_sheet, condition_id="restrained", add=False)
                current_trap["trap_added_restrained_actor_ids"] = [
                    item
                    for item in current_trap["trap_added_restrained_actor_ids"]
                    if item != actor_id
                ]
                trap_state["traps"][trap_id] = current_trap
            updated_sheets[actor_id] = updated_sheet
            result = {
                "check": check,
                "action": action,
                **(
                    {"contained_actor_ids": list(current_trap.get("contained_actor_ids") or [])}
                    if locking_pit
                    else {}
                ),
            }
            receipts = list(check.get("rule_receipts") or [])
        elif action == "rescue":
            target_id = str(rescue_target_id or "")
            restrained = list(current_trap.get("restrained_actor_ids") or [])
            if (
                current_trap.get("profile_id") != "srd5.1.falling_net"
                or current_trap.get("status") != "triggered"
            ):
                raise _support.CombatEngineError("rescue requires this triggered Falling Net")
            if target_id not in restrained:
                raise _support.CombatEngineError(
                    "rescue target is not restrained by this Falling Net"
                )
            if target_id == actor_id or actor_id not in [
                item.get("actor_id") for item in encounter.get("combatants", [])
            ]:
                raise _support.CombatEngineError(
                    "rescue requires a distinct rescuer in the active encounter"
                )
            active_scene = self.modules.current_scene(campaign_id, scope_id="party")
            progress = (
                dict(active_scene.get("progress") or {}) if isinstance(active_scene, dict) else {}
            )
            scene_revision = (
                active_scene.get("state_version", progress.get("state_version"))
                if isinstance(active_scene, dict)
                else None
            )
            if (
                not isinstance(active_scene, dict)
                or str(active_scene.get("scene_id") or "") != scene_id
                or type(scene_revision) is not int
            ):
                raise _support.CombatEngineError(
                    "Falling Net rescue requires the active source scene and revision"
                )
            try:
                normalized_rescue_facts = validate_falling_net_rescue_facts(
                    normalized_profile,
                    rescue_facts,
                    scene_id=scene_id,
                    scene_revision=scene_revision,
                    trap_id=trap_id,
                    source_ref=exact_source,
                    campaign_revision=campaign.revision,
                    reviewed_by=principal_id,
                    rescuer_id=actor_id,
                    target_id=target_id,
                )
            except ValueError as exc:
                raise _support.CombatEngineError(str(exc)) from exc
            if (
                _support.current_combatant(encounter) is None
                or _support.current_combatant(encounter).get("actor_id") != actor_id
            ):
                raise _support.CombatEngineError(
                    "Falling Net rescue costs an action on the rescuer's active turn"
                )
            encounter = _support.resolve_common_action(
                encounter,
                actor_id_value=actor_id,
                action="use_object",
                payload={
                    "source": "falling_net_rescue",
                    "trap_id": trap_id,
                    "target_id": target_id,
                },
            )
            target_snapshot = self.combat_actor_snapshot(target_id)
            context = self.effective_rule_context(
                campaign_id,
                branch_id=branch_id,
                facts={
                    "kind": "check",
                    "actor_id": actor_id,
                    "ability": "strength",
                    "dc": 10,
                    "trap_id": trap_id,
                    "trap_source_ref": exact_source,
                },
            )
            check = _support.resolve_actor_check(
                actor,
                kind="check",
                ability="strength",
                dc=10,
                encounter=encounter,
                ruleset=ruleset,
                rules=context,
                rng=stream,
            )
            check = {**check, "ability": "strength"}
            if check.get("success") is True:
                trap_state = transition_trap_state(
                    trap_state,
                    source_ref=exact_source,
                    trap_id=trap_id,
                    action="escape",
                    actor_id=target_id,
                )
                current_trap = dict(trap_state["traps"][trap_id])
                if target_id in list(current_trap.get("trap_added_restrained_actor_ids") or []):
                    target_sheet = _support.deepcopy(target_snapshot["sheet"])
                    _support.apply_condition_change(
                        target_sheet, condition_id="restrained", add=False
                    )
                    updated_sheets[target_id] = target_sheet
                    current_trap["trap_added_restrained_actor_ids"] = [
                        item
                        for item in current_trap["trap_added_restrained_actor_ids"]
                        if item != target_id
                    ]
                    trap_state["traps"][trap_id] = current_trap
            result = {
                "check": check,
                "action": action,
                "rescuer_id": actor_id,
                "target_id": target_id,
                "action_cost": "action",
                "action_paid": True,
                "rescue_facts": normalized_rescue_facts,
            }
            receipts = list(check.get("rule_receipts") or [])

        character_updates = []
        for updated_actor_id, updated_sheet in updated_sheets.items():
            updated_sheet = _support.validate_character_sheet(updated_sheet)
            current_actor = self.characters.get(updated_actor_id)
            character_updates.append(
                _support.CharacterStateUpdate(
                    character_id=updated_actor_id,
                    sheet=updated_sheet,
                    notes=_support.validate_character_notes(current_actor.notes),
                    expected_revision=current_actor.revision,
                )
            )
            if encounter.get("active"):
                self.sync_combatant_conditions(encounter, updated_actor_id, updated_sheet)
                _support.reconcile_readied_spells(encounter, updated_actor_id, updated_sheet)
        if encounter.get("active"):
            next_state["combat"] = encounter
        trap_state.setdefault("attempts", [])
        trap_state["attempts"] = [
            *list(trap_state["attempts"]),
            {
                "trap_id": trap_id,
                "source_ref": exact_source,
                "actor_id": actor_id,
                "action": action,
                "check": check,
                **(
                    {"target_ids": list(updated_sheets)} if action in {"trigger", "disable"} else {}
                ),
                "area_confirmed": True if action in {"trigger", "disable"} else None,
                "method": method,
                **(
                    {"spatial_facts": _support.deepcopy(spatial_facts)}
                    if spatial_facts is not None
                    else {}
                ),
                "campaign_revision": campaign.revision + 1,
            },
        ][-100:]
        next_state["trap_state"] = trap_state
        response = {
            "status": "committed",
            "trap_id": trap_id,
            "action": action,
            "trap": trap_state["traps"][trap_id],
            **result,
        }
        receipt = stream.receipt() if stream.draw_count else None
        if receipt is not None:
            response["random_stream_receipt"] = receipt
        return self.commit_campaign_state(
            campaign,
            next_state,
            operation="trap.state.transition",
            principal_id=principal_id,
            branch_id=branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=replay_payload,
            response_fields=response,
            character_updates=character_updates,
            rule_receipts=receipts,
            expected_campaign_revision=campaign.revision,
        )
