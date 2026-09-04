import pytest

import sagasmith_dnd.combat_engine as combat_engine
from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.combat_engine import resolve_actor_check
from sagasmith_dnd.rule_engine import resolution_context


class _SequenceRng:
    def __init__(self, *values: int) -> None:
        self._values = iter(values)

    def randint(self, _lower: int, _upper: int) -> int:
        return next(self._values)


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
                    **(
                        {"magical_sleep_immunity": True}
                        if trait_kind == "fey_ancestry"
                        else {}
                    ),
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(combat_engine, "core_receipts", lambda *_args: [])
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


def test_species_save_advantage_cancels_disadvantage_and_is_2014_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(combat_engine, "core_receipts", lambda *_args: [])
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
