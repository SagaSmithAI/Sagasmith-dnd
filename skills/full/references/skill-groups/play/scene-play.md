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

Changing the current scene does not perform travel or bypass nearby threats.
Establish departure, the actual route, elapsed time and any source-required
encounters or checks before recording arrival. Do not convert a defeated party's
intent to regroup into an automatic safe return. Use checks only where the rules
or scene create uncertainty; do not add rolls to routine, unobstructed travel.
`module_set_progress.expected_state_version` belongs to the destination scene's
progress for the specified scope. Read that scene's progress, not the old scene's
version or the campaign revision, when resolving a conflict. A rejected progress
write has not moved anyone, and fixing its version does not supply missing travel.
