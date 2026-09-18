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
