from collections import Counter
from pathlib import Path

from sagasmith_dnd.content_import import audit_release_semantic_validation
from sagasmith_dnd.core_content import (
    PACK_VERSION,
    _feat_prerequisites,
    _known_feature_structure,
    build_srd2014_content,
)
from sagasmith_dnd.core_rule_pack import get_core_rule_pack
from sagasmith_dnd.standard_feature_ids import (
    CORE_RELENTLESS_ENDURANCE_MECHANIC_ID,
)


def test_non_numeric_feat_prerequisite_defaults_to_agent_review() -> None:
    assert _feat_prerequisites("*Prerequisite: Spellcasting or Pact Magic feature*") == [
        {
            "kind": "dm_review",
            "text": "Spellcasting or Pact Magic feature",
            "default_resolver": "agent",
            "ruling_kind": "source_or_scene_fact",
        }
    ]


def test_arcane_recovery_reset_is_derived_from_the_source_edition_text() -> None:
    daily = _known_feature_structure(
        "Wizard",
        "Arcane Recovery",
        "Once per day when you finish a short rest, recover spell slots.",
    )
    long_rest = _known_feature_structure(
        "Wizard",
        "Arcane Recovery",
        "Once you use this feature, you can't do so again until you finish a Long Rest.",
    )

    assert daily["uses"]["recovers_on"] == "manual"
    assert long_rest["uses"]["recovers_on"] == "long_rest"


def test_srd2014_content_uses_leaf_records_and_structured_eligibility() -> None:
    workspace = Path(__file__).resolve().parents[3]
    manifest, artifacts = build_srd2014_content(workspace / "skills")
    counts = Counter(item["kind"] for item in artifacts)

    assert manifest["version"] == PACK_VERSION == "1.24.0"
    assert "dnd5e.core.spell.structured_resolution" in manifest["native_mechanic_refs"]
    registered = {boundary.id for boundary in get_core_rule_pack("2014").boundaries}
    assert set(manifest["native_mechanic_refs"]) <= registered
    assert len(artifacts) == 1012
    assert counts == {
        "background": 1,
        "class": 12,
        "feat": 1,
        "feature": 182,
        "item": 472,
        "species": 13,
        "spell": 319,
        "subclass": 12,
    }
    assert len({item["id"] for item in artifacts}) == len(artifacts)
    assert all(item["source_citations"] for item in artifacts)
    validation = audit_release_semantic_validation(artifacts)
    assert manifest["resolution_policy"] == "build_time_complete"
    assert manifest["semantic_validation"] == validation
    assert validation["complete"] is True
    assert validation["first_use_compilation_required"] is False
    assert validation["resolved_count"] == len(artifacts)
    assert all(
        artifact["semantic_resolution"]["first_use_compilation_required"] is False
        for artifact in artifacts
    )

    names = {(item["kind"], item["card"]["name"]) for item in artifacts}
    assert ("spell", "Spell Lists") not in names
    assert ("species", "Racial Traits") not in names
    assert ("background", "Acolyte") in names
    assert ("item", "Longsword") in names

    fighter = next(
        item for item in artifacts if item["kind"] == "class" and item["card"]["name"] == "Fighter"
    )
    assert fighter.get("application_state", "selection_ready") == "selection_ready"
    assert fighter["card"]["class_definition"] == {
        "hit_die": 10,
        "saving_throw_proficiencies": ["strength", "constitution"],
        "armor_proficiencies": ["All armor", "shields"],
        "weapon_proficiencies": ["Simple weapons", "martial weapons"],
        "tool_proficiencies": [],
        "skill_choice_count": 2,
        "skill_options": [
            "acrobatics",
            "athletics",
            "history",
            "insight",
            "intimidation",
            "perception",
        ],
    }
    assert all(
        item.get("application_state", "selection_ready") == "selection_ready"
        and isinstance(item["card"].get("class_definition"), dict)
        for item in artifacts
        if item["kind"] == "class"
    )

    ordinary_items = [
        item
        for item in artifacts
        if item["kind"] == "item"
        and item.get("application_state", "selection_ready") == "selection_ready"
    ]
    assert ordinary_items
    assert all(isinstance(item["card"].get("inventory_template"), dict) for item in ordinary_items)
    assert all(
        item.get("application_state") == "catalog_only"
        for item in artifacts
        if item["kind"] == "item" and "09_Magic_Items" in item["rule_refs"][0]
    )
    chain_mail = next(
        item for item in artifacts if item["id"] == "dnd5e.content.srd2014.item.chain-mail"
    )
    assert chain_mail["card"]["inventory_template"] == {
        "name": "Chain mail",
        "kind": "armor",
        "quantity": 1,
        "weight_oz": 880,
        "price_cp": 7500,
        "description": "",
        "source_key": "dnd5e.content.srd2014.item.chain-mail",
        "equipped": False,
        "identified": True,
        "attunement": "none",
        "condition": "normal",
        "uses": {},
        "charges": {},
        "mechanics": {
            "base_ac": 16,
            "category": "heavy",
            "dexterity_mode": "none",
            "stealth_disadvantage": True,
            "strength_requirement": 13,
        },
    }
    dagger = next(
        item for item in artifacts if item["id"] == "dnd5e.content.srd2014.item.dagger"
    )
    assert dagger["card"]["inventory_template"]["mechanics"]["proficient"] is False
    assert "Finesse" in dagger["card"]["inventory_template"]["mechanics"]["properties"]
    shields = [
        item
        for item in artifacts
        if item["kind"] == "item" and item["card"].get("name", "").casefold() == "shield"
    ]
    assert len(shields) == 1
    assert shields[0]["card"]["inventory_template"]["mechanics"] == {"ac_bonus": 2}
    arrows = next(item for item in artifacts if item["id"] == "dnd5e.content.srd2014.item.arrows")
    assert arrows["card"]["inventory_template"]["kind"] == "ammunition"
    assert arrows["card"]["inventory_template"]["quantity"] == 20
    assert any(item["card"].get("name") == "Dungeoneer's Pack" for item in ordinary_items)

    fireball = next(
        item for item in artifacts if item["kind"] == "spell" and item["card"]["name"] == "Fireball"
    )
    assert fireball["card"]["classes"] == ["sorcerer", "wizard"]
    assert fireball["card"]["access"]["known"] is False
    assert fireball["card"]["definition"]["components"]["material"] is True
    assert fireball["card"]["resolution"]["kind"] == "saving_throw"
    assert fireball["card"]["resolution"]["targeting"]["area"] == {
        "shape": "sphere",
        "radius_ft": 20,
    }
    lightning_bolt = next(
        item for item in artifacts if item["id"] == "dnd5e.content.srd2014.spell.lightning-bolt"
    )
    assert lightning_bolt["card"]["resolution"]["targeting"]["area"] == {
        "shape": "line",
        "length_ft": 100,
        "width_ft": 5,
    }
    scorching_ray = next(
        item for item in artifacts if item["id"] == "dnd5e.content.srd2014.spell.scorching-ray"
    )
    assert scorching_ray["card"]["resolution"]["attack"]["count"] == {
        "base": 3,
        "per_slot_above": 1,
        "slot_base_level": 2,
    }
    healing_word = next(
        item for item in artifacts if item["id"] == "dnd5e.content.srd2014.spell.healing-word"
    )
    assert healing_word["card"]["resolution"]["healing"]["add_spellcasting_modifier"] is True

    shield = next(item for item in artifacts if item["id"] == "dnd5e.content.srd2014.spell.shield")
    assert shield["mechanic_refs"] == ["dnd5e.core.spell.shield"]
    assert shield["card"]["mechanic_refs"] == ["dnd5e.core.spell.shield"]
    magic_missile = next(
        item for item in artifacts if item["id"] == "dnd5e.content.srd2014.spell.magic-missile"
    )
    assert magic_missile["mechanic_refs"] == ["dnd5e.core.spell.magic_missile"]
    assert magic_missile["card"]["mechanic_refs"] == ["dnd5e.core.spell.magic_missile"]
    hypnotic_pattern = next(
        item for item in artifacts if item["id"] == "dnd5e.content.srd2014.spell.hypnotic-pattern"
    )
    assert hypnotic_pattern["mechanic_refs"] == ["dnd5e.core.spell.hypnotic_pattern"]
    assert hypnotic_pattern["card"]["mechanic_refs"] == ["dnd5e.core.spell.hypnotic_pattern"]
    assert "ruling_requirements" not in hypnotic_pattern["card"]
    fly = next(item for item in artifacts if item["id"] == "dnd5e.content.srd2014.spell.fly")
    assert fly["mechanic_refs"] == ["dnd5e.core.spell.fly"]
    assert fly["card"]["mechanic_refs"] == ["dnd5e.core.spell.fly"]
    assert "ruling_requirements" not in fly["card"]
    invisibility = next(
        item for item in artifacts if item["id"] == "dnd5e.content.srd2014.spell.invisibility"
    )
    assert invisibility["mechanic_refs"] == ["dnd5e.core.spell.invisibility"]
    assert invisibility["card"]["mechanic_refs"] == ["dnd5e.core.spell.invisibility"]
    assert "ruling_requirements" not in invisibility["card"]
    light = next(item for item in artifacts if item["id"] == "dnd5e.content.srd2014.spell.light")
    assert light["card"]["ruling_requirements"][0]["default_resolver"] == "agent"
    assert light["card"]["ruling_requirements"][0]["source_excerpt"]
    assert light["card"]["ruling_requirements"][0]["policy_ref"] == "rule_clause.v1"
    assert all(
        (
            item["card"].get("resolution")
            or item["card"].get("mechanic_refs")
            or item["card"].get("ruling_requirements")
        )
        for item in artifacts
        if item["kind"] == "spell"
    )

    berserker = next(
        item
        for item in artifacts
        if item["kind"] == "subclass" and item["card"]["name"] == "Path of the Berserker"
    )
    assert berserker["card"]["class_name"] == "Barbarian"
    assert berserker["card"]["minimum_level"] == 3

    life_domain = next(
        item
        for item in artifacts
        if item["kind"] == "subclass" and item["card"]["name"] == "Life Domain"
    )
    assert life_domain["card"]["spell_grants"][:2] == [
        {"name": "bless", "minimum_level": 1, "method": "always_prepared"},
        {"name": "cure wounds", "minimum_level": 1, "method": "always_prepared"},
    ]
    fiend = next(
        item
        for item in artifacts
        if item["kind"] == "subclass" and item["card"]["name"] == "The Fiend"
    )
    assert fiend["card"]["spell_grants"] == []
    oath_of_devotion = next(
        item
        for item in artifacts
        if item["kind"] == "subclass" and item["card"]["name"] == "Oath of Devotion"
    )
    assert oath_of_devotion["card"]["spell_grants"][:2] == [
        {
            "name": "protection from evil and good",
            "minimum_level": 3,
            "method": "always_prepared",
        },
        {"name": "sanctuary", "minimum_level": 3, "method": "always_prepared"},
    ]
    purity = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.oath-of-devotion-purity-of-spirit"
    )
    assert purity["card"]["minimum_level"] == 15
    assert purity["card"]["subclass_name"] == "Oath of Devotion"

    life_bonus_proficiency = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.life-domain-bonus-proficiency"
    )
    assert life_bonus_proficiency["card"]["mechanical_grants"] == {
        "armor_proficiencies": ["heavy armor"]
    }

    sneak_attack = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.rogue-sneak-attack"
    )
    assert sneak_attack["card"]["class_name"] == "Rogue"
    assert sneak_attack["card"]["minimum_level"] == 1

    second_wind = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.fighter-second-wind"
    )
    assert second_wind["card"]["activation"]["type"] == "bonus_action"
    assert second_wind["card"]["uses"]["recovers_on"] == "short_rest"

    action_surge = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.fighter-action-surge"
    )
    assert action_surge["card"]["activation"]["type"] == "special"
    assert action_surge["card"]["uses"]["value"] == 1
    assert action_surge["card"]["resource_scaling"]["maximum_by_level"] == {
        "2": 1,
        "17": 2,
    }

    channel_divinity = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.cleric-channel-divinity"
    )
    assert channel_divinity["card"]["resource_key"] == "channel_divinity"
    assert (
        channel_divinity["card"]["mechanical_grants"]["resources"]["channel_divinity"][
            "recovers_on"
        ]
        == "short_rest"
    )
    assert channel_divinity["card"]["resource_scaling"]["maximum_by_level"] == {
        "2": 1,
        "6": 2,
        "18": 3,
    }

    rage = next(
        item for item in artifacts if item["id"] == "dnd5e.content.srd2014.feature.barbarian-rage"
    )
    assert rage["card"]["resource_scaling"]["maximum_by_level"] == {
        "1": 2,
        "3": 3,
        "6": 4,
        "12": 5,
        "17": 6,
    }
    assert rage["card"]["resource_scaling"]["unlimited_at_level"] == 20
    assert rage["card"]["scaling"] == [
        {"level": 1, "value": 2, "description": "rage damage bonus"},
        {"level": 9, "value": 3, "description": "rage damage bonus"},
        {"level": 16, "value": 4, "description": "rage damage bonus"},
    ]

    ki = next(item for item in artifacts if item["id"] == "dnd5e.content.srd2014.feature.monk-ki")
    assert ki["card"]["resource_key"] == "ki"
    assert ki["card"]["resource_scaling"]["maximum_by_level"]["20"] == 20
    assert ki["card"]["mechanical_grants"]["resources"]["ki"]["recovery_requirements"] == {
        "activity_minutes": {"meditation": 30}
    }

    sorcery = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.sorcerer-font-of-magic"
    )
    assert sorcery["card"]["resource_key"] == "sorcery_points"
    assert sorcery["card"]["resource_scaling"]["maximum_by_level"]["20"] == 20

    bardic = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.bard-bardic-inspiration"
    )
    assert bardic["card"]["resource_scaling"]["maximum_formula"] == {
        "kind": "ability_modifier",
        "ability": "charisma",
        "minimum": 1,
        "multiplier": 1,
        "offset": 0,
    }
    assert bardic["card"]["resource_scaling"]["recovery_by_level"] == {"5": "short_rest"}

    divine_sense = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.paladin-divine-sense"
    )
    assert divine_sense["card"]["resource_scaling"]["maximum_formula"]["minimum"] == 0
    assert divine_sense["card"]["uses"]["unlimited"] is False

    favored_enemy = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.ranger-favored-enemy"
    )
    requirements = favored_enemy["card"]["selection_requirements"]
    assert requirements["language_if_spoken"] is True
    assert "requires_language" not in requirements

    draconic_resilience = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.draconic-bloodline-draconic-resilience"
    )
    assert draconic_resilience["card"]["mechanical_grants"] == {
        "hp_per_class_level": 1,
        "unarmored_base": 13,
    }
    unarmored_defenses = {
        artifact["card"]["class_name"]: artifact["card"]["mechanical_grants"]["unarmored_formula"]
        for artifact in artifacts
        if artifact["id"]
        in {
            "dnd5e.content.srd2014.feature.barbarian-unarmored-defense",
            "dnd5e.content.srd2014.feature.monk-unarmored-defense",
        }
    }
    assert unarmored_defenses == {
        "Barbarian": {
            "base": 10,
            "ability": "constitution",
            "allows_shield": True,
            "includes_dexterity": True,
        },
        "Monk": {
            "base": 10,
            "ability": "wisdom",
            "allows_shield": False,
            "includes_dexterity": True,
        },
    }
    dragon_ancestor = next(
        artifact
        for artifact in artifacts
        if artifact["id"].endswith("draconic-bloodline-dragon-ancestor")
    )
    assert dragon_ancestor["card"]["mechanical_grants"]["languages"] == ["Draconic"]

    fighting_styles = [
        artifact
        for artifact in artifacts
        if artifact["kind"] == "feature" and artifact["card"].get("name") == "Fighting Style"
    ]
    assert len(fighting_styles) == 3
    assert all(
        artifact["card"]["selection_requirements"]["requires_new_choice"] is True
        for artifact in fighting_styles
    )
    assert {
        artifact["card"]["selection_requirements"]["choice_uniqueness_scope"]
        for artifact in fighting_styles
    } == {"fighting_style"}
    extra_attacks = {
        artifact["card"]["class_name"]: artifact["card"]["attack_scaling"][
            "attacks_per_action_by_level"
        ]
        for artifact in artifacts
        if artifact["kind"] == "feature" and artifact["card"].get("name") == "Extra Attack"
    }
    assert extra_attacks == {
        "Barbarian": {"5": 2},
        "Fighter": {"5": 2, "11": 3, "20": 4},
        "Monk": {"5": 2},
        "Paladin": {"5": 2},
        "Ranger": {"5": 2},
    }

    signature = next(
        artifact for artifact in artifacts if artifact["id"].endswith("wizard-signature-spells")
    )
    assert signature["card"]["selection_requirements"] == {
        "field": "spell_artifact_ids",
        "kind": "signature_spells",
        "count": 2,
        "eligible_class": "wizard",
        "required_spell_levels": [3, 3],
        "requires_spellbook": True,
    }
    relentless_rage = next(
        artifact for artifact in artifacts if artifact["id"].endswith("barbarian-relentless-rage")
    )
    assert relentless_rage["card"]["minimum_level"] == 11

    paladin_channel = next(
        artifact
        for artifact in artifacts
        if artifact["id"].endswith("oath-of-devotion-channel-divinity")
    )
    assert paladin_channel["card"]["minimum_level"] == 3
    assert paladin_channel["card"]["resource_key"] == "channel_divinity"
    assert paladin_channel["card"]["mechanical_grants"]["resources"]["channel_divinity"] == {
        "label": "Channel Divinity",
        "value": 1,
        "max": 1,
        "unlimited": False,
        "recovers_on": "short_rest",
        "source_key": "Paladin",
    }
    assert paladin_channel["card"]["choices"]["options"] == [
        "Sacred Weapon",
        "Turn the Unholy",
    ]

    preserve_life = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.life-domain-channel-divinity-preserve-life"
    )
    assert preserve_life["card"]["resource_key"] == "channel_divinity"

    bard_expertise = next(
        item for item in artifacts if item["id"] == "dnd5e.content.srd2014.feature.bard-expertise"
    )
    assert bard_expertise["card"]["selection_requirements"] == {
        "field": "proficiencies",
        "count": 2,
        "requires_existing_proficiency": True,
        "requires_new_expertise": True,
        "skills_only": True,
    }
    lore_proficiencies = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.college-of-lore-bonus-proficiencies"
    )
    assert lore_proficiencies["card"]["selection_requirements"] == {
        "field": "skills",
        "count": 3,
        "requires_untrained_skill": True,
        "grants_skill_proficiency": True,
    }
    use_magic_device = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.thief-use-magic-device"
    )
    assert use_magic_device["card"]["minimum_level"] == 13
    subclass_minimums = {
        item["card"]["name"]: item["card"]["minimum_level"]
        for item in artifacts
        if item["kind"] == "subclass"
    }
    subclass_features = [
        item
        for item in artifacts
        if item["kind"] == "feature" and item["card"].get("subclass_name")
    ]
    assert all(
        item["card"]["minimum_level"] >= subclass_minimums[item["card"]["subclass_name"]]
        for item in subclass_features
    )
    assert (
        next(
            item
            for item in subclass_features
            if item["id"] == "dnd5e.content.srd2014.feature.circle-of-the-land-circle-spells"
        )["card"]["minimum_level"]
        == 2
    )
    assert (
        next(
            item
            for item in subclass_features
            if item["id"] == "dnd5e.content.srd2014.feature.oath-of-devotion-oath-spells"
        )["card"]["minimum_level"]
        == 3
    )
    rogue_expertise = next(
        item for item in artifacts if item["id"] == "dnd5e.content.srd2014.feature.rogue-expertise"
    )
    assert rogue_expertise["card"]["unlock_levels"] == [1, 6]
    assert rogue_expertise["card"]["repeatable_selection_levels"] == [1, 6]
    assert rogue_expertise["card"]["selection_requirements"]["requires_new_expertise"] is True
    fighter_asi = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.fighter-ability-score-improvement"
    )
    assert fighter_asi["card"]["unlock_levels"] == [4, 6, 8, 12, 14, 16, 19]
    assert fighter_asi["card"]["repeatable_selection_levels"] == [
        4,
        6,
        8,
        12,
        14,
        16,
        19,
    ]
    assert fighter_asi["card"]["selection_requirements"] == {
        "field": "ability_score_increases",
        "kind": "ability_score_increase",
        "allowed_distributions": [[2], [1, 1]],
        "maximum_score": 20,
    }
    favored_enemy = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.ranger-favored-enemy"
    )
    natural_explorer = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.ranger-natural-explorer"
    )
    assert favored_enemy["card"]["unlock_levels"] == [1, 6, 14]
    assert favored_enemy["card"]["repeatable_selection_levels"] == [1, 6, 14]
    assert natural_explorer["card"]["unlock_levels"] == [1, 6, 10]
    assert natural_explorer["card"]["repeatable_selection_levels"] == [1, 6, 10]
    metamagic = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.sorcerer-metamagic"
    )
    assert metamagic["card"]["selection_requirements_by_level"]["3"]["count"] == 2
    assert metamagic["card"]["selection_requirements_by_level"]["10"]["count"] == 1
    assert metamagic["card"]["repeatable_selection_levels"] == [3, 10, 17]
    dragon_ancestor = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.draconic-bloodline-dragon-ancestor"
    )
    assert dragon_ancestor["card"]["choice_metadata"]["damage_type_by_option"]["Gold"] == "Fire"
    circle_spells = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.circle-of-the-land-circle-spells"
    )
    assert circle_spells["card"]["selection_requirements"]["options"] == [
        "Arctic",
        "Coast",
        "Desert",
        "Forest",
        "Grassland",
        "Mountain",
        "Swamp",
    ]
    assert circle_spells["card"]["always_prepared_spell_options"]["Coast"][:2] == [
        {"name": "mirror image", "minimum_level": 3},
        {"name": "misty step", "minimum_level": 3},
    ]
    warlock_invocations = [
        item
        for item in artifacts
        if item["id"].startswith("dnd5e.content.srd2014.feature.warlock-eldritch-invocations")
    ]
    assert len(warlock_invocations) == 1
    invocations = warlock_invocations[0]["card"]
    assert invocations["repeatable_selection_levels"] == [2, 5, 7, 9, 12, 15, 18]
    assert invocations["selection_requirements_by_level"]["2"]["count"] == 2
    assert invocations["selection_requirements_by_level"]["5"]["count"] == 1
    assert invocations["selection_requirements"]["option_prerequisites"]["Ascendant Step"] == {
        "minimum_level": 9
    }
    assert (
        invocations["selection_requirements"]["at_will_spells"]["Armor of Shadows"] == "mage armor"
    )
    magical_secrets = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.bard-magical-secrets"
    )
    assert magical_secrets["card"]["selection_requirements"]["eligible_class"] == "any"
    mystic_arcanum = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.warlock-mystic-arcanum"
    )
    assert mystic_arcanum["card"]["selection_requirements_by_level"]["15"]["spell_level"] == 8

    hill_dwarf = next(
        item
        for item in artifacts
        if item["kind"] == "species" and item["card"]["name"] == "Hill Dwarf"
    )
    assert hill_dwarf.get("application_state", "selection_ready") == "selection_ready"
    assert hill_dwarf["card"]["grants"]["ability_score_increases"] == {
        "constitution": 2,
        "wisdom": 1,
    }
    assert hill_dwarf["card"]["grants"]["hp_per_level"] == 1
    assert hill_dwarf["card"]["grants"]["resistances"] == ["poison"]

    half_orc = next(
        item
        for item in artifacts
        if item["kind"] == "species" and item["card"]["name"] == "Half-Orc"
    )
    relentless = next(
        feature
        for feature in half_orc["card"]["grants"]["features"]
        if feature["name"] == "Relentless Endurance"
    )
    assert half_orc["mechanic_refs"] == [CORE_RELENTLESS_ENDURANCE_MECHANIC_ID]
    assert relentless["mechanic_refs"] == [CORE_RELENTLESS_ENDURANCE_MECHANIC_ID]
    assert relentless["uses"] == {
        "label": "Relentless Endurance",
        "value": 1,
        "max": 1,
        "recovers_on": "long_rest",
        "source_key": "Half-Orc",
        "slot_level": 0,
        "unlimited": False,
    }
    assert relentless["choices"]["source_trait"] == {
        "kind": "relentless_endurance",
        "trigger": "reduced_to_zero_not_killed_outright",
        "result_hp": 1,
        "automatic": True,
        "source_excerpt": relentless["description"],
    }
    assert all(
        CORE_RELENTLESS_ENDURANCE_MECHANIC_ID not in feature.get("mechanic_refs", [])
        for feature in half_orc["card"]["grants"]["features"]
        if feature["name"] != "Relentless Endurance"
    )

    dragonborn = next(
        item
        for item in artifacts
        if item["kind"] == "species" and item["card"]["name"] == "Dragonborn"
    )
    assert dragonborn["application_state"] == "catalog_only"

    acolyte = next(item for item in artifacts if item["kind"] == "background")
    assert acolyte["card"]["skill_proficiencies"] == ["insight", "religion"]
    assert acolyte["card"]["background_grants"]["choices"]["language_count"] == 2
