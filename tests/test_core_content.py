from collections import Counter
from pathlib import Path

from sagasmith_dnd.core_content import PACK_VERSION, build_srd2014_content


def test_srd2014_content_uses_leaf_records_and_structured_eligibility() -> None:
    workspace = Path(__file__).resolve().parents[2]
    manifest, artifacts = build_srd2014_content(workspace / "SagaSmith-dnd-skills")
    counts = Counter(item["kind"] for item in artifacts)

    assert manifest["version"] == PACK_VERSION == "1.8.2"
    assert counts["spell"] == 319
    assert counts["species"] == 13
    assert counts["class"] == 12
    assert counts["subclass"] == 12
    assert counts["feature"] >= 175
    assert counts["background"] == 1
    assert counts["feat"] == 1
    assert counts["item"] > 450

    names = {(item["kind"], item["card"]["name"]) for item in artifacts}
    assert ("spell", "Spell Lists") not in names
    assert ("species", "Racial Traits") not in names
    assert ("background", "Acolyte") in names
    assert ("item", "Longsword") in names

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
    scorching_ray = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.spell.scorching-ray"
    )
    assert scorching_ray["card"]["resolution"]["attack"]["count"] == {
        "base": 3,
        "per_slot_above": 1,
        "slot_base_level": 2,
    }
    healing_word = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.spell.healing-word"
    )
    assert healing_word["card"]["resolution"]["healing"][
        "add_spellcasting_modifier"
    ] is True

    shield = next(
        item for item in artifacts if item["id"] == "dnd5e.content.srd2014.spell.shield"
    )
    assert shield["mechanic_refs"] == ["dnd5e.core.spell.shield"]
    assert shield["card"]["mechanic_refs"] == ["dnd5e.core.spell.shield"]
    magic_missile = next(
        item for item in artifacts if item["id"] == "dnd5e.content.srd2014.spell.magic-missile"
    )
    assert magic_missile["mechanic_refs"] == ["dnd5e.core.spell.magic_missile"]
    assert magic_missile["card"]["mechanic_refs"] == ["dnd5e.core.spell.magic_missile"]

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
    assert life_domain["card"]["always_prepared_spells"][:2] == [
        {"name": "bless", "minimum_level": 1},
        {"name": "cure wounds", "minimum_level": 1},
    ]
    fiend = next(
        item
        for item in artifacts
        if item["kind"] == "subclass" and item["card"]["name"] == "The Fiend"
    )
    assert fiend["card"]["always_prepared_spells"] == []
    oath_of_devotion = next(
        item
        for item in artifacts
        if item["kind"] == "subclass" and item["card"]["name"] == "Oath of Devotion"
    )
    assert oath_of_devotion["card"]["always_prepared_spells"][:2] == [
        {"name": "protection from evil and good", "minimum_level": 3},
        {"name": "sanctuary", "minimum_level": 3},
    ]

    life_bonus_proficiency = next(
        item
        for item in artifacts
        if item["id"]
        == "dnd5e.content.srd2014.feature.life-domain-bonus-proficiency"
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

    channel_divinity = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.cleric-channel-divinity"
    )
    assert channel_divinity["card"]["resource_key"] == "channel_divinity"
    assert channel_divinity["card"]["mechanical_grants"]["resources"][
        "channel_divinity"
    ]["recovers_on"] == "short_rest"

    preserve_life = next(
        item
        for item in artifacts
        if item["id"]
        == "dnd5e.content.srd2014.feature.life-domain-channel-divinity-preserve-life"
    )
    assert preserve_life["card"]["resource_key"] == "channel_divinity"

    bard_expertise = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.bard-expertise"
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
        if item["id"]
        == "dnd5e.content.srd2014.feature.college-of-lore-bonus-proficiencies"
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
    assert next(
        item
        for item in subclass_features
        if item["id"] == "dnd5e.content.srd2014.feature.circle-of-the-land-circle-spells"
    )["card"]["minimum_level"] == 2
    assert next(
        item
        for item in subclass_features
        if item["id"] == "dnd5e.content.srd2014.feature.oath-of-devotion-oath-spells"
    )["card"]["minimum_level"] == 3
    rogue_expertise = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2014.feature.rogue-expertise"
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
        if item["id"].startswith(
            "dnd5e.content.srd2014.feature.warlock-eldritch-invocations"
        )
    ]
    assert len(warlock_invocations) == 1

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

    dragonborn = next(
        item
        for item in artifacts
        if item["kind"] == "species" and item["card"]["name"] == "Dragonborn"
    )
    assert dragonborn["application_state"] == "catalog_only"

    acolyte = next(item for item in artifacts if item["kind"] == "background")
    assert acolyte["card"]["skill_proficiencies"] == ["insight", "religion"]
    assert acolyte["card"]["background_grants"]["choices"]["language_count"] == 2
