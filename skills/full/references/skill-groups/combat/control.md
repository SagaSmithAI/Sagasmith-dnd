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

Between post-combat death saves, follow the returned cadence: advance one round
with `campaign_change(action="clock_advance", payload={period:"round", count:1})`.
Do not reroll at the same game-time tick. Stable still means unconscious at 0 HP.
For unassisted recovery, use `campaign_change(action="stable_recovery",
payload={members:[{character_id, expected_revision}]})` with the campaign revision
guard and an idempotency key. Runtime rolls the 1d4-hour recovery period and
settles HP and elapsed time together. First establish whether uninterrupted
recovery is possible in the actual scene; the operation does not establish a
safe location or neutralize nearby enemies. Do not manually roll then heal.
An enemy's source-described intent to drive intruders away is not proof that an
unconscious creature actually moved. Record the actual action before changing
its location, and keep intent distinct from completed events.

After `combat_end`, use the Host-selected Play tools. Re-query character
and campaign state, then commit durable casualties,
relationships, clues, loot, scene progress, and manifest changes through normal
Play continuity tools.

If a combat write returns `narrative_followup`, keep the mechanical result and
send each listed named NPC through the isolated portrayal workflow before its
next narrative decision. The follow-up never grants a free move/action or
implements a module-specific surrender, escape, or negotiation trigger.
