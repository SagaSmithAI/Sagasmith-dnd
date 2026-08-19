# SagaSmith D&D Skills — Full MCP Runtime

> This is the repository-local `sagasmith-dnd/skills/full` distribution, not a standalone repository.

Full mode runs D&D 5e 2014/2024 through the `sagasmith_dnd` MCP server. MCP
owns state, Packs, modules, actors, knowledge, branches, and combat; this
directory contains Agent workflows only.

Read `sagasmith://bootstrap`, then call `storage_status`, `server_capabilities`,
and `campaign_query(view="resume")`. Open the MCP session with
`exposure(action="open")`, discover tools with `search`, change the native list
with `set`, and refresh schemas after `tools/list_changed`. The current native
list is the sole visibility surface.

Author books only in Lobby. Core+D&D performs the mechanical first pass; the
Agent repeatedly inspects evidence, finds errors, edits, and rechecks before
explicitly finalizing an immutable Pack. Keep book-specific decisions in the
draft/Pack and move only reusable review procedures into Skills.

The Agent rules hearing, comprehension, and NPC response in Play. Combat keeps
Grid and Agent spatial modes: in Agent mode the Agent decides range, cover,
line of effect, friendly fire, and movement legality, while MCP validates and
commits structured facts and authoritative state changes.

See [SKILL.md](SKILL.md) for the complete workflow.
