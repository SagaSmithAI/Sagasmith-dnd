from sagasmith_dnd.combat_engine import (
    newly_ended_witch_bolt_tethers,
    reconcile_readied_spells,
    source_spell_resolution,
)
from sagasmith_dnd.spell_resolution import SPELL_RESOLUTION_MECHANIC_ID
from sagasmith_dnd.spells import end_tether_concentrations
from sagasmith_dnd.standard_spell_ids import CORE_WITCH_BOLT_MECHANIC_ID


def test_source_spell_resolution_requires_reviewed_core_binding() -> None:
    resolution = {"kind": "spell", "attack": {"kind": "spell_attack"}}
    sheet = {
        "content": {
            "spells": [
                {
                    "id": "spell-1",
                    "mechanic_refs": [SPELL_RESOLUTION_MECHANIC_ID],
                    "resolution": resolution,
                }
            ]
        }
    }
    assert source_spell_resolution(sheet, "spell-1") == resolution


def test_ended_tether_closes_exact_concentration_effect() -> None:
    before = {
        "ongoing_effects": [
            {
                "id": "tether-1",
                "active": True,
                "mechanic_id": CORE_WITCH_BOLT_MECHANIC_ID,
                "source_actor_id": "caster",
                "concentration_effect_id": "effect-1",
            }
        ]
    }
    after = {
        "ongoing_effects": [
            {
                **before["ongoing_effects"][0],
                "active": False,
                "ended_reason": "target_out_of_range",
            }
        ]
    }
    ended = newly_ended_witch_bolt_tethers(before, after, source_actor_id="caster")
    sheet = {
        "effects": [
            {
                "id": "effect-1",
                "name": "Witch Bolt",
                "kind": "concentration",
                "active": True,
                "concentration": True,
                "duration": {"period": "manual", "remaining": 0},
            }
        ]
    }
    result = end_tether_concentrations(sheet, ended)
    assert result["ended_effect_ids"] == ["effect-1"]
    assert result["sheet"]["effects"][0]["ended_reason"] == "target_out_of_range"


def test_reconcile_readied_spells_removes_stale_window_and_logs() -> None:
    encounter = {
        "readied": [
            {
                "id": "ready-1",
                "kind": "spell",
                "actor_id": "caster",
                "holding_effect_id": "effect-1",
            }
        ],
        "pending": [{"id": "window-1", "readied_id": "ready-1"}],
        "log": [],
    }
    assert reconcile_readied_spells(encounter, "caster", {"effects": []}) == ["ready-1"]
    assert encounter["readied"] == []
    assert encounter["pending"] == []
    assert encounter["log"][-1]["type"] == "readied_spell_dissipated"
