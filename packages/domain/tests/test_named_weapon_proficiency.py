import pytest

from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    derive_character_sheet,
    equip_inventory_item,
)


@pytest.mark.parametrize(
    ("weapon_name", "proficiency", "expected"),
    [
        ("Shortsword", "shortswords", True),
        ("Longsword", "longswords", True),
        ("Rapier", "rapiers", True),
        ("Hand Crossbow", "hand crossbows", True),
        ("Battleaxe", "battleaxes", True),
        ("Light-Hammer", "light hammers", True),
        ("Shortsword", "shortsword", True),
        ("Shortsword", "martial weapons", True),
        ("Shortsword", "simple weapons", False),
        ("Greatsword", "shortswords", False),
        ("Heavy Crossbow", "hand crossbows", False),
    ],
)
def test_named_weapon_proficiency_affects_derived_attack_bonus(
    weapon_name: str, proficiency: str, expected: bool
) -> None:
    sheet = default_character_sheet()
    sheet["abilities"]["strength"]["score"] = 14
    sheet["traits"]["proficiencies"]["weapons"] = [proficiency]
    sheet, weapon_id = add_inventory_item(
        sheet,
        {
            "name": weapon_name,
            "kind": "weapon",
            "mechanics": {
                "category": "martial",
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d6",
                "damage_type": "piercing",
                "proficient": False,
            },
        },
    )
    sheet = equip_inventory_item(sheet, weapon_id, "main_hand")
    attack = derive_character_sheet(sheet)["inventory"]["weapon_attacks"][0]
    assert attack["proficient"] is expected
    assert attack["attack_bonus"] == 2 + (2 if expected else 0)
