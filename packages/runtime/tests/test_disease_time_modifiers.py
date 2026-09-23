from sagasmith_dnd.character_schema import active_effect_roll_bonus, default_character_sheet
from sagasmith_dnd.diseases import DISEASE_SOURCE_REF, infection_state
from sagasmith_dnd_runtime.services.campaigns import (
    _advance_disease_effects,
    _apply_sewer_plague_hit_die_limit,
)


def test_sight_rot_clock_refreshes_attack_penalty_without_global_check_penalty():
    sheet = default_character_sheet()
    state = infection_state(
        "sight_rot", actor_id="actor-1", elapsed_ticks=0, save_succeeded=False
    )
    effect = {
        "id": "sight-rot-1",
        "name": "Sight Rot",
        "kind": "disease_state",
        "source": DISEASE_SOURCE_REF,
        "active": True,
        "duration": {"period": "manual", "remaining": 0},
        "changes": [],
        "metadata": {"disease_state": state},
    }
    sheet["effects"].append(effect)

    advanced, changed = _advance_disease_effects(sheet, 24 * 600)

    updated = next(item for item in advanced["effects"] if item["id"] == effect["id"])
    assert changed == [effect["id"]]
    assert updated["metadata"]["disease_state"]["symptomatic"] is True
    assert updated["changes"] == []
    assert active_effect_roll_bonus(advanced, "attack") == 0
    assert active_effect_roll_bonus(advanced, "ability") == 0


def test_sight_rot_clock_reconciles_existing_penalty_to_attack_rider_only():
    sheet = default_character_sheet()
    state = infection_state(
        "sight_rot", actor_id="actor-1", elapsed_ticks=0, save_succeeded=False
    )
    state["symptomatic"] = True
    state["sight_penalty"] = 3
    effect = {
        "id": "sight-rot-2",
        "name": "Sight Rot",
        "kind": "disease_state",
        "source": DISEASE_SOURCE_REF,
        "active": True,
        "duration": {"period": "manual", "remaining": 0},
        "changes": [{"path": "rolls.ability_check.bonus", "mode": "add", "value": -3}],
        "metadata": {"disease_state": state},
    }
    sheet["effects"].append(effect)

    advanced, changed = _advance_disease_effects(sheet, 24 * 600)

    updated = next(item for item in advanced["effects"] if item["id"] == effect["id"])
    assert effect["id"] in changed
    assert updated["changes"] == [
        {"path": "rolls.attack.bonus", "mode": "add", "value": -3}
    ]
    assert active_effect_roll_bonus(advanced, "attack") == -3
    assert active_effect_roll_bonus(advanced, "ability") == 0


def test_sewer_plague_halves_hit_die_healing_before_hp_cap():
    sheet = default_character_sheet()
    sheet["abilities"]["constitution"]["score"] = 12
    sheet["combat"]["hp"]["max"] = 20
    sheet["combat"]["hp"]["value"] = 18
    applied = {
        "sheet": default_character_sheet(),
        "hit_die_rolls": [{"key": "d8", "total": 4}, {"key": "d8", "total": 4}],
        "hit_die_applied_healing": 2,
    }
    applied["sheet"]["combat"]["hp"]["max"] = 20

    resolved = _apply_sewer_plague_hit_die_limit(sheet, applied)

    assert resolved["sewer_plague_hit_die_healing"] == {
        "normal_hit_die_healing": 10,
        "disease_hit_die_healing": 5,
    }
    assert resolved["hit_die_applied_healing"] == 2
    assert resolved["sheet"]["combat"]["hp"]["value"] == 20
