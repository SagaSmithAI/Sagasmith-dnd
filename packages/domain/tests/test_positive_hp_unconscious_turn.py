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


def test_actor_reduced_to_zero_during_own_turn_waits_until_next_turn():
    actor = _actor("falling", 10, [])
    other = _actor("other", 10, [])
    other["initiative"] = 10
    encounter = start_encounter([actor, other], ruleset="2014")
    actor["sheet"]["combat"]["hp"]["value"] = 0
    actor["sheet"]["conditions"] = ["unconscious"]
    encounter["combatants"][0]["conditions"] = ["unconscious"]
    encounter["combatants"][0]["hit_points"] = 0
    ended = end_turn(encounter, actor_id_value="falling", current_actor_sheet=actor["sheet"])
    returned = end_turn(ended, actor_id_value="other", current_actor_sheet=other["sheet"])
    with pytest.raises(CombatEngineError, match="death save"):
        end_turn(returned, actor_id_value="falling", current_actor_sheet=actor["sheet"])


def test_stable_at_turn_start_then_damaged_does_not_owe_a_start_save():
    actor = _actor("stable", 0, ["unconscious", "stable"])
    other = _actor("other", 10, [])
    other["initiative"] = 10
    encounter = start_encounter([actor, other], ruleset="2014")
    actor["sheet"]["conditions"] = ["unconscious"]
    actor["sheet"]["combat"]["death_saves"]["failures"] = 1
    encounter["combatants"][0]["conditions"] = ["unconscious"]
    ended = end_turn(encounter, actor_id_value="stable", current_actor_sheet=actor["sheet"])
    assert ended["turn_index"] == 1
