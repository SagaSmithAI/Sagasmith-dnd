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
