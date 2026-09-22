"""Versioned primitive capabilities: one source for authoring and dispatch.

Content packs compose these installed capabilities. They cannot register arbitrary
Python handlers or override engine contracts through imported data.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

ALIASES = {"condition.add": "condition.apply", "hp.heal": "healing.apply",
           "effect.add": "effect.apply"}
SHEET_OPS = frozenset({
    "resource.spend", "resource.recover", "spell_slot.spend", "spell_slot.recover",
    "healing.apply", "hp.temp.set", "condition.apply", "condition.remove",
    "effect.apply", "effect.remove",
})

PLAN_FIELDS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "attack.ac_bonus": (
        frozenset({"bonus", "attack_modes"}),
        frozenset(
            {
                "bonus",
                "attack_modes",
                "requires_visible_attacker",
                "requires_wielded_melee_weapon",
            }
        ),
    ),
    "roll.table": (
        frozenset({"table"}),
        frozenset({"table", "roll_id", "exclude"}),
    ),
    "target.validate": (
        frozenset({"source_actor_id", "target_ids"}),
        frozenset(
            {
                "source_actor_id",
                "target_ids",
                "exclude_self",
                "forbid_conditions",
                "maximum_range_ft",
                "require_conditions",
                "require_visible",
                "source",
            }
        ),
    ),
    "check.save": (
        frozenset({"target_ids", "ability", "dc"}),
        frozenset(
            {
                "target_ids",
                "ability",
                "dc",
                "advantage",
                "disadvantage",
                "source",
                "success_damage",
            }
        ),
    ),
    "check.ability": (
        frozenset({"actor_id", "ability", "dc"}),
        frozenset(
            {
                "actor_id",
                "ability",
                "dc",
                "proficient",
                "bonus",
                "advantage",
                "disadvantage",
                "source",
            }
        ),
    ),
    "check.contest": (
        frozenset(
            {
                "source_actor_id",
                "target_actor_id",
                "source_ability",
                "target_ability",
            }
        ),
        frozenset(
            {
                "source_actor_id",
                "target_actor_id",
                "source_ability",
                "target_ability",
                "source_proficient",
                "target_proficient",
                "source_bonus",
                "target_bonus",
                "source_advantage",
                "source_disadvantage",
                "target_advantage",
                "target_disadvantage",
            }
        ),
    ),
    "attack.resolve": (
        frozenset({"source_actor_id", "target_actor_id", "attack_ref"}),
        frozenset(
            {
                "source_actor_id",
                "target_actor_id",
                "attack_ref",
                "attack_mode",
                "context",
            }
        ),
    ),
    "damage.apply": (
        frozenset({"target_ids", "damage_type", "source"}),
        frozenset(
            {
                "target_ids",
                "expression",
                "amount",
                "damage_type",
                "source",
                "critical",
                "reduction",
            }
        ),
    ),
    "healing.apply": (
        frozenset({"target_ids", "source"}),
        frozenset({"target_ids", "expression", "amount", "source"}),
    ),
    "condition.apply": (
        frozenset({"target_ids", "condition_id", "source"}),
        frozenset(
            {
                "target_ids",
                "condition_id",
                "source",
                "effect_id",
                "duration",
                "repeat_save",
                "source_actor_id",
            }
        ),
    ),
    "condition.remove": (
        frozenset({"target_ids", "condition_id"}),
        frozenset({"target_ids", "condition_id", "source"}),
    ),
    "effect.apply": (
        frozenset({"target_ids", "effect_id", "effect"}),
        frozenset({"target_ids", "effect_id", "effect", "source"}),
    ),
    "effect.remove": (
        frozenset({"target_ids", "effect_id"}),
        frozenset({"target_ids", "effect_id", "source"}),
    ),
    "resource.spend": (
        frozenset({"actor_id", "resource_ref", "amount"}),
        frozenset({"actor_id", "resource_ref", "amount", "source"}),
    ),
    "resource.recover": (
        frozenset({"actor_id", "resource_ref", "amount"}),
        frozenset({"actor_id", "resource_ref", "amount", "source"}),
    ),
    "movement.move": (
        frozenset({"actor_id"}),
        frozenset({"actor_id", "distance_ft", "destination", "path", "source",
                   "payment", "distance_limit", "voluntary", "travel_mode", "crawl",
                   "spatial_facts"}),
    ),
    "movement.force": (
        frozenset({"source_actor_id", "target_actor_id", "distance_ft"}),
        frozenset(
            {
                "source_actor_id",
                "target_actor_id",
                "distance_ft",
                "direction",
                "destination",
                "source",
            }
        ),
    ),
    "actor.link": (
        frozenset({"source_actor_id", "target_actor_id", "link_kind"}),
        frozenset(
            {
                "source_actor_id",
                "target_actor_id",
                "link_kind",
                "properties",
                "source",
            }
        ),
    ),
    "actor.unlink": (
        frozenset({"source_actor_id", "target_actor_id", "link_kind"}),
        frozenset({"source_actor_id", "target_actor_id", "link_kind", "source"}),
    ),
    "actor.control": (
        frozenset({"controller_actor_id", "target_actor_id", "mode"}),
        frozenset(
            {
                "controller_actor_id",
                "target_actor_id",
                "mode",
                "mental_ability_source",
                "source",
            }
        ),
    ),
    "knowledge.transfer": (
        frozenset({"from_actor_id", "to_actor_id", "knowledge_ids"}),
        frozenset(
            {
                "from_actor_id",
                "to_actor_id",
                "knowledge_ids",
                "reason",
                "source",
            }
        ),
    ),
    "world.counter.adjust": (
        frozenset({"key", "amount"}),
        frozenset({"key", "amount", "minimum", "maximum", "source"}),
    ),
    "world.counter.set": (
        frozenset({"key", "value"}),
        frozenset({"key", "value", "source"}),
    ),
    "state.assert": (
        frozenset({"subject", "operator", "expected"}),
        frozenset({"subject", "operator", "expected", "message"}),
    ),
}

ENCOUNTER_HANDLERS = {'actor.control': '_execute_actor_control',
 'actor.link': '_execute_actor_link',
 'actor.unlink': '_execute_actor_link',
 'attack.resolve': '_execute_attack_resolve',
 'check.ability': '_execute_check_ability',
 'check.contest': '_execute_check_contest',
 'check.save': '_execute_check_save',
 'condition.apply': '_execute_condition_apply',
 'condition.remove': '_execute_condition_apply',
 'damage.apply': '_execute_damage_apply',
 'effect.apply': '_execute_effect_apply',
 'effect.remove': '_execute_effect_remove',
 'healing.apply': '_execute_healing_apply',
 'knowledge.transfer': '_execute_knowledge_transfer',
 'movement.force': '_execute_movement_force',
 'movement.move': '_execute_movement_move',
 'resource.recover': '_execute_resource_spend',
 'resource.spend': '_execute_resource_spend',
 'roll.table': '_execute_roll_table',
 'state.assert': '_execute_state_assert',
 'target.validate': '_execute_target_validate',
 'world.counter.adjust': '_execute_world_counter_adjust',
 'world.counter.set': '_execute_world_counter_adjust'}


@dataclass(frozen=True)
class PrimitiveDefinition:
    opcode: str
    version: int = 1
    sheet_local: bool = False
    handler: str | None = None
    required_fields: frozenset[str] = frozenset()
    allowed_fields: frozenset[str] = frozenset()


PRIMITIVES = MappingProxyType({
    name: PrimitiveDefinition(
        name, sheet_local=name in SHEET_OPS, handler=ENCOUNTER_HANDLERS.get(name),
        required_fields=PLAN_FIELDS.get(name, (frozenset(), frozenset()))[0],
        allowed_fields=PLAN_FIELDS.get(name, (frozenset(), frozenset()))[1],
    ) for name in sorted(SHEET_OPS | PLAN_FIELDS.keys() | {
        "modifier.add", "advantage.add", "disadvantage.add", "choice.require", "ruling.require",
    })
})
PLAN_FIELDS = MappingProxyType(PLAN_FIELDS)
ENCOUNTER_HANDLERS = MappingProxyType(ENCOUNTER_HANDLERS)
PLAN_OPS = frozenset(PLAN_FIELDS)


def require_capabilities(requirements: Any) -> dict[str, int]:
    """Fail at compilation when a pack needs a missing or incompatible capability."""
    if not isinstance(requirements, dict):
        raise ValueError("requires must map canonical primitive names to versions")
    result = {}
    for name, version in requirements.items():
        spec = PRIMITIVES.get(name)
        if spec is None:
            raise ValueError(f"required primitive is unavailable: {name}")
        if isinstance(version, bool) or not isinstance(version, int) or version != spec.version:
            raise ValueError(f"unsupported primitive version: {name}@{version}")
        result[name] = version
    return dict(sorted(result.items()))


def capability_manifest() -> dict[str, dict[str, Any]]:
    """Detached, deterministic metadata for tooling and extension authors."""
    return {name: {
        "version": spec.version, "sheet_local": spec.sheet_local,
        "semantic_plan": name in PLAN_OPS,
        "execution_context": "paid_attack" if name == "attack.ac_bonus" else
            "encounter" if spec.handler else "sheet" if spec.sheet_local else "event",
        "required_fields": sorted(spec.required_fields),
        "allowed_fields": sorted(spec.allowed_fields),
    } for name, spec in PRIMITIVES.items()}
