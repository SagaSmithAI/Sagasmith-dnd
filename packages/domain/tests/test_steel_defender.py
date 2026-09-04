from __future__ import annotations

import pytest

from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.steel_defender import (
    SteelDefenderError,
    begin_steel_defender_revival,
    complete_steel_defender_revival,
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
