# Host integration

SagaSmith D&D Full Runtime requires the repository-local Skills distribution and
the `sagasmith_dnd` MCP server. The same tool handlers, schemas, authority rules,
structured errors, and result content apply over local stdio and Hosted
Streamable HTTP.

## Trust boundary

Keep trusted context separate from player/model text. For each call the Host
must know:

- caller/workload identity;
- requester principal and resource owner;
- acting Host principal and optional acting character;
- allowed operation and authorized audience (`sagasmith-dnd-mcp`);
- campaign, `room_turn_id`, `base_revision`, nonce, issued-at, and expiry.

Hosted HTTP uses a newly signed `sagasmith.auth-context/delegation-v2` envelope
for the exact target service and operation on every request. Do not forward a
browser token or a token issued for another MCP. A shared HTTP client may reuse
connections, but it must not cache principal, campaign, authorization, or
handle state on the connection. The model cannot choose an authoritative
identity.

A trusted single-user stdio process may bind
`SAGASMITH_DND_MCP_BOUND_PRINCIPAL_ID`. `system:local` is appropriate only for
an explicitly trusted local process.

## Modern MCP 2026-07-28

1. Connect only the MCP for the campaign's current system.
2. Use `server/discover` and require `2026-07-28` for the modern Hosted path.
3. Read the deterministic, sorted `tools/list`. Cache it privately for the
   advertised authorization scope and TTL.
4. Read `sagasmith://bootstrap`, or use bounded `skill_query` reads.
5. Resume the campaign and retain its `host_context_binding` and authority
   revision.
6. Select a sorted facade/workflow subset for system, authoritative phase,
   requester role, and current task. SagaSmith Hosted currently caps the
   model-facing projection at 16 tools.
7. Invoke every tool with fresh delegation-v2 metadata and explicit campaign /
   revision fields. The MCP independently rechecks role, phase, revision,
   identity, and allowed operation.
8. Refresh the server catalog only when its authorization/catalog cache scope
   changes. An ordinary combat or campaign write does not mutate modern
   `tools/list`.

Tool projection is a relevance optimization, not authorization. The stable MCP
catalog may contain many tools; the model should see only the small task-specific
subset selected by the Host.

### Explicit handles

On the modern path, `exposure(action="open")` returns an owner-bound, expiring
catalog-guidance handle. `search` and related operations must present the handle.
It is a name, not a capability: every use is re-authorized. Expiry produces a
structured `expired_handle` error and requires a new handle. Exposure never
changes the underlying modern `tools/list`.

### Legacy adapter

Legacy initialize clients may retain connection-scoped exposure and
`tools/list_changed`. That path is for explicit migration/rollback only. A Host
must not use legacy session identity, `Mcp-Session-Id`, mutable catalog state,
`sessionScoped`, or `injectPrincipal` as a modern authority boundary.

## Write, revision, and projection flow

- Generate one business idempotency key at the request boundary and reuse it
  across browser, durable Web job, Agent retry, and MCP write.
- Send the last authoritative revision. If the MCP returns `stale_revision`,
  refresh the affected state and retry with the same key only if the intent is
  still valid.
- Preserve successful MCP authority/random/auth receipts and result revision.
- Build Web-owned projection/outbox events only from successful commit receipts.
  Failed, rolled-back, and no-op calls do not invalidate caches.
- Cache by authority revision and audience. Never read D&D SQLite/Chroma or
  artifact directories directly from Web.

## Tasks

A Web `RoomTurnJob` is Host orchestration and is not an MCP Task. Advertise
`io.modelcontextprotocol/tasks` only when the Host supports durable polling,
cancellation, and recovery. The D&D server uses it solely for the long
`module_draft(action="start")` workflow; short tools remain synchronous.

After a task claim, use `tasks/get`, `tasks/update`, or `tasks/cancel`. Each
follow-up needs a newly signed delegation allowing exactly that operation and
matching the original requester, resource owner, acting Host, campaign, room
turn, and base revision. For HTTP set `Mcp-Name` to the task ID. There is no
`tasks/list` or `tasks/result`. Treat task ID as a name, never as authorization.

## Content, errors, and telemetry

- Preserve standard MCP text, image, audio, resource-link, and embedded-resource
  blocks. Convert media to Host artifacts only after receiving the standard
  `CallToolResult`.
- A combat render includes audience-filtered metadata and native
  `ImageContent`; never expose its local server path.
- Distinguish JSON-RPC protocol errors from tool execution
  `CallToolResult(isError=true)`. Preserve structured `code`, `message`,
  `retryable`, and `recovery` fields.
- Propagate `traceparent`, `tracestate`, and bounded `baggage`.
- Keep metrics low-cardinality: protocol era, discover/catalog/tool/projection
  stage, tool/operation, outcome, and count. Do not label by user, campaign,
  room turn, run ID, or raw arguments.

## Host smoke test

Run the same contract over stdio and Streamable HTTP:

1. discover modern protocol and read a deterministic catalog twice;
2. confirm the Host connects only D&D for a D&D campaign and projects at most
   16 sorted, task-relevant tools;
3. resume as two different requesters and verify private catalog/result cache
   isolation;
4. attempt a forged model principal and wrong-audience/operation delegation;
5. perform an idempotent write, replay the same key, then exercise a stale
   revision recovery;
6. change branch, requester, audience, or context epoch and rebuild model
   context once;
7. render a `party_public` combat map and verify hidden actors are absent while
   native image content reaches the Host media path;
8. start the long module workflow with Tasks, poll with fresh authorization,
   cancel one run, and recover another after an MCP restart;
9. verify structured tool errors are not converted to generic HTTP failures;
10. verify trace context propagates and metrics contain no high-cardinality
    identity or argument labels.

Codex uses `.codex-plugin/plugin.json` and
`skills/sagasmith-dnd-suite/`. Claude Code uses
`.claude-plugin/plugin.json`, `skills/`, and `.mcp.json`. Other compatible Hosts
may install the repository root as a Skill, but all must configure MCP transport,
identity issuance, tool projection, and media handling separately.
