import pytest

from sagasmith_dnd.combat_engine import CombatEngineError, _normalize_ruleset
from sagasmith_dnd.editions import (
    DEFAULT_CAMPAIGN_EDITION,
    DEFAULT_CHARACTER_EDITION,
    SUPPORTED_DND_EDITIONS,
    normalize_dnd_edition,
)
from sagasmith_dnd.lifecycle import validate_song_of_rest_source
from sagasmith_dnd.spells import _edition as normalize_spell_edition


def test_edition_aliases_share_one_canonical_parser() -> None:
    assert SUPPORTED_DND_EDITIONS == ("2014", "2024")
    assert DEFAULT_CHARACTER_EDITION == "2014"
    assert DEFAULT_CAMPAIGN_EDITION == "2024"
    assert normalize_dnd_edition("2014 rules") == "2014"
    assert normalize_dnd_edition("5.1") == "2014"
    assert normalize_dnd_edition("DND 5E 2024") == "2024"
    assert normalize_dnd_edition("5.2") == "2024"


def test_invalid_editions_never_silently_fall_back_between_engines() -> None:
    with pytest.raises(ValueError, match="edition must be 2014 or 2024"):
        normalize_dnd_edition("2030")
    with pytest.raises(CombatEngineError, match="ruleset must be 2014 or 2024"):
        _normalize_ruleset("2030")
    with pytest.raises(CombatEngineError, match="edition must be 2014 or 2024"):
        validate_song_of_rest_source({"edition": "2030"})
    with pytest.raises(CombatEngineError, match="edition must be 2014 or 2024"):
        normalize_spell_edition({"edition": "2030"})
