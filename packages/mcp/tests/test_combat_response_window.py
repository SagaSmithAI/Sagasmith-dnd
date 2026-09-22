from copy import deepcopy
from types import SimpleNamespace

import pytest
from sagasmith_dnd_runtime.services.combat import CombatService


@pytest.mark.parametrize("dm", [True, False])
@pytest.mark.parametrize("count", [0, 3, 4, 1000])
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
    assert result["combat"]["log"] == before["combat"]["log"][-3:]
    for field in ("round", "turn_index", "combatants", "pending"):
        assert result["combat"][field] == before["combat"][field]
    if count > 3:
        assert result["combat"]["log_window"]["omitted"] == count - 3
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
    assert result["combat"] == {"current_turn": None, "log": [{"public": True}]}
    assert len(receipt["combat"]["log"]) == 100


def test_write_omits_only_repeated_preflight_cards_after_audience_filtering():
    manifest = {"checksum": "source-checksum", "ready": True, "groups": [{
        "key": "guards", "required_count": 2, "actor_ids": ["a", "b"],
        "source_excerpt": "Two guards occupy this room.",
        "actors": [{"id": "a", "name": "Guard A", "combat_card": {"hit_points": 11}},
                   {"id": "b", "name": "Guard B", "combat_card": {"hit_points": 11}}],
    }]}
    receipt = {"combat": {"participant_manifest": manifest,
                          "combatants": [{"actor_id": "a", "hit_points": 2}], "log": []}}
    before = deepcopy(receipt)
    service = SimpleNamespace(combat_audience_view=lambda *args: args[-1], is_dm=lambda *args: True)
    result = CombatService.combat_response(service, "campaign", "dm", receipt)
    assert receipt == before
    assert result["combat"]["combatants"] == before["combat"]["combatants"]
    group = result["combat"]["participant_manifest"]["groups"][0]
    assert group["actors"] == [{"id": "a", "name": "Guard A"}, {"id": "b", "name": "Guard B"}]
    assert group["required_count"] == 2
    assert group["source_excerpt"] == manifest["groups"][0]["source_excerpt"]
    window = result["combat"]["preflight_cards_window"]
    assert window["omitted"] == 2
    assert window["read_next"]["payload"] == {"detail": "full"}
    service.combat_audience_view = lambda *args: {"combatants": []}
    assert CombatService.combat_response(service, "campaign", "player", receipt)["combat"] == {
        "current_turn": None, "combatants": []
    }


def test_current_turn_uses_filtered_index_without_leaking_hidden_actor():
    receipt = {"combat": {"active": True, "turn_index": 1, "combatants": [
        {"actor_id": "first"}, {"actor_id": "hidden", "hit_points": 30}
    ]}}
    before = deepcopy(receipt)
    service = SimpleNamespace(combat_audience_view=lambda *args: args[-1],
                              is_dm=lambda *args: True)
    result = CombatService.combat_response(service, "campaign", "dm", receipt)
    assert result["combat"]["current_turn"] == {"actor_id": "hidden", "hit_points": 30}
    assert receipt == before
    service.combat_audience_view = lambda *args: {
        "active": True, "turn_index": None, "combatants": [{"actor_id": "first"}]
    }
    result = CombatService.combat_response(service, "campaign", "player", receipt)
    assert result["combat"]["current_turn"] is None
    assert "hidden" not in str(result)


def test_status_current_turn_does_not_select_an_actor_after_combat_ends():
    service = SimpleNamespace(combat_view=lambda *args: {
        "active": False, "turn_index": 0, "combatants": [{"actor_id": "past"}]
    })
    assert CombatService.combat_status(service, "campaign")["current_turn"] is None
