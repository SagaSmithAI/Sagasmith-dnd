from __future__ import annotations

import pytest

from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.combat_engine import queue_combatant, start_encounter
from sagasmith_dnd.steel_defender import (
    STEEL_DEFENDER_DEFLECT_ATTACK_MECHANIC_ID,
    STEEL_DEFENDER_DEFLECT_ATTACK_SOURCE,
    STEEL_DEFENDER_VIGILANT_MECHANIC_ID,
    SteelDefenderError,
    apply_deflect_attack_to_plan,
    begin_steel_defender_revival,
    bind_steel_defender_runtime_mechanics,
    check_deflect_attack_eligibility,
    check_deflect_attack_in_encounter,
    complete_steel_defender_revival,
    consume_deflect_attack_reaction,
    has_steel_defender_vigilant,
    kill_steel_defender_when_owner_dies,
    mending_steel_defender,
    repair_steel_defender,
)


class FixedRng:
    def __init__(self, *values: int) -> None:
        self.values = list(values)

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0)
        assert minimum <= value <= maximum
        return value


def _sheet(*, hp: int = 10, maximum: int = 30, species: str = "construct") -> dict:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["progression"]["species"] = species
    sheet["combat"]["hp"] = {"value": hp, "max": maximum, "temp": 0}
    sheet["content"]["activities"].append(
        {
            "id": "repair",
            "name": "Repair (3/Day)",
            "activation": {"type": "action"},
            "uses": {"max": 3, "value": 3, "unlimited": False},
        }
    )
    return sheet


def _owner() -> dict:
    owner = default_character_sheet()
    owner["edition"] = "2014"
    owner["inventory"]["items"].append(
        {"id": "smith-tools", "name": "Smith's Tools", "quantity": 1}
    )
    owner["spellcasting"]["spell_slots"] = {
        "1": {"value": 1, "max": 1, "unlimited": False},
        "2": {"value": 1, "max": 1, "unlimited": False},
    }
    return owner


def _relation(*, death_tick: int = 100) -> dict[str, object]:
    return {
        "owner_character_id": "owner",
        "dependent_actor_id": "defender",
        "relation_key": "steel_defender",
        "status": "dead",
        "death_elapsed_ticks": death_tick,
        "revival_started_elapsed_ticks": None,
        "revival_completes_elapsed_ticks": None,
    }


def test_repair_self_rolls_2d8_plus_pb_and_consumes_one_of_three_uses() -> None:
    defender = _sheet(hp=10, maximum=30)

    result = repair_steel_defender(
        defender,
        proficiency_bonus=3,
        rng=FixedRng(4, 5),
    )

    assert result["roll"]["total"] == 12
    assert result["healing"]["amount"] == 12
    assert result["target_sheet"]["combat"]["hp"]["value"] == 22
    assert result["defender_sheet"]["content"]["activities"][0]["uses"]["value"] == 2
    assert defender["combat"]["hp"]["value"] == 10


@pytest.mark.parametrize("target_kind", ["construct", "object"])
def test_repair_allows_construct_or_object_within_five_feet(target_kind: str) -> None:
    defender = _sheet(hp=30, maximum=30)
    target = _sheet(
        hp=1,
        maximum=20,
        species="construct" if target_kind == "construct" else "object",
    )

    result = repair_steel_defender(
        defender,
        target,
        proficiency_bonus=2,
        target_kind=target_kind,
        distance_ft=5,
        rng=FixedRng(1, 1),
    )

    assert result["target_sheet"]["combat"]["hp"]["value"] == 5
    assert result["defender_sheet"]["content"]["activities"][0]["uses"]["value"] == 2


def test_repair_rejects_living_nonconstruct_out_of_range_and_exhausted_use() -> None:
    defender = _sheet(hp=10, maximum=30)
    humanoid = _sheet(hp=1, maximum=20, species="humanoid")

    with pytest.raises(SteelDefenderError, match="construct"):
        repair_steel_defender(defender, humanoid, proficiency_bonus=2, target_kind="construct")
    with pytest.raises(SteelDefenderError, match="within 5"):
        repair_steel_defender(defender, proficiency_bonus=2, distance_ft=5.1)
    with pytest.raises(SteelDefenderError, match="non-negative number"):
        repair_steel_defender(defender, proficiency_bonus=2, distance_ft=float("nan"))

    defender["content"]["activities"][0]["uses"]["value"] = 0
    with pytest.raises(SteelDefenderError, match="no uses"):
        repair_steel_defender(defender, proficiency_bonus=2)


def test_mending_heals_only_living_defender_for_2d6() -> None:
    defender = _sheet(hp=10, maximum=30)

    result = mending_steel_defender(defender, rng=FixedRng(3, 4))

    assert result["roll"]["total"] == 7
    assert result["sheet"]["combat"]["hp"]["value"] == 17

    defender["conditions"] = ["dead"]
    with pytest.raises(SteelDefenderError, match="dead"):
        mending_steel_defender(defender, rng=FixedRng(1, 1))


def test_revival_pays_lowest_available_slot_and_completes_after_ten_ticks() -> None:
    owner = _owner()
    defender = _sheet(hp=0, maximum=30)
    defender["conditions"] = ["dead"]

    started = begin_steel_defender_revival(
        owner,
        defender,
        relation=_relation(),
        elapsed_ticks=100,
        distance_ft=5,
        slot_level=1,
    )

    assert started["status"] == "pending"
    assert started["payment"]["key"] == "1"
    assert started["owner_sheet"]["spellcasting"]["spell_slots"]["1"]["value"] == 0
    assert started["pending_revival"]["completes_elapsed_ticks"] == 110
    before_due = complete_steel_defender_revival(
        defender, started["pending_revival"], elapsed_ticks=109
    )
    assert before_due["status"] == "pending"
    completed = complete_steel_defender_revival(
        defender, started["pending_revival"], elapsed_ticks=110
    )
    assert completed["status"] == "committed"
    assert completed["sheet"]["combat"]["hp"]["value"] == 30
    assert "dead" not in completed["sheet"]["conditions"]


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("distance_ft", 6, "within 5"),
        ("death_tick", 99, "last hour"),
    ],
)
def test_revival_rejects_invalid_range_or_expired_death(
    field: str, value: int, message: str
) -> None:
    owner = _owner()
    defender = _sheet(hp=0)
    defender["conditions"] = ["dead"]
    kwargs = {
        "relation": _relation(),
        "elapsed_ticks": 700,
        "distance_ft": 5,
        "slot_level": 1,
    }
    if field == "death_tick":
        kwargs["relation"] = _relation(death_tick=value)
    else:
        kwargs[field] = value
    with pytest.raises(SteelDefenderError, match=message):
        begin_steel_defender_revival(owner, defender, **kwargs)


def test_owner_death_perishes_defender_immediately_without_hour_timer() -> None:
    owner = _owner()
    defender = _sheet(hp=12, maximum=30)

    unchanged = kill_steel_defender_when_owner_dies(owner, defender)
    assert unchanged["status"] == "unchanged"
    owner["conditions"] = ["dead"]
    perished = kill_steel_defender_when_owner_dies(owner, defender)

    assert perished["status"] == "perished"
    assert perished["sheet"]["combat"]["hp"]["value"] == 0
    assert "dead" in perished["sheet"]["conditions"]


def test_owner_death_cancels_a_pending_revival() -> None:
    owner = _owner()
    defender = _sheet(hp=0)
    defender["conditions"] = ["dead"]
    started = begin_steel_defender_revival(
        owner,
        defender,
        relation=_relation(death_tick=0),
        elapsed_ticks=0,
        distance_ft=0,
        slot_level=1,
    )
    owner["conditions"] = ["dead"]

    result = kill_steel_defender_when_owner_dies(
        owner,
        defender,
        pending_revival=started["pending_revival"],
    )

    assert result["status"] == "perished"
    assert result["pending_revival"] is None


def test_dead_owner_cannot_begin_a_revival() -> None:
    owner = _owner()
    owner["conditions"] = ["dead"]
    defender = _sheet(hp=0)
    defender["conditions"] = ["dead"]

    with pytest.raises(SteelDefenderError, match="incapacitated owner"):
        begin_steel_defender_revival(
            owner,
            defender,
            relation=_relation(),
            elapsed_ticks=100,
            distance_ft=0,
            slot_level=1,
        )


def _combatant(identifier: str, *, position: dict[str, float] | None = None) -> dict:
    return {
        "actor_id": identifier,
        "position": position,
        "turn_budget": {"reaction": 1},
        "conditions": [],
    }


def test_deflect_requires_other_target_visible_attacker_and_five_feet() -> None:
    defender = _combatant("defender", position={"x": 0, "y": 0})
    attacker = _combatant("attacker", position={"x": 1, "y": 0})
    target = _combatant("target", position={"x": 3, "y": 0})
    result = check_deflect_attack_eligibility(defender, attacker, target)
    assert result["eligible"] is True
    assert result["distance_ft"] == 5

    assert "target_is_defender" in check_deflect_attack_eligibility(
        defender, attacker, defender
    )["reasons"]
    attacker["position"] = {"x": 2, "y": 0}
    assert "attacker_not_within_5_ft" in check_deflect_attack_eligibility(
        defender, attacker, target
    )["reasons"]
    attacker["position"] = {"x": 1, "y": 0}
    attacker["hidden"] = True
    assert "attacker_not_visible" in check_deflect_attack_eligibility(
        defender, attacker, target
    )["reasons"]


def test_deflect_rejects_missing_spatial_evidence_reaction_and_incapacitation() -> None:
    defender = _combatant("defender")
    attacker = _combatant("attacker")
    target = _combatant("target")
    facts = {
        "defender_can_see_attacker": True,
        "attacker_within_5_ft_of_defender": True,
    }
    defender["turn_budget"]["reaction"] = 0
    result = check_deflect_attack_eligibility(
        defender, attacker, target, spatial_facts=facts
    )
    assert "reaction_unavailable" in result["reasons"]
    defender["turn_budget"]["reaction"] = 1
    defender["conditions"] = ["incapacitated"]
    assert "defender_incapacitated" in check_deflect_attack_eligibility(
        defender, attacker, target, spatial_facts=facts
    )["reasons"]
    assert "attacker_not_within_5_ft" in check_deflect_attack_eligibility(
        defender,
        attacker,
        target,
        spatial_facts={"defender_can_see_attacker": True},
    )["reasons"]


def test_deflect_plan_is_idempotent_and_reaction_payment_is_atomic() -> None:
    plan = {"attack_bonus": 5, "disadvantage": False}
    once = apply_deflect_attack_to_plan(plan, defender_id="defender")
    twice = apply_deflect_attack_to_plan(once, defender_id="defender")
    assert plan["disadvantage"] is False
    assert twice["disadvantage"] is True
    assert twice["disadvantage_sources"] == [STEEL_DEFENDER_DEFLECT_ATTACK_SOURCE]
    assert twice["deflect_attack"]["mechanic_id"] == STEEL_DEFENDER_DEFLECT_ATTACK_MECHANIC_ID

    encounter = {"combatants": [_combatant("defender")], "log": []}
    paid = consume_deflect_attack_reaction(encounter, defender_id="defender")
    assert encounter["combatants"][0]["turn_budget"]["reaction"] == 1
    assert paid["combatants"][0]["turn_budget"]["reaction"] == 0
    with pytest.raises(SteelDefenderError, match="no reaction"):
        consume_deflect_attack_reaction(paid, defender_id="defender")


def test_deflect_encounter_helper_uses_grid_positions_and_budget() -> None:
    encounter = {
        "combatants": [
            _combatant("defender", position={"x": 0, "y": 0}),
            _combatant("attacker", position={"x": 1, "y": 0}),
            _combatant("target", position={"x": 2, "y": 0}),
        ]
    }
    result = check_deflect_attack_in_encounter(
        encounter,
        defender_id="defender",
        attacker_id="attacker",
        target_id="target",
    )
    assert result["eligible"] is True

    encounter["battle_map"] = {"grid": {"cell_ft": 10}}
    result = check_deflect_attack_in_encounter(
        encounter,
        defender_id="defender",
        attacker_id="attacker",
        target_id="target",
    )
    assert result["eligible"] is False
    assert result["distance_ft"] == 10


def test_runtime_mechanics_bind_only_exact_steel_defender_cards() -> None:
    sheet = default_character_sheet()
    sheet["content"]["features"] = [
        {"id": "vigilant", "name": "Vigilant"},
        {"id": "near-vigilant", "name": "Vigilant Aura"},
    ]
    sheet["content"]["activities"] = [
        {"id": "deflect", "name": "Deflect Attack"},
        {"id": "near-deflect", "name": "Improved Deflect Attack"},
    ]

    bound = bind_steel_defender_runtime_mechanics(sheet)

    assert "mechanic_refs" not in sheet["content"]["features"][0]
    assert bound["content"]["features"][0]["mechanic_refs"] == [
        STEEL_DEFENDER_VIGILANT_MECHANIC_ID
    ]
    assert "mechanic_refs" not in bound["content"]["features"][1]
    assert bound["content"]["activities"][0]["mechanic_refs"] == [
        STEEL_DEFENDER_DEFLECT_ATTACK_MECHANIC_ID
    ]
    assert "mechanic_refs" not in bound["content"]["activities"][1]


def test_2014_vigilant_source_bound_defender_is_not_surprised() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["content"]["features"].append(
        {
            "id": "steel-defender-vigilant",
            "name": "Vigilant",
            "mechanic_refs": [STEEL_DEFENDER_VIGILANT_MECHANIC_ID],
        }
    )
    actor = {
        "id": "defender",
        "sheet": sheet,
        "derived": {"initiative": 0, "speed": {"walk": 30}},
        "surprised": True,
        "dependent_turn": {
            "kind": "steel_defender_2014",
            "owner_actor_id": "owner",
            "source_artifact_id": "steel-defender",
            "source_pack_id": "tashas",
            "source_pack_version": "1.0.0",
            "reviewed_expression_hash": "a" * 64,
        },
    }
    assert has_steel_defender_vigilant(actor) is True
    owner = {
        "id": "owner",
        "sheet": default_character_sheet(),
        "initiative": 20,
    }
    encounter = start_encounter([owner, actor], ruleset="2014")
    state = next(
        item for item in encounter["combatants"] if item["actor_id"] == "defender"
    )
    assert state["surprised"] is False
    assert state["vigilant"] is True
    assert state["turn_budget"]["reaction"] == 1
    assert state["turn_budget"]["main_action"] == 1
    assert state["turn_budget"]["movement"] == 30
    assert STEEL_DEFENDER_VIGILANT_MECHANIC_ID not in state["rule_boundary_ids"]

    queued = queue_combatant(start_encounter([owner], ruleset="2014"), actor)
    reinforcement = queued["reinforcements"][0]
    assert reinforcement["surprised"] is False
    assert reinforcement["vigilant"] is True
    assert reinforcement["turn_budget"]["reaction"] == 1


def test_vigilant_requires_dependent_turn_authority() -> None:
    sheet = default_character_sheet()
    sheet["content"]["features"].append(
        {
            "id": "forged-vigilant",
            "name": "Vigilant",
            "mechanic_refs": [STEEL_DEFENDER_VIGILANT_MECHANIC_ID],
        }
    )
    actor = {"id": "forged", "sheet": sheet, "surprised": True}

    assert has_steel_defender_vigilant(actor) is False
    encounter = start_encounter([actor], ruleset="2014")
    assert encounter["combatants"][0]["surprised"] is True
