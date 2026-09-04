from copy import deepcopy
from pathlib import Path

import pytest

from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.conditions import apply_effect_conditions
from sagasmith_dnd.core_content import build_srd2014_content
from sagasmith_dnd.sleep import resolve_sleep_targets, wake_sleep_effects


def _actor(name: str, hp: int, *, elf: bool = False, conditions: list[str] | None = None) -> dict:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["combat"]["hp"] = {"value": hp, "max": hp, "temp": 0}
    sheet["conditions"] = list(conditions or [])
    if elf:
        _, artifacts = build_srd2014_content(Path(__file__).resolve().parents[3] / "skills")
        elf_artifact = next(
            item
            for item in artifacts
            if item["kind"] == "species" and item["card"].get("name") == "Elf"
        )
        feature = next(
            item
            for item in elf_artifact["card"]["grants"]["features"]
            if item["name"] == "Fey Ancestry"
        )
        sheet["content"]["features"] = [
                {
                    "id": feature["id"],
                    "name": feature["name"],
                    "choices": deepcopy(feature["choices"]),
                "mechanic_refs": list(feature["mechanic_refs"]),
            }
        ]
    return {"id": name, "sheet": sheet, "derived": derive_character_sheet(sheet)}


def test_sleep_orders_current_hp_skips_immunity_and_does_not_mutate_inputs() -> None:
    targets = [
        _actor("high", 10),
        _actor("elf", 4, elf=True),
        _actor("undead", 2),
        _actor("already", 1, conditions=["unconscious"]),
        _actor("low", 3),
    ]
    targets[2]["sheet"]["progression"]["species"] = "Undead"
    before = deepcopy(targets)
    settled = resolve_sleep_targets(
        targets,
        pool=7,
        source_actor_id="caster",
        source_spell_id="sleep",
        source_rule_refs=["bundled:srd2014/07_Spells/Spells_Each/Sleep.md"],
    )
    assert targets == before
    assert [item["target_id"] for item in settled["targets"]] == [
        "already",
        "undead",
        "low",
        "elf",
        "high",
    ]
    assert [item["skip_reason"] for item in settled["targets"][:2]] == [
        "already_unconscious",
        "undead",
    ]
    assert settled["targets"][2]["affected"] is True
    assert settled["targets"][3]["skip_reason"] == "immune_to_magical_sleep"
    assert settled["pool_remaining"] == 4
    low = settled["sheets"]["low"]
    assert [item["name"] for item in low["effects"] if item["active"]] == ["Sleep"]
    effect = next(item for item in low["effects"] if item["active"])
    assert effect["source"] == "caster"
    assert effect["source_spell_id"] == "sleep"
    assert effect["duration"] == {"period": "minute", "remaining": 1}
    assert low["conditions"] == ["unconscious"]


def test_sleep_pool_requires_exact_current_hp_and_wake_preserves_other_source() -> None:
    target = _actor("target", 5, conditions=["unconscious"])
    other = {
        "id": "other-unconscious",
        "name": "Other effect",
        "kind": "timed_conditions",
        "source": "other",
        "source_spell_id": "other-spell",
        "active": True,
        "concentration": False,
        "duration": {"period": "round", "remaining": 1},
        "changes": [{"path": "conditions", "mode": "add", "value": "unconscious"}],
        "description": "another source",
    }
    target["sheet"]["effects"].append(other)
    apply_effect_conditions(target["sheet"], other)
    settled = resolve_sleep_targets(
        [target], pool=5, source_actor_id="caster", source_spell_id="sleep"
    )
    assert settled["pool_remaining"] == 5
    assert settled["targets"][0]["skip_reason"] == "already_unconscious"

    awake = wake_sleep_effects(settled["sheets"]["target"], reason="shaken_awake")
    assert awake["ended_effect_ids"] == []
    assert awake["sheet"]["conditions"] == ["unconscious"]

    fresh = _actor("fresh", 5)
    applied = resolve_sleep_targets(
        [fresh], pool=5, source_actor_id="caster", source_spell_id="sleep"
    )
    woke = wake_sleep_effects(applied["sheets"]["fresh"], reason="damaged")
    assert len(woke["ended_effect_ids"]) == 1
    assert woke["sheet"]["conditions"] == []
    assert woke["sheet"]["effects"][0]["active"] is False


@pytest.mark.parametrize("bad_pool", [-1, True, 1.5])
def test_sleep_rejects_invalid_pool_without_target_mutation(bad_pool: object) -> None:
    target = _actor("target", 1)
    before = deepcopy(target)
    with pytest.raises(ValueError, match="pool"):
        resolve_sleep_targets(
            [target], pool=bad_pool, source_actor_id="caster", source_spell_id="sleep"
        )
    assert target == before
