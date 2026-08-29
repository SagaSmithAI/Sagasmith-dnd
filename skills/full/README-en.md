# SagaSmith D&D Skills — Full MCP Runtime

[中文](README.md) · [Full entry point](SKILL.md) ·
[Host integration](../HOST-INTEGRATION.md) · [MCP documentation](../../packages/mcp/README.md)

> This is the repository-local `sagasmith-dnd/skills/full` distribution, not a
> standalone repository. The archived Skills repository is not a release input
> or fallback implementation.

Full mode runs D&D 5e 2014/2024 through the `sagasmith_dnd` MCP server. MCP owns
campaigns, actors, Packs, modules, knowledge, branches, revisions, random
streams, and combat. This directory contains Agent workflows only; it does not
own a database or independently settle deterministic rules.

## Modern startup sequence

1. Read `sagasmith://bootstrap`, or use bounded `skill_query`
   `read/outline/section/search` requests.
2. Call `server/discover`, require protocol `2026-07-28`, then read the stable,
   deterministically sorted `tools/list`. The catalog may be cached privately
   for its authorization scope and does not change after another tool call.
3. Use `campaign_query(view="resume")` for audience-safe continuity and the
   `host_context_binding`; retain the authority revision.
4. Connect only the MCP for the active campaign system. Select a small
   system/phase/role/task facade or workflow subset from the stable catalog;
   SagaSmith Hosted currently exposes at most 16 tools to the model.
5. Keep requester, resource owner, acting Host, optional acting character,
   campaign, room turn, base revision, operation, and expiry in trusted
   structured context. Player text and model arguments cannot choose authority.
6. Reuse one end-to-end business idempotency key for writes. On a stale revision,
   refresh authoritative state, retain the key, and retry only if the operation
   still applies.

On the modern path, `exposure` returns an owner-bound, expiring catalog-search
handle only. It neither changes `tools/list` nor grants permission. Session
exposure, `search/set`, and `tools/list_changed` exist only in the explicit
legacy initialize adapter and must not form a modern Host's identity,
authorization, or tool-management boundary.

## Authoring and Tasks

Author rulebooks and modules only in Lobby. Core+D&D performs the mechanical
first pass; the Agent repeatedly inspects evidence, finds errors, edits, and
rechecks before explicitly finalizing an immutable Pack. Keep book-specific
decisions in the draft/Pack and only reusable review procedures in Skills.

Ordinary tools return a synchronous standard `CallToolResult`. Only the long
`module_draft(action="start")` workflow may return a SEP-2663 task after the
Host negotiates `io.modelcontextprotocol/tasks`. Use `tasks/get`,
`tasks/update`, and `tasks/cancel`, minting a fresh delegation-v2 for exactly
one follow-up operation each time. A `taskId` is a name, not a capability.
Unnegotiated and legacy clients continue to receive a synchronous result.

## Play and Combat

The Agent rules hearing, comprehension, NPC response, and narrative
consequences in Play, while all deterministic state commits still pass through
MCP. Combat supports Grid and Agent spatial modes: Grid owns engine-resolved
coordinates and geometry; Agent mode supplies reviewed range, cover, line of
effect, friendly-fire, and movement facts for MCP validation and commit.

Combat maps return audience-filtered text metadata and native MCP
`ImageContent`. Preserve standard media blocks through the Host attachment
pipeline; never expose local artifact paths or replace standard MCP results
with a private tool-result wire protocol.

## Errors and recovery

- `stale_revision`: refresh authoritative state and recover with the same
  idempotency key if the intent still applies;
- `expired_handle`: obtain a new handle; the old name is not a capability;
- `authorization_denied`: mint a new D&D-targeted delegation for the exact
  audience and operation;
- failed or cancelled task: handle the structured state and do not confuse it
  with a Web `RoomTurnJob`.

Preserve model-correctable `CallToolResult(isError=true)` details—`code`,
`message`, `retryable`, and `recovery`—instead of collapsing them into HTTP 502.

See [SKILL.md](SKILL.md) for the complete workflow.
