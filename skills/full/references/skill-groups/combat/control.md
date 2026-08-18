# Combat control and close

Join reinforcements only from reviewed canonical actors with source evidence,
entry timing, mode-appropriate positioning, and current encounter revision. Joining is a
separate transaction and must not rewrite initiative history.

Close combat after all active mechanical choices and the encounter outcome are
resolved. A surviving 0-HP actor with unfinished death saves moves into the
returned `post_combat_recovery`; this no longer blocks `combat_end`. In Play,
continue with `character_state_change(death_save|stabilize)` until settled. Use
an audited structured outcome; do not force a module ending from narration
alone.

After `combat_end`, consume `tools/list_changed`, refresh the native Play tool
list, and use `exposure(search/set)` on the existing binding for the needed Play
tools. Re-query character and campaign state, then commit durable casualties,
relationships, clues, loot, scene progress, and manifest changes through normal
Play continuity tools.

If a combat write returns `narrative_followup`, keep the mechanical result and
send each listed named NPC through the isolated portrayal workflow before its
next narrative decision. The follow-up never grants a free move/action or
implements a module-specific surrender, escape, or negotiation trigger.
