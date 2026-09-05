# SagaSmith D&D

[中文 Domain 文档](packages/domain/README.md) ·
[English Domain documentation](packages/domain/README-en.md) ·
[MCP server](packages/mcp/README.md) ·
[Agent Skills](skills/README.md) ·
[D&D Workbench](apps/ui/README.md) ·
[SagaSmith Web](https://github.com/SagaSmithAI/SagaSmith-Web) ·
[Platform overview](https://github.com/SagaSmithAI/.github/blob/main/profile/README.md)

SagaSmith D&D is the active vertical monorepo for the D&D 5e 2014/2024 product
line. It versions four independently deployable components together:

- `packages/domain`: deterministic rules, schemas, content compilation, and the
  `sagasmith-dnd` CLI;
- `packages/mcp`: authoritative campaign state, authorization, revisions,
  idempotency, random streams, tasks, media results, and MCP transports;
- `skills`: Agent workflows and bounded host-integration guidance;
- `apps/ui`: the local D&D Workbench, which uses the MCP-backed gateway and never
  reads authoritative storage directly.

The archived standalone D&D MCP, Skills, UI, and generic Module Generator
repositories are not release inputs, mirrors, or fallback implementations.
Current issues, integrations, documentation, and releases belong to this
repository.

## Architecture and authority

```text
Browser / chat client
        |
SagaSmith Web + SagaSmith Agent (Host)
  - LLM and context assembly
  - requester authentication and authorization decisions
  - current-system MCP selection
  - bounded model-facing tool projection
  - durable RoomTurnJob and Web-owned projections
        |
        | standard MCP CallToolResult + per-request delegation
        v
SagaSmith D&D MCP
  - campaign, actor, role, phase, combat, random, revision, idempotency
  - authoritative writes and audience-safe reads
  - stdio / Streamable HTTP parity
        |
        v
sagasmith-dnd + sagasmith-core
  - deterministic mechanics and persisted domain state
```

The Host owns the model and orchestration; the domain MCP owns D&D authority.
Prompts, model arguments, browser fields, cached projections, connection identity,
and tool annotations are never authorization boundaries.

### Identity model

Hosted MCP requests use a short-lived, signed, target- and audience-bound
`sagasmith.auth-context/delegation-v2` envelope. The relevant identities are
kept separate:

- **caller/workload identity**: the Agent process invoking the MCP;
- **requester principal**: the authenticated player whose campaign role is
  authorized;
- **resource owner**: the principal that owns the referenced resource;
- **acting Host principal**: the trusted workload responsible for the action;
- **acting character**: an optional campaign actor selected within the
  server-validated role and phase rules.

The envelope also binds the target service, authorized audience, exact allowed
operation, campaign, `room_turn_id`, `base_revision`, nonce, and expiry. The
model cannot select any authoritative identity. Browser tokens and tokens meant
for another audience must not be forwarded to this server.

## MCP compatibility

| Boundary | Modern path | Compatibility path |
|---|---|---|
| Protocol | MCP `2026-07-28` | Explicit legacy initialize clients |
| Discovery | `server/discover` | Legacy initialize |
| HTTP state | Stateless per request; no authoritative `Mcp-Session-Id` | Transport session may support the adapter only |
| Authorization | Fresh delegation-v2 on every request | Bound principal or explicitly configured legacy auth |
| Catalog | Stable, deterministic, sorted, private-cacheable | Mutable exposure and `tools/list_changed` adapter |
| Cross-call state | Explicit campaign/revision or owner-bound expiring handle | Legacy session exposure, never authority |
| Transports | Same handlers and schemas over stdio and Streamable HTTP | Same domain semantics |

Modern `tools/list` is stable for the same authorization scope and is not
mutated by another tool call. Its metadata advertises a private cache scope and
a five-minute TTL. The Host connects only the MCP for the active campaign
system, then selects a sorted, phase/task/role-appropriate facade subset for the
model. SagaSmith Hosted currently enforces a maximum of 16 projected tools.
That limit improves model selection; the MCP still authorizes every call and
does not rely on projection for security.

`exposure` remains useful on the modern path as catalog guidance. It returns an
opaque, owner-bound, expiring handle and does not change `tools/list` or confer
permission. An expired or mismatched handle returns a structured recovery error.

Collection facades (`*_query`, `*_search`, event/history views, draft indexes,
content packs, NPC conversations, Skills, and exposure search) accept a bounded
filter plus `limit`/`top_k` of 1–100 and an opaque `cursor`. Successful list
results preserve the existing `result` and text fallback while adding `page`
and top-level `next_cursor`. A cursor is bound to its authorized collection and
filter; it is neither identity nor authority, and every continuation is
authorized again. Do not parse cursors. Restart at page one after an invalid or
expired cursor. `content_pack(include_package=true)` is the deliberate exception:
it requests one complete, finite import artifact rather than a catalog page.

## Install and run

Python 3.11 or newer is required.

### Deterministic Domain package

```bash
pip install sagasmith-dnd
sagasmith-dnd doctor --json
sagasmith-dnd --help
```

See the [Domain README](packages/domain/README.md) for CLI, optional content
capabilities, schema upgrades, and rollback requirements.

### Local MCP over stdio

The text baseline includes SQLite, FTS, Markdown/text content, Skills resources,
and both modern and legacy protocol handlers:

```bash
pip install sagasmith-dnd-mcp
sagasmith-dnd-mcp
```

For a single-user Host that cannot inject trusted identity per request, bind the
process to one stable principal outside model input:

```powershell
$env:SAGASMITH_DND_MCP_BOUND_PRINCIPAL_ID = "local-user"
sagasmith-dnd-mcp
```

### Hosted or shared Streamable HTTP

```powershell
$env:SAGASMITH_DND_MCP_TRANSPORT = "streamable-http"
$env:SAGASMITH_DND_MCP_HTTP_HOST = "127.0.0.1"
$env:SAGASMITH_DND_MCP_HTTP_PORT = "8767"
$env:SAGASMITH_DND_MCP_HTTP_PATH = "/mcp"
sagasmith-dnd-mcp
```

The default endpoint is `http://127.0.0.1:8767/mcp`. A non-loopback bind is
rejected unless `SAGASMITH_AUTH_CONTEXT_SECRET` is configured with at least 32
bytes. Production deployments must also provide their own TLS termination,
origin/network policy, secret rotation, and process supervision. A shared HTTP
connection pool may reuse sockets, but never principal, campaign, or handle
state.

### Optional capabilities

Install only the capabilities the deployment uses:

```bash
pip install "sagasmith-dnd-mcp[documents]"  # PDF text/page processing
pip install "sagasmith-dnd-mcp[images]"     # portraits and combat PNGs
pip install "sagasmith-dnd-mcp[ocr]"        # scanned-document OCR
pip install "sagasmith-dnd-mcp[dense]"      # embeddings + vector store
pip install "sagasmith-dnd-mcp[gateway]"    # local Workbench gateway
pip install "sagasmith-dnd-mcp[all]"        # document/media/search runtime; gateway remains explicit
```

Heavy libraries are loaded lazily. A missing optional capability returns an
actionable install instruction instead of preventing the text MCP from starting.

## Correct write flow

Every business operation must keep its idempotency key stable from browser to
Web job, Agent, and MCP. A network retry is the same operation, not a new key.
Writes use optimistic concurrency:

1. read an audience-safe authoritative state and retain its revision;
2. call the write with the same business idempotency key and `base_revision` or
   the tool's documented expected-revision field;
3. on `stale_revision`, refresh the affected state and decide whether the same
   operation can be retried;
4. consume the returned revision and signed/auditable receipt only after the
   MCP reports a successful commit.

Failures, rolled-back transactions, and no-op results do not authorize cache
invalidation. SagaSmith Web owns its revisioned projection/cache and durable
outbox; it derives projection events from successful MCP receipts instead of
reading the D&D SQLite database. Cache keys include authority revision and
audience. Tool-directory cache invalidation follows catalog/authorization scope,
not ordinary campaign writes.

## Tool contracts and errors

All 77 public tools in the current contract are checked for:

- constrained, described input fields;
- an `outputSchema` and validated `structuredContent`;
- a concise text fallback for compatible clients;
- explicit `readOnlyHint`, `destructiveHint`, `idempotentHint`, and
  `openWorldHint` annotations;
- bounded list/search/query pagination (limits are capped at 100);
- server-side role, phase, revision, and operation enforcement.

Annotations help Hosts route tools; they do not grant access. Malformed MCP or
extension messages use JSON-RPC protocol errors. Model-correctable execution
failures use `CallToolResult(isError=true)` with
`structuredContent.error = {code, message, retryable, recovery}`. Clients should
preserve these details rather than replacing every failure with HTTP 502.

Text, image, audio, resource links, and embedded resources remain standard MCP
content blocks. In particular, combat grid renders return audience-filtered
text metadata plus native `ImageContent`; a Host should store/forward the media
through its normal artifact path without flattening it into a private wire
format or exposing server-local paths.

## Durable MCP Tasks

`RoomTurnJob` is a Web Host job and is not an MCP Task. The D&D MCP negotiates
the `io.modelcontextprotocol/tasks` extension only for the genuinely long
`module_draft(action="start")` workflow. Ordinary reads and writes stay
synchronous.

After capability negotiation, task creation returns a standard SEP-2663 task
claim. Poll with `tasks/get`, provide requested input with `tasks/update`, and
request cooperative cancellation with `tasks/cancel`. There is no
`tasks/list` or `tasks/result` method. HTTP follow-ups carry
`Mcp-Name: <taskId>`.

`taskId` is a name, not a capability. Every follow-up requires a newly signed
delegation for exactly that task method and must match the original requester,
resource owner, acting Host, campaign, room turn, and base revision. Tasks use
a bounded SQLite store with lease/heartbeat recovery, a 15-minute default TTL,
terminal tombstones, row and byte limits, and restart recovery. Unnegotiated or
legacy clients receive the synchronous `CallToolResult` fallback.

## State, storage, and observability

By default the MCP owns all mutable data below:

```text
<workspace>/.sagasmith-dnd-mcp/
  data/ttrpgbase.db
  data/chroma_db/
  artifacts/modules/
  artifacts/rulebooks/
  artifacts/normalized-rulebooks/
  artifacts/content-packages/
  runtime/npc-conversations/
  mcp-tasks.sqlite3
```

Clients must never edit these paths directly. Configure a dedicated
`SAGASMITH_DND_MCP_HOME` for each deployment and back it up consistently with
the pinned Core/D&D versions.

The server propagates bounded `traceparent`, `tracestate`, and `baggage` values
in result metadata. Metrics use low-cardinality dimensions such as protocol era,
stage, operation/tool, outcome, and count; user IDs, campaign IDs, run IDs, and
raw arguments must never become metric labels.

## Development and verification

From the repository root:

```bash
uv sync --all-packages --all-extras
uv run --package sagasmith-dnd pytest packages/domain/tests
uv run --package sagasmith-dnd-mcp pytest packages/mcp/tests
uv run ruff check packages/domain packages/mcp
```

UI checks are independent:

```bash
npm ci
npm --prefix apps/ui test
npm --prefix apps/ui run typecheck
npm --prefix apps/ui run build
```

Focused MCP contract checks:

```bash
uv run --package sagasmith-dnd-mcp pytest \
  packages/mcp/tests/test_mcp_2026_contract.py \
  packages/mcp/tests/test_mcp_tasks_extension.py \
  packages/mcp/tests/test_tool_contract_quality.py \
  packages/mcp/tests/test_read_only_evaluations.py \
  packages/mcp/tests/test_streamable_http_runtime.py
```

The repository contains ten independent, deterministic, read-only MCP Builder
evaluations in `packages/mcp/evaluations/read_only.xml`. Each question starts a
fresh traversal of a six-campaign, 30-actor fixture, follows multiple campaign
and roster continuation pages, inspects actor details, resolves the system
catalog, and computes its own answer. The test requires at least 35 public-tool
calls and seven cursor continuations per question and verifies every answer
without a paid model or external service. Write tests separately cover
authorization, idempotency, stale revisions, concurrency, cancellation, restart
recovery, structured errors, media, and transport parity.

## Upgrade and rollback

1. Pin matching Core, Domain, MCP, Agent, and Web commits in the component lock.
2. Stop writers, take a consistent MCP-home/database backup, and run required
   schema migration checks before starting the new process.
3. Canary `server/discover`, deterministic `tools/list`, one read, one
   idempotent write, an audience-safe combat render, and (when enabled) one
   Tasks workflow over each configured transport.
4. Move Hosted traffic only after identity, revision, trace, and media receipts
   are preserved end to end.
5. To roll back an incompatible Host, route it through the explicit legacy
   adapter; never restore session identity as authority.
6. Before rolling back the MCP, stop new module tasks and let active tasks finish
   or cancel them. Restore the database and matching component versions as one
   unit. Keep `mcp-tasks.sqlite3` until all previous task TTLs have elapsed.

No release or data deletion is implied by updating this repository. Follow the
deployment environment's change-management, backup, secret, and rollback policy.

## Documentation map

- [Domain package and CLI (中文)](packages/domain/README.md)
- [Domain package and CLI (English)](packages/domain/README-en.md)
- [MCP server, gateway, protocol, and operations](packages/mcp/README.md)
- [Host integration contract](skills/HOST-INTEGRATION.md)
- [Agent Skills](skills/README.md)
- [D&D Workbench](apps/ui/README.md)
- [Full Agent regression](packages/mcp/docs/FULL_AGENT_REGRESSION.md)
- [Long-term memory boundary](packages/mcp/docs/long-term-memory.md)

## License and content

Original code and documentation are Apache-2.0. Bundled SRD-derived material
and translations retain their own license and attribution in the relevant
NOTICE files. Repository visibility is not permission to redistribute
commercial rulebooks or user content. Public Content Packs must carry explicit,
verifiable license and distribution metadata.

For authorized local copies of the current SagaSmith Content Library, the
Domain package includes a metadata-only lock and verifier for all ten D&D
official expansion addons (2,008 artifacts; one additional class and 77
subclasses):

```bash
python packages/mcp/scripts/build_official_expansion_library.py \
  --source-library /private/canonical/content-library \
  --output /private/repaired-content-library
sagasmith-dnd content verify-official-expansions \
  --path /private/repaired-content-library --json
```

The offline [local repair builder](packages/mcp/scripts/build_official_expansion_library.md)
reproduces the five repaired private packages pinned by the current lock. The
recorded upstream commit identifies original inputs, not a public release of
repaired archives. Source archives and saves are never overwritten or deleted.

The verifier checks exact Pack/archive identities, D&D semantics, complete
artifact accounting, selection-materializer coverage and the PHB dependency. To mount the same
locked set as first-class, core-visible content in the MCP runtime, point the
server at the generated local library before startup:

```powershell
$env:SAGASMITH_DND_OFFICIAL_CONTENT_LIBRARY = "C:\private\repaired-content-library"
sagasmith-dnd-mcp
```

The runtime verifies and stores the exact 2014 Player's Handbook dependency and
all ten expansion Packs. They remain inactive until a DM explicitly enables
them with `content_pack(action="activate")`; 2024 campaigns neither advertise
nor accept these 2014 Packs. The verifier and mount perform no download, do not
redistribute commercial content, and grant no content license.
