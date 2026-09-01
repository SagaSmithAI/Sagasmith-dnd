from __future__ import annotations

from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import (
    add_effect,
    add_inventory_item,
    adjust_wallet,
    attune_inventory_item,
    consume_weapon_ammunition,
    default_character_sheet,
    derive_character_sheet,
    effective_ability_modifier,
    equip_inventory_item,
    receive_inventory_item,
    remove_effect,
    remove_inventory_item,
    set_exhaustion_level,
    set_spell_prepared,
    validate_character_notes,
    validate_character_sheet,
    validate_party_state,
    validate_world_time,
)
from sagasmith_dnd.chase_engine import start_chase
from sagasmith_dnd.combat_engine import start_encounter
from sagasmith_dnd.content_solution import build_content_solution
from sagasmith_dnd.resolution_plan import compile_resolution_plan
from sagasmith_dnd.rule_engine import resolution_context
from sagasmith_dnd.standard_feature_ids import (
    CORE_DWARF_HEAVY_ARMOR_SPEED_MECHANIC_ID,
    SRD2014_DWARF_SPEED_LEGACY_PACK_VERSIONS,
    SRD2014_DWARF_SPEED_SOURCE_RULE_REF,
)
from sagasmith_dnd.vocabulary import DENOMINATION_CP_VALUES


def test_runtime_sheet_rejects_portable_portrait_fields() -> None:
    sheet = default_character_sheet()
    sheet["identity"]["portrait_uri"] = "asset://portraits/mira.png"

    with pytest.raises(ValueError, match="portrait_uri"):
        validate_character_sheet(sheet)


def test_runtime_notes_accept_source_bound_portrait_reference() -> None:
    portrait_ref = {
        "asset_key": "actor.goblin.image",
        "checksum": "a" * 64,
        "media_type": "image/webp",
        "alt": "Goblin portrait",
        "source": {
            "kind": "content_pack",
            "package_id": "example.module",
            "package_version": "1.0.0",
            "package_checksum": "b" * 64,
        },
    }

    notes = validate_character_notes({"profile": {"portrait_ref": portrait_ref}})

    assert notes["profile"]["portrait_ref"] == portrait_ref


def test_runtime_notes_reject_unbound_portrait_uri() -> None:
    with pytest.raises(ValueError, match="portrait_uri"):
        validate_character_notes({"profile": {"portrait_uri": "https://example.com/goblin.png"}})


def test_weapon_attacks_derive_actor_proficiency_and_finesse_ability() -> None:
    sheet = default_character_sheet()
    sheet["abilities"]["strength"]["score"] = 10
    sheet["abilities"]["dexterity"]["score"] = 16
    sheet, greatsword_id = add_inventory_item(
        sheet,
        {
            "id": "greatsword",
            "name": "Greatsword",
            "kind": "weapon",
            "mechanics": {
                "category": "martial",
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "2d6",
                "damage_type": "slashing",
                "proficient": False,
            },
        },
    )
    sheet = equip_inventory_item(sheet, greatsword_id, "main_hand")

    attack = derive_character_sheet(sheet)["inventory"]["weapon_attacks"][0]
    assert attack["proficient"] is False
    assert attack["attack_bonus"] == 0

    sheet["traits"]["proficiencies"]["weapons"] = ["martial weapons"]
    proficient_attack = derive_character_sheet(sheet)["inventory"]["weapon_attacks"][0]
    assert proficient_attack["proficient"] is True
    assert proficient_attack["attack_bonus"] == 2

    sheet, _ = remove_inventory_item(sheet, greatsword_id)
    sheet["traits"]["proficiencies"]["weapons"] = ["simple weapons"]
    sheet, dagger_id = add_inventory_item(
        sheet,
        {
            "id": "dagger",
            "name": "Dagger",
            "kind": "weapon",
            "mechanics": {
                "category": "simple",
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d4",
                "damage_type": "piercing",
                "properties": ["Finesse", "light", "thrown"],
                "proficient": False,
            },
        },
    )
    sheet = equip_inventory_item(sheet, dagger_id, "main_hand")

    finesse_attack = derive_character_sheet(sheet)["inventory"]["weapon_attacks"][0]
    assert finesse_attack["attack_ability"] == "dexterity"
    assert finesse_attack["attack_bonus"] == 5
    assert finesse_attack["damage_expression"] == "1d4 + 3"


def test_2014_armor_proficiency_strength_and_encumbrance_affect_derived_rules() -> None:
    sheet = default_character_sheet()
    sheet["abilities"]["strength"]["score"] = 8
    sheet, armor_id = add_inventory_item(
        sheet,
        {
            "id": "chain-mail",
            "name": "Chain mail",
            "kind": "armor",
            "weight_oz": 880,
            "mechanics": {
                "base_ac": 16,
                "category": "heavy",
                "dexterity_mode": "none",
                "strength_requirement": 13,
                "stealth_disadvantage": True,
            },
        },
    )
    sheet = equip_inventory_item(sheet, armor_id, "armor")

    rules_2014 = resolution_context(
        {"edition": "2014", "fingerprint": "dwarf-test", "lock": [], "mechanics": []}
    )
    derived = derive_character_sheet(sheet, rules=rules_2014)
    assert derived["armor_class"] == 16
    assert derived["speed"]["walk"] == 20
    assert derived["armor_proficiency"]["proficient"] is False
    assert derived["equipment_penalties"] == {
        "attack_disadvantage_abilities": ["dexterity", "strength"],
        "check_disadvantage_abilities": ["dexterity", "strength"],
        "save_disadvantage_abilities": ["dexterity", "strength"],
        "spellcasting_blocked": True,
    }

    sheet["traits"]["proficiencies"]["armor"] = ["heavy armor"]
    proficient = derive_character_sheet(sheet)
    assert proficient["armor_proficiency"]["proficient"] is True
    assert proficient["equipment_penalties"]["spellcasting_blocked"] is False
    assert proficient["speed"]["walk"] == 20

    sheet["abilities"]["strength"]["score"] = 13
    assert derive_character_sheet(sheet)["speed"]["walk"] == 30

    unarmored = default_character_sheet()
    unarmored["inventory"]["encumbrance"]["mode"] = "variant"
    unarmored, _ = add_inventory_item(
        unarmored,
        {"id": "load", "name": "Load", "kind": "equipment", "weight_oz": 900},
    )
    encumbered = derive_character_sheet(unarmored)
    assert encumbered["inventory"]["encumbrance"]["state"] == "encumbered"
    assert encumbered["speed"]["walk"] == 20

    unarmored["inventory"]["items"][0]["weight_oz"] = 1800
    heavily_encumbered = derive_character_sheet(unarmored)
    assert heavily_encumbered["inventory"]["encumbrance"]["state"] == "heavily_encumbered"
    assert heavily_encumbered["speed"]["walk"] == 10
    assert heavily_encumbered["equipment_penalties"]["save_disadvantage_abilities"] == [
        "constitution",
        "dexterity",
        "strength",
    ]

    unarmored["inventory"]["items"][0]["weight_oz"] = 2500
    over_capacity = derive_character_sheet(unarmored)
    assert over_capacity["inventory"]["encumbrance"]["state"] == "over_capacity"
    assert over_capacity["speed"]["walk"] == 0


@pytest.mark.parametrize("legacy_pack_version", sorted(SRD2014_DWARF_SPEED_LEGACY_PACK_VERSIONS))
def test_2014_dwarf_heavy_armor_speed_exception_is_source_bound_and_narrow(
    legacy_pack_version: str,
) -> None:
    assert SRD2014_DWARF_SPEED_LEGACY_PACK_VERSIONS == frozenset({"1.24.0", "1.25.0"})
    rules_2014 = resolution_context(
        {"edition": "2014", "fingerprint": "dwarf-test", "lock": [], "mechanics": []}
    )
    sheet = default_character_sheet()
    sheet["progression"]["species"] = "Hill Dwarf"
    sheet["abilities"]["strength"]["score"] = 10
    sheet["combat"]["speed"]["walk"] = 25
    sheet["content"]["features"].append(
        {
            "id": "dnd5e.content.srd2014.species-feature.hill-dwarf-speed",
            "name": "Speed",
            "source_key": "Hill Dwarf",
            "description": (
                "Your base walking speed is 25 feet. Your speed is not reduced "
                "by wearing heavy armor."
            ),
            "choices": {
                "source_trait": {
                    "kind": "dwarf_heavy_armor_speed",
                    "trigger": "heavy_armor_strength_shortfall",
                    "ignored_penalty_ft": 10,
                    "automatic": True,
                    "source_excerpt": ("Your speed is not reduced by wearing heavy armor."),
                }
            },
            "mechanic_refs": [CORE_DWARF_HEAVY_ARMOR_SPEED_MECHANIC_ID],
        }
    )
    sheet, armor_id = add_inventory_item(
        sheet,
        {
            "id": "chain-mail",
            "name": "Chain mail",
            "kind": "armor",
            "weight_oz": 880,
            "mechanics": {
                "base_ac": 16,
                "category": "heavy",
                "dexterity_mode": "none",
                "strength_requirement": 13,
            },
        },
    )
    sheet = equip_inventory_item(sheet, armor_id, "armor")

    derived = derive_character_sheet(sheet, rules=rules_2014)
    assert derived["speed"]["walk"] == 25
    assert derived["armor_strength"] == {
        "requirement": 13,
        "meets_requirement": False,
        "speed_penalty_ft": 0,
    }
    dwarf_receipt = next(
        receipt
        for receipt in derived["rule_receipts"]
        if receipt["mechanic_id"] == CORE_DWARF_HEAVY_ARMOR_SPEED_MECHANIC_ID
    )
    assert dwarf_receipt["event"] == "character.derive"
    assert dwarf_receipt["citations"] == [
        {"source": SRD2014_DWARF_SPEED_SOURCE_RULE_REF + "#speed", "edition": "2014"}
    ]

    sheet["inventory"]["encumbrance"]["mode"] = "variant"
    encumbered = derive_character_sheet(sheet)
    assert encumbered["inventory"]["encumbrance"]["state"] == "encumbered"
    assert encumbered["speed"]["walk"] == 15

    ordinary_sheet = deepcopy(sheet)
    ordinary_sheet["progression"]["species"] = "Human"
    ordinary_sheet["content"]["features"] = []
    ordinary_sheet["inventory"]["encumbrance"]["mode"] = "standard"
    assert derive_character_sheet(ordinary_sheet)["speed"]["walk"] == 15

    legacy_sheet = deepcopy(sheet)
    legacy_sheet["content"]["features"] = []
    legacy_sheet["content"]["selections"] = [
        {
            "artifact_id": "dnd5e.content.srd2014.species.hill-dwarf",
            "kind": "species",
            "name": "ignored display name",
            "pack_id": "dnd5e.content.srd2014",
            "pack_version": legacy_pack_version,
            "rule_refs": [SRD2014_DWARF_SPEED_SOURCE_RULE_REF],
            "mechanic_refs": [],
            "selection": {"tools": ["smith's tools"]},
        }
    ]
    legacy_sheet["inventory"]["encumbrance"]["mode"] = "standard"
    legacy = derive_character_sheet(legacy_sheet, rules=rules_2014)
    assert legacy["speed"]["walk"] == 25
    assert CORE_DWARF_HEAVY_ARMOR_SPEED_MECHANIC_ID in {
        receipt["mechanic_id"] for receipt in legacy["rule_receipts"]
    }

    for field, forged_value in (
        ("artifact_id", "dnd5e.content.srd2014.species.forged-dwarf"),
        ("pack_version", "1.23.0"),
        ("rule_refs", ["bundled:srd2014/forged/Dwarf.md"]),
        ("mechanic_refs", [CORE_DWARF_HEAVY_ARMOR_SPEED_MECHANIC_ID]),
    ):
        forged_legacy = deepcopy(legacy_sheet)
        forged_legacy["content"]["selections"][0][field] = forged_value
        assert derive_character_sheet(forged_legacy)["speed"]["walk"] == 15

    non_heavy_sheet = deepcopy(sheet)
    non_heavy_sheet["inventory"]["items"][0]["mechanics"]["category"] = "medium"
    non_heavy_sheet["inventory"]["encumbrance"]["mode"] = "standard"
    assert derive_character_sheet(non_heavy_sheet)["speed"]["walk"] == 15

    modern_sheet = deepcopy(sheet)
    modern_sheet["edition"] = "2024"
    modern_sheet["inventory"]["encumbrance"]["mode"] = "standard"
    assert derive_character_sheet(modern_sheet)["speed"]["walk"] == 15
    rules_2024 = resolution_context(
        {"edition": "2024", "fingerprint": "wrong-edition", "lock": [], "mechanics": []}
    )
    with pytest.raises(KeyError, match=CORE_DWARF_HEAVY_ARMOR_SPEED_MECHANIC_ID):
        derive_character_sheet(sheet, rules=rules_2024)

    sheet["inventory"]["encumbrance"]["mode"] = "standard"
    dwarf_actor = {
        "id": "dwarf",
        "name": "Dwarf",
        "initiative": 20,
        "sheet": sheet,
        "derived": derived,
    }
    pursuer_sheet = default_character_sheet()
    pursuer_actor = {
        "id": "pursuer",
        "name": "Pursuer",
        "initiative": 10,
        "sheet": pursuer_sheet,
        "derived": derive_character_sheet(pursuer_sheet),
    }
    encounter = start_encounter([dwarf_actor, pursuer_actor], ruleset="2014")
    dwarf_combatant = next(item for item in encounter["combatants"] if item["actor_id"] == "dwarf")
    assert dwarf_combatant["turn_budget"]["speed"] == 25
    assert dwarf_combatant["turn_budget"]["movement"] == 25
    grappled_actor = deepcopy(dwarf_actor)
    grappled_actor["sheet"]["conditions"] = ["grappled"]
    grappled_actor["sheet"]["effects"] = [
        {
            "id": "grappled-speed",
            "active": True,
            "changes": [
                {
                    "path": "combat.speed.multiplier",
                    "mode": "multiply",
                    "value": 0,
                }
            ],
        }
    ]
    grappled_actor["derived"] = derive_character_sheet(grappled_actor["sheet"])
    grappled_encounter = start_encounter([grappled_actor, pursuer_actor], ruleset="2014")
    grappled_combatant = next(
        item for item in grappled_encounter["combatants"] if item["actor_id"] == "dwarf"
    )
    assert grappled_combatant["speed_multiplier"] == 0
    assert grappled_combatant["turn_budget"]["movement"] == 0
    chase = start_chase(
        [dwarf_actor, pursuer_actor],
        quarry_ids=["dwarf"],
        initial_distance_ft=60,
    )
    dwarf_participant = next(item for item in chase["participants"] if item["actor_id"] == "dwarf")
    assert dwarf_participant["base_speed_ft"] == 25
    assert dwarf_participant["speed_ft"] == 25


def test_effective_ability_modifier_uses_the_shared_override_projection() -> None:
    sheet = default_character_sheet()
    sheet["abilities"]["constitution"]["score"] = 10
    sheet, _ = add_effect(
        sheet,
        {
            "name": "Constitution override",
            "kind": "feature",
            "changes": [
                {
                    "path": "abilities.constitution.score",
                    "mode": "override",
                    "value": 18,
                }
            ],
        },
    )

    assert effective_ability_modifier(sheet, "constitution") == 4


def test_source_effect_can_project_giant_size_and_reconcile_hit_points() -> None:
    sheet = validate_character_sheet(
        {
            "abilities": {"strength": {"score": 16}},
            "combat": {"hp": {"value": 31, "max": 40, "temp": 0}},
            "traits": {"size": "medium"},
        }
    )
    sheet, weapon_id = add_inventory_item(
        sheet,
        {
            "id": "longsword",
            "name": "Longsword",
            "kind": "weapon",
            "mechanics": {
                "attack_type": "melee",
                "damage_formula": "1d8",
                "damage_type": "slashing",
                "reach_ft": 5,
            },
        },
    )
    sheet = equip_inventory_item(sheet, weapon_id, "main_hand")
    sheet, effect_id = add_effect(
        sheet,
        {
            "id": "source-owned-enlargement",
            "name": "Source-owned Enlargement",
            "kind": "custom",
            "duration": {"period": "hour", "remaining": 24},
            "changes": [
                {"path": "traits.size", "mode": "override", "value": "huge"},
                {
                    "path": "abilities.strength.score",
                    "mode": "minimum",
                    "value": 25,
                },
                {
                    "path": "combat.hp.maximum_multiplier",
                    "mode": "multiply",
                    "value": 2,
                },
                {
                    "path": "combat.hp.current_multiplier_on_apply",
                    "mode": "multiply",
                    "value": 2,
                },
                {
                    "path": "combat.melee_reach.bonus_ft",
                    "mode": "add",
                    "value": 5,
                },
                {
                    "path": "rolls.weapon_damage.dice_multiplier",
                    "mode": "multiply",
                    "value": 3,
                },
                {
                    "path": "combat.hp.excess_on_end",
                    "mode": "set",
                    "value": "temporary_hit_points",
                },
            ],
        },
    )

    enlarged = derive_character_sheet(sheet)
    attack = enlarged["inventory"]["weapon_attacks"][0]
    assert enlarged["size"] == "huge"
    assert enlarged["ability_scores"]["strength"] == 25
    assert enlarged["hit_points"]["value"] == 62
    assert enlarged["hit_points"]["max"] == 80
    assert attack["reach_ft"] == 10
    assert attack["damage_formula"] == "3d8"
    assert enlarged["unresolved_rules"] == []

    restored = remove_effect(sheet, effect_id)
    normal = derive_character_sheet(restored)
    assert normal["size"] == "medium"
    assert normal["ability_scores"]["strength"] == 16
    assert normal["hit_points"]["value"] == 40
    assert normal["hit_points"]["max"] == 40
    assert normal["hit_points"]["temp"] == 22
    assert normal["inventory"]["weapon_attacks"][0]["reach_ft"] == 5
    assert normal["inventory"]["weapon_attacks"][0]["damage_formula"] == "1d8"

    stronger_sheet = deepcopy(sheet)
    stronger_sheet["abilities"]["strength"]["score"] = 27
    stronger_sheet, _ = add_effect(
        stronger_sheet,
        {
            "id": "source-owned-strength-minimum",
            "name": "Source-owned Strength Minimum",
            "kind": "custom",
            "changes": [
                {
                    "path": "abilities.strength.score",
                    "mode": "minimum",
                    "value": 25,
                }
            ],
        },
    )
    stronger = derive_character_sheet(stronger_sheet)
    assert stronger["ability_scores"]["strength"] == 27
    assert stronger["unresolved_rules"] == []


def _caster_sheet() -> dict:
    return {
        "progression": {
            "level": 3,
            "classes": [{"name": "Cleric", "level": 3, "hit_die": 8}],
        },
        "abilities": {"wisdom": {"score": 16, "save_proficient": True}},
        "spellcasting": {
            "ability": "wisdom",
            "spell_slots": {"1": {"value": 4, "max": 4, "recovers_on": "long_rest"}},
            "preparation": {
                "mode": "prepared",
                "max_prepared": 2,
                "selected_spell_ids": ["cure-wounds"],
            },
        },
        "content": {
            "spells": [
                {
                    "id": "cure-wounds",
                    "source_key": "srd.cure-wounds",
                    "name": "Cure Wounds",
                    "level": 1,
                    "access": {"known": True},
                },
                {
                    "id": "bless",
                    "source_key": "srd.bless",
                    "name": "Bless",
                    "level": 1,
                    "access": {"known": True},
                },
            ]
        },
    }


def test_world_time_requires_one_canonical_elapsed_instant() -> None:
    expected = {
        "schema_version": 2,
        "tick_seconds": 6,
        "calendar_offset_ticks": 33150,
        "day": 3,
        "hour": 7,
        "minute": 15,
        "second": 0,
        "elapsed_minutes": 3315,
        "round_remainder": 0,
        "label": "Morning",
    }
    assert validate_world_time(expected) == expected

    with pytest.raises(ValueError, match="must match game_time"):
        validate_party_state({"world_time": {**expected, "elapsed_minutes": 3314}})


@pytest.mark.parametrize(
    "world_time",
    [
        {"day": 1, "hour": 24, "minute": 0, "elapsed_minutes": 1440},
        {"day": 0, "hour": 0, "minute": 0, "elapsed_minutes": 0},
        {
            "day": 1,
            "hour": 0,
            "minute": 0,
            "elapsed_minutes": 0,
            "timezone": "UTC",
        },
    ],
)
def test_world_time_rejects_invalid_or_noncanonical_fields(world_time: dict) -> None:
    with pytest.raises(ValueError):
        validate_party_state({"world_time": world_time})


def test_effect_duration_rejects_retired_minute_remainder() -> None:
    sheet = default_character_sheet()
    sheet["effects"] = [
        {
            "id": "legacy-hour",
            "name": "Legacy Hour",
            "active": True,
            "duration": {
                "period": "hour",
                "remaining": 1,
                "elapsed_minutes_remainder": 30,
            },
        }
    ]
    state = {
        "world_effects": [
            {
                "id": "legacy-day",
                "name": "Legacy Day",
                "active": True,
                "duration": {
                    "period": "day",
                    "remaining": 1,
                    "elapsed_minutes_remainder": 60,
                },
            }
        ]
    }

    with pytest.raises(ValueError, match="unsupported fields"):
        validate_character_sheet(sheet)
    with pytest.raises(ValueError, match="unsupported fields"):
        validate_party_state(state)


def test_world_effect_creation_time_has_one_canonical_tick_field() -> None:
    canonical = validate_party_state(
        {
            "world_effects": [
                {
                    "id": "canonical",
                    "name": "Canonical",
                    "created_at_elapsed_ticks": 15,
                }
            ]
        }
    )
    assert canonical["world_effects"][0]["created_at_elapsed_ticks"] == 15
    assert "created_at_elapsed_minutes" not in canonical["world_effects"][0]
    with pytest.raises(ValueError, match="unsupported fields"):
        validate_party_state(
            {
                "world_effects": [
                    {
                        "id": "conflict",
                        "name": "Conflict",
                        "created_at_elapsed_ticks": 15,
                        "created_at_elapsed_minutes": 2,
                    }
                ]
            }
        )


def test_party_state_rejects_retired_combat_phase_copy() -> None:
    with pytest.raises(ValueError, match="game_phase must be lobby or play"):
        validate_party_state({"game_phase": "combat", "combat": {"active": True}})
    with pytest.raises(ValueError, match="game_phase must be lobby or play"):
        validate_party_state({"game_phase": "paused"})


def test_party_state_rejects_retired_module_activation_projection() -> None:
    with pytest.raises(ValueError, match="module_imports is retired"):
        validate_party_state(
            {
                "module_imports": {
                    "active": {
                        "module-key": {
                            "module_id": "stale-module",
                            "checksum": "stale-checksum",
                        }
                    }
                }
            }
        )


def test_character_conditions_are_canonical_identifiers() -> None:
    sheet = default_character_sheet()
    sheet["conditions"] = [" Prone ", "PRONE", "Unconscious"]

    normalized = validate_character_sheet(sheet)

    assert normalized["conditions"] == ["prone", "unconscious"]


def test_v2_sheet_exposes_complete_derived_card_and_prepared_spells() -> None:
    sheet = validate_character_sheet(_caster_sheet())
    assert sheet["schema_version"] == 2
    assert sheet["content"]["spells"][0]["access"]["prepared"] is True
    assert sheet["content"]["spells"][1]["access"]["prepared"] is False

    prepared = set_spell_prepared(sheet, "bless", True)
    assert prepared["spellcasting"]["preparation"]["selected_spell_ids"] == [
        "cure-wounds",
        "bless",
    ]
    derived = derive_character_sheet(prepared)
    assert derived["proficiency_bonus"] == 2
    assert derived["attacks_per_action"] == 1
    assert derived["spellcasting"]["save_dc"] == 13
    assert set(derived["spellcasting"]["prepared_spell_ids"]) == {"cure-wounds", "bless"}


def test_ruling_requirement_rejects_a_resolver_that_disagrees_with_its_kind() -> None:
    sheet = _caster_sheet()
    sheet["content"]["spells"][0]["ruling_requirements"] = [
        {
            "kind": "effect",
            "reason": "Resolve the source-described spell effect.",
            "source_excerpt": "The target is affected as described.",
            "default_resolver": "external_input",
            "ruling_kind": "generic_spell_effect",
            "policy_ref": "server_capabilities.ruling_policy",
            "requires_external_input_only_for": [
                "player_owned_choice",
                "owner_approval",
                "permission_escalation",
                "missing_or_conflicting_source_review",
            ],
        }
    ]

    with pytest.raises(ValueError, match="default_resolver must be agent"):
        validate_character_sheet(sheet)

    sheet["content"]["spells"][0]["ruling_requirements"][0].update(
        default_resolver="agent",
        ruling_kind="missing_or_conflicting_source_review",
    )
    with pytest.raises(ValueError, match="default_resolver must be external_input"):
        validate_character_sheet(sheet)


def test_feature_card_preserves_bounded_agent_ruling_context() -> None:
    sheet = default_character_sheet()
    sheet["content"]["features"] = [
        {
            "id": "source-feature",
            "name": "Source Feature",
            "source_key": "Test Class",
            "description": "x" * 4000,
            "ruling_requirements": [
                {
                    "kind": "feature_semantics",
                    "reason": "Settle the exact source feature through public primitives.",
                    "source_excerpt": "The source-defined effect applies.",
                    "default_resolver": "agent",
                    "ruling_kind": "source_or_scene_fact",
                }
            ],
        }
    ]

    normalized = validate_character_sheet(sheet)

    requirement = normalized["content"]["features"][0]["ruling_requirements"][0]
    assert requirement["default_resolver"] == "agent"
    assert requirement["source_excerpt"] == "The source-defined effect applies."


def test_character_content_preserves_full_addon_artifact_identifiers() -> None:
    feature_id = "f" * 300
    spell_id = "s" * 300
    sheet = default_character_sheet()
    sheet["content"]["features"] = [{"id": feature_id, "name": "Addon Feature"}]
    sheet["content"]["spells"] = [{"id": spell_id, "name": "Addon Spell", "level": 1}]
    sheet["effects"] = [
        {
            "name": "Addon Spell Effect",
            "source_spell_id": spell_id,
        }
    ]

    normalized = validate_character_sheet(sheet)

    assert normalized["content"]["features"][0]["id"] == feature_id
    assert normalized["content"]["spells"][0]["id"] == spell_id
    assert normalized["effects"][0]["source_spell_id"] == spell_id

    sheet["content"]["features"][0]["id"] += "x"
    with pytest.raises(ValueError, match="exceeds 300 characters"):
        validate_character_sheet(sheet)


def test_class_prepared_spell_does_not_have_to_be_known() -> None:
    sheet = {
        "progression": {
            "level": 1,
            "classes": [{"name": "Cleric", "level": 1, "hit_die": 8}],
        },
        "spellcasting": {
            "preparation": {
                "mode": "prepared",
                "max_prepared": 1,
                "selected_spell_ids": ["bless"],
            },
        },
        "content": {
            "spells": [
                {
                    "id": "bless",
                    "name": "Bless",
                    "level": 1,
                    "grant": {
                        "source_type": "class",
                        "source_key": "Cleric",
                        "method": "class_prepared",
                    },
                    "access": {"known": False},
                }
            ]
        },
    }

    normalized = validate_character_sheet(sheet)

    assert normalized["content"]["spells"][0]["access"]["known"] is False
    assert normalized["content"]["spells"][0]["access"]["prepared"] is True


def test_inventory_wallet_and_effect_contracts() -> None:
    sheet, item_id = add_inventory_item(
        validate_character_sheet({}),
        {
            "id": "healing-potion",
            "name": "Potion of Healing",
            "kind": "consumable",
            "quantity": 2,
            "weight_oz": 8,
            "price_cp": 5000,
            "description": "A red herbal vial.",
        },
    )
    assert item_id == "healing-potion"
    sheet = adjust_wallet(sheet, "gp", 12)
    sheet = equip_inventory_item(sheet, item_id, "main_hand")
    sheet, effect_id = add_effect(
        sheet,
        {"name": "Bless", "kind": "spell", "source": "srd.bless", "changes": []},
    )
    derived = derive_character_sheet(sheet)
    assert derived["inventory"]["wallet_value_cp"] == 1200
    assert derived["active_effects"] == [{"id": effect_id, "name": "Bless"}]

    remaining, moved = remove_inventory_item(sheet, item_id, 1)
    assert moved["quantity"] == 1
    assert remaining["inventory"]["items"][0]["quantity"] == 1


def test_wallet_valuation_uses_the_shared_denomination_contract() -> None:
    sheet = validate_character_sheet(
        {"inventory": {"wallet": {name: 1 for name in DENOMINATION_CP_VALUES}}}
    )

    assert derive_character_sheet(sheet)["inventory"]["wallet_value_cp"] == sum(
        DENOMINATION_CP_VALUES.values()
    )


def test_2014_currency_weight_is_counted_by_default_and_can_be_opted_out() -> None:
    sheet = validate_character_sheet({"inventory": {"wallet": {"gp": 10}}})

    assert sheet["inventory"]["encumbrance"] == {
        "mode": "standard",
        "ignore_currency_weight": False,
    }
    assert derive_character_sheet(sheet)["inventory"]["total_weight_oz"] == pytest.approx(3.2)

    house_rule_sheet = validate_character_sheet(
        {
            "inventory": {
                "wallet": {"gp": 10},
                "encumbrance": {"ignore_currency_weight": True},
            }
        }
    )

    assert derive_character_sheet(house_rule_sheet)["inventory"]["total_weight_oz"] == 0


def test_default_currency_weight_can_cross_a_variant_encumbrance_threshold() -> None:
    sheet = validate_character_sheet(
        {
            "abilities": {"strength": {"score": 10}},
            "inventory": {
                "wallet": {"cp": 1},
                "items": [
                    {
                        "id": "threshold-load",
                        "name": "Threshold load",
                        "kind": "equipment",
                        "weight_oz": 800,
                    }
                ],
                "encumbrance": {"mode": "variant"},
            },
        }
    )

    derived = derive_character_sheet(sheet)
    assert derived["inventory"]["total_weight_oz"] == pytest.approx(800.32)
    assert derived["inventory"]["encumbrance"]["state"] == "encumbered"
    assert derived["speed"]["walk"] == 20


def test_removing_an_effect_cleans_only_conditions_no_longer_owned() -> None:
    sheet = default_character_sheet()
    sheet["conditions"] = ["prone"]
    fear = {
        "name": "Fear Ray",
        "kind": "timed_conditions",
        "source": "gazer-a",
        "duration": {"period": "source_turn_start", "remaining": 1},
        "changes": [{"path": "conditions", "mode": "add", "value": "frightened"}],
    }
    sheet, first_id = add_effect(sheet, {"id": "fear-a", **fear})
    sheet, second_id = add_effect(
        sheet,
        {"id": "fear-b", **fear, "source": "gazer-b"},
    )
    assert sheet["conditions"] == ["frightened", "prone"]

    one_removed = remove_effect(sheet, first_id)
    assert one_removed["conditions"] == ["frightened", "prone"]

    both_removed = remove_effect(one_removed, second_id)
    assert both_removed["conditions"] == ["prone"]


def test_exhaustion_level_setter_enforces_the_character_sheet_range() -> None:
    base = default_character_sheet()
    base["combat"]["hp"] = {"value": 37, "max": 37, "temp": 0}
    sheet = set_exhaustion_level(base, 3)

    assert sheet["combat"]["exhaustion"] == 3
    level_four = set_exhaustion_level(sheet, 4)
    assert level_four["combat"]["hp"] == {"value": 18, "max": 37, "temp": 0}
    dead = set_exhaustion_level(sheet, 6)
    assert dead["conditions"] == ["dead"]
    with pytest.raises(ValueError, match="at most 6"):
        set_exhaustion_level(sheet, 7)


def test_exhaustion_immunity_blocks_gain_but_allows_recovery() -> None:
    sheet = default_character_sheet()
    sheet["traits"]["condition_immunities"] = ["exhaustion"]
    immune = set_exhaustion_level(sheet, 1)
    assert immune["combat"]["exhaustion"] == 0

    immune["combat"]["exhaustion"] = 2
    recovered = set_exhaustion_level(immune, 1)
    assert recovered["combat"]["exhaustion"] == 1


def test_inventory_weight_supports_rule_book_fractional_ounce_units() -> None:
    sheet = validate_character_sheet(
        {
            "inventory": {
                "items": [
                    {
                        "id": "arrows",
                        "name": "Arrows",
                        "kind": "ammunition",
                        "quantity": 20,
                        "weight_oz": 0.8,
                    },
                    {
                        "id": "crossbow-bolts",
                        "name": "Crossbow bolts",
                        "kind": "ammunition",
                        "quantity": 20,
                        "weight_oz": 1.2,
                    },
                ]
            }
        }
    )

    assert derive_character_sheet(sheet)["inventory"]["total_weight_oz"] == 40


def test_inventory_preserves_long_published_action_descriptions() -> None:
    published_action = "Numbered ammunition effect. " * 100
    sheet, item_id = add_inventory_item(
        validate_character_sheet({}),
        {
            "name": "Reviewed multi-effect action",
            "kind": "equipment",
            "description": published_action,
        },
    )

    assert sheet["inventory"]["items"][0]["id"] == item_id
    assert sheet["inventory"]["items"][0]["description"] == published_action
    with pytest.raises(ValueError, match="exceeds 12000 characters"):
        add_inventory_item(
            validate_character_sheet({}),
            {
                "name": "Unbounded description",
                "kind": "equipment",
                "description": "x" * 12001,
            },
        )


def test_spellbook_inventory_preserves_structured_copy_sources() -> None:
    sheet, item_id = add_inventory_item(
        validate_character_sheet({}),
        {
            "id": "d11-red-spellbook",
            "name": "Red leather spellbook",
            "kind": "spellbook",
            "source_key": "module:avernus:d11:red-spellbook",
            "mechanics": {
                "edition": "2014",
                "spell_ids": [
                    "dnd5e.content.srd2014.spell.burning-hands",
                    "dnd5e.content.srd2014.spell.detect-magic",
                ],
                "unresolved_spell_names": ["Ray of Sickness"],
                "owner_mark": "No recorded owner mark",
                "source_scene_id": "d11-scene",
                "deciphered": True,
                "copyable": True,
            },
        },
    )

    assert item_id == "d11-red-spellbook"
    item = sheet["inventory"]["items"][0]
    assert item["kind"] == "spellbook"
    assert item["mechanics"]["spell_ids"] == [
        "dnd5e.content.srd2014.spell.burning-hands",
        "dnd5e.content.srd2014.spell.detect-magic",
    ]
    assert item["mechanics"]["unresolved_spell_names"] == ["Ray of Sickness"]

    with pytest.raises(ValueError, match="duplicate ids"):
        add_inventory_item(
            validate_character_sheet({}),
            {
                "name": "Invalid spellbook",
                "kind": "spellbook",
                "mechanics": {"spell_ids": ["spell:a", "spell:a"]},
            },
        )


def test_party_state_validates_structured_world_effect_targets() -> None:
    state = validate_party_state(
        {
            "world_effects": [
                {
                    "id": "mace-light",
                    "name": "Light on Mara's mace",
                    "kind": "light",
                    "source_spell_id": "dnd5e.content.srd2014.spell.light",
                    "source_actor_id": "mara",
                    "target": {"kind": "object", "id": "mara-mace", "label": "Mace"},
                    "duration": {"period": "hour", "remaining": 1},
                }
            ]
        }
    )
    assert state["world_effects"][0]["target"]["kind"] == "object"

    with pytest.raises(ValueError, match="target.id is required"):
        validate_party_state(
            {
                "world_effects": [
                    {
                        "name": "Invalid",
                        "target": {"kind": "scene"},
                    }
                ]
            }
        )


def test_equipment_slots_and_ac_derive_from_armor_shield_magic_and_effects() -> None:
    sheet = validate_character_sheet(
        {
            "abilities": {"dexterity": {"score": 16}},
            "combat": {"ac": {"base": 10}},
        }
    )
    sheet, armor_id = add_inventory_item(
        sheet,
        {
            "id": "leather",
            "name": "Leather Armor",
            "kind": "armor",
            "mechanics": {
                "base_ac": 11,
                "dexterity_mode": "full",
                "magic_bonus": 0,
                "stealth_disadvantage": True,
            },
        },
    )
    sheet, shield_id = add_inventory_item(
        sheet,
        {
            "id": "shield",
            "name": "Shield",
            "kind": "shield",
            "mechanics": {"ac_bonus": 2, "magic_bonus": 0},
        },
    )
    sheet, cloak_id = add_inventory_item(
        sheet,
        {
            "id": "cloak",
            "name": "Cloak of Protection",
            "kind": "magic_item",
            "mechanics": {"ac_bonus": 1},
        },
    )
    sheet = equip_inventory_item(sheet, armor_id, "armor")
    sheet = equip_inventory_item(sheet, shield_id, "shield")
    sheet = equip_inventory_item(sheet, cloak_id, "cloak")
    sheet, _ = add_effect(
        sheet,
        {
            "name": "Shield of Faith",
            "kind": "spell",
            "changes": [{"path": "derived.armor_class", "mode": "add", "value": 2}],
        },
    )

    derived = derive_character_sheet(sheet)
    assert derived["armor_class"] == 19
    assert derived["armor_class_breakdown"]["armor"]["dexterity_bonus"] == 3
    assert derived["stealth_disadvantage"] is True
    assert derived["armor_class_breakdown"]["shield"]["bonus"] == 2
    assert derived["armor_class_breakdown"]["magic_items"] == [
        {"item_id": "cloak", "name": "Cloak of Protection", "bonus": 1}
    ]
    assert derived["unresolved_rules"] == []


def test_ac_override_does_not_erase_equipped_armor_stealth_disadvantage() -> None:
    sheet = validate_character_sheet(
        {
            "abilities": {"dexterity": {"score": 12}},
            "combat": {"ac": {"base": 10, "override": 19}},
        }
    )
    sheet, armor_id = add_inventory_item(
        sheet,
        {
            "id": "scale-mail",
            "name": "Scale Mail",
            "kind": "armor",
            "mechanics": {
                "base_ac": 14,
                "dexterity_mode": "max",
                "dexterity_max": 2,
                "magic_bonus": 0,
                "stealth_disadvantage": True,
            },
        },
    )
    sheet = equip_inventory_item(sheet, armor_id, "armor")

    derived = derive_character_sheet(sheet)
    assert derived["armor_class"] == 19
    assert derived["armor_class_breakdown"]["mode"] == "override"
    assert derived["stealth_disadvantage"] is True


def test_imported_ac_override_accepts_magic_item_bonus_and_mage_armor_alternative() -> None:
    sheet = validate_character_sheet(
        {
            "abilities": {"dexterity": {"score": 14}},
            "combat": {"ac": {"base": 12, "override": 12}},
        }
    )
    sheet, staff_id = add_inventory_item(
        sheet,
        {
            "id": "staff-of-defense",
            "name": "Staff of Defense",
            "kind": "magic_item",
            "mechanics": {"ac_bonus": 1},
        },
    )
    sheet = equip_inventory_item(sheet, staff_id, "main_hand")

    held = derive_character_sheet(sheet)
    assert held["armor_class"] == 13
    assert held["armor_class_breakdown"]["mode"] == "override"
    assert held["armor_class_breakdown"]["magic_items"] == [
        {"item_id": "staff-of-defense", "name": "Staff of Defense", "bonus": 1}
    ]

    sheet, effect_id = add_effect(
        sheet,
        {
            "id": "mage-armor",
            "name": "Mage Armor",
            "kind": "spell",
            "changes": [{"path": "combat.ac.unarmored_base", "mode": "override", "value": 13}],
        },
    )
    protected = derive_character_sheet(sheet)

    assert protected["armor_class"] == 16
    assert protected["armor_class_breakdown"]["mode"] == "mage_armor"
    assert protected["armor_class_breakdown"]["effects"] == [
        {
            "effect_id": effect_id,
            "name": "Mage Armor",
            "mode": "override",
            "value": 13,
            "applied": True,
        }
    ]
    assert protected["unresolved_rules"] == []


def test_magic_item_ac_bonus_waits_for_required_attunement() -> None:
    sheet = validate_character_sheet(
        {
            "abilities": {"dexterity": {"score": 14}},
            "combat": {"ac": {"base": 10}},
        }
    )
    sheet, staff_id = add_inventory_item(
        sheet,
        {
            "id": "staff-of-defense",
            "name": "Staff of Defense",
            "kind": "magic_item",
            "attunement": "required",
            "mechanics": {"ac_bonus": 1},
        },
    )
    sheet = equip_inventory_item(sheet, staff_id, "main_hand")

    assert derive_character_sheet(sheet)["armor_class"] == 12

    staff = next(item for item in sheet["inventory"]["items"] if item["id"] == staff_id)
    staff["attunement"] = "attuned"
    assert derive_character_sheet(validate_character_sheet(sheet))["armor_class"] == 13


def test_required_attunement_suppresses_all_equipment_magic_properties() -> None:
    sheet = validate_character_sheet(
        {
            "abilities": {
                "strength": {"score": 16},
                "dexterity": {"score": 14},
            },
            "combat": {"ac": {"base": 10}},
        }
    )
    sheet, armor_id = add_inventory_item(
        sheet,
        {
            "id": "warded-mail",
            "name": "Warded Mail",
            "kind": "armor",
            "attunement": "required",
            "mechanics": {
                "base_ac": 14,
                "dexterity_mode": "none",
                "magic_bonus": 2,
            },
        },
    )
    sheet = equip_inventory_item(sheet, armor_id, "armor")
    sheet, shield_id = add_inventory_item(
        sheet,
        {
            "id": "warded-shield",
            "name": "Warded Shield",
            "kind": "shield",
            "attunement": "required",
            "mechanics": {"ac_bonus": 2, "magic_bonus": 1},
        },
    )
    sheet = equip_inventory_item(sheet, shield_id, "shield")
    sheet, weapon_id = add_inventory_item(
        sheet,
        {
            "id": "flame-blade",
            "name": "Flame Blade",
            "kind": "weapon",
            "attunement": "required",
            "mechanics": {
                "damage_formula": "1d8",
                "damage_type": "slashing",
                "magic_bonus": 2,
                "additional_damage": [{"damage_formula": "1d6", "damage_type": "fire"}],
                "on_hit_effect": "target burns",
            },
        },
    )
    sheet = equip_inventory_item(sheet, weapon_id, "main_hand")

    unattuned = derive_character_sheet(sheet)
    assert unattuned["armor_class"] == 16
    assert unattuned["armor_class_breakdown"]["armor"]["magic_bonus"] == 0
    assert unattuned["armor_class_breakdown"]["shield"]["magic_bonus"] == 0
    attack = unattuned["inventory"]["weapon_attacks"][0]
    assert attack["attack_bonus"] == 5
    assert attack["damage_bonus"] == 3
    assert attack["additional_damage"] == []
    assert attack["on_hit_effect"] == ""
    assert attack["magic_suppressed_by_attunement"] is True

    for item in sheet["inventory"]["items"]:
        if item["id"] in {armor_id, shield_id, weapon_id}:
            item["attunement"] = "attuned"
    attuned = derive_character_sheet(validate_character_sheet(sheet))
    assert attuned["armor_class"] == 19
    attack = attuned["inventory"]["weapon_attacks"][0]
    assert attack["attack_bonus"] == 7
    assert attack["damage_bonus"] == 5
    assert attack["additional_damage"][0]["damage_type"] == "fire"
    assert attack["on_hit_effect"] == "target burns"


def test_weapon_cards_preserve_reviewed_source_bound_resolution_plans() -> None:
    source_excerpt = "On a hit, the binding blade restrains the target."
    sheet = default_character_sheet()
    sheet, weapon_id = add_inventory_item(
        sheet,
        {
            "id": "binding-blade",
            "name": "Binding Blade",
            "kind": "weapon",
            "description": source_excerpt,
            "mechanics": {
                "attack_type": "melee",
                "damage_formula": "1d6",
                "damage_type": "slashing",
                "on_hit_effect": source_excerpt,
            },
            "resolution_plan": {
                "schema_version": 2,
                "id": "module.binding-blade.on-hit",
                "source_card_id": "binding-blade",
                "source_card_kind": "item",
                "trigger": "attack.after_hit",
                "slots": {
                    "target": {
                        "kind": "actor_id",
                        "owner": "agent",
                        "description": "The creature hit by the triggering attack.",
                    },
                },
                "steps": [
                    {
                        "id": "restrain",
                        "op": "condition.apply",
                        "args": {
                            "target_ids": [{"$slot": "target"}],
                            "condition_id": "restrained",
                            "source": "Binding Blade",
                        },
                    }
                ],
                "citations": [
                    {
                        "source": "module-review:binding-blade",
                        "source_ref": {"chunk_id": "binding-blade"},
                        "source_excerpt": source_excerpt,
                    }
                ],
            },
        },
    )
    assert weapon_id == "binding-blade"
    sheet = equip_inventory_item(sheet, weapon_id, "main_hand")

    normalized_item = next(item for item in sheet["inventory"]["items"] if item["id"] == weapon_id)
    attack = derive_character_sheet(sheet)["inventory"]["weapon_attacks"][0]

    assert normalized_item["resolution_plan"]["fingerprint"]
    assert attack["resolution_plan"] == normalized_item["resolution_plan"]
    compiled = compile_resolution_plan(normalized_item["resolution_plan"])
    normalized_item["resolution_solution"] = build_content_solution(
        compiled,
        source_card=normalized_item,
        application_id="choice:binding-blade",
        agent_ruling={
            "default_resolver": "agent",
            "ruling_kind": "module_specific_procedure",
            "decision": ("Store the quoted on-hit condition as this item's solution."),
            "reason": ("The exact source clause deterministically restrains the target."),
        },
    )
    persisted = validate_character_sheet(sheet)
    persisted_item = next(
        item for item in persisted["inventory"]["items"] if item["id"] == weapon_id
    )
    assert persisted_item["resolution_solution"]["plan_fingerprint"] == (compiled.fingerprint)


def test_attunement_enforces_capacity_copies_transfer_and_death() -> None:
    sheet = validate_character_sheet({})
    for index, name in enumerate(("Ring A", "Ring B", "Ring C", "Ring D"), start=1):
        sheet, _ = add_inventory_item(
            sheet,
            {
                "id": f"ring-{index}",
                "name": name,
                "kind": "magic_item",
                "source_key": f"core:item/ring-{index}",
                "attunement": "required",
            },
        )
    for item_id in ("ring-1", "ring-2", "ring-3"):
        sheet = attune_inventory_item(sheet, item_id)
    with pytest.raises(ValueError, match="more than three"):
        attune_inventory_item(sheet, "ring-4")

    duplicate_sheet, _ = add_inventory_item(
        validate_character_sheet({}),
        {
            "id": "ring-copy-1",
            "name": "Ring of Protection",
            "kind": "magic_item",
            "source_key": "core:item/ring-of-protection",
            "attunement": "required",
        },
    )
    duplicate_sheet, _ = add_inventory_item(
        duplicate_sheet,
        {
            "id": "ring-copy-2",
            "name": "Ring of Protection",
            "kind": "magic_item",
            "source_key": "core:item/ring-of-protection",
            "attunement": "required",
        },
    )
    duplicate_sheet = attune_inventory_item(duplicate_sheet, "ring-copy-1")
    with pytest.raises(ValueError, match="more than one copy"):
        attune_inventory_item(duplicate_sheet, "ring-copy-2")

    separate_sources, _ = add_inventory_item(
        validate_character_sheet({}),
        {
            "id": "separate-source-1",
            "name": "Ring of Protection",
            "kind": "magic_item",
            "source_key": "module:first-treasure",
            "attunement": "required",
        },
    )
    separate_sources, _ = add_inventory_item(
        separate_sources,
        {
            "id": "separate-source-2",
            "name": "Ring of Protection",
            "kind": "magic_item",
            "source_key": "module:second-treasure",
            "attunement": "required",
        },
    )
    separate_sources = attune_inventory_item(separate_sources, "separate-source-1")
    with pytest.raises(ValueError, match="more than one copy"):
        attune_inventory_item(separate_sources, "separate-source-2")

    with pytest.raises(ValueError, match="cannot be transferred"):
        receive_inventory_item(
            validate_character_sheet({}),
            next(item for item in sheet["inventory"]["items"] if item["id"] == "ring-1"),
        )

    sheet["conditions"] = ["dead"]
    dead = validate_character_sheet(sheet)
    assert {item["attunement"] for item in dead["inventory"]["items"]} == {"required"}


def test_unarmored_base_formula_keeps_shield_and_chooses_highest_source() -> None:
    sheet = validate_character_sheet(
        {
            "abilities": {"dexterity": {"score": 16}},
            "combat": {"ac": {"base": 10}},
        }
    )
    sheet, shield_id = add_inventory_item(
        sheet,
        {
            "id": "shield",
            "name": "Shield",
            "kind": "shield",
            "mechanics": {"ac_bonus": 2, "magic_bonus": 0},
        },
    )
    sheet = equip_inventory_item(sheet, shield_id, "shield")
    sheet, weaker_id = add_effect(
        sheet,
        {
            "name": "Weaker Formula",
            "kind": "feature",
            "changes": [{"path": "combat.ac.unarmored_base", "mode": "override", "value": 12}],
        },
    )
    sheet, stronger_id = add_effect(
        sheet,
        {
            "name": "Draconic Resilience",
            "kind": "feature",
            "changes": [{"path": "combat.ac.unarmored_base", "mode": "override", "value": 13}],
        },
    )

    derived = derive_character_sheet(sheet)

    assert derived["armor_class"] == 18
    assert derived["armor_class_breakdown"]["mode"] == "unarmored_formula"
    assert derived["armor_class_breakdown"]["shield"]["bonus"] == 2
    applied = {
        item["effect_id"]: item["applied"] for item in derived["armor_class_breakdown"]["effects"]
    }
    assert applied == {weaker_id: False, stronger_id: True}


def test_class_unarmored_formulas_honor_ability_and_shield_conditions() -> None:
    sheet = validate_character_sheet(
        {
            "abilities": {
                "dexterity": {"score": 14},
                "constitution": {"score": 14},
                "wisdom": {"score": 18},
            },
            "combat": {"ac": {"base": 10}},
        }
    )
    sheet, shield_id = add_inventory_item(
        sheet,
        {
            "id": "shield",
            "name": "Shield",
            "kind": "shield",
            "mechanics": {"ac_bonus": 2, "magic_bonus": 0},
        },
    )
    sheet = equip_inventory_item(sheet, shield_id, "shield")
    sheet, barbarian_id = add_effect(
        sheet,
        {
            "name": "Barbarian Unarmored Defense",
            "kind": "feature",
            "changes": [
                {
                    "path": "combat.ac.unarmored_formula",
                    "mode": "override",
                    "value": {
                        "base": 10,
                        "ability": "constitution",
                        "allows_shield": True,
                        "includes_dexterity": True,
                    },
                }
            ],
        },
    )
    sheet, monk_id = add_effect(
        sheet,
        {
            "name": "Monk Unarmored Defense",
            "kind": "feature",
            "changes": [
                {
                    "path": "combat.ac.unarmored_formula",
                    "mode": "override",
                    "value": {
                        "base": 10,
                        "ability": "wisdom",
                        "allows_shield": False,
                        "includes_dexterity": True,
                    },
                }
            ],
        },
    )

    shielded = derive_character_sheet(sheet)
    assert shielded["armor_class"] == 16
    assert shielded["armor_class_breakdown"]["ability_bonus"] == {
        "ability": "constitution",
        "bonus": 2,
    }
    shielded_effects = {
        item["effect_id"]: item["applied"] for item in shielded["armor_class_breakdown"]["effects"]
    }
    assert shielded_effects == {barbarian_id: True, monk_id: False}

    unshielded = equip_inventory_item(sheet, shield_id, None)
    derived = derive_character_sheet(unshielded)
    assert derived["armor_class"] == 16
    assert derived["armor_class_breakdown"]["ability_bonus"] == {
        "ability": "wisdom",
        "bonus": 4,
    }


def test_fixed_natural_armor_formula_omits_dexterity_and_allows_shield() -> None:
    sheet = default_character_sheet()
    sheet["abilities"]["dexterity"]["score"] = 18
    sheet, shield_id = add_inventory_item(
        sheet,
        {
            "id": "shell-shield",
            "name": "Shield",
            "kind": "shield",
            "mechanics": {"ac_bonus": 2, "magic_bonus": 0},
        },
    )
    sheet = equip_inventory_item(sheet, shield_id, "shield")
    sheet, _ = add_effect(
        sheet,
        {
            "name": "Shell Natural Armor",
            "kind": "feature",
            "changes": [
                {
                    "path": "combat.ac.unarmored_formula",
                    "mode": "override",
                    "value": {
                        "base": 17,
                        "ability": None,
                        "allows_shield": True,
                        "includes_dexterity": False,
                    },
                }
            ],
        },
    )

    derived = derive_character_sheet(sheet)
    assert derived["armor_class"] == 19
    assert derived["armor_class_breakdown"]["dexterity_bonus"] == 0


def test_equipment_schema_rejects_incompatible_slots_and_inconsistent_state() -> None:
    with pytest.raises(ValueError, match="base_ac is required"):
        add_inventory_item(
            validate_character_sheet({}),
            {"name": "Broken Armor", "kind": "armor", "mechanics": {}},
        )
    potion_sheet, potion_id = add_inventory_item(
        validate_character_sheet({}),
        {"id": "potion", "name": "Potion", "kind": "consumable"},
    )
    with pytest.raises(ValueError, match="cannot be equipped in armor"):
        equip_inventory_item(potion_sheet, potion_id, "armor")
    with pytest.raises(ValueError, match="equipment slot and item equipped state must agree"):
        validate_character_sheet(
            {
                "inventory": {
                    "items": [
                        {
                            "id": "armor",
                            "name": "Leather",
                            "kind": "armor",
                            "mechanics": {"base_ac": 11},
                        }
                    ],
                    "equipment_slots": {"armor": "armor"},
                }
            }
        )


def test_complete_card_supports_identity_weapons_spells_encumbrance_and_adventure_state() -> None:
    sheet = validate_character_sheet(
        {
            "identity": {
                "gender": "female",
                "age": "27",
                "height_cm": 168,
                "weight_lb": 132,
                "faith": "The Triad",
                "deity": "Tyr",
                "hair": "black",
                "skin": "olive",
                "eyes": "brown",
            },
            "progression": {
                "background": "Soldier",
                "background_grants": {
                    "feature": "Military Rank",
                    "equipment_item_ids": ["longbow"],
                    "languages": ["Common"],
                    "spell_list_expansion": [
                        {
                            "artifact_id": "dnd5e.content.example.spell.command",
                            "name": "Command",
                            "pack_id": "dnd5e.content.example",
                            "pack_version": "1.0.0",
                        }
                    ],
                    "tools": ["Dice set"],
                },
                "species": "Marked Human",
                "species_grants": {
                    "spell_list_expansion": [
                        {
                            "artifact_id": "dnd5e.content.example.spell.misty-step",
                            "name": "Misty Step",
                            "pack_id": "dnd5e.content.example",
                            "pack_version": "1.0.0",
                        }
                    ]
                },
                "subclass_grants": {
                    "spell_list_expansion": [
                        {
                            "artifact_id": "dnd5e.content.example.spell.command",
                            "name": "Command",
                            "pack_id": "dnd5e.content.example",
                            "pack_version": "1.0.0",
                            "source_class": "Warlock",
                        }
                    ]
                },
            },
            "abilities": {"strength": {"score": 16}, "dexterity": {"score": 14}},
            "combat": {
                "inspiration": True,
                "wounded": True,
                "hp_progression": [
                    {"level": 1, "method": "fixed", "value": 10, "source": "Fighter d10"},
                    {"level": 2, "method": "rolled", "value": 7, "source": "d10 roll"},
                ],
            },
            "traits": {"size": "medium", "senses": {"darkvision": 60, "truesight": 30}},
            "spellcasting": {
                "ability": "wisdom",
                "casting_economy": "spell_points",
                "spell_points": {"value": 7, "max": 10, "recovers_on": "long_rest"},
            },
            "content": {
                "spells": [
                    {
                        "id": "bless",
                        "name": "Bless",
                        "level": 1,
                        "point_cost": 2,
                        "definition": {
                            "school": "enchantment",
                            "casting_time": "1 action",
                            "range": {"kind": "distance", "normal_ft": 30},
                            "duration": {
                                "kind": "timed",
                                "value": 1,
                                "unit": "minute",
                                "concentration": True,
                            },
                            "components": {
                                "verbal": True,
                                "somatic": True,
                                "material": True,
                                "material_description": "holy water",
                            },
                            "effect": "Bless up to three creatures.",
                        },
                    }
                ],
                "features": [
                    {
                        "name": "Second Wind",
                        "resource_key": "second_wind",
                        "activation": {"type": "bonus_action", "cost": 1},
                        "scaling": [{"level": 1, "value": 1, "description": "One use."}],
                    }
                ],
            },
            "effects": [
                {
                    "name": "Bless",
                    "source_spell_id": "bless",
                    "concentration": True,
                    "duration": {"period": "round", "remaining": 10},
                }
            ],
            "adventure_state": {
                "reputation": {"Baldur's Gate": 3},
                "contributions": {"Harpers": 1},
                "blessings": ["Blessing of Health"],
                "wards": ["Temple ward"],
                "legendary_boons": ["Boon of Fortitude"],
                "status_tags": ["wanted"],
            },
            "inventory": {
                "encumbrance": {"mode": "variant", "ignore_currency_weight": True},
                "items": [
                    {
                        "id": "arrows",
                        "name": "Arrows",
                        "kind": "ammunition",
                        "quantity": 20,
                        "weight_oz": 1,
                    },
                    {
                        "id": "longbow",
                        "name": "Longbow",
                        "kind": "weapon",
                        "equipped": True,
                        "equipped_slot": "main_hand",
                        "mechanics": {
                            "category": "martial",
                            "attack_type": "ranged",
                            "attack_ability": "dexterity",
                            "damage_formula": "1d8",
                            "damage_type": "piercing",
                            "properties": ["ammunition", "heavy", "two_handed"],
                            "normal_range_ft": 150,
                            "long_range_ft": 600,
                            "ammunition_item_id": "arrows",
                        },
                    },
                    {
                        "id": "bag",
                        "name": "Bag of Holding",
                        "kind": "container",
                        "mechanics": {
                            "capacity_oz": 4000,
                            "weightless_contents": True,
                            "extra_dimensional": True,
                        },
                    },
                    {
                        "id": "anvil",
                        "name": "Anvil",
                        "kind": "equipment",
                        "weight_oz": 1600,
                        "container_id": "bag",
                    },
                ],
                "equipment_slots": {"main_hand": "longbow"},
            },
        }
    )
    assert sheet["progression"]["background_grants"]["spell_list_expansion"] == [
        {
            "artifact_id": "dnd5e.content.example.spell.command",
            "name": "Command",
            "pack_id": "dnd5e.content.example",
            "pack_version": "1.0.0",
        }
    ]
    assert sheet["progression"]["species_grants"]["spell_list_expansion"] == [
        {
            "artifact_id": "dnd5e.content.example.spell.misty-step",
            "name": "Misty Step",
            "pack_id": "dnd5e.content.example",
            "pack_version": "1.0.0",
        }
    ]
    assert sheet["progression"]["subclass_grants"]["spell_list_expansion"] == [
        {
            "artifact_id": "dnd5e.content.example.spell.command",
            "name": "Command",
            "pack_id": "dnd5e.content.example",
            "pack_version": "1.0.0",
            "source_class": "Warlock",
        }
    ]
    assert sheet["identity"]["deity"] == "Tyr"
    assert sheet["content"]["spells"][0]["definition"]["components"]["material"] is True
    assert sheet["effects"][0]["concentration"] is True
    assert sheet["inventory"]["items"][2]["mechanics"]["extra_dimensional"] is True
    derived = derive_character_sheet(sheet)
    assert derived["inventory"]["encumbrance"]["carried_weight_oz"] == 20
    assert derived["inventory"]["weapon_attacks"][0]["attack_bonus"] == 4
    assert derived["inventory"]["weapon_attacks"][0]["damage_expression"] == "1d8 + 2"
    assert derived["hit_point_progression"]["recorded_gain_total"] == 17
    after_shot, consumed = consume_weapon_ammunition(sheet, "longbow")
    assert consumed["item_id"] == "arrows"
    assert (
        next(item for item in after_shot["inventory"]["items"] if item["id"] == "arrows")[
            "quantity"
        ]
        == 19
    )
    last_shot_sheet = validate_character_sheet(after_shot)
    next(item for item in last_shot_sheet["inventory"]["items"] if item["id"] == "arrows")[
        "quantity"
    ] = 1
    empty_quiver, last_arrow = consume_weapon_ammunition(last_shot_sheet, "longbow")
    assert last_arrow["remaining"] == 0
    assert (
        next(item for item in empty_quiver["inventory"]["items"] if item["id"] == "arrows")[
            "quantity"
        ]
        == 0
    )
    with pytest.raises(ValueError, match="not enough"):
        consume_weapon_ammunition(empty_quiver, "longbow")

    notes = validate_character_notes({"profile": {"backstory": "A veteran of the border wars."}})
    assert notes["profile"]["backstory"] == "A veteran of the border wars."


def test_schema_rejects_invalid_ammunition_capacity_and_multiple_concentration_effects() -> None:
    with pytest.raises(ValueError, match="ammunition_item_id"):
        validate_character_sheet(
            {
                "inventory": {
                    "items": [
                        {
                            "id": "bow",
                            "name": "Bow",
                            "kind": "weapon",
                            "mechanics": {"ammunition_item_id": "missing"},
                        }
                    ]
                }
            }
        )
    with pytest.raises(ValueError, match="exceed capacity"):
        validate_character_sheet(
            {
                "inventory": {
                    "items": [
                        {
                            "id": "pack",
                            "name": "Pack",
                            "kind": "container",
                            "mechanics": {"capacity_oz": 1},
                        },
                        {
                            "id": "rope",
                            "name": "Rope",
                            "kind": "equipment",
                            "weight_oz": 2,
                            "container_id": "pack",
                        },
                    ]
                }
            }
        )
    with pytest.raises(ValueError, match="one active concentration"):
        validate_character_sheet(
            {
                "effects": [
                    {"name": "First", "concentration": True},
                    {"name": "Second", "concentration": True},
                ]
            }
        )


def test_schema_rejects_legacy_fields_and_invalid_container_cycles() -> None:
    with pytest.raises(ValueError, match="unsupported fields"):
        validate_character_sheet({"level": 3})
    with pytest.raises(ValueError, match="cycle"):
        validate_character_sheet(
            {
                "inventory": {
                    "items": [
                        {"id": "bag-a", "name": "A", "kind": "container", "container_id": "bag-b"},
                        {"id": "bag-b", "name": "B", "kind": "container", "container_id": "bag-a"},
                    ]
                }
            }
        )
    with pytest.raises(ValueError, match="npc notes.profile.summary"):
        validate_character_notes({}, character_type="npc")
    repaired = validate_character_notes(
        {"profile": {"summary": "Reviewed NPC."}}, character_type="npc"
    )
    assert repaired["profile"]["summary"] == "Reviewed NPC."


def test_content_selection_provenance_is_normalized_and_unique() -> None:
    sheet = validate_character_sheet(
        {
            "content": {
                "selections": [
                    {
                        "artifact_id": "dnd5e.content.srd2014.subclass.path-of-the-berserker",
                        "kind": "subclass",
                        "name": "Path of the Berserker",
                        "pack_id": "dnd5e.content.srd2014",
                        "pack_version": "1.1.0",
                        "rule_refs": ["bundled:srd2014/02_Classes/Barbarian.md"],
                        "selection": {"target_class_name": "Barbarian"},
                    }
                ]
            }
        }
    )
    assert sheet["content"]["selections"][0]["pack_version"] == "1.1.0"
    with pytest.raises(ValueError, match="duplicate artifact ids"):
        validate_character_sheet(
            {
                "content": {
                    "selections": [
                        {"artifact_id": "same", "kind": "background"},
                        {"artifact_id": "same", "kind": "subclass"},
                    ]
                }
            }
        )


def test_2014_exhaustion_halves_effective_hit_point_maximum() -> None:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["combat"]["hp"] = {"value": 37, "max": 37, "temp": 0}
    sheet["combat"]["exhaustion"] = 4

    normalized = validate_character_sheet(sheet)
    derived = derive_character_sheet(normalized)

    assert normalized["combat"]["hp"]["value"] == 18
    assert derived["hit_points"] == {
        "value": 18,
        "max": 18,
        "temp": 0,
        "base_max": 37,
    }


def test_whole_sheet_validation_enforces_exhaustion_death() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["exhaustion"] = 6

    normalized = validate_character_sheet(sheet)

    assert normalized["conditions"] == ["dead"]


def test_rest_history_rejects_retired_minute_positions() -> None:
    sheet = default_character_sheet()
    sheet["combat"]["rest_history"] = {
        "last_rest_type": "long_rest",
        "last_rest_started_elapsed_minutes": 60,
        "last_rest_completed_elapsed_minutes": 540,
        "last_long_rest_elapsed_minutes": 540,
    }

    with pytest.raises(ValueError, match="unsupported fields"):
        validate_character_sheet(sheet)
