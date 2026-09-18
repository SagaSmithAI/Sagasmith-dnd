# Combat turns and choices

Read `combat_query(view="status")` after a turn mismatch; the actor at
`combatants[turn_index]` is current. Dead actors may already have been skipped by
the engine. Do not call end-turn on them again or cycle through guessed actor ids.
`available_actions` lists categories and a budget, not a completed tactical turn:
an `attack_budget` of zero with `main_action` remaining still permits starting an
Attack action. Inspect living opponents before passing; do not repeat empty
rounds because an earlier opponent died.

In Agent positioning, structured single-creature spells use
`declaration={target_id, spatial_facts}`. The DM supplies `spatial_facts` with
`decision_id`, source-grounded `reason`, boolean `targetable`, `in_range`, and
`attacker_can_see_target`; optional `cover_degree` and `target_can_see_attacker`.
Use `cover` only if the spell's declaration requires it. Magic Missile still uses
`target_allocations=[{target_id,darts}]`, plus
`declaration={target_spatial_facts:{"<target-id>":{...facts...}}}` for every allocated
target. No coordinates are needed or inferred. Grid mode continues to use recorded
positions. These facts select valid targets; Runtime still pays slots/actions,
rolls saves/damage, and opens real reaction windows.

Resolve pending owned choice and reaction windows before ending or advancing a
turn. Ready declarations identify trigger, intended response, target rules,
resource commitment, and concentration implications; release occurs only
through the matching server window.

`combat_choice` may resolve a real open choice, source-bound on-hit ruling, or
execute a custom-content plan persisted during import/review or compiled by the
DM Agent on first use through `content_solution`. It must validate the current
attack/event, operator, revision, and pending window. It is not a general
free-form mutation tool.

For a standard spell already classified with a persisted Agent-as-DM clause,
use the exact `agent_ruling_contract` returned by its first `combat_cast_spell`
attempt and resubmit through that same tool as
`declaration={"agent_ruling": {...}}`. Do not put the contract fields directly
under `declaration` or in `component_ruling`. Do not compile the standard card or
route it through `combat_choice(execute_plan)`; the cast boundary atomically pays
the action/resource and records the exact evidence-bound ruling.
Omit `signature_free_cast` for statblock/innate spells so MCP consumes their
recorded use resource.

End the turn only after required action costs, saves, ongoing effects, death,
and concentration consequences are settled.
