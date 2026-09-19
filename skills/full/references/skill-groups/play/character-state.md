# Character state outside combat

Use character, inventory, wallet, content, and action facades for ordinary
out-of-combat changes. Query the latest character revision first and keep raw
sheet input separate from derived engine output.

Use one `campaign_change(action="party_rest")` transaction for a party rest.
Include each member's Hit Dice, recovery, preparation, attunement, light
activity, and other choices; do not advance the rest clock separately or apply
per-character rest mutations.

Timed effects, spell preparation models, consumables, currency, equipment, XP,
milestones, death, resurrection, departure, and replacement must survive
Snapshot save and restore.

Before advancing a level, check the campaign's progression mode, the exact
reward trigger, and the character's prior advancement receipts. A source saying
characters are likely to reach a level is not a level award. XP rewards require
their stated outcome and party division; a milestone requires an established
milestone policy and a completed trigger. Changing `target_level`, wording, or
the idempotency key does not make the same earned reward available again.
One source passage can describe several rewards, so distinguish actual earned
events rather than deduplicating solely by page or chunk. Record unsupported
past awards as corrections without silently rewriting sheets or undoing rolls.
