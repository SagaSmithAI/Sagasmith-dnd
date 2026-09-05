from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import add_inventory_item, default_character_sheet
from sagasmith_dnd.dependent_actor_refresh import (
    materialize_dependent_actor_owner_scaling,
    refresh_dependent_actor_sheet,
)


def _authoritative(*, hp_max: int, pb: int, spell_attack: int) -> dict:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["combat"]["hp"] = {"value": hp_max, "max": hp_max, "temp": 0}
    sheet["abilities"]["dexterity"]["bonus"] = pb + 1
    sheet["abilities"]["constitution"]["bonus"] = pb + 3
    sheet["skills"]["athletics"]["bonus"] = pb + 1
    sheet["skills"]["perception"]["bonus"] = pb + 1
    sheet, _ = add_inventory_item(
        sheet,
        {
            "id": "force-empowered-rend",
            "name": "Force-Empowered Rend",
            "kind": "weapon",
            "description": f"Hit: 1d8 + {pb} force damage.",
            "mechanics": {
                "attack_type": "melee",
                "attack_ability": "spell",
                "damage_formula": "1d8",
                "damage_type": "force",
                "properties": [],
                "attack_bonus_override": spell_attack,
                "damage_bonus_override": pb,
                "always_available": True,
            },
        },
    )
    sheet["content"]["activities"].append(
        {
            "id": "repair-action",
            "name": "Repair (3/Day)",
            "source_key": "rule-pack:example",
            "description": f"The defender restores 2d8 + {pb} hit points.",
            "activation": {"type": "action", "cost": 1},
            "choices": {
                "manual_ruling": {
                    "kind": "descriptive_activity",
                    "default_resolver": "agent",
                    "source_excerpt": f"The defender restores 2d8 + {pb} hit points.",
                }
            },
            "pack_id": "pack.example",
            "pack_version": "1.0.0",
        }
    )
    from sagasmith_dnd.character_schema import validate_character_sheet

    return validate_character_sheet(sheet)


def _params(pb: int, spell_attack: int, level: int = 5) -> dict[str, int]:
    return {
        "owner_class_level": level,
        "owner_proficiency_bonus": pb,
        "owner_spell_attack_modifier": spell_attack,
    }


def _reviewed_steel_defender_baseline() -> dict:
    sheet = _authoritative(hp_max=17, pb=2, spell_attack=4)
    sheet["abilities"]["dexterity"]["bonus"] = 2
    sheet["abilities"]["constitution"]["bonus"] = 2
    sheet["skills"]["athletics"]["bonus"] = 2
    sheet["skills"]["perception"]["bonus"] = 4
    sheet["inventory"]["items"][0]["description"] = (
        "Melee Weapon Attack: +4 to hit. Hit: 1d8 + 2 force damage."
    )
    return sheet


def test_materialize_steel_defender_might_of_the_master_from_reviewed_baseline() -> None:
    result = materialize_dependent_actor_owner_scaling(
        _reviewed_steel_defender_baseline(),
        _params(3, 5),
        relation_key="steel_defender",
        reviewed_expression_hash=(
            "539cc387391b58fce93a7f0268910b66615db8a42006ab1913378222f1216e8c"
        ),
    )
    assert result["abilities"]["dexterity"]["bonus"] == 3
    assert result["abilities"]["constitution"]["bonus"] == 3
    assert result["skills"]["athletics"]["bonus"] == 3
    assert result["skills"]["perception"]["bonus"] == 5
    rend = result["inventory"]["items"][0]
    assert rend["mechanics"]["attack_bonus_override"] == 5
    assert rend["mechanics"]["damage_bonus_override"] == 3
    assert "+5 to hit" in rend["description"]
    assert "1d8 + 3 force damage" in rend["description"]
    repair = result["content"]["activities"][0]
    assert "2d8 + 3 hit points" in repair["description"]
    assert "2d8 + 3 hit points" in repair["choices"]["manual_ruling"]["source_excerpt"]


def test_materialize_steel_defender_scaling_ignores_other_reviewed_templates() -> None:
    baseline = _reviewed_steel_defender_baseline()
    assert (
        materialize_dependent_actor_owner_scaling(
            baseline,
            _params(3, 5),
            relation_key="steel_defender",
            reviewed_expression_hash="0" * 64,
        )
        == baseline
    )


def test_materialize_steel_defender_scaling_rejects_baseline_drift() -> None:
    sheet = _reviewed_steel_defender_baseline()
    sheet["inventory"]["items"][0]["mechanics"]["attack_bonus_override"] = 99
    with pytest.raises(ValueError, match="Rend no longer matches"):
        materialize_dependent_actor_owner_scaling(
            sheet,
            _params(3, 5),
            relation_key="steel_defender",
            reviewed_expression_hash=(
                "539cc387391b58fce93a7f0268910b66615db8a42006ab1913378222f1216e8c"
            ),
        )


def test_refresh_updates_only_owner_bound_steel_defender_values() -> None:
    old = _authoritative(hp_max=30, pb=2, spell_attack=5)
    new = _authoritative(hp_max=35, pb=3, spell_attack=6)
    current = deepcopy(old)
    current["combat"]["hp"]["value"] = 7
    current["combat"]["hp"]["temp"] = 4
    current["combat"]["death_saves"] = {"successes": 1, "failures": 2}
    current["conditions"] = ["poisoned"]
    current["effects"] = []
    current["inventory"]["items"][0]["quantity"] = 3
    current["inventory"]["items"][0]["equipped"] = True
    current["inventory"]["items"][0]["equipped_slot"] = "main_hand"
    current["inventory"]["equipment_slots"]["main_hand"] = "force-empowered-rend"

    result = refresh_dependent_actor_sheet(
        current,
        old,
        new,
        _params(2, 5),
        _params(3, 6, 6),
    )
    refreshed = result["sheet"]
    assert refreshed["combat"]["hp"] == {"value": 7, "max": 35, "temp": 4}
    assert refreshed["combat"]["death_saves"] == {"successes": 1, "failures": 2}
    assert refreshed["conditions"] == ["poisoned"]
    assert refreshed["inventory"]["items"][0]["quantity"] == 3
    assert refreshed["inventory"]["equipment_slots"]["main_hand"] == "force-empowered-rend"
    assert refreshed["abilities"]["dexterity"]["bonus"] == 4
    assert refreshed["skills"]["perception"]["bonus"] == 4
    rend = refreshed["inventory"]["items"][0]
    assert rend["mechanics"]["attack_bonus_override"] == 6
    assert rend["mechanics"]["damage_bonus_override"] == 3
    assert refreshed["content"]["activities"][0]["description"].endswith("+ 3 hit points.")
    assert result["changed_paths"]


def test_refresh_is_noop_when_parameters_do_not_change() -> None:
    old = _authoritative(hp_max=30, pb=2, spell_attack=5)
    current = deepcopy(old)
    current["combat"]["hp"]["value"] = 3
    result = refresh_dependent_actor_sheet(
        current,
        old,
        deepcopy(old),
        _params(2, 5),
        _params(2, 5),
    )
    assert result["sheet"] == current
    assert result["changed_paths"] == []


def test_refresh_rejects_current_owner_bound_field_tampering() -> None:
    old = _authoritative(hp_max=30, pb=2, spell_attack=5)
    new = _authoritative(hp_max=30, pb=3, spell_attack=6)
    current = deepcopy(old)
    current["skills"]["perception"]["bonus"] += 1
    with pytest.raises(ValueError, match="diverged"):
        refresh_dependent_actor_sheet(current, old, new, _params(2, 5), _params(3, 6))


def test_refresh_rejects_unapproved_materialized_difference() -> None:
    old = _authoritative(hp_max=30, pb=2, spell_attack=5)
    new = _authoritative(hp_max=30, pb=3, spell_attack=6)
    new["combat"]["ac"]["override"] = 16
    with pytest.raises(ValueError, match="unsupported"):
        refresh_dependent_actor_sheet(old, old, new, _params(2, 5), _params(3, 6))


def test_refresh_does_not_clamp_current_hp_when_new_max_is_lower() -> None:
    old = _authoritative(hp_max=30, pb=2, spell_attack=5)
    new = _authoritative(hp_max=20, pb=3, spell_attack=6)
    current = deepcopy(old)
    current["combat"]["hp"]["value"] = 25
    with pytest.raises(ValueError, match="hp.value"):
        refresh_dependent_actor_sheet(current, old, new, _params(2, 5), _params(3, 6))
