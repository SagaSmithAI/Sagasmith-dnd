from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.combat_engine import (
    CombatEngineError,
    apply_damage_to_sheet,
    available_actions,
    resolve_common_action,
    start_encounter,
)
from sagasmith_dnd.sleep import SLEEP_SPELL_ID, resolve_sleep_targets


@pytest.mark.parametrize(
    "amount,temp,immune,awake",
    [(1, 0, False, True), (1, 5, False, True), (0, 0, False, False), (1, 0, True, False)],
)
def test_sleep_ends_on_damage_including_temp_hp_but_not_zero_damage(amount, temp, immune, awake):
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": temp}
    if immune:
        sheet["traits"]["immunities"] = ["fire"]
    slept = resolve_sleep_targets(
        [{"id": "target", "sheet": sheet}],
        pool=10,
        source_actor_id="caster",
        source_spell_id=SLEEP_SPELL_ID,
    )
    sleeping = slept["sheets"]["target"]
    before = deepcopy(sleeping)
    damaged = apply_damage_to_sheet(sleeping, amount=amount, damage_type="fire")
    assert sleeping == before
    assert ("unconscious" not in damaged["sheet"]["conditions"]) is awake
    assert damaged["ended_effect_ids"] == ([slept["targets"][0]["effect_id"]] if awake else [])


def test_damage_ending_sleep_does_not_remove_zero_hp_unconsciousness():
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
    slept = resolve_sleep_targets(
        [{"id": "target", "sheet": sheet}],
        pool=10,
        source_actor_id="caster",
        source_spell_id=SLEEP_SPELL_ID,
    )
    damaged = apply_damage_to_sheet(
        slept["sheets"]["target"], amount=10, damage_type="fire", death_saves=True
    )
    assert damaged["after_hp"] == 0
    assert damaged["ended_effect_ids"] == [slept["targets"][0]["effect_id"]]
    assert {"unconscious", "prone"} <= set(damaged["sheet"]["conditions"])


def test_shaking_sleep_uses_one_main_action_and_is_2014_only():
    actor = {"id": "helper", "sheet": default_character_sheet(), "initiative": 20}
    encounter = start_encounter([actor], ruleset="2014")
    before = deepcopy(encounter)
    assert "shake_sleep" in available_actions(encounter, "helper")
    paid = resolve_common_action(encounter, actor_id_value="helper", action="shake_sleep")
    assert paid["combatants"][0]["turn_budget"]["main_action"] == 0
    assert encounter == before
    with pytest.raises(CombatEngineError, match="legal action payment"):
        resolve_common_action(paid, actor_id_value="helper", action="shake_sleep")
    encounter_2024 = deepcopy(encounter)
    encounter_2024["ruleset"] = "2024"
    assert "shake_sleep" not in available_actions(encounter_2024, "helper")
    with pytest.raises(CombatEngineError, match="2014"):
        resolve_common_action(encounter_2024, actor_id_value="helper", action="shake_sleep")
