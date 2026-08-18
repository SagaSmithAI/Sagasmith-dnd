from __future__ import annotations

import pytest

from sagasmith_dnd.consumables import healing_potion_formula


def test_standard_healing_potion_is_edition_bound() -> None:
    item = {
        "id": "healing-potion",
        "name": "Potion of Healing",
        "kind": "consumable",
        "identified": True,
    }

    assert healing_potion_formula(item, edition="2014") == "2d4+2"
    assert healing_potion_formula(item, edition="2024") == "2d4+2"

    with pytest.raises(ValueError, match="edition 2014 or 2024"):
        healing_potion_formula(item, edition="2030")


def test_healing_potion_rejects_unidentified_or_wrong_items() -> None:
    with pytest.raises(ValueError, match="unidentified"):
        healing_potion_formula(
            {
                "name": "Potion of Healing",
                "kind": "consumable",
                "identified": False,
            },
            edition="2014",
        )
    with pytest.raises(ValueError, match="not a standard"):
        healing_potion_formula(
            {
                "name": "Potion of Poison",
                "kind": "consumable",
                "identified": True,
            },
            edition="2014",
        )
