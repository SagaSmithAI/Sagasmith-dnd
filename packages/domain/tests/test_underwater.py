"""2014 water rules use actual card weapons, speeds and typed damage ordering."""

from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.combat_engine import (
    NeedsRulingError,
    apply_damage_parts_to_sheet,
    apply_damage_to_sheet,
    preflight_attack,
    resolve_save_damage_to_sheets,
    roll_attack_action,
)
from sagasmith_dnd.objects import apply_object_damage, object_attack_plan
from sagasmith_dnd.water import WATER_RULE, validate_water_state


class NoRng:
    def randint(self, *_):
        raise AssertionError("automatic underwater miss must not roll")


def actor(name="Longsword", *, mode="melee", underwater=True, swim=0, distance=5):
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": 50, "max": 50, "temp": 0}
    sheet["combat"]["speed"]["swim"] = swim
    if underwater:
        sheet["combat"]["water_environment"] = {"underwater": True, "fully_immersed": True}
    sheet["inventory"]["items"] = [{
        "id": "weapon", "name": name, "kind": "weapon", "equipped": True,
        "equipped_slot": "main_hand", "mechanics": {
            "attack_type": mode, "attack_ability": "strength", "damage_formula": "1d6",
            "damage_type": "piercing", "normal_range_ft": 20, "long_range_ft": 60,
            "thrown_normal_range_ft": 20, "thrown_long_range_ft": 60,
            "properties": ["thrown"] if mode == "melee" else [], "proficient": True,
        },
    }]
    sheet["inventory"]["equipment_slots"]["main_hand"] = "weapon"
    a = {"id": "attacker", "sheet": sheet, "derived": derive_character_sheet(sheet),
         "position": {"x": 0, "y": 0}}
    target_sheet = default_character_sheet()
    b = {"id": "target", "sheet": target_sheet, "derived": derive_character_sheet(target_sheet),
         "position": {"x": distance // 5, "y": 0}}
    return a, b


def plan(name="Longsword", *, mode="melee", **kwargs):
    a, b = actor(name, mode=mode, **kwargs)
    return preflight_attack(a, b, action={"weapon_id": "weapon", "attack_mode": mode},
                            require_attack_action=False)


@pytest.mark.parametrize("name", ["Dagger", "Javelin", "Shortsword", "Spear", "Trident"])
def test_underwater_melee_exceptions(name):
    assert plan(name)["disadvantage"] is False


@pytest.mark.parametrize("name", ["Longsword", "Greataxe", "Mace"])
def test_underwater_melee_requires_a_current_swim_speed_or_exception(name):
    assert plan(name)["disadvantage"] is True
    assert plan(name, swim=30)["disadvantage"] is False
    assert plan(name, underwater=False)["disadvantage"] is False


def test_magically_granted_swim_speed_and_advantage_cancel_normally():
    a, b = actor()
    a["sheet"]["effects"] = [{
        "id": "swimming", "name": "Granted swimming", "active": True,
        "changes": [{"path": "combat.speed.swim", "mode": "add", "value": 30}],
    }]
    a["derived"] = derive_character_sheet(a["sheet"])
    result = preflight_attack(a, b, action={"weapon_id": "weapon"}, require_attack_action=False)
    assert result["disadvantage"] is False
    a["sheet"]["effects"] = []
    a["derived"] = derive_character_sheet(a["sheet"])
    result = preflight_attack(a, b, action={"weapon_id": "weapon", "context": {"advantage": True}},
                              require_attack_action=False)
    assert result["advantage"] is True and result["disadvantage"] is True


@pytest.mark.parametrize("name", ["Light Crossbow", "Crossbow, heavy", "Crossbow, hand", "Net",
                                  "Javelin", "Spear", "Trident", "Dart"])
def test_underwater_ranged_exceptions_within_normal_range(name):
    assert plan(name, mode="ranged", distance=20)["disadvantage"] is False


def test_longbow_disadvantage_is_not_removed_by_swimming_speed():
    assert plan("Longbow", mode="ranged", swim=30, distance=20)["disadvantage"] is True


@pytest.mark.parametrize("name", ["Light Crossbow", "Longbow", "Javelin"])
@pytest.mark.parametrize("distance", [25, 60, 100])
def test_beyond_normal_range_is_an_automatic_miss_without_rng(name, distance):
    prepared = plan(name, mode="ranged", distance=distance)
    result = roll_attack_action(plan=prepared, rng=NoRng())
    assert result["hit"] is False and result["critical"] is False
    assert result["rolls"] == [] and result["automatic_miss"] is True


def test_underwater_ranged_attack_needs_geometry_and_does_not_affect_spell_attacks():
    a, b = actor(mode="ranged")
    a.pop("position")
    b.pop("position")
    with pytest.raises(NeedsRulingError, match="range classification"):
        preflight_attack(a, b, action={"weapon_id": "weapon", "attack_mode": "ranged"},
                         require_attack_action=False)
    a["derived"]["inventory"]["weapon_attacks"][0]["spell_attack"] = True
    prepared = preflight_attack(a, b, action={"weapon_id": "weapon", "attack_mode": "ranged"},
                                 require_attack_action=False)
    assert prepared["underwater"] is None


@pytest.mark.parametrize("resistant,vulnerable,immune,expected", [
    (False, False, False, 5), (True, False, False, 5),
    (True, True, False, 10), (True, True, True, 0),
])
def test_immersed_fire_uses_the_shared_damage_order_without_stacking(
    resistant, vulnerable, immune, expected,
):
    a, _ = actor()
    sheet = a["sheet"]
    for field, active in (("resistances", resistant), ("vulnerabilities", vulnerable),
                          ("immunities", immune)):
        sheet["traits"][field] = ["fire"] if active else []
    result = apply_damage_to_sheet(sheet, amount=11, damage_type="fire")
    assert result["sheet"]["combat"]["hp"]["value"] == 50 - expected
    assert WATER_RULE in result["defense_sources"]
    assert apply_damage_to_sheet(sheet, amount=11, damage_type="cold")["applied_amount"] == 11
    sheet["combat"]["water_environment"]["fully_immersed"] = False
    if not resistant and not vulnerable and not immune:
        assert apply_damage_to_sheet(sheet, amount=11, damage_type="fire")["applied_amount"] == 11


def test_grouped_fire_parts_and_objects_share_immersion_defense():
    a, _ = actor()
    result = apply_damage_parts_to_sheet(a["sheet"], parts=[
        {"amount": 3, "damage_type": "fire"}, {"amount": 3, "damage_type": "fire"},
    ])
    assert result["sheet"]["combat"]["hp"]["value"] == 47
    profile = {"id": "wall", "name": "Wall", "scene_id": "vault", "material": "stone",
               "size": "large", "resilience": "resilient", "armor_class": 17, "hit_points": 100,
               "fully_immersed": True, "damage_resistances": ["fire"]}
    result = apply_object_damage(profile, 100, [{"amount": 11, "damage_type": "fire"}],
                                 weapon_id="fire", weapon_traits=[])
    assert result["hp_loss"] == 5


@pytest.mark.parametrize("state", [
    {"underwater": 1, "fully_immersed": True},
    {"underwater": True, "fully_immersed": "true"},
    {"underwater": False, "fully_immersed": True},
    {"underwater": True, "fully_immersed": True, "fire_resistance": True},
])
def test_invalid_environment_cannot_be_normalized_into_a_mechanical_bonus(state):
    with pytest.raises(ValueError):
        validate_water_state(state)


def test_2024_does_not_inherit_2014_water_state():
    a, _ = actor()
    sheet = deepcopy(a["sheet"])
    sheet["edition"] = "2024"
    with pytest.raises(ValueError, match="2014"):
        derive_character_sheet(sheet)


def test_renamed_source_weapon_keeps_its_type_and_bound_base_weapon():
    a, b = actor("Night's Edge")
    item = a["sheet"]["inventory"]["items"][0]
    item["source_key"] = "dnd5e.content.srd2014@1.42.0:dnd5e.content.srd2014.item.dagger"
    a["derived"] = derive_character_sheet(a["sheet"])
    assert not preflight_attack(a, b, action={"weapon_id": "weapon"})["disadvantage"]
    weapon = a["derived"]["inventory"]["weapon_attacks"][0]
    weapon["source_key"] = "official:armblade"
    weapon["base_weapon_source"] = {"artifact_id": "dnd5e.content.srd2014.item.dagger"}
    assert not preflight_attack(a, b, action={"weapon_id": "weapon"})["disadvantage"]
    weapon["base_weapon_source"]["artifact_id"] = "dnd5e.content.srd2014.item.longsword"
    weapon["name"] = "Dagger"
    assert preflight_attack(a, b, action={"weapon_id": "weapon"})["disadvantage"]


def test_reviewed_object_range_does_not_require_invented_coordinates():
    a, _ = actor("Longbow", mode="ranged")
    a.pop("position")
    profile = {"id": "wall", "name": "Wall", "scene_id": "vault", "material": "stone",
               "size": "large", "resilience": "resilient", "armor_class": 17, "hit_points": 100}
    with pytest.raises(NeedsRulingError, match="range classification"):
        object_attack_plan(a, profile, weapon_id="weapon")
    prepared = object_attack_plan(a, profile, weapon_id="weapon", reviewed_long_range=True)
    assert roll_attack_action(plan=prepared, rng=NoRng())["automatic_miss"]
    assert object_attack_plan(
        a, profile, weapon_id="weapon", reviewed_long_range=False,
    )["disadvantage"]


def test_multi_part_damage_carries_the_environment_source_receipt():
    a, _ = actor()
    receipts = [{"mechanic_id": WATER_RULE, "citation": "Underwater Combat"}]
    a["sheet"]["combat"]["water_environment"]["rule_receipts"] = receipts
    damage = apply_damage_parts_to_sheet(a["sheet"], [{"amount": 9, "damage_type": "fire"}])
    assert damage["applied_amount"] == 4
    assert damage["environment_receipts"] == receipts


def test_agent_positioning_requires_explicit_underwater_range_without_coordinates():
    a, b = actor("Longbow", mode="ranged")
    a.pop("position")
    b.pop("position")
    encounter = {"positioning_mode": "agent", "combatants": [], "turn_index": 0}
    facts = {"targetable": True, "in_range": True, "cover_degree": "none",
             "attacker_can_see_target": True, "target_can_see_attacker": True,
             "target_within_5_ft": False, "close_threat_actor_ids": []}
    action = {"weapon_id": "weapon", "context": {"spatial_facts": facts}}
    with pytest.raises(NeedsRulingError, match="explicit long_range"):
        preflight_attack(a, b, action=action, encounter=encounter, require_attack_action=False)
    facts["long_range"] = True
    prepared = preflight_attack(a, b, action=action, encounter=encounter,
                                 require_attack_action=False)
    assert roll_attack_action(plan=prepared, rng=NoRng())["automatic_miss"]
    facts["long_range"] = False
    prepared = preflight_attack(a, b, action=action, encounter=encounter,
                                 require_attack_action=False)
    assert prepared["disadvantage"] and not prepared["underwater"]["automatic_miss"]


def test_save_damage_halves_for_the_save_before_immersion_and_preserves_source():
    class HighRng:
        def randint(self, low, high):
            return high

    a, _ = actor()
    a["sheet"]["combat"]["water_environment"]["rule_receipts"] = [{"mechanic_id": WATER_RULE}]
    result = resolve_save_damage_to_sheets(
        [a], save_ability="dexterity", save_dc=1, damage_expression="1d10+1",
        damage_type="fire", half_on_success=True, source="fire trap", rng=HighRng(),
    )
    target = result["result"]["targets"][0]
    assert target["success"] and target["damage_amount"] == 5
    assert target["damage"]["applied_amount"] == 2
    assert target["damage"]["environment_receipts"][0]["mechanic_id"] == WATER_RULE
