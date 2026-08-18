# Phase: Combat

Combat is an engine-owned encounter transaction. Query the current combatant,
turn revision, action availability, pending choices, map, resources, and
relevant source evidence before acting.

The server owns initiative, dice, action economy, reactions, movement payment,
spell slots, concentration, damage, death saves, and random-stream position.
The Agent chooses legal intent and supplies source-bound DM rulings only when
the engine opens that boundary.

For an autonomous NPC/monster choice or an opened semantic ruling, use a fresh
signed bounded bundle. The proposal may choose intent but cannot roll, pay,
move, attack, or mutate state; return those requests to the combat engine.

Never advance past an unresolved player choice or pending transaction. End the
encounter with an audited outcome, then re-query the returned Play phase and
commit durable narrative consequences.
