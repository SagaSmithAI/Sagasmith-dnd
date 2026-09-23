"""Runtime-facing disease transition facade.

Campaign authority integration is composed by the application service layer;
these helpers keep lifecycle rules on the Domain side and accept only resolved
facts produced during that authoritative transaction.
"""

from dataclasses import asdict, dataclass
from typing import Any

from sagasmith_dnd.character_schema import derive_character_sheet
from sagasmith_dnd.conditions import effect_condition_additions
from sagasmith_dnd.diseases import (
    DISEASE_SOURCE_REF,
    advance_disease_clock,
    apply_sight_rot_ointment,
    cackle_carrier_immunity,
    cure_disease,
    disease_profile,
    infection_state,
    resolve_cackle_end_turn,
    resolve_cackle_spread,
    resolve_cackle_stress,
    resolve_long_rest,
)
from sagasmith_dnd.engine import roll

from .. import application_support as support

_DISEASE_EXPOSURES = {
    "cackle_fever": {"carrier_laughter"},
    "sewer_plague": {"carrier_bite", "contaminated_filth"},
    "sight_rot": {"tainted_water"},
}
_DISEASE_CURES = {
    "lesser_restoration": "bundled:srd2014/07_Spells/Spells_Each/Lesser_Restoration.md",
    "heal": "bundled:srd2014/07_Spells/Spells_Each/Heal.md",
    "lay_on_hands": "bundled:srd2014/02_Classes/Paladin.md#lay-on-hands",
    "raise_dead": "bundled:srd2014/07_Spells/Spells_Each/Raise_Dead.md",
}
_GNOME_SPECIES_IDS = {"gnome", "forest_gnome", "rock_gnome"}
EYEBRIGHT_FLOWER_NAME = "Eyebright flower"
EYEBRIGHT_OINTMENT_SOURCE_KEY = "dnd5e.srd2014.disease.sight_rot.eyebright_ointment"
EYEBRIGHT_OINTMENT_NAME = "Eyebright ointment"


@dataclass(frozen=True)
class EyebrightCraftingPlan:
    """Narrow internal extension to the shared campaign-hour transaction."""

    actor_id: str
    expected_actor_revision: int
    flower_item_id: str
    expected_elapsed_ticks: int

    def __post_init__(self) -> None:
        if not self.actor_id.strip() or not self.flower_item_id.strip():
            raise ValueError("Eyebright crafting plan requires actor and flower identities")
        if (
            isinstance(self.expected_actor_revision, bool)
            or not isinstance(self.expected_actor_revision, int)
            or self.expected_actor_revision < 0
        ):
            raise ValueError("Eyebright crafting actor revision must be a nonnegative integer")
        if (
            isinstance(self.expected_elapsed_ticks, bool)
            or not isinstance(self.expected_elapsed_ticks, int)
            or self.expected_elapsed_ticks < 0
        ):
            raise ValueError("Eyebright crafting elapsed ticks must be a nonnegative integer")

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def _disease_effect_state(effect: dict[str, Any]) -> dict[str, Any] | None:
    if (
        effect.get("kind") != "disease_state"
        or effect.get("source") != DISEASE_SOURCE_REF
        or not effect.get("active")
    ):
        return None
    state = dict(dict(effect.get("metadata") or {}).get("disease_state") or {})
    if state.get("source_ref") != DISEASE_SOURCE_REF or state.get("edition") != "2014":
        return None
    return state


def _actor_disease_state(
    sheet: dict[str, Any], disease_id: str
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    matches = []
    for effect in sheet.get("effects", []):
        state = _disease_effect_state(effect)
        if state is not None and state.get("active") and state.get("disease_id") == disease_id:
            matches.append((effect, state))
    if len(matches) > 1:
        raise support.CombatEngineError("actor has multiple active copies of the same disease")
    return matches[0] if matches else None


def _authoritative_disease_taxonomy(record: Any) -> tuple[str, str]:
    """Read normalized species/statblock type from the persisted actor card."""
    sheet = dict(record.sheet or {})
    species = str(dict(sheet.get("progression") or {}).get("species") or "")
    species_id = species.strip().casefold().replace(" ", "_").replace("-", "_")
    actor_kind = str(getattr(record, "character_type", "") or "").casefold()
    if actor_kind == "pc":
        if not species_id:
            raise support.NeedsRulingError(
                "disease eligibility needs the actor's normalized species",
                missing=("actor.species",),
                ruling_kind="source_or_scene_fact",
            )
        return "humanoid", species_id
    # The statblock importer stores its parsed creature type in this canonical
    # field. Only exact normalized 2014 creature types are accepted here.
    creature_type = species_id.split("(", 1)[0].strip()
    if actor_kind in {"npc", "monster"} and creature_type in {"humanoid", "beast"}:
        return creature_type, ""
    raise support.NeedsRulingError(
        "disease eligibility needs a normalized statblock creature type",
        missing=("actor.creature_type",),
        ruling_kind="source_or_scene_fact",
    )


class DiseasesService:
    """Narrow adapter for transaction owners integrating disease transitions."""

    @staticmethod
    def disease_infection_transition(
        disease: Any,
        *,
        actor_id: Any,
        elapsed_ticks: Any,
        save_succeeded: Any,
        incubation_roll: Any = None,
    ) -> dict[str, Any] | None:
        return infection_state(
            disease,
            actor_id=actor_id,
            elapsed_ticks=elapsed_ticks,
            save_succeeded=save_succeeded,
            incubation_roll=incubation_roll,
        )

    @staticmethod
    def disease_clock_transition(state: dict[str, Any], *, elapsed_ticks: Any) -> dict[str, Any]:
        return advance_disease_clock(state, elapsed_ticks=elapsed_ticks)

    @staticmethod
    def disease_cackle_stress_transition(
        state: dict[str, Any],
        *,
        trigger: Any,
        save_succeeded: Any,
        elapsed_ticks: Any,
        psychic_damage_roll: Any = None,
    ) -> dict[str, Any]:
        return resolve_cackle_stress(
            state,
            trigger=trigger,
            save_succeeded=save_succeeded,
            elapsed_ticks=elapsed_ticks,
            psychic_damage_roll=psychic_damage_roll,
        )

    @staticmethod
    def disease_cackle_end_turn_transition(
        state: dict[str, Any],
        *,
        save_succeeded: Any,
    ) -> dict[str, Any]:
        return resolve_cackle_end_turn(state, save_succeeded=save_succeeded)

    @staticmethod
    def disease_cackle_immunity_transition(
        *,
        target_id: Any,
        carrier_id: Any,
        elapsed_ticks: Any,
    ) -> dict[str, Any]:
        return cackle_carrier_immunity(
            target_id=target_id,
            carrier_id=carrier_id,
            elapsed_ticks=elapsed_ticks,
        )

    @staticmethod
    def disease_long_rest_transition(
        state: dict[str, Any],
        *,
        save_succeeded: bool | None,
        elapsed_ticks: Any,
        recovery_die: Any = None,
        madness_d100: Any = None,
    ) -> dict[str, Any]:
        return resolve_long_rest(
            state,
            save_succeeded=save_succeeded,
            elapsed_ticks=elapsed_ticks,
            recovery_die=recovery_die,
            madness_d100=madness_d100,
        )

    @staticmethod
    def disease_ointment_transition(state: dict[str, Any], *, doses: Any = 1) -> dict[str, Any]:
        return apply_sight_rot_ointment(state, doses=doses)

    @staticmethod
    def disease_cure_transition(state: dict[str, Any], *, disease_id: Any) -> dict[str, Any]:
        return cure_disease(state, disease_id=disease_id)

    def character_disease_eyebright_craft(
        self,
        campaign_id: str,
        actor_id: str,
        flower_item_id: str,
        *,
        principal_id: str = support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        expected_actor_revision: int | None = None,
        expected_elapsed_ticks: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Craft one dose through the existing atomic one-hour clock transaction."""
        self.access.require_campaign(campaign_id, principal_id, roles=support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        if expected_actor_revision is None or expected_elapsed_ticks is None:
            raise ValueError(
                "Eyebright crafting requires expected_actor_revision and expected_elapsed_ticks"
            )
        if not str(flower_item_id or "").strip():
            raise ValueError("Eyebright crafting requires an inventory flower_item_id")
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        plan = EyebrightCraftingPlan(
            actor_id=str(actor_id),
            expected_actor_revision=expected_actor_revision,
            flower_item_id=str(flower_item_id),
            expected_elapsed_ticks=expected_elapsed_ticks,
        )
        return self.campaign_advance_effects(
            campaign_id,
            "hour",
            1,
            principal_id,
            expected_revision,
            resolved_branch_id,
            idempotency_key,
            expected_elapsed_ticks,
            disease_eyebright_crafting=plan,
        )

    def character_disease_eyebright_apply(
        self,
        campaign_id: str,
        actor_id: str,
        disease_effect_id: str,
        ointment_item_id: str,
        *,
        principal_id: str = support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        expected_actor_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Consume one owned Eyebright dose and update one exact Sight Rot instance."""
        self.access.require_campaign(campaign_id, principal_id, roles=support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        if (
            isinstance(expected_actor_revision, bool)
            or not isinstance(expected_actor_revision, int)
            or expected_actor_revision < 0
        ):
            raise ValueError("expected_actor_revision is required for Eyebright application")
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        payload = {
            "actor_id": actor_id,
            "disease_effect_id": disease_effect_id,
            "ointment_item_id": ointment_item_id,
            "expected_actor_revision": expected_actor_revision,
            "branch_id": resolved_branch_id,
        }
        scope = (
            f"character-disease-eyebright-apply:{campaign_id}:"
            f"{resolved_branch_id}:{principal_id}"
        )
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                "campaign revision conflict: "
                f"expected {expected_revision}, found {campaign.revision}"
            )
        if self.campaign_rules_edition(campaign_id) != "2014":
            raise support.CombatEngineError("Eyebright disease treatment requires a 2014 campaign")
        actor = self.require_campaign_actor(campaign_id, actor_id)
        if actor.revision != expected_actor_revision:
            raise ValueError(
                "actor revision conflict: "
                f"expected {expected_actor_revision}, found {actor.revision}"
            )
        if str(actor.sheet.get("edition") or "") != "2014":
            raise support.CombatEngineError("Eyebright disease treatment requires a 2014 actor")
        sheet = support.deepcopy(actor.sheet)
        ointment = next(
            (
                item
                for item in sheet.get("inventory", {}).get("items", [])
                if str(item.get("id") or "") == str(ointment_item_id)
            ),
            None,
        )
        if (
            ointment is None
            or ointment.get("kind") != "consumable"
            or ointment.get("source_key") != EYEBRIGHT_OINTMENT_SOURCE_KEY
            or ointment.get("name") != EYEBRIGHT_OINTMENT_NAME
            or int(ointment.get("quantity", 0) or 0) < 1
        ):
            raise support.CombatEngineError(
                "ointment_item_id must identify an owned source-bound Eyebright dose"
            )
        effect = next(
            (
                item
                for item in sheet.get("effects", [])
                if str(item.get("id") or "") == str(disease_effect_id)
            ),
            None,
        )
        disease_state = _disease_effect_state(effect or {})
        if (
            disease_state is None
            or not disease_state.get("active")
            or disease_state.get("disease_id") != "sight_rot"
        ):
            raise support.CombatEngineError("selected disease effect is not active Sight Rot")
        try:
            transition = apply_sight_rot_ointment(disease_state, doses=1)
            sheet, consumed = support.remove_inventory_item(sheet, str(ointment_item_id), 1)
        except ValueError as error:
            raise support.CombatEngineError(str(error)) from error
        effect = next(
            item
            for item in sheet.get("effects", [])
            if str(item.get("id") or "") == str(disease_effect_id)
        )
        effect.setdefault("metadata", {})["disease_state"] = transition["state"]
        if not transition["state"].get("active"):
            effect["active"] = False
            effect["ended_reason"] = "cured_by_eyebright_ointment"
        sheet = support.validate_character_sheet(sheet)
        target_update = support.CharacterStateUpdate(
            character_id=actor.id,
            sheet=sheet,
            notes=support.validate_character_notes(actor.notes),
            expected_revision=actor.revision,
        )
        state = support.validate_party_state(support.deepcopy(campaign.state or {}))
        rules = self.effective_rule_context(
            campaign_id,
            branch_id=resolved_branch_id,
            facts={
                "actor_id": actor_id,
                "disease_id": "sight_rot",
                "disease_effect_id": disease_effect_id,
                "ointment_item_id": ointment_item_id,
                "dose_count": 1,
            },
        )
        receipt = {
            "mechanic_id": "dnd5e.core.gamemastering.disease.sight_rot.2014",
            "event": "character.disease.eyebright_apply",
            "operations": [
                {
                    "op": "inventory.consume",
                    "item_id": str(ointment_item_id),
                    "quantity": 1,
                }
            ],
            "citations": [{"source": DISEASE_SOURCE_REF, "edition": "2014"}],
            "ruleset_fingerprint": rules.fingerprint,
            "facts": {
                "actor_id": actor_id,
                "disease_effect_id": disease_effect_id,
                "ointment_item_id": ointment_item_id,
                "dose_count": 1,
                "total_doses_applied": transition["state"]["ointment_doses_applied"],
            },
        }
        result = {
            "status": "committed",
            "disease_id": "sight_rot",
            "disease_effect_id": disease_effect_id,
            "ointment_item_id": ointment_item_id,
            "consumed": consumed,
            "disease_state": transition["state"],
            "events": transition["events"],
        }
        resolution_id = f"resolution-{support.uuid4().hex}"
        state["resolution_log"] = [
            *list(state.get("resolution_log") or []),
            {
                "id": resolution_id,
                "thread_id": resolution_id,
                "event_sequence": 1,
                "type": "disease_eyebright_apply",
                "operation": "character.disease.eyebright_apply",
                "actor_id": actor_id,
                "audience": {
                    "scope": "actors",
                    "actor_refs": [actor_id],
                    "disclosure": "private",
                },
                "branch_id": resolved_branch_id,
                "campaign_revision": campaign.revision + 1,
                "result": result,
            },
        ][-100:]
        return self.commit_campaign_state(
            campaign,
            state,
            operation="character.disease.eyebright_apply",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "committed",
                "resolution_id": resolution_id,
                "result": result,
                "rule_receipts": [receipt],
            },
            character_updates=[target_update],
            rule_receipts=[receipt],
            expected_campaign_revision=expected_revision,
        )

    def character_disease_stress(
        self,
        campaign_id: str,
        actor_id: str,
        trigger: str,
        *,
        source_event_id: str | None = None,
        principal_id: str = support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        expected_actor_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Resolve one verifiable Cackle stress event through campaign RNG/CAS."""
        self.access.require_campaign(campaign_id, principal_id, roles=support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        if expected_actor_revision is None:
            raise ValueError("expected_actor_revision is required for disease stress")
        branch = self.require_current_branch(campaign_id, branch_id)
        trigger_id = str(trigger or "").strip().casefold().replace(" ", "_")
        if trigger_id not in {"entering_combat", "taking_damage", "fear", "nightmare"}:
            raise support.CombatEngineError("Cackle stress trigger is not source-defined")
        source_id = str(source_event_id or "").strip()
        if trigger_id != "entering_combat" and not source_id:
            raise support.CombatEngineError("Cackle stress requires a verifiable source event")
        payload = {
            "actor_id": actor_id,
            "trigger": trigger_id,
            "source_event_id": source_id or None,
            "expected_actor_revision": expected_actor_revision,
            "branch_id": branch,
        }
        scope = f"character-disease-stress:{campaign_id}:{branch}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                f"campaign revision conflict: expected {expected_revision}, "
                f"found {campaign.revision}"
            )
        if self.campaign_rules_edition(campaign_id) != "2014":
            raise support.CombatEngineError("Cackle Fever stress requires a 2014 campaign")
        state = support.validate_party_state(support.deepcopy(campaign.state or {}))
        actor = self.require_campaign_actor(campaign_id, actor_id)
        if actor.revision != expected_actor_revision:
            raise ValueError(
                f"actor revision conflict: expected {expected_actor_revision}, "
                f"found {actor.revision}"
            )
        matched = _actor_disease_state(actor.sheet, "cackle_fever")
        if matched is None:
            raise support.CombatEngineError("actor has no active Cackle Fever instance")
        disease_effect, disease_state = matched
        was_symptomatic = bool(disease_state.get("symptomatic"))
        elapsed_ticks = int(dict(state.get("game_time") or {}).get("elapsed_ticks", 0))
        disease_state = advance_disease_clock(disease_state, elapsed_ticks=elapsed_ticks)
        combat = dict(state.get("combat") or {})
        if trigger_id == "entering_combat":
            combatants = list(combat.get("combatants") or [])
            if (
                not combat.get("active")
                or int(combat.get("round", 0) or 0) != 1
                or int(combat.get("turn_index", -1)) != 0
                or actor_id not in {str(item.get("actor_id") or "") for item in combatants}
            ):
                raise support.CombatEngineError(
                    "entering-combat stress requires the authoritative first encounter boundary"
                )
            source_id = "combat.start:" + ":".join(
                sorted(str(item.get("actor_id") or "") for item in combatants)
            )
        elif trigger_id == "taking_damage":
            event = next(
                (item for item in state.get("resolution_log", []) if item.get("id") == source_id),
                None,
            )
            result = dict((event or {}).get("result") or {})
            damage = dict(result.get("damage") or {})
            if (
                (event or {}).get("operation") != "combat.attack"
                or (event or {}).get("status") != "settled"
                or str(result.get("target_id") or "") != str(actor_id)
                or int(damage.get("applied_amount", 0) or 0) <= 0
            ):
                raise support.CombatEngineError(
                    "taking-damage stress requires a committed attack event that damaged this actor"
                )
        elif trigger_id == "fear":
            fear_sources = [
                item
                for item in actor.sheet.get("effects", [])
                if item.get("active") and "frightened" in effect_condition_additions(item)
            ]
            if not fear_sources or source_id not in {
                str(item.get("id") or "") for item in fear_sources
            }:
                raise support.CombatEngineError(
                    "fear stress requires an active source-owned frightened condition effect"
                )
        else:
            event = next(
                (item for item in state.get("resolution_log", []) if item.get("id") == source_id),
                None,
            )
            if (event or {}).get("type") != "nightmare" or str(
                (event or {}).get("actor_id") or ""
            ) != str(actor_id):
                raise support.CombatEngineError(
                    "nightmare stress requires a committed actor-scoped nightmare event"
                )
        source_key = f"{trigger_id}:{source_id}"
        consumed_sources = list(disease_state.get("resolved_stress_sources") or [])
        if source_key in consumed_sources:
            raise support.CombatEngineError("this Cackle stress event has already been resolved")
        rules = self.effective_rule_context(
            campaign_id,
            branch_id=branch,
            facts={"actor_id": actor_id, "disease_id": "cackle_fever", "trigger": trigger_id},
        )
        if support.active_random_stream() is None:
            with self.campaign_random_context(
                campaign_id,
                "character.disease.stress",
                {"idempotency_key": idempotency_key},
            ):
                return self.character_disease_stress(
                    campaign_id,
                    actor_id,
                    trigger_id,
                    source_event_id=source_event_id,
                    principal_id=principal_id,
                    expected_revision=expected_revision,
                    expected_actor_revision=expected_actor_revision,
                    branch_id=branch,
                    idempotency_key=idempotency_key,
                )
        snapshot = self.combat_actor_snapshot(actor_id)
        snapshot["sheet"] = support.deepcopy(actor.sheet)
        snapshot["derived"] = derive_character_sheet(actor.sheet)
        save = support.resolve_actor_check(
            snapshot,
            kind="save",
            ability="constitution",
            dc=13,
            save_condition_id=str(disease_effect.get("id") or ""),
            encounter=combat if combat.get("active") else None,
            rules=rules,
            rng=support.active_random_stream(),
            ruleset="2014",
        )
        damage_roll = None
        damage_result = None
        psychic_damage = None
        if save.get("success") is not True:
            damage_roll = roll("1d10", rng=support.active_random_stream())
            psychic_damage = int(damage_roll.total)
        transition = resolve_cackle_stress(
            disease_state,
            trigger=trigger_id,
            save_succeeded=save.get("success") is True,
            elapsed_ticks=elapsed_ticks,
            psychic_damage_roll=psychic_damage,
        )
        next_sheet = support.deepcopy(actor.sheet)
        updated_state = transition["state"]
        if not was_symptomatic and updated_state.get("symptomatic"):
            before_exhaustion = int(dict(next_sheet.get("combat") or {}).get("exhaustion", 0) or 0)
            locked_exhaustion = min(6, before_exhaustion + 1)
            next_sheet = support.set_exhaustion_level(next_sheet, locked_exhaustion)
            updated_state["symptom_exhaustion_owned"] = True
            updated_state["locked_exhaustion_level"] = locked_exhaustion
        updated_state["resolved_stress_sources"] = [*consumed_sources, source_key][-100:]
        updated_disease = next(
            item
            for item in next_sheet.get("effects", [])
            if item.get("id") == disease_effect.get("id")
        )
        updated_disease.setdefault("metadata", {})["disease_state"] = updated_state
        if psychic_damage is not None:
            damage_result = support.apply_damage_to_sheet(
                next_sheet,
                amount=psychic_damage,
                damage_type="psychic",
                source=f"disease:cackle_fever:{disease_effect.get('id')}:stress",
                ruleset="2014",
                death_saves=getattr(actor, "character_type", None) == "pc",
            )
            next_sheet = damage_result["sheet"]
            laughter_id = f"{disease_effect.get('id')}:laughter"
            laughter = {
                "id": laughter_id,
                "name": "Cackle Fever mad laughter",
                "kind": "timed_conditions",
                "source": DISEASE_SOURCE_REF,
                "active": True,
                "duration": {"period": "minute", "remaining": 1},
                "changes": [{"path": "conditions", "mode": "add", "value": "incapacitated"}],
                "metadata": {
                    "disease_condition_owner": disease_effect.get("id"),
                    "disease_id": "cackle_fever",
                    "ends_elapsed_ticks": updated_state["laughing_until_elapsed_ticks"],
                },
            }
            next_sheet, _ = support.add_effect(next_sheet, laughter)
        next_sheet = support.validate_character_sheet(next_sheet)
        if combat.get("active"):
            self.sync_combatant_conditions(combat, actor_id, next_sheet)
        update = support.CharacterStateUpdate(
            character_id=actor_id,
            sheet=next_sheet,
            notes=support.validate_character_notes(actor.notes),
            expected_revision=actor.revision,
        )
        rule_context = self.effective_rule_context(
            campaign_id,
            branch_id=branch,
            facts={"actor_id": actor_id, "disease_id": "cackle_fever", "trigger": trigger_id},
        )
        disease_receipt = {
            "mechanic_id": "dnd5e.core.gamemastering.disease.cackle_fever.stress.2014",
            "event": "character.disease.stress",
            "operations": [{"op": "builtin.core_provider"}],
            "citations": [{"source": DISEASE_SOURCE_REF, "edition": "2014"}],
            "ruleset_fingerprint": rule_context.fingerprint,
            "facts": {"actor_id": actor_id, "trigger": trigger_id},
        }
        receipts = [disease_receipt, *support.deepcopy(list(save.get("rule_receipts") or []))]
        result = {
            "disease_id": "cackle_fever",
            "trigger": trigger_id,
            "source_event_id": source_id or None,
            "save": save,
            "damage": (
                {key: value for key, value in damage_result.items() if key != "sheet"}
                if damage_result is not None
                else None
            ),
            "damage_roll": support.asdict(damage_roll) if damage_roll is not None else None,
            "events": transition["events"],
            "disease_state": updated_state,
        }
        resolution_id = f"resolution-{support.uuid4().hex}"
        state["resolution_log"] = [
            *list(state.get("resolution_log") or []),
            {
                "id": resolution_id,
                "thread_id": resolution_id,
                "event_sequence": 1,
                "type": "disease_stress",
                "operation": "character.disease.stress",
                "actor_id": actor_id,
                "audience": {"scope": "actors", "actor_refs": [actor_id], "disclosure": "private"},
                "branch_id": branch,
                "campaign_revision": campaign.revision + 1,
                "result": result,
            },
        ][-100:]
        return self.commit_campaign_state(
            campaign,
            state,
            operation="character.disease.stress",
            principal_id=principal_id,
            branch_id=branch,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "committed",
                "resolution_id": resolution_id,
                "result": result,
                "campaign_revision": campaign.revision + 1,
                "random_stream_receipt": support.active_random_stream().receipt(),
                "rule_receipts": receipts,
            },
            character_updates=[update],
            rule_receipts=receipts,
            expected_campaign_revision=expected_revision,
        )

    def character_disease_end_turn(
        self,
        campaign_id: str,
        actor_id: str,
        *,
        principal_id: str = support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        expected_actor_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Resolve the currently active laughing actor's end-of-turn CON save."""
        self.access.require_campaign(campaign_id, principal_id, roles=support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        if expected_actor_revision is None:
            raise ValueError("expected_actor_revision is required for Cackle end-turn recovery")
        branch = self.require_current_branch(campaign_id, branch_id)
        payload = {
            "actor_id": actor_id,
            "expected_actor_revision": expected_actor_revision,
            "branch_id": branch,
        }
        scope = f"character-disease-end-turn:{campaign_id}:{branch}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                f"campaign revision conflict: expected {expected_revision}, "
                f"found {campaign.revision}"
            )
        if self.campaign_rules_edition(campaign_id) != "2014":
            raise support.CombatEngineError("Cackle end-turn save requires a 2014 campaign")
        state = support.validate_party_state(support.deepcopy(campaign.state or {}))
        combat = dict(state.get("combat") or {})
        combatants = list(combat.get("combatants") or [])
        turn_index = int(combat.get("turn_index", 0) or 0)
        current_turn_actor = (
            str(combatants[turn_index % len(combatants)].get("actor_id") or "")
            if combatants
            else ""
        )
        if not combat.get("active") or current_turn_actor != str(actor_id):
            raise support.CombatEngineError(
                "Cackle end-turn save requires this actor's authoritative active turn"
            )
        actor = self.require_campaign_actor(campaign_id, actor_id)
        if actor.revision != expected_actor_revision:
            raise ValueError(
                f"actor revision conflict: expected {expected_actor_revision}, "
                f"found {actor.revision}"
            )
        matched = _actor_disease_state(actor.sheet, "cackle_fever")
        if matched is None:
            raise support.CombatEngineError("actor has no active Cackle Fever instance")
        disease_effect, disease_state = matched
        if not disease_state.get("laughing"):
            raise support.CombatEngineError("Cackle end-turn save requires active mad laughter")
        if support.active_random_stream() is None:
            with self.campaign_random_context(
                campaign_id, "character.disease.end_turn", {"idempotency_key": idempotency_key}
            ):
                return self.character_disease_end_turn(
                    campaign_id,
                    actor_id,
                    principal_id=principal_id,
                    expected_revision=expected_revision,
                    expected_actor_revision=expected_actor_revision,
                    branch_id=branch,
                    idempotency_key=idempotency_key,
                )
        rules = self.effective_rule_context(
            campaign_id,
            branch_id=branch,
            facts={"actor_id": actor_id, "disease_id": "cackle_fever", "trigger": "end_of_turn"},
        )
        snapshot = self.combat_actor_snapshot(actor_id)
        snapshot["sheet"] = support.deepcopy(actor.sheet)
        snapshot["derived"] = derive_character_sheet(actor.sheet)
        save = support.resolve_actor_check(
            snapshot,
            kind="save",
            ability="constitution",
            dc=13,
            save_condition_id=str(disease_effect.get("id") or ""),
            encounter=combat,
            rules=rules,
            rng=support.active_random_stream(),
            ruleset="2014",
        )
        transition = resolve_cackle_end_turn(
            disease_state, save_succeeded=save.get("success") is True
        )
        next_sheet = support.deepcopy(actor.sheet)
        updated_disease = next(
            item
            for item in next_sheet.get("effects", [])
            if item.get("id") == disease_effect.get("id")
        )
        updated_disease.setdefault("metadata", {})["disease_state"] = transition["state"]
        ended_laughter = []
        if save.get("success") is True:
            for effect in next_sheet.get("effects", []):
                metadata = dict(effect.get("metadata") or {})
                if (
                    metadata.get("disease_condition_owner") == disease_effect.get("id")
                    and metadata.get("disease_id") == "cackle_fever"
                    and effect.get("active")
                ):
                    effect["active"] = False
                    effect["ended_reason"] = "cackle_fever_end_turn_save"
                    ended_laughter.append(effect)
            if ended_laughter:
                support.reconcile_ended_effect_conditions(next_sheet, ended_effects=ended_laughter)
        next_sheet = support.validate_character_sheet(next_sheet)
        self.sync_combatant_conditions(combat, actor_id, next_sheet)
        update = support.CharacterStateUpdate(
            character_id=actor_id,
            sheet=next_sheet,
            notes=support.validate_character_notes(actor.notes),
            expected_revision=actor.revision,
        )
        result = {
            "disease_id": "cackle_fever",
            "save": save,
            "events": transition["events"],
            "disease_state": transition["state"],
            "ended_condition_effect_ids": [str(item.get("id") or "") for item in ended_laughter],
        }
        resolution_id = f"resolution-{support.uuid4().hex}"
        state["resolution_log"] = [
            *list(state.get("resolution_log") or []),
            {
                "id": resolution_id,
                "thread_id": resolution_id,
                "event_sequence": 1,
                "type": "disease_end_turn",
                "operation": "character.disease.end_turn",
                "actor_id": actor_id,
                "audience": {"scope": "actors", "actor_refs": [actor_id], "disclosure": "private"},
                "branch_id": branch,
                "campaign_revision": campaign.revision + 1,
                "result": result,
            },
        ][-100:]
        return self.commit_campaign_state(
            campaign,
            state,
            operation="character.disease.end_turn",
            principal_id=principal_id,
            branch_id=branch,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "committed",
                "resolution_id": resolution_id,
                "result": result,
                "campaign_revision": campaign.revision + 1,
                "random_stream_receipt": support.active_random_stream().receipt(),
                "rule_receipts": list(save.get("rule_receipts") or []),
            },
            character_updates=[update],
            rule_receipts=list(save.get("rule_receipts") or []),
            expected_campaign_revision=expected_revision,
        )

    def character_disease_exposure(
        self,
        campaign_id: str,
        actor_id: str,
        disease_id: str,
        exposure_kind: str,
        exposure_source_id: str,
        exposure_source_ref: str,
        *,
        source_actor_id: str | None = None,
        target_turn_start: bool | None = None,
        within_10_feet: bool | None = None,
        principal_id: str = support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        expected_actor_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Settle one DM-confirmed exposure with a single campaign/actor CAS.

        The caller confirms the exact source fact; saves, infection outcomes,
        incubation and carrier-pair immunity are derived here from actor cards,
        campaign time and the campaign random stream.
        """
        self.access.require_campaign(campaign_id, principal_id, roles=support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        if expected_actor_revision is None:
            raise ValueError("expected_actor_revision is required for disease exposure")
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        try:
            profile = disease_profile(disease_id)
        except ValueError as error:
            raise support.CombatEngineError(str(error)) from error
        normalized_exposure = str(exposure_kind or "").strip().casefold().replace("-", "_")
        if normalized_exposure not in _DISEASE_EXPOSURES[profile["id"]]:
            raise support.CombatEngineError("exposure kind is not defined for this disease")
        if exposure_source_ref != DISEASE_SOURCE_REF:
            raise support.CombatEngineError("disease source must match the bundled 2014 definition")
        normalized_source_id = str(exposure_source_id or "").strip()
        if not normalized_source_id or len(normalized_source_id) > 200:
            raise support.CombatEngineError("disease exposure requires a bounded source identity")
        if source_actor_id is not None and not str(source_actor_id).strip():
            raise support.CombatEngineError("source_actor_id cannot be empty")
        if profile["id"] == "cackle_fever":
            if source_actor_id is None or target_turn_start is not True:
                raise support.CombatEngineError(
                    "Cackle Fever spread requires a carrier and DM confirmation "
                    "of target turn start"
                )
            if type(within_10_feet) not in {bool, type(None)}:
                raise support.CombatEngineError("within_10_feet confirmation must be boolean")
        elif target_turn_start is not None or within_10_feet is not None:
            raise support.CombatEngineError("turn-start distance facts apply only to Cackle Fever")
        if normalized_exposure in {"carrier_laughter", "carrier_bite"}:
            if not source_actor_id or normalized_source_id != str(source_actor_id):
                raise support.CombatEngineError(
                    "carrier exposure source identity must match its actor"
                )
            if str(source_actor_id) == str(actor_id):
                raise support.CombatEngineError("an actor cannot be exposed by itself as a carrier")
        elif source_actor_id is not None:
            raise support.CombatEngineError("non-carrier exposure cannot name a carrier actor")

        payload = {
            "actor_id": actor_id,
            "disease_id": profile["id"],
            "exposure_kind": normalized_exposure,
            "exposure_source_id": normalized_source_id,
            "exposure_source_ref": exposure_source_ref,
            "source_actor_id": source_actor_id,
            "target_turn_start": target_turn_start,
            "within_10_feet": within_10_feet,
            "expected_actor_revision": expected_actor_revision,
            "branch_id": resolved_branch_id,
        }
        scope = f"character-disease-exposure:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay

        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                f"campaign revision conflict: expected {expected_revision}, "
                f"found {campaign.revision}"
            )
        if self.campaign_rules_edition(campaign_id) != "2014":
            raise support.CombatEngineError("source-bound disease samples require a 2014 campaign")
        state = support.validate_party_state(support.deepcopy(campaign.state or {}))
        encounter = dict(state.get("combat") or {})
        if encounter.get("active"):
            self.require_no_blocking_pending(encounter)
        target = self.require_campaign_actor(campaign_id, actor_id)
        if target.revision != expected_actor_revision:
            raise ValueError(
                "actor revision conflict: "
                f"expected {expected_actor_revision}, found {target.revision}"
            )
        if str(target.sheet.get("edition") or "") != "2014":
            raise support.CombatEngineError("source-bound disease samples require a 2014 target")
        taxonomy, species_id = _authoritative_disease_taxonomy(target)
        disease_facts = {
            "disease_id": profile["id"],
            "source_ref": profile["source_ref"],
            "exposure_kind": normalized_exposure,
            "exposure_source_id": normalized_source_id,
            "target_actor_id": actor_id,
            "creature_type": taxonomy,
            "species_id": species_id,
        }
        target_active = _actor_disease_state(target.sheet, profile["id"])
        elapsed_ticks = int(dict(state.get("game_time") or {}).get("elapsed_ticks", 0))
        save_result: dict[str, Any] | None = None
        infection: dict[str, Any] | None = None
        immunity_result: dict[str, Any] | None = None
        status = "already_infected" if target_active else "ineligible"
        source_record = None
        carrier_state = None
        carrier_update = None
        immunity_pair = None

        if normalized_exposure in {"carrier_laughter", "carrier_bite"}:
            source_record = self.require_campaign_actor(campaign_id, str(source_actor_id))
            if str(source_record.sheet.get("edition") or "") != "2014":
                raise support.CombatEngineError("disease carrier must use the 2014 ruleset")
            active = _actor_disease_state(source_record.sheet, profile["id"])
            if active is None:
                raise support.CombatEngineError(
                    "named source actor is not an active carrier of this disease"
                )
            _carrier_effect, carrier_state = active
            carrier_state = advance_disease_clock(carrier_state, elapsed_ticks=elapsed_ticks)

        if not target_active and normalized_exposure in {"carrier_laughter", "carrier_bite"}:
            if normalized_exposure == "carrier_laughter":
                if not carrier_state.get("symptomatic") or not carrier_state.get("laughing"):
                    raise support.CombatEngineError(
                        "Cackle Fever carrier must be symptomatic and currently laughing"
                    )
                if encounter.get("active"):
                    combatants = list(encounter.get("combatants") or [])
                    turn_index = int(encounter.get("turn_index", 0) or 0)
                    current_id = (
                        str(combatants[turn_index % len(combatants)].get("actor_id") or "")
                        if combatants
                        else ""
                    )
                    if current_id != str(actor_id):
                        raise support.CombatEngineError(
                            "Cackle Fever spread can resolve only at the target's active turn start"
                        )
                    if encounter.get("positioning_mode") == "grid":
                        if within_10_feet is not None:
                            raise support.CombatEngineError(
                                "grid spread distance is derived from encounter positions"
                            )
                        source_combatant = next(
                            (
                                item
                                for item in combatants
                                if str(item.get("actor_id") or "") == str(source_actor_id)
                            ),
                            None,
                        )
                        target_combatant = next(
                            (
                                item
                                for item in combatants
                                if str(item.get("actor_id") or "") == str(actor_id)
                            ),
                            None,
                        )
                        distance = self.combat_distance(
                            dict(source_combatant or {}).get("position"),
                            dict(target_combatant or {}).get("position"),
                        )
                        if distance is None:
                            raise support.NeedsRulingError(
                                "Cackle Fever spread needs both grid positions",
                                missing=("combatant_positions",),
                                ruling_kind="source_or_scene_fact",
                            )
                        within = distance <= 10
                    else:
                        if type(within_10_feet) is not bool:
                            raise support.NeedsRulingError(
                                "Agent-mode spread needs DM confirmation of the 10-foot range",
                                missing=("within_10_feet",),
                                ruling_kind="source_or_scene_fact",
                            )
                        within = within_10_feet
                else:
                    if type(within_10_feet) is not bool:
                        raise support.NeedsRulingError(
                            "non-combat spread needs DM confirmation of the 10-foot range",
                            missing=("within_10_feet",),
                            ruling_kind="source_or_scene_fact",
                        )
                    within = within_10_feet
                immunity_pair = self._find_cackle_immunity(
                    state, target_id=actor_id, carrier_id=source_actor_id
                )
                carrier_effect = _carrier_effect
                if carrier_state != dict(
                    dict(carrier_effect.get("metadata") or {}).get("disease_state") or {}
                ):
                    carrier_sheet = support.deepcopy(source_record.sheet)
                    carrier_effect_copy = next(
                        item
                        for item in carrier_sheet.get("effects", [])
                        if item.get("id") == carrier_effect.get("id")
                    )
                    carrier_effect_copy.setdefault("metadata", {})["disease_state"] = carrier_state
                    carrier_update = support.CharacterStateUpdate(
                        character_id=source_record.id,
                        sheet=support.validate_character_sheet(carrier_sheet),
                        notes=support.validate_character_notes(source_record.notes),
                        expected_revision=source_record.revision,
                    )
                if taxonomy not in profile["eligible_creature_types"]:
                    status = "ineligible"
                elif not within:
                    status = "out_of_range"
                elif species_id in _GNOME_SPECIES_IDS:
                    status = "immune"
                elif immunity_pair is not None and elapsed_ticks < int(
                    immunity_pair.get("expires_elapsed_ticks", 0)
                ):
                    status = "immune_to_carrier"
                    immunity_result = immunity_pair
                else:
                    status = "eligible"
            else:
                if taxonomy not in profile["eligible_creature_types"]:
                    status = "ineligible"
                else:
                    status = "eligible"
        elif not target_active:
            status = "eligible" if taxonomy in profile["eligible_creature_types"] else "ineligible"
            if profile["id"] == "cackle_fever" and species_id in _GNOME_SPECIES_IDS:
                status = "immune"

        should_roll = status == "eligible"
        if should_roll and support.active_random_stream() is None:
            with self.campaign_random_context(
                campaign_id,
                "character.disease.exposure",
                {"idempotency_key": idempotency_key},
            ):
                return self.character_disease_exposure(
                    campaign_id,
                    actor_id,
                    profile["id"],
                    normalized_exposure,
                    normalized_source_id,
                    exposure_source_ref,
                    source_actor_id=source_actor_id,
                    target_turn_start=target_turn_start,
                    within_10_feet=within_10_feet,
                    principal_id=principal_id,
                    expected_revision=expected_revision,
                    expected_actor_revision=expected_actor_revision,
                    branch_id=resolved_branch_id,
                    idempotency_key=idempotency_key,
                )
        if should_roll:
            actor_snapshot = self.combat_actor_snapshot(actor_id)
            actor_snapshot["sheet"] = support.deepcopy(target.sheet)
            actor_snapshot["derived"] = derive_character_sheet(target.sheet)
            rules = self.effective_rule_context(
                campaign_id,
                branch_id=resolved_branch_id,
                facts={
                    **disease_facts,
                    "actor_id": actor_id,
                    "kind": "save",
                    "ability": "constitution",
                    "save_dc": int(profile["save_dc"]),
                },
            )
            save_result = support.resolve_actor_check(
                actor_snapshot,
                kind="save",
                ability="constitution",
                dc=int(profile["save_dc"]),
                rules=rules,
                encounter=encounter if encounter.get("active") else None,
                rng=support.active_random_stream(),
                ruleset="2014",
            )
            if profile["id"] == "cackle_fever":
                save_succeeded = bool(save_result.get("success"))
                incubation_roll = None
                if not save_succeeded:
                    incubation_roll = int(roll("1d4", rng=support.active_random_stream()).total)
                transition = resolve_cackle_spread(
                    carrier_state,
                    target_actor_id=actor_id,
                    target_creature_type=taxonomy,
                    target_species_id=species_id,
                    distance_ft=(
                        distance
                        if encounter.get("active") and encounter.get("positioning_mode") == "grid"
                        else None
                    ),
                    within_10_feet=(
                        None
                        if encounter.get("active") and encounter.get("positioning_mode") == "grid"
                        else within
                    ),
                    elapsed_ticks=elapsed_ticks,
                    save_succeeded=save_succeeded,
                    incubation_roll=incubation_roll,
                    immunity=immunity_pair,
                )
                infection = transition.get("infection")
                if infection is not None and incubation_roll is not None:
                    infection["incubation_roll"] = incubation_roll
                immunity_result = transition.get("immunity")
                status = str(transition["status"])
            else:
                save_succeeded = bool(save_result.get("success"))
                incubation_roll = None
                if not save_succeeded and "die" in profile["incubation"]:
                    incubation_roll = int(roll("1d4", rng=support.active_random_stream()).total)
                infection = infection_state(
                    profile["id"],
                    actor_id=actor_id,
                    elapsed_ticks=elapsed_ticks,
                    save_succeeded=save_succeeded,
                    incubation_roll=incubation_roll,
                )
                status = "saved" if save_succeeded else "infected"
                if infection is not None and incubation_roll is not None:
                    infection["incubation_roll"] = incubation_roll

        rule_context = self.effective_rule_context(
            campaign_id, branch_id=resolved_branch_id, facts=disease_facts
        )
        disease_receipt = {
            "mechanic_id": f"dnd5e.core.gamemastering.disease.{profile['id']}.2014",
            "event": "character.disease.exposure",
            "operations": [{"op": "builtin.core_provider"}],
            "citations": [{"source": profile["source_ref"], "edition": "2014"}],
            "ruleset_fingerprint": rule_context.fingerprint,
            "facts": disease_facts,
        }
        receipts = [disease_receipt]
        if save_result is not None:
            receipts.extend(support.deepcopy(list(save_result.get("rule_receipts") or [])))
        target_update = None
        effect = None
        if infection is not None:
            effect = {
                "id": support.uuid4().hex,
                "name": profile["name"],
                "kind": "disease_state",
                "source": DISEASE_SOURCE_REF,
                "active": True,
                "duration": {"period": "manual", "remaining": 0},
                "changes": [],
                "metadata": {"disease_state": infection},
            }
            try:
                infected_sheet, _ = support.add_effect(target.sheet, effect)
            except ValueError as error:
                raise support.CombatEngineError(str(error)) from error
            target_update = support.CharacterStateUpdate(
                character_id=target.id,
                sheet=infected_sheet,
                notes=support.validate_character_notes(target.notes),
                expected_revision=target.revision,
            )

        result = {
            "status": status,
            "disease_id": profile["id"],
            "target_actor_id": actor_id,
            "exposure_kind": normalized_exposure,
            "exposure_source_id": normalized_source_id,
            "exposure_source_ref": profile["source_ref"],
            "save": save_result,
            "disease_state": infection,
            "disease_effect": effect,
            "carrier_immunity": immunity_result,
        }
        resolution_id = f"resolution-{support.uuid4().hex}"
        new_event = {
            "id": resolution_id,
            "thread_id": resolution_id,
            "event_sequence": 1,
            "type": "disease_exposure",
            "operation": "character.disease.exposure",
            "actor_id": actor_id,
            "audience": {
                "scope": "actors",
                "actor_refs": [actor_id],
                "disclosure": "private",
            },
            "branch_id": resolved_branch_id,
            "campaign_revision": campaign.revision + 1,
            "result": result,
        }
        previous_events = list(state.get("resolution_log") or [])
        live_immunity_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
        for event in previous_events:
            event_immunity = dict(dict(event.get("result") or {}).get("carrier_immunity") or {})
            if not event_immunity:
                continue
            expiry = int(event_immunity.get("expires_elapsed_ticks", 0))
            if expiry <= elapsed_ticks:
                continue
            pair = (
                str(event_immunity.get("target_actor_id") or ""),
                str(event_immunity.get("carrier_actor_id") or ""),
            )
            prior = live_immunity_by_pair.get(pair)
            if (
                prior is None
                or int(
                    dict(dict(prior.get("result") or {}).get("carrier_immunity") or {}).get(
                        "expires_elapsed_ticks", 0
                    )
                )
                <= expiry
            ):
                live_immunity_by_pair[pair] = event
        live_immunity_events = list(live_immunity_by_pair.values())
        other_events = [event for event in previous_events if event not in live_immunity_events][
            -100:
        ]
        state["resolution_log"] = [*other_events, *live_immunity_events, new_event]
        updates = [item for item in (target_update, carrier_update) if item is not None]
        if (
            target_update is not None
            and carrier_update is not None
            and target_update.character_id == carrier_update.character_id
        ):
            raise support.CombatEngineError("disease source and target state updates overlap")
        return self.commit_campaign_state(
            campaign,
            state,
            operation="character.disease.exposure",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "committed",
                "resolution_id": resolution_id,
                "result": result,
                "rule_receipts": receipts,
            },
            character_updates=updates,
            rule_receipts=receipts,
            expected_campaign_revision=expected_revision,
        )

    @staticmethod
    def _find_cackle_immunity(
        campaign_state: dict[str, Any], *, target_id: str, carrier_id: str
    ) -> dict[str, Any] | None:
        matches = []
        for event in campaign_state.get("resolution_log", []):
            if event.get("operation") != "character.disease.exposure":
                continue
            immunity = dict(dict(event.get("result") or {}).get("carrier_immunity") or {})
            if (
                immunity.get("target_actor_id") == str(target_id)
                and immunity.get("carrier_actor_id") == str(carrier_id)
                and immunity.get("source_ref") == DISEASE_SOURCE_REF
            ):
                matches.append(immunity)
        return (
            max(matches, key=lambda item: int(item.get("expires_elapsed_ticks", 0)))
            if matches
            else None
        )

    def character_disease_cure(
        self,
        campaign_id: str,
        actor_id: str,
        disease_effect_id: str,
        cure_source_id: str,
        cure_source_ref: str,
        *,
        principal_id: str = support.LOCAL_SYSTEM_PRINCIPAL_ID,
        expected_revision: int | None = None,
        expected_actor_revision: int | None = None,
        branch_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """End one exact disease instance through a DM-confirmed 2014 cure source.

        The spell or class feature mechanics that authorize this boundary remain
        owned by their normal executor; the DM confirmation binds that result to
        one exact disease marker without exposing generic effect removal.
        """
        self.access.require_campaign(campaign_id, principal_id, roles=support.CAMPAIGN_DM_ROLES)
        self.require_write_contract(expected_revision, idempotency_key)
        if expected_actor_revision is None:
            raise ValueError("expected_actor_revision is required for disease cure")
        resolved_branch_id = self.require_current_branch(campaign_id, branch_id)
        cure_id = str(cure_source_id or "").strip().casefold().replace("-", "_").replace(" ", "_")
        expected_source = _DISEASE_CURES.get(cure_id)
        if expected_source is None or cure_source_ref != expected_source:
            raise support.CombatEngineError("cure source is not an applicable bundled 2014 source")
        payload = {
            "actor_id": actor_id,
            "disease_effect_id": disease_effect_id,
            "cure_source_id": cure_id,
            "cure_source_ref": cure_source_ref,
            "expected_actor_revision": expected_actor_revision,
            "branch_id": resolved_branch_id,
        }
        scope = f"character-disease-cure:{campaign_id}:{resolved_branch_id}:{principal_id}"
        replay = self.replay_idempotent(scope, idempotency_key, payload)
        if replay is not None:
            return replay
        campaign = self.campaigns.get(campaign_id)
        if campaign.revision != expected_revision:
            raise ValueError(
                f"campaign revision conflict: expected {expected_revision}, "
                f"found {campaign.revision}"
            )
        if self.campaign_rules_edition(campaign_id) != "2014":
            raise support.CombatEngineError("source-bound disease cures require a 2014 campaign")
        target = self.require_campaign_actor(campaign_id, actor_id)
        if target.revision != expected_actor_revision:
            raise ValueError(
                f"actor revision conflict: expected {expected_actor_revision}, "
                f"found {target.revision}"
            )
        sheet = support.deepcopy(target.sheet)
        effect = next(
            (
                item
                for item in sheet.get("effects", [])
                if str(item.get("id") or "") == str(disease_effect_id)
            ),
            None,
        )
        disease_state = _disease_effect_state(effect or {})
        if disease_state is None or not disease_state.get("active"):
            raise support.CombatEngineError("selected disease effect is not active")
        disease_id = str(disease_state.get("disease_id") or "")
        try:
            transition = cure_disease(disease_state, disease_id=disease_id)
        except ValueError as error:
            raise support.CombatEngineError(str(error)) from error
        cured_state = transition["state"]
        if disease_id == "sight_rot" and cure_id in {"lesser_restoration", "heal"}:
            cured_state["blindness_owned"] = False
            transition["events"].append({"kind": "disease_owned_sight_restored"})
            blindness_effects = []
            for projection in sheet.get("effects", []):
                metadata = dict(projection.get("metadata") or {})
                if (
                    metadata.get("disease_condition_owner") == str(disease_effect_id)
                    and metadata.get("disease_id") == "sight_rot"
                    and projection.get("active")
                ):
                    projection["active"] = False
                    projection["ended_reason"] = f"restored_by:{cure_id}"
                    blindness_effects.append(projection)
            if blindness_effects:
                support.reconcile_ended_effect_conditions(
                    sheet,
                    ended_effects=blindness_effects,
                )
        effect["metadata"]["disease_state"] = cured_state
        effect["active"] = False
        effect["ended_reason"] = f"cured_by:{cure_id}"
        sheet = support.validate_character_sheet(sheet)
        target_update = support.CharacterStateUpdate(
            character_id=target.id,
            sheet=sheet,
            notes=support.validate_character_notes(target.notes),
            expected_revision=target.revision,
        )
        cure_rules = self.effective_rule_context(
            campaign_id,
            branch_id=resolved_branch_id,
            facts={
                "disease_id": disease_id,
                "disease_effect_id": disease_effect_id,
                "cure_source_id": cure_id,
                "target_actor_id": actor_id,
            },
        )
        source_receipt = {
            "mechanic_id": f"dnd5e.core.gamemastering.disease.{disease_id}.2014",
            "event": "character.disease.cure",
            "operations": [{"op": "effect.end"}],
            "citations": [
                {"source": DISEASE_SOURCE_REF, "edition": "2014"},
                {"source": cure_source_ref, "edition": "2014"},
            ],
            "ruleset_fingerprint": cure_rules.fingerprint,
            "facts": {
                "disease_id": disease_id,
                "disease_effect_id": disease_effect_id,
                "cure_source_id": cure_id,
                "cure_source_ref": cure_source_ref,
                "target_actor_id": actor_id,
            },
        }
        result = {
            "status": "committed",
            "disease_id": disease_id,
            "disease_effect_id": disease_effect_id,
            "cure_source_id": cure_id,
            "events": transition["events"],
        }
        state = support.validate_party_state(support.deepcopy(campaign.state or {}))
        if bool(dict(state.get("combat") or {}).get("active")):
            self.sync_combatant_conditions(state["combat"], actor_id, sheet)
        resolution_id = f"resolution-{support.uuid4().hex}"
        state["resolution_log"] = [
            *list(state.get("resolution_log") or []),
            {
                "id": resolution_id,
                "thread_id": resolution_id,
                "event_sequence": 1,
                "type": "disease_cure",
                "operation": "character.disease.cure",
                "actor_id": actor_id,
                "audience": {
                    "scope": "actors",
                    "actor_refs": [actor_id],
                    "disclosure": "private",
                },
                "branch_id": resolved_branch_id,
                "campaign_revision": campaign.revision + 1,
                "result": result,
            },
        ][-100:]
        return self.commit_campaign_state(
            campaign,
            state,
            operation="character.disease.cure",
            principal_id=principal_id,
            branch_id=resolved_branch_id,
            idempotency_key=idempotency_key,
            scope=scope,
            payload=payload,
            response_fields={
                "status": "committed",
                "resolution_id": resolution_id,
                "result": result,
                "rule_receipts": [source_receipt],
            },
            character_updates=[target_update],
            rule_receipts=[source_receipt],
            expected_campaign_revision=expected_revision,
        )
