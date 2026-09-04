from copy import deepcopy
from pathlib import Path

import pytest

from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.combat_engine import resolve_save_damage_to_sheet
from sagasmith_dnd.core_content import build_srd2014_content
from sagasmith_dnd.core_content_2024 import build_srd2024_content
from sagasmith_dnd.rule_engine import resolution_context


def _evasion_actor(edition: str, class_name: str, incapacitated: bool) -> dict:
    skills = Path(__file__).resolve().parents[3] / "skills"
    builder = build_srd2014_content if edition == "2014" else build_srd2024_content
    _, artifacts = builder(skills)
    artifact = next(
        item
        for item in artifacts
        if item["kind"] == "feature"
        and item["card"].get("name") == "Evasion"
        and item["card"].get("class_name") == class_name
    )
    assert artifact["card"]["minimum_level"] == 7
    assert artifact["mechanic_refs"] == ["dnd5e.core.save.evasion"]
    assert artifact["rule_refs"]
    assert all(f"srd{edition}/" in ref for ref in artifact["rule_refs"])
    trait = artifact["card"]["choices"]["source_trait"]
    assert trait.get("unavailable_conditions", []) == (
        [] if edition == "2014" else ["incapacitated"]
    )
    sheet = default_character_sheet()
    sheet["edition"] = edition
    sheet["combat"]["hp"] = {"value": 30, "max": 30, "temp": 0}
    sheet["conditions"] = ["incapacitated"] if incapacitated else []
    sheet["content"]["features"] = [
        {
            "id": artifact["id"],
            "name": artifact["card"]["name"],
            "choices": deepcopy(artifact["card"]["choices"]),
            "mechanic_refs": list(artifact["mechanic_refs"]),
        }
    ]
    return {"id": "target", "sheet": sheet, "derived": derive_character_sheet(sheet)}


class _SaveRoll:
    def __init__(self, succeeds: bool) -> None:
        self.value = 20 if succeeds else 1

    def randint(self, lower: int, upper: int) -> int:
        if (lower, upper) == (1, 8):
            return 8
        assert (lower, upper) == (1, 20)
        return self.value


@pytest.mark.parametrize("edition", ["2014", "2024"])
@pytest.mark.parametrize("class_name", ["Rogue", "Monk"])
@pytest.mark.parametrize("incapacitated", [False, True])
@pytest.mark.parametrize("succeeds", [False, True])
def test_real_evasion_artifacts_preserve_edition_specific_incapacitation(
    edition: str, class_name: str, incapacitated: bool, succeeds: bool
) -> None:
    actor = _evasion_actor(edition, class_name, incapacitated)
    rules = resolution_context({"edition": edition, "fingerprint": "", "lock": [], "mechanics": []})
    settled = resolve_save_damage_to_sheet(
        actor,
        save_ability="dexterity",
        save_dc=10,
        damage_expression="1d8 + 1",
        damage_type="fire",
        half_on_success=True,
        source="test:source-backed-evasion",
        rules=rules,
        rng=_SaveRoll(succeeds),
    )
    applies = edition == "2014" or not incapacitated
    expected_damage = (0 if succeeds else 4) if applies else (4 if succeeds else 9)
    assert settled["result"]["damage_amount"] == expected_damage
    assert settled["sheet"]["combat"]["hp"]["value"] == 30 - expected_damage
    receipts = settled["result"]["rule_receipts"]
    assert [item["mechanic_id"] for item in receipts] == (
        ["dnd5e.core.save.evasion"] if applies else []
    )
    if applies:
        receipt = receipts[0]
        assert receipt["event"] == "save.damage_reduction"
        assert receipt["operations"] == [{"op": "builtin.core_provider"}]
        assert receipt["core_pack_fingerprint"] == rules.core_pack.fingerprint
        assert receipt["ruleset_fingerprint"] == rules.fingerprint
        assert receipt["citations"] == [
            {
                "source": (
                    "bundled:srd2014/02_Classes/Rogue.md#evasion"
                    if edition == "2014"
                    else "bundled:srd2024/DND5eSRD_047-063.md#level-7-evasion"
                ),
                "edition": edition,
            }
        ]


@pytest.mark.parametrize("edition", ["2014", "2024"])
@pytest.mark.parametrize("class_name", ["Rogue", "Monk"])
@pytest.mark.parametrize("ability,half_on_success", [("constitution", True), ("dexterity", False)])
def test_real_evasion_does_not_rewrite_unrelated_saves(
    edition: str, class_name: str, ability: str, half_on_success: bool
) -> None:
    settled = resolve_save_damage_to_sheet(
        _evasion_actor(edition, class_name, False),
        save_ability=ability,
        save_dc=10,
        damage_expression="1d8 + 1",
        damage_type="fire",
        half_on_success=half_on_success,
        source="test:nonqualifying-evasion-save",
        rng=_SaveRoll(False),
    )
    assert settled["result"]["damage_amount"] == 9
    assert settled["result"]["rule_receipts"] == []
