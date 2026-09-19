# Combat turns and choices

## Source encounter preparation

Before `combat_start`, validate the source encounter with
`module_query(view="preflight", payload={scene_id, participant_manifest})`.
The manifest's only top-level fields are `schema_version`, `groups`, and optional
`notes`. Each group has `key`, optional `label`, `role`, positive integer
`required_count`, `actor_ids`, `source_scene_id`, and `source_excerpt` (8–4000
characters copied from that scene). Put evidence inside each group, not at the
manifest root. `role="combatant"` means present at the start; `reinforcement`
means a later arrival; `optional` means a source-supported optional group.
These groups describe module participants, not a required player count. Supply
PCs separately in the opening `participant_ids` alongside initial combatants.
Do not label opening enemies as reinforcements merely to pass validation.

Bind individual occupants, not just matching statblocks. Two rooms containing
the same creature type normally require different actor instances. Reuse an
existing actor id only with evidence that this same individual is present here;
retain its actual wounds and history. Reuse the content review for new instances
without repeating the card review. Preflight validates the supplied manifest;
it does not prove an Agent's unstated relocation or individual identity claim.

Apply source-specific starting conditions before initiative. Presence in the
manifest does not imply consciousness, hostility, or willingness to fight.
For a fainted occupant, retain the source's actual waking trigger and duration;
intrusion that causes fainting cannot also prove awakening. A wake action must
be performed and paid by the actual acting creature, not the unconscious target.
An unsuccessful tool call or a narrative description is not a wake receipt.

```json
{
  "schema_version": 1,
  "groups": [{
    "key": "guards",
    "role": "combatant",
    "required_count": 2,
    "actor_ids": ["<first prepared actor id>", "<second prepared actor id>"],
    "source_scene_id": "<encounter scene id>",
    "source_excerpt": "<exact source sentence requiring these two guards>"
  }]
}
```

## Current turn

Combat write replies retain current tactical state and the latest ten visible
log entries. `combat.log_window` reports any omitted history; use
`combat_query(view="status", payload={"detail":"full"})` only when that history
is needed. Use the returned state and campaign revision for the next action;
do not automatically query status again after every successful write. Stored
transaction receipts retain the full original encounter log.

In 2014 combat, draw an owned stowed weapon with
`combat_common_action(action="draw_weapon", payload={item_id, slot:"main_hand"})`
(or `off_hand`). Stow a held weapon with `action="stow_weapon", payload={item_id}`.
These settle the equipment change and object interaction (or an available action
if that interaction was spent) together. Do not prepay the interaction. Drawing
requires an empty destination hand; stow separately if needed. Ground weapons
instead use `pickup_ground` with the actual ground record and reach evidence.
Do not call the out-of-combat `inventory_change(equip)` or fabricate a ground item.

First aid uses `combat_check(kind="stabilize", ability="medicine", target_id=...)`:
Runtime spends one action, rolls the DC 10 Medicine check, and applies stability
on success in one transaction. Do not first pay `combat_common_action(stabilize)`
or use `combat_hp_change` with a generic rules excerpt. In Agent positioning,
also supply top-level `spatial_facts={decision_id, reason, within_5_ft:true}` based
on the actual scene; move into reach first if needed. Grid mode uses positions.

Read `combat_query(view="status", payload={detail:"summary"})` after a turn
mismatch; this omits accumulated historical logs but retains current tactical
state. Use `detail:"full"` only when historical encounter events are needed.
The actor at
`combatants[turn_index]` is current. Dead actors may already have been skipped by
the engine. Do not call end-turn on them again or cycle through guessed actor ids.
`available_actions` lists categories and a budget, not a completed tactical turn:
an `attack_budget` of zero with `main_action` remaining still permits starting an
Attack action. Inspect living opponents before passing; do not repeat empty
rounds because an earlier opponent died.

A rejected out-of-turn attack has not spent an action or dealt damage. Finish
the actual current actor's turn, then resolve the intended attack when its actor
becomes current. Do not use successive `combat_end_turn` calls to skip living
opponents while repairing turn order. Passing is a tactical decision requiring
an actual reason, not error recovery. If a turn was already skipped incorrectly,
retain the receipt and record the defect; do not replay earlier rounds or invent
an attack result to repair history.

Spell attacks (for example Guiding Bolt or Scorching Ray) first use
`combat_cast_spell` without a target declaration. The returned
`spell_resolution_id` owns the paid cast and remaining attacks. Then call
`combat_resolve_attack` for each target, passing
`action={spell_resolution_id, context:{spatial_facts}}` and `target_id`.
Do not cast again to select a target or finish a paid attack sequence.

In Agent positioning, structured single-creature save/direct-effect spells use
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
