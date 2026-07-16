from collections import Counter
from pathlib import Path

from sagasmith_dnd.core_content import PACK_VERSION, build_srd2014_content


def test_srd2014_content_uses_leaf_records_and_structured_eligibility() -> None:
    workspace = Path(__file__).resolve().parents[2]
    manifest, artifacts = build_srd2014_content(workspace / "SagaSmith-dnd-skills")
    counts = Counter(item["kind"] for item in artifacts)

    assert manifest["version"] == PACK_VERSION == "1.2.0"
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
