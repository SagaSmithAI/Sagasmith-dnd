"""2014 initiative is a Dexterity check, including persistent sheet modifiers."""

from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    derive_character_sheet,
    equip_inventory_item,
    validate_character_sheet,
)
from sagasmith_dnd.chase_engine import start_chase
from sagasmith_dnd.combat_engine import queue_combatant, resolve_actor_check, start_encounter
from sagasmith_dnd.lifecycle import apply_raise_dead_to_sheet, reduce_revival_ordeal_after_long_rest


class CountingRng:
    def __init__(self, values: tuple[int, ...] = (16, 4)) -> None:
        self.values = iter(values)
        self.calls = 0

    def randint(self, lower: int, upper: int) -> int:
        self.calls += 1
        value = next(self.values)
        assert lower <= value <= upper
        return value


def actor_for(case: str = "ordinary", *, edition: str = "2014") -> dict:
    sheet = default_character_sheet()
    sheet["edition"] = edition
    sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
    if case == "poisoned":
        sheet["conditions"] = ["poisoned"]
    if case in {"armor", "proficient_armor"}:
        sheet, item_id = add_inventory_item(sheet, {
            "id": "chain-mail", "name": "Chain mail", "kind": "armor", "weight_oz": 880,
            "mechanics": {
                "base_ac": 16, "category": "heavy", "dexterity_mode": "none",
                "strength_requirement": 13, "stealth_disadvantage": True,
            },
        })
        sheet = equip_inventory_item(sheet, item_id, "armor")
        if case == "proficient_armor":
            sheet["traits"]["proficiencies"]["armor"] = ["heavy armor"]
    if case in {"light_variant", "heavy_variant", "normal_load"}:
        sheet["inventory"]["encumbrance"]["mode"] = (
            "standard" if case == "normal_load" else "variant"
        )
        sheet, _ = add_inventory_item(sheet, {
            "id": "load", "name": "Load", "kind": "equipment",
            "weight_oz": 900 if case == "light_variant" else 1800,
        })
    if case.startswith("revival_"):
        sheet["combat"]["hp"]["value"] = 0
        sheet["conditions"] = ["dead"]
        sheet = apply_raise_dead_to_sheet(
            sheet, elapsed_days=1, soul_willing=True, body_intact=True,
            source_ref="dnd5e.content.srd2014.spell.raise-dead", source_actor_id="cleric",
        )["sheet"]
        for _ in range(int(case.rsplit("_", 1)[1])):
            sheet = reduce_revival_ordeal_after_long_rest(sheet)["sheet"]
    sheet = validate_character_sheet(sheet)
    return {"id": "subject", "sheet": sheet, "derived": derive_character_sheet(sheet)}


def settle(actor: dict, operation: str, rng: CountingRng, *, edition: str = "2014") -> dict:
    observer = actor_for(edition=edition)
    observer.update(id="observer", initiative=30)
    if operation == "start":
        return start_encounter([actor], ruleset=edition, rng=rng)["combatants"][0]
    if operation == "join":
        encounter = start_encounter([observer], ruleset=edition)
        before = deepcopy(encounter)
        result = queue_combatant(encounter, actor, rng=rng)["reinforcements"][0]
        assert encounter == before
        return result
    result = start_chase(
        [observer, actor], quarry_ids=["observer"], initial_distance_ft=60,
        ruleset=edition, rng=rng,
    )
    return next(item for item in result["participants"] if item["actor_id"] == "subject")


@pytest.mark.parametrize("operation", ["start", "join", "chase"])
@pytest.mark.parametrize(("case", "draws", "bonus"), [
    ("ordinary", 1, 0), ("poisoned", 2, 0), ("armor", 2, 0),
    ("proficient_armor", 1, 0), ("light_variant", 1, 0),
    ("heavy_variant", 2, 0), ("normal_load", 1, 0),
    ("revival_0", 1, -4), ("revival_1", 1, -3), ("revival_4", 1, 0),
])
def test_initiative_reuses_actual_dexterity_check_modifiers(
    operation: str, case: str, draws: int, bonus: int,
) -> None:
    actor = actor_for(case)
    before = deepcopy(actor)
    check = resolve_actor_check(
        actor, kind="ability", ability="dexterity", dc=10, rng=CountingRng(),
    )
    rng = CountingRng()
    result = settle(actor, operation, rng)
    assert check["rolls"] == ([16, 4] if draws == 2 else [16])
    assert check["total"] == (4 if draws == 2 else 16) + bonus
    assert result["initiative_roll"]["rolls"] == check["rolls"]
    assert result["initiative"] == check["total"]
    assert result["initiative_bonus"] == bonus
    assert rng.calls == draws
    assert actor == before


@pytest.mark.parametrize("case", ["poisoned", "armor", "heavy_variant"])
def test_initiative_advantage_cancels_shared_disadvantage(case: str) -> None:
    actor = actor_for(case)
    actor["initiative_advantage"] = True
    rng = CountingRng()
    result = settle(actor, "start", rng)
    assert result["initiative_roll"]["rolls"] == [16]
    assert result["initiative"] == 16
    assert rng.calls == 1


def test_initiative_preserves_custom_base_and_combines_effects_with_exhaustion() -> None:
    actor = actor_for("revival_0")
    actor["derived"]["initiative"] = 7
    actor["sheet"]["combat"]["exhaustion"] = 1
    result = settle(actor, "start", CountingRng())
    assert result["initiative_bonus"] == 3
    assert result["initiative"] == 7  # lower die 4 + authored base 7 - ordeal 4


@pytest.mark.parametrize("case", ["poisoned", "armor", "heavy_variant", "revival_0"])
def test_supplied_initiative_is_not_rolled_or_readjusted(case: str) -> None:
    actor = actor_for(case)
    actor["initiative"] = 19
    rng = CountingRng(())
    result = settle(actor, "start", rng)
    assert result["initiative"] == 19
    assert result["initiative_roll"] is None
    assert rng.calls == 0


@pytest.mark.parametrize("path", ["rolls.attack.bonus", "rolls.saving_throw.bonus"])
def test_initiative_excludes_noncheck_effect_paths(path: str) -> None:
    actor = actor_for("revival_0")
    actor["sheet"]["effects"][0]["changes"] = [{"path": path, "mode": "add", "value": -4}]
    assert settle(actor, "start", CountingRng())["initiative"] == 16


def test_initiative_excludes_inactive_effect_and_nondex_equipment_penalty() -> None:
    actor = actor_for("revival_0")
    actor["sheet"]["effects"][0]["active"] = False
    actor["derived"]["equipment_penalties"]["check_disadvantage_abilities"] = ["strength"]
    result = settle(actor, "start", CountingRng())
    assert result["initiative"] == 16
    assert result["initiative_roll"]["rolls"] == [16]


def test_all_rolled_participants_preflight_effect_values_before_any_rng() -> None:
    actor = actor_for("revival_0")
    for change in actor["sheet"]["effects"][0]["changes"]:
        if change["path"] == "rolls.ability_check.bonus":
            change["value"] = True
    first = actor_for()
    first["id"] = "first"
    before = deepcopy([first, actor])
    rng = CountingRng()
    with pytest.raises(ValueError):
        start_encounter([first, actor], rng=rng)
    assert rng.calls == 0
    assert [first, actor] == before


def test_2014_modifiers_do_not_silently_change_2024_initiative() -> None:
    actor = actor_for("poisoned", edition="2024")
    result = settle(actor, "start", CountingRng(), edition="2024")
    assert result["initiative_roll"]["rolls"] == [16]


def test_poisoned_initiative_keeps_lucky_replacement_and_reports_shared_boundary() -> None:
    actor = actor_for("poisoned")
    actor["sheet"]["content"]["features"] = [{
        "id": "dnd5e.content.srd2014.species-feature.lightfoot-lucky",
        "name": "Lucky", "source_key": "Lightfoot",
    }]
    rng = CountingRng((1, 7, 18))
    result = settle(actor, "start", rng)
    assert result["initiative_roll"]["rolls"] == [18, 7]
    assert result["initiative_roll"]["rerolls"] == [
        {"index": 0, "from": 1, "to": 18, "source": "halfling_lucky"},
    ]
    assert result["initiative"] == 7
    assert rng.calls == 3
    assert "dnd5e.core.initiative.ability_check_modifiers" in result["rule_boundary_ids"]


def test_revival_penalty_and_jack_of_all_trades_each_apply_once() -> None:
    actor = actor_for("revival_0")
    actor["sheet"]["progression"] = {
        "level": 2, "classes": [{"name": "Bard", "level": 2, "hit_die": 8}],
    }
    actor["sheet"]["abilities"]["dexterity"]["score"] = 14
    actor["sheet"]["content"]["features"] = [{
        "id": "dnd5e.content.srd2014.feature.bard-jack-of-all-trades",
        "name": "Jack of All Trades", "source_key": "Bard",
        "mechanic_refs": ["dnd5e.core.check.jack_of_all_trades"],
    }]
    actor["derived"] = derive_character_sheet(actor["sheet"])
    result = settle(actor, "start", CountingRng())
    assert result["initiative_bonus"] == -1  # Dexterity 2 + half proficiency 1 - ordeal 4
    assert result["initiative"] == 15
