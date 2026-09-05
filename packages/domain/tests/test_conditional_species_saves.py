from pathlib import Path

import pytest

from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.combat_engine import (
    CombatEngineError,
    NeedsRulingError,
    resolve_actor_check,
    resolve_hypnotic_pattern_target,
    resolve_save_damage_to_sheets,
)
from sagasmith_dnd.core_content import build_srd2014_content
from sagasmith_dnd.rule_engine import resolution_context
from sagasmith_dnd.standard_spell_ids import CORE_HYPNOTIC_PATTERN_SPELL_ID


class _SequenceRng:
    def __init__(self, *values: int) -> None:
        self._values = iter(values)

    def randint(self, _lower: int, _upper: int) -> int:
        return next(self._values)


@pytest.mark.parametrize(
    ("trait", "condition"), [("fey_ancestry", "charmed"), ("halfling_brave", "frightened")]
)
@pytest.mark.parametrize("disadvantage", [False, True])
def test_shared_save_damage_preserves_authoritative_effect_conditions(
    trait, condition, disadvantage
):
    mechanic = f"dnd5e.core.save.{trait}"
    actor = _actor(trait, mechanic)
    actor["sheet"]["combat"]["hp"] = {"value": 30, "max": 30, "temp": 0}
    actor["derived"] = derive_character_sheet(actor["sheet"])
    result = resolve_save_damage_to_sheets(
        [actor],
        save_ability="wisdom",
        save_dc=10,
        damage_expression="1d6",
        damage_type="psychic",
        half_on_success=True,
        source="reviewed-effect",
        disadvantage=disadvantage,
        ruleset="2014",
        rules=resolution_context(
            {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []},
            facts={
                "save_source_kind": "magical_effect",
                "save_effect_conditions": [condition],
                "save_against_poison": False,
            },
        ),
        rng=_SequenceRng(2, 2, 18),
    )
    saved = result["result"]["targets"][0]["save"]
    assert saved["roll_mode"] == ("normal" if disadvantage else "advantage")
    assert saved["rolls"] == ([2] if disadvantage else [2, 18])
    assert sum(receipt["mechanic_id"] == mechanic for receipt in saved["rule_receipts"]) == 1
    assert result["sheets"]["species-test"]["combat"]["hp"]["value"] == (28 if disadvantage else 29)


@pytest.mark.parametrize("trait", ["fey_ancestry", "halfling_brave"])
def test_shared_save_damage_cannot_invent_empty_effect_conditions(trait):
    actor = _actor(trait, f"dnd5e.core.save.{trait}")
    with pytest.raises(NeedsRulingError, match="authoritative effect conditions"):
        resolve_save_damage_to_sheets(
            [actor],
            save_ability="wisdom",
            save_dc=10,
            damage_expression="1d6",
            damage_type="psychic",
            half_on_success=True,
            source="unclassified-effect",
            ruleset="2014",
            rng=_SequenceRng(2, 2, 18),
        )


def _actor(trait_kind: str, mechanic_id: str) -> dict:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["content"]["features"] = [
        {
            "id": f"species-{trait_kind}",
            "name": trait_kind,
            "mechanic_refs": [mechanic_id],
            "choices": {
                "source_trait": {
                    "kind": trait_kind,
                    "automatic": True,
                    "source_excerpt": f"official {trait_kind}",
                    **({"magical_sleep_immunity": True} if trait_kind == "fey_ancestry" else {}),
                }
            },
        }
    ]
    return {
        "id": "species-test",
        "sheet": sheet,
        "derived": derive_character_sheet(sheet),
    }


@pytest.mark.parametrize(
    ("trait", "mechanic", "ability", "source", "conditions"),
    [
        (
            "dwarven_resilience",
            "dnd5e.core.save.dwarven_resilience",
            "constitution",
            "nonmagical_effect",
            ["poison"],
        ),
        (
            "fey_ancestry",
            "dnd5e.core.save.fey_ancestry",
            "wisdom",
            "spell",
            ["charmed"],
        ),
        (
            "gnome_cunning",
            "dnd5e.core.save.gnome_cunning",
            "intelligence",
            "magical_effect",
            [],
        ),
        (
            "halfling_brave",
            "dnd5e.core.save.halfling_brave",
            "wisdom",
            "nonmagical_effect",
            ["frightened"],
        ),
    ],
)
def test_2014_species_save_traits_add_authoritative_advantage(
    trait: str,
    mechanic: str,
    ability: str,
    source: str,
    conditions: list[str],
) -> None:
    actor = _actor(trait, mechanic)
    result = resolve_actor_check(
        actor,
        kind="save",
        ability=ability,
        dc=10,
        save_source_kind=source,
        save_effect_conditions=conditions,
        ruleset="2014",
        rules=resolution_context(
            {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []},
            facts={"save_against_poison": True} if trait == "dwarven_resilience" else {},
        ),
        rng=_SequenceRng(2, 18),
    )

    assert result["roll_mode"] == "advantage"
    receipts = [item for item in result["rule_receipts"] if item["mechanic_id"] == mechanic]
    assert len(receipts) == 1
    assert receipts[0]["event"] == "check.resolve"


def test_species_save_advantage_cancels_disadvantage_and_is_2014_only() -> None:
    actor = _actor("dwarven_resilience", "dnd5e.core.save.dwarven_resilience")
    cancelled = resolve_actor_check(
        actor,
        kind="save",
        ability="constitution",
        dc=10,
        save_source_kind="poison",
        save_effect_conditions=["poison"],
        ruleset="2014",
        rules=resolution_context(
            {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []},
            facts={"save_against_poison": True},
        ),
        disadvantage=True,
        rng=_SequenceRng(12, 3),
    )
    assert cancelled["roll_mode"] == "normal"

    actor["sheet"]["edition"] = "2024"
    actor["derived"] = derive_character_sheet(actor["sheet"])
    modern = resolve_actor_check(
        actor,
        kind="save",
        ability="constitution",
        dc=10,
        save_source_kind="poison",
        save_effect_conditions=["poison"],
        ruleset="2024",
        rng=_SequenceRng(12, 3),
    )
    assert modern["roll_mode"] == "normal"


@pytest.mark.parametrize(
    "ability",
    ["strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"],
)
@pytest.mark.parametrize("source", ["spell", "magical_effect", "nonmagical_effect"])
def test_gnome_cunning_only_applies_to_mental_magical_saves(ability: str, source: str) -> None:
    actor = _actor("gnome_cunning", "dnd5e.core.save.gnome_cunning")
    result = resolve_actor_check(
        actor,
        kind="save",
        ability=ability,
        dc=10,
        save_source_kind=source,
        save_effect_conditions=[],
        ruleset="2014",
        rng=_SequenceRng(2, 18),
    )
    expected = ability in {"intelligence", "wisdom", "charisma"} and source != "nonmagical_effect"
    assert result["roll_mode"] == ("advantage" if expected else "normal")


@pytest.mark.parametrize("value", [None, "true", 1])
def test_dwarven_resilience_requires_strict_poison_fact(value: object) -> None:
    actor = _actor("dwarven_resilience", "dnd5e.core.save.dwarven_resilience")
    rules = resolution_context(
        {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []},
        facts={} if value is None else {"save_against_poison": value},
    )
    expected_error = NeedsRulingError if value is None else CombatEngineError
    with pytest.raises(expected_error):
        resolve_actor_check(
            actor,
            kind="save",
            ability="constitution",
            dc=10,
            save_effect_conditions=[],
            ruleset="2014",
            rules=rules,
            rng=_SequenceRng(2, 18),
        )

    false_rules = resolution_context(
        {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []},
        facts={"save_against_poison": False},
    )
    normal = resolve_actor_check(
        actor,
        kind="save",
        ability="constitution",
        dc=10,
        save_effect_conditions=[],
        ruleset="2014",
        rules=false_rules,
        rng=_SequenceRng(2, 18),
    )
    assert normal["roll_mode"] == "normal"


def test_malformed_fey_trait_is_rejected_before_rng() -> None:
    actor = _actor("fey_ancestry", "dnd5e.core.save.fey_ancestry")
    actor["sheet"]["content"]["features"][0]["choices"]["source_trait"]["automatic"] = "true"
    with pytest.raises(CombatEngineError):
        resolve_actor_check(
            actor,
            kind="save",
            ability="wisdom",
            dc=10,
            save_source_kind="spell",
            save_effect_conditions=["charmed"],
            ruleset="2014",
            rng=_SequenceRng(20),
        )


def test_dwarf_uses_poison_fact_without_effect_condition_list() -> None:
    actor = _actor("dwarven_resilience", "dnd5e.core.save.dwarven_resilience")
    rules = resolution_context(
        {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []},
        facts={"save_against_poison": True},
    )
    result = resolve_actor_check(
        actor,
        kind="save",
        ability="constitution",
        dc=10,
        ruleset="2014",
        rules=rules,
        rng=_SequenceRng(2, 18),
    )
    assert result["roll_mode"] == "advantage"
    assert len(result["rolls"]) == 2


def test_auto_failed_strength_save_does_not_require_trait_classification_or_roll() -> None:
    actor = _actor("dwarven_resilience", "dnd5e.core.save.dwarven_resilience")
    actor["sheet"]["conditions"] = ["paralyzed"]
    result = resolve_actor_check(
        actor,
        kind="save",
        ability="strength",
        dc=10,
        ruleset="2014",
        rng=_SequenceRng(20),
    )
    assert result["automatic_failure"] is True
    assert result["rolls"] == []


def test_duplicate_and_combined_traits_roll_once_with_stable_receipts() -> None:
    traits = [
        ("dwarven_resilience", "dnd5e.core.save.dwarven_resilience"),
        ("fey_ancestry", "dnd5e.core.save.fey_ancestry"),
        ("gnome_cunning", "dnd5e.core.save.gnome_cunning"),
        ("halfling_brave", "dnd5e.core.save.halfling_brave"),
    ]
    rules = resolution_context(
        {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []},
        facts={"save_against_poison": True},
    )

    def resolve(features: list[tuple[str, str]]) -> dict:
        actor = _actor(*features[0])
        actor["sheet"]["content"]["features"] = [
            _actor(kind, mechanic)["sheet"]["content"]["features"][0] for kind, mechanic in features
        ]
        actor["derived"] = derive_character_sheet(actor["sheet"])
        return resolve_actor_check(
            actor,
            kind="save",
            ability="wisdom",
            dc=10,
            save_source_kind="spell",
            save_effect_conditions=["charmed", "frightened"],
            ruleset="2014",
            rules=rules,
            rng=_SequenceRng(2, 18),
        )

    combined = resolve(traits + [traits[0]])
    reversed_features = resolve(list(reversed(traits)))
    assert len(combined["rolls"]) == 2
    assert len(reversed_features["rolls"]) == 2
    combined_ids = [item["mechanic_id"] for item in combined["rule_receipts"]]
    reversed_ids = [item["mechanic_id"] for item in reversed_features["rule_receipts"]]
    assert combined_ids == reversed_ids == [item[1] for item in traits]


def test_conditional_traits_do_not_apply_to_non_save_or_concentration() -> None:
    actor = _actor("dwarven_resilience", "dnd5e.core.save.dwarven_resilience")
    result = resolve_actor_check(
        actor,
        kind="ability",
        ability="constitution",
        dc=10,
        ruleset="2014",
        rng=_SequenceRng(12),
    )
    assert result["roll_mode"] == "normal"
    assert len(result["rolls"]) == 1

    concentration = resolve_actor_check(
        actor,
        kind="save",
        ability="constitution",
        dc=10,
        save_purpose="concentration",
        ruleset="2014",
        rng=_SequenceRng(12),
    )
    assert concentration["roll_mode"] == "normal"
    assert len(concentration["rolls"]) == 1


def test_real_built_hill_dwarf_and_elf_traits_apply_with_core_receipts() -> None:
    _, artifacts = build_srd2014_content(Path(__file__).resolve().parents[3] / "skills")
    cards = {str(item["card"].get("name")): item["card"] for item in artifacts}

    def actor_from_card(name: str) -> dict:
        card = cards[name]
        sheet = default_character_sheet()
        sheet["edition"] = "2014"
        sheet["content"]["features"] = list(card["grants"]["features"])
        return {"id": name, "sheet": sheet, "derived": derive_character_sheet(sheet)}

    rules = resolution_context(
        {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []},
        facts={"save_against_poison": True},
    )
    dwarf = resolve_actor_check(
        actor_from_card("Hill Dwarf"),
        kind="save",
        ability="constitution",
        dc=10,
        ruleset="2014",
        rules=rules,
        rng=_SequenceRng(2, 18),
    )
    elf = resolve_actor_check(
        actor_from_card("High Elf"),
        kind="save",
        ability="wisdom",
        dc=10,
        save_source_kind="spell",
        save_effect_conditions=["charmed"],
        ruleset="2014",
        rules=rules,
        rng=_SequenceRng(2, 18),
    )
    assert len(dwarf["rolls"]) == 2
    assert len(elf["rolls"]) == 2


@pytest.mark.parametrize("source", [None, "", "unknown"])
def test_gnome_unknown_source_kind_is_rejected_before_rng(source: str | None) -> None:
    actor = _actor("gnome_cunning", "dnd5e.core.save.gnome_cunning")
    with pytest.raises(NeedsRulingError):
        resolve_actor_check(
            actor,
            kind="save",
            ability="intelligence",
            dc=10,
            save_source_kind=source,
            save_effect_conditions=[],
            ruleset="2014",
            rng=_SequenceRng(20),
        )


@pytest.mark.parametrize("source", ["argument", "fact"])
@pytest.mark.parametrize("conditions", ["charmed", True, 1, False, 0, ""])
def test_condition_classification_rejects_non_list_types_before_rng(
    conditions: object, source: str
) -> None:
    actor = _actor("fey_ancestry", "dnd5e.core.save.fey_ancestry")
    rules = resolution_context(
        {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []},
        facts={"save_effect_conditions": conditions} if source == "fact" else {},
    )
    with pytest.raises(CombatEngineError):
        resolve_actor_check(
            actor,
            kind="save",
            ability="wisdom",
            dc=10,
            save_source_kind="spell",
            save_effect_conditions=conditions if source == "argument" else None,  # type: ignore[arg-type]
            ruleset="2014",
            rules=rules,
            rng=_SequenceRng(20),
        )


@pytest.mark.parametrize("conditions", [None, False, 0, ""])
def test_condition_classification_rejects_invalid_rule_fact_values_before_rng(
    conditions: object,
) -> None:
    actor = _actor("fey_ancestry", "dnd5e.core.save.fey_ancestry")
    rules = resolution_context(
        {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []},
        facts={"save_effect_conditions": conditions},
    )
    with pytest.raises(CombatEngineError):
        resolve_actor_check(
            actor,
            kind="save",
            ability="wisdom",
            dc=10,
            save_source_kind="spell",
            ruleset="2014",
            rules=rules,
            rng=_SequenceRng(),
        )


@pytest.mark.parametrize(
    ("name", "expected_rolls", "expected_mechanics"),
    [
        ("Hill Dwarf", 1, []),
        ("High Elf", 2, ["dnd5e.core.save.fey_ancestry"]),
        ("Rock Gnome", 2, ["dnd5e.core.save.gnome_cunning"]),
        ("Halfling", 1, []),
    ],
)
def test_native_hypnotic_pattern_uses_real_species_projection(
    name: str, expected_rolls: int, expected_mechanics: list[str]
) -> None:
    _, artifacts = build_srd2014_content(Path(__file__).resolve().parents[3] / "skills")
    card = next(item["card"] for item in artifacts if item["card"].get("name") == name)
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["content"]["features"] = list(card["grants"]["features"])
    actor = {"id": name, "sheet": sheet, "derived": derive_character_sheet(sheet)}
    rules = resolution_context(
        {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []},
        facts={"save_against_poison": False},
    )
    result = resolve_hypnotic_pattern_target(
        actor,
        caster_id="caster",
        spell_id=CORE_HYPNOTIC_PATTERN_SPELL_ID,
        save_dc=10,
        rules=rules,
        rng=_SequenceRng(2, 18),
    )
    save = result["result"]["save"]
    assert save["rolls"] == ([2] if expected_rolls == 1 else [2, 18])
    assert [item["mechanic_id"] for item in save["rule_receipts"]] == expected_mechanics
    assert save["ruleset_fingerprint"] == rules.fingerprint
