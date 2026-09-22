# 2014 Legendary Resistance

Imported, reviewed 2014 statblocks with the exact `Legendary Resistance (X/Day)`
trait now carry `dnd5e.core.save.legendary_resistance`, the printed source citation,
and the printed number of uses. Unrecognized wording stays a manual ruling.
The 2014 monster **Limited Usage** rule restores `X/Day` uses on a completed long
rest. Short rests, turn starts, encounter starts, and calendar changes do not reset them.

## Failed-save flow

1. Submit the usual authoritative command with its original operation ID. Successful
   saves, ability checks, attack rolls, and exhausted pools do not offer this choice.
2. A failed saving throw returns `status: pending_save` and a `choice`. The failed
   outcome has not damaged the target, ended concentration, added a condition, or
   killed a dying creature. Automatic failures can also offer Legendary Resistance.
3. The target's current controller or a DM resolves the choice:

   ```json
   {
     "tool": "character_state_change",
     "character_id": "<choice.actor_id>",
     "action": "legendary_resistance",
     "payload": {"choice_id": "<choice.id>", "accept": true},
     "expected_revision": 12,
     "idempotency_key": "<new stable operation ID>"
   }
   ```

   Use `accept: false` to retain the failed save. Local authority fills the revision.
   This passive trait costs neither an action nor a reaction.
4. Runtime resumes the saved declaration. Another eligible failed save can return
   another owned choice. A completed result includes `operation_result` for its
   initiating caller or the DM; another target controller receives only the completion
   receipt. Read `campaign_query(view=get).pending_save` after reconnecting.

Accepted choices reserve one use each until the suspended command finishes. While a
choice remains pending, unrelated game mutations are blocked. Final settlement spends
the reserved uses together with all game effects. Actions or spell slots already paid
by an earlier source-commitment operation remain paid and are not paid again.

## Durability and authority

The command attempt runs in a Core transaction. At a failed-save decision, the attempt
rolls back and only the owned choice, exact random prefix, and guarded character
revisions commit. Resume feeds the original recorded dice back to the same source
executor; it neither rewinds the campaign random stream nor draws replacements.
Dice needed after the decision advance the stream normally. Each save must match its
recorded actor, source feature, and original result before a decision can apply.

The checkpoint retains the original declaration, principal, branch, random position,
and actor versions. Callers cannot replace the spell, target, damage expression, DC,
or source commitment. Character and campaign compare-and-swap checks guard both
offer and resume. Conflicts leave the random stream, use pool, effects, and replay
receipts unchanged. Retry unknown writes with their original operation IDs.
Replay envelopes are also bound to the current authorization fingerprint and timeline.
A current DM can finish an unchanged declaration after its initiating DM loses access;
a player cannot acquire the original command's authority.

The internal checkpoint and other callers' original replies are excluded from public
campaign views. A controller sees only their own failed-save details. Core currently
represents read guards as unchanged character updates, so offering a decision advances
the guarded character revisions even though their game state is unchanged.

## Covered execution boundary

The failed-save boundary is shared by ordinary and automatic saves, death saves,
spell saves, Hypnotic Pattern, concentration, source monster activities, semantic
`check.save` plans, and weapon on-hit saves. A death-save success replaces the failed
counter change while retaining the rolled natural value; it does not invent natural-20
healing. The runtime resumes its existing spell, damage, condition, action-budget,
reaction, and semantic-continuation handlers to apply the chosen result.

New authoritative operations that execute saving throws must join `SAVE_COMMANDS`
in `services/saving_throws.py`; an eligible failed save outside the decision context
raises instead of silently applying failure. Pure Domain callers must provide a
`saving_throw_decisions` handler or explicitly handle `SaveDecisionRequiredError`.
