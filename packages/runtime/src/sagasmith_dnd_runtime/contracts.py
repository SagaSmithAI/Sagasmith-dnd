"""Action payload contracts shared by direct Runtime calls and MCP adapters.

The models describe routing data. Domain-owned source facts, declarations and
compiled commitments remain structured objects validated by their owning engine.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, create_model

Identifier = Annotated[str, Field(min_length=1, max_length=256)]
Position = dict[str, float] | list[float] | tuple[float, float]


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


def _payload(name: str, **fields: Any) -> type[Payload]:
    return create_model(name, __base__=Payload, **fields)


def _optional(kind: Any, default: Any = None) -> tuple[Any, Any]:
    return kind | None, default


_choice = _payload(
    "ResolveChoice", choice_id=(Identifier, ...), selection=(dict[str, Any], ...)
)
_trigger = _payload(
    "TriggerReadied", readied_id=(Identifier, ...), event=(str, ...)
)
_release = _payload(
    "ResolveReadied",
    actor_id=(Identifier, ...),
    choice_id=(Identifier, ...),
    release=(bool, ...),
    declaration=_optional(dict[str, Any]),
)

ACTION_PAYLOADS: dict[str, dict[str, type[Payload]]] = {
    "character_check": {
        "scene_save": _payload(
            "SceneHazardSave", actor_id=(Identifier, ...), ability=(str, ...), dc=(int, ...),
            source_ref=(dict[str, Any], ...), source_excerpt=(str, ...),
            reason=(str, ...),
            save_source_kind=(Literal["nonmagical_effect", "magical_effect"], ...),
            save_effect_conditions=(list[str], ...), save_against_poison=(bool, ...),
            advantage=(bool, False), disadvantage=(bool, False), bonus=(int, 0),
        ),
        "check": _payload(
            "CharacterCheck", actor_id=(Identifier, ...),
            kind=(Literal["ability", "check", "save", "death_save"], ...),
            ability=(str, ...), dc=(int, 0), proficient=(bool, False), bonus=(int, 0),
            advantage=(bool, False), disadvantage=(bool, False),
            rule_facts=_optional(dict[str, Any]),
        ),
        "group": _payload(
            "CharacterGroupCheck", actor_ids=(list[Identifier], ...),
            ability=(str, ...), dc=(int, ...), proficient=(bool, False), bonus=(int, 0),
            advantage=(bool, False), disadvantage=(bool, False),
            rule_facts=_optional(dict[str, Any]),
        ),
        "contest": _payload(
            "CharacterContest", source_actor_id=(Identifier, ...),
            target_actor_id=(Identifier, ...), source_ability=(str, ...), target_ability=(str, ...),
            source_proficient=(bool, False), target_proficient=(bool, False),
            source_bonus=(int, 0), target_bonus=(int, 0),
            source_advantage=(bool, False), source_disadvantage=(bool, False),
            target_advantage=(bool, False), target_disadvantage=(bool, False),
            source_rule_facts=_optional(dict[str, Any]),
            target_rule_facts=_optional(dict[str, Any]),
        ),
        "reroll": _payload(
            "CharacterReroll", actor_id=(Identifier, ...), resolution_id=(Identifier, ...),
            roll_index=(int, ...), expected_original_roll=(int, ...),
        ),
        "source_feature": _payload(
            "CharacterSourceFeature", actor_id=(Identifier, ...), feature_id=(Identifier, ...),
            capability=(str, ...), settlement_ref=(str, ...), fact_key=(str, ...),
        ),
    },
    "combat_choice": {
        "open": _payload(
            "OpenChoice",
            event=(str, ...),
            candidates=_optional(list[dict[str, Any]]),
            kind=(str, "reaction"),
        ),
        "resolve": _choice,
        "resolve_defense": _choice,
        "on_hit_ruling": _choice,
        "execute_plan": _payload("ExecutePlan", commitment=(dict[str, Any], ...)),
    },
    "combat_ready": {
        "ready_spell": _payload(
            "ReadySpell",
            actor_id=(Identifier, ...),
            spell_id=(Identifier, ...),
            trigger=(str, ...),
            cast_level=_optional(int),
            declaration=_optional(dict[str, Any]),
            component_ruling=_optional(dict[str, Any]),
        ),
        "trigger_spell": _trigger,
        "trigger_action": _trigger,
        "resolve_spell": _release,
        "resolve_action": _release,
    },
    "combat_hp_change": {
        "damage": _payload(
            "ApplyDamage", parts=(list[dict[str, Any]], ...),
            critical=(bool, False), knock_out=(bool, False), melee=(bool, False),
        ),
        "fall": _payload("ApplyFall", distance_ft=(int, ...)),
        "heal": _payload(
            "ApplyHealing", amount=(int, ...), source_actor_id=_optional(Identifier),
            spell_id=_optional(Identifier), spell_level=_optional(int),
        ),
        "stabilize": _payload("SourceStabilization", source_excerpt=(str, ...)),
        "save_damage": _payload(
            "SaveDamage", target_ids=_optional(list[Identifier]),
            application_id=_optional(Identifier),
            source_actor_id=(Identifier, ...), source_card_id=(Identifier, ...),
            source_card_kind=(str, ...), save_ability=(str, ...), save_dc=(int, ...),
            damage_expression=(str, ...), damage_type=(str, ...),
            half_on_success=(bool, ...), save_advantage=(bool, False),
            save_disadvantage=(bool, False), mechanic_source_excerpt=(str, ...),
            agent_ruling=(dict[str, Any], ...), spatial_facts=_optional(dict[str, Any]),
        ),
    },
    "combat_movement": {
        "move": _payload(
            "MoveActor", distance=(int, ...), destination=_optional(Position),
            path=_optional(list[Position]), movement_mode=(str, "voluntary"),
            travel_mode=(str, "walk"), crawl=(bool, False),
            spatial_facts=_optional(dict[str, Any]),
        ),
        "stand": _payload("StandActor"),
    },
}


def validate_action_payload(tool: str, arguments: dict[str, Any]) -> None:
    """Reject malformed routing data before state access, dice or mutation."""
    actions = ACTION_PAYLOADS.get(tool)
    if actions is None:
        return
    action = arguments.get("action", "check" if tool == "character_check" else None)
    model = actions.get(action)
    if model is None:
        raise ValueError(f"{tool}: unsupported action {action!r}")
    payload = arguments.get("payload")
    try:
        model.model_validate({} if payload is None else payload)
    except ValidationError as error:
        messages = []
        for item in error.errors(include_input=False, include_url=False):
            field = ".".join(["payload", *(str(part) for part in item["loc"])])
            if item["type"] == "bool_type":
                messages.append(f"{field} must be a boolean")
            elif item["type"] == "missing":
                messages.append(f"{field} is required")
            elif item["type"] == "extra_forbidden":
                messages.append(f"unexpected {field}")
            else:
                messages.append(f"{field}: {item['msg']}")
        raise ValueError("; ".join(messages)) from error
    if tool == "combat_hp_change" and action == "save_damage" and isinstance(payload, dict):
        # Existing preflight receipts include this repeated identifier. Keep it
        # compatible while refusing contradictory copies of the source commitment.
        application_id = payload.get("application_id")
        if application_id is not None and application_id != payload["agent_ruling"].get(
            "application_id"
        ):
            raise ValueError("payload.application_id must match agent_ruling.application_id")
    if tool == "combat_choice":
        actor_id = arguments.get("actor_id")
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("combat_choice requires actor_id")


def action_parameters(tool: str, parameters: dict[str, Any]) -> dict[str, Any]:
    """Publish the same per-action models enforced by validate_action_payload."""
    revision_fields = {
        "character_ability_apply": "expected_revision",
        "character_content_apply": "expected_revision",
        "character_metadata_update": "expected_revision",
        "character_spell_prepare": "expected_revision",
        "combat_end": "expected_revision",
        "module_set_progress": "expected_state_version",
    }
    if tool in revision_fields:
        parameters = deepcopy(parameters)
        fields = {revision_fields[tool]: "integer", "idempotency_key": "string"}
        for name, kind in fields.items():
            field = parameters["properties"][name]
            field.pop("anyOf", None)
            field.pop("default", None)
            field["type"] = kind
            if kind == "string":
                field["minLength"] = 1
        parameters["required"] = sorted(set(parameters.get("required", [])) | fields.keys())
    if tool not in ACTION_PAYLOADS:
        return parameters
    schema = deepcopy(parameters)
    branches = schema.setdefault("allOf", [])
    for action, model in ACTION_PAYLOADS[tool].items():
        payload_schema = model.model_json_schema()
        then: dict[str, Any] = {"properties": {"payload": payload_schema}}
        if payload_schema.get("required"):
            then["required"] = ["payload"]
        else:
            then["properties"]["payload"] = {"anyOf": [payload_schema, {"type": "null"}]}
        condition: dict[str, Any] = {"properties": {"action": {"const": action}}}
        if not (tool == "character_check" and action == "check"):
            condition["required"] = ["action"]
        branches.append({
            "if": condition,
            "then": then,
        })
    if tool == "combat_choice":
        schema["properties"]["actor_id"] = {
            **schema["properties"]["actor_id"], "type": "string", "minLength": 1,
        }
        schema["properties"]["actor_id"].pop("anyOf", None)
        schema["properties"]["actor_id"].pop("default", None)
        schema["required"] = sorted(set(schema.get("required", [])) | {"actor_id"})
    return schema
