"""Bound generic Ready responses; Runtime settles their authoritative effects."""

from copy import deepcopy

from . import combat_engine as engine

COMMON_RESPONSES = frozenset({"dash", "dodge", "disengage", "help"})


def validate_response(payload):
    if not isinstance(payload, dict):
        raise engine.CombatEngineError("Ready requires an executable response object")
    value = deepcopy(payload)
    action = value.get("action")
    allowed = {
        "dash": {"action"},
        "dodge": {"action", "payload"},
        "disengage": {"action"},
        "help": {"action", "target_id", "payload"},
        "attack": {"action", "target_id", "attack"},
        "move": {
            "action",
            "distance",
            "destination",
            "path",
            "travel_mode",
            "crawl",
            "spatial_facts",
        },
        "ruling": {"action", "response", "source", "question"},
    }
    if not isinstance(action, str) or action not in allowed or set(value) - allowed[action]:
        raise engine.CombatEngineError(
            "Ready requires a supported response or explicit ruling contract"
        )
    if action == "attack":
        attack = value.get("attack")
        if not isinstance(value.get("target_id"), str) or not value["target_id"].strip():
            raise engine.CombatEngineError("readied Attack requires its original target_id")
        if not isinstance(attack, dict) or not isinstance(attack.get("weapon_id"), str):
            raise engine.CombatEngineError("readied Attack requires its original weapon_id")
        if not attack["weapon_id"].strip() or set(attack) - {"weapon_id", "attack_mode", "context"}:
            raise engine.CombatEngineError("Ready permits one weapon attack with fixed parameters")
    if action == "move":
        distance = value.get("distance")
        if isinstance(distance, bool) or not isinstance(distance, int) or distance <= 0:
            raise engine.CombatEngineError("readied movement requires a positive distance")
        if not any(
            value.get(field) is not None for field in ("destination", "path", "spatial_facts")
        ):
            raise engine.CombatEngineError(
                "readied movement requires its route or Agent spatial facts"
            )
    if action == "help":
        engine._normalize_help_declaration(value.get("payload"))
    if action == "ruling" and any(
        not isinstance(value.get(field), str) or not value[field].strip()
        for field in ("response", "source", "question")
    ):
        raise engine.CombatEngineError(
            "readied ruling requires the fixed response, source and question"
        )
    return value


def execute_non_attack(encounter, actor_id, choice_id):
    value, readied = engine.resolve_readied_action_window(
        encounter,
        actor_id_value=actor_id,
        choice_id=choice_id,
        release=True,
        _spend_reaction=False,
    )
    response = validate_response(readied["payload"])
    action = response["action"]
    if action == "move":
        from .movement_continuations import spend_source_movement

        value = spend_source_movement(
            value,
            actor_id,
            response["distance"],
            payment="reaction",
            distance_limit="speed",
            source={"id": "dnd5e.core.ready.action", "readied_id": readied["id"]},
            **{k: v for k, v in response.items() if k not in {"action", "distance"}},
        )
    elif action in COMMON_RESPONSES:
        value = engine.resolve_common_action(
            value,
            actor_id_value=actor_id,
            action=action,
            target_id=response.get("target_id"),
            payload=response.get("payload"),
            payment="reaction",
            _readied_response=True,
        )
    else:
        raise engine.CombatEngineError("this Ready response requires its Runtime resolver")
    return value, readied
