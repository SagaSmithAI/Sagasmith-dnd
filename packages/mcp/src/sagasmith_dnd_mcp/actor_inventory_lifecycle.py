"""D&D inventory settlement at Core's atomic campaign-actor creation boundary."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable
from uuid import uuid4

from sagasmith_core import CampaignService, CharacterService
from sagasmith_core.actor_lifecycle import ActorLifecycleService
from sagasmith_core.idempotency import IdempotencyService
from sagasmith_dnd.character_schema import validate_party_state
from sagasmith_dnd.combat_engine import end_concentration_for_incapacitating_conditions
from sagasmith_dnd.conditions import apply_condition_change, condition_ids
from sagasmith_dnd.external_custody import validate_external_inventory_custody
from sagasmith_dnd.ground_transfer import drop_held_items
from sagasmith_dnd.held_items import held_item_roots


class InventoryActorLifecycleService(ActorLifecycleService):
    """Create an actor and its initial ground custody in one lifecycle group."""

    def __init__(self, database, *, ground_context: Callable[..., dict[str, Any]]) -> None:
        super().__init__(database)
        self.ground_context = ground_context

    def create(self, campaign_id: str, **kwargs: Any):
        # Preserve Core's existing request digest. Generated IDs and derived
        # ground state are outcomes, not retry inputs. Existing receipts must
        # replay before inspecting a later campaign/custody snapshot.
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
        with self.database.transaction() as session:
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
            actor_id = kwargs.get("actor_id") or str(uuid4())
            if any(actor.id == actor_id for actor in records):
                raise ValueError("new actor id already exists in the campaign")
            sheets = {actor.id: actor.sheet for actor in records}
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
            return result
