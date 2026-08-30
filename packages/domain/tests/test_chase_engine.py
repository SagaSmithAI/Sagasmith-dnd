from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.chase_engine import (
    CHASE_MANUAL_OUTCOME_STATUS_ORDER,
    advance_chase_turn,
    current_chase_participant,
    end_chase,
    start_chase,
)


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
) -> dict:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 20, "max": 20, "temp": 0}
    sheet["combat"]["speed"]["walk"] = speed
    sheet["abilities"]["constitution"]["score"] = constitution
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
        rng=_SequenceRng(1, 1, 20),
    )

    assert result["sheet"]["combat"]["hp"]["value"] == 0
    assert "unconscious" in result["sheet"]["conditions"]
    assert result["turn"]["moved_ft"] == 0
    assert result["chase"]["participants"][1]["position_ft"] == 100
    assert result["chase"]["participants"][1]["active"] is False
    assert result["chase"]["participants"][1]["dropped_reason"] == "incapacitated"


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
        rng=_SequenceRng(1, 20, 20),
    )

    assert result["sheet"]["combat"]["exhaustion"] == 6
    assert "dead" in result["sheet"]["conditions"]
    assert result["chase"]["participants"][0]["active"] is False
    assert result["chase"]["participants"][0]["dropped_reason"] == "exhaustion_death"


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
