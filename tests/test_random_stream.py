import pytest

from sagasmith_dnd.ability_generation import roll_ability_scores
from sagasmith_dnd.character_schema import validate_party_state
from sagasmith_dnd.engine import roll, roll_d20
from sagasmith_dnd.random_stream import (
    CampaignRandomStream,
    initial_random_stream,
    use_random_stream,
    validate_random_stream_state,
)


def _stream(position: int = 0) -> CampaignRandomStream:
    state = {"random_stream": initial_random_stream("campaign-seed")}
    state["random_stream"]["position"] = position
    return CampaignRandomStream.from_campaign_state(
        "campaign-1",
        state,
        operation="test.roll",
        idempotency_key="test-key",
    )


def test_same_seed_and_position_replay_the_same_mixed_dice_sequence() -> None:
    first = _stream()
    second = _stream()

    with use_random_stream(first):
        first_results = [
            roll("2d6+3"),
            roll_d20(advantage=True),
            roll_ability_scores("2014"),
        ]
    with use_random_stream(second):
        second_results = [
            roll("2d6+3"),
            roll_d20(advantage=True),
            roll_ability_scores("2014"),
        ]

    assert first_results == second_results
    assert first.position == second.position == 28
    assert first.receipt() == {
        "algorithm": "sha256-counter-v1",
        "seed_fingerprint": first.seed[:16],
        "position_before": 0,
        "position_after": 28,
        "draw_count": 28,
        "operation": "test.roll",
        "idempotency_key": "test-key",
    }


def test_restoring_a_position_replays_only_the_suffix() -> None:
    original = _stream()
    with use_random_stream(original):
        prefix = roll("3d8")
        checkpoint_position = original.position
        suffix = roll("4d10")

    restored = _stream(checkpoint_position)
    with use_random_stream(restored):
        replayed_suffix = roll("4d10")

    assert prefix.rolls
    assert replayed_suffix == suffix
    assert restored.position == original.position


def test_campaign_state_rejects_tampered_random_stream_documents() -> None:
    valid = initial_random_stream("campaign-seed")
    validated = validate_party_state({"random_stream": valid})
    assert validated["random_stream"] == validate_random_stream_state(valid)

    tampered = {**valid, "position": -1}
    with pytest.raises(ValueError, match="non-negative"):
        validate_party_state({"random_stream": tampered})

    with pytest.raises(ValueError, match="unsupported fields"):
        validate_party_state({"random_stream": {**valid, "caller_roll": 20}})
