from copy import deepcopy
from pathlib import Path

import pytest

from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.chase_engine import _check
from sagasmith_dnd.core_content import build_srd2014_content
from sagasmith_dnd.rule_engine import resolution_context


class OneDie:
    def __init__(self) -> None:
        self.calls = 0

    def randint(self, minimum: int, maximum: int) -> int:
        self.calls += 1
        assert (minimum, maximum) == (1, 20)
        assert self.calls == 1
        return 12


@pytest.fixture(scope="module")
def species_cards() -> dict:
    _, artifacts = build_srd2014_content(Path(__file__).resolve().parents[3] / "skills")
    return {
        item["card"]["name"]: item["card"] for item in artifacts if item["kind"] == "species"
    }


@pytest.mark.parametrize("species", ["Hill Dwarf", "High Elf", "Rock Gnome", "Lightfoot"])
def test_native_chase_save_does_not_inherit_unrelated_poison_facts(species, species_cards) -> None:
    sheet = default_character_sheet()
    sheet["content"]["features"] = deepcopy(species_cards[species]["grants"]["features"])
    actor = {"id": "runner", "sheet": sheet, "derived": derive_character_sheet(sheet)}
    context = resolution_context(
        {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []},
        facts={"save_against_poison": True},
    )
    before = deepcopy(actor)
    rng = OneDie()
    result = _check(actor, dc=10, kind="save", ability="dexterity", rules=context, rng=rng)
    assert result["roll_mode"] == "normal"
    assert result["rolls"] == [12]
    assert rng.calls == 1
    assert not result["rule_receipts"]
    assert actor == before
    assert context.facts == {"save_against_poison": True}
