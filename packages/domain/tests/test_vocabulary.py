from sagasmith_dnd.vocabulary import (
    ADVANCEMENT_MODES,
    ATTACK_MODES,
    CAMPAIGN_GAME_PHASES,
    COMBAT_OUTCOME_STATUSES,
    EFFECTIVE_GAME_PHASES,
    REST_TYPES,
    WEAPON_HAND_SLOTS,
)


def test_cross_layer_runtime_vocabularies_are_explicit() -> None:
    assert ATTACK_MODES == {"melee", "ranged"}
    assert ADVANCEMENT_MODES == {"milestone", "xp"}
    assert CAMPAIGN_GAME_PHASES == {"lobby", "play"}
    assert EFFECTIVE_GAME_PHASES == {"combat", "lobby", "play"}
    assert REST_TYPES == {"long_rest", "short_rest"}
    assert WEAPON_HAND_SLOTS == {"main_hand", "off_hand"}
    assert COMBAT_OUTCOME_STATUSES == {
        "defeat",
        "interrupted",
        "surrender",
        "truce",
        "victory",
        "withdrawal",
    }
