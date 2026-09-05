from __future__ import annotations

import copy

import pytest

from sagasmith_dnd.content_validation import (
    DND_SELECTION_MATERIALIZERS,
    background_materializer_errors,
    build_catalog_review,
    build_selection_contract,
    catalog_review_errors,
    content_fingerprint,
    feat_materializer_errors,
    selection_contract_errors,
    selection_input_errors,
    selection_schema_for_artifact,
    species_materializer_errors,
    subclass_spell_grant_errors,
)


def _artifact() -> dict:
    return {
        "id": "dnd5e.example.spell.star-flare",
        "kind": "spell",
        "card": {
            "name": "Star Flare",
            "level": 1,
            "classes": ["wizard"],
            "definition": {
                "school": "evocation",
                "casting_time": "1 action",
                "range": {"kind": "distance", "normal_ft": 60},
                "components": {
                    "verbal": True,
                    "somatic": True,
                    "material": False,
                },
                "duration": {"kind": "instantaneous", "concentration": False},
                "effect": "A source-defined flare.",
            },
            "description": "A source-defined flare.",
        },
        "source_citations": [
            {
                "source": "rule-source:example",
                "source_ref": {"chunk_key": "example/section-1/chunk-1"},
                "source_excerpt": "A source-defined flare.",
            }
        ],
    }


def _decisions() -> list[dict]:
    checks = {
        "identity": True,
        "classification": True,
        "entry_boundary": True,
        "references": True,
    }
    return [
        {
            "role": "primary",
            "reviewer": "agent:extractor-v1",
            "method": "agent",
            "checks": checks,
            "notes": "Matched the exact entry and type.",
        },
        {
            "role": "critic",
            "reviewer": "agent:critic-v1",
            "method": "agent",
            "checks": checks,
            "notes": "Independently checked boundaries and references.",
        },
    ]


def test_catalog_review_is_check_and_content_bound() -> None:
    artifact = _artifact()
    artifact["catalog_review"] = build_catalog_review(
        artifact,
        decisions=_decisions(),
    )

    assert catalog_review_errors(artifact) == []
    assert artifact["catalog_review"]["reviewed_content_hash"] == content_fingerprint(artifact)

    stale = copy.deepcopy(artifact)
    stale["card"]["level"] = 2
    assert any("stale" in error for error in catalog_review_errors(stale))

    illustrated = copy.deepcopy(artifact)
    illustrated["card"]["image"] = {
        "asset_key": "statblock.example.image",
        "alt": "Example source illustration",
    }
    assert content_fingerprint(illustrated) == content_fingerprint(artifact)
    assert catalog_review_errors(illustrated) == []

    same_reviewer = _decisions()
    same_reviewer[1]["reviewer"] = same_reviewer[0]["reviewer"]
    assert build_catalog_review(_artifact(), decisions=same_reviewer)["status"] == "approved"


def test_catalog_review_cannot_approve_a_failed_boundary_check() -> None:
    decisions = _decisions()
    decisions[0]["checks"]["entry_boundary"] = False

    with pytest.raises(ValueError, match="at least one passing review"):
        build_catalog_review(_artifact(), decisions=decisions)


def test_selection_validation_is_independent_from_runtime_settlement() -> None:
    artifact = _artifact()
    artifact["semantic_resolution"] = {
        "status": "resolved",
        "mode": "agent_ruling",
        "first_use_compilation_required": False,
    }
    artifact["selection_contract"] = build_selection_contract(
        artifact,
        status="ready",
    )

    assert selection_contract_errors(artifact) == []
    assert artifact["selection_contract"]["materializer"] == (DND_SELECTION_MATERIALIZERS["spell"])
    assert artifact["selection_contract"]["schema"] == selection_schema_for_artifact(artifact)
    assert selection_input_errors(artifact, {"method": "spellbook", "source_class": "wizard"}) == []
    assert selection_input_errors(artifact, {"raw_payload": {}}) == [
        "dnd5e.example.spell.star-flare.selection has unsupported fields: raw_payload"
    ]


def test_selection_contract_fails_closed_for_missing_or_stale_materialization() -> None:
    artifact = _artifact()
    assert selection_contract_errors(artifact) == [
        "dnd5e.example.spell.star-flare.selection_contract is required"
    ]

    with pytest.raises(ValueError, match="blocked status requires blockers"):
        build_selection_contract(artifact, status="blocked")

    artifact["selection_contract"] = build_selection_contract(
        artifact,
        status="not_applicable",
    )
    artifact["card"]["name"] = "Changed"
    assert any("stale" in error for error in selection_contract_errors(artifact))


def test_background_materializer_requires_bounded_choice_semantics() -> None:
    card = {
        "name": "Guild Agent",
        "background_grants": {
            "languages": ["Common"],
            "tools": [],
            "equipment_item_ids": [],
            "choices": {
                "language_count": 1,
                "language_options": [],
                "allow_any_language": False,
                "skill_choice_count": 1,
                "skill_options": [],
                "tool_choice_count": 1,
                "tool_options": [],
            },
        },
    }

    assert background_materializer_errors(card) == [
        "background language choices need language_options or allow_any_language",
        "background skill_options cannot satisfy skill_choice_count",
        "background tool_options cannot satisfy tool_choice_count",
    ]

    card["background_grants"]["choices"].update(
        {
            "language_options": ["Draconic", "Goblin"],
            "skill_options": ["Arcana", "Religion"],
            "tool_options": ["Alchemist's Supplies", "Tinker's Tools"],
            "tool_option_groups": [
                {
                    "id": "artisan",
                    "maximum": 1,
                    "options": ["Alchemist's Supplies"],
                },
                {
                    "id": "other",
                    "maximum": 1,
                    "options": ["Tinker's Tools"],
                },
            ],
        }
    )
    assert background_materializer_errors(card) == []

    card["background_grants"]["choices"]["tool_option_groups"][1]["options"] = [
        "Alchemist's Supplies"
    ]
    assert background_materializer_errors(card) == [
        "background tool_option_groups options must not overlap",
        "background tool_option_groups must cover tool_options exactly",
    ]
    card["background_grants"]["choices"]["tool_option_groups"][1]["options"] = ["Tinker's Tools"]

    card["background_grants"]["spell_list_expansion"] = ["Aid", "aid"]
    assert background_materializer_errors(card) == [
        "background spell_list_expansion must be distinct"
    ]

    card["background_grants"]["spell_list_expansion"] = []
    card["skill_proficiencies"] = ["Investigation"]
    card["background_grants"]["skills"] = ["Persuasion"]
    assert background_materializer_errors(card) == [
        "background skill_proficiencies conflict with background_grants.skills"
    ]

    card["background_grants"]["skills"] = ["Investigation"]
    card["background_grants"]["choices"]["skill_options"] = [
        "Investigation",
        "Religion",
    ]
    assert background_materializer_errors(card) == []


def test_background_materializer_accepts_only_reviewed_embedded_equipment() -> None:
    card = {
        "name": "Guild Agent",
        "background_grants": {
            "languages": [],
            "tools": [],
            "equipment_item_ids": [],
            "equipment": {
                "items": [
                    {
                        "inventory_template": {
                            "name": "Identification Papers",
                            "kind": "equipment",
                            "quantity": 1,
                            "description": "Source-reviewed identification papers.",
                            "mechanics": {},
                        }
                    }
                ],
                "wallet": {"gp": 2},
            },
            "choices": {
                "language_count": 0,
                "tool_choice_count": 0,
                "equipment_packages": {
                    "A": {
                        "items": [
                            {
                                "inventory_template": {
                                    "name": "Guild Signet",
                                    "kind": "equipment",
                                    "quantity": 1,
                                    "description": "A source-reviewed guild signet.",
                                    "mechanics": {},
                                },
                                "quantity": 1,
                            }
                        ],
                        "wallet": {"gp": 10},
                    }
                },
            },
        },
    }

    assert background_materializer_errors(card) == []

    card["background_grants"]["equipment"]["wallet"]["gp"] = -1
    assert background_materializer_errors(card) == [
        "background equipment package fixed wallet amounts must be non-negative integers"
    ]
    card["background_grants"]["equipment"]["wallet"]["gp"] = 2

    item = card["background_grants"]["choices"]["equipment_packages"]["A"]["items"][0]
    item["artifact_id"] = "dnd5e.content.srd2014.item.guild-signet"
    assert background_materializer_errors(card) == [
        "background equipment package A items[0] needs exactly one of artifact_id, "
        "selected_tool, or inventory_template"
    ]


def test_species_materializer_rejects_ocr_counts_and_unbounded_choices() -> None:
    card = {
        "name": "Simic Hybrid",
        "grants": {
            "size": "",
            "size_options": ["Medium"],
            "languages": ["Common"],
            "language_choice_count": 100,
            "language_options": [],
            "allow_any_language": False,
            "skill_proficiencies": [],
            "skill_choice_count": 1,
            "skill_options": [],
            "allow_any_skill": False,
            "armor_proficiencies": ["Light Armor"],
            "tool_proficiencies": [],
            "tool_choice_count": 1,
            "tool_options": [],
        },
    }

    errors = species_materializer_errors(card)
    assert "species language_choice_count must be an integer from 0 to 5" in errors
    assert "species skill choices need skill_options or allow_any_skill" in errors
    assert "species tool_options cannot satisfy tool_choice_count" in errors

    card["grants"].update(
        {
            "fly_speed": 30,
            "language_choice_count": 1,
            "language_options": ["Elvish", "Vedalken"],
            "skill_options": ["Arcana", "Medicine"],
            "tool_options": ["Alchemist's Supplies", "Tinker's Tools"],
        }
    )
    assert species_materializer_errors(card) == []
    card["grants"]["fly_speed"] = "30"
    assert species_materializer_errors(card) == ["species fly_speed must be a nonnegative integer"]
    card["grants"]["fly_speed"] = 30
    card["grants"]["armor_proficiencies"] = ["Light Armor", "light armor"]
    assert species_materializer_errors(card) == ["species armor_proficiencies must be distinct"]

    card["grants"]["armor_proficiencies"] = ["Light Armor"]
    card["grants"]["spell_list_expansion"] = ["Aid", "aid"]
    assert species_materializer_errors(card) == ["species spell_list_expansion must be distinct"]


def test_species_materializer_bounds_ability_choices_to_reviewed_options() -> None:
    card = {
        "name": "Changeling",
        "grants": {
            "ability_score_increases": {"charisma": 2},
            "ability_choice": {
                "count": 1,
                "amount": 1,
                "exclude": ["charisma"],
                "options": ["dexterity", "intelligence"],
            },
            "size": "medium",
            "size_options": [],
            "languages": ["Common"],
            "language_choice_count": 0,
            "language_options": [],
            "allow_any_language": False,
            "skill_proficiencies": [],
            "skill_choice_count": 0,
            "skill_options": [],
            "allow_any_skill": False,
            "tool_proficiencies": [],
            "tool_choice_count": 0,
            "tool_options": [],
        },
    }

    assert species_materializer_errors(card) == []
    card["grants"]["ability_choice"]["options"] = ["dexterity", "luck"]
    assert "species ability_choice contains an unknown ability" in (
        species_materializer_errors(card)
    )


def test_species_materializer_accepts_cross_kind_proficiency_and_tool_expertise() -> None:
    card = {
        "name": "Shadowmarked Elf",
        "grants": {
            "ability_score_increases": {"charisma": 1},
            "ability_choice": {"count": 0, "amount": 0, "exclude": [], "options": []},
            "size": "medium",
            "size_options": [],
            "languages": ["Common", "Elvish"],
            "language_choice_count": 0,
            "language_options": [],
            "allow_any_language": False,
            "skill_proficiencies": [],
            "skill_choice_count": 0,
            "skill_options": [],
            "allow_any_skill": False,
            "tool_proficiencies": [],
            "tool_choice_count": 0,
            "tool_options": [],
            "weapon_proficiencies": [],
            "proficiency_choice_groups": [
                {
                    "id": "natural_talent",
                    "count": 1,
                    "options": [
                        {"kind": "skill", "name": "Performance"},
                        {"kind": "tool", "name": "Lute"},
                    ],
                }
            ],
            "tool_expertise_choice_count": 1,
            "tool_expertise_options": [],
            "allow_any_proficient_tool_expertise": True,
        },
    }

    assert species_materializer_errors(card) == []


def test_species_materializer_accepts_narrative_choices_and_fixed_spells() -> None:
    card = {
        "name": "Kalashtar",
        "grants": {
            "narrative_choice_groups": [
                {
                    "id": "psychic_glamour",
                    "count": 1,
                    "options": [
                        "Insight",
                        "Intimidation",
                        "Performance",
                        "Persuasion",
                    ],
                }
            ],
            "spell_grants": [
                {
                    "name": "Detect Magic",
                    "level": 1,
                    "eligible_classes": ["Wizard"],
                    "method": "limited_use",
                    "spellcasting_ability": "intelligence",
                    "free_casts": 0,
                    "recovers_on": None,
                    "allow_slot_cast": False,
                    "minimum_level": 1,
                    "ritual_only": True,
                    "casting_overrides": {
                        "ignore_material_components": True,
                        "duration": {
                            "kind": "timed",
                            "value": 1,
                            "unit": "hour",
                            "concentration": False,
                        },
                    },
                }
            ],
        },
    }

    assert species_materializer_errors(card) == []
    card["grants"]["spell_grants"][0]["casting_overrides"]["effect"] = "changed"
    assert any(
        "casting_overrides has unsupported fields: effect" in error
        for error in species_materializer_errors(card)
    )


def test_species_materializer_accepts_one_bounded_feat_choice() -> None:
    card = {
        "name": "Variant Human",
        "grants": {
            "feat_choice": {"count": 1, "allowed_categories": []},
        },
    }

    assert species_materializer_errors(card) == []
    card["grants"]["feat_choice"]["count"] = 2
    assert species_materializer_errors(card) == ["species feat_choice.count must be 1"]


def test_species_materializer_validates_resources_and_fixed_spell_levels() -> None:
    card = {
        "name": "Eladrin",
        "grants": {
            "resources": {
                "species:eladrin:fey_step": {
                    "label": "Fey Step",
                    "value": 1,
                    "max": 1,
                    "recovers_on": "short_rest",
                    "source_key": "Eladrin",
                }
            },
            "spell_grants": [
                {
                    "name": "Ray of Sickness",
                    "level": 1,
                    "eligible_classes": ["Wizard"],
                    "method": "limited_use",
                    "spellcasting_ability": "charisma",
                    "free_casts": 1,
                    "recovers_on": "long_rest",
                    "allow_slot_cast": False,
                    "minimum_level": 3,
                    "ritual_only": False,
                    "casting_overrides": {"fixed_cast_level": 2},
                }
            ],
        },
    }

    assert species_materializer_errors(card) == []
    card["grants"]["resources"]["species:eladrin:fey_step"]["value"] = 2
    assert any("value cannot exceed max" in error for error in species_materializer_errors(card))
    card["grants"]["resources"]["species:eladrin:fey_step"]["value"] = 1
    card["grants"]["spell_grants"][0]["casting_overrides"]["fixed_cast_level"] = 0
    assert any(
        "fixed_cast_level must be at least 1" in error
        for error in species_materializer_errors(card)
    )


def test_species_materializer_accepts_decreases_defenses_and_shared_spell_resources() -> None:
    card = {
        "name": "Legacy Source Species",
        "grants": {
            "ability_score_increases": {"dexterity": 2},
            "ability_score_decreases": {"strength": 2},
            "resistances": ["cold"],
            "immunities": ["poison"],
            "condition_immunities": ["poisoned"],
            "natural_armor_base": 13,
            "natural_armor_includes_dexterity": True,
            "natural_weapons": [
                {
                    "name": "Bite",
                    "attack_ability": "strength",
                    "damage_formula": "1d6",
                    "damage_type": "piercing",
                    "reach_ft": 5,
                    "description": "A natural weapon.",
                }
            ],
            "spell_grants": [
                {
                    "name": spell_name,
                    "level": 1,
                    "eligible_classes": ["Wizard"],
                    "method": "limited_use",
                    "spellcasting_ability": "intelligence",
                    "free_casts": 1,
                    "recovers_on": "long_rest",
                    "resource_group": "source_magic",
                    "allow_slot_cast": False,
                    "minimum_level": 1,
                    "ritual_only": False,
                }
                for spell_name in ("Detect Magic", "Disguise Self")
            ],
        },
    }

    assert species_materializer_errors(card) == []
    card["grants"]["spell_grants"][1]["free_casts"] = 2
    assert any(
        "sharing resource_group must use the same" in error
        for error in species_materializer_errors(card)
    )
    card["grants"]["spell_grants"][1]["free_casts"] = 1
    card["grants"]["natural_weapons"][0]["damage_formula"] = "source dice"
    assert any(
        "damage_formula must be one bounded dice formula" in error
        for error in species_materializer_errors(card)
    )
    card["grants"]["natural_weapons"][0]["damage_formula"] = "101d6"
    assert any(
        "damage_formula must be one bounded dice formula" in error
        for error in species_materializer_errors(card)
    )


def test_feat_materializer_accepts_fixed_and_selected_spell_grants() -> None:
    card = {
        "name": "Aberrant Dragonmark",
        "prerequisites": [{"kind": "feature_forbidden", "feature": "dragonmark"}],
        "repeatable": False,
        "mechanical_grants": {
            "ability_score_increases": {"constitution": 1},
            "maximum_ability_score": 20,
            "languages": [],
            "tool_proficiencies": [],
            "weapon_proficiencies": [],
            "spell_grants": [],
        },
        "selection_requirements": {
            "field": "spell_choices",
            "kind": "spell_grants",
            "groups": [
                {
                    "id": "cantrip",
                    "count": 1,
                    "level": 0,
                    "eligible_classes": ["Sorcerer"],
                    "method": "known",
                    "spellcasting_ability": "constitution",
                    "free_casts": 0,
                    "recovers_on": None,
                    "allow_slot_cast": False,
                    "minimum_level": 1,
                    "ritual_only": False,
                },
                {
                    "id": "level_1_spell",
                    "count": 1,
                    "level": 1,
                    "eligible_classes": ["Sorcerer"],
                    "method": "limited_use",
                    "spellcasting_ability": "constitution",
                    "free_casts": 1,
                    "recovers_on": "long_rest",
                    "allow_slot_cast": False,
                    "minimum_level": 1,
                    "ritual_only": False,
                },
            ],
        },
    }

    assert feat_materializer_errors(card) == []

    invalid = copy.deepcopy(card)
    invalid["selection_requirements"]["groups"][1]["eligible_classes"] = []
    assert any(
        "eligible_classes must not be empty" in error for error in feat_materializer_errors(invalid)
    )


def test_feat_materializer_accepts_reviewed_fixed_spell_grants() -> None:
    card = {
        "name": "Greater Dragonmark (Detection)",
        "prerequisites": [
            {"kind": "level_minimum", "minimum": 8},
            {"kind": "feature_required", "feature": "Mark of Detection"},
        ],
        "repeatable": False,
        "selection_requirements": {
            "field": "ability_score_increases",
            "kind": "ability_score_increase",
            "allowed_distributions": [[1]],
            "ability_options": ["charisma", "intelligence"],
            "maximum_score": 20,
        },
        "mechanical_grants": {
            "ability_score_increases": {},
            "maximum_ability_score": 20,
            "languages": [],
            "tool_proficiencies": [],
            "weapon_proficiencies": [],
            "spell_grants": [
                {
                    "name": "See Invisibility",
                    "level": 2,
                    "eligible_classes": ["Bard", "Sorcerer", "Wizard"],
                    "method": "limited_use",
                    "spellcasting_ability": "intelligence",
                    "free_casts": 1,
                    "recovers_on": "long_rest",
                    "allow_slot_cast": False,
                    "minimum_level": 8,
                    "ritual_only": False,
                }
            ],
        },
    }

    assert feat_materializer_errors(card) == []


def test_feat_materializer_accepts_size_or_species_and_proficiency_groups() -> None:
    card = {
        "name": "Reviewed Heritage Training",
        "prerequisites": [
            {
                "kind": "species_or_size",
                "species": ["Dwarf"],
                "sizes": ["Small"],
            }
        ],
        "repeatable": False,
        "mechanical_grants": {
            "ability_score_increases": {},
            "maximum_ability_score": 20,
            "languages": [],
            "tool_proficiencies": [],
            "weapon_proficiencies": [],
            "spell_grants": [],
        },
        "selection_requirements": {
            "field": "training_choices",
            "kind": "proficiency_groups",
            "groups": [
                {
                    "id": "skill",
                    "kind": "skill",
                    "count": 1,
                    "options": ["Athletics", "History"],
                },
                {
                    "id": "expertise",
                    "kind": "skill_expertise",
                    "count": 1,
                    "options": [],
                    "allow_unlisted": True,
                },
            ],
        },
    }

    assert feat_materializer_errors(card) == []

    invalid = copy.deepcopy(card)
    invalid["prerequisites"][0]["sizes"] = ["Colossal"]
    invalid["selection_requirements"]["groups"][1]["id"] = "skill"
    errors = feat_materializer_errors(invalid)
    assert any("supported sizes" in error for error in errors)
    assert any("ids must be distinct" in error for error in errors)


def test_subclass_spell_grants_keep_known_and_prepared_semantics_distinct() -> None:
    card = {
        "name": "Circle of Spores",
        "class_name": "Druid",
        "minimum_level": 2,
        "spell_grants": [
            {"name": "Blindness/Deafness", "minimum_level": 3, "method": "always_prepared"},
            {"name": "Chill Touch", "minimum_level": 2, "method": "known"},
        ],
        "spell_list_expansion": ["Aid"],
    }
    assert subclass_spell_grant_errors(card) == []

    card["spell_grants"][1]["method"] = "prepared"
    assert subclass_spell_grant_errors(card) == [
        "subclass spell_grants[1].method must be always_prepared, known, or spellbook"
    ]

    card["spell_grants"][1]["method"] = "known"
    card["spell_list_expansion"] = ["Aid", "aid"]
    assert subclass_spell_grant_errors(card) == [
        "subclass spell_list_expansion must not repeat a spell"
    ]


def test_subclass_rejects_duplicate_always_prepared_spell_shape() -> None:
    card = {
        "name": "Forge Domain",
        "class_name": "Cleric",
        "minimum_level": 1,
        "always_prepared_spells": [{"name": "Identify", "minimum_level": 1}],
        "spell_grants": [],
        "spell_list_expansion": [],
    }

    assert subclass_spell_grant_errors(card) == [
        "subclass always_prepared_spells is unsupported; use spell_grants with "
        "method=always_prepared"
    ]


@pytest.mark.parametrize("legacy", [[], [{"name": "Identify", "minimum_level": 1}]])
def test_subclass_contract_cannot_hide_legacy_spell_grants_in_projection(legacy) -> None:
    artifact = {
        "id": "fixture.subclass.forge-domain",
        "kind": "subclass",
        "card": {
            "name": "Forge Domain",
            "class_name": "Cleric",
            "minimum_level": 1,
            "spell_grants": [],
            "spell_list_expansion": [],
        },
    }
    # An old binding omits the legacy field although its hash covers the whole
    # card. A valid hash does not prove that the materializer consumes the grants.
    artifact["selection_contract"] = build_selection_contract(artifact, status="ready")
    artifact["card"]["always_prepared_spells"] = legacy
    artifact["selection_contract"]["reviewed_content_hash"] = content_fingerprint(artifact)
    errors = selection_contract_errors(artifact)
    assert any("always_prepared_spells is unsupported" in error for error in errors)
    with pytest.raises(ValueError, match="always_prepared_spells is unsupported"):
        selection_schema_for_artifact(artifact)
    with pytest.raises(ValueError, match="always_prepared_spells is unsupported"):
        build_selection_contract(artifact, status="ready")


def test_canonical_subclass_contract_preserves_distinct_spell_access_modes() -> None:
    artifact = {
        "id": "fixture.subclass.circle-of-spores",
        "kind": "subclass",
        "card": {
            "name": "Circle of Spores",
            "class_name": "Druid",
            "minimum_level": 2,
            "spell_grants": [
                {"name": "Chill Touch", "minimum_level": 2, "method": "known"},
                {"name": "Gentle Repose", "minimum_level": 3, "method": "always_prepared"},
            ],
            "spell_list_expansion": [],
        },
    }
    before = copy.deepcopy(artifact)
    contract = build_selection_contract(artifact, status="ready")
    assert artifact == before
    assert contract["schema"]["card_binding"] == artifact["card"]
    assert selection_contract_errors({**artifact, "selection_contract": contract}) == []


@pytest.mark.parametrize(
    ("kind", "card", "selection_fields"),
    [
        ("activity", {"name": "Special Action"}, []),
        (
            "background",
            {"name": "Sage", "background_grants": {}},
            [
                "ability_score_increases",
                "custom_name",
                "equipment_item_ids",
                "equipment_package",
                "languages",
                "origin_feat_selection",
                "skills",
                "tools",
            ],
        ),
        (
            "feat",
            {
                "name": "Adept",
                "prerequisites": [],
                "repeatable": False,
                "selection_requirements": {"field": "chosen_skill"},
            },
            ["chosen_skill"],
        ),
        (
            "class",
            {
                "name": "Artificer",
                "class_definition": {
                    "hit_die": 8,
                    "saving_throw_proficiencies": ["constitution", "intelligence"],
                    "armor_proficiencies": ["light armor"],
                    "weapon_proficiencies": ["simple weapons"],
                    "tool_proficiencies": ["thieves' tools"],
                    "skill_choice_count": 2,
                    "skill_options": ["arcana", "investigation", "medicine"],
                },
            },
            ["skills", "tools"],
        ),
        (
            "feature",
            {
                "name": "Training",
                "class_name": "Fighter",
                "minimum_level": 2,
                "selection_requirements": {"field": "style"},
                "selection_requirements_by_level": {"4": {"field": "ability_score_increases"}},
                "mechanical_grants": {},
            },
            [
                "ability_score_increases",
                "grant_level",
                "initial_setup_full_hp",
                "replace_existing",
                "study_started_elapsed_minutes",
                "study_started_elapsed_ticks",
                "style",
                "tool_replacements",
            ],
        ),
        (
            "item",
            {
                "name": "Moon Blade",
                "inventory_template": {
                    "name": "Moon Blade",
                    "kind": "weapon",
                    "quantity": 1,
                },
            },
            [],
        ),
        (
            "species",
            {"name": "Elf", "grants": {}},
            [
                "abilities",
                "ability_scores_include_species_grants",
                "cantrip_artifact_id",
                "feat_selection",
                "feature_choices",
                "hit_points_include_species_grants",
                "languages",
                "proficiency_choices",
                "size",
                "skills",
                "tool_expertise",
                "tools",
                "values_include_species_grants",
            ],
        ),
        (
            "subclass",
            {"name": "Champion", "class_name": "Fighter", "minimum_level": 3},
            ["target_class_name"],
        ),
    ],
)
def test_selection_schema_is_typed_and_card_bound(
    kind: str, card: dict, selection_fields: list[str]
) -> None:
    artifact = {"id": f"example.{kind}", "kind": kind, "card": card}
    artifact["selection_contract"] = build_selection_contract(artifact, status="ready")

    assert selection_contract_errors(artifact) == []
    assert artifact["selection_contract"]["schema"]["selection_fields"] == (selection_fields)

    stale_schema = copy.deepcopy(artifact)
    stale_schema["selection_contract"]["schema"]["selection_fields"].append("payload")
    assert any(
        "schema does not match" in error for error in selection_contract_errors(stale_schema)
    )


def test_unsupported_kind_cannot_claim_a_safe_materializer() -> None:
    artifact = {
        "id": "example.monster",
        "kind": "monster",
        "card": {"name": "Inventor"},
    }
    with pytest.raises(ValueError, match="no safe character materializer"):
        build_selection_contract(artifact, status="ready")

    artifact["selection_contract"] = build_selection_contract(artifact, status="not_applicable")
    assert selection_contract_errors(artifact) == []
