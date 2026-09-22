from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    equip_inventory_item,
    validate_character_sheet,
)
from sagasmith_dnd.combat_engine import CombatEngineError, NeedsRulingError
from sagasmith_dnd.spells import consume_readied_spell, consume_spell_cast


def caster(*, verbal=True, somatic=True, material=False, consumed=False, cost=0):
    sheet = default_character_sheet()
    sheet["spellcasting"]["spell_slots"] = {
        "1": {"value": 2, "max": 2, "recovers_on": "long_rest"},
    }
    sheet["content"]["spells"] = [
        {
            "id": "test-spell",
            "name": "Test spell",
            "level": 1,
            "grant": {"source_type": "class", "source_key": "wizard"},
            "access": {"known": True, "prepared": True},
            "definition": {
                "casting_time": "1 action",
                "components": {
                    "verbal": verbal,
                    "somatic": somatic,
                    "material": material,
                    "consumed": consumed,
                    "material_cost_cp": cost,
                },
            },
        }
    ]
    return validate_character_sheet(sheet)


def constraint(sheet, **facts):
    sheet = deepcopy(sheet)
    sheet["effects"].append(
        {
            "id": "restriction",
            "name": "Reviewed casting restriction",
            "active": True,
            "source": "review:scene restriction",
            "duration": {"period": "manual", "remaining": 0},
            "metadata": {"spell_component_constraints": facts},
        }
    )
    return validate_character_sheet(sheet)


def component(sheet, kind="pouch", *, focus_type="arcane", price=0, quantity=1, slot=None):
    role = {"kind": kind, "source": "review:SRD 2014 equipment/component entry"}
    if kind == "focus":
        role["focus_type"] = focus_type
    if kind == "material":
        role["spell_ids"] = ["test-spell"]
    sheet, _ = add_inventory_item(
        sheet,
        {
            "id": "component",
            "name": "Reviewed component",
            "kind": "focus",
            "quantity": quantity,
            "price_cp": price,
            "mechanics": {"spell_component": role},
        },
    )
    return equip_inventory_item(sheet, "component", slot) if slot else sheet


def shield(sheet):
    sheet["traits"]["proficiencies"]["armor"] = ["shields"]
    sheet, _ = add_inventory_item(
        sheet,
        {
            "id": "shield",
            "name": "Shield",
            "kind": "shield",
            "mechanics": {"ac_bonus": 2},
        },
    )
    return equip_inventory_item(sheet, "shield", "shield")


@pytest.mark.parametrize("ready", [False, True])
@pytest.mark.parametrize("restriction", ["silence", "gag"])
def test_verbal_prevention_rejects_before_slot_concentration_or_ready(ready, restriction):
    sheet = constraint(caster(), can_speak=False)
    sheet["effects"][0]["source"] = f"review:{restriction}"
    before = deepcopy(sheet)
    with pytest.raises(CombatEngineError, match="verbal component"):
        (consume_readied_spell if ready else consume_spell_cast)(sheet, spell_id="test-spell")
    assert sheet == before


def test_componentless_cast_ignores_silence_bound_hands_and_empty_inventory():
    sheet = constraint(caster(verbal=False, somatic=False), can_speak=False, usable_hands=0)
    result = consume_spell_cast(sheet, spell_id="test-spell")
    assert result["sheet"]["spellcasting"]["spell_slots"]["1"]["value"] == 1


def test_restrained_does_not_itself_mean_bound_hands():
    sheet = caster()
    sheet["conditions"] = ["restrained"]
    assert consume_spell_cast(sheet, spell_id="test-spell")["status"] == "committed"
    sheet = constraint(sheet, usable_hands=0)
    with pytest.raises(CombatEngineError, match="somatic component"):
        consume_spell_cast(sheet, spell_id="test-spell")


def test_shield_and_weapon_prevent_somatic_but_holding_two_handed_weapon_leaves_a_hand():
    sheet, _ = add_inventory_item(
        caster(),
        {
            "id": "weapon",
            "name": "Greatsword",
            "kind": "weapon",
            "mechanics": {"properties": ["two-handed"]},
        },
    )
    sheet = equip_inventory_item(sheet, "weapon", "main_hand")
    assert consume_spell_cast(sheet, spell_id="test-spell")["status"] == "committed"
    with pytest.raises(CombatEngineError, match="somatic component"):
        consume_spell_cast(shield(sheet), spell_id="test-spell")


@pytest.mark.parametrize("kind", ["focus", "material"])
def test_material_hand_can_also_perform_somatic(kind):
    sheet = shield(component(caster(material=True), kind, slot="main_hand"))
    result = consume_spell_cast(sheet, spell_id="test-spell")
    assert result["component_receipt"]["shared_material_hand"] is True
    assert result["component_receipt"]["free_hands"] == 0


def test_free_hand_accesses_pouch_and_performs_somatic():
    sheet = shield(component(caster(material=True)))
    result = consume_spell_cast(sheet, spell_id="test-spell")
    assert result["component_receipt"]["shared_material_hand"] is True
    assert result["component_receipt"]["free_hands"] == 1


def test_focus_hand_does_not_satisfy_somatic_only_spell():
    sheet = shield(component(caster(), "focus", slot="main_hand"))
    with pytest.raises(CombatEngineError, match="somatic component"):
        consume_spell_cast(sheet, spell_id="test-spell")


@pytest.mark.parametrize(
    "kind,cost,consumed",
    [
        ("pouch", 100, False),
        ("focus", 0, True),
        ("material", 101, False),
    ],
)
def test_substitutes_and_insufficient_value_cannot_pay_specific_components(kind, cost, consumed):
    sheet = component(
        caster(material=True, cost=cost, consumed=consumed), kind, price=100, slot="main_hand"
    )
    before = deepcopy(sheet)
    with pytest.raises(NeedsRulingError, match="material component"):
        consume_spell_cast(
            sheet,
            spell_id="test-spell",
            component_ruling={"material_confirmed": True},
        )
    assert sheet == before


def test_consumed_material_is_atomic_and_exhausts_inventory_without_spending_wallet():
    sheet = component(
        caster(material=True, consumed=True, cost=100),
        "material",
        price=100,
        quantity=2,
        slot="main_hand",
    )
    before = deepcopy(sheet)
    first = consume_spell_cast(sheet, spell_id="test-spell")
    second = consume_spell_cast(first["sheet"], spell_id="test-spell")
    assert sheet == before
    assert first["sheet"]["inventory"]["items"][0]["quantity"] == 1
    assert second["sheet"]["inventory"]["items"] == []
    assert second["sheet"]["inventory"]["equipment_slots"]["main_hand"] is None
    assert second["sheet"]["inventory"]["wallet"] == sheet["inventory"]["wallet"]


def test_focus_cannot_borrow_a_different_class_or_be_used_while_stowed():
    sheet = component(caster(material=True), "focus", focus_type="druidic", slot="main_hand")
    with pytest.raises(NeedsRulingError):
        consume_spell_cast(sheet, spell_id="test-spell")
    sheet["inventory"]["items"][0]["mechanics"]["spell_component"]["focus_type"] = "arcane"
    assert consume_spell_cast(sheet, spell_id="test-spell")["status"] == "committed"
    sheet = equip_inventory_item(sheet, "component", None)
    with pytest.raises(NeedsRulingError):
        consume_spell_cast(sheet, spell_id="test-spell")


def test_reviewed_feature_override_is_selected_from_card_not_caller():
    sheet = constraint(caster(material=True), can_speak=False, usable_hands=0)
    sheet["content"]["spells"][0]["access"]["feature_casting_sources"] = [
        {
            "source_key": "innate",
            "allow_slot_cast": False,
            "minimum_level": 1,
            "casting_overrides": {"ignore_components": True},
            "usage": "at_will",
        }
    ]
    # The ordinary casting source validator remains responsible for this grant;
    # this test only exercises its reviewed component override contract.
    from sagasmith_dnd.spell_components import preflight_spell_components

    result = preflight_spell_components(sheet, sheet["content"]["spells"][0])
    assert result["required"]["verbal"] is False
    with pytest.raises(CombatEngineError, match="unsupported component_ruling"):
        consume_spell_cast(
            caster(), spell_id="test-spell", component_ruling={"ignore_components": True}
        )


def test_expired_constraint_is_ignored_and_2024_is_not_given_2014_focus_rules():
    sheet = constraint(caster(), can_speak=False)
    sheet["effects"][0]["active"] = False
    assert consume_spell_cast(sheet, spell_id="test-spell")["status"] == "committed"
    sheet = constraint(caster(), can_speak=False)
    sheet["edition"] = "2024"
    result = consume_spell_cast(sheet, spell_id="test-spell")
    assert result["component_receipt"] is None
    assert "verbal_component" in result["ruling_required"]


def test_holy_symbol_on_shield_shares_somatic_hand_but_worn_symbol_does_not():
    sheet = shield(caster(material=True))
    sheet["content"]["spells"][0]["grant"]["source_key"] = "class:cleric"
    sheet["inventory"]["items"][0]["mechanics"]["spell_component"] = {
        "kind": "focus", "focus_type": "holy_symbol", "presentation": "shield",
        "source": "SRD 2014 Equipment: Holy Symbol",
    }
    sheet, _ = add_inventory_item(sheet, {"id": "sword", "name": "Sword", "kind": "weapon"})
    sheet = equip_inventory_item(sheet, "sword", "main_hand")
    assert consume_spell_cast(sheet, spell_id="test-spell")["component_receipt"][
        "shared_material_hand"
    ] is True
    del sheet["inventory"]["items"][0]["mechanics"]["spell_component"]
    sheet, _ = add_inventory_item(sheet, {
        "id": "symbol", "name": "Visible amulet", "kind": "equipment",
        "mechanics": {"spell_component": {
            "kind": "focus", "focus_type": "holy_symbol", "presentation": "worn",
            "source": "SRD 2014 Equipment: Holy Symbol",
        }},
    })
    sheet = equip_inventory_item(sheet, "symbol", "neck")
    with pytest.raises(NeedsRulingError):
        consume_spell_cast(sheet, spell_id="test-spell")
    sheet["content"]["spells"][0]["definition"]["components"]["somatic"] = False
    assert consume_spell_cast(sheet, spell_id="test-spell")["status"] == "committed"


def test_2024_costly_component_keeps_its_separate_confirmation_contract():
    sheet = caster(material=True, cost=100)
    sheet["edition"] = "2024"
    with pytest.raises(NeedsRulingError):
        consume_spell_cast(sheet, spell_id="test-spell")
    result = consume_spell_cast(
        sheet, spell_id="test-spell", component_ruling={"material_confirmed": True},
    )
    assert result["component_receipt"] is None
    assert "material_component" in result["ruling_required"]
