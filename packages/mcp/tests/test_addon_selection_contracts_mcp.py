import hashlib
import json
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest
from sagasmith_core.indexed_source import rule_chunk_key
from sagasmith_core.rule_packs import RulesetUnavailableError
from sagasmith_dnd.character_schema import (
    add_effect,
    add_inventory_item,
    default_character_sheet,
    derive_character_sheet,
)
from sagasmith_dnd.content_validation import (
    build_catalog_review,
    build_selection_contract,
)
from sagasmith_dnd.official_expansions import official_expansion_dependency_rebinds
from sagasmith_dnd.standard_feature_ids import (
    CORE_TORTLE_NATURAL_ARMOR_MECHANIC_ID,
    TORTLE_NATURAL_ARMOR_ARTIFACT_ID,
    TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_CHECKSUM,
    TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
    TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
    TORTLE_NATURAL_ARMOR_LEGACY_PACK_ID,
    TORTLE_NATURAL_ARMOR_SOURCE_KEY,
)
from sagasmith_dnd.standard_spell_ids import (
    CORE_MENDING_MECHANIC_ID,
    CORE_MENDING_SPELL_ID,
)
from sagasmith_dnd.statblocks import (
    compile_parameterized_statblock_solution,
    parameterized_statblock_requirements,
)
from sagasmith_dnd.steel_defender import (
    STEEL_DEFENDER_DEFLECT_ATTACK_MECHANIC_ID,
    STEEL_DEFENDER_VIGILANT_MECHANIC_ID,
)

import sagasmith_dnd_mcp.server as server_module
from sagasmith_dnd_mcp.config import McpConfig
from sagasmith_dnd_mcp.server import (
    _append_selected_proficiencies,
    _apply_skill_proficiency_or_expertise,
    _character_spell_card,
    _feature_requirements_with_active_extensions,
    _materialize_feature_proficiency_groups,
    _reviewed_statblock_variants,
    _validate_group_limited_choices,
    _validated_additive_choices,
    _validated_narrative_choices,
    _validated_species_ability_choices,
    _validated_species_proficiency_choices,
    close_server,
    create_server,
)
from tests.authoring_helpers import import_and_activate_addon_fixture


def test_active_addon_options_extend_selector_without_granting_extra_choices() -> None:
    requirements = {
        "field": "options",
        "count": 1,
        "options": ["Armor of Shadows"],
        "option_prerequisites": {},
    }
    option = {
        "id": "dnd5e.addon.xanathar.feature.shroud-of-shadow",
        "kind": "feature",
        "card": {
            "name": "Shroud of Shadow",
            "class_name": "Warlock",
            "minimum_level": 15,
            "feature_subtype": "selectable_option",
            "extends_feature": {
                "name": "Eldritch Invocations",
                "class_name": "Warlock",
            },
            "at_will_spell": "Invisibility",
        },
    }

    extended = _feature_requirements_with_active_extensions(
        requirements,
        selector_card={"name": "Eldritch Invocations", "class_name": "Warlock"},
        candidates=[("dnd5e.addon.xanathar", "1.0.0", option)],
    )

    assert extended["count"] == 1
    assert extended["options"] == ["Armor of Shadows", "Shroud of Shadow"]
    assert extended["option_artifact_ids"] == {"Shroud of Shadow": option["id"]}
    assert extended["option_prerequisites"] == {"Shroud of Shadow": {"minimum_level": 15}}
    assert extended["at_will_spells"] == {"Shroud of Shadow": "Invisibility"}


def test_active_addon_option_rejects_conflicting_selector_identity() -> None:
    option = {
        "id": "dnd5e.addon.conflict.feature.armor-of-shadows",
        "kind": "feature",
        "card": {
            "name": "Armor of Shadows",
            "class_name": "Warlock",
            "feature_subtype": "selectable_option",
            "extends_feature": {
                "name": "Eldritch Invocations",
                "class_name": "Warlock",
            },
        },
    }

    with pytest.raises(RulesetUnavailableError, match="conflicts"):
        _feature_requirements_with_active_extensions(
            {
                "field": "options",
                "count": 1,
                "options": ["Armor of Shadows"],
                "option_artifact_ids": {
                    "Armor of Shadows": "dnd5e.srd2014.feature.armor-of-shadows"
                },
            },
            selector_card={
                "name": "Eldritch Invocations",
                "class_name": "Warlock",
            },
            candidates=[("dnd5e.addon.conflict", "1.0.0", option)],
        )


def test_reviewed_statblock_variants_require_full_distinct_matching_cards() -> None:
    source = """# Ox

*Large beast, unaligned*

**Armor Class** 10
**Hit Points** 15 (2d10 + 4)
**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---:|---:|---:|---:|---:|---:|
| 18 (+4) | 10 (+0) | 14 (+2) | 2 (-4) | 10 (+0) | 4 (-3) |

**Senses** passive Perception 10
**Languages** —
**Challenge** 1/4 (50 XP)

## Actions

***Gore.*** Melee Weapon Attack: +6 to hit, reach 5 ft., one target.
Hit: 7 (1d6 + 4) piercing damage.
"""
    card = {"statblock_variants": [{"name": "Ox", "normalized_content": source}]}

    assert _reviewed_statblock_variants(card) == [
        {"name": "Ox", "normalized_content": source.strip()}
    ]
    card["statblock_variants"][0]["name"] = "Rothé"
    with pytest.raises(ValueError, match="heading must match"):
        _reviewed_statblock_variants(card)


async def _call(server, name: str, arguments: dict):
    _, result = await server.call_tool(name, arguments)
    return result.get("result", result) if isinstance(result, dict) else result


def _review_decision(role: str, reviewer: str) -> dict:
    return {
        "role": role,
        "reviewer": reviewer,
        "method": "agent",
        "checks": {
            "identity": True,
            "classification": True,
            "entry_boundary": True,
            "references": True,
        },
        "notes": "Verified against the exact source-bound actor template.",
    }


def test_catalog_spell_projection_strips_search_metadata_and_is_independent() -> None:
    catalog_card = {
        "name": "Source Spell",
        "level": 1,
        "classes": ["Wizard"],
        "description": "Catalog-only retrieval text.",
        "source_title": "Addon Source",
        "definition": {"school": "evocation"},
    }

    projected = _character_spell_card(catalog_card)

    assert projected == {
        "name": "Source Spell",
        "level": 1,
        "definition": {"school": "evocation"},
    }
    catalog_card["definition"]["school"] = "illusion"
    assert projected["definition"]["school"] == "evocation"


def test_background_additive_choices_preserve_fixed_and_enforce_bounds() -> None:
    selected, combined = _validated_additive_choices(
        ["gObLiN"],
        count=1,
        label="background language",
        fixed=["Common"],
        options=["Goblin", "Vedalken"],
    )
    assert selected == ["Goblin"]
    assert combined == ["Common", "Goblin"]

    with pytest.raises(ValueError, match="not one of the allowed options"):
        _validated_additive_choices(
            ["Abyssal"],
            count=1,
            label="background language",
            fixed=["Common"],
            options=["Goblin", "Vedalken"],
        )


def test_background_group_limits_reject_two_choices_from_one_category() -> None:
    groups = [
        {"id": "gaming", "maximum": 1, "options": ["Dice", "Dragonchess"]},
        {"id": "instrument", "maximum": 1, "options": ["Lute"]},
    ]
    _validate_group_limited_choices(["Dice", "Lute"], groups=groups, label="background tool")
    with pytest.raises(ValueError, match="reviewed group limit: gaming"):
        _validate_group_limited_choices(
            ["Dice", "Dragonchess"], groups=groups, label="background tool"
        )


def test_selected_and_conditional_feature_proficiencies_mutate_exactly() -> None:
    languages = ["Common"]
    _append_selected_proficiencies(["Elvish"], target=languages, label="language")
    assert languages == ["Common", "Elvish"]
    with pytest.raises(ValueError, match="already proficient"):
        _append_selected_proficiencies(["elvish"], target=languages, label="language")

    sheet = default_character_sheet()
    _apply_skill_proficiency_or_expertise(sheet, ["Persuasion"])
    assert sheet["skills"]["persuasion"]["proficiency"] == "proficient"
    _apply_skill_proficiency_or_expertise(sheet, ["Persuasion"])
    assert sheet["skills"]["persuasion"]["proficiency"] == "expertise"

    selected = _materialize_feature_proficiency_groups(
        sheet,
        value={"languages": ["Elvish", "Goblin"], "gaming": ["Dice"]},
        groups=[
            {
                "id": "languages",
                "kind": "language",
                "count": 2,
                "options": [],
                "allow_unlisted": True,
            },
            {
                "id": "gaming",
                "kind": "tool",
                "count": 1,
                "options": ["Dice", "Dragonchess"],
            },
        ],
    )
    assert selected == {
        "languages": ["Elvish", "Goblin"],
        "gaming": ["Dice"],
    }
    assert sheet["traits"]["languages"] == ["Elvish", "Goblin"]
    assert sheet["traits"]["proficiencies"]["tools"] == ["Dice"]

    expertise_sheet = default_character_sheet()
    expertise = _materialize_feature_proficiency_groups(
        expertise_sheet,
        value={"skill": ["Stealth"], "expertise": ["Stealth"]},
        groups=[
            {
                "id": "skill",
                "kind": "skill",
                "count": 1,
                "options": ["Stealth"],
            },
            {
                "id": "expertise",
                "kind": "skill_expertise",
                "count": 1,
                "options": [],
                "allow_unlisted": True,
            },
        ],
    )
    assert expertise == {"skill": ["Stealth"], "expertise": ["Stealth"]}
    assert expertise_sheet["skills"]["stealth"]["proficiency"] == "expertise"
    with pytest.raises(ValueError, match="expertise requires proficiency"):
        _materialize_feature_proficiency_groups(
            default_character_sheet(),
            value={"expertise": ["Arcana"]},
            groups=[
                {
                    "id": "expertise",
                    "kind": "skill_expertise",
                    "count": 1,
                    "options": [],
                    "allow_unlisted": True,
                }
            ],
        )
    with pytest.raises(ValueError, match="cannot duplicate a fixed grant"):
        _validated_additive_choices(
            ["common"],
            count=1,
            label="background language",
            fixed=["Common"],
            options=[],
            allow_unlisted=True,
        )
    with pytest.raises(RulesetUnavailableError, match="reviewed options"):
        _validated_additive_choices(
            ["Smith's Tools"],
            count=1,
            label="background tool",
            fixed=[],
            options=[],
        )


def test_species_ability_choices_enforce_reviewed_option_subset() -> None:
    requirement = {
        "count": 1,
        "amount": 1,
        "exclude": ["charisma"],
        "options": ["dexterity", "intelligence"],
    }

    assert _validated_species_ability_choices(
        ["Dexterity"],
        requirement=requirement,
        valid_abilities={
            "strength",
            "dexterity",
            "constitution",
            "intelligence",
            "wisdom",
            "charisma",
        },
    ) == ["dexterity"]
    with pytest.raises(ValueError, match="allowed options"):
        _validated_species_ability_choices(
            ["wisdom"],
            requirement=requirement,
            valid_abilities={
                "strength",
                "dexterity",
                "constitution",
                "intelligence",
                "wisdom",
                "charisma",
            },
        )


def test_species_cross_kind_proficiency_choices_are_bounded_and_typed() -> None:
    groups = [
        {
            "id": "natural_talent",
            "count": 1,
            "options": [
                {"kind": "skill", "name": "Performance"},
                {"kind": "tool", "name": "Lute"},
            ],
        }
    ]

    assert _validated_species_proficiency_choices(
        {"natural_talent": [{"kind": "tool", "name": "lute"}]},
        groups=groups,
    ) == {"natural_talent": [{"kind": "tool", "name": "Lute"}]}
    with pytest.raises(ValueError, match="allowed option"):
        _validated_species_proficiency_choices(
            {"natural_talent": [{"kind": "skill", "name": "Stealth"}]},
            groups=groups,
        )


def test_narrative_choices_preserve_bounded_agent_context_without_false_grants() -> None:
    groups = [
        {
            "id": "psychic_glamour",
            "count": 1,
            "options": ["Insight", "Intimidation", "Performance", "Persuasion"],
        }
    ]

    assert _validated_narrative_choices(
        {"psychic_glamour": ["insight"]},
        groups=groups,
    ) == {"psychic_glamour": ["Insight"]}
    with pytest.raises(ValueError, match="not an allowed option"):
        _validated_narrative_choices(
            {"psychic_glamour": ["Perception"]},
            groups=groups,
        )


@pytest.mark.fresh_database
def test_reviewed_addon_feat_materializes_bounded_spell_sources(tmp_path: Path) -> None:
    workspace = Path(__file__).resolve().parents[3]
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=workspace / "skills",
        modulegen_skills_dir=workspace / "skills" / "dnd-module-generator",
    )

    async def exercise() -> None:
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Addon feat", "idempotency_key": "addon-feat-campaign"},
        )
        profile = await _call(
            server,
            "campaign_rules",
            {
                "campaign_id": campaign["id"],
                "action": "set_profile",
                "payload": {"edition": "2014"},
                "principal_id": "system:local",
                "expected_revision": campaign["revision"],
                "idempotency_key": "addon-feat-profile",
            },
        )
        artifact = {
            "id": "dnd5e.addon.eberron.feat.aberrant-dragonmark",
            "kind": "feat",
            "application_state": "selection_ready",
            "mechanical_scope": "mechanical",
            "execution_state": "engine_ready",
            "semantic_resolution": {
                "status": "resolved",
                "mode": "static_grant",
                "first_use_compilation_required": False,
                "clause_ids": ["aberrant-dragonmark-grants"],
            },
            "rule_clauses": [
                {
                    "schema_version": 1,
                    "id": "aberrant-dragonmark-grants",
                    "title": "Aberrant Dragonmark grants",
                    "scope": "mechanical",
                    "source_citations": [
                        {
                            "source": "book:eberron",
                            "source_ref": {"page": 112},
                            "source_excerpt": (
                                "Increase Constitution by 1 and choose Sorcerer spells."
                            ),
                        }
                    ],
                    "settlement": {
                        "mode": "static_grant",
                        "grant_refs": [
                            "card.mechanical_grants",
                            "card.selection_requirements",
                        ],
                    },
                }
            ],
            "card": {
                "name": "Aberrant Dragonmark",
                "prerequisites": [{"kind": "feature_forbidden", "feature": "dragonmark"}],
                "repeatable": False,
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
                "mechanical_grants": {
                    "ability_score_increases": {"constitution": 1},
                    "maximum_ability_score": 20,
                    "languages": [],
                    "tool_proficiencies": [],
                    "weapon_proficiencies": [],
                    "spell_grants": [],
                },
            },
            "rule_refs": ["book:eberron:p112"],
        }
        artifact["selection_contract"] = build_selection_contract(
            artifact,
            status="ready",
            references=["book:eberron:p112"],
        )
        await import_and_activate_addon_fixture(
            _call,
            server,
            campaign["id"],
            config.home,
            manifest={
                "id": "dnd5e.addon.eberron",
                "version": "1.0.0",
                "title": "Reviewed Eberron",
                "namespace": "dnd5e.addon.eberron",
                "system_id": "dnd5e",
                "editions": ["2014"],
                "capabilities": [],
            },
            artifacts=[artifact],
            mechanics=[],
            expected_revision=profile["campaign_revision"],
            request_key="addon-feat",
        )
        sheet = default_character_sheet()
        sheet["abilities"]["constitution"]["score"] = 10
        character = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Marked Tester", "sheet": sheet},
                "principal_id": "system:local",
                "idempotency_key": "addon-feat-character",
            },
        )
        applied = await _call(
            server,
            "character_content_apply",
            {
                "character_id": character["id"],
                "artifact_id": artifact["id"],
                "selection": {
                    "spell_choices": {
                        "cantrip": ["dnd5e.content.srd2014.spell.light"],
                        "level_1_spell": ["dnd5e.content.srd2014.spell.burning-hands"],
                    }
                },
                "expected_revision": character["revision"],
                "idempotency_key": "addon-feat-apply",
            },
        )

        assert applied["sheet"]["abilities"]["constitution"]["score"] == 11
        spells = {item["id"]: item for item in applied["sheet"]["content"]["spells"]}
        burning_hands = spells["dnd5e.content.srd2014.spell.burning-hands"]
        casting_source = burning_hands["access"]["feature_casting_sources"][0]
        assert casting_source["spellcasting_ability"] == "constitution"
        assert casting_source["allow_slot_cast"] is False
        resource = applied["sheet"]["resources"][casting_source["resource_key"]]
        assert resource["value"] == resource["max"] == 1
        assert resource["recovers_on"] == "long_rest"
        feat = applied["sheet"]["content"]["feats"][0]
        assert feat["choices"]["spell_choices"] == {
            "cantrip": ["dnd5e.content.srd2014.spell.light"],
            "level_1_spell": ["dnd5e.content.srd2014.spell.burning-hands"],
        }

    import asyncio

    asyncio.run(exercise())


@pytest.mark.fresh_database
def test_reviewed_addon_actor_template_derives_owner_values_and_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = Path(__file__).resolve().parents[3]
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=workspace / "skills",
        modulegen_skills_dir=workspace / "skills" / "dnd-module-generator",
    )
    source_text = """### Steel Defender

*Medium construct, neutral*

**Armor Class** 15 (natural armor)

**Hit Points** equal the steel defender's Constitution modifier + your
Intelligence modifier + five times your artificer level

**Speed** 40 ft.

| STR | DEX | CON | INT | WIS | CHA |
|:---:|:---:|:---:|:---:|:---:|:---:|
| 14 (+2) | 12 (+1) | 14 (+2) | 4 (-3) | 10 (+0) | 6 (-2) |

**Senses** darkvision 60 ft., passive Perception 10

**Languages** understands the languages you speak

**Challenge** 1 (200 XP)

###### Actions

***Force-Empowered Rend.*** *Melee Weapon Attack:* your spell attack modifier
to hit, reach 5 ft., one target. *Hit:* 1d8 + PB force damage.
"""
    requirement = parameterized_statblock_requirements(source_text)
    assert requirement is not None and requirement["runtime_ready"] is True
    artifact = {
        "id": "dnd5e.addon.defender.statblock.steel-defender",
        "kind": "statblock",
        "application_state": "catalog_only",
        "mechanical_scope": "mechanical",
        "execution_state": "ruling_ready",
        "semantic_resolution": {
            "status": "resolved",
            "mode": "agent_ruling",
            "first_use_compilation_required": False,
            "clause_ids": ["steel-defender-source"],
        },
        "rule_clauses": [
            {
                "schema_version": 1,
                "id": "steel-defender-source",
                "title": "Steel Defender",
                "scope": "mechanical",
                "source_citations": [
                    {
                        "source": "book:addon:defender",
                        "source_ref": {"page": 1},
                        "source_excerpt": "Steel Defender",
                    }
                ],
                "settlement": {
                    "mode": "agent_ruling",
                    "default_resolver": "agent",
                    "ruling_kind": "agent_dm_adjudication",
                    "reason": "Resolve remaining source-specific behavior as DM.",
                },
            }
        ],
        "card": {
            "name": "Steel Defender",
            "normalized_content": source_text,
            "dependent_actor_template": requirement,
        },
        "rule_refs": ["book:addon:defender:p1"],
    }
    artifact["selection_contract"] = build_selection_contract(
        artifact,
        status="not_applicable",
        references=["book:addon:defender:p1"],
    )
    artifact["catalog_review"] = build_catalog_review(
        artifact,
        decisions=[
            _review_decision("primary", "agent:template-author"),
            _review_decision("critic", "agent:template-critic"),
        ],
    )

    async def exercise() -> None:
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Addon actor", "idempotency_key": "addon-actor-campaign"},
        )
        profile = await _call(
            server,
            "campaign_rules",
            {
                "campaign_id": campaign["id"],
                "action": "set_profile",
                "payload": {"edition": "2014"},
                "principal_id": "system:local",
                "expected_revision": campaign["revision"],
                "idempotency_key": "addon-actor-profile",
            },
        )
        await import_and_activate_addon_fixture(
            _call,
            server,
            campaign["id"],
            config.home,
            manifest={
                "id": "dnd5e.addon.defender",
                "version": "1.0.0",
                "title": "Reviewed Defender",
                "namespace": "dnd5e.addon.defender",
                "system_id": "dnd5e",
                "editions": ["2014"],
                "capabilities": [],
            },
            artifacts=[artifact],
            mechanics=[],
            expected_revision=profile["campaign_revision"],
            request_key="addon-actor",
        )
        owner_sheet = default_character_sheet()
        owner_sheet["progression"]["level"] = 5
        owner_sheet["progression"]["classes"] = [
            {"name": "Artificer", "level": 5, "subclass": "", "hit_die": 8}
        ]
        owner_sheet["abilities"]["intelligence"]["score"] = 18
        owner_sheet["spellcasting"]["ability"] = "intelligence"
        owner = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Owner", "sheet": owner_sheet},
                "principal_id": "system:local",
                "idempotency_key": "addon-actor-owner",
            },
        )
        catalog = await _call(
            server,
            "character_query",
            {
                "view": "catalog",
                "payload": {
                    "campaign_id": campaign["id"],
                    "kind": "statblock",
                    "query": artifact["id"],
                },
                "principal_id": "system:local",
            },
        )
        assert catalog[0]["selection_requirements"]["creation_tool"] == ("addon_actor_instantiate")
        original_variant = server_module.apply_dependent_actor_template_variant

        def forge_rest_window(*args, **kwargs):
            sheet = original_variant(*args, **kwargs)
            sheet["combat"]["short_rest_hit_dice"] = {
                "rest_completed_elapsed_ticks": 600,
                "expected_character_revision": 1,
                "remaining": {"fighter:d10": 1},
                "spent_count": 0,
                "song_of_rest_die_sides": None,
                "song_of_rest_used": False,
            }
            return sheet

        monkeypatch.setattr(
            server_module,
            "apply_dependent_actor_template_variant",
            forge_rest_window,
        )
        with pytest.raises(Exception, match="short_rest_hit_dice"):
            await _call(
                server,
                "addon_actor_instantiate",
                {
                    "campaign_id": campaign["id"],
                    "artifact_id": artifact["id"],
                    "owner_character_id": owner["id"],
                    "idempotency_key": "addon-actor-forged-window",
                },
            )

        def forge_intrinsic_attack(*args, **kwargs):
            sheet = original_variant(*args, **kwargs)
            sheet["traits"]["intrinsic_attacks"] = [
                {
                    "id": "forged-addon-claws",
                    "name": "Forged Claws",
                    "attack_ability": "strength",
                    "damage_formula": "1d4",
                    "damage_type": "slashing",
                    "reach_ft": 5,
                    "source": {
                        "artifact_id": artifact["id"],
                        "pack_id": "dnd5e.addon.defender",
                        "pack_version": "1.0.0",
                        "rule_refs": ["book:addon:defender:p1"],
                    },
                }
            ]
            return sheet

        monkeypatch.setattr(
            server_module,
            "apply_dependent_actor_template_variant",
            forge_intrinsic_attack,
        )
        with pytest.raises(Exception, match="only by character_content_apply"):
            await _call(
                server,
                "addon_actor_instantiate",
                {
                    "campaign_id": campaign["id"],
                    "artifact_id": artifact["id"],
                    "owner_character_id": owner["id"],
                    "idempotency_key": "addon-actor-forged-intrinsic-attack",
                },
            )
        monkeypatch.setattr(
            server_module,
            "apply_dependent_actor_template_variant",
            original_variant,
        )
        created = await _call(
            server,
            "addon_actor_instantiate",
            {
                "campaign_id": campaign["id"],
                "artifact_id": artifact["id"],
                "owner_character_id": owner["id"],
                "idempotency_key": "addon-actor-create",
            },
        )
        replay = await _call(
            server,
            "addon_actor_instantiate",
            {
                "campaign_id": campaign["id"],
                "artifact_id": artifact["id"],
                "owner_character_id": owner["id"],
                "idempotency_key": "addon-actor-create",
            },
        )
        second_owner = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Second Owner",
                    "sheet": owner_sheet,
                },
                "principal_id": "system:local",
                "idempotency_key": "addon-actor-second-owner",
            },
        )
        second_created = await _call(
            server,
            "addon_actor_instantiate",
            {
                "campaign_id": campaign["id"],
                "artifact_id": artifact["id"],
                "owner_character_id": second_owner["id"],
                "idempotency_key": "addon-actor-second-create",
            },
        )

        assert created["character"]["id"] == replay["character"]["id"]
        assert created["character"]["name"] == "Steel Defender (Owner)"
        assert second_created["character"]["name"] == "Steel Defender (Second Owner)"
        assert created["character"]["sheet"]["combat"]["hp"]["max"] == 31
        assert created["content_receipt"]["numeric_parameters"] == {
            "owner_class_level": 5,
            "owner_intelligence_modifier": 4,
            "owner_proficiency_bonus": 3,
            "owner_spell_attack_modifier": 7,
        }
        assert created["actor_knowledge_imported"] is False
        assert (
            "sagasmith:addon-actor-template:"
            in created["character"]["notes"]["profile"]["dm_notes"]
        )

        current = await _call(
            server,
            "campaign_query",
            {
                "view": "get",
                "payload": {"campaign_id": campaign["id"]},
                "principal_id": "system:local",
            },
        )
        phase = await _call(
            server,
            "game_phase",
            {
                "campaign_id": campaign["id"],
                "action": "set",
                "tool_profile": "play",
                "expected_revision": current["revision"],
                "idempotency_key": "addon-actor-play",
            },
        )
        started = await _call(
            server,
            "combat_start",
            {
                "campaign_id": campaign["id"],
                "positioning_mode": "agent",
                "participant_ids": [owner["id"]],
                "expected_revision": phase["campaign_revision"],
                "idempotency_key": "addon-actor-combat",
            },
        )
        combat_arguments = {
            "campaign_id": campaign["id"],
            "artifact_id": artifact["id"],
            "owner_character_id": owner["id"],
            "name": "Combat Steel Defender",
            "participant_config": {
                "initiative": 12,
                "tie_breaker": 1,
                "disposition": "friendly",
            },
            "expected_revision": started["campaign_revision"],
            "idempotency_key": "addon-actor-combat-create",
        }
        combat_created = await _call(server, "addon_actor_instantiate", combat_arguments)
        combat_replay = await _call(server, "addon_actor_instantiate", combat_arguments)
        for field, value in combat_created["character"].items():
            assert combat_replay["character"][field] == value, field
        assert {**combat_replay, "character": {}} == {**combat_created, "character": {}}
        combat_actor_id = combat_created["character"]["id"]
        assert combat_actor_id in {
            item["actor_id"] for item in combat_created["combat"]["combat"]["reinforcements"]
        }

        history = await _call(
            server,
            "state_revision",
            {
                "campaign_id": campaign["id"],
                "action": "history",
                "payload": {},
                "principal_id": "system:local",
            },
        )
        await _call(
            server,
            "state_revision",
            {
                "campaign_id": campaign["id"],
                "action": "undo",
                "payload": {"expected_history_sequence": history[0]["sequence"]},
                "principal_id": "system:local",
                "idempotency_key": "undo-addon-actor-combat-create",
            },
        )
        with pytest.raises(Exception, match=combat_actor_id):
            await _call(
                server,
                "character_query",
                {
                    "view": "get",
                    "payload": {"character_id": combat_actor_id},
                    "principal_id": "system:local",
                },
            )
        undone_combat = await _call(
            server,
            "combat_query",
            {"campaign_id": campaign["id"], "view": "status"},
        )
        assert combat_actor_id not in {item["actor_id"] for item in undone_combat["reinforcements"]}
        undone_history = await _call(
            server,
            "state_revision",
            {
                "campaign_id": campaign["id"],
                "action": "history",
                "payload": {},
                "principal_id": "system:local",
            },
        )
        redo_cursor = next(item["sequence"] for item in undone_history if item["applied"])
        await _call(
            server,
            "state_revision",
            {
                "campaign_id": campaign["id"],
                "action": "redo",
                "payload": {"expected_history_sequence": redo_cursor},
                "principal_id": "system:local",
                "idempotency_key": "redo-addon-actor-combat-create",
            },
        )
        restored_actor = await _call(
            server,
            "character_query",
            {
                "view": "get",
                "payload": {"character_id": combat_actor_id},
                "principal_id": "system:local",
            },
        )
        restored_combat = await _call(
            server,
            "combat_query",
            {"campaign_id": campaign["id"], "view": "status"},
        )
        assert restored_actor["id"] == combat_actor_id
        assert combat_actor_id in {item["actor_id"] for item in restored_combat["reinforcements"]}

    import asyncio

    asyncio.run(exercise())


@pytest.mark.fresh_database
def test_dependent_actor_feature_binding_is_atomic_unique_and_restart_safe(
    tmp_path: Path,
) -> None:
    workspace = Path(__file__).resolve().parents[3]
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=workspace / "skills",
        modulegen_skills_dir=workspace / "skills" / "dnd-module-generator",
    )
    source_text = (
        "### Steel Defender\n\n*Medium construct, neutral*\n\n"
        "**Armor Class** 15 (natural armor)\n\n"
        "**Hit Points** equal the steel defender's Constitution modifier + your Intelligence "
        "modifier + five times your level in this class\n\n"
        "**Speed** 40 ft.\n\n"
        "| STR | DEX | CON | INT | WIS | CHA |\n"
        "|:---:|:---:|:---:|:---:|:---:|:---:|\n"
        "| 14 (+2) | 12 (+1) | 14 (+2) | 4 (-3) | 10 (+0) | 6 (-2) |\n\n"
        "**Saving Throws** Dex +3, Con +4\n\n"
        "**Skills** Athletics +4, Perception +4\n\n"
        "**Senses** darkvision 60 ft., passive Perception 14\n\n"
        "**Languages** understands the languages you speak\n\n"
        "***Might of the Master.*** The following numbers increase by 1 when your proficiency "
        "bonus increases by 1: the defender's skill and saving throw bonuses (above), the "
        "bonuses to hit and damage of its rend attack, and the number of hit points restored by "
        "its Repair action (below).\n\n"
        "***Vigilant.*** The defender can't be surprised.\n\n"
        "###### Actions\n\n"
        "***Force-Empowered Rend.*** *Melee Weapon Attack:* +4 to hit, reach 5 ft., "
        "one target. *Hit:* 1d8 + 2 force damage.\n\n"
        "***Repair (3/Day).*** The magical mechanisms inside the defender restore 2d8 + 2 hit "
        "points to itself or to one construct or object within 5 feet of it.\n\n"
        "###### Reactions\n\n"
        "***Deflect Attack.*** The defender imposes disadvantage on the attack roll of one "
        "creature it can see that is within 5 feet of it, provided the attack roll is against "
        "a creature other than the defender.\n"
    )
    requirement = parameterized_statblock_requirements(source_text)
    assert requirement is not None
    requirement["parameters"].append("owner_proficiency_bonus")
    requirement["owner_class_name"] = "Artificer"
    requirement["owner_class_binding"] = "reviewed_context"
    requirement["solution"] = compile_parameterized_statblock_solution(
        requirement["source_expressions"],
        parameters=requirement["parameters"],
    )
    assert requirement["solution"] is not None
    feature_id = "dnd5e.addon.binding.feature.steel-defender"
    requirement["owner_binding"] = {
        "schema_version": 1,
        "kind": "feature_entitlement",
        "feature_artifact_id": feature_id,
        "relation_key": "steel_defender",
    }
    artifact = {
        "id": "dnd5e.addon.binding.statblock.steel-defender",
        "rule_definition_id": "dnd5e.addon.binding",
        "kind": "statblock",
        "application_state": "catalog_only",
        "mechanical_scope": "mechanical",
        "execution_state": "ruling_ready",
        "semantic_resolution": {
            "status": "resolved",
            "mode": "agent_ruling",
            "first_use_compilation_required": False,
            "clause_ids": ["steel-defender-source"],
        },
        "rule_clauses": [
            {
                "schema_version": 1,
                "id": "steel-defender-source",
                "title": "Steel Defender",
                "scope": "mechanical",
                "source_citations": [
                    {
                        "source": "book:addon:binding",
                        "source_ref": {"page": 1},
                        "source_excerpt": "Steel Defender",
                    }
                ],
                "settlement": {
                    "mode": "agent_ruling",
                    "default_resolver": "agent",
                    "ruling_kind": "agent_dm_adjudication",
                    "reason": "Resolve remaining source-specific behavior as DM.",
                },
            }
        ],
        "card": {
            "name": "Steel Defender",
            "normalized_content": source_text,
            "dependent_actor_template": requirement,
        },
        "rule_refs": ["book:addon:binding:p1"],
    }
    fixture_source_key = "fixture.bound-addon"
    fixture_source_text = (
        "# Reviewed fixture\n\n"
        "## Steel Defender Entitlement\n\n"
        "Mechanics and choices for Steel Defender Entitlement were reviewed for this fixture.\n\n"
        "## Steel Defender\n\n"
        "Mechanics and choices for Steel Defender were reviewed for this fixture."
    )
    fixture_source_checksum = hashlib.sha256(fixture_source_text.encode()).hexdigest()
    fixture_chunk_key = rule_chunk_key(fixture_source_key, 0, 0, fixture_source_text)
    fixture_citation = {
        "source": f"rule-source:{fixture_source_key}",
        "source_key": fixture_source_key,
        "chunk_key": fixture_chunk_key,
        "source_checksum": fixture_source_checksum,
        "page_start": 1,
        "page_end": 1,
        "source_excerpt": fixture_source_text,
    }
    artifact["source_citations"] = [deepcopy(fixture_citation)]
    artifact["selection_contract"] = build_selection_contract(
        artifact,
        status="not_applicable",
        references=["book:addon:binding:p1"],
    )
    artifact["catalog_review"] = build_catalog_review(
        artifact,
        decisions=[
            _review_decision("primary", "agent:binding-author"),
            _review_decision("critic", "agent:binding-critic"),
        ],
    )
    feature_artifact = {
        "id": feature_id,
        "rule_definition_id": "dnd5e.addon.binding",
        "kind": "feature",
        "application_state": "selection_ready",
        "mechanical_scope": "mechanical",
        "execution_state": "engine_ready",
        "semantic_resolution": {
            "status": "resolved",
            "mode": "static_grant",
            "first_use_compilation_required": False,
            "clause_ids": ["steel-defender-entitlement"],
        },
        "rule_clauses": [
            {
                "schema_version": 1,
                "id": "steel-defender-entitlement",
                "title": "Steel Defender entitlement",
                "scope": "mechanical",
                "source_citations": [
                    {
                        "source": "book:addon:binding",
                        "source_ref": {"page": 1},
                        "source_excerpt": "You create a Steel Defender.",
                    }
                ],
                "settlement": {
                    "mode": "static_grant",
                    "grant_refs": ["card.mechanical_grants"],
                },
            }
        ],
        "card": {
            "name": "Steel Defender Entitlement",
            "description": "Authorizes one bound Steel Defender.",
            "minimum_level": 1,
            "repeatable_selection_levels": [],
            "selection_requirements": {},
            "selection_requirements_by_level": {},
            "mechanical_grants": {},
        },
        "rule_refs": ["book:addon:binding:p1"],
        "source_citations": [deepcopy(fixture_citation)],
    }
    feature_artifact["selection_contract"] = build_selection_contract(
        feature_artifact,
        status="ready",
        references=["book:addon:binding:p1"],
    )
    feature_artifact["catalog_review"] = build_catalog_review(
        feature_artifact,
        decisions=[
            _review_decision("primary", "agent:binding-author"),
            _review_decision("critic", "agent:binding-critic"),
        ],
    )

    async def exercise() -> tuple[str, str, str, dict]:
        server = create_server(config)
        try:
            campaign = await _call(
                server,
                "campaign_create",
                {"name": "Bound actor", "idempotency_key": "bound-campaign"},
            )
            profile = await _call(
                server,
                "campaign_rules",
                {
                    "campaign_id": campaign["id"],
                    "action": "set_profile",
                    "payload": {"edition": "2014"},
                    "expected_revision": campaign["revision"],
                    "idempotency_key": "bound-profile",
                },
            )
            await import_and_activate_addon_fixture(
                _call,
                server,
                campaign["id"],
                config.home,
                manifest={
                    "id": "dnd5e.addon.binding",
                    "version": "1.0.0",
                    "title": "Bound Defender",
                    "namespace": "dnd5e.addon.binding",
                    "system_id": "dnd5e",
                    "editions": ["2014"],
                    "capabilities": [],
                },
                artifacts=[feature_artifact, artifact],
                mechanics=[],
                expected_revision=profile["campaign_revision"],
                request_key="bound-addon",
                source_key_override=fixture_source_key,
                source_chunks_override=[fixture_source_text],
            )
            base_sheet = default_character_sheet()
            base_sheet["progression"]["level"] = 3
            base_sheet["progression"]["classes"] = [
                {"name": "Artificer", "level": 3, "subclass": "Battle Smith", "hit_die": 8}
            ]
            base_sheet["abilities"]["intelligence"]["score"] = 16
            base_sheet["spellcasting"]["ability"] = "intelligence"
            base_sheet["spellcasting"]["spell_slots"] = {
                "1": {"value": 1, "max": 1, "unlimited": False}
            }
            base_sheet["content"]["spells"].append(
                {
                    "id": CORE_MENDING_SPELL_ID,
                    "name": "Mending",
                    "level": 0,
                    "grant": {
                        "source_type": "class",
                        "source_key": "artificer",
                        "method": "known",
                    },
                    "access": {"known": True, "prepared": True},
                    "mechanic_refs": [CORE_MENDING_MECHANIC_ID],
                    "definition": {
                        "casting_time": "1 minute",
                        "range": {"kind": "touch"},
                        "duration": {"kind": "instantaneous", "concentration": False},
                        "components": {
                            "verbal": True,
                            "somatic": True,
                            "material": True,
                            "material_description": "two lodestones",
                        },
                        "effect": "Repairs a single break or tear in a touched object.",
                    },
                    "pack_id": "dnd5e.content.srd2014",
                    "pack_version": "1.16.0",
                    "rule_refs": ["bundled:srd2014/07_Spells/Spells_Each/Mending.md"],
                }
            )
            base_sheet, _ = add_inventory_item(
                base_sheet,
                {
                    "id": "smiths-tools",
                    "name": "Smith's Tools",
                    "kind": "tool",
                    "quantity": 1,
                },
            )
            unauthorized = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Missing feature",
                        "sheet": base_sheet,
                    },
                    "idempotency_key": "missing-owner",
                },
            )
            before = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            with pytest.raises(Exception, match="feature entitlement"):
                await _call(
                    server,
                    "addon_actor_instantiate",
                    {
                        "campaign_id": campaign["id"],
                        "artifact_id": artifact["id"],
                        "owner_character_id": unauthorized["id"],
                        "idempotency_key": "unauthorized-defender",
                    },
                )
            assert (
                await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
                == before
            )

            forged_sheet = deepcopy(base_sheet)
            forged_sheet["content"]["features"] = [
                {
                    "id": feature_id,
                    "name": "Steel Defender",
                    "pack_id": "dnd5e.addon.binding",
                    "pack_version": "1.0.0",
                }
            ]
            forged = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Forged feature metadata",
                        "sheet": forged_sheet,
                    },
                    "idempotency_key": "forged-owner",
                },
            )
            with pytest.raises(Exception, match="applied feature entitlement"):
                await _call(
                    server,
                    "addon_actor_instantiate",
                    {
                        "campaign_id": campaign["id"],
                        "artifact_id": artifact["id"],
                        "owner_character_id": forged["id"],
                        "idempotency_key": "forged-defender",
                    },
                )

            async def create_entitled_owner(name: str, key: str) -> dict:
                created_owner = await _call(
                    server,
                    "character_create_from",
                    {
                        "mode": "direct",
                        "payload": {
                            "campaign_id": campaign["id"],
                            "name": name,
                            "sheet": base_sheet,
                        },
                        "idempotency_key": key,
                    },
                )
                applied = await _call(
                    server,
                    "character_content_apply",
                    {
                        "character_id": created_owner["id"],
                        "artifact_id": feature_id,
                        "expected_revision": created_owner["revision"],
                        "idempotency_key": key + "-feature",
                    },
                )
                receipt = applied["rule_receipts"][0]
                assert receipt["event"] == "character.content.apply"
                assert receipt["artifact_id"] == feature_id
                assert len(receipt["artifact_content_hash"]) == 64
                assert "reviewed_content_hash" not in receipt
                return applied

            revoked_owner = await create_entitled_owner("Revoked Battle Smith", "revoked-owner")
            revoked_arguments = {
                "campaign_id": campaign["id"],
                "artifact_id": artifact["id"],
                "owner_character_id": revoked_owner["id"],
                "idempotency_key": "revoked-defender",
            }
            await _call(server, "addon_actor_instantiate", revoked_arguments)
            history = await _call(
                server,
                "state_revision",
                {
                    "campaign_id": campaign["id"],
                    "action": "history",
                    "payload": {},
                    "principal_id": "system:local",
                },
            )
            revoked_actor_revision = next(
                item
                for item in history
                if item["idempotency_key"] == "revoked-defender" and item["applied"]
            )
            await _call(
                server,
                "state_revision",
                {
                    "campaign_id": campaign["id"],
                    "action": "undo",
                    "payload": {"expected_history_sequence": revoked_actor_revision["sequence"]},
                    "principal_id": "system:local",
                    "idempotency_key": "undo-revoked-defender",
                },
            )
            history = await _call(
                server,
                "state_revision",
                {
                    "campaign_id": campaign["id"],
                    "action": "history",
                    "payload": {},
                    "principal_id": "system:local",
                },
            )
            revoked_feature_revision = next(
                item
                for item in history
                if item["idempotency_key"] == "revoked-owner-feature" and item["applied"]
            )
            await _call(
                server,
                "state_revision",
                {
                    "campaign_id": campaign["id"],
                    "action": "undo",
                    "payload": {"expected_history_sequence": revoked_feature_revision["sequence"]},
                    "principal_id": "system:local",
                    "idempotency_key": "undo-revoked-owner-feature",
                },
            )
            with pytest.raises(Exception, match="feature entitlement"):
                await _call(server, "addon_actor_instantiate", revoked_arguments)

            owner = await create_entitled_owner("Battle Smith", "bound-owner")
            arguments = {
                "campaign_id": campaign["id"],
                "artifact_id": artifact["id"],
                "owner_character_id": owner["id"],
                "idempotency_key": "bound-defender",
            }
            created = await _call(server, "addon_actor_instantiate", arguments)
            replay = await _call(server, "addon_actor_instantiate", arguments)
            assert replay["character"] == created["character"]
            after = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            relation = after["state"]["dependent_actor_relations"][0]
            expected_relation = {
                "owner_character_id": owner["id"],
                "dependent_actor_id": created["character"]["id"],
                "relation_key": "steel_defender",
                "source_artifact_id": artifact["id"],
                "source_pack_id": "dnd5e.addon.binding",
                "source_pack_version": "1.0.0",
                "status": "active",
                "created_campaign_revision": after["revision"],
                "created_long_rest_elapsed_ticks": None,
            }
            assert {key: relation[key] for key in expected_relation} == expected_relation
            binding = relation["template_binding"]
            assert binding["owner_class_name"].casefold() == "artificer"
            assert binding["casting_slot_level"] is None
            assert binding["template_variant"] is None
            assert binding["numeric_parameters"] == created["content_receipt"]["numeric_parameters"]
            assert len(binding["reviewed_expression_hash"]) == 64
            authorization = dict(binding["authorization"])
            assert len(authorization.pop("signature")) == 64
            assert authorization == {
                "schema_version": 1,
                "purpose": "dependent_actor_template",
                "campaign_id": campaign["id"],
                "owner_character_id": owner["id"],
                "dependent_actor_id": created["character"]["id"],
                "relation_key": "steel_defender",
                "source_artifact_id": artifact["id"],
                "source_pack_id": "dnd5e.addon.binding",
                "source_pack_version": "1.0.0",
                "owner_class_name": binding["owner_class_name"],
                "casting_slot_level": None,
                "template_variant": None,
                "numeric_parameters": binding["numeric_parameters"],
                "reviewed_expression_hash": binding["reviewed_expression_hash"],
            }

            initial_defender = created["character"]
            vigilant = next(
                item
                for item in initial_defender["sheet"]["content"]["features"]
                if item["name"] == "Vigilant"
            )
            assert vigilant["mechanic_refs"] == [STEEL_DEFENDER_VIGILANT_MECHANIC_ID]
            deflect = next(
                item
                for item in initial_defender["sheet"]["content"]["activities"]
                if item["name"] == "Deflect Attack"
            )
            assert deflect["mechanic_refs"] == [
                STEEL_DEFENDER_DEFLECT_ATTACK_MECHANIC_ID
            ]
            level_four = await _call(
                server,
                "character_state_change",
                {
                    "character_id": owner["id"],
                    "action": "level_advance",
                    "payload": {
                        "class_name": "Artificer",
                        "hp_method": "fixed",
                        "reason": "Steel Defender owner scaling regression",
                        "source_ref": ("bundled:srd2014/03_Characterization/Beyond_1st_Level.md"),
                    },
                    "expected_revision": owner["revision"],
                    "idempotency_key": "bound-owner-level-4",
                },
            )
            owner = level_four["character"]
            defender_four = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": initial_defender["id"]}},
            )
            assert defender_four["sheet"]["combat"]["hp"]["max"] == (
                initial_defender["sheet"]["combat"]["hp"]["max"] + 5
            )
            assert defender_four["sheet"]["abilities"]["dexterity"]["bonus"] == 2

            level_five_arguments = {
                "character_id": owner["id"],
                "action": "level_advance",
                "payload": {
                    "class_name": "Artificer",
                    "hp_method": "fixed",
                    "reason": "Steel Defender owner scaling regression",
                    "source_ref": "bundled:srd2014/03_Characterization/Beyond_1st_Level.md",
                },
                "expected_revision": owner["revision"],
                "idempotency_key": "bound-owner-level-5",
            }
            level_five = await _call(server, "character_state_change", level_five_arguments)
            owner = level_five["character"]
            defender_five = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": initial_defender["id"]}},
            )
            assert defender_five["sheet"]["combat"]["hp"]["max"] == (
                defender_four["sheet"]["combat"]["hp"]["max"] + 5
            )
            assert defender_five["sheet"]["abilities"]["dexterity"]["bonus"] == 3
            assert defender_five["sheet"]["abilities"]["constitution"]["bonus"] == 3
            assert defender_five["sheet"]["skills"]["athletics"]["bonus"] == 3
            assert defender_five["sheet"]["skills"]["perception"]["bonus"] == 5
            rend = next(
                item
                for item in defender_five["sheet"]["inventory"]["items"]
                if item["name"] == "Force-Empowered Rend"
            )
            assert rend["mechanics"]["attack_bonus_override"] == 5
            assert rend["mechanics"]["damage_bonus_override"] == 3
            repair = next(
                item
                for item in defender_five["sheet"]["content"]["activities"]
                if item["name"].startswith("Repair")
            )
            assert "2d8 + 3 hit points" in repair["description"]
            after_level_five = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            refreshed_binding = after_level_five["state"]["dependent_actor_relations"][0][
                "template_binding"
            ]
            refreshed_authorization = dict(refreshed_binding["authorization"])
            assert "authorization" not in refreshed_authorization
            assert refreshed_authorization["numeric_parameters"]["owner_proficiency_bonus"] == 3
            assert await _call(server, "character_state_change", level_five_arguments) == level_five
            assert (
                await _call(
                    server,
                    "character_query",
                    {"view": "get", "payload": {"character_id": initial_defender["id"]}},
                )
                == defender_five
            )
            damaged_for_mending = await _call(
                server,
                "character_state_change",
                {
                    "character_id": initial_defender["id"],
                    "action": "damage",
                    "payload": {
                        "parts": [{"amount": 10, "damage_type": "force"}],
                    },
                    "expected_revision": defender_five["revision"],
                    "idempotency_key": "damage-defender-before-mending",
                },
            )
            mending_arguments = {
                "character_id": owner["id"],
                "action": "cast_spell",
                "payload": {
                    "spell_id": CORE_MENDING_SPELL_ID,
                    "target_character_ids": [initial_defender["id"]],
                    "declaration": {
                        "spatial_facts": {
                            "distance_ft": 5,
                            "default_resolver": "agent",
                            "ruling_kind": "agent_dm_adjudication",
                            "reason": "The owner is touching the adjacent Steel Defender.",
                        }
                    },
                },
                "expected_revision": owner["revision"],
                "idempotency_key": "mend-bound-defender",
            }
            with pytest.raises(Exception, match="spell is not recorded"):
                await _call(
                    server,
                    "character_action",
                    {
                        **mending_arguments,
                        "payload": {
                            **mending_arguments["payload"],
                            "spell_id": "dnd5e.content.srd2014.spell.not-present",
                        },
                        "idempotency_key": "reject-unrecorded-spell",
                    },
                )
            mended = await _call(server, "character_action", mending_arguments)
            assert await _call(server, "character_action", mending_arguments) == mended
            assert mended["result"]["automatic_effect"] == "steel_defender_mending"
            assert mended["result"]["target_id"] == initial_defender["id"]
            assert mended["elapsed_ticks"] == 10
            mending_source_receipt = next(
                item
                for item in mended["result"]["rule_receipts"]
                if item["mechanic_id"] == "dnd5e.expansion.steel_defender.mending"
            )
            assert mending_source_receipt["citations"] == [
                {
                    "source_artifact_id": artifact["id"],
                    "source_pack_id": "dnd5e.addon.binding",
                    "source_pack_version": "1.0.0",
                    "reviewed_expression_hash": requirement["solution"][
                        "reviewed_expression_hash"
                    ],
                }
            ]
            assert any(
                item["mechanic_id"] == CORE_MENDING_MECHANIC_ID
                for item in mended["result"]["rule_receipts"]
            )
            owner = mended["character"]
            defender_after_mending = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": initial_defender["id"]}},
            )
            assert defender_after_mending["sheet"]["combat"]["hp"]["value"] > (
                damaged_for_mending["character"]["sheet"]["combat"]["hp"]["value"]
            )
            damaged_for_item_mending = await _call(
                server,
                "character_state_change",
                {
                    "character_id": initial_defender["id"],
                    "action": "damage",
                    "payload": {"parts": [{"amount": 5, "damage_type": "force"}]},
                    "expected_revision": defender_after_mending["revision"],
                    "idempotency_key": "damage-defender-before-item-mending",
                },
            )
            owner_before_mending_tool = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": owner["id"]}},
            )
            mending_tool = await _call(
                server,
                "inventory_change",
                {
                    "owner": "character",
                    "action": "add",
                    "owner_id": owner["id"],
                    "payload": {
                        "item": {
                            "id": "mending-tool",
                            "name": "Mending Tool",
                            "kind": "magic_item",
                            "source_key": "test:mending-tool",
                            "attunement": "attuned",
                            "charges": {
                                "label": "Mending Tool charges",
                                "value": 1,
                                "max": 1,
                                "recovers_on": "dawn",
                                "source_key": "test:mending-tool",
                            },
                            "mechanics": {
                                "rarity": "artifact",
                                "requires_attunement": True,
                                "spellcasting": {
                                    "requires_attunement": True,
                                    "requires_class_spell_list": False,
                                    "components_required": False,
                                    "spells": [
                                        {
                                            "artifact_id": CORE_MENDING_SPELL_ID,
                                            "charge_cost": 1,
                                            "casting_time": "1 minute",
                                        }
                                    ],
                                },
                            },
                        }
                    },
                    "expected_revision": owner_before_mending_tool["revision"],
                    "idempotency_key": "add-mending-tool",
                },
            )
            await _call(
                server,
                "inventory_change",
                {
                    "owner": "character",
                    "action": "equip",
                    "owner_id": owner["id"],
                    "payload": {"item_id": "mending-tool", "slot": "main_hand"},
                    "expected_revision": mending_tool["character"]["revision"],
                    "idempotency_key": "equip-mending-tool",
                },
            )
            owner_with_mending_tool = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": owner["id"]}},
            )
            item_mending_arguments = {
                "character_id": owner["id"],
                "action": "cast_spell",
                "payload": {
                    "spell_id": CORE_MENDING_SPELL_ID,
                    "source_item_id": "mending-tool",
                    "target_character_ids": [initial_defender["id"]],
                    "declaration": {
                        "spatial_facts": {
                            "distance_ft": 5,
                            "default_resolver": "agent",
                            "ruling_kind": "agent_dm_adjudication",
                            "reason": "The magic item casts Mending on the touched defender.",
                        }
                    },
                },
                "expected_revision": owner_with_mending_tool["revision"],
                "idempotency_key": "item-mend-bound-defender",
            }
            item_mended = await _call(server, "character_action", item_mending_arguments)
            assert item_mended["result"]["automatic_effect"] == "steel_defender_mending"
            owner = item_mended["character"]
            mending_item_after = next(
                item
                for item in owner["sheet"]["inventory"]["items"]
                if item["id"] == "mending-tool"
            )
            assert mending_item_after["charges"]["value"] == 0
            defender_after_item_mending = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": initial_defender["id"]}},
            )
            assert defender_after_item_mending["sheet"]["combat"]["hp"]["value"] > (
                damaged_for_item_mending["character"]["sheet"]["combat"]["hp"]["value"]
            )
            after = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            with pytest.raises(Exception, match="already has an active"):
                await _call(
                    server,
                    "addon_actor_instantiate",
                    {**arguments, "idempotency_key": "duplicate-defender"},
                )
            assert (
                await _call(
                    server,
                    "campaign_query",
                    {"view": "get", "payload": {"campaign_id": campaign["id"]}},
                )
                == after
            )

            second_owner = await create_entitled_owner("Combat Battle Smith", "combat-bound-owner")
            attack_target = await _call(
                server,
                "character_create_from",
                {
                    "mode": "direct",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "name": "Deflect Attack Target",
                        "character_type": "npc",
                        "sheet": default_character_sheet(),
                    },
                    "idempotency_key": "deflect-attack-target",
                },
            )
            await _call(
                server,
                "access_grant",
                {
                    "scope": "campaign",
                    "campaign_id": campaign["id"],
                    "principal_id": "player:combat-battle-smith",
                    "payload": {"role": "player"},
                    "by_principal_id": "system:local",
                },
            )
            await _call(
                server,
                "access_grant",
                {
                    "scope": "campaign",
                    "campaign_id": campaign["id"],
                    "principal_id": "player:unrelated-artificer",
                    "payload": {"role": "player"},
                    "by_principal_id": "system:local",
                },
            )
            await _call(
                server,
                "access_grant",
                {
                    "scope": "actor",
                    "campaign_id": campaign["id"],
                    "principal_id": "player:combat-battle-smith",
                    "payload": {
                        "actor_id": second_owner["id"],
                        "can_control": True,
                        "can_view_private": True,
                    },
                    "by_principal_id": "system:local",
                },
            )
            phase = await _call(
                server,
                "game_phase",
                {
                    "campaign_id": campaign["id"],
                    "action": "set",
                    "tool_profile": "play",
                    "expected_revision": after["revision"],
                    "idempotency_key": "bound-actor-play",
                },
            )
            owner = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": owner["id"]}},
            )
            rested = await _call(
                server,
                "campaign_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "party_rest",
                    "payload": {
                        "rest_type": "long_rest",
                        "duration_minutes": 480,
                        "members": [
                            {
                                "character_id": owner["id"],
                                "expected_revision": owner["revision"],
                            }
                        ],
                    },
                    "expected_revision": phase["campaign_revision"],
                    "idempotency_key": "bound-owner-long-rest",
                },
            )
            rested_owner = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": owner["id"]}},
            )
            await _call(
                server,
                "inventory_change",
                {
                    "owner": "character",
                    "action": "remove",
                    "owner_id": owner["id"],
                    "payload": {"item_id": "smiths-tools"},
                    "expected_revision": rested_owner["revision"],
                    "idempotency_key": "remove-smiths-tools-before-replacement",
                },
            )
            with pytest.raises(Exception, match="smith's tools"):
                await _call(
                    server,
                    "addon_actor_instantiate",
                    {
                        **arguments,
                        "replace_existing": True,
                        "expected_revision": rested["campaign_revision"],
                        "idempotency_key": "replacement-without-tools",
                    },
                )
            without_tools = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": owner["id"]}},
            )
            await _call(
                server,
                "inventory_change",
                {
                    "owner": "character",
                    "action": "add",
                    "owner_id": owner["id"],
                    "payload": {
                        "item": {
                            "id": "smiths-tools",
                            "name": "Smith's Tools",
                            "kind": "tool",
                            "quantity": 1,
                        }
                    },
                    "expected_revision": without_tools["revision"],
                    "idempotency_key": "restore-smiths-tools-for-replacement",
                },
            )
            before_tampered_replacement = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": created["character"]["id"]}},
            )
            database_path = config.home / "data" / "ttrpgbase.db"
            with sqlite3.connect(database_path) as database:
                stored_state = database.execute(
                    "select state from campaigns where id = ?", (campaign["id"],)
                ).fetchone()[0]
                tampered_state = json.loads(stored_state)
                active_relation = next(
                    item
                    for item in tampered_state["dependent_actor_relations"]
                    if item["dependent_actor_id"] == created["character"]["id"]
                    and item["status"] == "active"
                )
                signature = active_relation["template_binding"]["authorization"]["signature"]
                active_relation["template_binding"]["authorization"]["signature"] = (
                    "0" if signature[0] != "0" else "1"
                ) + signature[1:]
                database.execute(
                    "update campaigns set state = ? where id = ?",
                    (json.dumps(tampered_state), campaign["id"]),
                )
            with pytest.raises(Exception, match="authorization signature is invalid"):
                await _call(
                    server,
                    "addon_actor_instantiate",
                    {
                        **arguments,
                        "replace_existing": True,
                        "expected_revision": rested["campaign_revision"],
                        "idempotency_key": "replacement-tampered-signature",
                    },
                )
            assert (
                await _call(
                    server,
                    "character_query",
                    {
                        "view": "get",
                        "payload": {"character_id": created["character"]["id"]},
                    },
                )
                == before_tampered_replacement
            )
            with sqlite3.connect(database_path) as database:
                database.execute(
                    "update campaigns set state = ? where id = ?",
                    (stored_state, campaign["id"]),
                )
            stale_state = json.loads(stored_state)
            stale_relation = next(
                item
                for item in stale_state["dependent_actor_relations"]
                if item["dependent_actor_id"] == created["character"]["id"]
                and item["status"] == "active"
            )
            stale_relation["template_binding"] = deepcopy(binding)
            with sqlite3.connect(database_path) as database:
                database.execute(
                    "update campaigns set state = ? where id = ?",
                    (json.dumps(stale_state), campaign["id"]),
                )
            with pytest.raises(Exception, match="binding is stale or conflicts"):
                await _call(
                    server,
                    "addon_actor_instantiate",
                    {
                        **arguments,
                        "replace_existing": True,
                        "expected_revision": rested["campaign_revision"],
                        "idempotency_key": "replacement-stale-owner-binding",
                    },
                )
            assert (
                await _call(
                    server,
                    "character_query",
                    {
                        "view": "get",
                        "payload": {"character_id": created["character"]["id"]},
                    },
                )
                == before_tampered_replacement
            )
            with sqlite3.connect(database_path) as database:
                database.execute(
                    "update campaigns set state = ? where id = ?",
                    (stored_state, campaign["id"]),
                )
            replacement = await _call(
                server,
                "addon_actor_instantiate",
                {
                    **arguments,
                    "replace_existing": True,
                    "expected_revision": rested["campaign_revision"],
                    "idempotency_key": "replacement-defender",
                },
            )
            assert replacement["replaced_actor_id"] == created["character"]["id"]
            perished = await _call(
                server,
                "character_query",
                {
                    "view": "get",
                    "payload": {"character_id": created["character"]["id"]},
                },
            )
            assert perished["sheet"]["combat"]["hp"]["value"] == 0
            assert "dead" in perished["sheet"]["conditions"]
            replaced_state = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            owner_relations = [
                item
                for item in replaced_state["state"]["dependent_actor_relations"]
                if item["owner_character_id"] == owner["id"]
            ]
            assert [item["status"] for item in owner_relations] == ["replaced", "active"]
            assert owner_relations[1]["created_long_rest_elapsed_ticks"] == replaced_state["state"][
                "game_time"
            ]["elapsed_ticks"]
            with pytest.raises(Exception, match="already created"):
                await _call(
                    server,
                    "addon_actor_instantiate",
                    {
                        **arguments,
                        "replace_existing": True,
                        "expected_revision": replaced_state["revision"],
                        "idempotency_key": "same-rest-replacement-defender",
                    },
                )
            started = await _call(
                server,
                "combat_start",
                {
                    "campaign_id": campaign["id"],
                    "positioning_mode": "agent",
                    "participant_ids": [second_owner["id"]],
                    "expected_revision": replaced_state["revision"],
                    "idempotency_key": "bound-actor-combat",
                },
            )
            combat_arguments = {
                "campaign_id": campaign["id"],
                "artifact_id": artifact["id"],
                "owner_character_id": second_owner["id"],
                "participant_config": {
                    "disposition": "friendly",
                },
                "expected_revision": started["campaign_revision"],
                "idempotency_key": "combat-bound-defender",
            }
            combat_created = await _call(server, "addon_actor_instantiate", combat_arguments)
            combat_replay = await _call(server, "addon_actor_instantiate", combat_arguments)
            assert combat_replay["character"] == combat_created["character"]
            committed = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert len(committed["state"]["dependent_actor_relations"]) == 3
            assert (
                committed["state"]["dependent_actor_relations"][2]["dependent_actor_id"]
                == combat_created["character"]["id"]
            )
            assert combat_created["character"]["id"] in {
                item["actor_id"] for item in combat_created["combat"]["combat"]["reinforcements"]
            }
            owner_combatant = next(
                item
                for item in combat_created["combat"]["combat"]["combatants"]
                if item["actor_id"] == second_owner["id"]
            )
            queued_defender = next(
                item
                for item in combat_created["combat"]["combat"]["reinforcements"]
                if item["actor_id"] == combat_created["character"]["id"]
            )
            assert queued_defender["initiative"] == owner_combatant["initiative"]
            assert queued_defender["tie_breaker"] == owner_combatant["tie_breaker"]
            assert queued_defender["dependent_turn"] == {
                "kind": "steel_defender_2014",
                "owner_actor_id": second_owner["id"],
                "source_artifact_id": artifact["id"],
                "source_pack_id": "dnd5e.addon.binding",
                "source_pack_version": "1.0.0",
                "reviewed_expression_hash": binding["reviewed_expression_hash"],
            }

            joined = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": second_owner["id"],
                    "expected_revision": committed["revision"],
                    "idempotency_key": "join-bound-defender-next-round",
                },
            )
            assert joined["combat"]["round"] == 2
            assert [item["actor_id"] for item in joined["combat"]["combatants"]] == [
                second_owner["id"],
                combat_created["character"]["id"],
            ]
            queued_target = await _call(
                server,
                "combat_join",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": attack_target["id"],
                    "participant_config": {
                        "disposition": "friendly",
                        "initiative": 0,
                    },
                    "expected_revision": joined["campaign_revision"],
                    "idempotency_key": "queue-deflect-attack-target",
                },
            )
            damaged_combat_defender = await _call(
                server,
                "combat_hp_change",
                {
                    "campaign_id": campaign["id"],
                    "target_id": combat_created["character"]["id"],
                    "action": "damage",
                    "payload": {"parts": [{"amount": 10, "damage_type": "force"}]},
                    "expected_revision": queued_target["campaign_revision"],
                    "idempotency_key": "damage-commanded-defender-before-repair",
                },
            )
            command_arguments = {
                "campaign_id": campaign["id"],
                "actor_id": second_owner["id"],
                "action": "command_dependent",
                "target_id": combat_created["character"]["id"],
                "principal_id": "player:combat-battle-smith",
                "expected_revision": damaged_combat_defender["campaign_revision"],
                "idempotency_key": "command-bound-defender",
            }
            commanded = await _call(server, "combat_common_action", command_arguments)
            assert await _call(server, "combat_common_action", command_arguments) == commanded
            commanded_audit = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            commanded_owner = commanded_audit["state"]["combat"]["combatants"][0]
            assert commanded_owner["turn_budget"]["bonus_action"] == 0
            defender_turn = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": second_owner["id"],
                    "principal_id": "player:combat-battle-smith",
                    "expected_revision": commanded["campaign_revision"],
                    "idempotency_key": "begin-commanded-defender-turn",
                },
            )
            defender_turn_audit = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            commanded_defender = defender_turn_audit["state"]["combat"]["combatants"][1]
            assert commanded_defender["turn_budget"]["main_action"] == 1
            assert commanded_defender["turn_flags"]["dependent_command_active"] is True
            combat_repair = next(
                item
                for item in combat_created["character"]["sheet"]["content"]["activities"]
                if item["name"].startswith("Repair")
            )
            repair_arguments = {
                "campaign_id": campaign["id"],
                "actor_id": combat_created["character"]["id"],
                "activity_id": combat_repair["id"],
                "declaration": {
                    "target_id": combat_created["character"]["id"],
                },
                "principal_id": "player:combat-battle-smith",
                "expected_revision": defender_turn["campaign_revision"],
                "idempotency_key": "repair-commanded-defender",
            }
            too_far_repair_arguments = deepcopy(repair_arguments)
            too_far_repair_arguments["declaration"]["spatial_facts"] = {
                "distance_ft": 6,
                "default_resolver": "agent",
                "ruling_kind": "agent_dm_adjudication",
                "reason": "A forged self-distance must not override the exact target.",
            }
            too_far_repair_arguments["idempotency_key"] = "repair-commanded-defender-forged-self"
            with pytest.raises(Exception, match="derives zero distance"):
                await server.call_tool("combat_use_activity", too_far_repair_arguments)
            unrelated_repair_arguments = {
                **repair_arguments,
                "principal_id": "player:unrelated-artificer",
                "idempotency_key": "repair-commanded-defender-unrelated-player",
            }
            with pytest.raises(Exception, match="cannot access actor"):
                await _call(server, "combat_use_activity", unrelated_repair_arguments)
            after_rejected_repair = await _call(
                server,
                "character_query",
                {
                    "view": "get",
                    "payload": {"character_id": combat_created["character"]["id"]},
                },
            )
            rejected_repair_activity = next(
                item
                for item in after_rejected_repair["sheet"]["content"]["activities"]
                if item["id"] == combat_repair["id"]
            )
            assert rejected_repair_activity["uses"]["value"] == 3
            assert after_rejected_repair["sheet"]["combat"]["hp"]["value"] == (
                damaged_combat_defender["result"]["after_hp"]
            )
            _, repaired = await server.call_tool("combat_use_activity", repair_arguments)
            _, repaired_replay = await server.call_tool("combat_use_activity", repair_arguments)
            assert repaired_replay == repaired
            assert repaired["status"] == "committed"
            repaired_defender = await _call(
                server,
                "character_query",
                {
                    "view": "get",
                    "payload": {"character_id": combat_created["character"]["id"]},
                },
            )
            assert repaired_defender["sheet"]["combat"]["hp"]["value"] > (
                damaged_combat_defender["result"]["after_hp"]
            )
            repaired_activity = next(
                item
                for item in repaired_defender["sheet"]["content"]["activities"]
                if item["id"] == combat_repair["id"]
            )
            assert repaired_activity["uses"]["value"] == 2
            owner_round_three = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": combat_created["character"]["id"],
                    "expected_revision": repaired["campaign_revision"],
                    "idempotency_key": "finish-commanded-defender-turn",
                },
            )
            deflect_attack_arguments = {
                "campaign_id": campaign["id"],
                "actor_id": second_owner["id"],
                "target_id": attack_target["id"],
                "action": {
                    "weapon_id": "unarmed-strike",
                    "attack_mode": "melee",
                    "context": {
                        "spatial_facts": {
                            "decision_id": "deflect-attack-primary-spatial-facts",
                            "reason": (
                                "The attacker can reach and see the target in the current scene."
                            ),
                            "targetable": True,
                            "in_range": True,
                            "cover_degree": "none",
                            "attacker_can_see_target": True,
                            "target_can_see_attacker": True,
                        }
                    },
                    "deflect_attack": {
                        "defender_id": combat_created["character"]["id"],
                        "spatial_facts": {
                            "decision_id": "steel-defender-deflect-spatial-facts",
                            "defender_can_see_attacker": True,
                            "attacker_within_5_ft_of_defender": True,
                            "default_resolver": "agent",
                            "ruling_kind": "agent_dm_adjudication",
                            "reason": (
                                "The Steel Defender sees the adjacent attacker before the roll."
                            ),
                        },
                    },
                },
                "expected_revision": owner_round_three["campaign_revision"],
                "idempotency_key": "resolve-source-bound-deflect-attack",
            }
            deflect_preflight = await _call(
                server,
                "combat_preflight_attack",
                {
                    key: value
                    for key, value in deflect_attack_arguments.items()
                    if key not in {"expected_revision", "idempotency_key"}
                },
            )
            assert deflect_preflight["disadvantage"] is True
            assert deflect_preflight["deflect_attack"] == {
                "mechanic_id": STEEL_DEFENDER_DEFLECT_ATTACK_MECHANIC_ID,
                "defender_id": combat_created["character"]["id"],
            }
            ineligible_deflect = deepcopy(deflect_attack_arguments)
            ineligible_deflect["action"]["deflect_attack"]["spatial_facts"][
                "attacker_within_5_ft_of_defender"
            ] = False
            ineligible_deflect["idempotency_key"] = "reject-out-of-range-deflect-attack"
            with pytest.raises(Exception, match="attacker_not_within_5_ft"):
                await server.call_tool("combat_resolve_attack", ineligible_deflect)
            before_deflect = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            assert before_deflect["revision"] == owner_round_three["campaign_revision"]
            assert next(
                item
                for item in before_deflect["state"]["combat"]["combatants"]
                if item["actor_id"] == second_owner["id"]
            )["turn_budget"]["main_action"] == 1
            assert next(
                item
                for item in before_deflect["state"]["combat"]["combatants"]
                if item["actor_id"] == combat_created["character"]["id"]
            )["turn_budget"]["reaction"] == 1
            _, deflected = await server.call_tool(
                "combat_resolve_attack",
                deflect_attack_arguments,
            )
            _, deflected_replay = await server.call_tool(
                "combat_resolve_attack",
                deflect_attack_arguments,
            )
            assert deflected_replay == deflected
            assert len(deflected["result"]["rolls"]) == 2
            assert deflected["result"]["deflect_attack"] == {
                "defender_id": combat_created["character"]["id"],
                "activity_id": deflect["id"],
                "reaction_paid": True,
            }
            deflect_receipt = next(
                item
                for item in deflected["result"]["rule_receipts"]
                if item["mechanic_id"] == STEEL_DEFENDER_DEFLECT_ATTACK_MECHANIC_ID
            )
            assert deflect_receipt["event"] == "attack.before_roll.steel_defender_deflect"
            deflected_audit = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            deflecting_combatant = next(
                item
                for item in deflected_audit["state"]["combat"]["combatants"]
                if item["actor_id"] == combat_created["character"]["id"]
            )
            assert deflecting_combatant["turn_budget"]["reaction"] == 0
            removed_attack_target = await _call(
                server,
                "combat_hp_change",
                {
                    "campaign_id": campaign["id"],
                    "target_id": attack_target["id"],
                    "action": "damage",
                    "payload": {"parts": [{"amount": 100, "damage_type": "force"}]},
                    "expected_revision": deflected["campaign_revision"],
                    "idempotency_key": "remove-deflect-attack-target",
                },
            )
            default_turn = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": second_owner["id"],
                    "expected_revision": removed_attack_target["campaign_revision"],
                    "idempotency_key": "begin-default-defender-turn",
                },
            )
            default_defender = default_turn["combat"]["combatants"][1]
            assert default_defender["turn_budget"]["main_action"] == 0
            assert default_defender["turn_budget"]["reaction"] == 1
            assert default_defender["turn_flags"]["dodging"] is True
            killed_defender = await _call(
                server,
                "combat_hp_change",
                {
                    "campaign_id": campaign["id"],
                    "target_id": combat_created["character"]["id"],
                    "action": "damage",
                    "payload": {"parts": [{"amount": 100, "damage_type": "force"}]},
                    "expected_revision": default_turn["campaign_revision"],
                    "idempotency_key": "kill-bound-defender-for-revival",
                },
            )
            dead_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            dead_relation = next(
                item
                for item in dead_campaign["state"]["dependent_actor_relations"]
                if item["dependent_actor_id"] == combat_created["character"]["id"]
            )
            assert dead_relation["status"] == "dead"
            assert dead_relation["death_elapsed_ticks"] == dead_campaign["state"]["game_time"][
                "elapsed_ticks"
            ]
            assert dead_relation["revival_started_elapsed_ticks"] is None
            assert dead_relation["revival_completes_elapsed_ticks"] is None
            owner_revival_turn = await _call(
                server,
                "combat_end_turn",
                {
                    "campaign_id": campaign["id"],
                    "actor_id": combat_created["character"]["id"],
                    "expected_revision": killed_defender["campaign_revision"],
                    "idempotency_key": "begin-owner-revival-turn",
                },
            )
            revival_arguments = {
                "campaign_id": campaign["id"],
                "actor_id": second_owner["id"],
                "action": "revive_steel_defender",
                "target_id": combat_created["character"]["id"],
                "payload": {
                    "slot_level": 1,
                    "spatial_facts": {
                        "distance_ft": 5,
                        "default_resolver": "agent",
                        "ruling_kind": "agent_dm_adjudication",
                        "reason": (
                            "The owner works beside the destroyed defender with Smith's Tools."
                        ),
                    },
                },
                "expected_revision": owner_revival_turn["campaign_revision"],
                "idempotency_key": "revive-bound-defender",
            }
            revival_started = await _call(server, "combat_common_action", revival_arguments)
            assert (
                await _call(server, "combat_common_action", revival_arguments)
                == revival_started
            )
            assert revival_started["status"] == "committed"
            assert revival_started["condition_resolution"]["action_paid"] is True
            assert revival_started["condition_resolution"]["kind"] == (
                "steel_defender_revival_started"
            )
            second_owner_after_start = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": second_owner["id"]}},
            )
            assert (
                second_owner_after_start["sheet"]["spellcasting"]["spell_slots"]["1"][
                    "value"
                ]
                == 0
            )
            pending_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            pending_relation = next(
                item
                for item in pending_campaign["state"]["dependent_actor_relations"]
                if item["dependent_actor_id"] == combat_created["character"]["id"]
            )
            assert pending_relation["status"] == "dead"
            assert pending_relation["revival_completes_elapsed_ticks"] == (
                pending_relation["revival_started_elapsed_ticks"] + 10
            )
            round_result = revival_started
            for round_offset in range(10):
                round_result = await _call(
                    server,
                    "combat_end_turn",
                    {
                        "campaign_id": campaign["id"],
                        "actor_id": second_owner["id"],
                        "expected_revision": round_result["campaign_revision"],
                        "idempotency_key": f"wait-for-defender-revival-{round_offset}",
                    },
                )
            revived_defender = await _call(
                server,
                "character_query",
                {
                    "view": "get",
                    "payload": {"character_id": combat_created["character"]["id"]},
                },
            )
            assert revived_defender["sheet"]["combat"]["hp"]["value"] == (
                revived_defender["sheet"]["combat"]["hp"]["max"]
            )
            assert "dead" not in revived_defender["sheet"]["conditions"]
            committed = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            revived_relation = next(
                item
                for item in committed["state"]["dependent_actor_relations"]
                if item["dependent_actor_id"] == combat_created["character"]["id"]
            )
            assert revived_relation["status"] == "active"
            assert revived_relation["death_elapsed_ticks"] is None
            assert revived_relation["revival_started_elapsed_ticks"] is None
            assert revived_relation["revival_completes_elapsed_ticks"] is None
            await _call(
                server,
                "combat_end",
                {
                    "campaign_id": campaign["id"],
                    "outcome": {
                        "status": "interrupted",
                        "summary": "The defender returned after the one-minute combat delay.",
                    },
                    "expected_revision": round_result["campaign_revision"],
                    "idempotency_key": "end-bound-defender-revival-combat",
                },
            )
            owner_after_revival = await _call(
                server,
                "character_query",
                {"view": "get", "payload": {"character_id": second_owner["id"]}},
            )
            owner_death = await _call(
                server,
                "character_state_change",
                {
                    "character_id": second_owner["id"],
                    "action": "damage",
                    "payload": {"parts": [{"amount": 100, "damage_type": "force"}]},
                    "expected_revision": owner_after_revival["revision"],
                    "idempotency_key": "kill-bound-defender-owner",
                },
            )
            assert "dead" in owner_death["character"]["sheet"]["conditions"]
            defender_after_owner_death = await _call(
                server,
                "character_query",
                {
                    "view": "get",
                    "payload": {"character_id": combat_created["character"]["id"]},
                },
            )
            assert defender_after_owner_death["sheet"]["combat"]["hp"]["value"] == 0
            assert "dead" in defender_after_owner_death["sheet"]["conditions"]
            committed = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            owner_death_relation = next(
                item
                for item in committed["state"]["dependent_actor_relations"]
                if item["dependent_actor_id"] == combat_created["character"]["id"]
            )
            assert owner_death_relation["status"] == "dead"
            assert owner_death_relation["death_elapsed_ticks"] == committed["state"][
                "game_time"
            ]["elapsed_ticks"]
            snapshot = await _call(
                server,
                "snapshot_create",
                {
                    "campaign_id": campaign["id"],
                    "label": "Idempotency branch boundary",
                    "expected_revision": committed["revision"],
                    "expected_head_snapshot_id": "",
                    "idempotency_key": "idempotency-branch-snapshot",
                },
            )
            branches = await _call(
                server,
                "branch_query",
                {"campaign_id": campaign["id"], "view": "list", "payload": {}},
            )
            main_branch = next(item for item in branches if item["is_current"])
            branch_campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            feature_branch = await _call(
                server,
                "branch_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "create",
                    "payload": {
                        "name": "idempotency-branch",
                        "from_snapshot_id": snapshot["id"],
                        "checkout": True,
                    },
                    "expected_revision": branch_campaign["revision"],
                    "expected_branch_id": main_branch["id"],
                    "idempotency_key": "idempotency-branch-create",
                },
            )
            with pytest.raises(Exception, match="idempotency"):
                await _call(server, "addon_actor_instantiate", arguments)
            checked_out = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            await _call(
                server,
                "snapshot_create",
                {
                    "campaign_id": campaign["id"],
                    "label": "Idempotency branch clean head",
                    "expected_revision": checked_out["revision"],
                    "expected_head_snapshot_id": snapshot["id"],
                    "idempotency_key": "idempotency-branch-snapshot-clean",
                },
            )
            checked_out = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign["id"]}},
            )
            await _call(
                server,
                "branch_change",
                {
                    "campaign_id": campaign["id"],
                    "action": "checkout",
                    "payload": {"branch_id": main_branch["id"]},
                    "expected_revision": checked_out["revision"],
                    "expected_branch_id": feature_branch["id"],
                    "idempotency_key": "idempotency-branch-checkout-main",
                },
            )
            return campaign["id"], owner["id"], created["character"]["id"], arguments
        finally:
            close_server(server)

    async def verify_restart(
        campaign_id: str, owner_id: str, defender_id: str, arguments: dict
    ) -> None:
        server = create_server(config)
        try:
            replay = await _call(server, "addon_actor_instantiate", arguments)
            assert replay["character"]["id"] == defender_id
            campaign = await _call(
                server,
                "campaign_query",
                {"view": "get", "payload": {"campaign_id": campaign_id}},
            )
            assert (
                campaign["state"]["dependent_actor_relations"][0]["owner_character_id"] == owner_id
            )
        finally:
            close_server(server)

    import asyncio

    checkpoint = asyncio.run(exercise())
    asyncio.run(verify_restart(*checkpoint))


@pytest.mark.fresh_database
def test_reviewed_addon_item_uses_bound_inventory_materializer(tmp_path: Path) -> None:
    workspace = Path(__file__).resolve().parents[3]
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=workspace / "skills",
        modulegen_skills_dir=workspace / "skills" / "dnd-module-generator",
    )

    async def exercise() -> None:
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Addon item", "idempotency_key": "addon-item-campaign"},
        )
        profile = await _call(
            server,
            "campaign_rules",
            {
                "campaign_id": campaign["id"],
                "action": "set_profile",
                "payload": {"edition": "2014"},
                "principal_id": "system:local",
                "expected_revision": campaign["revision"],
                "idempotency_key": "addon-item-profile",
            },
        )
        artifact = {
            "id": "dnd5e.addon.reviewed-item.item.moon-blade",
            "kind": "item",
            "application_state": "selection_ready",
            "mechanical_scope": "descriptive",
            "execution_state": "descriptive_ready",
            "semantic_resolution": {
                "status": "resolved",
                "mode": "descriptive",
                "first_use_compilation_required": False,
            },
            "card": {
                "name": "Moon Blade",
                "inventory_template": {
                    "name": "Moon Blade",
                    "kind": "weapon",
                    "quantity": 1,
                    "description": "A reviewed addon weapon.",
                    "mechanics": {
                        "damage_formula": "1d8",
                        "damage_type": "slashing",
                        "attack_ability": "strength",
                    },
                },
            },
            "rule_refs": ["book:addon:p1"],
        }
        artifact["selection_contract"] = build_selection_contract(
            artifact,
            status="ready",
            references=["book:addon:p1"],
        )
        await import_and_activate_addon_fixture(
            _call,
            server,
            campaign["id"],
            config.home,
            manifest={
                "id": "dnd5e.addon.reviewed-item",
                "version": "1.0.0",
                "title": "Reviewed item",
                "namespace": "dnd5e.addon.reviewed-item",
                "system_id": "dnd5e",
                "editions": ["2014"],
                "capabilities": [],
            },
            artifacts=[artifact],
            mechanics=[],
            expected_revision=profile["campaign_revision"],
            request_key="addon-item",
        )
        character = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Item Tester",
                    "sheet": default_character_sheet(),
                },
                "principal_id": "system:local",
                "idempotency_key": "addon-item-character",
            },
        )

        with pytest.raises(Exception, match="item content selection does not accept input fields"):
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": character["id"],
                    "artifact_id": artifact["id"],
                    "selection": {"raw_payload": {"mechanics": {"damage_formula": "99d99"}}},
                    "expected_revision": character["revision"],
                    "idempotency_key": "addon-item-rejected",
                },
            )

        applied = await _call(
            server,
            "character_content_apply",
            {
                "character_id": character["id"],
                "artifact_id": artifact["id"],
                "expected_revision": character["revision"],
                "idempotency_key": "addon-item-applied",
            },
        )
        assert "sheet" in applied, str(applied)
        item = applied["sheet"]["inventory"]["items"][0]
        assert item["name"] == "Moon Blade"
        assert item["mechanics"]["damage_formula"] == "1d8"
        assert item["source_key"] == (
            "dnd5e.addon.reviewed-item@1.0.0:dnd5e.addon.reviewed-item.item.moon-blade"
        )
        assert applied["sheet"]["content"]["selections"][0]["selection"] == {
            "inventory_item_id": item["id"]
        }
        assert applied["content_context"]["artifact_id"] == artifact["id"]
        assert applied["content_context"]["card"]["inventory_template"]["name"] == ("Moon Blade")
        queried = await _call(
            server,
            "character_query",
            {
                "view": "catalog",
                "payload": {
                    "campaign_id": campaign["id"],
                    "kind": "item",
                    "query": artifact["id"],
                    "include_context": True,
                },
                    "principal_id": "system:local",
                },
            )
        assert (
            queried[0]["runtime_context"]["content_hash"]
            == (applied["content_context"]["content_hash"])
        )

    import asyncio

    asyncio.run(exercise())


@pytest.mark.fresh_database
def test_reviewed_addon_background_materializes_embedded_equipment(tmp_path: Path) -> None:
    workspace = Path(__file__).resolve().parents[3]
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=workspace / "skills",
        modulegen_skills_dir=workspace / "skills" / "dnd-module-generator",
    )

    async def exercise() -> None:
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Addon background", "idempotency_key": "background-campaign"},
        )
        profile = await _call(
            server,
            "campaign_rules",
            {
                "campaign_id": campaign["id"],
                "action": "set_profile",
                "payload": {"edition": "2014"},
                "principal_id": "system:local",
                "expected_revision": campaign["revision"],
                "idempotency_key": "background-profile",
            },
        )
        artifact = {
            "id": "dnd5e.addon.guild.background.guild-agent",
            "kind": "background",
            "application_state": "selection_ready",
            "mechanical_scope": "descriptive",
            "execution_state": "descriptive_ready",
            "semantic_resolution": {
                "status": "resolved",
                "mode": "descriptive",
                "first_use_compilation_required": False,
            },
            "card": {
                "name": "Guild Agent",
                "skill_proficiencies": ["investigation"],
                "background_grants": {
                    "skills": ["investigation"],
                    "feature": "Guild Membership",
                    "languages": [],
                    "spell_list_expansion": ["Aid"],
                    "tools": [],
                    "equipment_item_ids": [],
                    "equipment": {
                        "items": [
                            {
                                "inventory_template": {
                                    "name": "Identification Papers",
                                    "kind": "equipment",
                                    "quantity": 1,
                                    "description": "Reviewed guild identification.",
                                    "mechanics": {},
                                }
                            }
                        ],
                        "wallet": {"gp": 2},
                    },
                    "choices": {
                        "language_count": 0,
                        "skill_choice_count": 1,
                        "skill_options": ["Persuasion", "Religion"],
                        "tool_choice_count": 0,
                        "equipment_packages": {
                            "A": {
                                "items": [
                                    {
                                        "inventory_template": {
                                            "name": "Guild Signet",
                                            "kind": "equipment",
                                            "quantity": 1,
                                            "description": "A reviewed guild signet.",
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
            },
            "rule_refs": ["book:addon:p1"],
        }
        artifact["selection_contract"] = build_selection_contract(
            artifact,
            status="ready",
            references=["book:addon:p1"],
        )
        reprinted_aid = {
            "id": "dnd5e.addon.guild.spell.aid",
            "kind": "spell",
            "application_state": "selection_ready",
            "mechanical_scope": "descriptive",
            "execution_state": "descriptive_ready",
            "semantic_resolution": {
                "status": "resolved",
                "mode": "descriptive",
                "first_use_compilation_required": False,
            },
            "card": {
                "name": "Aid",
                "classes": ["Cleric", "Paladin"],
                "level": 2,
                "description": "Catalog retrieval text must not enter a character card.",
                "source_title": "Guild addon source",
                "definition": {
                    "school": "abjuration",
                    "casting_time": "1 action",
                    "range": {
                        "kind": "distance",
                        "normal_ft": 30,
                        "long_ft": 0,
                        "area": "",
                    },
                    "duration": {
                        "kind": "timed",
                        "value": 8,
                        "unit": "hour",
                        "concentration": False,
                    },
                    "components": {
                        "verbal": True,
                        "somatic": True,
                        "material": True,
                        "material_description": "a tiny strip of white cloth",
                        "material_cost_cp": 0,
                        "consumed": False,
                    },
                    "effect": "Source-local reviewed reprint used by this addon.",
                },
            },
            "rule_refs": ["book:addon:p3"],
        }
        reprinted_aid["selection_contract"] = build_selection_contract(
            reprinted_aid,
            status="ready",
            references=["book:addon:p3"],
        )
        species_artifact = {
            "id": "dnd5e.addon.guild.species.marked-human",
            "kind": "species",
            "application_state": "selection_ready",
            "mechanical_scope": "mechanical",
            "execution_state": "engine_ready",
            "semantic_resolution": {
                "status": "resolved",
                "mode": "static_grant",
                "first_use_compilation_required": False,
                "clause_ids": ["marked-human-spell-list"],
            },
            "rule_clauses": [
                {
                    "schema_version": 1,
                    "id": "marked-human-spell-list",
                    "title": "Marked Human spell list",
                    "scope": "mechanical",
                    "source_citations": [
                        {
                            "source": "book:addon",
                            "source_ref": {"page": 3},
                            "source_excerpt": "Aid is added to the marked spell list.",
                        }
                    ],
                    "settlement": {
                        "mode": "static_grant",
                        "grant_refs": ["card.grants.spell_list_expansion"],
                    },
                }
            ],
            "card": {
                "name": "Marked Human",
                "base_species": "Human",
                "grants": {
                    "ability_score_increases": {"intelligence": 1},
                    "ability_score_decreases": {"strength": 2},
                    "size": "medium",
                    "walk_speed": 30,
                    "fly_speed": 30,
                    "natural_armor_base": 13,
                    "natural_armor_includes_dexterity": False,
                    "natural_weapons": [
                        {
                            "name": "Marked Claws",
                            "attack_ability": "strength",
                            "damage_formula": "1d4",
                            "damage_type": "slashing",
                            "reach_ft": 5,
                            "description": "A reviewed natural weapon.",
                        }
                    ],
                    "languages": ["Common"],
                    "armor_proficiencies": ["Light Armor"],
                    "immunities": ["poison"],
                    "condition_immunities": ["poisoned"],
                    "feat_choice": {"count": 1, "allowed_categories": []},
                    "spell_list_expansion": ["Aid"],
                    "spell_grants": [
                        {
                            "name": "Detect Magic",
                            "level": 1,
                            "eligible_classes": ["Wizard"],
                            "method": "limited_use",
                            "spellcasting_ability": "intelligence",
                            "free_casts": 1,
                            "recovers_on": "long_rest",
                            "resource_group": "Marked Magic",
                            "allow_slot_cast": False,
                            "minimum_level": 1,
                            "ritual_only": False,
                            "casting_overrides": {"ignore_material_components": True},
                        },
                        {
                            "name": "Nondetection",
                            "level": 3,
                            "eligible_classes": ["Wizard"],
                            "method": "at_will",
                            "spellcasting_ability": "intelligence",
                            "free_casts": 0,
                            "recovers_on": None,
                            "allow_slot_cast": False,
                            "minimum_level": 1,
                            "ritual_only": False,
                            "casting_overrides": {"ignore_material_components": True},
                        },
                        {
                            "name": "Burning Hands",
                            "level": 1,
                            "eligible_classes": ["Wizard"],
                            "method": "limited_use",
                            "spellcasting_ability": "charisma",
                            "free_casts": 1,
                            "recovers_on": "long_rest",
                            "resource_group": "Marked Magic",
                            "allow_slot_cast": False,
                            "minimum_level": 3,
                            "ritual_only": False,
                            "casting_overrides": {"fixed_cast_level": 2},
                        },
                    ],
                    "resources": {
                        "species:marked-human:insight": {
                            "label": "Marked Insight",
                            "value": 1,
                            "max": 1,
                            "recovers_on": "short_rest",
                            "source_key": "Marked Human",
                        }
                    },
                    "features": [
                        {
                            "name": "Marked Awareness",
                            "description": "A source-reviewed level-one species feature.",
                            "minimum_level": 1,
                        },
                        {
                            "name": "Greater Mark",
                            "description": "A source-reviewed higher-level species feature.",
                            "minimum_level": 4,
                        },
                    ],
                    "unresolved": [],
                },
            },
            "rule_refs": ["book:addon:p3"],
        }
        species_artifact["selection_contract"] = build_selection_contract(
            species_artifact,
            status="ready",
            references=["book:addon:p3"],
        )
        species_feat_artifact = {
            "id": "dnd5e.addon.guild.feat.marked-training",
            "kind": "feat",
            "application_state": "selection_ready",
            "mechanical_scope": "mechanical",
            "execution_state": "engine_ready",
            "semantic_resolution": {
                "status": "resolved",
                "mode": "static_grant",
                "first_use_compilation_required": False,
                "clause_ids": ["marked-training-language"],
            },
            "rule_clauses": [
                {
                    "schema_version": 1,
                    "id": "marked-training-language",
                    "title": "Marked Training language",
                    "scope": "mechanical",
                    "source_citations": [
                        {
                            "source": "book:addon",
                            "source_ref": {"page": 3},
                            "source_excerpt": "Marked Training grants Dwarvish.",
                        }
                    ],
                    "settlement": {
                        "mode": "static_grant",
                        "grant_refs": ["card.mechanical_grants.languages"],
                    },
                }
            ],
            "card": {
                "name": "Marked Training",
                "prerequisites": [],
                "repeatable": False,
                "selection_requirements": None,
                "mechanical_grants": {
                    "ability_score_increases": {},
                    "maximum_ability_score": 20,
                    "languages": ["Dwarvish"],
                    "tool_proficiencies": [],
                    "weapon_proficiencies": [],
                    "spell_grants": [],
                },
            },
            "rule_refs": ["book:addon:p3"],
        }
        species_feat_artifact["selection_contract"] = build_selection_contract(
            species_feat_artifact,
            status="ready",
            references=["book:addon:p3"],
        )
        subclass_artifact = {
            "id": "dnd5e.addon.guild.subclass.circle-of-spores",
            "kind": "subclass",
            "application_state": "selection_ready",
            "mechanical_scope": "mechanical",
            "execution_state": "engine_ready",
            "semantic_resolution": {
                "status": "resolved",
                "mode": "static_grant",
                "first_use_compilation_required": False,
                "clause_ids": ["circle-of-spores-spell-grants"],
            },
            "rule_clauses": [
                {
                    "schema_version": 1,
                    "id": "circle-of-spores-spell-grants",
                    "title": "Circle of Spores spell grants",
                    "scope": "mechanical",
                    "source_citations": [
                        {
                            "source": "book:addon",
                            "source_ref": {"page": 2},
                            "source_excerpt": (
                                "You learn the chill touch cantrip and gain circle spells."
                            ),
                        }
                    ],
                    "settlement": {
                        "mode": "static_grant",
                        "grant_refs": [
                            "card.spell_grants",
                            "card.spell_list_expansion",
                        ],
                    },
                }
            ],
            "card": {
                "name": "Circle of Spores",
                "class_name": "Druid",
                "minimum_level": 2,
                "spell_grants": [
                    {
                        "name": "Blindness/Deafness",
                        "minimum_level": 3,
                        "method": "always_prepared",
                    },
                    {"name": "Chill Touch", "minimum_level": 2, "method": "known"},
                ],
                "spell_list_expansion": ["Aid"],
            },
            "rule_refs": ["book:addon:p2"],
        }
        subclass_artifact["selection_contract"] = build_selection_contract(
            subclass_artifact,
            status="ready",
            references=["book:addon:p2"],
        )
        await import_and_activate_addon_fixture(
            _call,
            server,
            campaign["id"],
            config.home,
            manifest={
                "id": "dnd5e.addon.guild",
                "version": "1.0.0",
                "title": "Guild addon",
                "namespace": "dnd5e.addon.guild",
                "system_id": "dnd5e",
                "editions": ["2014"],
                "capabilities": [],
            },
            artifacts=[
                artifact,
                reprinted_aid,
                species_artifact,
                species_feat_artifact,
                subclass_artifact,
            ],
            mechanics=[],
            expected_revision=profile["campaign_revision"],
            request_key="background",
        )
        catalog = await _call(
            server,
            "character_query",
            {
                "view": "catalog",
                "payload": {
                    "campaign_id": campaign["id"],
                    "kind": "background",
                    "query": "Guild Agent",
                },
                "principal_id": "system:local",
            },
        )
        background_entry = next(item for item in catalog if item["id"] == artifact["id"])
        assert background_entry["selection_requirements"]["fields"] == [
            "skills",
            "equipment_package",
        ]
        assert background_entry["selection_requirements"]["skill_choice_count"] == 1
        assert background_entry["selection_requirements"]["skill_options"] == [
            "Persuasion",
            "Religion",
        ]
        species_catalog = await _call(
            server,
            "character_query",
            {
                "view": "catalog",
                "payload": {
                    "campaign_id": campaign["id"],
                    "kind": "species",
                    "query": "Marked Human",
                },
                "principal_id": "system:local",
            },
        )
        marked_entry = next(
            item for item in species_catalog if item["id"] == species_artifact["id"]
        )
        assert "feat_selection" in marked_entry["selection_requirements"]["fields"]
        assert marked_entry["selection_requirements"]["feat_choice"] == {
            "count": 1,
            "allowed_categories": [],
        }
        character_sheet = default_character_sheet()
        character_sheet["progression"]["level"] = 3
        character_sheet["progression"]["classes"] = [
            {"name": "Wizard", "level": 3, "subclass": "", "hit_die": 6}
        ]
        character_sheet["spellcasting"].update(
            {
                "ability": "intelligence",
                "class_lists": ["wizard"],
                "spell_slots": {
                    "1": {
                        "label": "1st-level slots",
                        "value": 4,
                        "max": 4,
                        "recovers_on": "long_rest",
                        "source_key": "wizard",
                    },
                    "2": {
                        "label": "2nd-level slots",
                        "value": 2,
                        "max": 2,
                        "recovers_on": "long_rest",
                        "source_key": "wizard",
                    },
                },
                "preparation": {
                    "mode": "spellbook",
                    "max_prepared": 6,
                    "changes_on": "long_rest",
                    "selected_spell_ids": [],
                },
                "spellbook": {"enabled": True, "spell_ids": []},
            }
        )
        character = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Guild Initiate",
                    "sheet": character_sheet,
                },
                "principal_id": "system:local",
                "idempotency_key": "background-character",
            },
        )
        applied = await _call(
            server,
            "character_content_apply",
            {
                "character_id": character["id"],
                "artifact_id": artifact["id"],
                "selection": {
                    "skills": ["persuasion"],
                    "equipment_package": "A",
                },
                "expected_revision": character["revision"],
                "idempotency_key": "background-apply",
            },
        )
        items = applied["sheet"]["inventory"]["items"]
        assert [item["name"] for item in items] == [
            "Identification Papers",
            "Guild Signet",
        ]
        assert applied["sheet"]["inventory"]["wallet"]["gp"] == 12
        assert applied["sheet"]["skills"]["investigation"]["proficiency"] == "proficient"
        assert applied["sheet"]["skills"]["persuasion"]["proficiency"] == "proficient"
        assert applied["sheet"]["progression"]["background_grants"]["equipment_item_ids"] == [
            item["id"] for item in items
        ]
        assert applied["sheet"]["progression"]["background_grants"]["spell_list_expansion"] == [
            {
                "artifact_id": "dnd5e.addon.guild.spell.aid",
                "name": "Aid",
                "pack_id": "dnd5e.addon.guild",
                "pack_version": "1.0.0",
            }
        ]
        assert applied["sheet"]["progression"]["background_grants"]["choices"][
            "selected_skill_choices"
        ] == ["persuasion"]
        spell = await _call(
            server,
            "character_content_apply",
            {
                "character_id": character["id"],
                "artifact_id": "dnd5e.addon.guild.spell.aid",
                "selection": {"source_class": "Wizard", "method": "spellbook"},
                "expected_revision": applied["revision"],
                "idempotency_key": "background-expanded-spell",
            },
        )
        aid = next(
            item
            for item in spell["sheet"]["content"]["spells"]
            if item["id"] == "dnd5e.addon.guild.spell.aid"
        )
        assert aid["grant"] == {
            "source_type": "class",
            "source_key": "wizard",
            "method": "spellbook",
        }
        assert "description" not in aid
        assert "source_title" not in aid

        marked_character = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Marked Wizard",
                    "sheet": character_sheet,
                },
                "principal_id": "system:local",
                "idempotency_key": "species-character",
            },
        )
        marked = await _call(
            server,
            "character_content_apply",
            {
                "character_id": marked_character["id"],
                "artifact_id": species_artifact["id"],
                "selection": {
                    "feat_selection": {
                        "artifact_id": species_feat_artifact["id"],
                        "selection": {},
                    }
                },
                "expected_revision": marked_character["revision"],
                "idempotency_key": "species-apply",
            },
        )
        assert marked["sheet"]["progression"]["species_grants"]["spell_list_expansion"] == [
            {
                "artifact_id": "dnd5e.addon.guild.spell.aid",
                "name": "Aid",
                "pack_id": "dnd5e.addon.guild",
                "pack_version": "1.0.0",
            }
        ]
        assert marked["sheet"]["traits"]["proficiencies"]["armor"] == ["Light Armor"]
        assert marked["sheet"]["combat"]["speed"]["fly"] == 30
        assert marked["sheet"]["abilities"]["strength"]["score"] == 8
        assert marked["sheet"]["abilities"]["intelligence"]["score"] == 11
        assert marked["sheet"]["traits"]["immunities"] == ["poison"]
        assert marked["sheet"]["traits"]["condition_immunities"] == ["poisoned"]
        assert "Dwarvish" in marked["sheet"]["traits"]["languages"]
        assert any(
            feat["id"] == species_feat_artifact["id"]
            and feat["choices"]["grant_source"] == "Marked Human species"
            for feat in marked["sheet"]["content"]["feats"]
        )
        assert derive_character_sheet(marked["sheet"])["armor_class"] == 13
        natural_armor = next(
            effect
            for effect in marked["sheet"]["effects"]
            if effect["name"].endswith("Natural Armor")
        )
        assert natural_armor["changes"] == [
            {
                "path": "combat.ac.unarmored_formula",
                "mode": "override",
                "value": {
                    "base": 13,
                    "ability": None,
                    "allows_shield": True,
                    "includes_dexterity": False,
                },
            }
        ]
        natural_weapon = next(
            item
            for item in marked["sheet"]["traits"]["intrinsic_attacks"]
            if item["name"] == "Marked Claws"
        )
        assert natural_weapon["damage_formula"] == "1d4"
        assert natural_weapon["source"] == {
            "artifact_id": species_artifact["id"],
            "pack_id": "dnd5e.addon.guild",
            "pack_version": "1.0.0",
            "rule_refs": ["book:addon:p3"],
        }
        derived_natural_weapon = next(
            item
            for item in derive_character_sheet(marked["sheet"])["inventory"]["weapon_attacks"]
            if item["item_id"] == natural_weapon["id"]
        )
        assert derived_natural_weapon["intrinsic"] is True
        assert derived_natural_weapon["natural_weapon"] is True
        assert derived_natural_weapon["unarmed_strike"] is True
        detect_magic = next(
            item for item in marked["sheet"]["content"]["spells"] if item["name"] == "Detect Magic"
        )
        assert detect_magic["access"]["feature_casting_sources"][0]["casting_overrides"] == {
            "ignore_material_components": True
        }
        nondetection = next(
            item for item in marked["sheet"]["content"]["spells"] if item["name"] == "Nondetection"
        )
        assert nondetection["access"]["at_will"] is True
        assert nondetection["access"]["feature_casting_sources"][0]["method"] == ("at_will")
        burning_hands = next(
            item for item in marked["sheet"]["content"]["spells"] if item["name"] == "Burning Hands"
        )
        assert burning_hands["access"]["feature_casting_sources"][0]["casting_overrides"] == {
            "fixed_cast_level": 2
        }
        assert (
            detect_magic["access"]["feature_casting_sources"][0]["resource_key"]
            == burning_hands["access"]["feature_casting_sources"][0]["resource_key"]
        )
        assert marked["sheet"]["resources"]["species:marked-human:insight"]["value"] == 1
        assert [item["name"] for item in marked["sheet"]["content"]["features"]] == [
            "Marked Awareness"
        ]
        marked_awareness = marked["sheet"]["content"]["features"][0]
        assert marked_awareness["id"].endswith(".feature.marked-awareness")
        assert marked_awareness["source_key"] == "Marked Human"
        assert "minimum_level" not in marked_awareness
        marked_spell = await _call(
            server,
            "character_content_apply",
            {
                "character_id": marked_character["id"],
                "artifact_id": "dnd5e.addon.guild.spell.aid",
                "selection": {"source_class": "Wizard", "method": "spellbook"},
                "expected_revision": marked["revision"],
                "idempotency_key": "species-expanded-spell",
            },
        )
        assert any(
            item["id"] == "dnd5e.addon.guild.spell.aid"
            for item in marked_spell["sheet"]["content"]["spells"]
        )
        marked_level_four = await _call(
            server,
            "character_state_change",
            {
                "character_id": marked_character["id"],
                "action": "level_advance",
                "payload": {
                    "class_name": "Wizard",
                    "hp_method": "fixed",
                    "reason": "unlock the reviewed level-four species feature",
                    "source_ref": "book:addon:p3",
                },
                "expected_revision": marked_spell["revision"],
                "idempotency_key": "species-level-four",
            },
        )
        advanced_marked = marked_level_four["character"]
        assert [item["name"] for item in advanced_marked["sheet"]["content"]["features"]] == [
            "Marked Awareness",
            "Greater Mark",
        ]
        assert marked_level_four["advancement"]["species_feature_grants"] == [
            {
                "artifact_id": ("dnd5e.addon.guild.species.marked-human.feature.greater-mark"),
                "name": "Greater Mark",
                "minimum_level": 4,
                "source_species": "Marked Human",
            }
        ]

        dragonborn_catalog = await _call(
            server,
            "character_query",
            {
                "view": "catalog",
                "payload": {
                    "campaign_id": campaign["id"],
                    "kind": "species",
                    "query": "Dragonborn",
                },
                "principal_id": "system:local",
            },
        )
        dragonborn_id = "dnd5e.content.standard2014.species.dragonborn"
        dragonborn_entry = next(item for item in dragonborn_catalog if item["id"] == dragonborn_id)
        assert dragonborn_entry["selection_requirements"]["fields"] == ["damage_affinity"]
        dragonborn_character = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Red Dragonborn",
                    "sheet": character_sheet,
                },
                "principal_id": "system:local",
                "idempotency_key": "dragonborn-character",
            },
        )
        dragonborn_applied = await _call(
            server,
            "character_content_apply",
            {
                "character_id": dragonborn_character["id"],
                "artifact_id": dragonborn_id,
                "selection": {"damage_affinity": "red"},
                "expected_revision": dragonborn_character["revision"],
                "idempotency_key": "dragonborn-species-apply",
            },
        )
        assert "fire" in dragonborn_applied["sheet"]["traits"]["resistances"]
        breath = next(
            item
            for item in dragonborn_applied["sheet"]["content"]["activities"]
            if item["name"] == "Breath Weapon"
        )
        breath_spec = breath["choices"]["standard_resolution"]
        assert breath_spec["kind"] == "area_save_damage"
        assert breath["mechanic_refs"] == ["dnd5e.core.activity.dragonborn_breath_weapon"]
        assert breath_spec["save_dc_formula"] == {
            "base": 8,
            "ability": "constitution",
            "include_proficiency": True,
        }
        assert breath_spec["damage_formula_by_level"]["1"] == "2d6"

        druid_sheet = default_character_sheet()
        druid_sheet["progression"]["level"] = 3
        druid_sheet["progression"]["classes"] = [
            {"name": "Druid", "level": 3, "subclass": "", "hit_die": 8}
        ]
        druid_sheet["spellcasting"].update(
            {
                "ability": "wisdom",
                "class_lists": ["druid"],
                "spell_slots": {
                    "1": {
                        "label": "1st-level slots",
                        "value": 4,
                        "max": 4,
                        "recovers_on": "long_rest",
                        "source_key": "druid",
                    },
                    "2": {
                        "label": "2nd-level slots",
                        "value": 2,
                        "max": 2,
                        "recovers_on": "long_rest",
                        "source_key": "druid",
                    },
                },
                "preparation": {
                    "mode": "prepared",
                    "max_prepared": 6,
                    "changes_on": "long_rest",
                    "selected_spell_ids": [],
                },
            }
        )
        druid = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Spore Druid",
                    "sheet": druid_sheet,
                },
                "principal_id": "system:local",
                "idempotency_key": "subclass-character",
            },
        )
        subclass_applied = await _call(
            server,
            "character_content_apply",
            {
                "character_id": druid["id"],
                "artifact_id": subclass_artifact["id"],
                "selection": {"target_class_name": "Druid"},
                "expected_revision": druid["revision"],
                "idempotency_key": "subclass-apply",
            },
        )
        subclass_spells = {
            item["name"]: item for item in subclass_applied["sheet"]["content"]["spells"]
        }
        assert subclass_spells["Chill Touch"]["grant"]["method"] == "known"
        assert subclass_spells["Chill Touch"]["access"]["known"] is True
        assert subclass_spells["Chill Touch"]["access"]["always_prepared"] is False
        assert subclass_spells["Blindness/Deafness"]["grant"]["method"] == ("class_prepared")
        assert subclass_spells["Blindness/Deafness"]["access"]["always_prepared"] is True
        assert "Aid" not in subclass_spells
        assert subclass_applied["sheet"]["progression"]["subclass_grants"][
            "spell_list_expansion"
        ] == [
            {
                "artifact_id": "dnd5e.addon.guild.spell.aid",
                "name": "Aid",
                "pack_id": "dnd5e.addon.guild",
                "pack_version": "1.0.0",
                "source_class": "Druid",
            }
        ]
        expanded_spell = await _call(
            server,
            "character_content_apply",
            {
                "character_id": druid["id"],
                "artifact_id": "dnd5e.addon.guild.spell.aid",
                "selection": {
                    "source_class": "Druid",
                    "method": "class_prepared",
                },
                "expected_revision": subclass_applied["revision"],
                "idempotency_key": "subclass-expanded-spell",
            },
        )
        expanded_aid = next(
            item
            for item in expanded_spell["sheet"]["content"]["spells"]
            if item["id"] == "dnd5e.addon.guild.spell.aid"
        )
        assert expanded_aid["grant"] == {
            "source_type": "class",
            "source_key": "druid",
            "method": "class_prepared",
        }

    import asyncio

    asyncio.run(exercise())


def _forged_tortle_natural_armor_sheet(*, standard: bool) -> dict[str, object]:
    forged = default_character_sheet()
    forged["progression"]["species"] = "Tortle"
    if standard:
        forged["content"]["features"].append(
            {
                "id": f"{TORTLE_NATURAL_ARMOR_ARTIFACT_ID}.feature.natural-armor",
                "name": "Natural Armor",
                "source_key": "Tortle",
                "description": "Caller-forged feature.",
                "activation": {"type": "passive"},
                "choices": {
                    "source_trait": {
                        "kind": "tortle_natural_armor",
                        "effect_source": TORTLE_NATURAL_ARMOR_ARTIFACT_ID,
                        "base_ac": 17,
                        "includes_dexterity": False,
                        "armor_benefit": "none",
                        "allows_shield": True,
                        "source_excerpt": "Caller-forged excerpt.",
                    }
                },
                "advancement_grants": [],
                "pack_id": TORTLE_NATURAL_ARMOR_LEGACY_PACK_ID,
                "pack_version": "1.0.0",
                "rule_refs": [f"rule-source:{TORTLE_NATURAL_ARMOR_SOURCE_KEY}#chunk:forged"],
                "mechanic_refs": [CORE_TORTLE_NATURAL_ARMOR_MECHANIC_ID],
                "ruling_requirements": [],
            }
        )
    else:
        forged["content"]["selections"].append(
            {
                "artifact_id": TORTLE_NATURAL_ARMOR_ARTIFACT_ID,
                "kind": "species",
                "name": "Tortle",
                "pack_id": TORTLE_NATURAL_ARMOR_LEGACY_PACK_ID,
                "pack_version": "1.0.0",
                "rule_refs": [f"rule-source:{TORTLE_NATURAL_ARMOR_SOURCE_KEY}#chunk:forged"],
                "mechanic_refs": [],
                "selection": {},
            }
        )
    forged, _ = add_effect(
        forged,
        {
            "name": "Tortle Natural Armor",
            "kind": "feature",
            "source": TORTLE_NATURAL_ARMOR_ARTIFACT_ID,
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
    return forged


def test_tortle_natural_armor_authority_requires_locked_outer_archive() -> None:
    provenance = {
        "content_definition": {
            "package_id": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
            "package_version": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
            "package_checksum": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_CHECKSUM,
        }
    }
    with pytest.raises(RulesetUnavailableError, match="immutable official expansion archive"):
        server_module._verified_tortle_natural_armor_authority(
            pack_id=TORTLE_NATURAL_ARMOR_LEGACY_PACK_ID,
            pack_version="1.0.0",
            artifact_id=TORTLE_NATURAL_ARMOR_ARTIFACT_ID,
            provenance=provenance,
        )
    assert server_module._verified_tortle_natural_armor_authority(
        pack_id=TORTLE_NATURAL_ARMOR_LEGACY_PACK_ID,
        pack_version="1.0.0",
        artifact_id=TORTLE_NATURAL_ARMOR_ARTIFACT_ID,
        provenance=provenance,
        archive_definition_verified=True,
    ) == {
        "package_id": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
        "package_version": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
        "package_checksum": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_CHECKSUM,
    }

    forged = deepcopy(provenance)
    forged["content_definition"]["package_checksum"] = "0" * 64
    with pytest.raises(RulesetUnavailableError, match="immutable official expansion archive"):
        server_module._verified_tortle_natural_armor_authority(
            pack_id=TORTLE_NATURAL_ARMOR_LEGACY_PACK_ID,
            pack_version="1.0.0",
            artifact_id=TORTLE_NATURAL_ARMOR_ARTIFACT_ID,
            provenance=forged,
            archive_definition_verified=True,
        )


@pytest.mark.fresh_database
def test_tortle_natural_armor_provenance_rejects_whole_sheet_forgery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = Path(__file__).resolve().parents[3]
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=workspace / "skills",
        modulegen_skills_dir=workspace / "skills" / "dnd-module-generator",
    )

    async def exercise() -> None:
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Tortle forge guard", "idempotency_key": "tortle-forge-campaign"},
        )
        clean_character = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Clean Character",
                    "sheet": default_character_sheet(),
                },
                "principal_id": "system:local",
                "idempotency_key": "tortle-forge-clean-character",
            },
        )
        with pytest.raises(Exception, match="global library actor"):
            await _call(
                server,
                "character_create_from",
                {
                    "mode": "template",
                    "payload": {
                        "template_id": clean_character["id"],
                        "campaign_id": campaign["id"],
                        "name": "Forbidden campaign clone",
                    },
                    "principal_id": "system:local",
                    "idempotency_key": "tortle-forge-campaign-template",
                },
            )
        for standard in (False, True):
            forged = _forged_tortle_natural_armor_sheet(standard=standard)
            with pytest.raises(Exception, match="only by character_content_apply"):
                await _call(
                    server,
                    "character_create_from",
                    {
                        "mode": "direct",
                        "payload": {
                            "campaign_id": campaign["id"],
                            "name": f"Forged Tortle {standard}",
                            "sheet": forged,
                        },
                        "principal_id": "system:local",
                        "idempotency_key": f"tortle-forge-create-{standard}",
                    },
                )
            with pytest.raises(Exception, match="cannot add, remove, or alter"):
                await _call(
                    server,
                    "character_sheet_replace",
                    {
                        "character_id": clean_character["id"],
                        "sheet": forged,
                        "expected_revision": clean_character["revision"],
                        "idempotency_key": f"tortle-forge-replace-{standard}",
                    },
                )

        original_guard = server_module._reject_new_tortle_natural_armor_provenance
        monkeypatch.setattr(
            server_module,
            "_reject_new_tortle_natural_armor_provenance",
            lambda _sheet: None,
        )
        poisoned_template = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "name": "Pre-upgrade forged Tortle template",
                    "sheet": _forged_tortle_natural_armor_sheet(standard=False),
                },
                "principal_id": "system:local",
                "idempotency_key": "tortle-forge-poisoned-template",
            },
        )
        monkeypatch.setattr(
            server_module,
            "_reject_new_tortle_natural_armor_provenance",
            original_guard,
        )
        with pytest.raises(Exception, match="only by character_content_apply"):
            await _call(
                server,
                "character_create_from",
                {
                    "mode": "template",
                    "payload": {
                        "template_id": poisoned_template["id"],
                        "campaign_id": campaign["id"],
                    },
                    "principal_id": "system:local",
                    "idempotency_key": "tortle-forge-poisoned-template-instantiate",
                },
            )

    import asyncio

    asyncio.run(exercise())


@pytest.mark.fresh_database
def test_preupgrade_forged_tortle_addon_cannot_replay_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = Path(__file__).resolve().parents[3]
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=workspace / "skills",
        modulegen_skills_dir=workspace / "skills" / "dnd-module-generator",
    )

    async def exercise() -> None:
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Tortle AC", "idempotency_key": "tortle-ac-campaign"},
        )
        profile = await _call(
            server,
            "campaign_rules",
            {
                "campaign_id": campaign["id"],
                "action": "set_profile",
                "payload": {"edition": "2014"},
                "principal_id": "system:local",
                "expected_revision": campaign["revision"],
                "idempotency_key": "tortle-ac-profile",
            },
        )

        source_chunks = [
            "# Reviewed fixture\n\n## Tortle\n\nThe Tortle species was reviewed.",
            (
                "## Natural Armor\n\nThe shell gives base AC 17, Dexterity does not "
                "affect it, armor gives no benefit, and a shield applies normally."
            ),
        ]
        source_rule_refs = [
            f"rule-source:{TORTLE_NATURAL_ARMOR_SOURCE_KEY}#chunk:"
            f"{rule_chunk_key(TORTLE_NATURAL_ARMOR_SOURCE_KEY, 0, index, chunk)}"
            for index, chunk in enumerate(source_chunks)
        ]
        artifact = {
            "id": TORTLE_NATURAL_ARMOR_ARTIFACT_ID,
            "kind": "species",
            "application_state": "selection_ready",
            "mechanical_scope": "mechanical",
            "execution_state": "ruling_ready",
            "semantic_resolution": {
                "status": "resolved",
                "mode": "agent_ruling",
                "first_use_compilation_required": False,
                "clause_ids": ["tortle-natural-armor"],
            },
            "ruling_requirements": [
                {
                    "kind": "source_bound_import_resolution",
                    "policy_ref": "rule_clause.v1",
                    "reason": "Apply only the exact source-bound Tortle trait.",
                    "default_resolver": "agent",
                    "ruling_kind": "agent_dm_adjudication",
                    "source_excerpt": "Armor gives no benefit; a shield applies normally.",
                    "requires_external_input_only_for": [],
                }
            ],
            "rule_clauses": [
                {
                    "schema_version": 1,
                    "id": "tortle-natural-armor",
                    "title": "Tortle Natural Armor",
                    "scope": "mechanical",
                    "source_citations": [
                        {
                            "source": f"rule-source:{TORTLE_NATURAL_ARMOR_SOURCE_KEY}",
                            "source_ref": {"page": 4},
                            "source_excerpt": (
                                "The shell gives base AC 17, armor gives no benefit, "
                                "and a shield applies normally."
                            ),
                        }
                    ],
                    "settlement": {
                        "mode": "agent_ruling",
                        "default_resolver": "agent",
                        "ruling_kind": "agent_dm_adjudication",
                        "reason": "Apply only the exact source-bound Tortle trait.",
                    },
                }
            ],
            "card": {
                "name": "Tortle",
                "base_species": "Tortle",
                "grants": {
                    "natural_armor_base": 17,
                    "natural_armor_includes_dexterity": False,
                    "features": [
                        {
                            "name": "Natural Armor",
                            "description": "Fixed AC 17; a shield applies normally.",
                        }
                    ],
                    "unresolved": [],
                },
            },
            "rule_refs": source_rule_refs,
        }
        artifact["selection_contract"] = build_selection_contract(
            artifact,
            status="ready",
            references=source_rule_refs,
        )
        with pytest.raises(Exception, match="reserved for official package"):
            await import_and_activate_addon_fixture(
                _call,
                server,
                campaign["id"],
                config.home,
                manifest={
                    "id": "dnd5e.addon.third-party-tortle-shadow",
                    "version": "1.0.0",
                    "title": "Third-party Tortle shadow",
                    "namespace": "dnd5e.addon.third-party-tortle-shadow",
                    "system_id": "dnd5e",
                    "editions": ["2014"],
                    "capabilities": [],
                },
                artifacts=[artifact],
                mechanics=[],
                expected_revision=profile["campaign_revision"],
                request_key="tortle-artifact-shadow",
            )
        campaign_after_shadow = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        assert campaign_after_shadow["revision"] == profile["campaign_revision"]
        assert (
            await _call(
                server,
                "content_pack",
                {
                    "action": "list",
                    "payload": {"campaign_id": campaign["id"], "kind": "addon"},
                },
            )
            == []
        )
        tortle_rebind = next(
            item
            for item in official_expansion_dependency_rebinds()
            if item["package_id"] == TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID
            and item["definition_id"] == TORTLE_NATURAL_ARMOR_LEGACY_PACK_ID
        )
        await _call(
            server,
            "content_pack",
            {
                "action": "activate",
                "payload": {
                    "campaign_id": campaign["id"],
                    "kind": "core_rules",
                    "pack_id": tortle_rebind["dependency_id"],
                    "version": tortle_rebind["runtime_version"],
                },
                "expected_revision": profile["campaign_revision"],
                "idempotency_key": "tortle-ac-support-activate",
            },
        )
        campaign_after_support = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        original_official_validator = server_module._validate_reserved_official_package_identity

        def trust_synthetic_fixture(package: dict[str, object]) -> None:
            with pytest.raises(ValueError, match="reserved official identity"):
                server_module._validate_reserved_official_package_identity(package)
            monkeypatch.setattr(
                server_module,
                "_validate_reserved_official_package_identity",
                lambda _package: None,
            )

        fixture = await import_and_activate_addon_fixture(
            _call,
            server,
            campaign["id"],
            config.home,
            manifest={
                "id": TORTLE_NATURAL_ARMOR_LEGACY_PACK_ID,
                "version": "1.0.0",
                "title": "Reviewed Tortle Package",
                "namespace": TORTLE_NATURAL_ARMOR_LEGACY_PACK_ID,
                "system_id": "dnd5e",
                "editions": ["2014"],
                "capabilities": [],
                "dependencies": [
                    {
                        "id": tortle_rebind["dependency_id"],
                        "version": tortle_rebind["dependency_version"],
                        "checksum": tortle_rebind["source_checksum"],
                        "rule_checksum": tortle_rebind["source_checksum"],
                    }
                ],
            },
            artifacts=[artifact],
            mechanics=[],
            expected_revision=campaign_after_support["revision"],
            request_key="tortle-ac",
            content_dependencies_override=[],
            content_package_id_override=TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
            content_package_version_override=TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
            source_key_override=TORTLE_NATURAL_ARMOR_SOURCE_KEY,
            source_chunks_override=source_chunks,
            before_import=trust_synthetic_fixture,
        )
        assert fixture["package"]["id"] == TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID
        monkeypatch.setattr(
            server_module,
            "_validate_reserved_official_package_identity",
            original_official_validator,
        )
        with pytest.raises(Exception, match="reserved official identity"):
            await _call(
                server,
                "content_pack",
                {
                    "action": "activate",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "kind": "addon",
                        "addon_id": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
                        "version": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
                    },
                    "expected_revision": campaign_after_support["revision"],
                    "idempotency_key": "tortle-ac:activate",
                },
            )
        unchanged = await _call(
            server,
            "campaign_query",
            {"view": "get", "payload": {"campaign_id": campaign["id"]}},
        )
        assert unchanged["revision"] == fixture["campaign_revision"]
        forged_inner_arguments = {
            "action": "activate",
            "payload": {
                "campaign_id": campaign["id"],
                "kind": "core_rules",
                "pack_id": TORTLE_NATURAL_ARMOR_LEGACY_PACK_ID,
                "version": "1.0.0",
            },
            "expected_revision": fixture["campaign_revision"],
            "idempotency_key": "tortle-forged-inner-activate",
        }
        with pytest.raises(Exception, match="immutable official content archive"):
            await _call(server, "content_pack", forged_inner_arguments)
        restarted = create_server(config)
        with pytest.raises(Exception, match="reserved official identity"):
            await _call(
                restarted,
                "content_pack",
                {
                    "action": "activate",
                    "payload": {
                        "campaign_id": campaign["id"],
                        "kind": "addon",
                        "addon_id": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_ID,
                        "version": TORTLE_NATURAL_ARMOR_CONTENT_PACKAGE_VERSION,
                    },
                    "expected_revision": campaign_after_support["revision"],
                    "idempotency_key": "tortle-ac:activate",
                },
            )
        with pytest.raises(Exception, match="immutable official content archive"):
            await _call(restarted, "content_pack", forged_inner_arguments)

    import asyncio

    asyncio.run(exercise())


@pytest.mark.fresh_database
def test_subclass_spell_prefers_exact_reviewed_dependency_over_bundled_duplicate(
    tmp_path: Path,
) -> None:
    workspace = Path(__file__).resolve().parents[3]
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=workspace / "skills",
        modulegen_skills_dir=workspace / "skills" / "dnd-module-generator",
    )

    async def exercise() -> None:
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {
                "name": "Pinned dependency spell",
                "idempotency_key": "dependency-spell-campaign",
            },
        )
        profile = await _call(
            server,
            "campaign_rules",
            {
                "campaign_id": campaign["id"],
                "action": "set_profile",
                "payload": {"edition": "2014"},
                "principal_id": "system:local",
                "expected_revision": campaign["revision"],
                "idempotency_key": "dependency-spell-profile",
            },
        )
        dependency_spell = {
            "id": "dnd5e.addon.reviewed-dependency.spell.identify",
            "kind": "spell",
            "application_state": "selection_ready",
            "mechanical_scope": "descriptive",
            "execution_state": "descriptive_ready",
            "semantic_resolution": {
                "status": "resolved",
                "mode": "descriptive",
                "first_use_compilation_required": False,
            },
            "card": {
                "name": "Identify",
                "classes": ["Bard", "Wizard"],
                "level": 1,
                "definition": {
                    "school": "divination",
                    "casting_time": "1 minute",
                    "range": {
                        "kind": "touch",
                        "normal_ft": 0,
                        "long_ft": 0,
                        "area": "",
                    },
                    "duration": {
                        "kind": "instantaneous",
                        "value": 0,
                        "unit": "special",
                        "concentration": False,
                    },
                    "components": {
                        "verbal": True,
                        "somatic": True,
                        "material": True,
                        "material_description": "a reviewed dependency component",
                        "material_cost_cp": 10000,
                        "consumed": False,
                    },
                    "effect": "The dependency's reviewed Identify printing.",
                },
            },
            "rule_refs": ["book:dependency:p1"],
        }
        dependency_spell["selection_contract"] = build_selection_contract(
            dependency_spell,
            status="ready",
            references=["book:dependency:p1"],
        )
        dependency_pack = await import_and_activate_addon_fixture(
            _call,
            server,
            campaign["id"],
            config.home,
            manifest={
                "id": "dnd5e.addon.reviewed-dependency",
                "version": "1.0.0",
                "title": "Reviewed dependency",
                "namespace": "dnd5e.addon.reviewed-dependency",
                "system_id": "dnd5e",
                "editions": ["2014"],
                "capabilities": [],
            },
            artifacts=[dependency_spell],
            mechanics=[],
            expected_revision=profile["campaign_revision"],
            request_key="dependency-spell",
        )

        subclass = {
            "id": "dnd5e.addon.dependent-domain.subclass.dependency-domain",
            "kind": "subclass",
            "application_state": "selection_ready",
            "mechanical_scope": "mechanical",
            "execution_state": "engine_ready",
            "semantic_resolution": {
                "status": "resolved",
                "mode": "static_grant",
                "first_use_compilation_required": False,
                "clause_ids": ["dependency-domain-spells"],
            },
            "rule_clauses": [
                {
                    "schema_version": 1,
                    "id": "dependency-domain-spells",
                    "title": "Dependency Domain spells",
                    "scope": "mechanical",
                    "source_citations": [
                        {
                            "source": "book:dependent-domain",
                            "source_ref": {"page": 2},
                            "source_excerpt": "Identify is always prepared.",
                        }
                    ],
                    "settlement": {
                        "mode": "static_grant",
                        "grant_refs": ["card.spell_grants"],
                    },
                }
            ],
            "card": {
                "name": "Dependency Domain",
                "class_name": "Cleric",
                "minimum_level": 1,
                "spell_grants": [
                    {"name": "Identify", "minimum_level": 1, "method": "always_prepared"}
                ],
            },
            "rule_refs": ["book:dependent-domain:p2"],
        }
        subclass["selection_contract"] = build_selection_contract(
            subclass,
            status="ready",
            references=["book:dependent-domain:p2"],
        )
        await import_and_activate_addon_fixture(
            _call,
            server,
            campaign["id"],
            config.home,
            manifest={
                "id": "dnd5e.addon.dependent-domain",
                "version": "1.0.0",
                "title": "Dependent domain",
                "namespace": "dnd5e.addon.dependent-domain",
                "system_id": "dnd5e",
                "editions": ["2014"],
                "capabilities": [],
                "dependencies": [
                    {
                        "id": "dnd5e.addon.reviewed-dependency",
                        "version": "1.0.0",
                        "checksum": dependency_pack["package"]["checksum"],
                        "rule_checksum": dependency_pack["imported"]["components"][0]["checksum"],
                    }
                ],
            },
            artifacts=[subclass],
            mechanics=[],
            expected_revision=dependency_pack["campaign_revision"],
            request_key="dependent-domain",
        )
        identify_catalog = await _call(
            server,
            "character_query",
            {
                "view": "catalog",
                "payload": {
                    "campaign_id": campaign["id"],
                    "kind": "spell",
                    "query": "Identify",
                },
            },
        )
        assert identify_catalog, "the active dependency must expose Identify"

        sheet = default_character_sheet()
        sheet["progression"]["level"] = 1
        sheet["progression"]["classes"] = [
            {"name": "Cleric", "level": 1, "subclass": "", "hit_die": 8}
        ]
        sheet["spellcasting"].update(
            {
                "ability": "wisdom",
                "class_lists": ["cleric"],
                "preparation": {
                    "mode": "prepared",
                    "max_prepared": 4,
                    "changes_on": "long_rest",
                    "selected_spell_ids": [],
                },
            }
        )
        character = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {
                    "campaign_id": campaign["id"],
                    "name": "Dependency Cleric",
                    "sheet": sheet,
                },
                "principal_id": "system:local",
                "idempotency_key": "dependency-cleric-create",
            },
        )
        applied = await _call(
            server,
            "character_content_apply",
            {
                "character_id": character["id"],
                "artifact_id": subclass["id"],
                "selection": {"target_class_name": "Cleric"},
                "expected_revision": character["revision"],
                "idempotency_key": "dependency-domain-apply",
            },
        )
        assert "sheet" in applied, str(applied)

        identify = next(
            item for item in applied["sheet"]["content"]["spells"] if item["name"] == "Identify"
        )
        assert identify["pack_id"] == "dnd5e.addon.reviewed-dependency"
        assert identify["pack_version"] == "1.0.0"
        assert identify["definition"]["effect"] == ("The dependency's reviewed Identify printing.")

    import asyncio

    asyncio.run(exercise())


@pytest.mark.fresh_database
@pytest.mark.parametrize("repeatable_levels", [[], [6]])
def test_reviewed_addon_base_class_uses_bound_level_one_materializer(
    tmp_path: Path, repeatable_levels: list[int]
) -> None:
    workspace = Path(__file__).resolve().parents[3]
    config = McpConfig(
        home=tmp_path / "home",
        database_url=None,
        chroma_url=None,
        chroma_path_override=None,
        dnd_skills_dir=workspace / "skills",
        modulegen_skills_dir=workspace / "skills" / "dnd-module-generator",
    )

    async def exercise() -> None:
        server = create_server(config)
        campaign = await _call(
            server,
            "campaign_create",
            {"name": "Addon class", "idempotency_key": "addon-class-campaign"},
        )
        profile = await _call(
            server,
            "campaign_rules",
            {
                "campaign_id": campaign["id"],
                "action": "set_profile",
                "payload": {"edition": "2014"},
                "principal_id": "system:local",
                "expected_revision": campaign["revision"],
                "idempotency_key": "addon-class-profile",
            },
        )
        artifact = {
            "id": "dnd5e.addon.artificer.class.artificer",
            "kind": "class",
            "application_state": "selection_ready",
            "mechanical_scope": "descriptive",
            "execution_state": "descriptive_ready",
            "semantic_resolution": {
                "status": "resolved",
                "mode": "descriptive",
                "first_use_compilation_required": False,
            },
            "card": {
                "name": "Artificer",
                "class_definition": {
                    "hit_die": 8,
                    "saving_throw_proficiencies": ["constitution", "intelligence"],
                    "armor_proficiencies": ["light armor", "medium armor", "shields"],
                    "weapon_proficiencies": ["simple weapons"],
                    "tool_proficiencies": [
                        "thieves' tools",
                        "tinker's tools",
                        "Alchemist's Supplies",
                    ],
                    "tool_choice_count": 1,
                    "tool_options": ["smith's tools", "weaver's tools"],
                    "skill_choice_count": 2,
                    "skill_options": ["arcana", "history", "investigation", "medicine"],
                    "spellcasting": {
                        "ability": "intelligence",
                        "class_list": "artificer",
                        "preparation_mode": "prepared",
                        "slot_progression": "half_round_up",
                        "ritual_casting": True,
                        "spellbook": False,
                        "cantrips_known_by_level": [
                            2,
                            2,
                            2,
                            2,
                            2,
                            2,
                            2,
                            2,
                            2,
                            3,
                            3,
                            3,
                            3,
                            4,
                            4,
                            4,
                            4,
                            4,
                            4,
                            4,
                        ],
                        "leveled_spells_known_by_level": [],
                        "prepared_limit": {
                            "ability": "intelligence",
                            "class_level_divisor": 2,
                            "rounding": "down",
                            "minimum": 1,
                        },
                        "spell_list_expansion": ["Cure Wounds", "Magic Weapon"],
                    },
                },
            },
            "rule_refs": ["book:addon:artificer:p2"],
        }
        artifact["selection_contract"] = build_selection_contract(
            artifact,
            status="ready",
            references=["book:addon:artificer:p2"],
        )
        tool_feature = {
            "id": "dnd5e.addon.artificer.feature.tool-expertise",
            "kind": "feature",
            "application_state": "selection_ready",
            "mechanical_scope": "mechanical",
            "execution_state": "engine_ready",
            "semantic_resolution": {
                "status": "resolved",
                "mode": "static_grant",
                "first_use_compilation_required": False,
                "clause_ids": ["artificer-tool-proficiencies"],
            },
            "rule_clauses": [
                {
                    "schema_version": 1,
                    "id": "artificer-tool-proficiencies",
                    "title": "Artificer tool proficiencies",
                    "scope": "mechanical",
                    "source_citations": [
                        {
                            "source": "book:addon:artificer",
                            "source_ref": {"page": 3},
                            "source_excerpt": "Tool Expertise doubles tool proficiency.",
                        }
                    ],
                    "settlement": {
                        "mode": "static_grant",
                        "grant_refs": ["card.mechanical_grants"],
                    },
                }
            ],
            "card": {
                "name": "Tool Expertise",
                "class_name": "Artificer",
                "subclass_name": "",
                "feature_subtype": "",
                "minimum_level": 1,
                "repeatable_selection_levels": [],
                "selection_requirements": {},
                "selection_requirements_by_level": {},
                "mechanical_grants": {
                    "tool_expertise_all": True,
                    "tool_proficiencies": ["Alchemist's Supplies"],
                    "tool_proficiency_replacement_options": {
                        "Alchemist's Supplies": [
                            "Smith's Tools",
                            "Weaver's Tools",
                        ]
                    },
                    "weapon_proficiencies": ["Martial Weapons"],
                    "skill_proficiencies": ["History"],
                },
            },
            "rule_refs": ["book:addon:artificer:p3"],
        }
        tool_feature["selection_contract"] = build_selection_contract(
            tool_feature,
            status="ready",
            references=["book:addon:artificer:p3"],
        )
        infusion_option = {
            "id": "dnd5e.addon.artificer.feature.enhanced-defense",
            "kind": "feature",
            "application_state": "selection_ready",
            "mechanical_scope": "descriptive",
            "execution_state": "descriptive_ready",
            "semantic_resolution": {
                "status": "resolved",
                "mode": "descriptive",
                "first_use_compilation_required": False,
            },
            "card": {
                "name": "Enhanced Defense",
                "class_name": "Artificer",
                "subclass_name": "",
                "feature_subtype": "selectable_option",
                "minimum_level": 1,
                "description": "A learned artificer infusion.",
                "repeatable_selection_levels": [],
                "selection_requirements": {},
                "selection_requirements_by_level": {},
                "mechanical_grants": {},
            },
            "rule_refs": ["book:addon:artificer:p4"],
        }
        infusion_option["selection_contract"] = build_selection_contract(
            infusion_option,
            status="ready",
            references=["book:addon:artificer:p4"],
        )
        infusion_feature = {
            "id": "dnd5e.addon.artificer.feature.infuse-item",
            "kind": "feature",
            "application_state": "selection_ready",
            "mechanical_scope": "descriptive",
            "execution_state": "descriptive_ready",
            "semantic_resolution": {
                "status": "resolved",
                "mode": "descriptive",
                "first_use_compilation_required": False,
            },
            "card": {
                "name": "Infuse Item",
                "class_name": "Artificer",
                "subclass_name": "",
                "minimum_level": 1,
                # Synthetic level-one fixture: later unlocks need not repeat
                # the initial minimum_level in their list (as in real Infuse Item).
                "repeatable_selection_levels": repeatable_levels,
                "selection_requirements": {
                    "field": "infusions",
                    "kind": "feature_grants",
                    "count": 1,
                    "options": ["Enhanced Defense"],
                    "option_artifact_ids": {
                        "Enhanced Defense": infusion_option["id"],
                    },
                    "option_prerequisites": {
                        "Enhanced Defense": {"minimum_level": 1},
                    },
                    "option_subtype": "selectable_option",
                },
                "selection_requirements_by_level": {},
                "mechanical_grants": {},
            },
            "rule_refs": ["book:addon:artificer:p4"],
        }
        infusion_feature["selection_contract"] = build_selection_contract(
            infusion_feature,
            status="ready",
            references=["book:addon:artificer:p4"],
        )
        unowned_feature = deepcopy(infusion_feature)
        unowned_feature["id"] = "dnd5e.addon.artificer.feature.unbound-advancement"
        unowned_feature["card"].update(
            name="Unbound Advancement",
            class_name="",
            minimum_level=2,
            repeatable_selection_levels=[6],
            selection_requirements={},
        )
        unowned_feature["selection_contract"] = build_selection_contract(
            unowned_feature,
            status="ready",
            references=["book:addon:artificer:p4"],
        )
        await import_and_activate_addon_fixture(
            _call,
            server,
            campaign["id"],
            config.home,
            manifest={
                "id": "dnd5e.addon.artificer",
                "version": "1.0.0",
                "title": "Reviewed Artificer",
                "namespace": "dnd5e.addon.artificer",
                "system_id": "dnd5e",
                "editions": ["2014"],
                "capabilities": [],
            },
            artifacts=[artifact, tool_feature, infusion_feature, infusion_option, unowned_feature],
            mechanics=[],
            expected_revision=profile["campaign_revision"],
            request_key="addon-class",
        )
        sheet = default_character_sheet()
        sheet["abilities"]["constitution"]["score"] = 14
        character = await _call(
            server,
            "character_create_from",
            {
                "mode": "direct",
                "payload": {"campaign_id": campaign["id"], "name": "Class Tester", "sheet": sheet},
                "principal_id": "system:local",
                "idempotency_key": "addon-class-character",
            },
        )

        applied = await _call(
            server,
            "character_content_apply",
            {
                "character_id": character["id"],
                "artifact_id": artifact["id"],
                "selection": {
                    "skills": ["arcana", "investigation"],
                    "tools": ["weaver's tools"],
                },
                "expected_revision": character["revision"],
                "idempotency_key": "addon-class-apply",
            },
        )
        assert applied["sheet"]["progression"]["classes"][0]["name"] == "Artificer"
        assert (
            applied["sheet"]["progression"]["classes"][0]["spellcasting"]
            == (artifact["card"]["class_definition"]["spellcasting"])
        )
        assert applied["sheet"]["combat"]["hp"]["max"] == 10
        assert applied["sheet"]["spellcasting"]["ability"] == "intelligence"
        assert applied["sheet"]["spellcasting"]["class_lists"] == ["artificer"]
        assert applied["sheet"]["spellcasting"]["spell_slots"]["1"]["max"] == 2
        assert applied["sheet"]["spellcasting"]["preparation"]["max_prepared"] == 1
        assert applied["sheet"]["skills"]["arcana"]["proficiency"] == "proficient"
        assert applied["class_materialization"]["saving_throw_proficiencies"] == [
            "constitution",
            "intelligence",
        ]
        assert applied["class_materialization"]["tool_proficiency_choices"] == ["weaver's tools"]
        assert applied["sheet"]["content"]["selections"][0]["selection"] == {
            "skills": ["arcana", "investigation"],
            "tools": ["weaver's tools"],
        }
        feature_applied = await _call(
            server,
            "character_content_apply",
            {
                "character_id": character["id"],
                "artifact_id": tool_feature["id"],
                "selection": {
                    "tool_replacements": {
                        "Alchemist's Supplies": "Smith's Tools",
                    }
                },
                "expected_revision": applied["revision"],
                "idempotency_key": "addon-tool-expertise-apply",
            },
        )
        proficiencies = feature_applied["sheet"]["traits"]["proficiencies"]
        assert proficiencies["weapons"] == ["simple weapons", "Martial Weapons"]
        assert proficiencies["tools"] == [
            "thieves' tools",
            "tinker's tools",
            "Alchemist's Supplies",
            "weaver's tools",
            "Smith's Tools",
        ]
        assert proficiencies["tool_expertise_all"] is True
        assert proficiencies["tool_expertise"] == proficiencies["tools"]
        assert feature_applied["sheet"]["skills"]["history"]["proficiency"] == ("proficient")
        infused = await _call(
            server,
            "character_content_apply",
            {
                "character_id": character["id"],
                "artifact_id": infusion_feature["id"],
                "selection": {"infusions": ["Enhanced Defense"]},
                "expected_revision": feature_applied["revision"],
                "idempotency_key": "addon-infusion-apply",
            },
        )
        enhanced_defense = next(
            item
            for item in infused["sheet"]["content"]["features"]
            if item["id"] == infusion_option["id"]
        )
        assert enhanced_defense["source_key"] == "Infuse Item"
        assert "repeatable_selection_levels" not in enhanced_defense
        with pytest.raises(Exception, match="not a repeatable selection level"):
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": character["id"],
                    "artifact_id": unowned_feature["id"],
                    "expected_revision": infused["revision"],
                    "idempotency_key": "unowned-initial",
                },
            )
        parent_feature = next(
            item
            for item in infused["sheet"]["content"]["features"]
            if item["id"] == infusion_feature["id"]
        )
        assert [item["level"] for item in parent_feature.get("advancement_grants", [])] == (
            [1] if repeatable_levels else []
        )
        for label, grant_level, message in (
            ("duplicate", None, "already present"),
            ("invalid-level", 2, "not a repeatable selection level"),
            ("future-level", 6, "exceeds the actor's class level"),
        ):
            if not repeatable_levels and label == "future-level":
                continue
            choices = {"infusions": ["Enhanced Defense"]}
            if grant_level is not None:
                choices["grant_level"] = grant_level
            # Use a fresh actor for invalid/future first grants so the duplicate
            # guard cannot mask whether the initial-grant exception is bounded.
            target = character
            if label != "duplicate":
                target = await _call(
                    server,
                    "character_create_from",
                    {
                        "mode": "direct",
                        "payload": {
                            "campaign_id": campaign["id"],
                            "name": label,
                            "sheet": feature_applied["sheet"],
                        },
                        "idempotency_key": f"infusion-{label}-actor",
                    },
                )
            else:
                target = {"id": character["id"], "revision": infused["revision"]}
            with pytest.raises(Exception, match=message):
                await _call(
                    server,
                    "character_content_apply",
                    {
                        "character_id": target["id"],
                        "artifact_id": infusion_feature["id"],
                        "selection": choices,
                        "expected_revision": target["revision"],
                        "idempotency_key": f"infusion-{label}",
                    },
                )
            unchanged = await _call(
                server,
                "character_query",
                {
                    "view": "get",
                    "payload": {"character_id": target["id"]},
                },
            )
            assert unchanged["revision"] == target["revision"]
        with pytest.raises(Exception, match="must be granted by their parent"):
            await _call(
                server,
                "character_content_apply",
                {
                    "character_id": character["id"],
                    "artifact_id": infusion_option["id"],
                    "selection": {},
                    "expected_revision": infused["revision"],
                    "idempotency_key": "addon-infusion-direct-rejected",
                },
            )

    import asyncio

    asyncio.run(exercise())
