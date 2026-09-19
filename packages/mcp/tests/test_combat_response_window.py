from copy import deepcopy
from types import SimpleNamespace

import pytest
from sagasmith_dnd_runtime.services.combat import CombatService


@pytest.mark.parametrize("dm", [True, False])
@pytest.mark.parametrize("count", [0, 10, 11, 1000])
def test_write_window_preserves_receipt_and_tactical_state(dm, count):
    receipt = {
        "status": "committed",
        "campaign_revision": 42,
        "combat": {
            "round": 7,
            "turn_index": 1,
            "combatants": [{"actor_id": "pc", "hit_points": 1}],
            "pending": [{"id": "reaction-required"}],
            "log": [{"sequence": i} for i in range(count)],
        },
    }
    before = deepcopy(receipt)
    service = SimpleNamespace(
        combat_audience_view=lambda campaign, principal, encounter: encounter,
        is_dm=lambda campaign, principal: dm,
    )
    result = CombatService.combat_response(service, "campaign", "principal", receipt)
    assert receipt == before
    assert result == CombatService.combat_response(service, "campaign", "principal", receipt)
    assert result["combat"]["log"] == before["combat"]["log"][-10:]
    for field in ("round", "turn_index", "combatants", "pending"):
        assert result["combat"][field] == before["combat"][field]
    if count > 10:
        assert result["combat"]["log_window"]["omitted"] == count - 10
        assert result["combat"]["log_window"]["read_next"]["payload"] == {"detail": "full"}
    else:
        assert "log_window" not in result["combat"]


def test_window_counts_only_audience_visible_events():
    receipt = {"combat": {"log": [{"secret": True}] * 100}}
    service = SimpleNamespace(
        combat_audience_view=lambda *args: {"log": [{"public": True}]},
        is_dm=lambda *args: False,
    )
    result = CombatService.combat_response(service, "campaign", "player", receipt)
    assert result["combat"] == {"log": [{"public": True}]}
    assert len(receipt["combat"]["log"]) == 100
