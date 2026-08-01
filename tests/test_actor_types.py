import pytest

from sagasmith_dnd.actor_types import (
    actor_decision_controller,
    require_agent_decidable_character_type,
)


@pytest.mark.parametrize("character_type", ["npc", "monster"])
def test_dm_agent_may_decide_only_non_player_actors(character_type: str) -> None:
    assert actor_decision_controller(character_type) == "dm_agent"
    require_agent_decidable_character_type(character_type)


def test_agent_cannot_silently_choose_for_a_player_character() -> None:
    assert actor_decision_controller("pc") == "human_player"
    with pytest.raises(ValueError, match="human-owned PC"):
        require_agent_decidable_character_type("pc")


def test_unknown_actor_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported character_type"):
        actor_decision_controller("vehicle")
