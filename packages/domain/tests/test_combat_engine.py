import random
from copy import deepcopy

import pytest

from sagasmith_dnd.character_schema import (
    add_inventory_item,
    default_character_sheet,
    derive_character_sheet,
    equip_inventory_item,
    validate_character_sheet,
)
from sagasmith_dnd.combat_engine import (
    CombatEngineError,
    NeedsRulingError,
    active_hypnotic_pattern_effect_ids,
    add_choice_window,
    apply_attack_ac_bonus,
    apply_concentration_result,
    apply_damage_parts_to_sheet,
    apply_damage_to_sheet,
    apply_healing_to_sheet,
    apply_hit_point_loss_to_sheet,
    apply_weapon_mastery_to_encounter,
    arm_readied_spell,
    available_actions,
    available_attack_defenses,
    available_reactions,
    can_see,
    consume_weapon_mastery_attack_effects,
    current_combatant,
    damage_amount_after_reduction,
    dodge_benefit_active,
    encounter_dodge_save_advantage,
    end_concentration_for_incapacitating_conditions,
    end_turn,
    force_move_directly_away,
    force_move_directly_toward,
    pay_activity_activation,
    pay_attack_action,
    pay_legendary_action,
    pay_multiattack_activity,
    preflight_attack,
    preflight_spell_attack,
    queue_combatant,
    reconcile_dodge_lifecycle,
    reconcile_effect_dependencies,
    resolve_actor_check,
    resolve_actor_contest,
    resolve_actor_group_check,
    resolve_attack_action,
    resolve_attack_damage,
    resolve_choice_window,
    resolve_common_action,
    resolve_death_save_to_sheet,
    resolve_divine_spark_to_sheet,
    resolve_hypnotic_pattern_target,
    resolve_preserve_life_to_sheets,
    resolve_readied_spell_window,
    resolve_save_damage_to_sheet,
    resolve_save_damage_to_sheets,
    resolve_second_wind_to_sheet,
    resolve_turn_undead_to_sheets,
    roll_attack_action,
    settle_core_activity_effect,
    source_speed_multiplier,
    spend_movement,
    stabilize_sheet,
    stand_up,
    standard_save_damage_reduction,
    start_encounter,
    trigger_readied_spell,
)
from sagasmith_dnd.content_solution import build_content_solution
from sagasmith_dnd.engine import resolve_check, roll_d20
from sagasmith_dnd.lifecycle import apply_rest
from sagasmith_dnd.resolution_plan import (
    compile_resolution_plan,
    resolution_plan_template,
)
from sagasmith_dnd.rule_engine import resolution_context
from sagasmith_dnd.spatial import compile_battle_map
from sagasmith_dnd.standard_feature_ids import (
    CORE_ORC_AGGRESSIVE_MECHANIC_ID,
    CORE_RELENTLESS_ENDURANCE_MECHANIC_ID,
    ORC_AGGRESSIVE_ACTIVITY_ID,
)
from sagasmith_dnd.standard_spell_ids import (
    CORE_2024_HYPNOTIC_PATTERN_SPELL_ID,
    CORE_HYPNOTIC_PATTERN_SPELL_ID,
)


def test_damage_reduction_uses_one_round_down_contract() -> None:
    assert damage_amount_after_reduction(7, "full") == 7
    assert damage_amount_after_reduction(7, "half") == 3
    assert damage_amount_after_reduction(7, "none") == 0
    with pytest.raises(CombatEngineError, match="full, half, or none"):
        damage_amount_after_reduction(7, "quarter")


def test_generic_save_damage_rolls_and_applies_half_damage_atomically() -> None:
    target = _actor("target", hp=20)
    target["sheet"]["abilities"]["dexterity"]["score"] = 20
    target["derived"] = derive_character_sheet(target["sheet"])

    settled = resolve_save_damage_to_sheet(
        target,
        save_ability="dexterity",
        save_dc=10,
        damage_expression="3d6",
        damage_type="fire",
        half_on_success=True,
        source="agent-ruling:test-save-damage",
        rng=_SequenceRng(1, 2, 4, 10),
    )

    assert settled["result"]["save"]["success"] is True
    assert settled["result"]["damage_roll"]["total"] == 7
    assert settled["result"]["damage_reduction"] == "half"
    assert settled["result"]["damage_amount"] == 3
    assert settled["sheet"]["combat"]["hp"]["value"] == 17


def test_generic_save_damage_shares_one_roll_across_targets() -> None:
    agile = _actor("agile", hp=20)
    clumsy = _actor("clumsy", hp=20)
    agile["sheet"]["abilities"]["dexterity"]["score"] = 20
    clumsy["sheet"]["abilities"]["dexterity"]["score"] = 1
    agile["derived"] = derive_character_sheet(agile["sheet"])
    clumsy["derived"] = derive_character_sheet(clumsy["sheet"])

    settled = resolve_save_damage_to_sheets(
        [agile, clumsy],
        save_ability="dexterity",
        save_dc=10,
        damage_expression="2d6",
        damage_type="fire",
        half_on_success=True,
        source="agent-ruling:test-shared-save-damage",
        rng=_SequenceRng(3, 4, 20, 1),
    )

    assert settled["result"]["damage_roll"]["total"] == 7
    assert [item["success"] for item in settled["result"]["targets"]] == [
        True,
        False,
    ]
    assert [item["damage_amount"] for item in settled["result"]["targets"]] == [
        3,
        7,
    ]
    assert settled["sheets"]["agile"]["combat"]["hp"]["value"] == 17
    assert settled["sheets"]["clumsy"]["combat"]["hp"]["value"] == 13


def test_generic_save_damage_applies_audited_per_target_save_bonuses() -> None:
    covered = _actor("covered", hp=20)
    open_target = _actor("open", hp=20)

    settled = resolve_save_damage_to_sheets(
        [covered, open_target],
        save_ability="dexterity",
        save_dc=10,
        damage_expression="2d6",
        damage_type="fire",
        half_on_success=True,
        source="breath:line",
        save_bonuses_by_actor_id={"covered": 5, "open": 0},
        rng=_SequenceRng(3, 4, 5, 9),
    )

    assert [item["save_bonus"] for item in settled["result"]["targets"]] == [
        5,
        0,
    ]
    assert [item["success"] for item in settled["result"]["targets"]] == [
        True,
        False,
    ]


def test_concentration_save_does_not_apply_magic_resistance() -> None:
    actor = _actor("archmage")
    actor["sheet"]["content"]["features"].append(
        {
            "id": "magic-resistance-passive",
            "name": "Magic Resistance",
            "choices": {
                "source_trait": {
                    "kind": "magic_resistance",
                    "trigger": "saving_throw",
                    "save_source_kinds": ["spell", "magical_effect"],
                    "grants": "advantage",
                    "automatic": True,
                    "source_excerpt": (
                        "The archmage has advantage on saving throws against "
                        "spells and other magical effects."
                    ),
                }
            },
        }
    )
    actor["derived"] = derive_character_sheet(actor["sheet"])
    effective = {
        "edition": "2014",
        "fingerprint": "",
        "lock": [],
        "mechanics": [],
    }

    result = resolve_actor_check(
        actor,
        kind="save",
        ability="constitution",
        dc=10,
        rules=resolution_context(
            effective,
            facts={"save_purpose": "concentration"},
        ),
        rng=_SequenceRng(7),
    )

    assert result["rolls"] == [7]
    assert result["roll_mode"] == "normal"
    assert result["rule_receipts"] == []


def test_evasion_rewrites_dexterity_save_for_half_damage() -> None:
    trait = {
        "kind": "evasion",
        "trigger": "dexterity_save_for_half_damage",
        "save_ability": "dexterity",
        "ordinary_successful_save": "half",
        "successful_save": "none",
        "failed_save": "half",
        "automatic": True,
        "source_excerpt": (
            "If the assassin is subjected to an effect that allows it to make "
            "a Dexterity saving throw to take only half damage, the assassin "
            "instead takes no damage if it succeeds on the saving throw, and "
            "only half damage if it fails."
        ),
    }
    agile = _actor("agile-assassin", hp=20)
    agile["sheet"]["abilities"]["dexterity"]["score"] = 20
    agile["sheet"]["content"]["features"].append(
        {
            "id": "evasion-passive",
            "name": "Evasion",
            "choices": {"source_trait": trait},
            "mechanic_refs": ["dnd5e.core.save.evasion"],
        }
    )
    agile["derived"] = derive_character_sheet(agile["sheet"])
    clumsy = deepcopy(agile)
    clumsy["id"] = "clumsy-assassin"
    clumsy["sheet"]["abilities"]["dexterity"]["score"] = 1
    clumsy["derived"] = derive_character_sheet(clumsy["sheet"])
    rules = resolution_context({"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []})

    settled = resolve_save_damage_to_sheets(
        [agile, clumsy],
        save_ability="dexterity",
        save_dc=10,
        damage_expression="2d6",
        damage_type="fire",
        half_on_success=True,
        source="spell:fireball",
        rules=rules,
        rng=_SequenceRng(3, 4, 10, 1),
    )

    assert [item["damage_reduction"] for item in settled["result"]["targets"]] == ["none", "half"]
    assert [item["damage_amount"] for item in settled["result"]["targets"]] == [0, 3]
    assert settled["sheets"]["agile-assassin"]["combat"]["hp"]["value"] == 20
    assert settled["sheets"]["clumsy-assassin"]["combat"]["hp"]["value"] == 17
    assert all(
        [receipt["mechanic_id"] for receipt in item["rule_receipts"]] == ["dnd5e.core.save.evasion"]
        for item in settled["result"]["targets"]
    )

    incapacitated = deepcopy(agile)
    incapacitated["sheet"]["conditions"] = ["incapacitated"]
    incapacitated["sheet"]["content"]["features"][0]["choices"]["source_trait"][
        "unavailable_conditions"
    ] = ["incapacitated"]
    denied = standard_save_damage_reduction(
        incapacitated,
        ability="dexterity",
        success=True,
        ordinary_successful_save="half",
    )
    assert denied["damage_reduction"] == "half"
    assert denied["rule_receipts"] == []

    counterfeit = deepcopy(agile)
    counterfeit["sheet"]["content"]["features"][0].pop("mechanic_refs")
    ignored = standard_save_damage_reduction(
        counterfeit,
        ability="dexterity",
        success=True,
        ordinary_successful_save="half",
    )
    assert ignored["damage_reduction"] == "half"
    assert ignored["rule_receipts"] == []


def test_generic_save_damage_rejects_conflicting_roll_states() -> None:
    with pytest.raises(
        CombatEngineError,
        match="save damage requires",
    ):
        resolve_save_damage_to_sheet(
            _actor("target", hp=20),
            save_ability="dexterity",
            save_dc=10,
            damage_expression="2d6",
            damage_type="fire",
            half_on_success=True,
            source="agent-ruling:test-invalid-save-damage",
            advantage=True,
            disadvantage=True,
        )


class _SequenceRng:
    def __init__(self, *values: int) -> None:
        self.values = list(values)

    def randint(self, minimum: int, maximum: int) -> int:
        value = self.values.pop(0)
        assert minimum <= value <= maximum
        return value


def _actor(identifier: str, *, hp: int = 12, ac: int = 10) -> dict:
    sheet = default_character_sheet()
    sheet["combat"]["hp"] = {"value": hp, "max": hp, "temp": 0}
    sheet["combat"]["ac"] = {"base": ac, "override": None}
    sheet["abilities"]["strength"]["score"] = 16
    return {
        "id": identifier,
        "name": identifier,
        "sheet": sheet,
        "derived": derive_character_sheet(sheet),
    }


def test_zero_walk_speed_is_preserved_when_a_combat_turn_starts() -> None:
    first = _actor("first")
    stopped = _actor("stopped")
    first["initiative"] = 20
    stopped["initiative"] = 10
    stopped["sheet"]["combat"]["speed"]["walk"] = 0
    stopped["derived"] = derive_character_sheet(stopped["sheet"])

    encounter = start_encounter([first, stopped])
    stopped_state = next(
        item for item in encounter["combatants"] if item["actor_id"] == "stopped"
    )
    assert stopped_state["base_speed"] == 0
    assert stopped_state["turn_budget"]["movement"] == 0

    stopped_turn = end_turn(encounter, actor_id_value="first")
    assert current_combatant(stopped_turn)["actor_id"] == "stopped"
    assert current_combatant(stopped_turn)["turn_budget"]["speed"] == 0
    assert current_combatant(stopped_turn)["turn_budget"]["movement"] == 0


def test_zero_speed_multiplier_is_preserved_when_a_combat_turn_starts() -> None:
    first = _actor("first")
    immobilized = _actor("immobilized")
    first["initiative"] = 20
    immobilized["initiative"] = 10
    immobilized["sheet"]["effects"] = [
        {
            "id": "immobilized-by-effect",
            "active": True,
            "changes": [
                {
                    "path": "combat.speed.multiplier",
                    "mode": "multiply",
                    "value": 0,
                }
            ],
        }
    ]

    encounter = start_encounter([first, immobilized])
    immobilized_state = next(
        item for item in encounter["combatants"] if item["actor_id"] == "immobilized"
    )
    assert immobilized_state["speed_multiplier"] == 0.0
    assert immobilized_state["turn_budget"]["movement"] == 0

    immobilized_turn = end_turn(encounter, actor_id_value="first")
    assert current_combatant(immobilized_turn)["actor_id"] == "immobilized"
    assert current_combatant(immobilized_turn)["turn_budget"]["movement"] == 0


def test_incapacitated_actor_retains_movement_and_free_object_interaction() -> None:
    actor = _actor("incapacitated")
    actor["sheet"]["conditions"] = ["incapacitated"]
    actor.update(position={"x": 0, "y": 0})

    encounter = _grid_encounter([actor])

    assert available_actions(encounter, "incapacitated") == ["move", "interact_object"]
    pending_reaction = add_choice_window(
        encounter,
        kind="reaction",
        actor_id_value="incapacitated",
        event="movement.leave_reach",
        candidates=[{"id": "skip"}],
    )
    assert available_reactions(pending_reaction, "incapacitated") == []
    moved = spend_movement(
        encounter,
        "incapacitated",
        5,
        destination={"x": 1, "y": 0},
    )
    assert current_combatant(moved)["turn_budget"]["movement"] == 25


def test_mid_turn_zero_speed_multiplier_blocks_projection_and_movement() -> None:
    actor = _actor("incapacitated")
    actor["sheet"]["conditions"] = ["incapacitated"]
    actor.update(position={"x": 0, "y": 0})
    encounter = _grid_encounter([actor])
    current = current_combatant(encounter)
    assert current is not None
    assert current["turn_budget"]["movement"] == 30

    current["speed_multiplier"] = 0.0

    assert current["turn_budget"]["movement"] == 30
    assert available_actions(encounter, "incapacitated") == ["interact_object"]
    with pytest.raises(CombatEngineError, match="effective speed is zero"):
        spend_movement(
            encounter,
            "incapacitated",
            5,
            destination={"x": 1, "y": 0},
        )
    assert current["turn_budget"]["movement"] == 30


def test_zero_effective_speed_does_not_block_forced_movement_or_teleportation() -> None:
    actor = _actor("immobilized")
    actor.update(position={"x": 0, "y": 0})
    encounter = _grid_encounter([actor])
    current = current_combatant(encounter)
    assert current is not None
    current["speed_multiplier"] = 0.0

    forced = spend_movement(
        encounter,
        "immobilized",
        5,
        destination={"x": 1, "y": 0},
        movement_mode="forced",
    )
    teleported = spend_movement(
        forced,
        "immobilized",
        20,
        destination={"x": 5, "y": 0},
        movement_mode="teleport",
    )

    assert current_combatant(teleported)["position"] == {"x": 5.0, "y": 0.0}
    assert current_combatant(teleported)["turn_budget"]["movement"] == 30


@pytest.mark.parametrize(
    ("speed_multiplier", "condition"),
    [(0.0, None), (1.0, "grappled"), (1.0, "restrained")],
)
def test_stand_up_rejects_current_zero_speed_with_stale_movement_budget(
    speed_multiplier: float,
    condition: str | None,
) -> None:
    actor = _actor("prone")
    actor["sheet"]["conditions"] = ["prone"]
    actor["position"] = {"x": 0, "y": 0}
    encounter = _grid_encounter([actor])
    current = current_combatant(encounter)
    assert current is not None
    assert current["turn_budget"]["movement"] == 30
    current["speed_multiplier"] = speed_multiplier
    if condition is not None:
        current["conditions"].append(condition)
    before = deepcopy(encounter)

    with pytest.raises(CombatEngineError, match="effective speed is zero"):
        stand_up(encounter, "prone")

    assert encounter == before


def test_stand_up_cost_uses_current_effective_speed() -> None:
    actor = _actor("prone")
    actor["sheet"]["conditions"] = ["prone"]
    actor["position"] = {"x": 0, "y": 0}
    encounter = _grid_encounter([actor])
    current = current_combatant(encounter)
    assert current is not None
    current["speed_multiplier"] = 0.5

    stood = stand_up(encounter, "prone")

    current = current_combatant(stood)
    assert current["turn_budget"]["movement"] == 8
    assert current["turn_budget"]["movement_spent"] == 7
    assert "prone" not in current["conditions"]


@pytest.mark.parametrize(
    ("speed_multiplier", "condition", "expected_movement"),
    [
        (0.0, None, 0),
        (0.5, None, 30),
        (1.0, "grappled", 0),
        (1.0, "restrained", 0),
    ],
)
def test_dash_uses_current_effective_speed_instead_of_stale_base_speed(
    speed_multiplier: float,
    condition: str | None,
    expected_movement: int,
) -> None:
    actor = _actor("dasher")
    actor["position"] = {"x": 0, "y": 0}
    encounter = _grid_encounter([actor])
    current = current_combatant(encounter)
    assert current is not None
    assert current["turn_budget"]["movement"] == 30
    current["speed_multiplier"] = speed_multiplier
    if condition is not None:
        current["conditions"].append(condition)

    dashed = resolve_common_action(encounter, actor_id_value="dasher", action="dash")

    budget = current_combatant(dashed)["turn_budget"]
    assert budget["movement"] == expected_movement
    assert budget["main_action"] == 0


def test_mid_turn_speed_change_caps_base_movement_and_preserves_locked_dash_grant() -> None:
    actor = _actor("mover")
    actor["position"] = {"x": 0, "y": 0}
    half_speed = _grid_encounter([actor])
    half_current = current_combatant(half_speed)
    assert half_current is not None
    half_current["speed_multiplier"] = 0.5
    exhausted_half_speed = spend_movement(
        half_speed, "mover", 15, destination={"x": 3, "y": 0}
    )
    with pytest.raises(CombatEngineError, match="no movement remaining"):
        spend_movement(
            exhausted_half_speed, "mover", 5, destination={"x": 4, "y": 0}
        )

    encounter = _grid_encounter([actor])
    moved = spend_movement(encounter, "mover", 10, destination={"x": 2, "y": 0})
    current = current_combatant(moved)
    assert current is not None
    current["speed_multiplier"] = 0.5

    with pytest.raises(CombatEngineError, match="remaining speed"):
        spend_movement(moved, "mover", 10, destination={"x": 4, "y": 0})
    slowed_move = spend_movement(moved, "mover", 5, destination={"x": 3, "y": 0})
    assert current_combatant(slowed_move)["turn_budget"]["movement"] == 0

    fresh = _grid_encounter([actor])
    fresh_current = current_combatant(fresh)
    assert fresh_current is not None
    fresh_current["speed_multiplier"] = 0.5
    dashed = resolve_common_action(fresh, actor_id_value="mover", action="dash")
    dash_budget = current_combatant(dashed)["turn_budget"]
    assert dash_budget["movement"] == 30
    assert dash_budget["extra_movement_granted"] == 15

    current_combatant(dashed)["speed_multiplier"] = 0.0
    before_zero_speed_attempt = deepcopy(dashed)
    assert "move" not in available_actions(dashed, "mover")
    with pytest.raises(CombatEngineError, match="effective speed is zero"):
        spend_movement(dashed, "mover", 5, destination={"x": 1, "y": 0})
    assert dashed == before_zero_speed_attempt

    current_combatant(dashed)["speed_multiplier"] = 1.0
    restored = spend_movement(dashed, "mover", 45, destination={"x": 9, "y": 0})
    restored_budget = current_combatant(restored)["turn_budget"]
    assert restored_budget["movement"] == 0
    assert restored_budget["movement_spent"] == 45
    assert restored_budget["extra_movement_granted"] == 15


def test_legacy_movement_budget_is_inferred_without_manufacturing_dash_grants() -> None:
    actor = _actor("legacy")
    actor["position"] = {"x": 0, "y": 0}
    encounter = _grid_encounter([actor])

    started_slow = deepcopy(encounter)
    slow_current = current_combatant(started_slow)
    slow_current["speed_multiplier"] = 0.5
    slow_budget = slow_current["turn_budget"]
    slow_budget.pop("movement_spent")
    slow_budget.pop("extra_movement_granted")
    slow_budget["movement"] = 15
    before_started_slow_move = deepcopy(started_slow)
    with pytest.raises(CombatEngineError, match="no movement remaining"):
        spend_movement(started_slow, "legacy", 5, destination={"x": 1, "y": 0})
    assert started_slow == before_started_slow_move

    stale_after_spend = deepcopy(encounter)
    stale_current = current_combatant(stale_after_spend)
    stale_current["speed_multiplier"] = 0.5
    stale_budget = stale_current["turn_budget"]
    stale_budget.pop("movement_spent")
    stale_budget.pop("extra_movement_granted")
    stale_budget["movement"] = 20
    stale_moved = spend_movement(
        stale_after_spend, "legacy", 5, destination={"x": 1, "y": 0}
    )
    assert current_combatant(stale_moved)["turn_budget"]["movement"] == 0

    ambiguous_spend = deepcopy(encounter)
    ambiguous_current = current_combatant(ambiguous_spend)
    ambiguous_current["speed_multiplier"] = 0.5
    ambiguous_budget = ambiguous_current["turn_budget"]
    ambiguous_budget.pop("movement_spent")
    ambiguous_budget.pop("extra_movement_granted")
    ambiguous_budget["movement"] = 10
    before_ambiguous_move = deepcopy(ambiguous_spend)
    with pytest.raises(CombatEngineError, match="no movement remaining"):
        spend_movement(
            ambiguous_spend, "legacy", 5, destination={"x": 1, "y": 0}
        )
    assert ambiguous_spend == before_ambiguous_move

    unknown_old_dash = deepcopy(encounter)
    dash_current = current_combatant(unknown_old_dash)
    dash_current["speed_multiplier"] = 0.5
    dash_budget = dash_current["turn_budget"]
    dash_budget.pop("movement_spent")
    dash_budget.pop("extra_movement_granted")
    dash_budget["movement"] = 45
    capped = spend_movement(
        unknown_old_dash, "legacy", 15, destination={"x": 3, "y": 0}
    )
    capped_budget = current_combatant(capped)["turn_budget"]
    assert capped_budget["movement"] == 0
    assert capped_budget["extra_movement_granted"] == 0


@pytest.mark.parametrize("movement_block", ["speed_zero", "spent", "grappled", "restrained"])
def test_incapacitated_actor_with_no_legal_movement_does_not_offer_move(
    movement_block: str,
) -> None:
    actor = _actor("incapacitated")
    actor["sheet"]["conditions"] = ["incapacitated"]
    if movement_block in {"grappled", "restrained"}:
        actor["sheet"]["conditions"].append(movement_block)
    if movement_block == "speed_zero":
        actor["sheet"]["combat"]["speed"]["walk"] = 0
        actor["derived"] = derive_character_sheet(actor["sheet"])
    encounter = start_encounter([actor], ruleset="2014")
    if movement_block == "spent":
        current = current_combatant(encounter)
        assert current is not None
        current["turn_budget"]["movement"] = 0
        current["turn_budget"]["movement_spent"] = 30

    assert available_actions(encounter, "incapacitated") == ["interact_object"]


@pytest.mark.parametrize("condition", ["dead", "unconscious", "stunned", "paralyzed", "petrified"])
def test_derived_incapacitating_states_still_offer_no_actions(condition: str) -> None:
    actor = _actor(condition)
    actor["sheet"]["conditions"] = [condition]

    encounter = start_encounter([actor], ruleset="2014")

    assert available_actions(encounter, condition) == []


def test_nonproficient_armor_and_heavy_encumbrance_apply_check_disadvantage() -> None:
    actor = _actor("encumbered")
    actor["sheet"]["abilities"]["strength"]["score"] = 10
    actor["sheet"]["inventory"]["encumbrance"]["mode"] = "variant"
    actor["sheet"], armor_id = add_inventory_item(
        actor["sheet"],
        {
            "id": "chain-mail",
            "name": "Chain mail",
            "kind": "armor",
            "weight_oz": 1800,
            "mechanics": {
                "base_ac": 16,
                "category": "heavy",
                "dexterity_mode": "none",
                "strength_requirement": 13,
            },
        },
    )
    actor["sheet"] = equip_inventory_item(actor["sheet"], armor_id, "armor")
    actor["derived"] = derive_character_sheet(actor["sheet"])

    strength_check = resolve_actor_check(
        actor,
        kind="ability",
        ability="strength",
        dc=10,
        rng=_SequenceRng(18, 2),
    )
    constitution_save = resolve_actor_check(
        actor,
        kind="save",
        ability="constitution",
        dc=10,
        rng=_SequenceRng(17, 3),
    )

    assert strength_check["equipment_disadvantage"] is True
    assert strength_check["rolls"] == [18, 2]
    assert strength_check["natural"] == 2
    assert constitution_save["equipment_disadvantage"] is True
    assert constitution_save["rolls"] == [17, 3]
    assert constitution_save["natural"] == 3


def _grid_encounter(
    participants: list[dict],
    *,
    ruleset: str = "2014",
) -> dict:
    positions = [dict(actor["position"]) for actor in participants]
    ordered_participants = [
        {**actor, "tie_breaker": actor.get("tie_breaker", index)}
        for index, actor in enumerate(participants)
    ]
    battle_map = compile_battle_map(
        {"scene_id": "test-grid", "spatial": {}},
        {
            "width_cells": max(int(item["x"]) for item in positions) + 10,
            "height_cells": max(int(item["y"]) for item in positions) + 10,
        },
    )
    return start_encounter(
        ordered_participants,
        ruleset=ruleset,
        battle_map=battle_map,
        positioning_mode="grid",
    )


def test_encounter_positioning_modes_are_explicit_engine_state() -> None:
    agent = start_encounter([_actor("agent")], positioning_mode="agent")
    assert agent["positioning_mode"] == "agent"
    assert agent["battle_map"] is None
    assert agent["combatants"][0]["position"] is None

    positioned = _actor("grid")
    positioned["position"] = {"x": 1, "y": 1}
    grid = _grid_encounter([positioned])
    assert grid["positioning_mode"] == "grid"
    assert grid["combatants"][0]["position"] == {"x": 1, "y": 1}

    with pytest.raises(CombatEngineError, match="requires a battle map"):
        start_encounter([positioned], positioning_mode="grid")
    with pytest.raises(CombatEngineError, match="does not accept a battle map"):
        start_encounter(
            [_actor("agent-map")],
            positioning_mode="agent",
            battle_map=grid["battle_map"],
        )


def test_agent_positioned_attack_requires_and_consumes_structured_spatial_facts() -> None:
    attacker = _actor("attacker")
    target = _actor("target")
    attacker["initiative"] = 20
    target["initiative"] = 10
    encounter = start_encounter(
        [attacker, target],
        positioning_mode="agent",
    )

    with pytest.raises(NeedsRulingError, match="structured spatial facts"):
        preflight_attack(
            attacker,
            target,
            action={"weapon_id": "unarmed-strike"},
            encounter=encounter,
        )

    spatial_facts = {
        "decision_id": "spatial:test-attack",
        "reason": "The target is beside the attacker with no intervening obstacle.",
        "targetable": True,
        "in_range": True,
        "long_range": False,
        "cover_degree": "none",
        "attacker_can_see_target": True,
        "target_can_see_attacker": True,
        "target_within_5_ft": True,
        "close_threat_actor_ids": [],
        "helper_actor_ids": [],
        "target_adjacent_ally_actor_ids": [],
    }
    plan = preflight_attack(
        attacker,
        target,
        action={
            "weapon_id": "unarmed-strike",
            "context": {"spatial_facts": spatial_facts},
        },
        encounter=encounter,
    )

    assert plan["status"] == "ready"
    assert plan["range"]["source"] == "agent_spatial_facts"
    assert plan["spatial_ruling"]["decision_id"] == "spatial:test-attack"


def test_agent_positioned_movement_consumes_distance_and_opportunity_facts() -> None:
    mover = _actor("mover")
    threat = _actor("threat")
    mover.update({"initiative": 20, "disposition": "friendly"})
    threat.update({"initiative": 10, "disposition": "hostile"})
    encounter = start_encounter([mover, threat], positioning_mode="agent")

    with pytest.raises(NeedsRulingError, match="structured movement decision"):
        spend_movement(encounter, "mover", 10)

    facts = {
        "decision_id": "spatial:test-move",
        "reason": "The mover crosses open ground and leaves the threat's reach.",
        "destination_legal": True,
        "distance_ft": 10,
        "difficult_terrain_extra_ft": 5,
        "moves_farther_from_turn_source": True,
        "enters_turn_source_30_ft": False,
        "moves_closer_to_visible_fear_source": False,
        "opportunity_attack_actor_ids": ["threat"],
    }
    moved = spend_movement(encounter, "mover", 10, spatial_facts=facts)
    current = current_combatant(moved)

    assert current["turn_budget"]["movement"] == 15
    assert moved["pending"][0]["actor_id"] == "threat"
    assert moved["pending"][0]["target_position"] is None
    assert moved["log"][-1]["decision"]["decision_id"] == "spatial:test-move"


def test_preflight_rejects_an_exhausted_recharge_weapon() -> None:
    attacker = _actor("recharge-attacker")
    attacker["sheet"]["inventory"]["items"] = [
        {
            "id": "web-recharge-5-6",
            "name": "Web (Recharge 5-6)",
            "kind": "weapon",
            "mechanics": {
                "attack_type": "ranged",
                "attack_ability": "dexterity",
                "damage_formula": "",
                "damage_type": "",
                "attack_bonus_override": 5,
                "always_available": True,
                "recharge": {
                    "kind": "d6_turn_start",
                    "minimum": 5,
                    "maximum": 6,
                    "source_marker": "(Recharge 5-6)",
                },
            },
            "uses": {
                "label": "Web (Recharge 5-6)",
                "value": 0,
                "max": 1,
                "recovers_on": "manual",
                "source_key": "test:web",
            },
        }
    ]
    attacker["sheet"] = validate_character_sheet(attacker["sheet"])
    attacker["derived"] = derive_character_sheet(attacker["sheet"])

    with pytest.raises(CombatEngineError, match="waiting for its Recharge roll"):
        preflight_attack(
            attacker,
            _actor("target"),
            action={"weapon_id": "web-recharge-5-6"},
        )


def _mastery_actor(identifier: str, mastery: str) -> dict:
    actor = _actor(identifier, hp=30)
    sheet = actor["sheet"]
    sheet["edition"] = "2024"
    sheet["progression"].update(
        level=1,
        classes=[{"name": "Fighter", "level": 1, "subclass": "", "hit_die": 10}],
    )
    feature_id = "dnd5e.content.srd2024.feature.fighter-weapon-mastery"
    sheet["content"]["features"].append(
        {
            "id": feature_id,
            "name": "Weapon Mastery",
            "source_key": "Fighter",
            "mechanic_refs": ["dnd5e.core.weapon.mastery"],
        }
    )
    sheet["content"]["selections"].append(
        {
            "artifact_id": feature_id,
            "kind": "feature",
            "name": "Weapon Mastery",
            "pack_id": "dnd5e.content.srd2024",
            "pack_version": "1.0.0",
            "rule_refs": ["bundled:srd2024/DND5eSRD_087-103.md#mastery-properties"],
            "mechanic_refs": ["dnd5e.core.weapon.mastery"],
            "selection": {
                "weapon_ids": ["mastery-weapon"],
                "mastery_by_weapon_id": {"mastery-weapon": mastery},
            },
        }
    )
    properties = ["light"] if mastery == "nick" else []
    sheet["inventory"]["items"] = [
        {
            "id": "mastery-weapon",
            "name": "Mastery Weapon",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d8",
                "damage_type": "slashing",
                "properties": properties,
                "mastery": mastery,
            },
        }
    ]
    sheet["inventory"]["equipment_slots"]["main_hand"] = "mastery-weapon"
    actor["sheet"] = validate_character_sheet(sheet)
    actor["derived"] = derive_character_sheet(actor["sheet"])
    return actor


def _add_light_weapon(actor: dict, *, item_id: str = "other-light-weapon") -> dict:
    actor = deepcopy(actor)
    actor["sheet"]["inventory"]["items"].append(
        {
            "id": item_id,
            "name": "Other Light Weapon",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "off_hand",
            "mechanics": {
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d6",
                "damage_type": "piercing",
                "properties": ["light"],
            },
        }
    )
    actor["sheet"]["inventory"]["equipment_slots"]["off_hand"] = item_id
    actor["sheet"] = validate_character_sheet(actor["sheet"])
    actor["derived"] = derive_character_sheet(actor["sheet"])
    return actor


def test_2024_graze_deals_only_attack_ability_modifier_on_a_miss() -> None:
    attacker = _mastery_actor("fighter", "graze")
    target = _actor("target", hp=20, ac=30)
    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "mastery-weapon", "use_weapon_mastery": True},
        rules=resolution_context({"edition": "2024", "fingerprint": "", "lock": []}),
    )

    _, updated_target, result = resolve_attack_action(
        attacker,
        target,
        plan=plan,
        rules=resolution_context({"edition": "2024", "fingerprint": "", "lock": []}),
        rng=_SequenceRng(2),
    )

    assert result["hit"] is False
    assert result["weapon_mastery"] == {
        "id": "graze",
        "weapon_id": "mastery-weapon",
        "applied": True,
        "amount": 3,
        "damage_type": "slashing",
        "cannot_be_increased": True,
    }
    assert updated_target["sheet"]["combat"]["hp"]["value"] == 17


def test_2024_topple_rolls_the_target_save_and_applies_prone() -> None:
    attacker = _mastery_actor("fighter", "topple")
    target = _actor("target", hp=20, ac=1)
    target["sheet"]["abilities"]["constitution"]["score"] = 1
    target["derived"] = derive_character_sheet(target["sheet"])
    rules = resolution_context({"edition": "2024", "fingerprint": "", "lock": []})
    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "mastery-weapon", "use_weapon_mastery": True},
        rules=rules,
    )

    _, updated_target, result = resolve_attack_action(
        attacker,
        target,
        plan=plan,
        rules=rules,
        rng=_SequenceRng(10, 1, 1),
    )

    assert result["weapon_mastery"]["id"] == "topple"
    assert result["weapon_mastery"]["save"]["success"] is False
    assert "prone" in updated_target["sheet"]["conditions"]


def test_2024_push_and_slow_masteries_update_encounter_state() -> None:
    pusher = _mastery_actor("pusher", "push")
    target = _actor("target", hp=20, ac=1)
    pusher.update(initiative=20, position={"x": 0, "y": 0}, disposition="friendly")
    target.update(initiative=10, position={"x": 1, "y": 0}, disposition="hostile")
    encounter = _grid_encounter([pusher, target], ruleset="2024")
    rules = resolution_context({"edition": "2024", "fingerprint": "", "lock": []})
    plan = preflight_attack(
        pusher,
        target,
        action={"weapon_id": "mastery-weapon", "use_weapon_mastery": True},
        encounter=encounter,
        rules=rules,
    )
    _, _, attack = resolve_attack_action(
        pusher, target, plan=plan, rules=rules, rng=_SequenceRng(10, 1)
    )
    pushed = apply_weapon_mastery_to_encounter(
        encounter,
        attack,
        attacker_id="pusher",
        target_id="target",
    )
    moved_target = next(
        item for item in pushed["encounter"]["combatants"] if item["actor_id"] == "target"
    )
    assert moved_target["position"] == {"x": 3, "y": 0}

    slower = _mastery_actor("slower", "slow")
    slow_target = _actor("slow-target", hp=20, ac=1)
    slower.update(initiative=20, position={"x": 0, "y": 0}, disposition="friendly")
    slow_target.update(initiative=10, position={"x": 1, "y": 0}, disposition="hostile")
    slow_encounter = _grid_encounter([slower, slow_target], ruleset="2024")
    slow_plan = preflight_attack(
        slower,
        slow_target,
        action={"weapon_id": "mastery-weapon", "use_weapon_mastery": True},
        encounter=slow_encounter,
        rules=rules,
    )
    _, _, slow_attack = resolve_attack_action(
        slower, slow_target, plan=slow_plan, rules=rules, rng=_SequenceRng(10, 1)
    )
    slowed = apply_weapon_mastery_to_encounter(
        slow_encounter,
        slow_attack,
        attacker_id="slower",
        target_id="slow-target",
    )["encounter"]
    target_turn = end_turn(slowed, actor_id_value="slower")
    current = current_combatant(target_turn)
    assert current["actor_id"] == "slow-target"
    assert current["turn_budget"]["speed"] == 20
    assert current["turn_budget"]["movement"] == 20


def test_2024_sap_and_vex_apply_only_to_the_next_eligible_attack_roll() -> None:
    rules = resolution_context({"edition": "2024", "fingerprint": "", "lock": []})
    sapper = _mastery_actor("sapper", "sap")
    target = _actor("target", hp=20, ac=1)
    third = _actor("third", hp=20, ac=10)
    sapper.update(initiative=20, position={"x": 0, "y": 0}, disposition="friendly")
    target.update(initiative=10, position={"x": 1, "y": 0}, disposition="hostile")
    third.update(initiative=5, position={"x": 2, "y": 0}, disposition="friendly")
    encounter = _grid_encounter([sapper, target, third], ruleset="2024")
    sap_plan = preflight_attack(
        sapper,
        target,
        action={"weapon_id": "mastery-weapon", "use_weapon_mastery": True},
        encounter=encounter,
        rules=rules,
    )
    _, _, sap_attack = resolve_attack_action(
        sapper, target, plan=sap_plan, rules=rules, rng=_SequenceRng(10, 1)
    )
    sapped = apply_weapon_mastery_to_encounter(
        encounter, sap_attack, attacker_id="sapper", target_id="target"
    )["encounter"]
    reply = preflight_attack(
        target,
        third,
        action={"weapon_id": "unarmed-strike"},
        encounter=sapped,
        allow_out_of_turn=True,
        require_attack_action=False,
        rules=rules,
    )
    assert reply["disadvantage"] is True
    assert reply["next_attack_disadvantage_effect_id"]
    consumed = consume_weapon_mastery_attack_effects(sapped, reply)
    assert consumed["consumed_effect_ids"] == [reply["next_attack_disadvantage_effect_id"]]

    vexer = _mastery_actor("vexer", "vex")
    vex_target = _actor("vex-target", hp=20, ac=1)
    vexer.update(initiative=20, position={"x": 0, "y": 0}, disposition="friendly")
    vex_target.update(initiative=10, position={"x": 1, "y": 0}, disposition="hostile")
    vex_encounter = _grid_encounter([vexer, vex_target], ruleset="2024")
    vex_plan = preflight_attack(
        vexer,
        vex_target,
        action={"weapon_id": "mastery-weapon", "use_weapon_mastery": True},
        encounter=vex_encounter,
        rules=rules,
    )
    _, _, vex_attack = resolve_attack_action(
        vexer, vex_target, plan=vex_plan, rules=rules, rng=_SequenceRng(10, 1)
    )
    vexed = apply_weapon_mastery_to_encounter(
        vex_encounter, vex_attack, attacker_id="vexer", target_id="vex-target"
    )["encounter"]
    followup = preflight_attack(
        vexer,
        vex_target,
        action={"weapon_id": "mastery-weapon"},
        encounter=vexed,
        allow_out_of_turn=True,
        require_attack_action=False,
        rules=rules,
    )
    assert followup["advantage"] is True
    assert followup["next_attack_advantage_effect_id"]


def test_2024_cleave_grants_one_restricted_attack_only_after_a_hit() -> None:
    rules = resolution_context({"edition": "2024", "fingerprint": "", "lock": []})
    attacker = _mastery_actor("cleaver", "cleave")
    primary = _actor("primary", hp=20, ac=1)
    secondary = _actor("secondary", hp=20, ac=1)
    attacker.update(initiative=20, position={"x": 0, "y": 0}, disposition="friendly")
    primary.update(initiative=10, position={"x": 1, "y": 0}, disposition="hostile")
    secondary.update(initiative=5, position={"x": 1, "y": 1}, disposition="hostile")
    encounter = _grid_encounter([attacker, primary, secondary], ruleset="2024")
    plan = preflight_attack(
        attacker,
        primary,
        action={
            "weapon_id": "mastery-weapon",
            "use_weapon_mastery": True,
            "mastery_secondary_target_id": "secondary",
        },
        encounter=encounter,
        rules=rules,
    )
    encounter, payment = pay_attack_action(
        encounter,
        attacker,
        weapon_id="mastery-weapon",
        attack_mode="melee",
        target_id="primary",
    )
    assert payment["kind"] == "attack_action"
    _, _, result = resolve_attack_action(
        attacker, primary, plan=plan, rules=rules, rng=_SequenceRng(10, 1)
    )
    cleave = apply_weapon_mastery_to_encounter(
        encounter,
        result,
        attacker_id="cleaver",
        target_id="primary",
    )["encounter"]
    combatant = next(item for item in cleave["combatants"] if item["actor_id"] == "cleaver")
    assert combatant["turn_flags"]["weapon_mastery_followup"]["target_id"] == "secondary"

    with pytest.raises(CombatEngineError, match="recorded second target"):
        pay_attack_action(
            cleave,
            attacker,
            weapon_id="mastery-weapon",
            attack_mode="melee",
            target_id="primary",
            weapon_mastery_followup="cleave",
        )
    followup = preflight_attack(
        attacker,
        secondary,
        action={
            "weapon_id": "mastery-weapon",
            "weapon_mastery_followup": "cleave",
        },
        encounter=cleave,
        rules=rules,
    )
    assert followup["damage_expression"] == "1d8"
    paid, followup_payment = pay_attack_action(
        cleave,
        attacker,
        weapon_id="mastery-weapon",
        attack_mode="melee",
        target_id="secondary",
        weapon_mastery_followup="cleave",
    )
    assert followup_payment["mastery"] == "cleave"
    consumed = consume_weapon_mastery_attack_effects(paid, followup)["encounter"]
    flags = next(item for item in consumed["combatants"] if item["actor_id"] == "cleaver")[
        "turn_flags"
    ]
    assert "pending_weapon_attack_modifier" not in flags
    with pytest.raises(CombatEngineError, match="only once per turn"):
        preflight_attack(
            attacker,
            primary,
            action={
                "weapon_id": "mastery-weapon",
                "use_weapon_mastery": True,
                "mastery_secondary_target_id": "secondary",
            },
            encounter=consumed,
            require_attack_action=False,
            rules=rules,
        )


def test_2024_nick_moves_the_light_extra_attack_into_the_attack_action() -> None:
    rules = resolution_context({"edition": "2024", "fingerprint": "", "lock": []})
    attacker = _add_light_weapon(_mastery_actor("duelist", "nick"))
    target = _actor("target", hp=20, ac=1)
    attacker.update(initiative=20, position={"x": 0, "y": 0}, disposition="friendly")
    target.update(initiative=10, position={"x": 1, "y": 0}, disposition="hostile")
    encounter = _grid_encounter([attacker, target], ruleset="2024")
    encounter, _ = pay_attack_action(
        encounter,
        attacker,
        weapon_id="other-light-weapon",
        attack_mode="melee",
        target_id="target",
    )
    before_bonus = current_combatant(encounter)["turn_budget"]["bonus_action"]
    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "mastery-weapon", "light_extra_attack": "nick"},
        encounter=encounter,
        rules=rules,
    )
    assert plan["damage_expression"] == "1d8"
    encounter, payment = pay_attack_action(
        encounter,
        attacker,
        weapon_id="mastery-weapon",
        attack_mode="melee",
        target_id="target",
        light_extra_attack="nick",
    )
    assert payment == {
        "kind": "weapon_mastery_followup",
        "mastery": "nick",
        "weapon_id": "mastery-weapon",
        "payment": "attack_action",
    }
    assert current_combatant(encounter)["turn_budget"]["bonus_action"] == before_bonus
    with pytest.raises(CombatEngineError, match="only once per turn"):
        pay_attack_action(
            encounter,
            attacker,
            weapon_id="mastery-weapon",
            attack_mode="melee",
            target_id="target",
            light_extra_attack="nick",
        )


def test_two_weapon_fighting_retains_the_light_extra_attack_modifier() -> None:
    rules = resolution_context({"edition": "2024", "fingerprint": "", "lock": []})
    attacker = _add_light_weapon(_mastery_actor("duelist", "nick"))
    attacker["sheet"]["content"]["features"].append(
        {"id": "two-weapon-fighting", "name": "Two-Weapon Fighting"}
    )
    attacker["sheet"] = validate_character_sheet(attacker["sheet"])
    attacker["derived"] = derive_character_sheet(attacker["sheet"])
    target = _actor("target", hp=20, ac=1)
    attacker.update(initiative=20, position={"x": 0, "y": 0}, disposition="friendly")
    target.update(initiative=10, position={"x": 1, "y": 0}, disposition="hostile")
    encounter = _grid_encounter([attacker, target], ruleset="2024")
    encounter, _ = pay_attack_action(
        encounter,
        attacker,
        weapon_id="other-light-weapon",
        attack_mode="melee",
        target_id="target",
    )
    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "mastery-weapon", "light_extra_attack": "nick"},
        encounter=encounter,
        rules=rules,
    )
    assert plan["damage_expression"] == "1d8 + 3"
    encounter, _ = pay_attack_action(
        encounter,
        attacker,
        weapon_id="mastery-weapon",
        attack_mode="melee",
        target_id="target",
        light_extra_attack="nick",
    )


def _give_magic_resistance(actor: dict) -> None:
    actor["sheet"]["content"]["features"].append(
        {
            "id": f"{actor['id']}-magic-resistance",
            "name": "Magic Resistance",
            "choices": {
                "source_trait": {
                    "kind": "magic_resistance",
                    "trigger": "saving_throw",
                    "save_source_kinds": ["spell", "magical_effect"],
                    "grants": "advantage",
                    "automatic": True,
                    "source_excerpt": (
                        f"The {actor['name']} has advantage on saving throws "
                        "against spells and other magical effects."
                    ),
                }
            },
        }
    )
    actor["derived"] = derive_character_sheet(actor["sheet"])


def test_2024_hypnotic_pattern_preserves_its_exact_source_spell_id() -> None:
    target = _actor("target")
    target["sheet"]["edition"] = "2024"
    target["derived"] = derive_character_sheet(target["sheet"])

    resolved = resolve_hypnotic_pattern_target(
        target,
        caster_id="caster",
        spell_id=CORE_2024_HYPNOTIC_PATTERN_SPELL_ID,
        save_dc=15,
        rules=resolution_context(
            {"edition": "2024", "fingerprint": "", "lock": [], "mechanics": []}
        ),
        rng=_SequenceRng(1),
    )

    assert resolved["result"]["outcome"] == "affected"
    effect = next(
        item
        for item in resolved["sheet"]["effects"]
        if item["id"] == resolved["result"]["effect_id"]
    )
    assert effect["source_spell_id"] == CORE_2024_HYPNOTIC_PATTERN_SPELL_ID


def test_hypnotic_pattern_effect_lifecycle_preserves_other_condition_sources() -> None:
    target = _actor("target", hp=20)
    target["sheet"]["effects"] = [
        {
            "id": "other-charm",
            "name": "Other charm",
            "kind": "timed_conditions",
            "source": "other-caster",
            "active": True,
            "concentration": False,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [
                {"path": "conditions", "mode": "add", "value": "charmed"},
                {"path": "conditions", "mode": "add", "value": "incapacitated"},
            ],
            "description": "",
        },
        {
            "id": "target-concentration",
            "name": "Target concentration",
            "kind": "concentration",
            "source": "spell.cast",
            "source_spell_id": "test.target-concentration",
            "active": True,
            "concentration": True,
            "duration": {"period": "manual", "remaining": 0},
            "changes": [],
            "description": "",
        },
    ]
    target["sheet"]["conditions"] = ["charmed", "incapacitated"]
    target["derived"] = derive_character_sheet(target["sheet"])

    resolved = resolve_hypnotic_pattern_target(
        target,
        caster_id="caster",
        spell_id=CORE_HYPNOTIC_PATTERN_SPELL_ID,
        save_dc=15,
        rng=_SequenceRng(1),
    )

    assert resolved["result"]["outcome"] == "affected"
    assert resolved["result"]["ended_concentration_effect_ids"] == ["target-concentration"]
    assert active_hypnotic_pattern_effect_ids(resolved["sheet"]) == [
        resolved["result"]["effect_id"]
    ]
    assert source_speed_multiplier(resolved["sheet"]) == 0.0

    no_damage = apply_damage_to_sheet(
        resolved["sheet"],
        amount=0,
        damage_type="force",
    )
    assert active_hypnotic_pattern_effect_ids(no_damage["sheet"])

    damaged = apply_damage_to_sheet(
        no_damage["sheet"],
        amount=1,
        damage_type="force",
    )
    assert active_hypnotic_pattern_effect_ids(damaged["sheet"]) == []
    assert {"charmed", "incapacitated"} <= set(damaged["sheet"]["conditions"])
    assert source_speed_multiplier(damaged["sheet"]) == 1.0
    assert resolved["result"]["effect_id"] in damaged["ended_effect_ids"]


def test_hypnotic_pattern_immunity_and_effect_dependency_are_hard_settled() -> None:
    immune = _actor("immune")
    immune["sheet"]["traits"]["condition_immunities"] = ["charmed"]
    immune["derived"] = derive_character_sheet(immune["sheet"])
    ignored = resolve_hypnotic_pattern_target(
        immune,
        caster_id="caster",
        spell_id=CORE_HYPNOTIC_PATTERN_SPELL_ID,
        save_dc=15,
        rng=_SequenceRng(1),
    )
    assert ignored["result"]["outcome"] == "immune_to_charmed"
    assert ignored["result"]["save"] is None

    target = _actor("target")
    affected = resolve_hypnotic_pattern_target(
        target,
        caster_id="caster",
        spell_id=CORE_HYPNOTIC_PATTERN_SPELL_ID,
        save_dc=15,
        rng=_SequenceRng(1),
    )
    target_effect_id = affected["result"]["effect_id"]
    caster_sheet = default_character_sheet()
    caster_sheet["effects"] = [
        {
            "id": "caster-concentration",
            "name": "Concentrating: Hypnotic Pattern",
            "kind": "concentration",
            "source": "spell.cast",
            "source_spell_id": CORE_HYPNOTIC_PATTERN_SPELL_ID,
            "active": False,
            "concentration": True,
            "duration": {"period": "round", "remaining": 10},
            "changes": [],
            "description": "",
            "ended_reason": "failed_save",
        }
    ]
    encounter = {
        "dependent_effects": [
            {
                "id": "link",
                "mechanic_id": "dnd5e.core.spell.hypnotic_pattern",
                "dependency": "source_effect_active",
                "source_actor_id": "caster",
                "source_effect_id": "caster-concentration",
                "target_actor_id": "target",
                "target_effect_id": target_effect_id,
                "active": True,
            }
        ]
    }

    reconciled = reconcile_effect_dependencies(
        encounter,
        {
            "caster": validate_character_sheet(caster_sheet),
            "target": affected["sheet"],
        },
    )

    assert reconciled["changed_actor_ids"] == ["target"]
    assert reconciled["ended_links"][0]["ended_reason"] == "source_effect_ended"
    assert active_hypnotic_pattern_effect_ids(reconciled["sheets"]["target"]) == []
    assert "charmed" not in reconciled["sheets"]["target"]["conditions"]
    assert "incapacitated" not in reconciled["sheets"]["target"]["conditions"]


def test_source_actor_capability_dependency_uses_all_incapacitating_states() -> None:
    source = default_character_sheet()
    source["edition"] = "2024"
    source["conditions"] = ["unconscious"]
    target = default_character_sheet()
    target["edition"] = "2024"
    turn_effect = {
        "id": "turn-effect",
        "name": "Turn Undead",
        "kind": "turn_undead",
        "source": "cleric",
        "active": True,
        "concentration": False,
        "duration": {"period": "minute", "remaining": 1},
        "changes": [
            {"path": "conditions", "mode": "add", "value": "frightened"},
            {"path": "conditions", "mode": "add", "value": "incapacitated"},
        ],
        "description": "",
    }
    target["effects"] = [turn_effect]
    target["conditions"] = ["frightened", "incapacitated"]
    encounter = {
        "dependent_effects": [
            {
                "id": "turn-dependency",
                "mechanic_id": "dnd5e.core.activity.turn_undead",
                "dependency": "source_actor_capable",
                "source_actor_id": "cleric",
                "target_actor_id": "undead",
                "target_effect_id": "turn-effect",
                "active": True,
            }
        ]
    }

    reconciled = reconcile_effect_dependencies(
        encounter,
        {
            "cleric": validate_character_sheet(source),
            "undead": validate_character_sheet(target),
        },
    )

    assert reconciled["changed_actor_ids"] == ["undead"]
    assert reconciled["ended_links"][0]["ended_reason"] == ("source_incapacitated_or_dead")
    assert reconciled["sheets"]["undead"]["conditions"] == []


def test_actor_check_rejects_attack_rolls_owned_by_the_attack_engine() -> None:
    with pytest.raises(CombatEngineError, match="unsupported check kind"):
        resolve_actor_check(
            _actor("attacker"),
            kind="attack",
            ability="strength",
            dc=10,
        )


def _rogue(identifier: str = "rogue") -> dict:
    actor = _actor(identifier, hp=30)
    actor["sheet"]["progression"] = {
        "level": 1,
        "classes": [{"name": "Rogue", "level": 1, "hit_die": 8}],
    }
    actor["sheet"]["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2014.feature.rogue-sneak-attack",
            "name": "Sneak Attack",
            "source_key": "Rogue",
        }
    ]
    actor["derived"] = derive_character_sheet(actor["sheet"])
    actor["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "dagger",
            "attack_type": "melee",
            "properties": ["finesse", "light", "thrown"],
            "attack_bonus": 99,
            "damage_expression": "1",
            "damage_type": "piercing",
        }
    ]
    return actor


def test_generic_effect_changes_speed_attacks_and_preserves_charm_source() -> None:
    source = _actor("source")
    dazed = _actor("dazed")
    other = _actor("other")
    dazed["sheet"]["conditions"] = ["charmed"]
    dazed["sheet"]["effects"] = [
        {
            "id": "dazing",
            "name": "Source-bound daze",
            "kind": "timed_conditions",
            "source": "source",
            "active": True,
            "duration": {"period": "source_turn_start", "remaining": 1},
            "changes": [
                {"path": "conditions", "mode": "add", "value": "charmed"},
                {"path": "combat.speed.multiplier", "mode": "multiply", "value": 0.5},
                {"path": "rolls.attack.disadvantage", "mode": "set", "value": True},
            ],
        }
    ]
    dazed["derived"] = derive_character_sheet(dazed["sheet"])

    with pytest.raises(CombatEngineError, match="cannot attack its charmer"):
        preflight_attack(dazed, source, action={"weapon_id": "unarmed-strike"})
    plan = preflight_attack(
        dazed,
        other,
        action={"weapon_id": "unarmed-strike"},
    )

    assert plan["disadvantage"] is True
    assert "dazing" in plan["disadvantage_sources"]
    assert source_speed_multiplier(dazed["sheet"]) == 0.5


def test_telekinetic_ray_moves_up_to_the_last_legal_cell_without_reactions() -> None:
    source = _actor("gazer")
    source.update(
        initiative=20,
        position={"x": 1, "y": 1},
        disposition="hostile",
    )
    target = _actor("target")
    target.update(
        initiative=10,
        position={"x": 2, "y": 1},
        disposition="friendly",
    )
    encounter = _grid_encounter([source, target])
    encounter["battle_map"] = compile_battle_map(
        {"scene_id": "gazer-rays", "spatial": {}},
        {"width_cells": 6, "height_cells": 4},
    )

    moved = force_move_directly_away(
        encounter,
        source_actor_id="gazer",
        target_actor_id="target",
        distance_ft=30,
    )

    assert moved["moved_distance_ft"] == 15
    assert moved["destination"] == {"x": 5, "y": 1}
    assert moved["encounter"]["pending"] == []
    assert moved["encounter"]["log"][-1]["opportunity_reactions"] is False


def test_telekinetic_ray_projects_noncardinal_bearings_onto_the_square_grid() -> None:
    source = _actor("gazer")
    source.update(
        initiative=20,
        position={"x": 2, "y": 2},
        disposition="hostile",
    )
    target = _actor("target")
    target.update(
        initiative=10,
        position={"x": 1, "y": 4},
        disposition="friendly",
    )
    encounter = _grid_encounter([source, target])
    encounter["battle_map"] = compile_battle_map(
        {"scene_id": "gazer-rays", "spatial": {}},
        {"width_cells": 4, "height_cells": 7},
    )

    moved = force_move_directly_away(
        encounter,
        source_actor_id="gazer",
        target_actor_id="target",
        distance_ft=30,
    )

    # The continuous (-1, 2) outward ray crosses (0, 5), then (0, 6);
    # the following cell is outside the map, so "up to 30 feet" stops there.
    assert moved["moved_distance_ft"] == 10
    assert moved["destination"] == {"x": 0, "y": 6}


def test_force_move_directly_toward_stops_before_the_source() -> None:
    source = _actor("merrow")
    source.update(
        initiative=20,
        position={"x": 1, "y": 1},
        disposition="hostile",
    )
    target = _actor("target")
    target.update(
        initiative=10,
        position={"x": 5, "y": 1},
        disposition="friendly",
    )
    encounter = _grid_encounter([source, target])
    encounter["battle_map"] = compile_battle_map(
        {"scene_id": "merrow-harpoon", "spatial": {}},
        {"width_cells": 7, "height_cells": 3},
    )

    moved = force_move_directly_toward(
        encounter,
        source_actor_id="merrow",
        target_actor_id="target",
        distance_ft=20,
    )

    assert moved["moved_distance_ft"] == 15
    assert moved["destination"] == {"x": 2, "y": 1}
    assert moved["direction"] == "toward_source"
    assert moved["encounter"]["pending"] == []
    assert moved["encounter"]["log"][-1] == {
        "type": "forced_movement",
        "source_actor_id": "merrow",
        "target_actor_id": "target",
        "requested_distance_ft": 20,
        "moved_distance_ft": 15,
        "direction": "toward_source",
        "opportunity_reactions": False,
    }


def test_frightened_creature_cannot_willingly_move_closer_to_visible_source() -> None:
    target = _actor("target")
    target["sheet"]["conditions"] = ["frightened"]
    target["sheet"]["effects"] = [
        {
            "id": "fear",
            "name": "Fear Ray",
            "kind": "timed_conditions",
            "source": "gazer",
            "active": True,
            "duration": {"period": "source_turn_start", "remaining": 1},
            "changes": [{"path": "conditions", "mode": "add", "value": "frightened"}],
        }
    ]
    target["derived"] = derive_character_sheet(target["sheet"])
    target.update(initiative=20, position={"x": 2, "y": 1})
    source = _actor("gazer")
    source.update(initiative=10, position={"x": 0, "y": 1})
    encounter = _grid_encounter([target, source])

    with pytest.raises(CombatEngineError, match="cannot willingly move closer"):
        spend_movement(
            encounter,
            "target",
            5,
            destination={"x": 1, "y": 1},
        )
    moved = spend_movement(
        encounter,
        "target",
        5,
        destination={"x": 3, "y": 1},
    )

    assert current_combatant(moved)["position"] == {"x": 3, "y": 1}


def _lightfoot(identifier: str = "lightfoot") -> dict:
    actor = _actor(identifier)
    actor["sheet"]["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2014.species-feature.lightfoot-lucky",
            "name": "Lucky",
            "source_key": "Lightfoot",
        }
    ]
    actor["derived"] = derive_character_sheet(actor["sheet"])
    return actor


def test_ordinary_checks_do_not_use_attack_natural_rules() -> None:
    result = resolve_check(
        dc=21,
        ability_score=10,
        kind="ability",
        rng=random.Random(5),
    )
    assert result["natural"] == 20
    assert result["success"] is False


def test_2014_jack_of_all_trades_applies_only_to_unproficient_ability_checks() -> None:
    bard = _actor("bard")
    bard["sheet"]["progression"] = {
        "level": 2,
        "classes": [{"name": "Bard", "level": 2, "hit_die": 8}],
    }
    bard["sheet"]["abilities"]["charisma"]["score"] = 16
    bard["sheet"]["abilities"]["dexterity"]["score"] = 14
    bard["sheet"]["skills"]["deception"]["proficiency"] = "proficient"
    bard["sheet"]["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2014.feature.bard-jack-of-all-trades",
            "name": "Jack of All Trades",
            "source_key": "Bard",
            "mechanic_refs": ["dnd5e.core.check.jack_of_all_trades"],
        }
    ]
    bard["derived"] = derive_character_sheet(bard["sheet"])
    rules = resolution_context({"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []})

    untrained = resolve_actor_check(
        bard,
        kind="check",
        ability="intimidation",
        dc=14,
        rules=rules,
        rng=_SequenceRng(10),
    )
    assert untrained["ability_modifier"] == 3
    assert untrained["proficiency_bonus"] == 0
    assert untrained["bonus"] == 1
    assert untrained["total"] == 14
    assert [receipt["mechanic_id"] for receipt in untrained["rule_receipts"]] == [
        "dnd5e.core.check.jack_of_all_trades"
    ]

    trained = resolve_actor_check(
        bard,
        kind="check",
        ability="deception",
        dc=15,
        rules=rules,
        rng=_SequenceRng(10),
    )
    assert trained["ability_modifier"] == 3
    assert trained["proficiency_bonus"] == 2
    assert trained["bonus"] == 0
    assert trained["total"] == 15
    assert trained["rule_receipts"] == []

    saving_throw = resolve_actor_check(
        bard,
        kind="save",
        ability="wisdom",
        dc=11,
        rules=rules,
        rng=_SequenceRng(10),
    )
    assert saving_throw["bonus"] == 0
    assert saving_throw["total"] == 10
    assert saving_throw["rule_receipts"] == []

    bard["sheet"]["edition"] = "2024"
    bard["derived"] = derive_character_sheet(bard["sheet"])
    revised_check = resolve_actor_check(
        bard,
        kind="check",
        ability="intimidation",
        dc=14,
        rng=_SequenceRng(10),
    )
    assert revised_check["bonus"] == 1
    assert revised_check["total"] == 14


def test_2024_jack_of_all_trades_uses_skill_proficiency_but_not_initiative() -> None:
    bard = _actor("bard-2024")
    bard["sheet"]["edition"] = "2024"
    bard["sheet"]["progression"] = {
        "level": 2,
        "classes": [{"name": "Bard", "level": 2, "hit_die": 8}],
    }
    bard["sheet"]["abilities"]["charisma"]["score"] = 16
    bard["sheet"]["abilities"]["dexterity"]["score"] = 14
    bard["sheet"]["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2024.feature.bard-jack-of-all-trades",
            "name": "Jack of All Trades",
            "source_key": "Bard",
            "mechanic_refs": ["dnd5e.core.check.jack_of_all_trades"],
        }
    ]
    bard["derived"] = derive_character_sheet(bard["sheet"])
    rules = resolution_context({"edition": "2024", "fingerprint": "", "lock": [], "mechanics": []})

    skill = resolve_actor_check(
        bard,
        kind="check",
        ability="intimidation",
        dc=14,
        rules=rules,
        rng=_SequenceRng(10),
    )
    raw_ability = resolve_actor_check(
        bard,
        kind="check",
        ability="dexterity",
        dc=13,
        rules=rules,
        rng=_SequenceRng(10),
    )
    encounter = start_encounter([bard], ruleset="2024", rng=_SequenceRng(10))

    assert skill["bonus"] == 1
    assert skill["total"] == 14
    assert raw_ability["bonus"] == 0
    assert raw_ability["total"] == 12
    assert encounter["combatants"][0]["initiative_bonus"] == 2
    assert encounter["rule_boundary_ids"] == []


def test_2014_group_check_succeeds_when_at_least_half_succeed() -> None:
    actors = [_actor(f"scout-{index}") for index in range(1, 7)]
    rules = resolution_context(
        {
            "edition": "2014",
            "fingerprint": "group-check-pack",
            "lock": [],
            "mechanics": [],
        }
    )

    result = resolve_actor_group_check(
        actors,
        ability="stealth",
        dc=16,
        advantage=True,
        rules_by_actor_id={actor["id"]: rules for actor in actors},
        rng=_SequenceRng(16, 4, 17, 3, 18, 2, 19, 1, 20, 5, 10, 6),
    )

    assert result["participant_count"] == 6
    assert result["success_count"] == 5
    assert result["failure_count"] == 1
    assert result["required_successes"] == 3
    assert result["success"] is True
    assert [entry["success"] for entry in result["participants"]] == [
        True,
        True,
        True,
        True,
        True,
        False,
    ]
    assert [item["mechanic_id"] for item in result["rule_receipts"]] == ["dnd5e.core.check.group"]


def test_2014_group_check_rejects_duplicate_or_single_actor_groups() -> None:
    actor = _actor("scout")

    with pytest.raises(CombatEngineError, match="at least two actors"):
        resolve_actor_group_check([actor], ability="stealth", dc=10)
    with pytest.raises(CombatEngineError, match="must be unique"):
        resolve_actor_group_check([actor, actor], ability="stealth", dc=10)


def test_2024_group_check_fails_at_the_public_rules_boundary() -> None:
    actors = [_actor("scout-1"), _actor("scout-2")]
    rules = resolution_context(
        {
            "edition": "2024",
            "fingerprint": "group-check-pack",
            "lock": [],
            "mechanics": [],
        }
    )

    with pytest.raises(CombatEngineError, match="2014 rules procedure"):
        resolve_actor_group_check(
            actors,
            ability="stealth",
            dc=10,
            rules_by_actor_id={actor["id"]: rules for actor in actors},
        )


def test_2014_ability_contest_compares_totals_and_uses_no_synthetic_dc() -> None:
    deceiver = _actor("deceiver")
    deceiver["sheet"]["abilities"]["charisma"]["score"] = 16
    deceiver["sheet"]["skills"]["deception"]["proficiency"] = "proficient"
    deceiver["derived"] = derive_character_sheet(deceiver["sheet"])
    observer = _actor("observer")
    observer["sheet"]["abilities"]["wisdom"]["score"] = 14
    observer["sheet"]["skills"]["insight"]["proficiency"] = "proficient"
    observer["derived"] = derive_character_sheet(observer["sheet"])

    result = resolve_actor_contest(
        deceiver,
        observer,
        source_ability="deception",
        target_ability="insight",
        target_advantage=True,
        rng=_SequenceRng(12, 4, 16),
    )

    assert result["kind"] == "ability_contest"
    assert result["source_check"]["rolls"] == [12]
    assert result["source_check"]["total"] == 17
    assert result["target_check"]["rolls"] == [4, 16]
    assert result["target_check"]["total"] == 20
    assert "dc" not in result["source_check"]
    assert "success" not in result["target_check"]
    assert result["winner_actor_id"] == "observer"
    assert result["outcome"] == "target_wins"


def test_2014_ability_contest_tie_leaves_situation_unchanged() -> None:
    source = _actor("source")
    target = _actor("target")

    result = resolve_actor_contest(
        source,
        target,
        source_ability="strength",
        target_ability="strength",
        rng=_SequenceRng(10, 10),
    )

    assert result["tie"] is True
    assert result["winner_actor_id"] == ""
    assert result["outcome"] == "tie_no_change"


def test_2014_jack_of_all_trades_applies_to_initiative() -> None:
    bard = _actor("bard")
    bard["sheet"]["progression"] = {
        "level": 2,
        "classes": [{"name": "Bard", "level": 2, "hit_die": 8}],
    }
    bard["sheet"]["abilities"]["dexterity"]["score"] = 14
    bard["sheet"]["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2014.feature.bard-jack-of-all-trades",
            "name": "Jack of All Trades",
            "source_key": "Bard",
            "mechanic_refs": ["dnd5e.core.check.jack_of_all_trades"],
        }
    ]
    bard["derived"] = derive_character_sheet(bard["sheet"])

    encounter = start_encounter([bard], ruleset="2014", rng=_SequenceRng(10))

    assert encounter["combatants"][0]["initiative_bonus"] == 3
    assert encounter["combatants"][0]["initiative"] == 13
    assert encounter["rule_boundary_ids"] == ["dnd5e.core.check.jack_of_all_trades"]


def test_attack_preflight_rejects_exhausted_linked_ammunition() -> None:
    attacker = _actor("archer")
    attacker["sheet"]["inventory"]["items"] = [
        {"id": "arrows", "name": "Arrows", "kind": "ammunition", "quantity": 0},
        {
            "id": "longbow",
            "name": "Longbow",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "ranged",
                "attack_ability": "dexterity",
                "damage_formula": "1d8",
                "damage_type": "piercing",
                "normal_range_ft": 150,
                "long_range_ft": 600,
                "ammunition_item_id": "arrows",
            },
        },
    ]
    attacker["sheet"]["inventory"]["equipment_slots"]["main_hand"] = "longbow"
    attacker["derived"] = derive_character_sheet(attacker["sheet"])

    with pytest.raises(CombatEngineError, match="no linked ammunition remaining"):
        preflight_attack(attacker, _actor("target"), action={"weapon_id": "longbow"})


def test_slaying_ammunition_opens_source_save_damage() -> None:
    source_excerpt = (
        "If a creature belonging to the type, race, or group associated with an "
        "arrow of slaying takes damage from the arrow, the creature must make a "
        "DC 17 Constitution saving throw, taking an extra 6d10 piercing damage "
        "on a failed save, or half as much extra damage on a successful one."
    )
    attacker = _actor("archer")
    attacker["sheet"]["inventory"]["items"] = [
        {"id": "arrows", "name": "Arrows", "kind": "ammunition", "quantity": 20},
        {
            "id": "dragon-slaying-arrows",
            "name": "Arrows of dragon slaying",
            "kind": "ammunition",
            "quantity": 2,
            "mechanics": {
                "magic": True,
                "rarity": "very_rare",
                "slaying": {
                    "target_groups": ["dragon"],
                    "save_ability": "constitution",
                    "save_dc": 17,
                    "damage_formula": "6d10",
                    "damage_type": "piercing",
                    "half_on_success": True,
                    "source_excerpt": source_excerpt,
                    "rule_refs": ["srd2014.magic-items.arrow-of-slaying"],
                },
            },
        },
        {
            "id": "longbow",
            "name": "Longbow",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "ranged",
                "attack_ability": "dexterity",
                "damage_formula": "1d8",
                "damage_type": "piercing",
                "properties": ["ammunition", "heavy", "two_handed"],
                "normal_range_ft": 150,
                "long_range_ft": 600,
                "ammunition_item_id": "arrows",
            },
        },
    ]
    attacker["sheet"]["inventory"]["equipment_slots"]["main_hand"] = "longbow"
    attacker["derived"] = derive_character_sheet(attacker["sheet"])
    target = _actor("dragon")
    target["sheet"]["progression"]["species"] = "Huge dragon"
    target["derived"] = derive_character_sheet(target["sheet"])

    plan = preflight_attack(
        attacker,
        target,
        action={
            "weapon_id": "longbow",
            "ammunition_item_id": "dragon-slaying-arrows",
        },
        rules=resolution_context(
            {"edition": "2014", "fingerprint": "", "lock": [], "mechanics": []}
        ),
    )

    assert plan["ammunition_item_id"] == "dragon-slaying-arrows"
    assert plan["on_hit_effect"] == source_excerpt
    assert plan["ammunition_slaying"]["matched_groups"] == ["dragon"]
    assert "dnd5e.core.magic_ammunition.slaying" in {
        receipt["mechanic_id"] for receipt in plan["rule_receipts"]
    }


def test_slaying_ammunition_does_not_trigger_for_an_unrelated_target() -> None:
    attacker = _actor("archer")
    attacker["sheet"]["inventory"]["items"] = [
        {
            "id": "dragon-slaying-arrow",
            "name": "Arrow of dragon slaying",
            "kind": "ammunition",
            "quantity": 1,
            "mechanics": {
                "magic": True,
                "slaying": {
                    "target_groups": ["dragon"],
                    "save_ability": "constitution",
                    "save_dc": 17,
                    "damage_formula": "6d10",
                    "damage_type": "piercing",
                    "half_on_success": True,
                    "source_excerpt": (
                        "The target must make a DC 17 Constitution saving throw, "
                        "taking an extra 6d10 piercing damage on a failed save, or "
                        "half as much extra damage on a successful one."
                    ),
                    "rule_refs": ["srd2014.magic-items.arrow-of-slaying"],
                },
            },
        },
        {
            "id": "shortbow",
            "name": "Shortbow",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "ranged",
                "attack_ability": "dexterity",
                "damage_formula": "1d6",
                "damage_type": "piercing",
                "properties": ["ammunition", "two_handed"],
                "normal_range_ft": 80,
                "long_range_ft": 320,
            },
        },
    ]
    attacker["sheet"]["inventory"]["equipment_slots"]["main_hand"] = "shortbow"
    attacker["derived"] = derive_character_sheet(attacker["sheet"])
    target = _actor("giant")
    target["sheet"]["progression"]["species"] = "Huge giant"
    target["derived"] = derive_character_sheet(target["sheet"])

    plan = preflight_attack(
        attacker,
        target,
        action={
            "weapon_id": "shortbow",
            "ammunition_item_id": "dragon-slaying-arrow",
        },
    )

    assert plan["ammunition_slaying"] is None
    assert plan["on_hit_effect"] == ""


def test_unarmed_strike_remains_available_with_an_unusable_equipped_weapon() -> None:
    attacker = _actor("archer")
    attacker["sheet"]["inventory"]["items"] = [
        {"id": "arrows", "name": "Arrows", "kind": "ammunition", "quantity": 0},
        {
            "id": "longbow",
            "name": "Longbow",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "ranged",
                "attack_ability": "dexterity",
                "damage_formula": "1d8",
                "damage_type": "piercing",
                "normal_range_ft": 150,
                "long_range_ft": 600,
                "ammunition_item_id": "arrows",
            },
        },
    ]
    attacker["sheet"]["inventory"]["equipment_slots"]["main_hand"] = "longbow"
    attacker["derived"] = derive_character_sheet(attacker["sheet"])

    plan = preflight_attack(
        attacker,
        _actor("target"),
        action={"weapon_id": "unarmed-strike"},
    )

    assert plan["weapon_id"] == "unarmed-strike"
    assert plan["damage_expression"] == "1 + 3"


def test_positioned_ranged_attack_requires_recorded_range() -> None:
    attacker = _actor("archer")
    attacker["sheet"]["inventory"]["items"] = [
        {
            "id": "mystery-bow",
            "name": "Mystery Bow",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "ranged",
                "attack_ability": "dexterity",
                "damage_formula": "1d8",
                "damage_type": "piercing",
            },
        }
    ]
    attacker["sheet"]["inventory"]["equipment_slots"]["main_hand"] = "mystery-bow"
    attacker["derived"] = derive_character_sheet(attacker["sheet"])
    target = _actor("target")
    attacker["position"] = {"x": 0, "y": 0}
    target["position"] = {"x": 1, "y": 0}

    with pytest.raises(NeedsRulingError, match="no recorded range") as raised:
        preflight_attack(attacker, target, action={"weapon_id": "mystery-bow"})
    assert raised.value.missing == ("weapon.range:mystery-bow",)


def test_ranged_attack_has_close_combat_disadvantage() -> None:
    attacker = _actor("archer")
    target = _actor("target")
    attacker.update(
        initiative=20,
        tie_breaker=0,
        position={"x": 0, "y": 0},
        disposition="friendly",
    )
    target.update(
        initiative=10,
        tie_breaker=0,
        position={"x": 1, "y": 0},
        disposition="hostile",
    )
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "shortbow",
            "attack_type": "ranged",
            "attack_bonus": 4,
            "damage_expression": "1d6",
            "damage_type": "piercing",
            "range_ft": {"normal": 80, "long": 320},
        }
    ]
    encounter = _grid_encounter([attacker, target])

    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "shortbow"},
        encounter=encounter,
    )

    assert plan["disadvantage"] is True
    assert plan["close_combat_threat_ids"] == ["target"]
    assert "hostile_creature_within_5_ft" in plan["disadvantage_sources"]

    encounter["combatants"][1]["conditions"] = ["incapacitated"]
    safe = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "shortbow"},
        encounter=encounter,
    )
    assert safe["close_combat_threat_ids"] == []


def test_spell_attack_preflight_uses_source_card_and_spellcasting_override() -> None:
    attacker = _actor("caster")
    target = _actor("target")
    attacker["sheet"]["spellcasting"].update(
        ability="intelligence",
        attack_bonus_override=6,
    )
    attacker["sheet"]["content"]["spells"] = [
        {
            "id": "module.spell.scorching-ray",
            "name": "Scorching Ray",
            "level": 2,
            "definition": {
                "range": {"kind": "distance", "normal_ft": 60},
            },
            "resolution": {
                "kind": "spell_attack",
                "targeting": {"mode": "creature", "max_targets": 100},
                "attack": {
                    "mode": "ranged",
                    "count": {"base": 3, "per_slot_above": 1, "slot_base_level": 2},
                    "damage": {"base_dice": "2d6", "damage_type": "fire"},
                },
            },
        }
    ]
    attacker["sheet"] = validate_character_sheet(attacker["sheet"])
    attacker["derived"] = derive_character_sheet(attacker["sheet"])
    attacker.update(
        initiative=20,
        tie_breaker=0,
        position={"x": 0, "y": 0},
        disposition="friendly",
    )
    target.update(
        initiative=10,
        tie_breaker=0,
        position={"x": 5, "y": 0},
        disposition="hostile",
    )
    encounter = _grid_encounter([attacker, target])

    plan = preflight_spell_attack(
        attacker,
        target,
        spell_id="module.spell.scorching-ray",
        cast_level=2,
        encounter=encounter,
    )

    assert plan["kind"] == "spell_attack"
    assert plan["attack_bonus"] == 6
    assert plan["damage_expression"] == "2d6"
    assert plan["range"]["normal_ft"] == 60


def test_preserve_life_enforces_pool_half_hp_and_creature_type() -> None:
    cleric = _actor("cleric", hp=21)
    cleric["sheet"]["progression"] = {
        "level": 2,
        "classes": [{"name": "Cleric", "level": 2, "hit_die": 8}],
    }
    cleric["sheet"]["content"]["features"] = [
        {
            "id": ("dnd5e.content.srd2014.feature.life-domain-channel-divinity-preserve-life"),
            "name": "Channel Divinity: Preserve Life",
            "source_key": "Life Domain",
        }
    ]
    rogue = _actor("rogue", hp=17)["sheet"]
    rogue["combat"]["hp"]["value"] = 1
    rogue["conditions"] = ["unconscious", "stable", "prone"]
    cleric_target = cleric["sheet"]
    cleric_target["combat"]["hp"]["value"] = 7

    result = resolve_preserve_life_to_sheets(
        cleric["sheet"],
        {"rogue": rogue, "cleric": cleric_target},
        allocations=[
            {"target_id": "rogue", "amount": 7},
            {"target_id": "cleric", "amount": 3},
        ],
    )

    assert result["allocated"] == result["pool"] == 10
    assert result["sheets"]["rogue"]["combat"]["hp"]["value"] == 8
    assert result["sheets"]["rogue"]["conditions"] == ["prone"]
    assert result["sheets"]["cleric"]["combat"]["hp"]["value"] == 10
    with pytest.raises(CombatEngineError, match="above half"):
        resolve_preserve_life_to_sheets(
            cleric["sheet"],
            {"rogue": rogue},
            allocations=[{"target_id": "rogue", "amount": 8}],
        )
    undead = _actor("undead")["sheet"]
    undead["progression"]["species"] = "undead"
    with pytest.raises(CombatEngineError, match="Undead or Constructs"):
        resolve_preserve_life_to_sheets(
            cleric["sheet"],
            {"undead": undead},
            allocations=[{"target_id": "undead", "amount": 1}],
        )


def test_2024_preserve_life_starts_at_level_three_and_can_target_undead() -> None:
    cleric = _actor("cleric-2024", hp=24)["sheet"]
    cleric["edition"] = "2024"
    cleric["progression"] = {
        "level": 3,
        "classes": [{"name": "Cleric", "level": 3, "hit_die": 8}],
    }
    cleric["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2024.feature.life-domain-preserve-life",
            "name": "Preserve Life",
            "source_key": "Life Domain",
            "mechanic_refs": ["dnd5e.core.activity.preserve_life"],
        }
    ]
    undead = _actor("undead-2024", hp=20)["sheet"]
    undead["edition"] = "2024"
    undead["progression"]["species"] = "undead"
    undead["combat"]["hp"]["value"] = 1

    result = resolve_preserve_life_to_sheets(
        cleric,
        {"undead": undead},
        allocations=[{"target_id": "undead", "amount": 9}],
    )

    assert result["edition"] == "2024"
    assert result["pool"] == 15
    assert result["sheets"]["undead"]["combat"]["hp"]["value"] == 10

    cleric["progression"] = {
        "level": 2,
        "classes": [{"name": "Cleric", "level": 2, "hit_die": 8}],
    }
    with pytest.raises(CombatEngineError, match="at least 3 Cleric levels"):
        resolve_preserve_life_to_sheets(
            cleric,
            {"undead": undead},
            allocations=[{"target_id": "undead", "amount": 1}],
        )


def test_2024_turn_undead_applies_frightened_and_incapacitated_until_damaged() -> None:
    cleric = _actor("cleric-2024")
    cleric["sheet"]["edition"] = "2024"
    cleric["sheet"]["progression"] = {
        "level": 2,
        "classes": [{"name": "Cleric", "level": 2, "hit_die": 8}],
    }
    cleric["sheet"]["abilities"]["wisdom"]["score"] = 16
    cleric["sheet"]["spellcasting"]["ability"] = "wisdom"
    cleric["sheet"]["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2024.feature.cleric-channel-divinity",
            "name": "Channel Divinity",
            "source_key": "Cleric",
            "activation": {"type": "action", "cost": 1},
            "resource_key": "channel_divinity",
            "choices": {"options": ["Divine Spark", "Turn Undead"]},
            "mechanic_refs": ["dnd5e.core.activity.turn_undead"],
        }
    ]
    cleric["derived"] = derive_character_sheet(cleric["sheet"])
    undead = _actor("undead-2024")
    undead["sheet"]["edition"] = "2024"
    undead["sheet"]["progression"]["species"] = "undead"
    undead["derived"] = derive_character_sheet(undead["sheet"])

    resolved = resolve_turn_undead_to_sheets(
        cleric,
        {"undead": undead},
        rng=_SequenceRng(1),
    )

    target = resolved["sheets"]["undead"]
    assert resolved["edition"] == "2024"
    assert resolved["targets"][0]["conditions"] == [
        "frightened",
        "incapacitated",
    ]
    assert {"frightened", "incapacitated"} <= set(target["conditions"])
    damaged = apply_damage_to_sheet(target, amount=1, damage_type="radiant")
    assert "frightened" not in damaged["sheet"]["conditions"]
    assert "incapacitated" not in damaged["sheet"]["conditions"]


def test_2024_sear_undead_shares_one_roll_without_ending_the_turn_effect() -> None:
    cleric = _actor("cleric-2024")
    cleric["sheet"]["edition"] = "2024"
    cleric["sheet"]["progression"] = {
        "level": 5,
        "classes": [{"name": "Cleric", "level": 5, "hit_die": 8}],
    }
    cleric["sheet"]["abilities"]["wisdom"]["score"] = 16
    cleric["sheet"]["spellcasting"]["ability"] = "wisdom"
    cleric["sheet"]["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2024.feature.cleric-channel-divinity",
            "name": "Channel Divinity",
            "choices": {"options": ["Divine Spark", "Turn Undead"]},
            "mechanic_refs": ["dnd5e.core.activity.turn_undead"],
        },
        {
            "id": "dnd5e.content.srd2024.feature.cleric-sear-undead",
            "name": "Sear Undead",
            "mechanic_refs": ["dnd5e.core.activity.sear_undead"],
        },
    ]
    cleric["derived"] = derive_character_sheet(cleric["sheet"])
    undead = _actor("undead-2024", hp=100)
    undead["sheet"]["edition"] = "2024"
    undead["sheet"]["progression"]["species"] = "undead"
    undead["derived"] = derive_character_sheet(undead["sheet"])

    resolved = resolve_turn_undead_to_sheets(
        cleric,
        {"undead": undead},
        sear_undead=True,
        rng=_SequenceRng(2, 3, 4, 1),
    )

    assert resolved["sear_undead"] == {
        "expression": "3d8",
        "rolls": [2, 3, 4],
        "total": 9,
        "damage_type": "radiant",
        "does_not_end_turn_undead": True,
    }
    target_result = resolved["targets"][0]
    assert target_result["sear_damage"]["applied_amount"] == 9
    assert target_result["turned"] is True
    target = resolved["sheets"]["undead"]
    assert target["combat"]["hp"]["value"] == 91
    assert {"frightened", "incapacitated"} <= set(target["conditions"])
    assert (
        next(effect for effect in target["effects"] if effect["id"] == target_result["effect_id"])[
            "active"
        ]
        is True
    )


def test_2024_divine_spark_heals_or_deals_save_for_half_damage() -> None:
    cleric = _actor("cleric-2024", hp=30)
    cleric["sheet"]["edition"] = "2024"
    cleric["sheet"]["progression"] = {
        "level": 7,
        "classes": [{"name": "Cleric", "level": 7, "hit_die": 8}],
    }
    cleric["sheet"]["abilities"]["wisdom"]["score"] = 16
    cleric["sheet"]["spellcasting"]["ability"] = "wisdom"
    cleric["sheet"]["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2024.feature.cleric-channel-divinity",
            "name": "Channel Divinity",
            "source_key": "Cleric",
            "choices": {"options": ["Divine Spark", "Turn Undead"]},
            "mechanic_refs": [
                "dnd5e.core.activity.divine_spark",
                "dnd5e.core.activity.turn_undead",
            ],
        }
    ]
    cleric["derived"] = derive_character_sheet(cleric["sheet"])
    target = _actor("target", hp=30)
    target["sheet"]["combat"]["hp"]["value"] = 4
    target["sheet"]["abilities"]["constitution"]["score"] = 10
    target["derived"] = derive_character_sheet(target["sheet"])

    healed = resolve_divine_spark_to_sheet(
        cleric,
        target,
        mode="heal",
        rng=_SequenceRng(4, 5),
    )
    assert healed["expression"] == "2d8 + 3"
    assert healed["total"] == 12
    assert healed["healing"]["amount"] == 12
    assert healed["sheet"]["combat"]["hp"]["value"] == 16

    target["sheet"]["combat"]["hp"]["value"] = 30
    damaged = resolve_divine_spark_to_sheet(
        cleric,
        target,
        mode="damage",
        damage_type="radiant",
        rng=_SequenceRng(4, 5, 20),
    )
    assert damaged["total"] == 12
    assert damaged["save"]["success"] is True
    assert damaged["damage"]["applied_amount"] == 6
    assert damaged["sheet"]["combat"]["hp"]["value"] == 24

    target["sheet"]["conditions"] = ["dead"]
    with pytest.raises(CombatEngineError, match="dead creature"):
        resolve_divine_spark_to_sheet(
            cleric,
            target,
            mode="heal",
            rng=_SequenceRng(8, 8),
        )


def test_damage_condition_cleanup_preserves_other_active_effect_owners() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["conditions"] = ["turned", "unconscious"]
    actor["sheet"]["effects"] = [
        {
            "id": "turn-undead",
            "kind": "turn_undead",
            "active": True,
            "changes": [],
        },
        {
            "id": "other-turned-owner",
            "kind": "timed_conditions",
            "active": True,
            "changes": [{"path": "conditions", "mode": "add", "value": "turned"}],
        },
        {
            "id": "sleep",
            "kind": "timed_conditions",
            "active": True,
            "changes": [{"path": "conditions", "mode": "add", "value": "unconscious"}],
        },
    ]

    damaged = apply_damage_to_sheet(
        actor["sheet"],
        amount=1,
        damage_type="radiant",
    )

    assert set(damaged["sheet"]["conditions"]) == {"turned", "unconscious"}
    assert damaged["sheet"]["effects"][0]["active"] is False
    assert damaged["ended_effect_ids"] == ["turn-undead"]


def test_restrained_actor_can_spend_its_action_to_escape() -> None:
    first = _actor("first")
    second = _actor("second")
    first["initiative"] = 20
    second["initiative"] = 10
    encounter = end_turn(
        start_encounter([first, second], ruleset="2014"),
        actor_id_value="first",
    )
    acting = current_combatant(encounter)
    assert acting is not None
    acting["conditions"].append("restrained")

    assert "escape" in available_actions(encounter, "second")
    escaped = resolve_common_action(
        encounter,
        actor_id_value="second",
        action="escape",
        payload={"effect_id": "web"},
    )
    assert current_combatant(escaped)["turn_budget"]["main_action"] == 0


def test_attack_ends_the_specific_invisibility_spell_concentration() -> None:
    attacker = _actor("invisible-attacker")
    attacker["sheet"]["conditions"] = ["invisible"]
    attacker["sheet"]["effects"] = [
        {
            "id": "invisibility-effect",
            "name": "Concentrating: Invisibility",
            "kind": "concentration",
            "source": "spell.cast",
            "source_spell_id": "dnd5e.content.srd2014.spell.invisibility",
            "active": True,
            "concentration": True,
            "duration": {"period": "hour", "remaining": 1},
            "changes": [],
            "description": "",
        }
    ]
    target = _actor("target")
    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "unarmed-strike", "attack_mode": "melee"},
    )
    attack = roll_attack_action(plan=plan, rng=_SequenceRng(10, 10))

    updated_attacker, _updated_target, result = resolve_attack_damage(
        attacker,
        target,
        plan=plan,
        attack=attack,
    )

    assert result["ended_invisibility_effect_ids"] == ["invisibility-effect"]
    assert "invisible" not in updated_attacker["sheet"]["conditions"]
    effect = updated_attacker["sheet"]["effects"][0]
    assert effect["active"] is False
    assert effect["ended_reason"] == "actor_attacked"


def test_halfling_lucky_rerolls_only_one_natural_one_and_keeps_replacement() -> None:
    result = roll_d20(
        advantage=True,
        reroll_ones=True,
        rng=_SequenceRng(1, 7, 18),
    )
    assert result["rolls"] == [18, 7]
    assert result["natural"] == 18
    assert result["rerolls"] == [{"index": 0, "from": 1, "to": 18, "source": "halfling_lucky"}]
    assert result["roll_mode"] == "advantage"
    assert result["advantage_applied"] is True
    assert result["disadvantage_applied"] is False


def test_d20_result_audits_disadvantage_and_cancellation() -> None:
    disadvantaged = roll_d20(
        disadvantage=True,
        rng=_SequenceRng(18, 2),
    )
    assert disadvantaged["natural"] == 2
    assert disadvantaged["roll_mode"] == "disadvantage"
    assert disadvantaged["advantage_applied"] is False
    assert disadvantaged["disadvantage_applied"] is True

    cancelled = roll_d20(
        advantage=True,
        disadvantage=True,
        rng=_SequenceRng(12),
    )
    assert cancelled["rolls"] == [12]
    assert cancelled["roll_mode"] == "normal"
    assert cancelled["advantage_applied"] is False
    assert cancelled["disadvantage_applied"] is False


def test_halfling_lucky_applies_to_actor_checks_attacks_and_death_saves() -> None:
    halfling = _lightfoot()
    check = resolve_actor_check(
        halfling,
        kind="ability",
        ability="strength",
        dc=10,
        rng=_SequenceRng(1, 15),
    )
    assert check["natural"] == 15
    assert check["rerolls"][0]["source"] == "halfling_lucky"

    halfling["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "shortsword",
            "attack_type": "melee",
            "properties": ["finesse"],
            "attack_bonus": 0,
            "damage_expression": "1",
            "damage_type": "piercing",
        }
    ]
    target = _actor("target", ac=10)
    plan = preflight_attack(halfling, target, action={"weapon_id": "shortsword"})
    _, _, attack = resolve_attack_action(
        halfling,
        target,
        plan=plan,
        rng=_SequenceRng(1, 15, 1),
    )
    assert attack["hit"] is True
    assert attack["rerolls"][0]["to"] == 15

    death_sheet = halfling["sheet"]
    death_sheet["combat"]["hp"]["value"] = 0
    death_sheet["conditions"] = ["unconscious"]
    death = resolve_death_save_to_sheet(death_sheet, rng=_SequenceRng(1, 14))
    assert death["natural"] == 14
    assert death["failures"] == 0
    assert death["successes"] == 1


def test_damage_applies_resistance_and_vulnerability_in_order() -> None:
    actor = _actor("target", hp=20)
    actor["sheet"]["traits"]["resistances"] = ["fire"]
    actor["sheet"]["traits"]["vulnerabilities"] = ["fire"]
    result = apply_damage_to_sheet(actor["sheet"], amount=9, damage_type="fire")
    assert result["applied_amount"] == 8
    assert result["after_hp"] == 12
    assert result["adjustment"] == "resistant_and_vulnerable"


def test_attuned_magic_item_grants_damage_resistance() -> None:
    actor = _actor("target", hp=20)
    actor["sheet"]["inventory"]["items"] = [
        {
            "id": "ring-of-cold-resistance",
            "name": "Ring of cold resistance",
            "kind": "magic_item",
            "equipped": True,
            "equipped_slot": "ring_1",
            "attunement": "attuned",
            "mechanics": {
                "grants": {"resistances": ["cold"]},
            },
        }
    ]
    actor["sheet"]["inventory"]["equipment_slots"]["ring_1"] = "ring-of-cold-resistance"

    result = apply_damage_to_sheet(actor["sheet"], amount=9, damage_type="cold")

    assert result["applied_amount"] == 4
    assert result["adjustment"] == "resistant"
    assert result["defense_sources"] == ["magic_item:ring-of-cold-resistance"]

    actor["sheet"]["inventory"]["items"][0]["attunement"] = "required"
    unattuned = apply_damage_to_sheet(actor["sheet"], amount=9, damage_type="cold")
    assert unattuned["applied_amount"] == 9
    assert unattuned["defense_sources"] == []


def test_attack_preflight_and_resolution_keep_target_sheet_auditable() -> None:
    attacker = _actor("attacker")
    target = _actor("target", hp=10, ac=1)
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "sword",
            "attack_bonus": 99,
            "damage_expression": "1d4",
            "damage_type": "slashing",
        }
    ]
    plan = preflight_attack(
        attacker,
        target,
        action={
            "weapon_id": "sword",
            "attack_bonus": 1,
            "damage_expression": "999d999",
        },
    )
    assert plan["attack_bonus"] == 99
    assert plan["damage_expression"] == "1d4"
    _, updated_target, result = resolve_attack_action(
        attacker,
        target,
        plan=plan,
        rng=random.Random(2),
    )
    assert result["hit"] is True
    assert result["damage"]["after_hp"] < 10
    assert updated_target["sheet"]["combat"]["hp"]["value"] < 10


def test_attack_settles_each_source_bound_damage_type_and_surfaces_on_hit_ruling() -> None:
    attacker = _actor("attacker")
    target = _actor("target", hp=30, ac=1)
    target["sheet"]["traits"]["resistances"] = ["necrotic"]
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "silvered-skull-flail",
            "attack_type": "melee",
            "reach_ft": 5,
            "properties": [],
            "attack_bonus": 99,
            "damage_expression": "1d8",
            "damage_type": "bludgeoning",
            "additional_damage": [
                {
                    "damage_expression": "4d6",
                    "damage_type": "necrotic",
                    "source": "monster-statblock",
                }
            ],
            "on_hit_effect": "The target has disadvantage on specified saving throws.",
        }
    ]
    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "silvered-skull-flail"},
    )

    _, updated_target, result = resolve_attack_action(
        attacker,
        target,
        plan=plan,
        rng=_SequenceRng(19, 4, 3, 3, 3, 3),
    )

    assert result["damage"]["input_amount"] == 16
    assert result["damage"]["applied_amount"] == 10
    assert [part["damage_type"] for part in result["damage"]["roll_parts"]] == [
        "bludgeoning",
        "necrotic",
    ]
    assert result["damage"]["roll_parts"][1]["source"] == "monster-statblock"
    assert updated_target["sheet"]["combat"]["hp"]["value"] == 20
    assert result["on_hit_ruling"] == {
        "required": True,
        "effect": "The target has disadvantage on specified saving throws.",
        "default_resolver": "agent",
        "ruling_kind": "source_or_scene_fact",
    }


def test_effect_only_attack_surfaces_ruling_without_applying_fake_damage() -> None:
    attacker = _actor("attacker")
    target = _actor("target", hp=30, ac=1)
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "web",
            "attack_type": "ranged",
            "range_ft": {"normal": 30, "long": 60},
            "properties": [],
            "attack_bonus": 99,
            "damage_expression": "",
            "damage_type": "",
            "additional_damage": [],
            "on_hit_effect": "The target is restrained by webbing.",
        }
    ]
    plan = preflight_attack(attacker, target, action={"weapon_id": "web"})

    _, updated_target, result = resolve_attack_action(
        attacker,
        target,
        plan=plan,
        rng=_SequenceRng(19),
    )

    assert result["hit"] is True
    assert result["damage"] is None
    assert updated_target["sheet"]["combat"]["hp"]["value"] == 30
    assert result["on_hit_ruling"] == {
        "required": True,
        "effect": "The target is restrained by webbing.",
        "default_resolver": "agent",
        "ruling_kind": "source_or_scene_fact",
    }


def test_agent_compiled_reaction_defense_opens_after_hit_and_before_damage() -> None:
    attacker = _actor("attacker")
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "sword",
            "attack_type": "melee",
            "reach_ft": 5,
            "attack_bonus": 4,
            "damage_expression": "1d8 + 2",
            "damage_type": "slashing",
            "properties": [],
        }
    ]
    attacker.update(
        initiative=20,
        position={"x": 0, "y": 0},
        disposition="hostile",
    )
    target = _actor("target", hp=20, ac=15)
    target["sheet"]["inventory"]["items"] = [
        {
            "id": "scimitar",
            "name": "Scimitar",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d6",
                "damage_type": "slashing",
                "properties": ["finesse", "light"],
            },
        }
    ]
    target["sheet"]["inventory"]["equipment_slots"]["main_hand"] = "scimitar"
    activity = {
        "id": "source-bound-parry",
        "name": "Parry",
        "source_key": "addon:test/parry",
        "description": (
            "The defender adds 2 to its AC against one melee attack that would hit it."
        ),
        "activation": {"type": "reaction"},
        "choices": {
            "manual_ruling": {
                "kind": "descriptive_activity",
                "default_resolver": "agent",
                "source_excerpt": (
                    "The defender adds 2 to its AC against one melee attack that would hit it."
                ),
            }
        },
    }
    target["sheet"]["content"]["activities"] = [activity]
    target["sheet"] = validate_character_sheet(target["sheet"])
    activity = target["sheet"]["content"]["activities"][0]

    legacy_target = deepcopy(target)
    legacy_target["sheet"]["content"]["activities"][0]["choices"] = {
        "reaction_defense": {
            "kind": "armor_class_bonus",
            "bonus": 99,
            "attack_modes": ["melee"],
        }
    }
    legacy_target["derived"] = derive_character_sheet(legacy_target["sheet"])
    legacy_target.update(
        initiative=10,
        position={"x": 1, "y": 0},
        disposition="friendly",
    )
    legacy_encounter = _grid_encounter([attacker, legacy_target])
    legacy_plan = preflight_attack(
        attacker,
        legacy_target,
        action={"weapon_id": "sword"},
        encounter=legacy_encounter,
    )
    legacy_attack = roll_attack_action(plan=legacy_plan, rng=_SequenceRng(12))
    assert (
        available_attack_defenses(
            legacy_target,
            plan=legacy_plan,
            attack=legacy_attack,
            encounter=legacy_encounter,
        )
        == []
    )

    compiled = compile_resolution_plan(
        {
            "schema_version": 2,
            "id": "addon.test.parry-defense",
            "source_card_id": "source-bound-parry",
            "source_card_kind": "activity",
            "trigger": "attack.after_hit",
            "trigger_filter": {"hit": True},
            "slots": {},
            "steps": [
                {
                    "id": "defend",
                    "op": "attack.ac_bonus",
                    "args": {
                        "bonus": 2,
                        "attack_modes": ["melee"],
                        "requires_visible_attacker": True,
                        "requires_wielded_melee_weapon": True,
                    },
                }
            ],
            "citations": [
                {
                    "source": "addon:test/parry",
                    "source_ref": {"chunk_id": "parry-rule"},
                    "source_excerpt": activity["description"],
                }
            ],
        }
    )
    activity["resolution_plan"] = resolution_plan_template(compiled)
    activity["resolution_solution"] = build_content_solution(
        compiled,
        source_card=activity,
        application_id="content:activity:source-bound-parry",
        agent_ruling={
            "default_resolver": "agent",
            "ruling_kind": "agent_dm_adjudication",
            "decision": "Treat the quoted reaction as a contextual AC bonus.",
            "reason": "The exact card text states the bonus and triggering attack mode.",
        },
    )
    target["derived"] = derive_character_sheet(target["sheet"])
    target.update(
        initiative=10,
        position={"x": 1, "y": 0},
        disposition="friendly",
    )
    encounter = _grid_encounter([attacker, target])
    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "sword"},
        encounter=encounter,
    )
    attack = roll_attack_action(plan=plan, rng=_SequenceRng(12))
    assert attack["total"] == 16
    assert attack["hit"] is True
    defenses = available_attack_defenses(
        target,
        plan=plan,
        attack=attack,
        encounter=encounter,
    )
    assert len(defenses) == 1
    assert {
        key: defenses[0][key]
        for key in (
            "id",
            "name",
            "kind",
            "bonus",
            "projected_hit",
            "source_key",
            "rule_refs",
            "plan_id",
            "plan_fingerprint",
            "solution_version",
        )
    } == {
        "id": "source-bound-parry",
        "name": "Parry",
        "kind": "armor_class_bonus",
        "bonus": 2,
        "projected_hit": False,
        "source_key": "addon:test/parry",
        "rule_refs": [],
        "plan_id": "addon.test.parry-defense",
        "plan_fingerprint": compiled.fingerprint,
        "solution_version": 1,
    }
    assert defenses[0]["compiled_by"]["default_resolver"] == "agent"
    assert defenses[0]["citations"][0]["source_excerpt"] == activity["description"]
    defended = apply_attack_ac_bonus(
        attack,
        bonus=defenses[0]["bonus"],
        source_id=defenses[0]["id"],
    )
    _, updated_target, result = resolve_attack_damage(
        attacker,
        target,
        plan=plan,
        attack=defended,
    )
    assert result["hit"] is False
    assert result["damage"] is None
    assert updated_target["sheet"]["combat"]["hp"]["value"] == 20

    ranged_plan = {**plan, "attack_mode": "ranged", "melee_attack": False}
    assert (
        available_attack_defenses(
            target,
            plan=ranged_plan,
            attack=attack,
            encounter=encounter,
        )
        == []
    )


def test_dueling_style_adds_damage_only_for_one_equipped_melee_weapon() -> None:
    attacker = _actor("duelist")
    attacker["sheet"]["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2014.feature.fighter-fighting-style",
            "name": "Fighting Style",
            "source_key": "Fighter",
            "choices": {"option": "Dueling"},
        }
    ]
    attacker["sheet"]["inventory"]["items"] = [
        {
            "id": "longsword",
            "name": "Longsword",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "category": "martial",
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d8",
                "damage_type": "slashing",
                "properties": ["versatile"],
            },
        }
    ]
    attacker["sheet"]["inventory"]["equipment_slots"]["main_hand"] = "longsword"
    attacker["derived"] = derive_character_sheet(attacker["sheet"])
    target = _actor("target", hp=20, ac=1)
    plan = preflight_attack(attacker, target, action={"weapon_id": "longsword"})
    assert plan["damage_expression"] == "1d8 + 3 + 2"
    assert plan["damage_modifiers"] == [{"source": "Fighting Style: Dueling", "value": 2}]


def test_qualified_multiattack_preserves_recorded_weapon_composition() -> None:
    captain = _actor("captain", hp=65)
    captain["sheet"]["inventory"]["items"] = [
        {
            "id": "scimitar",
            "name": "Scimitar",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d6",
                "damage_type": "slashing",
                "properties": ["finesse", "light"],
            },
        },
        {
            "id": "dagger",
            "name": "Dagger",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "off_hand",
            "mechanics": {
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "1d4",
                "damage_type": "piercing",
                "properties": ["finesse", "light", "thrown"],
                "thrown_normal_range_ft": 20,
                "thrown_long_range_ft": 60,
            },
        },
    ]
    captain["sheet"]["inventory"]["equipment_slots"].update(
        {"main_hand": "scimitar", "off_hand": "dagger"}
    )
    captain["sheet"]["content"]["activities"] = [
        {
            "id": "bandit-captain-multiattack",
            "name": "Multiattack (Armed Form Only)",
            "source_key": "Bandit Captain",
            "activation": {"type": "action"},
            "mechanic_refs": ["dnd5e.core.action.multiattack_choice"],
            "choices": {
                "multiattack_options": [
                    {
                        "id": "melee",
                        "attacks": [
                            {"weapon_id": "scimitar", "attack_mode": "melee", "count": 2},
                            {"weapon_id": "dagger", "attack_mode": "melee", "count": 1},
                        ],
                    },
                    {
                        "id": "ranged",
                        "attacks": [{"weapon_id": "dagger", "attack_mode": "ranged", "count": 2}],
                    },
                ]
            },
        }
    ]
    captain["derived"] = derive_character_sheet(captain["sheet"])
    assert captain["derived"]["attacks_per_action"] == 1
    assert {item["id"] for item in captain["derived"]["multiattack_options"]} == {
        "melee",
        "ranged",
    }
    target = _actor("target", hp=65)
    captain.update(
        initiative=20,
        tie_breaker=0,
        position={"x": 0, "y": 0},
        disposition="hostile",
    )
    target.update(
        initiative=10,
        tie_breaker=0,
        position={"x": 5, "y": 0},
        disposition="friendly",
    )
    encounter = start_encounter([captain, target])

    encounter, first = pay_attack_action(
        encounter,
        captain,
        weapon_id="scimitar",
        attack_mode="melee",
        multiattack_option_id="melee",
    )
    assert first["attack_count"] == 3
    encounter, _ = pay_attack_action(encounter, captain, weapon_id="scimitar", attack_mode="melee")
    with pytest.raises(ValueError, match="remaining Multiattack"):
        pay_attack_action(encounter, captain, weapon_id="scimitar", attack_mode="melee")
    encounter, _ = pay_attack_action(encounter, captain, weapon_id="dagger", attack_mode="melee")
    current = encounter["combatants"][encounter["turn_index"]]
    assert current["turn_budget"]["attack_budget"] == 0
    assert "multiattack" not in current.get("turn_flags", {})

    ordinary = start_encounter([captain, target])
    ordinary, payment = pay_attack_action(
        ordinary, captain, weapon_id="scimitar", attack_mode="melee"
    )
    assert payment["kind"] == "attack_action"
    assert payment["attack_count"] == 1
    assert ordinary["combatants"][0]["turn_budget"]["attack_budget"] == 0


def test_mixed_multiattack_pays_one_weapon_and_one_source_activity() -> None:
    attacker = _actor("intellect-devourer", hp=21)
    attacker["sheet"]["inventory"]["items"] = [
        {
            "id": "claws",
            "name": "Claws",
            "kind": "weapon",
            "equipped": True,
            "equipped_slot": "main_hand",
            "mechanics": {
                "attack_type": "melee",
                "attack_ability": "dexterity",
                "damage_formula": "2d4",
                "damage_type": "slashing",
                "properties": [],
            },
        }
    ]
    attacker["sheet"]["inventory"]["equipment_slots"]["main_hand"] = "claws"
    attacker["sheet"]["content"]["activities"] = [
        {
            "id": "multiattack-activity",
            "name": "Multiattack",
            "activation": {"type": "action"},
            "mechanic_refs": ["dnd5e.core.action.multiattack_choice"],
            "choices": {
                "multiattack_options": [
                    {
                        "id": "claws-and-devour",
                        "attacks": [
                            {
                                "weapon_id": "claws",
                                "attack_mode": "melee",
                                "count": 1,
                            }
                        ],
                        "activities": [
                            {
                                "activity_id": "devour-intellect-action",
                                "count": 1,
                            }
                        ],
                    }
                ]
            },
        },
        {
            "id": "devour-intellect-action",
            "name": "Devour Intellect",
            "activation": {"type": "action"},
        },
    ]
    attacker["derived"] = derive_character_sheet(attacker["sheet"])
    target = _actor("target")
    attacker.update(initiative=20, tie_breaker=0)
    target.update(initiative=10, tie_breaker=0)
    encounter = start_encounter([attacker, target])

    encounter, attack_payment = pay_attack_action(
        encounter,
        attacker,
        weapon_id="claws",
        attack_mode="melee",
        multiattack_option_id="claws-and-devour",
    )
    encounter, activity_payment = pay_multiattack_activity(
        encounter,
        attacker["id"],
        activity_id="devour-intellect-action",
    )

    assert attack_payment["attack_count"] == 2
    assert activity_payment == {
        "kind": "multiattack_activity_followup",
        "activity_id": "devour-intellect-action",
        "option_id": "claws-and-devour",
    }
    current = encounter["combatants"][encounter["turn_index"]]
    assert current["turn_budget"]["main_action"] == 0
    assert current["turn_budget"]["attack_budget"] == 0
    assert "multiattack" not in current.get("turn_flags", {})


def test_unstructured_multiattack_does_not_block_an_ordinary_weapon_attack() -> None:
    attacker = _actor("attacker")
    attacker["sheet"]["content"]["activities"] = [
        {
            "id": "unresolved-multiattack",
            "name": "Multiattack",
            "activation": {"type": "action"},
            "description": "The actor attacks and uses a descriptive command.",
            "mechanic_refs": ["dnd5e.core.action.multiattack_choice"],
        }
    ]
    attacker["derived"] = derive_character_sheet(attacker["sheet"])
    target = _actor("target")
    participants = [
        {**attacker, "initiative": 20, "tie_breaker": 0},
        {**target, "initiative": 10, "tie_breaker": 1},
    ]
    encounter = start_encounter(participants)

    encounter, payment = pay_attack_action(
        encounter,
        attacker,
        weapon_id="unarmed-strike",
        attack_mode="melee",
    )
    assert payment["kind"] == "attack_action"
    assert payment["attack_count"] == 1

    with pytest.raises(CombatEngineError, match="no structured options"):
        pay_attack_action(
            start_encounter(participants),
            attacker,
            weapon_id="unarmed-strike",
            attack_mode="melee",
            multiattack_option_id="invented",
        )


def test_thrown_weapon_requires_explicit_ranged_attack_mode() -> None:
    attacker = _actor("thrower")
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "dagger",
            "attack_type": "melee",
            "reach_ft": 5,
            "attack_bonus": 5,
            "damage_expression": "1d4 + 3",
            "damage_type": "piercing",
            "properties": ["finesse", "light", "thrown"],
            "thrown_range_ft": {"normal": 20, "long": 60},
        }
    ]
    target = _actor("target")
    attacker["position"] = {"x": 0, "y": 0}
    target["position"] = {"x": 10, "y": 0}

    with pytest.raises(ValueError, match="outside melee reach"):
        preflight_attack(attacker, target, action={"weapon_id": "dagger"})
    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "dagger", "attack_mode": "ranged"},
    )
    assert plan["attack_mode"] == "ranged"
    assert plan["melee_attack"] is False
    assert plan["range"]["normal_ft"] == 20


def test_zero_reach_weapon_can_attack_only_a_target_in_the_same_space() -> None:
    attacker = _actor("swarm")
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "bites",
            "attack_type": "melee",
            "reach_ft": 0,
            "attack_bonus": 2,
            "damage_expression": "2d6",
            "damage_type": "piercing",
            "properties": [],
        }
    ]
    target = _actor("target")
    attacker["position"] = {"x": 0, "y": 0}
    target["position"] = {"x": 0, "y": 0}

    plan = preflight_attack(attacker, target, action={"weapon_id": "bites"})
    assert plan["weapon_reach_ft"] == 0
    assert plan["range"]["normal_ft"] == 0
    assert plan["range"]["distance_ft"] == 0

    target["position"] = {"x": 1, "y": 0}
    with pytest.raises(ValueError, match="outside melee reach"):
        preflight_attack(attacker, target, action={"weapon_id": "bites"})


def test_preflight_stops_on_unresolved_rules() -> None:
    attacker = _actor("attacker")
    target = _actor("target")
    attacker["derived"]["unresolved_rules"] = ["effect:unknown"]
    with pytest.raises(NeedsRulingError):
        preflight_attack(attacker, target, action={"attack_bonus": 3})


def test_encounter_uses_actor_references_and_turn_budget() -> None:
    encounter = start_encounter([_actor("a"), _actor("b")], rng=random.Random(1))
    assert encounter["active"] is True
    assert {item["actor_id"] for item in encounter["combatants"]} == {"a", "b"}
    assert encounter["combatants"][0]["turn_budget"]["reaction"] == 1


def test_encounter_validates_every_participant_before_rolling_initiative() -> None:
    valid = _actor("valid")
    invalid = _actor("invalid")
    invalid["sheet"]["combat"]["exhaustion"] = 6
    rng = _SequenceRng(7)

    with pytest.raises(CombatEngineError, match="exhaustion level 6"):
        start_encounter([valid, invalid], rng=rng)

    assert rng.values == [7]


def test_initiative_ties_require_explicit_tie_breakers() -> None:
    with pytest.raises(NeedsRulingError, match="tie_breaker") as npc_tie:
        start_encounter([{**_actor("a"), "initiative": 10}, {**_actor("b"), "initiative": 10}])
    assert npc_tie.value.ruling_kind == "agent_dm_adjudication"

    with pytest.raises(NeedsRulingError, match="tie_breaker") as pc_tie:
        start_encounter(
            [
                {**_actor("pc-a"), "character_type": "pc", "initiative": 10},
                {**_actor("pc-b"), "character_type": "pc", "initiative": 10},
            ]
        )
    assert pc_tie.value.ruling_kind == "player_owned_choice"


@pytest.mark.parametrize("ruleset", ["2014", "2024"])
def test_engine_rolled_initiative_ties_require_the_rules_owner_to_choose(
    ruleset: str,
) -> None:
    participants = [
        {**_actor("pc-a"), "character_type": "pc"},
        {**_actor("pc-b"), "character_type": "pc"},
    ]

    with pytest.raises(NeedsRulingError, match="tie_breaker") as tied:
        start_encounter(participants, ruleset=ruleset, rng=_SequenceRng(10, 10))

    assert tied.value.ruling_kind == "player_owned_choice"

    settled = start_encounter(
        [
            {**participants[0], "tie_breaker": 1},
            {**participants[1], "tie_breaker": 0},
        ],
        ruleset=ruleset,
        rng=_SequenceRng(10, 10),
    )
    assert [item["actor_id"] for item in settled["combatants"]] == ["pc-b", "pc-a"]


@pytest.mark.parametrize(
    ("participants", "ruling_kind"),
    [
        (
            [
                {**_actor("pc-a"), "character_type": "pc"},
                {**_actor("pc-b"), "character_type": "pc"},
            ],
            "player_owned_choice",
        ),
        ([_actor("npc-a"), _actor("npc-b")], "agent_dm_adjudication"),
        (
            [{**_actor("pc"), "character_type": "pc"}, _actor("npc")],
            "agent_dm_adjudication",
        ),
    ],
)
def test_initiative_ties_reject_duplicate_explicit_tie_breakers(
    participants: list[dict[str, object]],
    ruling_kind: str,
) -> None:
    with pytest.raises(NeedsRulingError, match="unique tie_breaker") as engine_rolled:
        start_encounter(participants, rng=_SequenceRng(10, 10))
    assert engine_rolled.value.ruling_kind == ruling_kind

    tied = [
        {**participant, "initiative": 10, "tie_breaker": 0}
        for participant in participants
    ]

    with pytest.raises(NeedsRulingError, match="unique tie_breaker") as raised:
        start_encounter(tied)

    assert raised.value.ruling_kind == ruling_kind


def test_initiative_tie_groups_request_player_choices_before_dm_choices() -> None:
    participants = [
        {**_actor("pc-a"), "character_type": "pc", "initiative": 20},
        {**_actor("pc-b"), "character_type": "pc", "initiative": 20},
        {**_actor("npc-a"), "initiative": 10},
        {**_actor("npc-b"), "initiative": 10},
    ]

    with pytest.raises(NeedsRulingError, match="pc-a, pc-b") as player_choice:
        start_encounter(participants)
    assert player_choice.value.ruling_kind == "player_owned_choice"

    player_settled = [
        {**participant, "tie_breaker": index}
        if participant.get("character_type") == "pc"
        else participant
        for index, participant in enumerate(participants)
    ]
    with pytest.raises(NeedsRulingError, match="npc-a, npc-b") as dm_choice:
        start_encounter(player_settled)
    assert dm_choice.value.ruling_kind == "agent_dm_adjudication"


def test_half_cover_uses_the_rules_ac_bonus() -> None:
    attacker = _actor("attacker")
    target = _actor("target", ac=10)
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "sword",
            "attack_bonus": 5,
            "damage_expression": "1",
            "damage_type": "slashing",
        }
    ]
    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "sword", "context": {"cover": {"degree": "half"}}},
    )
    assert plan["target_ac"] == 12
    assert plan["cover"] == {
        "degree": "half",
        "armor_class_bonus": 2,
    }


def test_cover_uses_only_the_rules_defined_degrees() -> None:
    attacker = _actor("attacker")
    target = _actor("target", ac=10)
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "sword",
            "attack_bonus": 5,
            "damage_expression": "1",
            "damage_type": "slashing",
        }
    ]

    three_quarters = preflight_attack(
        attacker,
        target,
        action={
            "weapon_id": "sword",
            "context": {"cover": {"degree": "three_quarters"}},
        },
    )
    assert three_quarters["target_ac"] == 15
    assert three_quarters["cover"] == {
        "degree": "three_quarters",
        "armor_class_bonus": 5,
    }
    _updated_attacker, _updated_target, resolved = resolve_attack_action(
        attacker,
        target,
        plan=three_quarters,
        rng=random.Random(0),
    )
    assert resolved["cover"] == three_quarters["cover"]

    with pytest.raises(CombatEngineError, match="total cover"):
        preflight_attack(
            attacker,
            target,
            action={
                "weapon_id": "sword",
                "context": {"cover": {"degree": "total"}},
            },
        )
    with pytest.raises(CombatEngineError, match="rules-defined degree"):
        preflight_attack(
            attacker,
            target,
            action={
                "weapon_id": "sword",
                "context": {"cover": {"degree": "half", "ac_bonus": 9}},
            },
        )
    with pytest.raises(CombatEngineError, match="cover degree"):
        preflight_attack(
            attacker,
            target,
            action={
                "weapon_id": "sword",
                "context": {"cover": {"degree": "nine_tenths"}},
            },
        )


def test_help_grants_and_then_consumes_attack_advantage() -> None:
    attacker = _actor("attacker")
    helper = _actor("helper")
    target = _actor("target")
    for actor in (attacker, helper, target):
        actor["initiative"] = {"attacker": 20, "helper": 15, "target": 10}[actor["id"]]
        actor["tie_breaker"] = 0
    attacker["position"] = {"x": 0, "y": 0}
    helper["position"] = {"x": 1, "y": 0}
    target["position"] = {"x": 1, "y": 0}
    attacker["disposition"] = helper["disposition"] = "friendly"
    target["disposition"] = "hostile"
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {"item_id": "sword", "attack_bonus": 5, "damage_expression": "1", "damage_type": "slashing"}
    ]
    encounter = _grid_encounter([attacker, helper, target])
    encounter["combatants"][1]["turn_flags"] = {"helping": {"target_id": "attacker"}}
    plan = preflight_attack(attacker, target, action={"weapon_id": "sword"}, encounter=encounter)
    assert plan["helped_by"] == "helper"
    assert "help" in plan["advantage_sources"]


def test_next_attack_advantage_uses_active_target_effect() -> None:
    attacker = _actor("attacker")
    target = _actor("target")
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "sword",
            "attack_bonus": 5,
            "damage_expression": "1",
            "damage_type": "slashing",
        }
    ]
    attacker.update(
        initiative=20,
        tie_breaker=0,
        position={"x": 0, "y": 0},
        disposition="friendly",
    )
    target.update(
        initiative=10,
        tie_breaker=0,
        position={"x": 1, "y": 0},
        disposition="hostile",
    )
    encounter = _grid_encounter([attacker, target])
    encounter["ongoing_effects"] = [
        {
            "id": "guiding-bolt-mark",
            "kind": "next_attack_advantage",
            "source_actor_id": "cleric",
            "target_id": "target",
            "active": True,
        }
    ]

    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "sword"},
        encounter=encounter,
    )

    assert plan["advantage"] is True
    assert plan["next_attack_advantage_effect_id"] == "guiding-bolt-mark"
    assert "guiding-bolt-mark" in plan["advantage_sources"]


def test_sneak_attack_requires_card_feature_and_records_critical_bonus_damage() -> None:
    rogue = _rogue()
    ally = _actor("ally")
    target = _actor("target", hp=30, ac=1)
    rogue.update(initiative=20, tie_breaker=0, position={"x": 0, "y": 0}, disposition="friendly")
    ally.update(initiative=15, tie_breaker=0, position={"x": 1, "y": 0}, disposition="friendly")
    target.update(initiative=10, tie_breaker=0, position={"x": 1, "y": 0}, disposition="hostile")
    encounter = _grid_encounter([rogue, ally, target])

    plan = preflight_attack(
        rogue,
        target,
        action={"weapon_id": "dagger", "use_sneak_attack": True},
        encounter=encounter,
    )
    assert plan["sneak_attack"]["expression"] == "1d6"
    assert plan["sneak_attack"]["eligibility"] == "adjacent_enemy"

    _, updated_target, result = resolve_attack_action(
        rogue,
        target,
        plan=plan,
        rng=random.Random(5),
    )
    assert result["weapon_id"] == "dagger"
    assert result["attack_mode"] == "melee"
    assert result["critical"] is True
    assert result["sneak_attack"]["used"] is True
    assert result["sneak_attack"]["rolled_expression"] == "2d6"
    assert result["damage"]["sneak_attack"] == result["sneak_attack"]
    assert updated_target["sheet"]["combat"]["hp"]["value"] < 29


def test_sneak_attack_enforces_once_per_turn_weapon_and_disadvantage_boundaries() -> None:
    rogue = _rogue()
    ally = _actor("ally")
    target = _actor("target", ac=1)
    rogue.update(initiative=20, tie_breaker=0, position={"x": 0, "y": 0}, disposition="friendly")
    ally.update(initiative=15, tie_breaker=0, position={"x": 1, "y": 0}, disposition="friendly")
    target.update(initiative=10, tie_breaker=0, position={"x": 1, "y": 0}, disposition="hostile")
    encounter = _grid_encounter([rogue, ally, target])
    turn_token = f"1:0:{rogue['id']}"
    encounter["combatants"][0]["turn_flags"] = {"sneak_attack_turn_token": turn_token}
    with pytest.raises(Exception, match="already been used"):
        preflight_attack(
            rogue,
            target,
            action={"weapon_id": "dagger", "use_sneak_attack": True},
            encounter=encounter,
        )

    encounter["combatants"][0].pop("turn_flags")
    with pytest.raises(Exception, match="disadvantage"):
        preflight_attack(
            rogue,
            target,
            action={
                "weapon_id": "dagger",
                "use_sneak_attack": True,
                "context": {"disadvantage": True},
            },
            encounter=encounter,
        )

    rogue["derived"]["inventory"]["weapon_attacks"][0]["properties"] = ["light"]
    with pytest.raises(Exception, match="finesse or ranged"):
        preflight_attack(
            rogue,
            target,
            action={
                "weapon_id": "dagger",
                "use_sneak_attack": True,
                "context": {"advantage": True},
            },
            encounter=encounter,
        )


def test_multi_damage_preserves_types_and_massive_damage() -> None:
    actor = _actor("target", hp=10)
    result = apply_damage_parts_to_sheet(
        actor["sheet"],
        [{"amount": 4, "damage_type": "fire"}, {"amount": 10, "damage_type": "cold"}],
    )
    assert len(result["parts"]) == 2
    assert "unconscious" in result["sheet"]["conditions"]
    assert "dead" not in result["sheet"]["conditions"]


@pytest.mark.parametrize("ruleset", ["2014", "2024"])
def test_multi_damage_kills_target_that_does_not_use_death_saves(ruleset: str) -> None:
    actor = _actor("monster", hp=5)

    result = apply_damage_parts_to_sheet(
        actor["sheet"],
        [{"amount": 5, "damage_type": "radiant"}],
        ruleset=ruleset,
        death_saves=False,
    )

    assert {"dead", "prone"} <= set(result["sheet"]["conditions"])
    assert "unconscious" not in result["sheet"]["conditions"]


def test_simultaneous_damage_parts_create_one_concentration_dc_from_total() -> None:
    actor = _actor("target", hp=30)
    actor["sheet"]["effects"] = [
        {
            "id": "bless",
            "name": "Bless",
            "kind": "concentration",
            "source": "spell.cast",
            "source_spell_id": "bless",
            "active": True,
            "concentration": True,
            "duration": {"period": "round", "remaining": 10},
            "changes": [],
            "description": "",
        }
    ]
    result = apply_damage_parts_to_sheet(
        actor["sheet"],
        [{"amount": 12, "damage_type": "fire"}, {"amount": 12, "damage_type": "cold"}],
    )
    assert result["concentration"]["dc"] == 12
    assert result["after_hp"] == 6


def test_zero_hp_ends_concentration_with_a_schema_valid_audit_reason() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["effects"] = [
        {
            "id": "bless",
            "name": "Bless",
            "kind": "concentration",
            "active": True,
            "concentration": True,
            "duration": {"period": "minute", "remaining": 1},
            "changes": [],
            "description": "",
        }
    ]

    damaged = apply_damage_to_sheet(
        validate_character_sheet(actor["sheet"]),
        amount=10,
        damage_type="fire",
    )

    assert damaged["ended_effect_ids"] == ["bless"]
    effect = damaged["sheet"]["effects"][0]
    assert effect["active"] is False
    assert effect["ended_reason"] == "unconscious"
    assert validate_character_sheet(damaged["sheet"])["effects"][0] == effect


def test_zero_hp_ends_invisibility_concentration_and_clears_condition() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["conditions"] = ["invisible"]
    actor["sheet"]["content"]["spells"] = [
        {
            "id": "dnd5e.content.srd2014.spell.invisibility",
            "name": "Invisibility",
            "level": 2,
        }
    ]
    actor["sheet"]["effects"] = [
        {
            "id": "invisibility",
            "name": "Invisibility",
            "kind": "concentration",
            "source_spell_id": "dnd5e.content.srd2014.spell.invisibility",
            "active": True,
            "concentration": True,
            "duration": {"period": "hour", "remaining": 1},
            "changes": [],
        }
    ]

    damaged = apply_damage_to_sheet(
        validate_character_sheet(actor["sheet"]),
        amount=10,
        damage_type="fire",
    )

    assert damaged["ended_effect_ids"] == ["invisibility"]
    assert "invisible" not in damaged["sheet"]["conditions"]


def test_failed_concentration_save_records_why_the_effect_ended() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["effects"] = [
        {
            "id": "bless",
            "name": "Bless",
            "kind": "concentration",
            "active": True,
            "concentration": True,
            "duration": {"period": "minute", "remaining": 1},
        }
    ]

    resolved = apply_concentration_result(
        validate_character_sheet(actor["sheet"]),
        effect_ids=["bless"],
        success=False,
    )

    assert resolved["effects"][0]["active"] is False
    assert resolved["effects"][0]["ended_reason"] == "failed_concentration_save"
    validate_character_sheet(resolved)


def test_failed_concentration_save_clears_invisibility_condition() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["conditions"] = ["invisible"]
    actor["sheet"]["content"]["spells"] = [
        {"id": "invisibility", "name": "Invisibility", "level": 2}
    ]
    actor["sheet"]["effects"] = [
        {
            "id": "invisibility",
            "name": "Invisibility",
            "kind": "concentration",
            "source_spell_id": "invisibility",
            "active": True,
            "concentration": True,
            "duration": {"period": "hour", "remaining": 1},
            "changes": [],
        }
    ]

    resolved = apply_concentration_result(
        validate_character_sheet(actor["sheet"]),
        effect_ids=["invisibility"],
        success=False,
    )

    assert "invisible" not in resolved["conditions"]
    assert resolved["effects"][0]["ended_reason"] == "failed_concentration_save"


@pytest.mark.parametrize(
    "condition",
    ["incapacitated", "paralyzed", "petrified", "stunned", "unconscious"],
)
def test_incapacitating_conditions_end_concentration(condition: str) -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["conditions"] = [condition]
    actor["sheet"]["effects"] = [
        {
            "id": "bless",
            "name": "Bless",
            "kind": "concentration",
            "active": True,
            "concentration": True,
            "duration": {"period": "minute", "remaining": 1},
            "changes": [],
        }
    ]

    ended = end_concentration_for_incapacitating_conditions(actor["sheet"])

    assert ended == ["bless"]
    assert actor["sheet"]["effects"][0]["active"] is False
    assert actor["sheet"]["effects"][0]["ended_reason"] == "incapacitated"


def test_same_type_simultaneous_parts_round_resistance_only_once() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["traits"]["resistances"] = ["fire"]
    result = apply_damage_parts_to_sheet(
        actor["sheet"],
        [{"amount": 1, "damage_type": "fire"}, {"amount": 1, "damage_type": "fire"}],
    )
    assert result["applied_amount"] == 1
    assert result["after_hp"] == 9
    assert len(result["parts"]) == 1


def test_critical_multi_part_damage_at_zero_causes_two_failures_once() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["combat"]["hp"]["value"] = 0
    actor["sheet"]["conditions"] = ["prone", "unconscious"]
    result = apply_damage_parts_to_sheet(
        actor["sheet"],
        [{"amount": 1, "damage_type": "fire"}, {"amount": 1, "damage_type": "cold"}],
        critical=True,
    )
    assert result["sheet"]["combat"]["death_saves"]["failures"] == 2


@pytest.mark.parametrize(
    (
        "temp_hp",
        "critical",
        "stable",
        "immune",
        "expected_absorbed",
        "expected_hp_damage",
        "expected_failures",
        "expected_temp_hp",
    ),
    [
        (10, False, False, False, 5, 0, 1, 5),
        (10, True, False, False, 5, 0, 2, 5),
        (10, False, True, False, 5, 0, 1, 5),
        (2, False, False, False, 2, 3, 1, 0),
        (10, False, True, True, 0, 0, 0, 10),
    ],
)
def test_damage_at_zero_uses_adjusted_damage_for_death_failures(
    temp_hp: int,
    critical: bool,
    stable: bool,
    immune: bool,
    expected_absorbed: int,
    expected_hp_damage: int,
    expected_failures: int,
    expected_temp_hp: int,
) -> None:
    actor = _actor("target", hp=20)
    actor["sheet"]["combat"]["hp"].update(value=0, temp=temp_hp)
    actor["sheet"]["conditions"] = [
        "prone",
        "unconscious",
        *(["stable"] if stable else []),
    ]
    if immune:
        actor["sheet"]["traits"]["immunities"] = ["fire"]

    result = apply_damage_to_sheet(
        actor["sheet"],
        amount=5,
        damage_type="fire",
        critical=critical,
    )

    assert result["absorbed_temp"] == expected_absorbed
    assert result["hp_damage"] == expected_hp_damage
    assert result["after_temp"] == expected_temp_hp
    assert result["sheet"]["combat"]["death_saves"]["failures"] == expected_failures
    assert ("stable" in result["sheet"]["conditions"]) is (stable and immune)


@pytest.mark.parametrize(
    ("amount", "resistant", "immune", "expected_applied", "expected_dead"),
    [
        (20, False, False, 20, True),
        (40, True, False, 20, True),
        (38, True, False, 19, False),
        (100, False, True, 0, False),
    ],
)
def test_damage_at_zero_uses_pre_temp_damage_for_instant_death(
    amount: int,
    resistant: bool,
    immune: bool,
    expected_applied: int,
    expected_dead: bool,
) -> None:
    actor = _actor("target", hp=20)
    actor["sheet"]["combat"]["hp"].update(value=0, temp=10)
    actor["sheet"]["conditions"] = ["prone", "unconscious"]
    if resistant:
        actor["sheet"]["traits"]["resistances"] = ["fire"]
    if immune:
        actor["sheet"]["traits"]["immunities"] = ["fire"]

    result = apply_damage_to_sheet(
        actor["sheet"],
        amount=amount,
        damage_type="fire",
    )

    assert result["applied_amount"] == expected_applied
    assert result["massive_damage"] is expected_dead
    assert ("dead" in result["sheet"]["conditions"]) is expected_dead
    assert result["sheet"]["combat"]["death_saves"]["failures"] == (
        0 if expected_dead or immune else 1
    )


def test_damage_at_zero_equal_to_maximum_causes_instant_death() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["combat"]["hp"]["value"] = 0
    actor["sheet"]["conditions"] = ["unconscious"]
    result = apply_damage_to_sheet(actor["sheet"], amount=10, damage_type="force")
    assert "dead" in result["sheet"]["conditions"]
    assert result["sheet"]["combat"]["death_saves"]["failures"] == 0


def _standard_relentless_endurance_feature(*, mechanic_ref: bool = True) -> dict:
    return {
        "id": "srd2014-half-orc-relentless-endurance",
        "name": "Relentless Endurance",
        "source_key": "Half-Orc",
        "description": "Drop to 1 hit point instead.",
        "activation": {"type": "passive"},
        "uses": {
            "label": "Relentless Endurance",
            "value": 1,
            "max": 1,
            "recovers_on": "long_rest",
            "source_key": "Half-Orc",
            "slot_level": 0,
            "unlimited": False,
        },
        "choices": {
            "source_trait": {
                "kind": "relentless_endurance",
                "trigger": "reduced_to_zero_not_killed_outright",
                "result_hp": 1,
                "automatic": True,
                "source_excerpt": "Drop to 1 hit point instead.",
            }
        },
        "mechanic_refs": ([CORE_RELENTLESS_ENDURANCE_MECHANIC_ID] if mechanic_ref else []),
    }


def test_standard_relentless_endurance_is_core_card_bound_and_once_per_rest() -> None:
    actor = _actor("half-orc", hp=10)
    actor["sheet"]["content"]["features"] = [_standard_relentless_endurance_feature()]
    sheet = validate_character_sheet(actor["sheet"])

    recovered = apply_damage_to_sheet(sheet, amount=10, damage_type="force")

    assert recovered["after_hp"] == 1
    assert not ({"prone", "unconscious", "dead"} & set(recovered["sheet"]["conditions"]))
    assert recovered["zero_hp_recovery"] == {
        "mechanic_id": CORE_RELENTLESS_ENDURANCE_MECHANIC_ID,
        "feature_id": "srd2014-half-orc-relentless-endurance",
        "result_hp": 1,
        "spent": 1,
        "remaining": 0,
    }

    exhausted = apply_damage_to_sheet(recovered["sheet"], amount=1, damage_type="force")
    assert exhausted["after_hp"] == 0
    assert exhausted["zero_hp_recovery"] is None
    assert {"prone", "unconscious"} <= set(exhausted["sheet"]["conditions"])

    rested = apply_rest(recovered["sheet"], rest_type="long_rest")
    refreshed_feature = rested["sheet"]["content"]["features"][0]
    assert refreshed_feature["uses"]["value"] == 1
    assert rested["recovered"]["features:0:uses"] == 1

    killed_outright = apply_damage_to_sheet(rested["sheet"], amount=20, damage_type="force")
    assert killed_outright["after_hp"] == 0
    assert killed_outright["zero_hp_recovery"] is None
    assert "dead" in killed_outright["sheet"]["conditions"]
    assert killed_outright["sheet"]["content"]["features"][0]["uses"]["value"] == 1


def test_counterfeit_relentless_endurance_prose_cannot_inject_core_behavior() -> None:
    actor = _actor("counterfeit", hp=10)
    actor["sheet"]["content"]["features"] = [
        _standard_relentless_endurance_feature(mechanic_ref=False)
    ]

    damaged = apply_damage_to_sheet(
        validate_character_sheet(actor["sheet"]),
        amount=10,
        damage_type="force",
        death_saves=False,
    )

    assert damaged["after_hp"] == 0
    assert damaged["zero_hp_recovery"] is None
    assert "dead" in damaged["sheet"]["conditions"]
    assert damaged["sheet"]["content"]["features"][0]["uses"]["value"] == 1


def test_standard_relentless_endurance_applies_to_direct_hit_point_loss() -> None:
    actor = _actor("half-orc-hp-loss", hp=6)
    actor["sheet"]["content"]["features"] = [_standard_relentless_endurance_feature()]

    result = apply_hit_point_loss_to_sheet(
        validate_character_sheet(actor["sheet"]),
        amount=6,
        death_saves=False,
    )

    assert result["after_hp"] == 1
    assert result["zero_hp_recovery"]["mechanic_id"] == (CORE_RELENTLESS_ENDURANCE_MECHANIC_ID)
    assert not ({"prone", "unconscious", "dead"} & set(result["sheet"]["conditions"]))


def test_falling_unconscious_also_leaves_actor_prone_after_healing() -> None:
    actor = _actor("target", hp=5)
    dropped = apply_damage_to_sheet(actor["sheet"], amount=5, damage_type="force")
    assert {"prone", "unconscious"} <= set(dropped["sheet"]["conditions"])


def test_disciple_of_life_uses_recorded_spell_and_cast_level_before_hp_clamp() -> None:
    target = _actor("target", hp=20)
    target["sheet"]["combat"]["hp"]["value"] = 1
    cleric = _actor("cleric")
    cleric["sheet"]["content"]["spells"] = [
        {
            "id": "cure-wounds",
            "name": "Cure Wounds",
            "level": 1,
        }
    ]
    cleric["sheet"]["content"]["features"] = [
        {
            "id": "dnd5e.content.srd2014.feature.life-domain-disciple-of-life",
            "name": "Disciple of Life",
            "source_key": "Life Domain",
        }
    ]

    result = apply_healing_to_sheet(
        target["sheet"],
        amount=8,
        source_sheet=cleric["sheet"],
        spell_id="cure-wounds",
        spell_level=2,
    )

    assert result["after_hp"] == 13
    assert result["requested_amount"] == 8
    assert result["bonus_amount"] == 4
    assert result["source"]["modifiers"][0]["name"] == "Disciple of Life"

    zero_roll = apply_healing_to_sheet(
        target["sheet"],
        amount=0,
        source_sheet=cleric["sheet"],
        spell_id="cure-wounds",
        spell_level=1,
    )
    assert zero_roll["requested_amount"] == 0
    assert zero_roll["bonus_amount"] == 3
    assert zero_roll["amount"] == 3


def test_spell_healing_rejects_unrecorded_spells_and_illegal_cast_levels() -> None:
    target = _actor("target")
    source = _actor("source")
    source["sheet"]["content"]["spells"] = [
        {"id": "cure-wounds", "name": "Cure Wounds", "level": 1}
    ]

    with pytest.raises(ValueError, match="not recorded"):
        apply_healing_to_sheet(
            target["sheet"],
            amount=1,
            source_sheet=source["sheet"],
            spell_id="invented-heal",
            spell_level=1,
        )
    with pytest.raises(ValueError, match="legal cast level"):
        apply_healing_to_sheet(
            target["sheet"],
            amount=1,
            source_sheet=source["sheet"],
            spell_id="cure-wounds",
            spell_level=0,
        )


def test_petrified_condition_grants_resistance_to_every_damage_type_once() -> None:
    actor = _actor("target", hp=20)
    actor["sheet"]["conditions"] = ["petrified"]
    result = apply_damage_to_sheet(actor["sheet"], amount=9, damage_type="force")
    assert result["applied_amount"] == 4
    assert result["adjustment"] == "resistant"


def test_negative_damage_is_rejected_instead_of_silently_healing_or_nooping() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        apply_damage_to_sheet(_actor("target")["sheet"], amount=-1)


def test_massive_damage_uses_excess_over_zero_hp() -> None:
    actor = _actor("target", hp=10)
    result = apply_damage_to_sheet(actor["sheet"], amount=20, damage_type="cold")
    assert "dead" in result["sheet"]["conditions"]


def test_stunned_and_unconscious_cannot_move() -> None:
    encounter = start_encounter([_actor("a"), _actor("b")], rng=random.Random(1))
    current = encounter["combatants"][encounter["turn_index"]]
    current["conditions"] = ["stunned"]
    with pytest.raises(ValueError):
        spend_movement(encounter, current["actor_id"], 5)


def test_surprise_semantics_are_ruleset_specific() -> None:
    actors = [_actor("a"), _actor("b")]
    actors[0]["surprised"] = True
    old = start_encounter(actors, ruleset="2014", rng=random.Random(1))
    modern = start_encounter(actors, ruleset="2024", rng=random.Random(1))
    old_surprised = next(item for item in old["combatants"] if item["actor_id"] == "a")
    modern_surprised = next(item for item in modern["combatants"] if item["actor_id"] == "a")
    assert old_surprised["turn_budget"]["main_action"] == 0
    assert old_surprised["turn_budget"]["bonus_action"] == 0
    assert old_surprised["turn_budget"]["object_interaction"] == 0
    assert modern_surprised["turn_budget"]["main_action"] == 1
    assert modern_surprised["turn_budget"]["bonus_action"] == 1
    assert modern_surprised["turn_budget"]["object_interaction"] == 1


def test_2014_surprised_actor_regains_reaction_when_first_turn_ends() -> None:
    surprised = _actor("surprised")
    surprised.update(initiative=20, surprised=True)
    other = _actor("other")
    other["initiative"] = 10
    encounter = start_encounter([surprised, other], ruleset="2014")
    ended = end_turn(encounter, actor_id_value="surprised")
    combatant = next(item for item in ended["combatants"] if item["actor_id"] == "surprised")
    assert combatant["turn_budget"]["reaction"] == 1
    assert combatant["turn_budget"]["bonus_action"] == 0
    assert combatant["turn_budget"]["object_interaction"] == 0


def test_end_turn_skips_dead_actor_but_keeps_death_save_turn() -> None:
    first = _actor("first")
    first["initiative"] = 30
    dead = _actor("dead")
    dead["initiative"] = 20
    dead["sheet"]["conditions"] = ["dead", "prone"]
    dying = _actor("dying")
    dying.update(initiative=10, death_saves=True)
    dying["sheet"]["conditions"] = ["unconscious", "prone"]
    encounter = start_encounter([first, dead, dying])

    advanced = end_turn(encounter, actor_id_value="first")

    assert current_combatant(advanced)["actor_id"] == "dying"
    assert any(
        item.get("type") == "turn_skipped"
        and item.get("actor_id") == "dead"
        and item.get("reason") == "dead"
        for item in advanced["log"]
    )
    with pytest.raises(ValueError, match="death save"):
        end_turn(advanced, actor_id_value="dying")


def test_dodge_lasts_until_start_of_next_turn_and_affects_attacks() -> None:
    dodger = _actor("dodger")
    dodger["initiative"] = 20
    attacker = _actor("attacker")
    attacker.update(initiative=10, position={"x": 1, "y": 0})
    dodger["position"] = {"x": 0, "y": 0}
    encounter = _grid_encounter([dodger, attacker])
    encounter = resolve_common_action(encounter, actor_id_value="dodger", action="dodge")
    encounter = end_turn(encounter, actor_id_value="dodger")
    plan = preflight_attack(attacker, dodger, action={}, encounter=encounter)
    assert plan["disadvantage"] is True
    assert "target_dodging" in plan["disadvantage_sources"]
    encounter = end_turn(encounter, actor_id_value="attacker")
    dodger_state = next(item for item in encounter["combatants"] if item["actor_id"] == "dodger")
    assert not dict(dodger_state.get("turn_flags") or {}).get("dodging")


@pytest.mark.parametrize(
    ("condition", "speed_multiplier", "ended_reason"),
    [
        (None, 0.0, "speed_zero"),
        ("grappled", 1.0, "speed_zero"),
        ("restrained", 1.0, "speed_zero"),
        ("incapacitated", 1.0, "incapacitated"),
    ],
)
def test_dodge_lifecycle_does_not_reactivate_after_invalidating_state_ends(
    condition: str | None,
    speed_multiplier: float,
    ended_reason: str,
) -> None:
    dodger = _actor("dodger")
    dodger.update(initiative=20, position={"x": 0, "y": 0})
    attacker = _actor("attacker")
    attacker.update(initiative=10, position={"x": 1, "y": 0})
    encounter = _grid_encounter([dodger, attacker])
    encounter = resolve_common_action(encounter, actor_id_value="dodger", action="dodge")
    encounter = end_turn(encounter, actor_id_value="dodger")
    dodger_state = next(
        item for item in encounter["combatants"] if item["actor_id"] == "dodger"
    )
    assert dodge_benefit_active(dodger_state) is True

    dodger_state["speed_multiplier"] = speed_multiplier
    if condition is not None:
        dodger_state["conditions"].append(condition)
    transition = reconcile_dodge_lifecycle(dodger_state)

    assert transition == {
        "active": False,
        "mechanic_id": "dnd5e.core.action.dodge",
        "ended_reason": ended_reason,
    }
    assert dict(dodger_state["turn_flags"])["dodge_ended"]["reason"] == ended_reason
    assert "dodging" not in dodger_state["turn_flags"]

    dodger_state["speed_multiplier"] = 1.0
    if condition is not None:
        dodger_state["conditions"].remove(condition)
    assert dodge_benefit_active(dodger_state) is False
    normal = preflight_attack(attacker, dodger, action={}, encounter=encounter)
    opportunity = preflight_attack(
        attacker,
        dodger,
        action={},
        encounter=encounter,
        allow_out_of_turn=True,
        require_attack_action=False,
    )
    assert "target_dodging" not in normal["disadvantage_sources"]
    assert "target_dodging" not in opportunity["disadvantage_sources"]


@pytest.mark.parametrize("condition", ["grappled", "restrained"])
def test_dodge_taken_at_zero_effective_speed_ends_immediately(condition: str) -> None:
    dodger = _actor("dodger")
    dodger["position"] = {"x": 0, "y": 0}
    dodger["sheet"]["conditions"] = [condition]
    dodger["derived"] = derive_character_sheet(dodger["sheet"])
    encounter = _grid_encounter([dodger])

    resolved = resolve_common_action(encounter, actor_id_value="dodger", action="dodge")

    combatant = resolved["combatants"][0]
    assert dodge_benefit_active(combatant) is False
    assert "dodging" not in combatant["turn_flags"]
    assert combatant["turn_flags"]["dodge_ended"]["reason"] == "speed_zero"
    assert resolved["log"][-1] == {
        "type": "dodge_ended",
        "actor_id": "dodger",
        "mechanic_id": "dnd5e.core.action.dodge",
        "reason": "speed_zero",
    }


def test_dodge_advantage_uses_authoritative_encounter_and_normalized_dexterity() -> None:
    dodger = _actor("dodger")
    encounter = {
        "ruleset": "2014",
        "combatants": [
            {
                "actor_id": "dodger",
                "conditions": [],
                "turn_flags": {"dodging": True},
                "turn_budget": {"speed": 30},
                "speed_multiplier": 1.0,
            }
        ],
    }
    rules = resolution_context({"edition": "2014", "fingerprint": "", "lock": []})

    assert encounter_dodge_save_advantage(encounter, "dodger", ability=" DEX ") is True
    saved = resolve_actor_check(
        dodger,
        kind="save",
        ability=" DEX ",
        dc=12,
        encounter=encounter,
        rules=rules,
        rng=_SequenceRng(2, 17),
    )
    assert saved["natural"] == 17
    assert saved["rolls"] == [2, 17]
    assert [item["mechanic_id"] for item in saved["rule_receipts"]] == [
        "dnd5e.core.action.dodge"
    ]

    cancelled = resolve_actor_check(
        dodger,
        kind="save",
        ability="dexterity",
        dc=12,
        encounter=encounter,
        disadvantage=True,
        rules=rules,
        rng=_SequenceRng(9),
    )
    assert cancelled["rolls"] == [9]
    assert [item["mechanic_id"] for item in cancelled["rule_receipts"]] == [
        "dnd5e.core.action.dodge"
    ]


def test_area_save_damage_applies_dodge_per_authoritative_target() -> None:
    dodger = _actor("dodger", hp=20)
    bystander = _actor("bystander", hp=20)
    encounter = {
        "ruleset": "2014",
        "combatants": [
            {
                "actor_id": "dodger",
                "conditions": [],
                "turn_flags": {"dodging": True},
                "turn_budget": {"speed": 30},
                "speed_multiplier": 1.0,
            },
            {
                "actor_id": "bystander",
                "conditions": [],
                "turn_flags": {},
                "turn_budget": {"speed": 30},
                "speed_multiplier": 1.0,
            },
        ],
    }
    rules = resolution_context({"edition": "2014", "fingerprint": "", "lock": []})

    settled = resolve_save_damage_to_sheets(
        [dodger, bystander],
        save_ability=" DEX ",
        save_dc=12,
        damage_expression="1d6",
        damage_type="fire",
        half_on_success=True,
        source="test-area",
        encounter=encounter,
        ruleset="2014",
        rules=rules,
        rng=_SequenceRng(6, 3, 18, 11),
    )

    by_id = {item["target_id"]: item for item in settled["result"]["targets"]}
    assert by_id["dodger"]["save"]["rolls"] == [3, 18]
    assert by_id["bystander"]["save"]["rolls"] == [11]
    assert [
        item["mechanic_id"] for item in by_id["dodger"]["save"]["rule_receipts"]
    ] == ["dnd5e.core.action.dodge"]
    assert by_id["bystander"]["save"]["rule_receipts"] == []


def test_paralyzed_target_is_automatic_critical_within_five_feet() -> None:
    attacker = _actor("attacker")
    attacker.update(initiative=20, position={"x": 0, "y": 0})
    target = _actor("target", hp=20, ac=1)
    target.update(initiative=10, position={"x": 1, "y": 0})
    target["sheet"]["conditions"] = ["paralyzed"]
    target["derived"] = derive_character_sheet(target["sheet"])
    encounter = _grid_encounter([attacker, target])
    plan = preflight_attack(attacker, target, action={}, encounter=encounter)
    assert plan["automatic_critical_on_hit"] is True
    _, _, result = resolve_attack_action(attacker, target, plan=plan, rng=random.Random(1))
    assert result["hit"] is True
    assert result["critical"] is True


def test_unseen_attacker_and_target_apply_opposed_attack_modifiers() -> None:
    attacker = _actor("attacker")
    attacker.update(initiative=20, position={"x": 0, "y": 0}, hidden=True)
    target = _actor("target")
    target.update(initiative=10, position={"x": 1, "y": 0}, hidden=True)
    encounter = _grid_encounter([attacker, target])
    plan = preflight_attack(attacker, target, action={}, encounter=encounter)
    assert plan["advantage"] is True
    assert plan["disadvantage"] is True
    assert "attacker_unseen" in plan["advantage_sources"]
    assert "target_unseen" in plan["disadvantage_sources"]


def _kobold_attack_trait_actor(identifier: str) -> dict:
    actor = _actor(identifier)
    actor["sheet"]["content"]["features"] = [
        {
            "id": "pack-tactics-passive",
            "name": "Pack Tactics",
            "activation": {"type": "passive"},
            "choices": {
                "source_trait": {
                    "kind": "pack_tactics",
                    "trigger": "attack_roll",
                    "ally_within_target_ft": 5,
                    "requires_ally_not_incapacitated": True,
                    "grants": "advantage",
                    "automatic": True,
                }
            },
        },
        {
            "id": "sunlight-sensitivity-passive",
            "name": "Sunlight Sensitivity",
            "activation": {"type": "passive"},
            "choices": {
                "source_trait": {
                    "kind": "sunlight_sensitivity",
                    "trigger": "attack_roll_or_sight_perception",
                    "environment_fact": "direct_sunlight",
                    "grants": "disadvantage",
                    "automatic": True,
                }
            },
        },
    ]
    actor["derived"] = derive_character_sheet(actor["sheet"])
    return actor


@pytest.mark.parametrize(
    ("operation", "expected_kind"),
    [
        ({"op": "ruling.require", "id": "weather"}, "agent_dm_adjudication"),
        ({"op": "choice.require", "id": "maneuver"}, "player_owned_choice"),
    ],
)
def test_attack_extension_preserves_pending_owner(
    operation: dict[str, str],
    expected_kind: str,
) -> None:
    attacker = _actor("attacker")
    target = _actor("target")
    rules = resolution_context(
        {
            "edition": "2014",
            "fingerprint": "",
            "lock": [],
            "mechanics": [
                {
                    "id": "dnd5e.extension.attack.pending",
                    "event": "attack.preflight",
                    "operations": [operation],
                    "citations": [{"source": "local:extension"}],
                }
            ],
        }
    )

    with pytest.raises(NeedsRulingError) as raised:
        preflight_attack(attacker, target, action={}, rules=rules)

    assert raised.value.ruling_kind == expected_kind


def test_2024_invisible_actor_has_initiative_advantage() -> None:
    invisible = _actor("invisible")
    invisible["sheet"]["conditions"] = ["invisible"]
    invisible["derived"] = derive_character_sheet(invisible["sheet"])
    encounter = start_encounter([invisible], ruleset="2024", rng=random.Random(1))
    assert len(encounter["combatants"][0]["initiative_roll"]["rolls"]) == 2


def test_2024_exhaustion_reduces_speed_attacks_and_death_saves() -> None:
    exhausted = _actor("exhausted")
    exhausted["sheet"]["edition"] = "2024"
    exhausted["sheet"]["combat"]["exhaustion"] = 1
    exhausted["derived"] = derive_character_sheet(exhausted["sheet"])
    target = _actor("target")
    exhausted.update(initiative=20, position={"x": 0, "y": 0})
    target.update(initiative=10, position={"x": 1, "y": 0})
    encounter = _grid_encounter([exhausted, target], ruleset="2024")
    assert encounter["combatants"][0]["turn_budget"]["speed"] == 25
    plan = preflight_attack(exhausted, target, action={}, encounter=encounter)
    assert plan["attack_bonus"] == 3

    exhausted["sheet"]["combat"]["hp"]["value"] = 0
    exhausted["sheet"]["conditions"] = ["prone", "unconscious"]
    save = resolve_death_save_to_sheet(exhausted["sheet"], rng=random.Random(7))
    assert save["natural"] == 11
    assert save["total"] == 9
    assert save["failures"] == 1

    legacy = _actor("legacy-exhausted")
    legacy["sheet"]["combat"]["hp"]["value"] = 0
    legacy["sheet"]["combat"]["exhaustion"] = 3
    legacy_save = resolve_death_save_to_sheet(legacy["sheet"], rng=random.Random(7))
    assert len(legacy_save["rolls"]) == 2


def test_active_roll_effects_apply_to_attacks_saves_and_ability_checks() -> None:
    actor = _actor("revived")
    actor["sheet"]["effects"].append(
        {
            "id": "raise-dead-ordeal",
            "name": "Raise Dead ordeal",
            "kind": "revival_ordeal",
            "source": "allied-cleric",
            "source_spell_id": "dnd5e.content.srd2014.spell.raise-dead",
            "active": True,
            "concentration": False,
            "duration": {"period": "long_rest", "remaining": 4},
            "changes": [
                {"path": "rolls.attack.bonus", "mode": "add", "value": -4},
                {"path": "rolls.ability_check.bonus", "mode": "add", "value": -4},
                {"path": "rolls.saving_throw.bonus", "mode": "add", "value": -4},
            ],
            "description": "Raise Dead ordeal.",
        }
    )
    actor["derived"] = derive_character_sheet(actor["sheet"])
    target = _actor("target")

    plan = preflight_attack(actor, target, action={})
    assert plan["effect_roll_bonus"] == -4
    assert plan["attack_bonus"] == 1
    check = resolve_actor_check(
        actor,
        kind="ability",
        ability="strength",
        dc=10,
        rng=_SequenceRng(10),
    )
    save = resolve_actor_check(
        actor,
        kind="save",
        ability="strength",
        dc=10,
        rng=_SequenceRng(10),
    )
    assert check["effect_roll_bonus"] == -4
    assert save["effect_roll_bonus"] == -4
    assert check["total"] == 9
    assert save["total"] == 9


def test_condition_saving_throw_effects_are_not_left_to_client_modifiers() -> None:
    actor = _actor("target")
    actor["sheet"]["conditions"] = ["paralyzed"]
    actor["derived"] = derive_character_sheet(actor["sheet"])
    result = resolve_actor_check(
        actor,
        kind="save",
        ability="dexterity",
        dc=1,
        ruleset="2024",
        rng=random.Random(5),
    )
    assert result["automatic_failure"] is True
    assert result["success"] is False

    actor = _actor("exhausted")
    actor["sheet"]["edition"] = "2024"
    actor["sheet"]["combat"]["exhaustion"] = 2
    actor["derived"] = derive_character_sheet(actor["sheet"])
    save = resolve_actor_check(
        actor,
        kind="save",
        ability="dexterity",
        dc=30,
        ruleset="2024",
        rng=random.Random(1),
    )
    assert save["bonus"] == -4


def test_equipped_armor_automatically_imposes_stealth_disadvantage() -> None:
    actor = _actor("armored-scout")
    sheet, armor_id = add_inventory_item(
        actor["sheet"],
        {
            "id": "scale-mail",
            "name": "Scale mail",
            "kind": "armor",
            "mechanics": {
                "base_ac": 14,
                "dexterity_mode": "max",
                "dexterity_max": 2,
                "stealth_disadvantage": True,
            },
        },
    )
    actor["sheet"] = equip_inventory_item(sheet, armor_id, "armor")
    actor["derived"] = derive_character_sheet(actor["sheet"])

    result = resolve_actor_check(
        actor,
        kind="ability",
        ability="stealth",
        dc=10,
        rng=_SequenceRng(18, 2),
    )

    assert result["rolls"] == [18, 2]
    assert result["natural"] == 2
    assert result["roll_mode"] == "disadvantage"
    assert result["disadvantage_applied"] is True
    assert result["success"] is False


def test_death_save_persists_nat20_recovery() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["combat"]["hp"]["value"] = 0
    actor["sheet"]["conditions"] = ["unconscious"]
    result = resolve_death_save_to_sheet(actor["sheet"], rng=random.Random(5))
    assert result["outcome"] == "revived"
    assert result["sheet"]["combat"]["hp"]["value"] == 1
    assert "unconscious" not in result["sheet"]["conditions"]


def test_fatal_death_save_does_not_leave_actor_unconscious() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["combat"]["hp"]["value"] = 0
    actor["sheet"]["combat"]["death_saves"] = {"successes": 1, "failures": 2}
    actor["sheet"]["conditions"] = ["prone", "unconscious"]

    result = resolve_death_save_to_sheet(actor["sheet"], rng=_SequenceRng(2))

    assert result["outcome"] == "dead"
    assert set(result["sheet"]["conditions"]) == {"dead", "prone"}


def test_natural_one_caps_fatal_death_save_failures_at_schema_limit() -> None:
    actor = _actor("target", hp=10)
    actor["sheet"]["combat"]["hp"]["value"] = 0
    actor["sheet"]["combat"]["death_saves"] = {"successes": 1, "failures": 2}
    actor["sheet"]["conditions"] = ["prone", "unconscious"]

    result = resolve_death_save_to_sheet(actor["sheet"], rng=_SequenceRng(1))

    assert result["outcome"] == "dead"
    assert result["failures"] == 3
    assert result["sheet"]["combat"]["death_saves"] == {
        "successes": 1,
        "failures": 3,
    }
    assert set(result["sheet"]["conditions"]) == {"dead", "prone"}


def test_stabilize_sheet_requires_zero_hp_and_clears_death_saves() -> None:
    actor = _actor("dying")
    actor["sheet"]["combat"]["hp"]["value"] = 0
    actor["sheet"]["combat"]["death_saves"] = {"successes": 1, "failures": 2}
    actor["sheet"]["conditions"] = ["prone", "unconscious"]

    result = stabilize_sheet(actor["sheet"])

    assert result["status"] == "stable"
    assert result["before_death_saves"] == {"successes": 1, "failures": 2}
    assert result["sheet"]["combat"]["death_saves"] == {"successes": 0, "failures": 0}
    assert set(result["sheet"]["conditions"]) == {"prone", "stable", "unconscious"}

    with pytest.raises(ValueError, match="0 hit points"):
        stabilize_sheet(_actor("healthy")["sheet"])
    dead = actor["sheet"] | {"conditions": ["dead"]}
    with pytest.raises(ValueError, match="dead creature"):
        stabilize_sheet(dead)


def test_movement_and_choice_window_are_explicit() -> None:
    first = _actor("a")
    first["position"] = {"x": 0, "y": 2}
    second = _actor("b")
    second["position"] = {"x": 3, "y": 2}
    encounter = _grid_encounter([first, second])
    current = encounter["combatants"][encounter["turn_index"]]["actor_id"]
    origin = encounter["combatants"][encounter["turn_index"]]["position"]
    distance = max(abs(origin["x"] - 1), abs(origin["y"] - 2)) * 5
    moved = spend_movement(encounter, current, distance, destination={"x": 1, "y": 2})
    assert moved["combatants"][encounter["turn_index"]]["turn_budget"]["movement"] == 30 - distance
    pending = add_choice_window(
        moved,
        kind="opportunity_attack",
        actor_id_value="b",
        event="a leaves reach",
        candidates=[{"id": "skip"}, {"id": "attack"}],
    )
    choice_id = pending["pending"][0]["id"]
    resolved = resolve_choice_window(
        pending,
        choice_id=choice_id,
        actor_id_value="b",
        selection={"id": "skip"},
    )
    assert not resolved["pending"]


def test_common_actions_pay_action_and_keep_tactical_state_explicit() -> None:
    encounter = start_encounter([_actor("a"), _actor("b")], rng=random.Random(1))
    current = encounter["combatants"][encounter["turn_index"]]["actor_id"]
    dashed = resolve_common_action(encounter, actor_id_value=current, action="dash")
    actor = dashed["combatants"][dashed["turn_index"]]
    assert actor["turn_budget"]["main_action"] == 0
    assert actor["turn_budget"]["movement"] == 60

    encounter = start_encounter([_actor("a"), _actor("b")], rng=random.Random(1))
    current = encounter["combatants"][encounter["turn_index"]]["actor_id"]
    readied = resolve_common_action(
        encounter,
        actor_id_value=current,
        action="ready",
        trigger="the foe enters reach",
        payload={"action": "attack"},
    )
    assert readied["readied"][0]["status"] == "armed"


def test_free_object_interaction_consumes_only_its_turn_budget() -> None:
    encounter = start_encounter([_actor("goblin")])

    interacted = resolve_common_action(
        encounter,
        actor_id_value="goblin",
        action="interact_object",
        payload={
            "object_description": "an eyeless hollowed-out pumpkin",
            "interaction": "remove",
        },
    )

    actor = current_combatant(interacted)
    assert actor is not None
    assert actor["turn_budget"]["object_interaction"] == 0
    assert actor["turn_budget"]["main_action"] == 1
    assert "interact_object" not in available_actions(interacted, "goblin")
    assert actor["turn_flags"]["object_interaction_declared"] == {
        "object_description": "an eyeless hollowed-out pumpkin",
        "interaction": "remove",
    }
    with pytest.raises(CombatEngineError, match="legal action payment"):
        resolve_common_action(
            interacted,
            actor_id_value="goblin",
            action="interact_object",
            payload={
                "object_description": "a belt pouch",
                "interaction": "open",
            },
        )


def test_common_cast_can_pay_available_bonus_action_without_spending_main_action() -> None:
    encounter = start_encounter([_actor("a"), _actor("b")], rng=random.Random(1))
    current = encounter["combatants"][encounter["turn_index"]]["actor_id"]

    assert "bonus_action" in available_actions(encounter, current)
    cast = resolve_common_action(
        encounter,
        actor_id_value=current,
        action="cast",
        payment="bonus_action",
        payload={"spell_id": "healing-word"},
    )

    actor = cast["combatants"][cast["turn_index"]]
    assert actor["turn_budget"]["bonus_action"] == 0
    assert actor["turn_budget"]["main_action"] == 1
    assert actor["turn_flags"]["cast_declared"]["spell_id"] == "healing-word"
    assert "bonus_action" not in available_actions(cast, current)


def test_action_surge_grants_one_current_turn_action_and_never_carries_forward() -> None:
    encounter = start_encounter([_actor("a"), _actor("b")], rng=random.Random(1))
    actor_id = encounter["combatants"][encounter["turn_index"]]["actor_id"]
    surged, effect = settle_core_activity_effect(
        encounter,
        actor_id_value=actor_id,
        activity_id="dnd5e.content.srd2014.feature.fighter-action-surge",
    )

    current = surged["combatants"][surged["turn_index"]]
    assert effect == {
        "kind": "action_surge",
        "extra_actions_granted": 1,
        "extra_actions_available": 1,
    }
    assert current["turn_budget"]["extra_action"] == 1
    with pytest.raises(ValueError, match="once on the same turn"):
        settle_core_activity_effect(
            surged,
            actor_id_value=actor_id,
            activity_id="dnd5e.content.srd2014.feature.fighter-action-surge",
        )

    next_turn = end_turn(surged, actor_id_value=actor_id)
    other_id = next_turn["combatants"][next_turn["turn_index"]]["actor_id"]
    returned = end_turn(next_turn, actor_id_value=other_id)
    returned_actor = returned["combatants"][returned["turn_index"]]
    assert returned_actor["actor_id"] == actor_id
    assert returned_actor["turn_budget"]["extra_action"] == 0


def test_2024_action_surge_extra_action_cannot_take_the_magic_action() -> None:
    encounter = start_encounter(
        [_actor("a"), _actor("b")],
        ruleset="2024",
        rng=random.Random(1),
    )
    actor_id = encounter["combatants"][encounter["turn_index"]]["actor_id"]
    spent = resolve_common_action(
        encounter,
        actor_id_value=actor_id,
        action="dash",
    )
    surged, _effect = settle_core_activity_effect(
        spent,
        actor_id_value=actor_id,
        activity_id="dnd5e.content.srd2024.feature.fighter-action-surge",
    )

    assert "attack" in available_actions(surged, actor_id)
    assert "cast" not in available_actions(surged, actor_id)
    with pytest.raises(CombatEngineError, match="extra action cannot be used to cast"):
        resolve_common_action(
            surged,
            actor_id_value=actor_id,
            action="cast",
            payment="extra_action",
        )

    with pytest.raises(CombatEngineError, match="extra action cannot be used"):
        pay_activity_activation(
            surged,
            actor_id_value=actor_id,
            activation_type="action",
            action_kind="magic",
        )

    fresh = start_encounter(
        [_actor("a"), _actor("b")],
        ruleset="2024",
        rng=random.Random(1),
    )
    fresh_actor_id = fresh["combatants"][fresh["turn_index"]]["actor_id"]
    fresh_surge, _ = settle_core_activity_effect(
        fresh,
        actor_id_value=fresh_actor_id,
        activity_id="dnd5e.content.srd2024.feature.fighter-action-surge",
    )
    magic_activity = pay_activity_activation(
        fresh_surge,
        actor_id_value=fresh_actor_id,
        activation_type="action",
        action_kind="magic",
    )
    assert current_combatant(magic_activity)["turn_budget"]["main_action"] == 0
    assert current_combatant(magic_activity)["turn_budget"]["extra_action"] == 1


def test_cunning_action_settles_dash_and_disengage_but_not_hide_outcome() -> None:
    rogue = _actor("rogue")
    rogue["initiative"] = 20
    threat = _actor("threat")
    threat["initiative"] = 10
    encounter = start_encounter([rogue, threat])

    paid_dash = pay_activity_activation(
        encounter, actor_id_value="rogue", activation_type="bonus_action"
    )
    dashed, dash_effect = settle_core_activity_effect(
        paid_dash,
        actor_id_value="rogue",
        activity_id="dnd5e.content.srd2014.feature.rogue-cunning-action",
        declaration={"action": "Dash"},
    )
    assert dash_effect == {
        "kind": "cunning_action",
        "action": "dash",
        "requires_ruling": False,
    }
    assert dashed["combatants"][0]["turn_budget"]["movement"] == 60
    assert dashed["combatants"][0]["turn_budget"]["bonus_action"] == 0

    encounter = start_encounter([rogue, threat])
    paid_disengage = pay_activity_activation(
        encounter, actor_id_value="rogue", activation_type="bonus_action"
    )
    disengaged, effect = settle_core_activity_effect(
        paid_disengage,
        actor_id_value="rogue",
        activity_id="dnd5e.content.srd2014.feature.rogue-cunning-action",
        declaration={"action": "disengage"},
    )
    assert effect["requires_ruling"] is False
    assert disengaged["combatants"][0]["turn_flags"]["disengaged"] is True

    encounter = start_encounter([rogue, threat])
    paid_hide = pay_activity_activation(
        encounter, actor_id_value="rogue", activation_type="bonus_action"
    )
    hiding, effect = settle_core_activity_effect(
        paid_hide,
        actor_id_value="rogue",
        activity_id="dnd5e.content.srd2014.feature.rogue-cunning-action",
        declaration={"action": "hide", "cover": "larger ally"},
    )
    assert effect["requires_ruling"] is True
    assert effect["ruling_requirement"] == {
        "default_resolver": "agent",
        "ruling_kind": "source_or_scene_fact",
        "reason": (
            "Determine from the current cover, visibility, and observer facts whether "
            "hiding is possible and resolve the Stealth boundary."
        ),
    }
    assert hiding["combatants"][0]["hidden"] is False
    assert hiding["combatants"][0]["turn_flags"]["hide_declared"] == {
        "source_activity_id": "dnd5e.content.srd2014.feature.rogue-cunning-action",
        "declaration": {"action": "hide", "cover": "larger ally"},
    }

    encounter = start_encounter([rogue, threat], ruleset="2024")
    paid_2024 = pay_activity_activation(
        encounter, actor_id_value="rogue", activation_type="bonus_action"
    )
    dashed_2024, effect_2024 = settle_core_activity_effect(
        paid_2024,
        actor_id_value="rogue",
        activity_id="dnd5e.content.srd2024.feature.rogue-cunning-action",
        declaration={"action": "dash"},
    )
    assert effect_2024["kind"] == "cunning_action"
    assert dashed_2024["combatants"][0]["turn_budget"]["movement"] == 60


@pytest.mark.parametrize(
    ("ruleset", "activity_id", "speed_multiplier", "expected_movement"),
    [
        ("2014", "dnd5e.content.srd2014.feature.rogue-cunning-action", 0.5, 30),
        ("2014", "dnd5e.content.srd2014.feature.rogue-cunning-action", 0.0, 0),
        ("2024", "dnd5e.content.srd2024.feature.rogue-cunning-action", 0.5, 30),
        ("2024", "dnd5e.content.srd2024.feature.rogue-cunning-action", 0.0, 0),
    ],
)
def test_cunning_action_dash_uses_current_effective_speed(
    ruleset: str,
    activity_id: str,
    speed_multiplier: float,
    expected_movement: int,
) -> None:
    rogue = _actor("rogue")
    rogue["position"] = {"x": 0, "y": 0}
    encounter = _grid_encounter([rogue], ruleset=ruleset)
    current = current_combatant(encounter)
    assert current is not None
    current["speed_multiplier"] = speed_multiplier
    paid = pay_activity_activation(
        encounter, actor_id_value="rogue", activation_type="bonus_action"
    )

    dashed, _ = settle_core_activity_effect(
        paid,
        actor_id_value="rogue",
        activity_id=activity_id,
        declaration={"action": "dash"},
    )

    assert current_combatant(dashed)["turn_budget"]["movement"] == expected_movement
    if speed_multiplier == 0.5:
        current_combatant(dashed)["speed_multiplier"] = 0.0
        before_move = deepcopy(dashed)
        assert "move" not in available_actions(dashed, "rogue")
        with pytest.raises(CombatEngineError, match="effective speed is zero"):
            spend_movement(dashed, "rogue", 5, destination={"x": 1, "y": 0})
        assert dashed == before_move


def test_orc_aggressive_grants_separate_toward_only_movement() -> None:
    orc = _actor("orc")
    orc.update(
        initiative=20,
        position={"x": 2, "y": 2},
        disposition="friendly",
    )
    hostile = _actor("hostile")
    hostile.update(
        initiative=10,
        position={"x": 8, "y": 2},
        disposition="hostile",
    )
    encounter = _grid_encounter([orc, hostile])
    current_combatant(encounter)["speed_multiplier"] = 0.5
    source_card = {
        "id": ORC_AGGRESSIVE_ACTIVITY_ID,
        "activation": {"type": "bonus_action", "cost": 1, "trigger": ""},
        "mechanic_refs": [CORE_ORC_AGGRESSIVE_MECHANIC_ID],
        "choices": {
            "standard_resolution": {
                "kind": "aggressive_movement",
                "maximum": "speed",
                "target": "one_visible_hostile",
            }
        },
    }
    paid = pay_activity_activation(
        encounter,
        actor_id_value="orc",
        activation_type="bonus_action",
    )
    granted, effect = settle_core_activity_effect(
        paid,
        actor_id_value="orc",
        activity_id=ORC_AGGRESSIVE_ACTIVITY_ID,
        declaration={"target_id": "hostile"},
        source_card=source_card,
    )

    assert effect == {
        "kind": "orc_aggressive",
        "target_id": "hostile",
        "movement_granted": 15,
        "movement_remaining": 15,
        "requires_ruling": False,
    }
    current = current_combatant(granted)
    assert current["turn_budget"]["bonus_action"] == 0
    assert current["turn_budget"]["movement"] == 30
    assert current["turn_flags"]["aggressive_movement"]["remaining"] == 15

    immobilized = deepcopy(granted)
    current_combatant(immobilized)["speed_multiplier"] = 0.0
    with pytest.raises(CombatEngineError, match="effective speed is zero"):
        spend_movement(
            immobilized,
            "orc",
            5,
            destination={"x": 3, "y": 2},
            movement_mode="aggressive",
        )

    hidden_target = deepcopy(granted)
    next(
        item for item in hidden_target["combatants"] if item["actor_id"] == "hostile"
    )["hidden"] = True
    with pytest.raises(CombatEngineError, match="no longer visible"):
        spend_movement(
            hidden_target,
            "orc",
            5,
            destination={"x": 3, "y": 2},
            movement_mode="aggressive",
        )

    with pytest.raises(
        CombatEngineError,
        match="every Aggressive movement segment must move toward",
    ):
        spend_movement(
            granted,
            "orc",
            5,
            destination={"x": 1, "y": 2},
            path=[{"x": 2, "y": 2}, {"x": 1, "y": 2}],
            movement_mode="aggressive",
        )

    moved = spend_movement(
        granted,
        "orc",
        10,
        destination={"x": 4, "y": 2},
        path=[
            {"x": 2, "y": 2},
            {"x": 3, "y": 2},
            {"x": 4, "y": 2},
        ],
        movement_mode="aggressive",
    )
    current = current_combatant(moved)
    assert current["position"] == {"x": 4, "y": 2}
    assert current["turn_budget"]["movement"] == 30
    assert current["turn_flags"]["aggressive_movement"]["remaining"] == 5

    ended = end_turn(moved, actor_id_value="orc")
    orc_after = next(item for item in ended["combatants"] if item["actor_id"] == "orc")
    assert "aggressive_movement" not in dict(orc_after.get("turn_flags") or {})


def test_orc_aggressive_agent_positioning_requires_a_toward_decision() -> None:
    orc = _actor("orc")
    orc.update(initiative=20, disposition="friendly")
    hostile = _actor("hostile")
    hostile.update(initiative=10, disposition="hostile")
    encounter = start_encounter([orc, hostile], positioning_mode="agent")
    source_card = {
        "activation": {"type": "bonus_action", "cost": 1},
        "mechanic_refs": [CORE_ORC_AGGRESSIVE_MECHANIC_ID],
        "choices": {
            "standard_resolution": {
                "kind": "aggressive_movement",
                "maximum": "speed",
                "target": "one_visible_hostile",
            }
        },
    }
    granted, _ = settle_core_activity_effect(
        encounter,
        actor_id_value="orc",
        activity_id=ORC_AGGRESSIVE_ACTIVITY_ID,
        declaration={"target_id": "hostile"},
        source_card=source_card,
    )
    facts = {
        "destination_legal": True,
        "distance_ft": 20,
        "difficult_terrain_extra_ft": 0,
        "moves_toward_aggressive_target": False,
    }

    with pytest.raises(CombatEngineError, match="must move toward"):
        spend_movement(
            granted,
            "orc",
            20,
            movement_mode="aggressive",
            spatial_facts=facts,
        )

    facts["moves_toward_aggressive_target"] = True
    moved = spend_movement(
        granted,
        "orc",
        20,
        movement_mode="aggressive",
        spatial_facts=facts,
    )
    current = current_combatant(moved)
    assert current["turn_budget"]["movement"] == 30
    assert current["turn_flags"]["aggressive_movement"]["remaining"] == 10


def test_versatile_weapon_grip_uses_exact_alternate_damage_once() -> None:
    orc = _actor("orc")
    target = _actor("target")
    orc["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "spear",
            "attack_type": "melee",
            "properties": ["thrown", "versatile"],
            "attack_bonus": 6,
            "damage_formula": "1d6",
            "damage_bonus": 4,
            "damage_expression": "1d6 + 4",
            "damage_type": "piercing",
            "additional_damage": [
                {
                    "damage_formula": "1d8",
                    "damage_bonus": 0,
                    "damage_expression": "1d8",
                    "damage_type": "piercing",
                }
            ],
            "versatile_damage_formula": "2d8",
            "reach_ft": 5,
            "thrown_range_ft": {"normal": 20, "long": 60},
        }
    ]
    orc.update(
        initiative=20,
        tie_breaker=0,
        position={"x": 0, "y": 0},
        disposition="hostile",
    )
    target.update(
        initiative=10,
        tie_breaker=0,
        position={"x": 1, "y": 0},
        disposition="friendly",
    )
    encounter = _grid_encounter([orc, target])

    one_handed = preflight_attack(
        orc,
        target,
        action={"weapon_id": "spear", "weapon_grip": "one_handed"},
        encounter=encounter,
    )
    two_handed = preflight_attack(
        orc,
        target,
        action={"weapon_id": "spear", "weapon_grip": "two_handed"},
        encounter=encounter,
    )

    assert one_handed["damage_expression"] == "1d6 + 4"
    assert [part["damage_expression"] for part in one_handed["additional_damage"]] == ["1d8"]
    assert two_handed["damage_expression"] == "2d8 + 4"
    assert two_handed["additional_damage"] == []
    assert two_handed["weapon_grip"] == "two_handed"

    shielded = deepcopy(orc)
    shielded_sheet, shield_id = add_inventory_item(
        shielded["sheet"],
        {
            "id": "shield",
            "name": "Shield",
            "kind": "shield",
            "mechanics": {"ac_bonus": 2, "magic_bonus": 0},
        },
    )
    shielded["sheet"] = equip_inventory_item(
        shielded_sheet,
        shield_id,
        "shield",
    )
    with pytest.raises(CombatEngineError, match="wielding a shield"):
        preflight_attack(
            shielded,
            target,
            action={"weapon_id": "spear", "weapon_grip": "two_handed"},
            encounter=encounter,
        )


def test_versatile_weapon_retains_damage_printed_after_alternate_formula() -> None:
    salamander = _actor("salamander")
    target = _actor("target")
    salamander["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "spear",
            "attack_type": "melee",
            "properties": ["thrown", "versatile"],
            "attack_bonus": 7,
            "damage_formula": "2d6",
            "damage_bonus": 4,
            "damage_expression": "2d6 + 4",
            "damage_type": "piercing",
            "additional_damage": [
                {
                    "damage_formula": "1d6",
                    "damage_bonus": 0,
                    "damage_expression": "1d6",
                    "damage_type": "fire",
                }
            ],
            "versatile_damage_formula": "2d8",
            "versatile_additional_damage": [
                {
                    "damage_formula": "1d6",
                    "damage_bonus": 0,
                    "damage_expression": "1d6",
                    "damage_type": "fire",
                }
            ],
            "reach_ft": 5,
            "thrown_range_ft": {"normal": 20, "long": 60},
        }
    ]

    plan = preflight_attack(
        salamander,
        target,
        action={"weapon_id": "spear", "weapon_grip": "two_handed"},
    )

    assert plan["damage_expression"] == "2d8 + 4"
    assert [part["damage_expression"] for part in plan["additional_damage"]] == ["1d6"]


def test_second_wind_rolls_fighter_level_healing_and_clamps_at_maximum() -> None:
    actor = _actor("fighter")
    actor["sheet"]["progression"]["level"] = 2
    actor["sheet"]["progression"]["classes"] = [
        {"name": "Fighter", "level": 2, "subclass": "", "hit_die": 10}
    ]
    actor["sheet"]["combat"]["hp"] = {"value": 5, "max": 10, "temp": 0}

    result = resolve_second_wind_to_sheet(actor["sheet"], rng=_SequenceRng(8))

    assert result["roll"]["total"] == 8
    assert result["healing_amount"] == 10
    assert result["applied_amount"] == 5
    assert result["sheet"]["combat"]["hp"]["value"] == 10


def test_common_stabilize_action_pays_main_action_and_records_target() -> None:
    encounter = start_encounter([_actor("helper"), _actor("target")], rng=random.Random(1))
    current = encounter["combatants"][encounter["turn_index"]]["actor_id"]
    target = "target" if current == "helper" else "helper"

    stabilized = resolve_common_action(
        encounter,
        actor_id_value=current,
        action="stabilize",
        target_id=target,
        payload={"method": "medicine"},
    )

    actor = stabilized["combatants"][stabilized["turn_index"]]
    assert actor["turn_budget"]["main_action"] == 0
    assert actor["turn_flags"]["stabilizing"] == {
        "target_id": target,
        "payload": {"method": "medicine"},
    }


def test_queued_combatant_joins_at_next_round_without_moving_current_turn() -> None:
    encounter = start_encounter(
        [
            {**_actor("fast"), "initiative": 20, "tie_breaker": 0},
            {**_actor("slow"), "initiative": 10, "tie_breaker": 1},
        ]
    )
    queued = queue_combatant(
        encounter,
        {**_actor("ally"), "initiative": 15, "tie_breaker": 2},
    )

    assert current_combatant(queued)["actor_id"] == "fast"
    assert [item["actor_id"] for item in queued["combatants"]] == ["fast", "slow"]
    assert queued["reinforcements"][0]["join_round"] == 2

    slow = end_turn(queued, actor_id_value="fast")
    assert current_combatant(slow)["actor_id"] == "slow"
    joined = end_turn(slow, actor_id_value="slow")
    assert joined["round"] == 2
    assert [item["actor_id"] for item in joined["combatants"]] == [
        "fast",
        "ally",
        "slow",
    ]
    assert joined["reinforcements"] == []
    assert current_combatant(joined)["actor_id"] == "fast"


def test_queued_combatant_requires_explicit_tie_breaker_for_initiative_tie() -> None:
    encounter = start_encounter(
        [
            {**_actor("fast"), "initiative": 20, "tie_breaker": 0},
            {**_actor("slow"), "initiative": 10, "tie_breaker": 1},
        ]
    )

    with pytest.raises(NeedsRulingError, match="tie_breaker") as raised:
        queue_combatant(encounter, {**_actor("ally"), "initiative": 10})
    assert raised.value.ruling_kind == "agent_dm_adjudication"

    with pytest.raises(NeedsRulingError, match="unique tie_breaker") as duplicate:
        queue_combatant(
            encounter,
            {**_actor("ally"), "initiative": 10, "tie_breaker": 1},
        )
    assert duplicate.value.ruling_kind == "agent_dm_adjudication"


def test_generic_ready_rejects_spell_payload_that_would_bypass_resources() -> None:
    encounter = start_encounter([_actor("a"), _actor("b")], rng=random.Random(1))
    current = encounter["combatants"][encounter["turn_index"]]["actor_id"]
    with pytest.raises(ValueError, match="readying a spell is not supported"):
        resolve_common_action(
            encounter,
            actor_id_value=current,
            action="ready",
            trigger="the foe moves",
            payload={"kind": "spell", "spell_id": "fire-bolt"},
        )


def test_readied_spell_trigger_can_be_declined_then_released_with_reaction() -> None:
    first = _actor("first")
    first["initiative"] = 20
    second = _actor("second")
    second["initiative"] = 10
    encounter = start_encounter([first, second])
    encounter = resolve_common_action(
        encounter,
        actor_id_value="first",
        action="cast",
        payment="main_action",
    )
    encounter = arm_readied_spell(
        encounter,
        actor_id_value="first",
        spell_id="magic-missile",
        trigger="the goblin moves",
        holding_effect_id="holding",
        release_concentration=False,
        release_duration={"period": "manual", "remaining": 0},
        release_effect_kind="readied_spell",
    )
    readied_id = encounter["readied"][0]["id"]
    with pytest.raises(ValueError, match="observed event"):
        trigger_readied_spell(encounter, readied_id=readied_id, event="")
    triggered = trigger_readied_spell(encounter, readied_id=readied_id, event="the goblin moves")
    choice_id = triggered["pending"][0]["id"]
    declined, _ = resolve_readied_spell_window(
        triggered,
        actor_id_value="first",
        choice_id=choice_id,
        release=False,
    )
    assert declined["readied"][0]["status"] == "armed"
    assert declined["combatants"][0]["turn_budget"]["reaction"] == 1

    triggered_again = trigger_readied_spell(
        declined, readied_id=readied_id, event="the goblin moves again"
    )
    released, _ = resolve_readied_spell_window(
        triggered_again,
        actor_id_value="first",
        choice_id=triggered_again["pending"][0]["id"],
        release=True,
    )
    assert released["readied"] == []
    assert released["combatants"][0]["turn_budget"]["reaction"] == 0


def test_readied_spell_expires_at_start_of_casters_next_turn() -> None:
    first = _actor("first")
    first["initiative"] = 20
    second = _actor("second")
    second["initiative"] = 10
    encounter = start_encounter([first, second])
    encounter = arm_readied_spell(
        encounter,
        actor_id_value="first",
        spell_id="magic-missile",
        trigger="the goblin moves",
        holding_effect_id="holding",
        release_concentration=False,
        release_duration={"period": "manual", "remaining": 0},
        release_effect_kind="readied_spell",
    )
    encounter = end_turn(encounter, actor_id_value="first")
    encounter = end_turn(encounter, actor_id_value="second")
    assert encounter["readied"] == []


def test_reactions_are_available_outside_the_actors_turn() -> None:
    encounter = start_encounter([_actor("a"), _actor("b")], rng=random.Random(1))
    current = encounter["combatants"][encounter["turn_index"]]["actor_id"]
    reactor = next(
        item["actor_id"] for item in encounter["combatants"] if item["actor_id"] != current
    )
    pending = add_choice_window(
        encounter,
        kind="reaction",
        actor_id_value=reactor,
        event="movement.leave_reach",
        candidates=[{"id": "skip"}],
    )
    assert available_reactions(pending, reactor)[0]["event"] == "movement.leave_reach"


def test_grid_movement_opens_opportunity_window_only_when_leaving_hostile_reach() -> None:
    mover = _actor("mover")
    mover.update(initiative=20, position={"x": 0, "y": 0}, disposition="friendly")
    threat = _actor("threat")
    threat.update(initiative=10, position={"x": 1, "y": 0}, disposition="hostile", reach_ft=5)
    encounter = _grid_encounter([mover, threat])

    moved = spend_movement(encounter, "mover", 15, destination={"x": 3, "y": 0})
    reaction = available_reactions(moved, "threat")
    assert reaction[0]["trigger"] == "opportunity_attack"
    assert reaction[0]["target_id"] == "mover"

    disengaged = resolve_common_action(encounter, actor_id_value="mover", action="disengage")
    moved_safely = spend_movement(disengaged, "mover", 15, destination={"x": 3, "y": 0})
    assert available_reactions(moved_safely, "threat") == []


def test_positioned_movement_rejects_declared_distance_that_disagrees_with_grid() -> None:
    mover = _actor("mover")
    mover.update(initiative=20, position={"x": 0, "y": 0})
    threat = _actor("threat")
    threat.update(initiative=10, position={"x": 4, "y": 0})
    encounter = _grid_encounter([mover, threat])
    with pytest.raises(ValueError, match="grid distance"):
        spend_movement(encounter, "mover", 5, destination={"x": 2, "y": 0})


def test_explicit_path_pays_difficult_terrain_cost() -> None:
    mover = _actor("mover")
    mover.update(initiative=20, position={"x": 0, "y": 0})
    other = _actor("other")
    other.update(initiative=10, position={"x": 4, "y": 0})
    encounter = _grid_encounter([mover, other])
    encounter["battle_map"] = compile_battle_map(
        {"scene_id": "terrain", "spatial": {}},
        {
            "width_cells": 6,
            "height_cells": 4,
            "difficult_cells": [{"x": 1, "y": 0}],
        },
    )

    with pytest.raises(NeedsRulingError) as missing_path:
        spend_movement(encounter, "mover", 10, destination={"x": 2, "y": 0})
    assert missing_path.value.missing == ("movement_path_for_difficult_terrain",)

    moved = spend_movement(
        encounter,
        "mover",
        10,
        destination={"x": 2, "y": 0},
        path=[{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 2, "y": 0}],
    )
    assert current_combatant(moved)["turn_budget"]["movement"] == 15


def test_voluntary_movement_cannot_end_in_another_living_creatures_space() -> None:
    mover = _actor("mover")
    mover.update(initiative=20, position={"x": 0, "y": 0})
    occupant = _actor("occupant")
    occupant.update(initiative=10, position={"x": 1, "y": 0})
    encounter = _grid_encounter([mover, occupant])

    with pytest.raises(ValueError, match="cannot willingly end"):
        spend_movement(encounter, "mover", 5, destination={"x": 1, "y": 0})


def test_space_sharing_trait_allows_an_occupied_destination() -> None:
    mover = _actor("swarm")
    mover.update(
        initiative=20,
        position={"x": 0, "y": 0},
        can_share_space=True,
    )
    occupant = _actor("occupant")
    occupant.update(initiative=10, position={"x": 1, "y": 0})
    encounter = _grid_encounter([mover, occupant])

    moved = spend_movement(encounter, "swarm", 5, destination={"x": 1, "y": 0})

    assert moved["combatants"][0]["position"] == {"x": 1, "y": 0}


def test_forced_movement_into_occupied_space_requires_effect_specific_ruling() -> None:
    mover = _actor("mover")
    mover.update(initiative=20, position={"x": 0, "y": 0})
    occupant = _actor("occupant")
    occupant.update(initiative=10, position={"x": 1, "y": 0})
    encounter = _grid_encounter([mover, occupant])

    with pytest.raises(NeedsRulingError) as error:
        spend_movement(
            encounter,
            "mover",
            5,
            destination={"x": 1, "y": 0},
            movement_mode="forced",
        )

    assert error.value.missing == ("occupied_destination_resolution",)


def test_forced_movement_and_teleport_bypass_turn_speed_and_condition_limits() -> None:
    controller = _actor("controller")
    controller.update(initiative=20, position={"x": 0, "y": 0}, disposition="hostile")
    target = _actor("target")
    target["sheet"]["conditions"] = ["restrained", "prone"]
    target["derived"] = derive_character_sheet(target["sheet"])
    target.update(initiative=10, position={"x": 2, "y": 0}, disposition="friendly")
    encounter = _grid_encounter([controller, target])
    target_before = next(item for item in encounter["combatants"] if item["actor_id"] == "target")
    movement_before = target_before["turn_budget"]["movement"]

    with pytest.raises(CombatEngineError, match="not this actor's turn"):
        spend_movement(encounter, "target", 5, destination={"x": 3, "y": 0})

    forced = spend_movement(
        encounter,
        "target",
        5,
        destination={"x": 3, "y": 0},
        movement_mode="forced",
    )
    forced_target = next(item for item in forced["combatants"] if item["actor_id"] == "target")
    assert forced_target["position"] == {"x": 3, "y": 0}
    assert forced_target["turn_budget"]["movement"] == movement_before
    assert forced["pending"] == []

    teleported = spend_movement(
        encounter,
        "target",
        35,
        destination={"x": 9, "y": 0},
        movement_mode="teleport",
    )
    teleported_target = next(
        item for item in teleported["combatants"] if item["actor_id"] == "target"
    )
    assert teleported_target["position"] == {"x": 9, "y": 0}
    assert teleported_target["turn_budget"]["movement"] == movement_before
    assert teleported["pending"] == []

    with pytest.raises(CombatEngineError, match="not a traversed path"):
        spend_movement(
            encounter,
            "target",
            35,
            destination={"x": 9, "y": 0},
            path=[{"x": 2, "y": 0}, {"x": 9, "y": 0}],
            movement_mode="teleport",
        )


def test_hidden_mover_does_not_automatically_reveal_itself_with_a_reaction_window() -> None:
    mover = _actor("mover")
    mover.update(
        initiative=20,
        position={"x": 0, "y": 0},
        disposition="friendly",
        hidden=True,
    )
    threat = _actor("threat")
    threat.update(initiative=10, position={"x": 1, "y": 0}, disposition="hostile")
    encounter = _grid_encounter([mover, threat])
    moved = spend_movement(encounter, "mover", 15, destination={"x": 3, "y": 0})
    assert available_reactions(moved, "threat") == []


def test_recorded_visibility_is_authoritative_for_sight_checks() -> None:
    viewer = _actor("viewer")
    subject = _actor("subject")

    assert can_see(viewer, subject) is True

    excluded = deepcopy(subject)
    excluded["visible_to_actor_ids"] = []
    assert can_see(viewer, excluded) is False

    concealed = deepcopy(subject)
    concealed["hidden"] = True
    concealed["conditions"] = ["invisible"]
    assert can_see(viewer, concealed) is False
    concealed["visible_to_actor_ids"] = ["viewer"]
    assert can_see(viewer, concealed) is True

    blinded = deepcopy(viewer)
    blinded["sheet"]["conditions"] = ["blinded"]
    assert can_see(blinded, concealed) is False


def test_recorded_visibility_can_open_reaction_window_for_invisible_mover() -> None:
    mover = _actor("mover")
    mover["sheet"]["conditions"] = ["invisible"]
    mover["derived"] = derive_character_sheet(mover["sheet"])
    mover.update(
        initiative=20,
        position={"x": 0, "y": 0},
        disposition="friendly",
        visible_to_actor_ids=["threat"],
    )
    threat = _actor("threat")
    threat.update(initiative=10, position={"x": 1, "y": 0}, disposition="hostile")
    encounter = _grid_encounter([mover, threat])
    moved = spend_movement(encounter, "mover", 15, destination={"x": 3, "y": 0})
    assert available_reactions(moved, "threat")[0]["target_id"] == "mover"


def test_activity_activation_pays_only_the_matching_action_economy() -> None:
    first = _actor("first")
    first["initiative"] = 20
    second = _actor("second")
    second["initiative"] = 10
    encounter = start_encounter([first, second])
    paid = pay_activity_activation(
        encounter, actor_id_value="first", activation_type="bonus_action"
    )
    assert paid["combatants"][0]["turn_budget"]["bonus_action"] == 0

    reacted = pay_activity_activation(paid, actor_id_value="second", activation_type="reaction")
    assert reacted["combatants"][1]["turn_budget"]["reaction"] == 0


def test_legendary_action_pool_and_weapon_followup_follow_2014_timing() -> None:
    hero = _actor("hero")
    hero["initiative"] = 20
    dragon = _actor("dragon")
    dragon["initiative"] = 10
    dragon["sheet"]["inventory"]["items"].append(
        {
            "id": "tail",
            "name": "Tail",
            "kind": "weapon",
            "mechanics": {
                "attack_type": "melee",
                "attack_ability": "strength",
                "damage_formula": "2d8+9",
                "damage_type": "bludgeoning",
                "attack_bonus_override": 16,
                "reach_ft": 20,
            },
        }
    )
    dragon["derived"] = derive_character_sheet(dragon["sheet"])
    encounter = start_encounter([hero, dragon])
    spec = {
        "kind": "legendary_action_2014",
        "pool": {
            "kind": "legendary_action_pool_2014",
            "maximum": 3,
            "one_option_per_trigger": True,
            "trigger": "end_of_another_creature_turn",
            "recovers_on": "source_turn_start",
        },
        "cost": 1,
        "effect": {
            "kind": "weapon_attack",
            "weapon_id": "tail",
            "attack_mode": "melee",
        },
    }

    paid, payment = pay_legendary_action(
        encounter,
        actor_id_value="dragon",
        activity_id="tail-attack-special",
        spec=spec,
    )
    assert payment["remaining"] == 2
    ended_hero = current_combatant(paid)
    assert ended_hero is not None
    assert ended_hero["turn_flags"]["turn_end_committed"] is True
    assert ended_hero["turn_budget"]["main_action"] == 0
    assert ended_hero["turn_budget"]["bonus_action"] == 0
    assert ended_hero["turn_budget"]["movement"] == 0
    with pytest.raises(CombatEngineError, match="must be resolved"):
        end_turn(paid, actor_id_value="hero")
    attacked, attack_payment = pay_attack_action(
        paid,
        dragon,
        weapon_id="tail",
        attack_mode="melee",
    )
    assert attack_payment["kind"] == "legendary_action_attack"
    with pytest.raises(CombatEngineError, match="Only one|only one"):
        pay_legendary_action(
            attacked,
            actor_id_value="dragon",
            activity_id="tail-attack-special",
            spec=spec,
        )

    dragon_turn = end_turn(attacked, actor_id_value="hero")
    current = current_combatant(dragon_turn)
    assert current is not None and current["actor_id"] == "dragon"
    assert current["legendary_actions"]["remaining"] == 3


def test_surprised_creature_cannot_take_legendary_actions_before_first_turn() -> None:
    hero = _actor("hero")
    hero["initiative"] = 20
    dragon = _actor("dragon")
    dragon.update(initiative=10, surprised=True)
    encounter = start_encounter([hero, dragon], ruleset="2014")
    spec = {
        "kind": "legendary_action_2014",
        "pool": {
            "kind": "legendary_action_pool_2014",
            "maximum": 3,
            "one_option_per_trigger": True,
            "trigger": "end_of_another_creature_turn",
            "recovers_on": "source_turn_start",
        },
        "cost": 1,
        "effect": {"kind": "skill_check"},
    }

    with pytest.raises(CombatEngineError, match="surprised creature"):
        pay_legendary_action(
            encounter,
            actor_id_value="dragon",
            activity_id="detect-special",
            spec=spec,
        )


def test_incapacitated_actor_cannot_pay_reaction_activity() -> None:
    first = _actor("first")
    first.update(initiative=20)
    second = _actor("second")
    second.update(initiative=10)
    second["sheet"]["conditions"] = ["incapacitated"]
    second["derived"] = derive_character_sheet(second["sheet"])
    encounter = start_encounter([first, second])
    with pytest.raises(ValueError, match="cannot activate content"):
        pay_activity_activation(encounter, actor_id_value="second", activation_type="reaction")


def test_hit_point_loss_bypasses_temporary_hp_and_damage_traits() -> None:
    actor = _actor("target", hp=8)
    actor["sheet"]["combat"]["hp"]["temp"] = 7
    actor["sheet"]["traits"]["resistances"] = ["piercing"]

    result = apply_hit_point_loss_to_sheet(actor["sheet"], amount=5)

    assert result["after_hp"] == 3
    assert result["bypassed_temp_hp"] == 7
    assert result["sheet"]["combat"]["hp"]["temp"] == 7


def test_common_use_object_action_preserves_the_reviewed_source_payload() -> None:
    encounter = start_encounter([_actor("hero")])
    payload = {
        "source_finisher_id": "source-zero-hp-finisher:troll",
        "stage": "douse",
        "source_excerpt": "douse the troll with lamp oil",
    }

    used = resolve_common_action(
        encounter,
        actor_id_value="hero",
        action="use_object",
        target_id="troll",
        payload=payload,
    )

    assert used["log"][-1] == {
        "type": "common_action",
        "action": "use_object",
        "actor_id": "hero",
        "target_id": "troll",
        "payload": payload,
        "round": 1,
        "turn_index": 0,
    }


def test_lookalike_critical_text_does_not_gain_an_automatic_attack_contract() -> None:
    effect = (
        "If the target is a creature and Durnan rolls a 20 on the d20 for the "
        "attack roll, the target takes an extra 14 slashing damage, and Durnan "
        "rolls another d20. On a roll of 20, he lops off one of the target's "
        "limbs, or some other part of its body if it is limbless."
    )
    attacker = _actor("durnan")
    attacker["derived"]["inventory"]["weapon_attacks"] = [
        {
            "item_id": "lookalike-sword",
            "attack_type": "melee",
            "reach_ft": 5,
            "properties": [],
            "attack_bonus": 8,
            "damage_expression": "2d6 + 4",
            "damage_type": "slashing",
            "additional_damage": [],
            "on_hit_effect": effect,
        }
    ]
    target = _actor("target")

    plan = preflight_attack(
        attacker,
        target,
        action={"weapon_id": "lookalike-sword"},
    )

    assert "critical_followup" not in plan
    assert plan["on_hit_effect"] == effect
