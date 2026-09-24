from sagasmith_dnd.character_schema import active_effect_roll_bonus, default_character_sheet
from sagasmith_dnd.diseases import DISEASE_SOURCE_REF, infection_state
from sagasmith_dnd_runtime.services.campaigns import (
    _advance_disease_effects,
    _sewer_plague_symptomatic,
)
from sagasmith_dnd_runtime.services.diseases import deactivate_disease_owned_conditions


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


def test_sewer_plague_rest_policy_requires_an_active_source_owned_2014_instance():
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    state = infection_state(
        "sewer_plague",
        actor_id="actor-1",
        elapsed_ticks=0,
        incubation_roll=1,
        save_succeeded=False,
    )
    effect = {
        "id": "sewer-plague-1",
        "kind": "disease_state",
        "source": DISEASE_SOURCE_REF,
        "metadata": {"disease_state": state},
    }
    sheet["effects"].append(effect)

    assert _sewer_plague_symptomatic(sheet) is False
    assert _sewer_plague_symptomatic(sheet, elapsed_ticks=24 * 600) is True

    effect["source"] = "untrusted:copied-disease"
    assert _sewer_plague_symptomatic(sheet) is False

    effect["source"] = DISEASE_SOURCE_REF
    sheet["edition"] = "2024"
    assert _sewer_plague_symptomatic(sheet) is False


def test_disease_cure_reconciles_only_the_selected_instances_condition_rider():
    sheet = default_character_sheet()
    sheet["conditions"].append("incapacitated")
    sheet["effects"].extend(
        [
            {
                "id": "selected-laughter",
                "kind": "timed_conditions",
                "active": True,
                "changes": [{"path": "conditions", "mode": "add", "value": "incapacitated"}],
                "metadata": {
                    "disease_condition_owner": "selected-cackle",
                    "disease_id": "cackle_fever",
                },
            },
            {
                "id": "other-disease-laughter",
                "kind": "timed_conditions",
                "active": True,
                "changes": [{"path": "conditions", "mode": "add", "value": "incapacitated"}],
                "metadata": {
                    "disease_condition_owner": "other-cackle",
                    "disease_id": "cackle_fever",
                },
            },
            {
                "id": "unrelated-incapacitation",
                "kind": "timed_conditions",
                "active": True,
                "changes": [{"path": "conditions", "mode": "add", "value": "incapacitated"}],
                "metadata": {},
            },
        ]
    )

    cured, ended = deactivate_disease_owned_conditions(
        sheet,
        disease_effect_id="selected-cackle",
        disease_id="cackle_fever",
        ended_reason="cured_by:test",
    )

    effects = {effect["id"]: effect for effect in cured["effects"]}
    assert [effect["id"] for effect in ended] == ["selected-laughter"]
    assert effects["selected-laughter"]["active"] is False
    assert effects["other-disease-laughter"]["active"] is True
    assert effects["unrelated-incapacitation"]["active"] is True
    assert "incapacitated" in cured["conditions"]
