"""Canonical D&D actor-type vocabulary."""

from __future__ import annotations

CHARACTER_TYPES = ("pc", "npc", "monster")
NON_PLAYER_CHARACTER_TYPES = frozenset({"npc", "monster"})
HUMAN_DECISION_CONTROLLER = "human_player"
DM_AGENT_DECISION_CONTROLLER = "dm_agent"


def actor_decision_controller(character_type: str) -> str:
    """Return who may originate choices; mechanics remain engine-owned either way."""

    normalized = str(character_type or "").strip().casefold()
    if normalized not in CHARACTER_TYPES:
        raise ValueError(f"unsupported character_type: {character_type}")
    return (
        DM_AGENT_DECISION_CONTROLLER
        if normalized in NON_PLAYER_CHARACTER_TYPES
        else HUMAN_DECISION_CONTROLLER
    )


def require_agent_decidable_character_type(character_type: str) -> None:
    """Prevent an Agent evaluator from silently choosing for a human PC."""

    if actor_decision_controller(character_type) != DM_AGENT_DECISION_CONTROLLER:
        raise ValueError(
            "an Agent may decide only an NPC or monster; a human-owned PC must "
            "provide its own choice"
        )
