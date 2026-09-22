# 2014 Bardic Inspiration

The selected SRD 2014 Bard feature now grants and spends real inspiration dice.
Its die uses **Bard class level** at grant time: d6 at levels 1–4, d8 at 5–9,
d10 at 10–14, and d12 at 15–20. A later level or ability change does not alter an
already granted die. The feature's Charisma-based use pool recovers on a long
rest through level 4 and on a short or long rest from level 5.

## Grant a die

Use the selected feature's existing activity entry:

- Outside combat: `character_action(action="use_activity")`, with payload
  `{activity_id: "dnd5e.content.srd2014.feature.bard-bardic-inspiration", declaration}`.
- During combat: `combat_use_activity`, with that `activity_id` and `declaration`.
  Runtime requires the Bard's turn and spends one bonus action.

The declaration targets one other campaign creature:

```json
{
  "target_id": "<recipient>",
  "scene_facts": {
    "decision_id": "<stable DM decision ID>",
    "reason": "The recipient can hear the Bard across this room.",
    "target_can_hear": true,
    "within_60_ft": true
  }
}
```

The current DM supplies these scene facts. Grid combat derives distance from
the actors' full footprints and requires `within_60_ft` to be omitted. Agent
mode and narrative play require its explicit boolean. Runtime also checks the
source card, current Bard level, use pool, target capacity and known conditions.
Invalid declarations spend no use, action or random draw.

The grant spends one use and creates a recipient effect lasting exactly 100
canonical six-second ticks. Combat and narrative time share this clock. Calendar
relabeling does not extend the gift. A campaign-owned grant record prevents a
copied effect, an altered die, a reset duration or a spent gift from authorizing
another roll. The private grant ledger is omitted from campaign views.

## Choose after the d20

An eligible check, attack or save returns `status: pending_roll` and an owned
`choice`. Its authorized controller sees the selected d20, its dice and current
total, plus the inspiration die size. DC, armor class, success/failure and other
actors' private results are withheld. Passive checks and automatic outcomes with
no d20 do not offer a die.

Resolve with `character_state_change(action="bardic_inspiration")` and
`payload={choice_id, accept:true|false}`. Use the recipient's current revision
and a new stable operation ID; local authority supplies the revision. Declining
retains the die. Accepting rolls exactly one die and consumes the gift even if
the new total still fails. Attack and death-save natural 1/20 rules remain intact.

The same flow covers group checks, contests, initiative, weapon/spell/reaction
attacks, source-object attacks, concentration, death saves and source-plan checks.
If a save still fails, Legendary Resistance follows as a separate owned choice.
Already chosen dice are replayed verbatim through later decisions.

## Atomicity and replay

The speculative declaration runs inside a Core savepoint. Pausing rolls back
its effects and payments, then commits only its random prefix and owned choice.
This works inside the existing transaction that protects phase transitions.
All participating character revisions are guarded. Final settlement applies the
chosen bonus and gift consumption together with the original command's effects,
receipts and action costs. CAS or receipt failure rolls the complete write back.

Reconnect through `campaign_query(view="get").pending_roll`. Retry an unknown
write with its original operation ID. Original command and choice receipts remain
replayable; local timing metrics and receipt-currentness may change on replay.
The private declaration stays hidden from other controllers. A recipient resolving
a DM's operation does not receive the DM's full operation reply.
The recipient's `resolved_roll` reports their own original d20, final total and
inspiration die without disclosing the private DC, AC or other actors' results.

This implementation is edition-bound to 2014. It does not apply the different
2024 wording, Cutting Words or Peerless Skill through this feature.
