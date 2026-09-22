import random
from copy import deepcopy
from types import SimpleNamespace

import pytest
from sagasmith_dnd.character_schema import default_character_sheet, derive_character_sheet
from sagasmith_dnd.combat_engine import roll_attack_action, start_encounter
from sagasmith_dnd.resolution_plan import (
    ResolutionPlanExecutionError,
    bind_resolution_plan,
    execute_resolution_plan,
)
from sagasmith_dnd.spatial import compile_battle_map
from sagasmith_dnd_runtime import application_support
from sagasmith_dnd_runtime.services.semantic_execution import CombatPlanContext, CombatPlanRuntime
from sagasmith_dnd_runtime.services.spells import SpellsService


@pytest.mark.parametrize("fail_after_hit", [False, True])
def test_attack_creates_concentration_window_and_rolls_it_back_atomically(
    monkeypatch, fail_after_hit,
):
    actors = {}
    for index, name in enumerate(("attacker", "target")):
        sheet = default_character_sheet()
        sheet["combat"]["hp"].update(value=100, max=100)
        sheet["abilities"]["strength"]["score"] = 16
        if name == "target":
            sheet["effects"] = [{"id": "focus", "name": "Focus", "kind": "concentration",
                                 "active": True, "concentration": True,
                                 "duration": {"period": "hour", "remaining": 1}, "changes": []}]
        actors[name] = {"id": name, "name": name, "sheet": sheet,
                        "derived": derive_character_sheet(sheet),
                        "position": {"x": index, "y": 0}, "tie_breaker": index}
    encounter = start_encounter(list(actors.values()), positioning_mode="grid",
        battle_map=compile_battle_map({"scene_id": "audit", "spatial": {}}, {}),
        rng=random.Random(1))
    encounter["turn_index"] = next(index for index, item in enumerate(encounter["combatants"])
                                   if item["actor_id"] == "attacker")
    steps = [{"id": "hit", "op": "attack.resolve", "args": {
        "source_actor_id": "attacker", "target_actor_id": "target", "attack_ref": "unarmed-strike",
    }}]
    if fail_after_hit:
        steps.insert(0, {"id": "fail", "op": "state.assert", "args": {
            "subject": 1, "expected": 2, "operator": "equals",
        }})
    plan = bind_resolution_plan({
        "schema_version": 2, "id": "audit.attack", "source_card_id": "audit.activity",
        "source_card_kind": "activity", "trigger": "action", "slots": {},
        "citations": [{"source": "fixture", "source_ref": {"chunk_id": "audit"},
                       "source_excerpt": "Make an unarmed attack against the target."}],
        "steps": steps,
    }, {})

    class Services:
        add_concentration_window = SpellsService.add_concentration_window

        def require_campaign_actor(self, campaign_id, actor_id):
            return SimpleNamespace(sheet=deepcopy(actors[actor_id]["sheet"]))

        def combat_actor_snapshot(self, actor_id):
            return deepcopy(actors[actor_id])

        def derive_character_sheet(self, sheet, character_id):
            return derive_character_sheet(sheet)

        def effective_rule_context(self, *args, **kwargs):
            return None

        def sync_combatant_conditions(self, *args):
            pass

    runtime = CombatPlanRuntime(CombatPlanContext(encounter, Services(),
        SimpleNamespace(revision=1), "audit", "main", plan, plan.compiled, {}))
    monkeypatch.setattr(application_support, "roll_attack_action",
                        lambda *, plan: roll_attack_action(plan=plan, rng=random.Random(5)))
    if fail_after_hit:
        with pytest.raises(ResolutionPlanExecutionError):
            execute_resolution_plan(plan, runtime)
        assert runtime.sheets == {}
        assert runtime.encounter == encounter
    else:
        result = execute_resolution_plan(plan, runtime)
        assert result.status == "pending_choice"
        assert runtime.sheets["target"]["combat"]["hp"]["value"] == 96
        window, = runtime.encounter["pending"]
        assert window["kind"] == "concentration"
        assert window["actor_id"] == "target"
        assert window["dc"] == 10
        assert window["id"] == "concentration:target:2"
        assert "sheet" not in result.results["hit"]["damage"]
    assert actors["target"]["sheet"]["combat"]["hp"]["value"] == 100
