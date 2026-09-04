from copy import deepcopy
from pathlib import Path

import pytest

from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.conditions import apply_effect_conditions
from sagasmith_dnd.core_content import build_srd2014_content
from sagasmith_dnd.sleep import SLEEP_SPELL_ID, resolve_sleep_targets, wake_sleep_effects


def _actor(name: str, hp: int, *, elf: bool = False, conditions: list[str] | None = None) -> dict:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["combat"]["hp"] = {"value": hp, "max": max(1, hp), "temp": 0}
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
    targets.append(_actor("charmed", 2))
    targets[-1]["sheet"]["traits"]["condition_immunities"] = ["charmed"]
    targets.append(_actor("zero", 0))
    targets[-1]["sheet"]["combat"]["hp"]["temp"] = 5
    before = deepcopy(targets)
    settled = resolve_sleep_targets(
        targets,
        pool=7,
        source_actor_id="caster",
        source_spell_id=SLEEP_SPELL_ID,
        source_rule_refs=["bundled:srd2014/07_Spells/Spells_Each/Sleep.md"],
    )
    assert targets == before
    assert [item["target_id"] for item in settled["targets"]] == [
        "zero",
        "already",
        "charmed",
        "undead",
        "low",
        "elf",
        "high",
    ]
    assert settled["targets"][0]["affected"] is True
    assert [item["skip_reason"] for item in settled["targets"][1:4]] == [
        "already_unconscious",
        "immune_to_charmed",
        "undead",
    ]
    assert settled["targets"][5]["skip_reason"] == "immune_to_magical_sleep"
    assert settled["pool_remaining"] == 4
    low = settled["sheets"]["low"]
    assert [item["name"] for item in low["effects"] if item["active"]] == ["Sleep"]
    effect = next(item for item in low["effects"] if item["active"])
    assert effect["source"] == "caster"
    assert effect["source_spell_id"] == SLEEP_SPELL_ID
    assert effect["duration"] == {"period": "round", "remaining": 10}
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
        [target], pool=5, source_actor_id="caster", source_spell_id=SLEEP_SPELL_ID
    )
    assert settled["pool_remaining"] == 5
    assert settled["targets"][0]["skip_reason"] == "already_unconscious"

    awake = wake_sleep_effects(settled["sheets"]["target"], reason="shaken_awake")
    assert awake["ended_effect_ids"] == []
    assert awake["sheet"]["conditions"] == ["unconscious"]

    fresh = _actor("fresh", 5)
    applied = resolve_sleep_targets(
        [fresh], pool=5, source_actor_id="caster", source_spell_id=SLEEP_SPELL_ID
    )
    sleep_sheet = applied["sheets"]["fresh"]
    sleep_effect = next(item for item in sleep_sheet["effects"] if item["active"])
    other["id"] = "other-unconscious-2"
    other["source_spell_id"] = "other-spell-2"
    sleep_sheet["effects"].append(other)
    apply_effect_conditions(sleep_sheet, other)
    woke = wake_sleep_effects(sleep_sheet, reason="damaged")
    assert len(woke["ended_effect_ids"]) == 1
    assert woke["sheet"]["conditions"] == ["unconscious"]
    assert sleep_effect["id"] in woke["ended_effect_ids"]
    assert woke["sheet"]["effects"][-1]["active"] is True

    second = resolve_sleep_targets(
        [_actor("second", 1)], pool=1, source_actor_id="caster", source_spell_id=SLEEP_SPELL_ID
    )
    third = resolve_sleep_targets(
        [_actor("third", 1)], pool=1, source_actor_id="caster", source_spell_id=SLEEP_SPELL_ID
    )
    assert second["targets"][0]["effect_id"] != third["targets"][0]["effect_id"]
    same_name = deepcopy(second["sheets"]["second"])
    same_name["effects"].append(
        {
            "id": "homebrew-sleep",
            "name": "Sleep",
            "kind": "timed_conditions",
            "source": "homebrew",
            "source_spell_id": "homebrew.sleep",
            "active": True,
            "concentration": False,
            "duration": {"period": "round", "remaining": 1},
            "changes": [{"path": "conditions", "mode": "add", "value": "unconscious"}],
            "description": "homebrew",
        }
    )
    assert wake_sleep_effects(same_name, reason="damaged")["ended_effect_ids"] == [
        second["targets"][0]["effect_id"]
    ]


@pytest.mark.parametrize("bad_pool", [-1, True, 1.5])
def test_sleep_rejects_invalid_pool_without_target_mutation(bad_pool: object) -> None:
    target = _actor("target", 1)
    before = deepcopy(target)
    with pytest.raises(ValueError, match="pool"):
        resolve_sleep_targets(
            [target], pool=bad_pool, source_actor_id="caster", source_spell_id=SLEEP_SPELL_ID
        )
    assert target == before


def test_sleep_rejects_2024_ruleset_and_preserves_concentration_on_affected_copy() -> None:
    target = _actor("target", 1)
    concentration = {
        "id": "concentration",
        "name": "Bless",
        "kind": "timed_conditions",
        "source": "caster",
        "source_spell_id": "bless",
        "active": True,
        "concentration": True,
        "duration": {"period": "round", "remaining": 1},
        "changes": [],
        "description": "concentration",
    }
    target["sheet"]["effects"].append(concentration)
    before = deepcopy(target)
    with pytest.raises(ValueError, match="2014"):
        resolve_sleep_targets(
            [target],
            pool=1,
            source_actor_id="caster",
            source_spell_id=SLEEP_SPELL_ID,
            ruleset="2024",
        )
    assert target == before
    settled = resolve_sleep_targets(
        [target], pool=1, source_actor_id="caster", source_spell_id=SLEEP_SPELL_ID
    )
    assert settled["sheets"]["target"]["effects"][0]["active"] is False
