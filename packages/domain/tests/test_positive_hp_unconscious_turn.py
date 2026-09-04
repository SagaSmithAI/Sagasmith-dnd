import pytest

from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.combat_engine import CombatEngineError, end_turn, start_encounter


def _actor(actor_id: str, hp: int, conditions: list[str]) -> dict:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": hp, "max": 10, "temp": 0}
    sheet["conditions"] = conditions
    return {
        "id": actor_id,
        "name": actor_id,
        "character_type": "pc",
        "initiative": 20,
        "sheet": sheet,
        "derived": derive_character_sheet(sheet),
    }


def test_positive_hp_unconscious_actor_does_not_need_death_save_at_turn_end():
    actor = _actor("sleeping", 1, ["unconscious"])
    other = _actor("other", 10, [])
    other["initiative"] = 10
    encounter = start_encounter([actor, other])
    ended = end_turn(encounter, actor_id_value="sleeping", current_actor_sheet=actor["sheet"])
    assert ended["turn_index"] == 1


def test_zero_hp_unconscious_actor_still_needs_death_save_at_turn_end():
    actor = _actor("dying", 0, ["unconscious"])
    other = _actor("other", 10, [])
    other["initiative"] = 10
    encounter = start_encounter([actor, other])
    with pytest.raises(CombatEngineError, match="death save"):
        end_turn(encounter, actor_id_value="dying", current_actor_sheet=actor["sheet"])


@pytest.mark.parametrize("condition", ["stable", "dead"])
def test_zero_hp_settled_actor_does_not_need_another_death_save(condition):
    actor = _actor("settled", 0, ["unconscious", condition])
    other = _actor("other", 10, [])
    other["initiative"] = 10
    encounter = start_encounter([actor, other])
    ended = end_turn(encounter, actor_id_value="settled", current_actor_sheet=actor["sheet"])
    assert ended["turn_index"] == 1
