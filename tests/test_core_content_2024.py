from collections import Counter
from pathlib import Path

from sagasmith_dnd.character_schema import add_inventory_item, default_character_sheet
from sagasmith_dnd.core_content_2024 import (
    PACK_ID,
    PACK_VERSION,
    build_srd2024_content,
    parse_srd2024_monster_artifact,
)
from sagasmith_dnd.spell_resolution import effective_spell_resolution


def test_srd2024_content_covers_every_core_catalog_kind_with_exact_sources() -> None:
    workspace = Path(__file__).resolve().parents[2]
    manifest, artifacts = build_srd2024_content(workspace / "SagaSmith-dnd-skills")
    counts = Counter(item["kind"] for item in artifacts)

    assert manifest["id"] == PACK_ID
    assert manifest["version"] == PACK_VERSION == "1.1.0"
    assert manifest["editions"] == ["2024"]
    assert counts["class"] == 12
    assert counts["subclass"] == 12
    assert counts["species"] == 9
    assert counts["background"] == 4
    assert counts["feat"] >= 15
    assert counts["feature"] >= 220
    assert counts["spell"] >= 330
    assert counts["item"] >= 460
    assert counts["monster"] >= 320
    assert all(
        ref.startswith("bundled:srd2024/")
        for artifact in artifacts
        for ref in artifact["rule_refs"]
    )
    assert all(artifact["source_citations"] for artifact in artifacts)

    items = {
        item["card"]["name"]: item
        for item in artifacts
        if item["kind"] == "item"
    }
    assert set(items) >= {
        "Calligrapher's Supplies",
        "Thieves' Tools",
        "Dice",
        "Dragonchess",
        "Playing Cards",
        "Three-Dragon Ante",
        "Arrows",
        "Holy Symbol",
        "Clothes, Traveler's",
        "Warhorse",
        "Airship",
    }
    for name in ("Dagger", "Arrows", "Calligrapher's Supplies", "Airship"):
        template = items[name]["card"]["inventory_template"]
        _, item_id = add_inventory_item(default_character_sheet(), template)
        assert item_id
    assert items["Dagger"]["card"]["inventory_template"]["kind"] == "weapon"
    assert items["Dagger"]["card"]["inventory_template"]["mechanics"][
        "mastery"
    ] == "nick"
    assert items["Arrows"]["card"]["inventory_template"]["quantity"] == 20
    assert items["Arrows"]["card"]["inventory_template"]["weight_oz"] == 0.8
    assert items["Calligrapher's Supplies"]["card"]["inventory_template"][
        "weight_oz"
    ] == 80
    assert items["Mirror"]["card"]["inventory_template"]["weight_oz"] == 8

    invocations = [
        item
        for item in artifacts
        if item["kind"] == "feature"
        and item["card"].get("feature_subtype") == "eldritch_invocation"
    ]
    assert len(invocations) == 28
    assert {item["card"]["name"] for item in invocations} >= {
        "Agonizing Blast",
        "Pact of the Blade",
        "Thirsting Blade",
    }
    agonizing_blast = next(
        item for item in invocations if item["card"]["name"] == "Agonizing Blast"
    )
    assert agonizing_blast["card"]["minimum_level"] == 2
    assert agonizing_blast["card"]["repeatable"] is True
    assert "Warlock Cantrip That Deals Damage" in agonizing_blast["card"][
        "prerequisite_text"
    ]

    invocation_feature = next(
        item
        for item in artifacts
        if item["id"]
        == "dnd5e.content.srd2024.feature.warlock-eldritch-invocations"
    )
    requirements = invocation_feature["card"]["selection_requirements"]
    assert requirements["kind"] == "eldritch_invocations_2024"
    assert requirements["count"] == 1
    assert invocation_feature["card"]["selection_requirements_by_level"]["2"][
        "count"
    ] == 2
    assert invocation_feature["card"]["repeatable_selection_levels"] == [
        1,
        2,
        5,
        7,
        9,
        12,
        15,
        18,
    ]
    assert requirements["option_prerequisites"]["Devouring Blade"] == {
        "minimum_level": 12,
        "required_invocation": "Thirsting Blade",
    }

    metamagic_options = [
        item
        for item in artifacts
        if item["kind"] == "feature"
        and item["card"].get("feature_subtype") == "metamagic_option"
    ]
    assert len(metamagic_options) == 10
    assert all(item["application_state"] == "catalog_only" for item in metamagic_options)
    assert {item["card"]["name"] for item in metamagic_options} >= {
        "Careful Spell",
        "Seeking Spell",
        "Twinned Spell",
    }
    assert next(
        item for item in metamagic_options if item["card"]["name"] == "Quickened Spell"
    )["card"]["choices"]["sorcery_point_cost"] == 2

    metamagic = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2024.feature.sorcerer-metamagic"
    )["card"]
    assert metamagic["repeatable_selection_levels"] == [2, 10, 17]
    assert metamagic["selection_requirements_by_level"]["10"]["count"] == 2
    assert len(metamagic["selection_requirements"]["options"]) == 10


def test_srd2024_changed_spells_never_borrow_2014_resolution_values() -> None:
    workspace = Path(__file__).resolve().parents[2]
    _, artifacts = build_srd2024_content(workspace / "SagaSmith-dnd-skills")
    spells = {
        item["card"]["name"]: item
        for item in artifacts
        if item["kind"] == "spell"
    }

    cure_wounds = spells["Cure Wounds"]
    healing_word = spells["Healing Word"]
    chill_touch = spells["Chill Touch"]
    assert cure_wounds["card"]["resolution"]["healing"]["base_dice"] == "2d8"
    assert healing_word["card"]["resolution"]["healing"]["base_dice"] == "2d4"
    assert chill_touch["card"]["resolution"]["attack"]["mode"] == "melee"
    assert chill_touch["card"]["resolution"]["attack"]["damage"]["base_dice"] == "1d10"
    assert effective_spell_resolution(cure_wounds["card"]) == cure_wounds["card"][
        "resolution"
    ]
    assert spells["Fly"]["mechanic_refs"] == ["dnd5e.core.spell.fly"]
    assert spells["Invisibility"]["mechanic_refs"] == [
        "dnd5e.core.spell.invisibility"
    ]
    assert spells["Hypnotic Pattern"]["mechanic_refs"] == [
        "dnd5e.core.spell.hypnotic_pattern"
    ]


def test_srd2024_features_and_weapons_carry_executable_resource_evidence() -> None:
    workspace = Path(__file__).resolve().parents[2]
    _, artifacts = build_srd2024_content(workspace / "SagaSmith-dnd-skills")

    second_wind = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2024.feature.fighter-second-wind"
    )
    resource = second_wind["card"]["mechanical_grants"]["resources"]["second_wind"]
    assert resource["max"] == 2
    assert resource["recovery_amounts"] == {"short_rest": 1, "long_rest": "all"}

    channel_divinity = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2024.feature.cleric-channel-divinity"
    )
    assert channel_divinity["mechanic_refs"] == [
        "dnd5e.core.activity.divine_spark",
        "dnd5e.core.activity.turn_undead",
    ]

    longsword = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2024.item.longsword"
    )
    assert longsword["card"]["mechanics"]["mastery"] == "sap"
    assert longsword["mechanic_refs"] == ["dnd5e.core.weapon.mastery"]

    human = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2024.species.human"
    )
    resourceful = next(
        feature
        for feature in human["card"]["grants"]["features"]
        if feature["name"] == "Resourceful"
    )
    assert resourceful["choices"]["grant_heroic_inspiration_on"] == "long_rest"

    mechanics_by_feature = {
        item["card"]["name"]: item["mechanic_refs"]
        for item in artifacts
        if item["kind"] == "feature"
        and item["card"]["name"]
        in {
            "Jack of All Trades",
            "Sneak Attack",
            "Cunning Action",
            "Preserve Life",
            "Pact Magic",
            "Sear Undead",
            "Sorcerous Restoration",
        }
    }
    assert mechanics_by_feature == {
        "Jack of All Trades": ["dnd5e.core.check.jack_of_all_trades"],
        "Sneak Attack": ["dnd5e.core.attack.sneak_attack"],
        "Cunning Action": ["dnd5e.core.activity.cunning_action"],
        "Preserve Life": ["dnd5e.core.activity.preserve_life"],
        "Pact Magic": ["dnd5e.core.spell.pact_magic"],
        "Sear Undead": ["dnd5e.core.activity.sear_undead"],
        "Sorcerous Restoration": ["dnd5e.core.rest.sorcerous_restoration"],
    }

    fighter_extra_attacks = [
        item["card"]
        for item in artifacts
        if item["kind"] == "feature"
        and item["card"].get("class_name") == "Fighter"
        and item["card"]["name"]
        in {"Extra Attack", "Two Extra Attacks", "Three Extra Attacks"}
    ]
    assert len(fighter_extra_attacks) == 3
    assert all(
        card["attack_scaling"]["attacks_per_action_by_level"]
        == {"5": 2, "11": 3, "20": 4}
        for card in fighter_extra_attacks
    )


def test_srd2024_primary_class_resources_are_structured_without_fake_execution() -> None:
    workspace = Path(__file__).resolve().parents[2]
    _, artifacts = build_srd2024_content(workspace / "SagaSmith-dnd-skills")
    features = {
        (item["card"].get("class_name"), item["card"]["name"]): item
        for item in artifacts
        if item["kind"] == "feature" and not item["card"].get("subclass_name")
    }

    expected_shared = {
        ("Barbarian", "Rage"): ("rage", {"1": 2, "3": 3, "6": 4, "12": 5, "17": 6}),
        ("Druid", "Wild Shape"): ("wild_shape", {"2": 2, "6": 3, "17": 4}),
        ("Monk", "Monk's Focus"): ("focus_points", {}),
        ("Paladin", "Lay On Hands"): ("lay_on_hands", {}),
        ("Paladin", "Channel Divinity"): ("channel_divinity", {"3": 2, "11": 3}),
        (
            "Ranger",
            "Favored Enemy",
        ): ("favored_enemy_hunters_mark", {"1": 2, "5": 3, "9": 4, "13": 5, "17": 6}),
        ("Sorcerer", "Font of Magic"): ("sorcery_points", {}),
    }
    for feature_key, (resource_key, maximums) in expected_shared.items():
        artifact = features[feature_key]
        card = artifact["card"]
        assert card["resource_key"] == resource_key
        assert resource_key in card["mechanical_grants"]["resources"]
        assert card["resource_scaling"]["target"] == resource_key
        assert card["resource_scaling"]["class_name"] == feature_key[0]
        assert card["resource_scaling"]["maximum_by_level"] == maximums
        assert card["ruling_requirements"][0]["default_resolver"] == "agent"
        assert artifact["mechanic_refs"] == []

    bard = features[("Bard", "Bardic Inspiration")]["card"]
    assert bard["resource_scaling"]["target"] == "bardic_inspiration"
    assert bard["resource_scaling"]["maximum_formula"] == {
        "kind": "ability_modifier",
        "ability": "charisma",
        "minimum": 1,
        "multiplier": 1,
        "offset": 0,
    }
    assert bard["resource_scaling"]["recovery_by_level"] == {"5": "short_rest"}
    assert bard["scaling"][-1] == {
        "level": 15,
        "value": 12,
        "description": "Bardic Inspiration die d12",
    }

    indomitable = features[("Fighter", "Indomitable")]["card"]
    innate_sorcery = features[("Sorcerer", "Innate Sorcery")]["card"]
    assert indomitable["resource_scaling"]["target"] == "uses"
    assert indomitable["resource_scaling"]["maximum_by_level"] == {
        "9": 1,
        "13": 2,
        "17": 3,
    }
    assert innate_sorcery["uses"]["max"] == 2

    for feature_key in {
        ("Monk", "Uncanny Metabolism"),
        ("Ranger", "Tireless"),
        ("Ranger", "Nature's Veil"),
        ("Rogue", "Stroke of Luck"),
        ("Sorcerer", "Sorcerous Restoration"),
    }:
        assert features[feature_key]["card"]["resource_scaling"]["target"] == "uses"

    # The class-table headings occur inside these feature descriptions in the
    # source PDF conversion. They must not truncate the rule text after the table.
    assert "Damage Resistance" in features[("Barbarian", "Rage")]["card"]["description"]
    assert "Number of Uses" in bard["description"]
    assert "As a Bonus Action" in features[("Paladin", "Lay On Hands")]["card"][
        "description"
    ]


def test_srd2024_advancement_choices_and_feat_prerequisites_are_executable() -> None:
    workspace = Path(__file__).resolve().parents[2]
    _, artifacts = build_srd2024_content(workspace / "SagaSmith-dnd-skills")
    by_id = {item["id"]: item for item in artifacts}

    fighter_asi = by_id[
        "dnd5e.content.srd2024.feature.fighter-ability-score-improvement"
    ]["card"]
    assert fighter_asi["unlock_levels"] == [4, 6, 8, 12, 14, 16]
    assert fighter_asi["repeatable_selection_levels"] == [4, 6, 8, 12, 14, 16]
    assert fighter_asi["selection_requirements"]["kind"] == "feat_grant"

    bard_expertise = by_id["dnd5e.content.srd2024.feature.bard-expertise"]["card"]
    assert bard_expertise["unlock_levels"] == [2, 9]
    assert bard_expertise["selection_requirements_by_level"]["9"]["count"] == 2

    mystic_arcanum = by_id[
        "dnd5e.content.srd2024.feature.warlock-mystic-arcanum"
    ]["card"]
    assert mystic_arcanum["unlock_levels"] == [11, 13, 15, 17]
    assert mystic_arcanum["selection_requirements_by_level"]["17"][
        "spell_level"
    ] == 9

    spell_mastery = by_id[
        "dnd5e.content.srd2024.feature.wizard-spell-mastery"
    ]["card"]["selection_requirements"]
    assert spell_mastery["required_spell_levels"] == [1, 2]
    assert spell_mastery["casting_times"] == ["Action"]

    evocation_savant = by_id[
        "dnd5e.content.srd2024.feature.evoker-evocation-savant"
    ]["card"]["selection_requirements"]
    assert evocation_savant["schools"] == ["Evocation"]
    assert evocation_savant["grant_method"] == "spellbook"

    thieves_cant = by_id[
        "dnd5e.content.srd2024.feature.rogue-thieves-cant"
    ]["card"]
    assert thieves_cant["selection_requirements"]["kind"] == "language_grant"
    assert thieves_cant["mechanical_grants"]["languages"] == ["Thieves' Cant"]

    circle_spells = by_id[
        "dnd5e.content.srd2024.feature."
        "circle-of-the-land-circle-of-the-land-spells"
    ]["card"]
    assert circle_spells["selection_requirements"]["options"] == [
        "Arid",
        "Polar",
        "Temperate",
        "Tropical",
    ]
    assert circle_spells["always_prepared_spell_options"]["Arid"][0] == {
        "name": "Blur",
        "minimum_level": 3,
    }

    ability_score_improvement = by_id[
        "dnd5e.content.srd2024.feat.ability-score-improvement"
    ]["card"]
    assert ability_score_improvement["repeatable"] is True
    assert ability_score_improvement["prerequisites"] == [
        {"kind": "level_minimum", "minimum": 4}
    ]
    assert ability_score_improvement["selection_requirements"][
        "allowed_distributions"
    ] == [[2], [1, 1]]

    grappler = by_id["dnd5e.content.srd2024.feat.grappler"]["card"]
    assert grappler["prerequisites"] == [
        {"kind": "level_minimum", "minimum": 4},
        {
            "kind": "ability_any_minimum",
            "abilities": ["strength", "dexterity"],
            "minimum": 13,
        },
    ]
    assert by_id["dnd5e.content.srd2024.feat.magic-initiate"]["card"][
        "selection_requirements"
    ]["kind"] == "magic_initiate"
    skilled = by_id["dnd5e.content.srd2024.feat.skilled"]["card"][
        "selection_requirements"
    ]
    assert skilled["kind"] == "proficiency_grants"
    assert skilled["count"] == 3

    subclass_markers = [
        item
        for item in artifacts
        if item["kind"] == "feature"
        and item["card"]["name"].casefold().endswith(" subclass")
    ]
    assert subclass_markers
    assert all(item["application_state"] == "catalog_only" for item in subclass_markers)


def test_srd2024_backgrounds_use_character_schema_shaped_grants() -> None:
    workspace = Path(__file__).resolve().parents[2]
    _, artifacts = build_srd2024_content(workspace / "SagaSmith-dnd-skills")
    backgrounds = {
        item["card"]["name"]: item["card"]
        for item in artifacts
        if item["kind"] == "background"
    }

    acolyte = backgrounds["Acolyte"]
    grants = acolyte["background_grants"]
    assert set(grants) == {
        "feature",
        "equipment_item_ids",
        "languages",
        "tools",
        "choices",
    }
    assert grants["feature"] == "Magic Initiate (Cleric)"
    assert grants["choices"]["origin_feat_preset"] == {"source_class": "Cleric"}
    assert grants["choices"]["allowed_ability_score_distributions"] == [
        [2, 1],
        [1, 1, 1],
    ]
    assert grants["choices"]["equipment_packages"]["A"]["wallet"] == {"gp": 8}
    assert grants["choices"]["equipment_packages"]["B"] == {
        "items": [],
        "wallet": {"gp": 50},
    }
    assert backgrounds["Soldier"]["background_grants"]["choices"][
        "tool_options"
    ] == ["Dice", "Dragonchess", "Playing Cards", "Three-Dragon Ante"]
    item_ids = {item["id"] for item in artifacts if item["kind"] == "item"}
    for background in backgrounds.values():
        for package_item in background["background_grants"]["choices"][
            "equipment_packages"
        ]["A"]["items"]:
            if not package_item.get("selected_tool"):
                assert package_item["artifact_id"] in item_ids


def test_srd2024_unstructured_rules_retain_agent_context_not_fake_mechanics() -> None:
    workspace = Path(__file__).resolve().parents[2]
    _, artifacts = build_srd2024_content(workspace / "SagaSmith-dnd-skills")
    light = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2024.spell.light"
    )
    aboleth = next(
        item
        for item in artifacts
        if item["id"] == "dnd5e.content.srd2024.monster.aboleth"
    )

    assert light["card"]["ruling_requirements"][0]["default_resolver"] == "agent"
    assert light["card"]["ruling_requirements"][0]["source_excerpt"]
    assert aboleth["application_state"] == "source_bound"
    assert "**Tentacle.**" in aboleth["card"]["statblock_source"]
    assert aboleth["card"]["ruling_requirements"][0]["default_resolver"] == "agent"


def test_srd2024_monsters_cross_file_boundaries_and_preserve_modifier_only_blocks() -> None:
    workspace = Path(__file__).resolve().parents[2]
    _, artifacts = build_srd2024_content(workspace / "SagaSmith-dnd-skills")
    monsters = {
        item["card"]["name"]: item
        for item in artifacts
        if item["kind"] == "monster"
    }

    aboleth = parse_srd2024_monster_artifact(monsters["Aboleth"])
    chain_devil = parse_srd2024_monster_artifact(monsters["Chain Devil"])
    assert aboleth.sheet["edition"] == "2024"
    assert aboleth.sheet["combat"]["hp"]["max"] == 150
    assert next(
        item
        for item in aboleth.sheet["inventory"]["items"]
        if item["name"] == "Tentacle"
    )["mechanics"]["attack_bonus_override"] == 9
    assert len(monsters["Chain Devil"]["rule_refs"]) == 2
    assert chain_devil.sheet["abilities"]["constitution"]["score"] == 18

    otyugh = parse_srd2024_monster_artifact(monsters["Otyugh"])
    assert otyugh.sheet["abilities"]["strength"]["score"] == 16
    assert otyugh.sheet["abilities"]["constitution"]["bonus"] == 3
    assert any(
        "canonical representatives" in note
        for note in otyugh.normalization_notes
    )
