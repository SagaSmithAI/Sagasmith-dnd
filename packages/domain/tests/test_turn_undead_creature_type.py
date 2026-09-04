import random
from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.combat_engine import CombatEngineError, resolve_turn_undead_to_sheets


def _actor(actor_id: str, *, species: str, cleric: bool = False, edition: str = "2014") -> dict:
    sheet = default_character_sheet()
    sheet["edition"] = edition
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
                "id": f"dnd5e.content.srd{edition}.feature.cleric-channel-divinity",
                "name": "Channel Divinity",
                "source_key": "Cleric",
                "rule_refs": [f"bundled:srd{edition}/02_Classes/Cleric.md"],
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
@pytest.mark.parametrize("edition", ["2014", "2024"])
def test_turn_undead_requires_complete_creature_type(
    species: str, is_undead: bool, edition: str
) -> None:
    cleric = _actor("cleric", species="Human", cleric=True, edition=edition)
    target = _actor("target", species=species, edition=edition)
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
    expected = {"turned"} if edition == "2014" else {"frightened", "incapacitated"}
    assert expected <= set(resolved["sheets"]["target"]["conditions"])


def test_turn_undead_prevalidates_mixed_targets_before_any_save_roll() -> None:
    cleric = _actor("cleric", species="Human", cleric=True)
    valid = _actor("valid", species="Undead")
    invalid = _actor("invalid", species="Undead Hunter")
    before = deepcopy({"valid": valid, "invalid": invalid})
    rng = random.Random(7)
    rng_before = rng.getstate()
    with pytest.raises(CombatEngineError, match="not Undead"):
        resolve_turn_undead_to_sheets(cleric, {"valid": valid, "invalid": invalid}, rng=rng)
    assert {"valid": valid, "invalid": invalid} == before
    assert rng.getstate() == rng_before


def test_turn_undead_prevalidates_before_2024_sear_roll() -> None:
    cleric = _actor("cleric", species="Human", cleric=True, edition="2024")
    cleric["sheet"]["progression"]["level"] = 5
    cleric["sheet"]["progression"]["classes"] = [
        {"name": "Cleric", "level": 5, "hit_die": 8}
    ]
    cleric["sheet"]["content"]["features"].append(
        {
            "id": "dnd5e.content.srd2024.feature.cleric-sear-undead",
            "name": "Sear Undead",
            "source_key": "Cleric",
            "rule_refs": ["bundled:srd2024/02_Classes/Cleric.md"],
            "mechanic_refs": ["dnd5e.core.activity.sear_undead"],
        }
    )
    cleric["derived"] = derive_character_sheet(cleric["sheet"])
    valid = _actor("valid", species="Undead", edition="2024")
    valid["sheet"]["combat"]["hp"] = {"value": 100, "max": 100, "temp": 0}
    valid["derived"] = derive_character_sheet(valid["sheet"])
    sear_success = resolve_turn_undead_to_sheets(
        cleric, {"valid": valid}, sear_undead=True, rng=random.Random(7)
    )
    assert sear_success["sear_undead"]["total"] > 0
    invalid = _actor("invalid", species="Humanoid (undead hunter)", edition="2024")
    before = deepcopy({"valid": valid, "invalid": invalid})
    rng = random.Random(7)
    rng_before = rng.getstate()
    with pytest.raises(CombatEngineError, match="not Undead"):
        resolve_turn_undead_to_sheets(
            cleric, {"valid": valid, "invalid": invalid}, sear_undead=True, rng=rng
        )
    assert {"valid": valid, "invalid": invalid} == before
    assert rng.getstate() == rng_before
