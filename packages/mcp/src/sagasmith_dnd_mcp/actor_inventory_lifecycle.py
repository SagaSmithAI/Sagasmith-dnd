"""D&D inventory settlement at Core's atomic campaign-actor creation boundary."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Callable
from uuid import uuid4

from sagasmith_core import CampaignService, CharacterService
from sagasmith_core.actor_lifecycle import ActorLifecycleService
from sagasmith_core.idempotency import IdempotencyService
from sagasmith_core.rule_receipts import RuleReceiptService
from sagasmith_dnd.character_schema import validate_character_sheet, validate_party_state
from sagasmith_dnd.combat_engine import end_concentration_for_incapacitating_conditions
from sagasmith_dnd.conditions import apply_condition_change, condition_ids
from sagasmith_dnd.dependent_actor_relations import (
    validate_dependent_actor_references,
    validate_dependent_actor_relations,
)
from sagasmith_dnd.external_custody import validate_external_inventory_custody
from sagasmith_dnd.ground_transfer import drop_held_items
from sagasmith_dnd.held_items import held_item_roots


class InventoryActorLifecycleService(ActorLifecycleService):
    """Create an actor and its initial ground custody in one lifecycle group."""

    def __init__(self, database, *, ground_context: Callable[..., dict[str, Any]]) -> None:
        super().__init__(database)
        self.ground_context = ground_context

    def create(self, campaign_id: str, **kwargs: Any):
        dependent_actor_authorization = deepcopy(kwargs.pop("dependent_actor_authorization", None))
        dependent_actor_replacement = deepcopy(kwargs.pop("dependent_actor_replacement", None))
        # Preserve Core's existing request digest. Generated IDs and derived
        # ground state are outcomes, not retry inputs. The lifecycle receipt
        # is additionally scoped to the resolved branch below.
        defaults = {
            "player_name": None,
            "summary": "",
            "template_id": None,
            "campaign_state": None,
            "expected_campaign_revision": None,
            "operation": "actor.lifecycle.create",
            "branch_id": None,
            "actor_id": None,
        }
        payload = {
            "campaign_id": campaign_id,
            **{key: deepcopy(kwargs.get(key, default)) for key, default in defaults.items()},
            **{
                key: deepcopy(kwargs[key])
                for key in ("system_id", "name", "character_type", "sheet", "notes")
            },
            "initial_grants": [
                {
                    "principal_id": grant.principal_id,
                    "can_control": grant.can_control,
                    "can_view_private": grant.can_view_private,
                }
                for grant in kwargs.get("initial_grants", ())
            ],
        }
        payload = deepcopy(kwargs.get("idempotency_payload") or payload)
        scope = f"actor-lifecycle:{campaign_id}:{kwargs['principal_id']}"
        # Core 0.2.3 maps this explicit write-lock transaction to
        # ``BEGIN IMMEDIATE`` on SQLite and native row locks elsewhere.
        with self.database.transaction(immediate=True) as session:
            if dependent_actor_authorization is not None:
                required = {
                    "owner_character_id",
                    "owner_character_revision",
                    "feature_artifact_id",
                    "source_pack_id",
                    "source_pack_version",
                    "receipt_event",
                    "receipt_fields",
                    "branch_id",
                }
                if (
                    not isinstance(dependent_actor_authorization, Mapping)
                    or set(dependent_actor_authorization) != required
                ):
                    raise ValueError("dependent actor authorization is invalid")
                authorization = dict(dependent_actor_authorization)
                expected_owner_revision = authorization["owner_character_revision"]
                if isinstance(expected_owner_revision, bool) or not isinstance(
                    expected_owner_revision, int
                ):
                    raise ValueError("dependent actor owner revision is invalid")
                owner = CharacterService(self.database).get_for_update(
                    str(authorization["owner_character_id"])
                )
                if owner.campaign_id != campaign_id:
                    raise ValueError("dependent actor owner belongs to another campaign")
                if owner.revision != expected_owner_revision:
                    raise ValueError(
                        "dependent actor owner revision conflict: "
                        f"expected {expected_owner_revision}, found {owner.revision}"
                    )
                feature_matches = [
                    feature
                    for feature in dict(owner.sheet.get("content") or {}).get("features", [])
                    if isinstance(feature, Mapping)
                    and str(feature.get("id") or "") == authorization["feature_artifact_id"]
                    and str(feature.get("pack_id") or "") == authorization["source_pack_id"]
                    and str(feature.get("pack_version") or "")
                    == authorization["source_pack_version"]
                ]
                receipt_fields = authorization["receipt_fields"]
                if len(feature_matches) != 1 or not RuleReceiptService(
                    self.database
                ).has_applied_receipt(
                    campaign_id,
                    event=str(authorization["receipt_event"]),
                    receipt_fields=receipt_fields,
                    branch_id=(
                        str(authorization["branch_id"])
                        if authorization["branch_id"] is not None
                        else None
                    ),
                ):
                    raise ValueError(
                        "dependent actor owner lacks the exact applied feature entitlement"
                    )
            # Generic actor retries can replay immediately. Source-bound
            # dependents reach this point only after their current-branch
            # feature receipt has been revalidated above.
            replay = IdempotencyService(self.database).lookup_in_session(
                session, scope, str(kwargs["idempotency_key"]).strip(), payload
            )
            if replay is not None and replay.response is not None:
                return super().create(campaign_id, **kwargs)
            campaign = CampaignService(self.database).get(campaign_id)
            state = deepcopy(
                kwargs.get("campaign_state")
                if kwargs.get("campaign_state") is not None
                else campaign.state or {}
            )
            records = CharacterService(self.database).list(campaign_id=campaign_id)
            sheets = {actor.id: actor.sheet for actor in records}
            actor_id = kwargs.get("actor_id") or str(uuid4())
            if any(actor.id == actor_id for actor in records):
                raise ValueError("new actor id already exists in the campaign")

            replacement = None
            if dependent_actor_replacement is not None:
                required = {"character_id", "expected_revision"}
                if (
                    not isinstance(dependent_actor_replacement, Mapping)
                    or set(dependent_actor_replacement) != required
                ):
                    raise ValueError("dependent actor replacement is invalid")
                replacement_id = dependent_actor_replacement["character_id"]
                expected_revision = dependent_actor_replacement["expected_revision"]
                if not isinstance(replacement_id, str) or not replacement_id.strip():
                    raise ValueError("dependent actor replacement character id is invalid")
                if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
                    raise ValueError("dependent actor replacement revision is invalid")
                replacement = CharacterService(self.database).get_for_update(replacement_id)
                if replacement.campaign_id != campaign_id:
                    raise ValueError(
                        "dependent actor replacement target belongs to another campaign"
                    )
                if replacement.revision != expected_revision:
                    raise ValueError(
                        "dependent actor replacement revision conflict: "
                        f"expected {expected_revision}, found {replacement.revision}"
                    )
                current_relations = validate_dependent_actor_references(
                    (campaign.state or {}).get("dependent_actor_relations", []),
                    {actor.id for actor in records},
                )
                active_matches = [
                    relation
                    for relation in current_relations
                    if relation["dependent_actor_id"] == replacement.id
                    and relation["status"] == "active"
                ]
                if len(active_matches) != 1:
                    raise ValueError(
                        "dependent actor replacement target must have exactly one active relation"
                    )
                submitted_relations = validate_dependent_actor_relations(
                    state.get("dependent_actor_relations", [])
                )
                submitted_target = [
                    relation
                    for relation in submitted_relations
                    if relation["dependent_actor_id"] == replacement.id
                ]
                if len(submitted_target) != 1 or submitted_target[0]["status"] != "replaced":
                    raise ValueError(
                        "dependent actor replacement relation must mark the old actor replaced"
                    )
                old_relation = active_matches[0]
                if any(
                    submitted_target[0][field] != old_relation[field]
                    for field in (
                        "owner_character_id",
                        "relation_key",
                        "source_artifact_id",
                        "source_pack_id",
                        "source_pack_version",
                    )
                ):
                    raise ValueError(
                        "dependent actor replacement relation changed the old actor binding"
                    )
                new_matches = [
                    relation
                    for relation in submitted_relations
                    if relation["dependent_actor_id"] == actor_id
                    and relation["status"] == "active"
                    and relation["owner_character_id"] == old_relation["owner_character_id"]
                    and relation["relation_key"] == old_relation["relation_key"]
                ]
                if len(new_matches) != 1:
                    raise ValueError("dependent actor replacement relation must bind the new actor")
                new_relation = new_matches[0]
                if new_relation["created_long_rest_elapsed_ticks"] is None:
                    raise ValueError(
                        "dependent actor replacement relation requires a long-rest elapsed tick"
                    )
                for field in (
                    "source_artifact_id",
                    "source_pack_id",
                    "source_pack_version",
                ):
                    if new_relation[field] != old_relation[field]:
                        raise ValueError(
                            "dependent actor replacement relation changed its source template"
                        )
                replacement_sheet = deepcopy(replacement.sheet)
                replacement_hp = dict(
                    dict(replacement_sheet.setdefault("combat", {})).setdefault("hp", {})
                )
                replacement_hp["value"] = 0
                replacement_sheet["combat"]["hp"] = replacement_hp
                replacement_conditions = condition_ids(replacement_sheet.get("conditions"))
                replacement_conditions.add("dead")
                replacement_sheet["conditions"] = sorted(replacement_conditions)
                end_concentration_for_incapacitating_conditions(replacement_sheet)
                if held_item_roots(replacement_sheet):
                    dropped = drop_held_items(
                        {**sheets, replacement.id: replacement_sheet},
                        state.get("ground_items", []),
                        replacement.id,
                        record_ids={
                            item_id: f"ground-{uuid4().hex}"
                            for item_id in held_item_roots(replacement_sheet)
                        },
                        **self.ground_context(campaign, state, replacement.id),
                    )
                    replacement_sheet = dropped["sheets"][replacement.id]
                    state["ground_items"] = dropped["ground_items"]
                    kwargs["campaign_state"] = validate_party_state(state)
                replacement_sheet = validate_character_sheet(replacement_sheet)
                CharacterService(self.database).update(
                    replacement.id,
                    sheet=replacement_sheet,
                    expected_revision=expected_revision,
                )
                sheets[replacement.id] = replacement_sheet
            sheets[actor_id] = deepcopy(kwargs["sheet"])
            validate_external_inventory_custody(sheets, state.get("ground_items", []))
            sheet = sheets[actor_id]
            unconscious = sheet.get("edition") == "2014" and "unconscious" in condition_ids(
                sheet.get("conditions")
            )
            if sheet.get("edition") == "2014":
                end_concentration_for_incapacitating_conditions(sheet)
            if unconscious:
                apply_condition_change(sheet, condition_id="prone", add=True)
            kwargs = {
                **kwargs,
                "actor_id": actor_id,
                "sheet": sheet,
                "idempotency_payload": payload,
            }
            if unconscious and held_item_roots(sheet):
                dropped = drop_held_items(
                    sheets,
                    state.get("ground_items", []),
                    actor_id,
                    record_ids={
                        item_id: f"ground-{uuid4().hex}" for item_id in held_item_roots(sheet)
                    },
                    **self.ground_context(campaign, state, actor_id),
                )
                # A newly allocated identity cannot already carry somebody
                # else's physical item. Preflight custody rejects such dangling
                # references; never silently omit third-party updates here.
                if any(dropped["sheets"][actor.id] != sheets[actor.id] for actor in records):
                    raise ValueError("new actor drop cannot change existing actor custody")
                state["ground_items"] = dropped["ground_items"]
                kwargs = {
                    **kwargs,
                    "actor_id": actor_id,
                    "sheet": dropped["sheets"][actor_id],
                    "campaign_state": validate_party_state(state),
                    "expected_campaign_revision": (
                        kwargs.get("expected_campaign_revision")
                        if kwargs.get("expected_campaign_revision") is not None
                        else campaign.revision
                    ),
                    "idempotency_payload": payload,
                }
            result = super().create(campaign_id, **kwargs)
            # Includes callers creating source-bound actors with no initial
            # drop. Validation joins the owning transaction, never a post-commit
            # check that could leave an actor, grant, or replay receipt behind.
            current_sheets = {
                actor.id: actor.sheet
                for actor in CharacterService(self.database).list(campaign_id=campaign_id)
            }
            current_state = CampaignService(self.database).get(campaign_id).state or {}
            validate_external_inventory_custody(
                current_sheets, current_state.get("ground_items", [])
            )
            validate_dependent_actor_references(
                current_state.get("dependent_actor_relations", []),
                set(current_sheets),
            )
            return result
