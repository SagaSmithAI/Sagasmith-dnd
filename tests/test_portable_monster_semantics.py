from pathlib import Path

from sagasmith_dnd.character_schema import derive_character_sheet
from sagasmith_dnd.core_rule_pack import get_core_rule_pack
from sagasmith_dnd.statblocks import parse_2014_statblock

SOURCE = """# Emberling

*Small elemental, neutral*

**Armor Class** 13
**Hit Points** 22 (5d6 + 5)
**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---:|---:|---:|---:|---:|---:|
| 8 (-1) | 16 (+3) | 12 (+1) | 7 (-2) | 10 (+0) | 9 (-1) |

**Damage Immunities** fire
**Senses** darkvision 60 ft., passive Perception 10
**Languages** Ignan
**Challenge** 1 (200 XP)

***Cinder Skin.*** A creature that touches the emberling takes 3 (1d6) fire damage.

###### Actions

***Multiattack.*** The emberling makes two Claw attacks.

***Claw.*** *Melee Weapon Attack:* +5 to hit, reach 5 ft., one target. *Hit:* 6
(1d6 + 3) slashing damage, and the target catches fire, taking 3 (1d6) fire
damage at the end of its next turn.

***Ash Step (Recharge 5-6).*** Each creature within 10 feet must make a DC 13
Dexterity saving throw or be blinded until the end of its next turn.
"""


def test_nonstandard_monster_semantics_stay_source_bound_for_agent_review() -> None:
    parsed = parse_2014_statblock(SOURCE, source_key="addon:test/emberling")
    features = {item["name"]: item for item in parsed.sheet["content"]["features"]}
    activities = {item["name"]: item for item in parsed.sheet["content"]["activities"]}
    weapons = {item["name"]: item for item in parsed.sheet["inventory"]["items"]}

    assert features["Cinder Skin"]["choices"] == {
        "manual_ruling": {
            "kind": "descriptive_passive",
            "default_resolver": "agent",
            "source_excerpt": ("A creature that touches the emberling takes 3 (1d6) fire damage."),
        }
    }
    ash_step = activities["Ash Step (Recharge 5-6)"]
    assert ash_step["choices"]["manual_ruling"] == {
        "kind": "descriptive_activity",
        "default_resolver": "agent",
        "source_excerpt": (
            "Each creature within 10 feet must make a DC 13 Dexterity saving "
            "throw or be blinded until the end of its next turn."
        ),
    }
    assert ash_step["choices"]["recharge"] == {
        "kind": "d6_turn_start",
        "minimum": 5,
        "maximum": 6,
        "source_marker": "(Recharge 5-6)",
    }
    assert ash_step["mechanic_refs"] == ["dnd5e.core.activity.recharge"]
    assert activities["Multiattack"]["choices"]["multiattack_options"] == [
        {
            "id": "melee",
            "attacks": [{"weapon_id": "claw", "attack_mode": "melee", "count": 2}],
        }
    ]
    assert weapons["Claw"]["mechanics"]["on_hit_effect"].startswith("and the target catches fire")
    assert "on_hit_resolution" not in weapons["Claw"]["mechanics"]
    assert "source_trait" not in features["Cinder Skin"]["choices"]
    assert all(
        not reference.startswith("dnd5e.core.monster")
        for card in [*features.values(), *activities.values()]
        for reference in card.get("mechanic_refs", [])
    )
    assert derive_character_sheet(parsed.sheet)["inventory"]["weapon_attacks"][0][
        "on_hit_effect"
    ].startswith("and the target catches fire")


def test_core_rule_pack_contains_rules_not_creature_content_contracts() -> None:
    forbidden = {
        "random_save_effects",
        "area_save_damage",
        "frightful_presence",
        "source_save_effect",
        "source_contest_effect",
        "pack_tactics",
        "sunlight_sensitivity",
        "assassinate",
        "weapon_hit_save_damage",
        "weapon_hit_contest_pull",
        "keen_perception",
        "magic_resistance",
        "advantage_against_conditions",
    }
    for edition in ("2014", "2024"):
        ids = {boundary.id for boundary in get_core_rule_pack(edition).boundaries}
        assert not any(identifier.startswith("dnd5e.core.monster") for identifier in ids)
        assert not any(identifier.rsplit(".", 1)[-1] in forbidden for identifier in ids)


def test_generic_legendary_weapon_action_is_structured() -> None:
    source = (
        SOURCE
        + """

## Legendary Actions

The emberling can take 3 legendary actions, choosing from the options below.
Only one legendary action option can be used at a time and only at the end of
another creature's turn. The emberling regains spent legendary actions at the
start of its turn.

***Cinder Swipe.*** The emberling makes a Claw attack.
"""
    )
    parsed = parse_2014_statblock(
        source,
        source_key="addon:test/legendary-emberling",
    )
    activity = next(
        item for item in parsed.sheet["content"]["activities"] if item["name"] == "Cinder Swipe"
    )

    assert activity["mechanic_refs"] == ["dnd5e.core.activity.legendary_action"]
    assert activity["choices"]["legendary_action"] == {
        "kind": "legendary_action_2014",
        "pool": {
            "kind": "legendary_action_pool_2014",
            "maximum": 3,
            "one_option_per_trigger": True,
            "trigger": "end_of_another_creature_turn",
            "recovers_on": "source_turn_start",
            "source_excerpt": (
                "The emberling can take 3 legendary actions, choosing from the "
                "options below. Only one legendary action option can be used at a "
                "time and only at the end of another creature's turn. The emberling "
                "regains spent legendary actions at the start of its turn."
            ),
        },
        "cost": 1,
        "effect": {
            "kind": "weapon_attack",
            "weapon_id": "claw",
            "attack_mode": "melee",
        },
        "source_excerpt": "The emberling makes a Claw attack.",
    }


def test_engine_source_has_no_legacy_monster_specific_runtime_contracts() -> None:
    source_root = Path(__file__).parents[1] / "src" / "sagasmith_dnd"
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(source_root.glob("*.py"))
    ).casefold()
    forbidden = {
        "dnd5e.core.monster",
        "gazer_eye_ray",
        "intellect_devourer_body_thief",
        "intellect_devourer_devour_intellect",
        "corrosive_form",
        "heated_body",
        "armor_corrosion",
        "ignition_ongoing_damage",
        "death_burst",
        "wing_attack_2014",
        "corrosion_penalty",
        "critical_followup",
        "anatomical_loss",
        "inside_host",
        "source_traits",
        "dazing ray",
    }
    assert not (forbidden & {token for token in forbidden if token in source})

    creature_runtime = (source_root / "statblocks.py").read_text(encoding="utf-8").casefold()
    assert '"reaction_defense"' not in creature_runtime
    assert '"relentless_endurance"' not in creature_runtime

    combat_runtime = (source_root / "combat_engine.py").read_text(encoding="utf-8").casefold()
    standard_feature_guard = combat_runtime.split(
        "def _validated_standard_relentless_endurance_feature", 1
    )[1].split("\ndef ", 1)[0]
    assert "core_relentless_endurance_mechanic_id" in standard_feature_guard
    assert "re.search" not in standard_feature_guard
    assert '.get("name")' not in standard_feature_guard
