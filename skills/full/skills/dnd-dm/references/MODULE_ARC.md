# Module Arc

Static module text remains external to saves. At a chapter transition, persist
scoped scene progress with `module_set_progress`, then call one
`memory_change(action="commit")` with an `event_type: "chapter"` event, accepted fact and
actor-knowledge changes, and the chapter snapshot.

Restoring a snapshot forks a new branch; it never rewrites the static module source
or deletes future campaign history. Keep scene progress, clues, faction state, and
player-caused world changes in MCP campaign state rather than modifying module text.
