"""Truthful shared result metadata and required high-frequency result envelopes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def affected_state_slice(campaign, branch_id, updates, revision):
    """Project the exact documents used by a commit's durable response callback."""
    return {
        "campaign_id": campaign.id, "branch_id": branch_id,
        "timeline_epoch": campaign.timeline_epoch, "campaign_revision": revision,
        "actors": [
            {"id": update.character_id,
             "revision": (update.expected_revision + 1
                          if update.expected_revision is not None else None),
             "sheet": {key: deepcopy(update.sheet[key]) for key in (
                 "combat", "resources", "conditions", "effects", "inventory",
                 "spellcasting", "abilities", "proficiencies",
             ) if key in update.sheet}}
            for update in updates or []
        ],
    }


def tool_output_schema(tool: str) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "status": {"type": "string"},
        "action": {"type": "string"},
        "campaign_id": {"type": ["string", "null"]},
        "campaign_revision": {"type": ["integer", "null"], "minimum": 0},
        "character_revision": {"type": ["integer", "null"], "minimum": 0},
        "branch_id": {"type": ["string", "null"]},
        "resolution_id": {"type": "string"},
        "thread_id": {"type": "string"},
        "event_sequence": {"type": "integer"},
        "random_stream_receipt": {"type": "object"},
        "host_context_binding": {"type": ["object", "null"]},
        "next_cursor": {"type": ["string", "null"]},
        "has_more": {"type": "boolean"},
        "idempotent_replay": {"type": "boolean"},
        "error": {
            "type": "object",
            "required": ["code", "message", "retryable", "recovery"],
            "properties": {
                "code": {"type": "string"},
                "message": {"type": "string"},
                "retryable": {"type": "boolean"},
                "recovery": {"type": ["string", "object"]},
            },
            "additionalProperties": False,
        },
    }
    required: list[str] = []
    if tool == "character_check":
        # All check actions return the resolution directly, not a facade action envelope.
        properties["result"] = {"type": "object"}
        required = ["status", "result", "campaign_revision"]
    elif tool in {"combat_choice", "combat_ready", "combat_hp_change", "combat_movement",
                  "resolution_presentation"}:
        properties["result"] = {"type": "object"}
        required = ["status", "action", "result"]
    elif tool in {"combat_check", "combat_cast_spell", "combat_resolve_attack"}:
        properties["result"] = {
            "type": "object",
            "properties": {
                "kind": {"type": "string"}, "action": {"type": "string"},
                "actor_id": {"type": "string"}, "target_id": {"type": "string"},
                "spell_id": {"type": "string"}, "total": {"type": "integer"},
                "success": {"type": "boolean"}, "hit": {"type": "boolean"},
                "critical": {"type": "boolean"}, "fumble": {"type": "boolean"},
            },
            "additionalProperties": True,
        }
        properties["combat"] = {"type": ["object", "null"]}
        properties["choice"] = {"type": "object"}
        required = ["status", "result", "campaign_revision"]
    elif tool == "combat_preflight_attack":
        properties.update({name: {"type": "string"} for name in (
            "kind", "attacker_id", "target_id",
        )})
        properties["weapon_id"] = {"type": ["string", "null"]}
        properties["opaque"] = {"type": "boolean"}
        required = ["status", "kind", "attacker_id", "target_id"]
    elif tool in {"dnd_check", "dnd_dice_roll"}:
        required = ["resolution_id", "thread_id", "event_sequence",
                    "campaign_revision", "random_stream_receipt"]
        properties["total"] = {"type": "integer"}
        if tool == "dnd_check":
            properties["success"] = {"type": "boolean"}
            properties["dc"] = {"type": "integer"}
            required += ["total", "success", "dc"]
        else:
            properties["expression"] = {"type": "string"}
            properties["rolls"] = {"type": "array"}
            required += ["total", "expression", "rolls"]
    for name, definition in properties.items():
        definition["description"] = f"Authoritative {name.replace('_', ' ')} returned by {tool}."
    schema: dict[str, Any] = {
        "type": "object",
        "description": f"Structured authoritative result for {tool}.",
        "properties": properties,
        # Domain-specific projections evolve without silently dropping evidence.
        "additionalProperties": True,
    }
    if required:
        schema["anyOf"] = [
            {"required": required},
            {"required": ["error"]},
            {
                "required": ["status", "ruling_kind", "default_resolver"],
                "properties": {
                    "status": {"const": "pending_ruling"},
                    "ruling_kind": {"type": "string", "minLength": 1},
                    "default_resolver": {"enum": ["agent", "external_input"]},
                },
            },
        ]
    return schema
