# Scene play

Read the current scene with audience-safe scope, then search and expand exact
module evidence for places, actors, clues, goals, transitions, and special
procedures. Do not expose DM-only fields through player narration.

The Agent decides descriptive activity, NPC intent, dialogue, and
module-specific narrative behavior from current world state and exact context.
The engine changes only validated state primitives. A narrative instruction is
context for reasoning, not a hidden trigger engine.

Use the persistent NPC conversation runtime for connected Play dialogue. Use
`actor_turn` or the legacy single-turn `npc_turn` for standalone autonomous actors,
`faction_turn` for faction decisions, and `audience_render` before publishing a
player-safe view. Validate each generic proposal with `bounded_evaluation`; a
validated proposal is still not a state change.

Advance module progress and the playthrough manifest only after the fiction
actually reaches that state. At scene close, record durable consequences and a
checkpoint only when the recovery value justifies it.
