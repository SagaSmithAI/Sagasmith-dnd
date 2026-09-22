# Source-bound content solutions

Use this group only for custom or imported cards that have exact source evidence but no locked standard mechanic or persisted `resolution_plan`.

Do not call `content_solution` for a standard spell whose card already carries a
persisted `agent_ruling` clause. Its first ordinary cast returns the exact
`agent_ruling_contract` without payment; resubmit that cast with an
exact `declaration={"agent_ruling": {...}}` object containing the returned
source excerpt plus a bounded decision. Do not put those fields directly under
`declaration` or send the effect decision through `component_ruling`.
The successful response must record the action/resource payment and
`semantic_solution.status="agent_ruling_committed"`. This is execution of an
already reviewed standard clause, not first-use content authoring.
For a statblock/innate spell, omit `signature_free_cast`; the spell card's
recorded grant/resource is the payment authority.

1. Query `content_solution` for the exact `actor_id`, `source_card_id`, and `source_card_kind`.
2. If missing, read the complete card excerpt and only the relevant current-scene evidence. The Agent authors a bounded plan whose citations reproduce those managed source excerpts exactly. Do not infer semantics from a creature name, trait name, or lookalike prose.
3. Compile once with `content_solution(action="compile")`, the actor revision, an idempotency key, the authored `resolution_plan`, and a bounded `agent_ruling`. Compilation is allowed in Lobby, Play, or Combat; it persists on the exact runtime actor card.
4. The engine still owns payment, dice, saves, damage, conditions, resources, revisions, and random receipts. The Agent supplies only declared bindings and source/scene facts.
5. In Combat, first pay the triggering action through its normal tool. For an item rider, the successful attack creates the payment window. Then call `combat_choice(action="execute_plan")` with the exact returned application, plan identity/fingerprint, bindings, and current-scene Agent ruling.
6. A custom AC-changing Reaction is the contextual exception: compile a source-bound `trigger="attack.after_hit"` plan with exactly one static `attack.ac_bonus` step. The step records `bonus`, one or both `attack_modes`, and any printed visible-attacker or wielded-melee-weapon requirements. When the stored attack opens `pending_reaction`, use `combat_choice(action="resolve_defense")`; that atomic window spends the Reaction and card use, applies the reviewed bonus, and then resolves damage. Raw `choices.reaction_defense` objects are non-authoritative and ignored.
7. Never settle a custom card by injecting damage, conditions, attachment state, internal database values, or creature-specific facade fields. If the generic plan vocabulary cannot express the reviewed outcome, leave the boundary pending and record a DM ruling instead of adding a named monster rule.
8. Reuse the persisted plan on later triggers. Re-evaluate only current bindings and narrative facts; do not recompile unchanged source text.

## Author the actual plan schema

The plan requires `schema_version: 2`, a stable `id`, the exact `source_card_id`
and `source_card_kind`, a supported `trigger`, nonempty `steps`, and nonempty
`citations`. For an activity paid through the ordinary action tool, the trigger
is `"action"`, not `"activity.use"`. Each step has `id`, `op`, and `args`.
Use the operation contracts in `dnd:full/references/generated-primitives.json`;
do not invent combined opcodes such as `save_damage`.

Declare runtime target selection in `slots`, for example:

```json
{"targets":{"kind":"actor_ids","owner":"agent","description":"Creatures actually inside the source-defined area.","minimum_items":1,"maximum_items":20}}
```

The upper bound is a plan safety limit, not permission to target creatures
outside the source-defined area. Bind the actual actor IDs at execution.
For a source that calls for a save and half damage on success, use separate
save and damage steps. This structural example uses illustrative values;
copy the actual ability, DC, dice, type and source from the reviewed card:

```json
[
  {"id":"save","op":"check.save","args":{
    "target_ids":{"$slot":"targets"},"ability":"constitution","dc":12,
    "success_damage":"half","source":"Reviewed source action"
  }},
  {"id":"damage","op":"damage.apply","args":{
    "target_ids":{"$slot":"targets"},"expression":"2d6","damage_type":"fire",
    "source":"Reviewed source action",
    "reduction":{"$result":"save.damage_reduction_by_actor_id"}
  }}
]
```

One damage step with multiple targets rolls the damage once and applies each
target's save reduction and defenses. Do not roll once per target for a shared
area effect. Where conditional saves depend on the effect's source, provide
the source classification supported by the exact cited excerpt; do not omit
poison or magic evidence to avoid a defense.

Every citation needs `source`, the returned managed `source_ref` object, and a
literal `source_excerpt` from that source. A guessed locator, a scene ID alone,
or invented prose does not establish the card's rule. Compile with the actor's
revision; after compilation, reuse the returned plan identity and payment
application. A successful compile itself spends no action and applies no damage.

### Locate rule evidence without rereading the actor

An actor card's `source_key` identifies its origin; it is not the compiler's
`source_ref`. For an indexed rule effect, use
`rule_search(campaign_id, query=<distinctive effect text>)`, then
`rule_expand(campaign_id, chunk_id=<returned chunk_id>)`. Read `chunk.content`
to verify that the exact card effect is present. The expanded section's broader
`content` can include text outside that particular chunk and is not sufficient.

Build the citation with `source="rule-source:" + expanded.source.key`,
`source_ref={"chunk_id": expanded.chunk_id}`, and a literal effect excerpt from
`expanded.chunk.content`. These fields come from the returned rule evidence;
do not substitute an artifact ID, stable chunk key, file path, or invented UUID.
For module evidence, retain the exact managed module `source_ref` returned by
its expansion instead. If the effect is missing from the indexed evidence,
report that precise source boundary rather than repeatedly fetching the same
full actor sheet or citing an unrelated scene.
