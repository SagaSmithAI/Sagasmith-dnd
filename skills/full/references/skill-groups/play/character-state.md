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
