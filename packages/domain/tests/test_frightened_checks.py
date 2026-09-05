from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.combat_engine import (
    NeedsRulingError,
    queue_combatant,
    resolve_actor_check,
    resolve_actor_contest,
    resolve_actor_group_check,
    start_encounter,
)
from sagasmith_dnd.rule_engine import resolution_context


class CountingRng:
    def __init__(self) -> None:
        self.calls = 0

    def randint(self, lower: int, upper: int) -> int:
        self.calls += 1
        return 16 if self.calls == 1 else 4


def frightened_actor() -> dict:
    sheet = default_character_sheet()
    sheet["edition"] = "2014"
    sheet["conditions"] = ["frightened"]
    sheet["effects"] = [{
        "id": "fear-effect",
        "name": "Fear",
        "kind": "timed_conditions",
        "source": "fear-source",
        "active": True,
        "duration": {"period": "source_turn_start", "remaining": 1},
        "changes": [{"path": "conditions", "mode": "add", "value": "frightened"}],
    }]
    return {"id": "hero", "sheet": sheet, "derived": derive_character_sheet(sheet)}


def encounter_with_source(**source_state: object) -> dict:
    return {"combatants": [
        {"actor_id": "hero", "conditions": ["frightened"]},
        {"actor_id": "fear-source", "conditions": [], **source_state},
    ]}


@pytest.mark.parametrize("kind", ["ability", "check"])
@pytest.mark.parametrize("ability", ["strength", "perception", "stealth", "medicine"])
def test_visible_fear_disadvantages_every_ability_check(kind: str, ability: str) -> None:
    actor = frightened_actor()
    before = deepcopy(actor)
    rng = CountingRng()
    result = resolve_actor_check(
        actor, kind=kind, ability=ability, dc=10,
        encounter=encounter_with_source(), rng=rng,
    )
    assert result["natural"] == 4
    assert rng.calls == 2
    assert actor == before


@pytest.mark.parametrize("source_state", [
    {"hidden": True}, {"conditions": ["invisible"]}, {"visible_to_actor_ids": []},
])
def test_unseen_fear_does_not_disadvantage_checks(source_state: dict) -> None:
    rng = CountingRng()
    result = resolve_actor_check(
        frightened_actor(), kind="ability", ability="wisdom", dc=10,
        encounter=encounter_with_source(**source_state), rng=rng,
    )
    assert result["natural"] == 16
    assert rng.calls == 1


@pytest.mark.parametrize("missing", ["encounter", "source", "effect", "effect-source"])
def test_unknown_fear_source_pauses_without_rolling(missing: str) -> None:
    actor = frightened_actor()
    encounter = encounter_with_source()
    if missing == "encounter":
        encounter = None
    elif missing == "source":
        encounter["combatants"].pop()
    elif missing == "effect":
        actor["sheet"]["effects"] = []
    else:
        actor["sheet"]["effects"][0]["source"] = ""
    before = deepcopy(actor)
    rng = CountingRng()
    with pytest.raises(NeedsRulingError):
        resolve_actor_check(
            actor, kind="check", ability="dexterity", dc=10,
            encounter=encounter, rng=rng,
        )
    assert rng.calls == 0
    assert actor == before


def test_fear_and_advantage_cancel_normally() -> None:
    rng = CountingRng()
    result = resolve_actor_check(
        frightened_actor(), kind="ability", ability="wisdom", dc=10,
        encounter=encounter_with_source(), advantage=True, rng=rng,
    )
    assert result["natural"] == 16
    assert rng.calls == 1


def test_fear_does_not_change_saves_or_require_the_fear_source_for_them() -> None:
    rng = CountingRng()
    result = resolve_actor_check(
        frightened_actor(), kind="save", ability="wisdom", dc=10, rng=rng,
    )
    assert result["natural"] == 16
    assert rng.calls == 1


def test_blinded_actor_cannot_see_its_recorded_fear_source() -> None:
    actor = frightened_actor()
    actor["sheet"]["conditions"].append("blinded")
    rng = CountingRng()
    result = resolve_actor_check(
        actor, kind="ability", ability="strength", dc=10,
        encounter=encounter_with_source(), rng=rng,
    )
    assert result["natural"] == 16
    assert rng.calls == 1


def test_one_visible_fear_source_is_enough_but_does_not_stack_dice() -> None:
    actor = frightened_actor()
    second = deepcopy(actor["sheet"]["effects"][0])
    second.update(id="second-fear", source="second-source")
    actor["sheet"]["effects"].append(second)
    encounter = encounter_with_source(hidden=True)
    encounter["combatants"].append({"actor_id": "second-source", "conditions": []})
    rng = CountingRng()
    result = resolve_actor_check(
        actor, kind="check", ability="wisdom", dc=10, encounter=encounter, rng=rng,
    )
    assert result["natural"] == 4
    assert rng.calls == 2


@pytest.mark.parametrize(
    "incomplete", ["inactive-effect", "blank-extra-source", "duplicate-source"]
)
def test_incomplete_fear_ownership_or_visibility_does_not_silently_roll(incomplete: str) -> None:
    actor = frightened_actor()
    encounter = encounter_with_source()
    if incomplete == "inactive-effect":
        actor["sheet"]["effects"][0]["active"] = False
    elif incomplete == "blank-extra-source":
        second = deepcopy(actor["sheet"]["effects"][0])
        second.update(id="unknown-fear", source="")
        actor["sheet"]["effects"].append(second)
    else:
        encounter["combatants"].append(deepcopy(encounter["combatants"][-1]))
    rng = CountingRng()
    with pytest.raises(NeedsRulingError):
        resolve_actor_check(
            actor, kind="check", ability="wisdom", dc=10, encounter=encounter, rng=rng,
        )
    assert rng.calls == 0


def test_removed_fear_condition_does_not_leave_a_check_penalty() -> None:
    actor = frightened_actor()
    actor["sheet"]["conditions"] = []
    actor["sheet"]["effects"] = []
    rng = CountingRng()
    result = resolve_actor_check(actor, kind="check", ability="wisdom", dc=10, rng=rng)
    assert result["natural"] == 16
    assert rng.calls == 1


def unafraid_actor() -> dict:
    actor = frightened_actor()
    actor["id"] = "unafraid"
    actor["sheet"]["conditions"] = []
    actor["sheet"]["effects"] = []
    return actor


@pytest.mark.parametrize("operation", ["group", "contest"])
def test_later_unresolved_fear_pauses_before_any_participant_rolls(operation: str) -> None:
    actors = [unafraid_actor(), frightened_actor()]
    before = deepcopy(actors)
    rng = CountingRng()
    with pytest.raises(NeedsRulingError):
        if operation == "group":
            resolve_actor_group_check(actors, ability="wisdom", dc=10, rng=rng)
        else:
            resolve_actor_contest(
                *actors, source_ability="wisdom", target_ability="wisdom", rng=rng,
            )
    assert rng.calls == 0
    assert actors == before


@pytest.mark.parametrize("operation", ["single", "group", "contest"])
def test_2014_fear_preflight_does_not_change_existing_2024_checks(operation: str) -> None:
    actor = frightened_actor()
    actor["sheet"]["edition"] = "2024"
    rng = CountingRng()
    if operation == "single":
        resolve_actor_check(actor, kind="ability", ability="wisdom", dc=10, rng=rng)
    elif operation == "group":
        resolve_actor_group_check([unafraid_actor(), actor], ability="wisdom", dc=10, rng=rng)
    else:
        resolve_actor_contest(
            unafraid_actor(), actor, source_ability="wisdom", target_ability="wisdom", rng=rng,
        )
    assert rng.calls == (1 if operation == "single" else 2)


@pytest.mark.parametrize("hidden", [False, True])
def test_frightened_check_emits_2014_core_receipt_for_known_visibility(hidden: bool) -> None:
    rules = resolution_context({"edition": "2014", "fingerprint": "", "lock": []})
    result = resolve_actor_check(
        frightened_actor(), kind="ability", ability="wisdom", dc=10,
        encounter=encounter_with_source(hidden=hidden), rules=rules, rng=CountingRng(),
    )
    receipts = [receipt for receipt in result["rule_receipts"]
                if receipt["mechanic_id"] == "dnd5e.core.check.frightened"]
    assert len(receipts) == 1
    assert receipts[0]["core_pack_fingerprint"] == rules.core_pack.fingerprint


@pytest.mark.parametrize("second_hidden", [False, True])
@pytest.mark.parametrize("source_advantage", [False, True])
def test_contest_checks_each_actors_own_fear_sources(
    second_hidden: bool, source_advantage: bool
) -> None:
    source = frightened_actor()
    target = frightened_actor()
    target["id"] = "target"
    target["sheet"]["effects"][0]["source"] = "second-source"
    encounter = encounter_with_source()
    encounter["combatants"].extend([
        {"actor_id": "target", "conditions": ["frightened"]},
        {"actor_id": "second-source", "conditions": [], "hidden": second_hidden},
    ])
    before = deepcopy((source, target, encounter))
    rng = CountingRng()
    result = resolve_actor_contest(
        source, target, source_ability="wisdom", target_ability="wisdom",
        source_advantage=source_advantage, encounter=encounter, rng=rng,
    )
    assert len(result["source_check"]["rolls"]) == (1 if source_advantage else 2)
    assert len(result["target_check"]["rolls"]) == (1 if second_hidden else 2)
    assert rng.calls == (1 if source_advantage else 2) + (1 if second_hidden else 2)
    assert (source, target, encounter) == before


@pytest.mark.parametrize("joining", [False, True])
@pytest.mark.parametrize("hidden", [False, True])
@pytest.mark.parametrize("advantage", [False, True])
def test_initiative_is_a_frightened_dexterity_check(
    joining: bool, hidden: bool, advantage: bool
) -> None:
    actor = frightened_actor()
    actor.update(tie_breaker=1, initiative_advantage=advantage)
    source = unafraid_actor()
    source.update(id="fear-source", initiative=25, tie_breaker=2, hidden=hidden)
    rng = CountingRng()
    if joining:
        before = start_encounter([source])
        original = deepcopy(before)
        after = queue_combatant(before, actor, rng=rng)
        combatant = after["reinforcements"][0]
        assert before == original
    else:
        # Fear source is intentionally after the actor whose initiative is rolled.
        after = start_encounter([actor, source], rng=rng)
        combatant = next(item for item in after["combatants"] if item["actor_id"] == "hero")
    disadvantage = not hidden
    expected_dice = 2 if advantage != disadvantage else 1
    assert rng.calls == expected_dice
    assert len(combatant["initiative_roll"]["rolls"]) == expected_dice
    assert combatant["initiative_roll"]["natural"] == (4 if disadvantage and not advantage else 16)
    assert "dnd5e.core.initiative.frightened" in after["rule_boundary_ids"]


def test_missing_fear_source_preflights_before_the_first_initiative_roll() -> None:
    actors = [unafraid_actor(), frightened_actor()]
    before = deepcopy(actors)
    rng = CountingRng()
    with pytest.raises(NeedsRulingError):
        start_encounter(actors, rng=rng)
    assert rng.calls == 0
    assert actors == before


def test_missing_fear_source_on_join_does_not_change_the_encounter_or_rng() -> None:
    encounter = start_encounter([unafraid_actor()])
    before = deepcopy(encounter)
    rng = CountingRng()
    with pytest.raises(NeedsRulingError):
        queue_combatant(encounter, frightened_actor(), rng=rng)
    assert encounter == before
    assert rng.calls == 0


def test_frightened_initiative_does_not_reinterpret_explicit_rolls_or_2024() -> None:
    actor = frightened_actor()
    actor["initiative"] = 10
    rng = CountingRng()
    start_encounter([actor], rng=rng)
    assert rng.calls == 0
    actor.pop("initiative")
    actor["sheet"]["edition"] = "2024"
    after = start_encounter([actor], ruleset="2024", rng=rng)
    assert rng.calls == 1
    assert "dnd5e.core.initiative.frightened" not in after["rule_boundary_ids"]


def test_a_queued_source_does_not_establish_current_initiative_line_of_sight() -> None:
    observer = unafraid_actor()
    observer["initiative"] = 10
    source = unafraid_actor()
    source.update(id="fear-source", initiative=25)
    encounter = queue_combatant(start_encounter([observer]), source)
    before = deepcopy(encounter)
    rng = CountingRng()
    with pytest.raises(NeedsRulingError):
        queue_combatant(encounter, frightened_actor(), rng=rng)
    assert encounter == before
    assert rng.calls == 0
