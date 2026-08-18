# Checks and rolls

Use a check only when outcome is uncertain and failure has a meaningful
consequence. Retrieve the applicable campaign rule and exact module evidence
before choosing ability, skill, DC, advantage, or disadvantage.

The server rolls all dice. `character_check(action="contest")` rolls both
sides in Play and preserves the campaign random stream. Group checks and
ordinary checks use their dedicated actions; raw dice are not a substitute for
a structured settlement when one exists.

For 2024 Heroic Inspiration, the ordinary check must finish first and return its
`resolution_id` plus ordered d20 rolls. Immediately call
`character_check(action="reroll")` with that id, the one `roll_index`, and the
exact `expected_original_roll`. The engine spends the actor's single Heroic
Inspiration, advances the same branch random stream once, replaces only that die,
and requires the new result. Do not rerun the check, reroll both
Advantage/Disadvantage dice, apply this to a death save, or choose the better
result afterward.

The Agent describes intent and consequence, while the engine owns modifiers,
exhaustion, rule-pack effects, roll receipts, and payment.
