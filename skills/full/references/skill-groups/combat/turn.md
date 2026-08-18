# Combat turns and choices

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
