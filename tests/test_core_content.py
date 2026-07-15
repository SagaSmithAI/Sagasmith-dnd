from collections import Counter
from pathlib import Path

from sagasmith_dnd.core_content import PACK_VERSION, build_srd2014_content


def test_srd2014_content_uses_leaf_records_and_structured_eligibility() -> None:
    workspace = Path(__file__).resolve().parents[2]
    manifest, artifacts = build_srd2014_content(workspace / "SagaSmith-dnd-skills")
    counts = Counter(item["kind"] for item in artifacts)

    assert manifest["version"] == PACK_VERSION == "1.1.0"
    assert counts["spell"] == 319
    assert counts["species"] == 9
    assert counts["class"] == 12
    assert counts["subclass"] == 12
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

    acolyte = next(item for item in artifacts if item["kind"] == "background")
    assert acolyte["card"]["skill_proficiencies"] == ["insight", "religion"]
    assert acolyte["card"]["background_grants"]["choices"]["language_count"] == 2
