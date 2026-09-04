import random
from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.combat_engine import CombatEngineError, resolve_turn_undead_to_sheets


def _actor(actor_id: str, *, species: str, cleric: bool = False) -> dict:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["combat"]["hp"] = {"value": 10, "max": 10, "temp": 0}
    sheet["progression"]["species"] = species
    if cleric:
        sheet["progression"] = {
            **sheet["progression"],
            "level": 2,
            "classes": [{"name": "Cleric", "level": 2, "hit_die": 8}],
        }
        sheet["abilities"]["wisdom"]["score"] = 16
        sheet["spellcasting"]["ability"] = "wisdom"
        sheet["content"]["features"] = [
            {
                "id": "dnd5e.content.srd2014.feature.cleric-channel-divinity",
                "name": "Channel Divinity",
                "source_key": "Cleric",
                "rule_refs": ["bundled:srd2014/02_Classes/Cleric.md"],
                "choices": {"options": ["Turn Undead"]},
                "mechanic_refs": ["dnd5e.core.activity.turn_undead"],
            }
        ]
    return {"id": actor_id, "sheet": sheet, "derived": derive_character_sheet(sheet)}


@pytest.mark.parametrize(
    ("species", "is_undead"),
    [
        ("Undead", True),
        ("undead (shapechanger)", True),
        ("Undead Hunter", False),
        ("Undeadish", False),
        ("Humanoid (undead hunter)", False),
    ],
)
def test_turn_undead_requires_complete_creature_type(species: str, is_undead: bool) -> None:
    cleric = _actor("cleric", species="Human", cleric=True)
    target = _actor("target", species=species)
    before = deepcopy(target)
    rng = random.Random(7)
    rng_before = rng.getstate()
    if not is_undead:
        with pytest.raises(CombatEngineError, match="not Undead"):
            resolve_turn_undead_to_sheets(cleric, {"target": target}, rng=rng)
        assert target == before
        assert rng.getstate() == rng_before
        return
    resolved = resolve_turn_undead_to_sheets(cleric, {"target": target}, rng=rng)
    assert resolved["targets"][0]["turned"] is True
    assert "turned" in resolved["sheets"]["target"]["conditions"]
