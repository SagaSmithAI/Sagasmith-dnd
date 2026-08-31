import pytest

from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.chase_engine import (
    CHASE_MANUAL_OUTCOME_STATUS_ORDER,
    advance_chase_turn,
    current_chase_participant,
    end_chase,
    start_chase,
)
from sagasmith_dnd.combat_engine import CombatEngineError


class _SequenceRng:
    def __init__(self, *values: int) -> None:
        self.values = list(values)

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0)
        assert minimum <= value <= maximum
        return value


def _actor(
    identifier: str,
    *,
    initiative: int,
    speed: int = 30,
    constitution: int = 10,
    passive_perception: int | None = None,
) -> dict:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
    sheet["combat"]["speed"]["walk"] = speed
    sheet["abilities"]["constitution"]["score"] = constitution
    if passive_perception is not None:
        sheet["traits"]["senses"]["passive_perception_bonus"] = passive_perception - 10
    return {
        "id": identifier,
        "name": identifier,
        "sheet": sheet,
        "derived": derive_character_sheet(sheet),
        "initiative": initiative,
        "tie_breaker": 0,
    }


def _give_magic_resistance(actor: dict) -> None:
    actor["sheet"]["content"]["features"].append(
        {
            "id": "magic-resistance",
            "name": "Magic Resistance",
            "choices": {
                "source_trait": {
                    "kind": "magic_resistance",
                    "trigger": "saving_throw",
                    "save_source_kinds": ["spell", "magical_effect"],
                    "grants": "advantage",
                    "automatic": True,
                    "source_excerpt": (
                        f"The {actor['name']} has advantage on saving throws "
                        "against spells and other magical effects."
                    ),
                }
            },
        }
    )
    actor["derived"] = derive_character_sheet(actor["sheet"])


def _guard_chase(*, armor_class: int = 10) -> tuple[dict, dict]:
    pursuer = _actor("pursuer", initiative=20)
    quarry = _actor("quarry", initiative=10)
    quarry["sheet"]["combat"]["ac"] = {
        "base": armor_class,
        "override": armor_class,
    }
    quarry["derived"] = derive_character_sheet(quarry["sheet"])
    chase = start_chase(
        [pursuer, quarry],
        quarry_ids=["quarry"],
        initial_distance_ft=100,
    )
    chase["turn_index"] = 1
    chase["pending_complication"] = {
        "number": 9,
        "source_actor_id": "pursuer",
        "rolled_round": 1,
    }
    return chase, quarry


def test_module_close_transition_ends_chase() -> None:
    pursuer = _actor("pursuer", initiative=20)
    quarry = _actor("quarry", initiative=10)
    chase = start_chase(
        [pursuer, quarry],
        quarry_ids=["quarry"],
        initial_distance_ft=60,
        close_transition={
            "distance_ft": 0,
            "status": "destination_reached",
            "summary": "The quarry ducks into the old tower.",
        },
    )

    assert chase["mode"] == "theater_of_the_mind"
    assert current_chase_participant(chase)["actor_id"] == "pursuer"
    assert "battle_map" not in chase

    result = advance_chase_turn(
        chase,
        pursuer,
        actor_id_value="pursuer",
        action="dash",
        rng=_SequenceRng(20),
    )

    assert result["turn"]["moved_ft"] == 60
    assert result["chase"]["active"] is False
    assert result["chase"]["outcome"]["status"] == "destination_reached"


def test_source_reviewed_chase_speed_adjustment_is_contextual() -> None:
    pursuer = _actor("pursuer", initiative=20)
    quarry = _actor("quarry", initiative=10)
    quarry["chase_speed_adjustment_ft"] = -10
    quarry["chase_speed_source_excerpt"] = (
        "While dragging the heavily laden sack, the quarry suffers a "
        "10-foot reduction to its speed."
    )

    chase = start_chase(
        [pursuer, quarry],
        quarry_ids=["quarry"],
        initial_distance_ft=100,
    )
    quarry_state = next(item for item in chase["participants"] if item["actor_id"] == "quarry")

    assert quarry_state["base_speed_ft"] == 30
    assert quarry_state["speed_adjustment_ft"] == -10
    assert quarry_state["speed_ft"] == 20
    assert quarry_state["speed_source_excerpt"] == (quarry["chase_speed_source_excerpt"])
    assert quarry["derived"]["speed"]["walk"] == 30


def test_chase_preserves_an_explicit_zero_walk_speed() -> None:
    pursuer = _actor("pursuer", initiative=20)
    quarry = _actor("quarry", initiative=10, speed=0)

    chase = start_chase(
        [pursuer, quarry],
        quarry_ids=["quarry"],
        initial_distance_ft=100,
    )
    quarry_state = next(item for item in chase["participants"] if item["actor_id"] == "quarry")

    assert quarry["derived"]["speed"]["walk"] == 0
    assert quarry_state["base_speed_ft"] == 0
    assert quarry_state["speed_ft"] == 0


def test_chase_preserves_non_positive_passive_perception_for_escape_checks() -> None:
    pursuer = _actor("pursuer", initiative=20)
    pursuer["sheet"]["abilities"]["wisdom"]["score"] = 1
    pursuer["sheet"]["traits"]["senses"]["passive_perception_bonus"] = -5
    pursuer["derived"] = derive_character_sheet(pursuer["sheet"])
    quarry = _actor("quarry", initiative=10)

    zero_chase = start_chase(
        [pursuer, quarry],
        quarry_ids=["quarry"],
        initial_distance_ft=100,
    )
    assert pursuer["derived"]["passive_perception"] == 0
    assert zero_chase["pursuer_passive_perception_max"] == 0
    assert zero_chase["participants"][0]["passive_perception"] == 0

    pursuer["sheet"]["traits"]["senses"]["passive_perception_bonus"] = -6
    pursuer["derived"] = derive_character_sheet(pursuer["sheet"])
    negative_chase = start_chase(
        [pursuer, quarry],
        quarry_ids=["quarry"],
        initial_distance_ft=100,
    )
    negative_chase["turn_index"] = 1
    result = advance_chase_turn(
        negative_chase,
        quarry,
        actor_id_value="quarry",
        action="move",
        quarry_visibility={"quarry": False},
        quarry_actors={"quarry": quarry},
        rng=_SequenceRng(1),
    )

    escape = result["turn"]["escape_checks"][0]
    assert pursuer["derived"]["passive_perception"] == -1
    assert negative_chase["pursuer_passive_perception_max"] == -1
    assert escape["passive_perception_max"] == -1
    assert escape["check"]["dc"] == 0
    assert result["chase"]["pending_complication"] is None
    assert result["turn"]["next_complication_roll"] is None
    assert result["turn"]["next_complication"] is None


@pytest.mark.parametrize(
    ("drop_kind", "dropped_reason"),
    [
        ("incapacitated", "incapacitated"),
        ("voluntary", "voluntary"),
        ("exhaustion", "exhaustion_speed_zero"),
    ],
)
def test_escape_dc_uses_only_active_pursuers(
    drop_kind: str,
    dropped_reason: str,
) -> None:
    high = _actor("high", initiative=30, passive_perception=20)
    low = _actor("low", initiative=20, passive_perception=10)
    quarry = _actor("quarry", initiative=10)
    chase = start_chase(
        [high, low, quarry],
        quarry_ids=["quarry"],
        initial_distance_ft=100,
    )
    if drop_kind == "incapacitated":
        high["sheet"]["combat"]["hp"]["value"] = 0
        dropped = advance_chase_turn(
            chase,
            high,
            actor_id_value="high",
            action="move",
            rng=_SequenceRng(20),
        )
    elif drop_kind == "exhaustion":
        high["sheet"]["combat"]["exhaustion"] = 4
        chase["participants"][0]["dash_count"] = chase["participants"][0][
            "free_dash_limit"
        ]
        dropped = advance_chase_turn(
            chase,
            high,
            actor_id_value="high",
            action="dash",
            rng=_SequenceRng(1, 20, 20),
        )
    else:
        dropped = advance_chase_turn(
            chase,
            high,
            actor_id_value="high",
            action="drop_out",
            rng=_SequenceRng(20),
        )

    high_state = next(
        item for item in dropped["chase"]["participants"] if item["actor_id"] == "high"
    )
    assert high_state["active"] is False
    assert high_state["dropped_reason"] == dropped_reason
    assert dropped["chase"]["pursuer_passive_perception_max"] == 10

    low_turn = advance_chase_turn(
        dropped["chase"],
        low,
        actor_id_value="low",
        action="move",
        rng=_SequenceRng(20),
    )
    escaped = advance_chase_turn(
        low_turn["chase"],
        quarry,
        actor_id_value="quarry",
        action="move",
        quarry_visibility={"quarry": False},
        quarry_actors={"quarry": quarry},
        rng=_SequenceRng(12),
    )

    escape = escaped["turn"]["escape_checks"][0]
    assert escape["passive_perception_max"] == 10
    assert escape["check"]["dc"] == 11
    assert escape["check"]["total"] == 12
    assert escape["escaped"] is True
    assert escaped["chase"]["pending_complication"] is None
    assert escaped["turn"]["next_complication_roll"] is None
    assert escaped["turn"]["next_complication"] is None


def test_last_active_pursuer_dropout_ends_chase_without_escape_check() -> None:
    pursuer = _actor("pursuer", initiative=20, passive_perception=15)
    quarry = _actor("quarry", initiative=10)
    chase = start_chase(
        [pursuer, quarry],
        quarry_ids=["quarry"],
        initial_distance_ft=100,
    )

    dropped = advance_chase_turn(
        chase,
        pursuer,
        actor_id_value="pursuer",
        action="drop_out",
        rng=_SequenceRng(),
    )

    assert dropped["chase"]["active"] is False
    assert dropped["chase"]["pursuer_passive_perception_max"] is None
    assert dropped["chase"]["pending_complication"] is None
    assert dropped["chase"]["outcome"]["status"] == "quarry_escaped"
    assert dropped["turn"]["next_complication_roll"] is None
    assert dropped["turn"]["next_complication"] is None
    assert dropped["turn"]["escape_checks"] == []


def test_last_active_quarry_dropout_ends_chase_without_next_complication() -> None:
    quarry = _actor("quarry", initiative=20)
    pursuer = _actor("pursuer", initiative=10, passive_perception=15)
    chase = start_chase(
        [quarry, pursuer],
        quarry_ids=["quarry"],
        initial_distance_ft=100,
    )

    dropped = advance_chase_turn(
        chase,
        quarry,
        actor_id_value="quarry",
        action="drop_out",
        rng=_SequenceRng(),
    )

    assert dropped["chase"]["active"] is False
    assert dropped["chase"]["pending_complication"] is None
    assert dropped["chase"]["outcome"]["status"] == "quarry_incapacitated"
    assert dropped["turn"]["next_complication_roll"] is None
    assert dropped["turn"]["next_complication"] is None
    assert dropped["turn"]["escape_checks"] == []


def test_legacy_chase_cache_is_rejected_after_pursuer_set_changes() -> None:
    high = _actor("high", initiative=30, passive_perception=20)
    low = _actor("low", initiative=20, passive_perception=10)
    quarry = _actor("quarry", initiative=10)
    chase = start_chase(
        [high, low, quarry],
        quarry_ids=["quarry"],
        initial_distance_ft=100,
    )
    for participant in chase["participants"]:
        participant.pop("passive_perception")
    chase["participants"][0]["active"] = False
    chase["participants"][0]["dropped_reason"] = "voluntary"
    chase["turn_index"] = 2

    with pytest.raises(CombatEngineError, match="legacy chase participants"):
        advance_chase_turn(
            chase,
            quarry,
            actor_id_value="quarry",
            action="move",
            quarry_visibility={"quarry": False},
            quarry_actors={"quarry": quarry},
            rng=_SequenceRng(20),
        )


def test_legacy_chase_cache_remains_valid_while_all_pursuers_are_active() -> None:
    high = _actor("high", initiative=30, passive_perception=20)
    low = _actor("low", initiative=20, passive_perception=10)
    quarry = _actor("quarry", initiative=10)
    chase = start_chase(
        [high, low, quarry],
        quarry_ids=["quarry"],
        initial_distance_ft=100,
    )
    for participant in chase["participants"]:
        participant.pop("passive_perception")
    chase["turn_index"] = 2

    result = advance_chase_turn(
        chase,
        quarry,
        actor_id_value="quarry",
        action="move",
        quarry_visibility={"quarry": False},
        quarry_actors={"quarry": quarry},
        rng=_SequenceRng(12, 7),
    )

    escape = result["turn"]["escape_checks"][0]
    assert escape["passive_perception_max"] == 20
    assert escape["check"]["dc"] == 21
    assert escape["escaped"] is False
    assert result["turn"]["next_complication_roll"]["total"] == 7
    assert result["turn"]["next_complication"]["number"] == 7
    assert result["chase"]["pending_complication"]["number"] == 7


def test_urban_complication_affects_next_participant() -> None:
    pursuer = _actor("pursuer", initiative=20)
    quarry = _actor("quarry", initiative=10)
    chase = start_chase(
        [pursuer, quarry],
        quarry_ids=["quarry"],
        initial_distance_ft=100,
    )
    first = advance_chase_turn(
        chase,
        pursuer,
        actor_id_value="pursuer",
        action="dash",
        rng=_SequenceRng(1),
    )

    assert first["chase"]["pending_complication"]["number"] == 1
    assert first["chase"]["pending_complication"]["source_actor_id"] == "pursuer"

    second = advance_chase_turn(
        first["chase"],
        quarry,
        actor_id_value="quarry",
        action="dash",
        complication_choice="acrobatics",
        rng=_SequenceRng(2, 20),
    )

    assert second["turn"]["complication"]["affected_actor_id"] == "quarry"
    assert second["turn"]["complication"]["check"]["success"] is False
    assert second["turn"]["movement_penalty_ft"] == 10
    assert second["turn"]["moved_ft"] == 50


def test_urban_guard_attack_preserves_zero_armor_class() -> None:
    chase, quarry = _guard_chase(armor_class=0)

    result = advance_chase_turn(
        chase,
        quarry,
        actor_id_value="quarry",
        action="move",
        rng=_SequenceRng(5, 1, 20),
    )

    guard_attack = result["turn"]["guard_attack"]
    assert guard_attack["target_ac"] == 0
    assert guard_attack["total"] == 8
    assert guard_attack["hit"] is True
    assert guard_attack["damage"]["total"] == 2


def test_urban_guard_attack_applies_natural_one_and_ordinary_miss_rules() -> None:
    natural_one_chase, quarry = _guard_chase(armor_class=0)
    natural_one = advance_chase_turn(
        natural_one_chase,
        quarry,
        actor_id_value="quarry",
        action="move",
        rng=_SequenceRng(1, 20),
    )["turn"]["guard_attack"]

    assert natural_one["attack_roll"]["fumble"] is True
    assert natural_one["hit"] is False
    assert natural_one["damage"] is None

    ordinary_chase, quarry = _guard_chase(armor_class=10)
    ordinary_miss = advance_chase_turn(
        ordinary_chase,
        quarry,
        actor_id_value="quarry",
        action="move",
        rng=_SequenceRng(6, 20),
    )["turn"]["guard_attack"]

    assert ordinary_miss["attack_roll"]["fumble"] is False
    assert ordinary_miss["total"] == 9
    assert ordinary_miss["hit"] is False
    assert ordinary_miss["damage"] is None


def test_urban_guard_attack_natural_twenty_hits_and_doubles_the_damage_die() -> None:
    chase, quarry = _guard_chase(armor_class=30)

    result = advance_chase_turn(
        chase,
        quarry,
        actor_id_value="quarry",
        action="move",
        rng=_SequenceRng(20, 3, 4, 20),
    )

    guard_attack = result["turn"]["guard_attack"]
    assert guard_attack["attack_roll"]["critical"] is True
    assert guard_attack["total"] == 23
    assert guard_attack["target_ac"] == 30
    assert guard_attack["hit"] is True
    assert guard_attack["damage"]["expression"] == "2d6+1"
    assert guard_attack["damage"]["rolls"] == [3, 4]
    assert guard_attack["damage"]["total"] == 8
    assert result["sheet"]["combat"]["hp"]["value"] == 12


def test_urban_complication_incapacitation_prevents_movement() -> None:
    pursuer = _actor("pursuer", initiative=20)
    quarry = _actor("quarry", initiative=10)
    quarry["sheet"]["combat"]["hp"] = {"value": 1, "max": 20, "temp": 0}
    quarry["derived"] = derive_character_sheet(quarry["sheet"])
    chase = start_chase(
        [pursuer, quarry],
        quarry_ids=["quarry"],
        initial_distance_ft=100,
    )
    chase["turn_index"] = 1
    chase["pending_complication"] = {
        "number": 6,
        "source_actor_id": "pursuer",
        "rolled_round": 1,
    }

    result = advance_chase_turn(
        chase,
        quarry,
        actor_id_value="quarry",
        action="move",
        complication_choice="acrobatics",
        rng=_SequenceRng(1, 1),
    )

    assert result["sheet"]["combat"]["hp"]["value"] == 0
    assert "unconscious" in result["sheet"]["conditions"]
    assert result["turn"]["moved_ft"] == 0
    assert result["chase"]["participants"][1]["position_ft"] == 100
    assert result["chase"]["participants"][1]["active"] is False
    assert result["chase"]["participants"][1]["dropped_reason"] == "incapacitated"
    assert result["chase"]["pending_complication"] is None
    assert result["chase"]["outcome"]["status"] == "quarry_incapacitated"
    assert result["turn"]["next_complication_roll"] is None
    assert result["turn"]["next_complication"] is None


def test_chase_prone_changes_share_immunity_and_effect_ownership() -> None:
    pursuer = _actor("pursuer", initiative=20)
    quarry = _actor("quarry", initiative=10)
    _give_magic_resistance(quarry)
    quarry["sheet"]["traits"]["condition_immunities"] = ["prone"]
    quarry["derived"] = derive_character_sheet(quarry["sheet"])
    chase = start_chase(
        [pursuer, quarry],
        quarry_ids=["quarry"],
        initial_distance_ft=100,
    )
    chase["turn_index"] = 1
    chase["pending_complication"] = {
        "number": 3,
        "source_actor_id": "pursuer",
        "affected_actor_id": "quarry",
    }

    immune = advance_chase_turn(
        chase,
        quarry,
        actor_id_value="quarry",
        action="move",
        complication_choice="strength",
        rng=_SequenceRng(1, 20),
    )

    assert immune["turn"]["complication"]["knocked_prone"] is False
    assert immune["turn"]["complication"]["check"]["roll_mode"] == "normal"
    assert "prone" not in immune["sheet"]["conditions"]

    pursuer["sheet"]["conditions"] = ["prone"]
    pursuer["sheet"]["effects"] = [
        {
            "id": "restraining-prone-effect",
            "kind": "timed_conditions",
            "active": True,
            "changes": [{"path": "conditions", "mode": "add", "value": "prone"}],
        }
    ]
    pursuer["derived"] = derive_character_sheet(pursuer["sheet"])
    owned = start_chase(
        [pursuer, _actor("other-quarry", initiative=10)],
        quarry_ids=["other-quarry"],
        initial_distance_ft=100,
    )
    remained = advance_chase_turn(
        owned,
        pursuer,
        actor_id_value="pursuer",
        action="move",
        stand_from_prone=True,
    )

    assert "prone" in remained["sheet"]["conditions"]
    assert remained["turn"]["moved_ft"] == 15


def test_extra_dash_uses_constitution_check_and_exhaustion() -> None:
    pursuer = _actor("pursuer", initiative=20, constitution=10)
    quarry = _actor("quarry", initiative=10)
    chase = start_chase(
        [pursuer, quarry],
        quarry_ids=["quarry"],
        initial_distance_ft=100,
    )
    chase["participants"][0]["dash_count"] = chase["participants"][0]["free_dash_limit"]

    result = advance_chase_turn(
        chase,
        pursuer,
        actor_id_value="pursuer",
        action="dash",
        rng=_SequenceRng(1, 20, 20),
    )

    assert result["turn"]["dash_check"]["success"] is False
    assert result["turn"]["exhaustion_gained"] == 1
    assert result["sheet"]["combat"]["exhaustion"] == 1
    assert result["chase"]["participants"][0]["chase_exhaustion"] == 1


def test_chase_exhaustion_level_six_marks_the_actor_dead() -> None:
    pursuer = _actor("pursuer", initiative=20, constitution=10)
    pursuer["sheet"]["combat"]["exhaustion"] = 5
    pursuer["derived"] = derive_character_sheet(pursuer["sheet"])
    quarry = _actor("quarry", initiative=10)
    chase = start_chase(
        [pursuer, quarry],
        quarry_ids=["quarry"],
        initial_distance_ft=100,
    )
    chase["participants"][0]["dash_count"] = chase["participants"][0]["free_dash_limit"]

    result = advance_chase_turn(
        chase,
        pursuer,
        actor_id_value="pursuer",
        action="dash",
        rng=_SequenceRng(1, 20),
    )

    assert result["sheet"]["combat"]["exhaustion"] == 6
    assert "dead" in result["sheet"]["conditions"]
    assert result["chase"]["participants"][0]["active"] is False
    assert result["chase"]["participants"][0]["dropped_reason"] == "exhaustion_death"
    assert result["chase"]["pending_complication"] is None
    assert result["turn"]["next_complication_roll"] is None
    assert result["turn"]["next_complication"] is None


def test_inactive_participants_are_skipped_when_turn_order_wraps() -> None:
    first_pursuer = _actor("first-pursuer", initiative=30)
    second_pursuer = _actor("second-pursuer", initiative=20)
    quarry = _actor("quarry", initiative=10)
    chase = start_chase(
        [first_pursuer, second_pursuer, quarry],
        quarry_ids=["quarry"],
        initial_distance_ft=100,
    )

    first = advance_chase_turn(
        chase,
        first_pursuer,
        actor_id_value="first-pursuer",
        action="drop_out",
        rng=_SequenceRng(20),
    )
    second = advance_chase_turn(
        first["chase"],
        second_pursuer,
        actor_id_value="second-pursuer",
        action="move",
        rng=_SequenceRng(20),
    )
    wrapped = advance_chase_turn(
        second["chase"],
        quarry,
        actor_id_value="quarry",
        action="move",
        rng=_SequenceRng(20),
    )

    assert wrapped["chase"]["active"] is True
    assert wrapped["chase"]["round"] == 2
    assert current_chase_participant(wrapped["chase"])["actor_id"] == "second-pursuer"


def test_starting_exhaustion_halves_chase_speed_only_once() -> None:
    pursuer = _actor("pursuer", initiative=20)
    pursuer["sheet"]["combat"]["exhaustion"] = 2
    pursuer["derived"] = derive_character_sheet(pursuer["sheet"])
    quarry = _actor("quarry", initiative=10)
    chase = start_chase(
        [pursuer, quarry],
        quarry_ids=["quarry"],
        initial_distance_ft=100,
    )

    result = advance_chase_turn(
        chase,
        pursuer,
        actor_id_value="pursuer",
        action="dash",
        rng=_SequenceRng(20),
    )

    assert chase["participants"][0]["speed_ft"] == 30
    assert result["turn"]["speed_ft"] == 15
    assert result["turn"]["moved_ft"] == 30


def test_manual_chase_endings_share_one_public_status_contract() -> None:
    chase = start_chase(
        [_actor("pursuer", initiative=20), _actor("quarry", initiative=10)],
        quarry_ids=["quarry"],
        initial_distance_ft=100,
    )

    for status in CHASE_MANUAL_OUTCOME_STATUS_ORDER:
        ended = end_chase(chase, status=status, summary=f"Ended as {status}.")
        assert ended["active"] is False
        assert ended["outcome"]["status"] == status
