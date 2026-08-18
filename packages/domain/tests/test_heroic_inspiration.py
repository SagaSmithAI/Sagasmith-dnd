from sagasmith_dnd.character_schema import default_character_sheet
from sagasmith_dnd.heroic_inspiration import (
    grant_heroic_inspiration,
    reroll_recorded_d20_result,
    spend_heroic_inspiration_reroll,
)


class _Rng:
    def __init__(self, value: int) -> None:
        self.value = value

    def randint(self, minimum: int, maximum: int) -> int:
        assert minimum <= self.value <= maximum
        return self.value


def test_heroic_inspiration_rerolls_any_recorded_die_and_requires_the_new_roll() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2024"
    sheet["combat"]["inspiration"] = True

    result = spend_heroic_inspiration_reroll(
        sheet,
        die_sides=8,
        original_roll=8,
        rng=_Rng(1),
    )

    assert result["original_roll"] == 8
    assert result["new_roll"] == 1
    assert result["must_use_new_roll"] is True
    assert result["sheet"]["combat"]["inspiration"] is False


def test_duplicate_heroic_inspiration_can_transfer_only_to_a_pc_without_it() -> None:
    source = default_character_sheet()
    source["edition"] = "2024"
    source["combat"]["inspiration"] = True
    recipient = default_character_sheet()
    recipient["edition"] = "2024"

    result = grant_heroic_inspiration(
        source,
        recipient_sheet=recipient,
        recipient_is_player_character=True,
    )

    assert result["outcome"] == "transferred"
    assert result["sheet"]["combat"]["inspiration"] is True
    assert result["recipient_sheet"]["combat"]["inspiration"] is True


def test_duplicate_heroic_inspiration_is_lost_without_a_legal_recipient() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2024"
    sheet["combat"]["inspiration"] = True

    result = grant_heroic_inspiration(sheet)

    assert result["outcome"] == "duplicate_lost"
    assert result["sheet"]["combat"]["inspiration"] is True


def test_recorded_d20_reroll_rebuilds_the_canonical_outcome() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2024"
    sheet["combat"]["inspiration"] = True
    result = reroll_recorded_d20_result(
        sheet,
        {
            "rolls": [4, 17],
            "roll_mode": "advantage",
            "natural": 17,
            "total": 22,
            "dc": 20,
            "success": True,
        },
        roll_index=1,
        expected_original_roll=17,
        rng=_Rng(1),
    )
    assert result["result"]["rolls"] == [4, 1]
    assert result["result"]["natural"] == 4
    assert result["result"]["total"] == 9
    assert result["result"]["success"] is False
    assert result["sheet"]["combat"]["inspiration"] is False
