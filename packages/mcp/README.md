# SagaSmith D&D MCP

[Repository](../../README.md) · [Domain](../domain/README.md) ·
[Skills](../../skills/README.md) · [Workbench](../../apps/ui/README.md) ·
[SagaSmith Web](https://github.com/SagaSmithAI/SagaSmith-Web) ·
[Content library](https://github.com/SagaSmithAI/SagaSmith-dnd-content-library)

> Current source: `sagasmith-dnd/packages/mcp`. The archived standalone D&D MCP
> repository is not a release input or fallback implementation.

SagaSmith D&D MCP is the authoritative D&D 5e Agent service. It owns campaign,
membership, actor, phase, combat, random-stream, revision, idempotency,
settlement, Tasks, and audience boundaries. SagaSmith Web/Agent owns the LLM,
context assembly, scheduling, model-facing tool projection, media storage, and
Web projections; it must not read the MCP database directly.

## Install

Python 3.11+:

```bash
pip install sagasmith-dnd-mcp
sagasmith-dnd-mcp
```

The baseline starts over stdio and includes SQLite, FTS, Markdown/text content,
Skills resources, and all authoritative handlers. Optional capabilities are
explicit and lazy-loaded:

| Extra | Capability |
|---|---|
| `documents` | PDF text and page handling |
| `images` | actor images and combat PNG rendering |
| `ocr` | scanned-PDF OCR |
| `embedding` | Sentence Transformers embeddings |
| `vector` | ChromaDB vector storage |
| `dense` | `embedding` + `vector` |
| `gateway` | local Workbench HTTP/SSE gateway |
| `all` | document/image/OCR/embedding/vector runtime; gateway remains explicit |

```bash
pip install "sagasmith-dnd-mcp[documents]"
pip install "sagasmith-dnd-mcp[images]"
pip install "sagasmith-dnd-mcp[ocr]"
pip install "sagasmith-dnd-mcp[dense]"
pip install "sagasmith-dnd-mcp[gateway]"
```

A missing optional runtime produces an actionable install error instead of
breaking text-only startup or silently degrading the operation.

## Transport and protocol matrix

| Transport/client | Protocol | Discovery | Catalog | Identity |
|---|---|---|---|---|
| Local stdio, modern | `2026-07-28` | `server/discover` | stable/sorted/private-cacheable | process-bound or per-request delegation |
| Hosted Streamable HTTP | `2026-07-28` | `server/discover` | stable/sorted/private-cacheable | fresh delegation-v2 every request |
| Legacy stdio/HTTP | negotiated initialize version | initialize | 7-tool bootstrap + mutable exposure adapter | explicit legacy binding only |

Both transports call the same handlers and publish the same schemas, authority
rules, structured errors, and standard `CallToolResult` content.

### Streamable HTTP

```powershell
$env:SAGASMITH_DND_MCP_TRANSPORT = "streamable-http"
$env:SAGASMITH_DND_MCP_HTTP_HOST = "127.0.0.1"
$env:SAGASMITH_DND_MCP_HTTP_PORT = "8767"
$env:SAGASMITH_DND_MCP_HTTP_PATH = "/mcp"
sagasmith-dnd-mcp
```

The default endpoint is `http://127.0.0.1:8767/mcp`. A non-loopback bind fails
closed unless `SAGASMITH_AUTH_CONTEXT_SECRET` contains at least 32 bytes.
Production TLS, routing, origin policy, process supervision, and secret rotation
remain deployment responsibilities.

## MCP 2026-07-28 contract

Modern requests do not rely on initialize, protocol-level session state, or
`Mcp-Session-Id`. Protocol version, extension capability, identity, and request
metadata are evaluated per request. Cross-call state is explicit:

- campaign and authority revision fields for ordinary operations;
- server-issued opaque handles with owner, TTL, and explicit expiry errors for
  catalog/import/render-style workflows;
- durable MCP Tasks only for the one long module-authoring workflow.

Handles are names, never capabilities. Every use is independently authorized.

### Stable catalog and bounded Host projection

The current contract test locks 77 public tools. `tools/list` returns them in
deterministic name order for the same authorization scope, with a private
five-minute cache hint. An `exposure` operation cannot mutate the modern list.

The Host should:

1. connect only the MCP matching the active campaign system;
2. discover and privately cache the stable catalog;
3. select a sorted facade/workflow subset for system, authoritative phase,
   requester role, and task;
4. expose at most 16 tools to the model in SagaSmith Hosted;
5. let MCP revalidate identity, role, phase, revision, and operation on every
   call.

The 16-tool budget is a SagaSmith model-routing policy, not an MCP protocol
limit or security boundary. Ordinary combat/campaign writes do not cause a
catalog refresh.

Modern `exposure(action="open")` returns an owner-bound, expiring guidance
handle for bounded search/inspection. The legacy adapter alone keeps mutable
session exposure and `tools/list_changed`; `sessionScoped` and
`injectPrincipal` are not modern authority mechanisms.

## Authorization and identity

Hosted calls require a short-lived signed
`sagasmith.auth-context/delegation-v2` envelope targeted to and audience-bound
for `sagasmith-dnd-mcp`. It separates:

- caller/workload identity;
- requester/authorization principal;
- resource-owner principal;
- acting Host/authority principal;
- optional acting character;
- exact allowed operation, campaign, room turn, base revision, nonce, and
  expiry.

The requester receives campaign role checks; the acting Host performs the
operation and is recorded for audit. The model cannot choose either identity.
Never pass through a browser token or a token issued for another audience.
HTTP connection pooling may reuse sockets, but must not pool principal,
campaign, authorization, or handle state.

A trusted single-user stdio Host without per-request identity injection should
set:

```powershell
$env:SAGASMITH_DND_MCP_BOUND_PRINCIPAL_ID = "stable-local-user"
sagasmith-dnd-mcp
```

`system:local` is only for an explicitly trusted local process.

## Tool contracts

Every public tool is tested for:

- described, constrained input fields;
- stable `outputSchema` and validated `structuredContent`;
- concise text fallback for compatible clients;
- explicit `readOnlyHint`, `destructiveHint`, `idempotentHint`, and
  `openWorldHint` annotations;
- bounded collection sizes and list/search/query pagination (`limit` 1–100);
- call-time authorization, phase, revision, and operation enforcement.

Annotations are routing hints, not permissions. Invalid protocol/extension
messages use JSON-RPC errors. Model-correctable execution failures return
`CallToolResult(isError=true)` with:

```json
{
  "error": {
    "code": "stale_revision",
    "message": "safe actionable detail",
    "retryable": true,
    "recovery": "refresh the authoritative revision and retry with the same idempotency key"
  }
}
```

Clients must preserve these details instead of converting every failure to 502.

## Revisions, idempotency, and projections

MCP writes are optimistic and transactional. The client retains the latest
authority revision and reuses one end-to-end business idempotency key from the
browser/Web job through Agent retries to MCP. A transport retry never creates a
new business operation.

On `stale_revision`, refresh the affected state and retry with the same key only
if the user's intent still applies. Successful results carry revision and
authority/random/auth receipts. Failed, rolled-back, and no-op calls do not
authorize cache invalidation.

SagaSmith Web owns its revisioned projection/cache and durable outbox. It emits
projection events from successful MCP commit receipts and scopes cache entries
by authority revision and audience. It never queries D&D SQLite, ChromaDB, or
artifact storage as a read shortcut. Tool-directory caches change only when the
catalog/authorization cache scope changes.

## Standard result media

Tools return standard MCP text, image, audio, resource-link, and
embedded-resource content. Combat rendering returns audience-filtered text
metadata and native `ImageContent`. The Host may convert this to its internal
media envelope and object-storage artifact ID after receipt, but must not replace
`CallToolResult` with a private MCP wire protocol or expose local artifact paths.

`party_public` grid rendering strips hidden actors and private map annotations.
Rendering failure is a media failure and does not roll back an already committed
combat operation.

## Durable MCP Tasks

`RoomTurnJob` is a SagaSmith Web Host job, not an MCP Task. This server advertises
`io.modelcontextprotocol/tasks` only for the genuinely long
`module_draft(action="start")` workflow. All ordinary reads and writes remain
synchronous.

A client that negotiated the extension may receive a SEP-2663 task claim. Use:

- `tasks/get` to poll;
- `tasks/update` to provide requested input (the current workflow does not emit
  input requests yet);
- `tasks/cancel` for cooperative cancellation.

There are deliberately no `tasks/list` or `tasks/result` methods. Streamable
HTTP follow-ups send `Mcp-Name: <taskId>`. Each follow-up requires a fresh
delegation allowing exactly that method and matching the original requester,
resource owner, acting Host, campaign, room turn, and base revision.

`taskId` is a name, not permission. The bounded `mcp-tasks.sqlite3` store uses a
60-second lease, 15-second heartbeat, 15-minute default task TTL, terminal
tombstones, a 10,000-row default limit, a 64 MiB default database limit, and
restart recovery. Unnegotiated and legacy clients receive the synchronous
`CallToolResult` fallback.

## State and configuration

Default state is owned below `SAGASMITH_DND_MCP_HOME` (or the repository-local
`.sagasmith-dnd-mcp` default):

```text
data/ttrpgbase.db
data/chroma_db/
artifacts/modules/
artifacts/module-assets/
artifacts/rulebooks/
artifacts/normalized-rulebooks/
artifacts/normalized-modules/
artifacts/content-packages/
artifacts/actor-images/
runtime/npc-conversations/
mcp-tasks.sqlite3
```

Clients must not edit these paths directly.

| Variable | Purpose |
|---|---|
| `SAGASMITH_DND_MCP_HOME` | root of all MCP-owned local state |
| `SAGASMITH_DND_MCP_TRANSPORT` | `stdio` or `streamable-http` |
| `SAGASMITH_DND_MCP_HTTP_HOST/PORT/PATH` | HTTP listener; default `127.0.0.1:8767/mcp` |
| `SAGASMITH_AUTH_CONTEXT_SECRET` | minimum 32-byte delegated-auth signing secret |
| `SAGASMITH_DND_MCP_BOUND_PRINCIPAL_ID` | trusted single-user process identity |
| `SAGASMITH_DND_SKILLS_DIR` | repository-local D&D Skills directory |
| `SAGASMITH_MODULEGEN_SKILLS_DIR` | module-generator Skill directory |
| `SAGASMITH_DND_MCP_RULE_IMPORT_ROOTS` | `os.pathsep`-separated rulebook allowlist |
| `SAGASMITH_DND_MCP_MODULE_IMPORT_ROOTS` | `os.pathsep`-separated module allowlist |
| `SAGASMITH_DND_OFFICIAL_CONTENT_LIBRARY` | authorized local Content Library checkout; verifies and mounts the locked 2014 official expansion set inactive |
| `SAGASMITH_DND_MCP_AUTO_SEED=0` | disable bundled core-reference seed |
| `SAGASMITH_DND_MCP_RULE_OCR=0/1` | rulebook OCR toggle |
| `SAGASMITH_DND_MCP_MODULE_OCR=0/1` | module OCR toggle |
| `SAGASMITH_DND_MCP_DENSE_ENABLED=1` | enable embedding-backed retrieval |
| `SAGASMITH_DATABASE_URL` | external Core database URL |
| `CHROMA_DB_URL` / `CHROMA_DB_PATH` | remote/local Chroma configuration |
| `SAGASMITH_DOCUMENT_CACHE_DIR` | optional normalized document/OCR cache root |

Keep secrets outside files, logs, fixtures, and command examples committed to
the repository.

### Official 2014 expansion mount

Set `SAGASMITH_DND_OFFICIAL_CONTENT_LIBRARY` before server startup to mount the
ten locked official supplement/legacy Packs and their exact Player's Handbook
support dependency. The Packs are registered at the same runtime layer as other
core-visible content, but are stored inactive and must be enabled per campaign
through `content_pack(action="activate")`. Their edition metadata is enforced:
2014 campaigns can opt in, while 2024 campaigns cannot advertise or activate
them.

The repository contains only the compatibility registry and checksums. The
commercial archives remain in the authorized local library and are never
returned by profile or inventory APIs. A rights-gated end-to-end regression can
be run locally without exporting content:

```powershell
uv run --package sagasmith-dnd-mcp python packages/mcp/scripts/regression_official_expansions.py `
  --content-library C:\path\to\SagaSmith-dnd-content-library
```

It activates all ten Packs, checks all 2,007 catalog entries and 1,134
selection-ready entries, builds and advances an Artificer/Battle Smith with an
official species and background, and commits an actual rules settlement.

## Workbench gateway

The optional gateway connects to one authoritative Streamable HTTP MCP process;
it does not create a second in-process state owner.

```powershell
pip install "sagasmith-dnd-mcp[gateway]"

$env:SAGASMITH_DND_MCP_TRANSPORT = "streamable-http"
$env:SAGASMITH_DND_MCP_HTTP_PORT = "8767"
sagasmith-dnd-mcp

$env:SAGASMITH_DND_MCP_URL = "http://127.0.0.1:8767/mcp"
$env:SAGASMITH_DND_UI_DIST = "C:\path\to\sagasmith-dnd\apps\ui\dist"
sagasmith-dnd-gateway
```

The gateway defaults to `127.0.0.1:8766`. It provides audience-safe inventory,
detail, import/export, activation/deactivation/removal, combat movement/render,
and revision notifications. Uploads default to 64 MiB and can be bounded with
`SAGASMITH_DND_GATEWAY_UPLOAD_LIMIT`. Gateway bearer authentication protects
the adapter; it does not replace Hosted per-request MCP authorization.

## Observability

The transport propagates bounded `traceparent`, `tracestate`, and `baggage` in
result metadata. Metrics use low-cardinality dimensions only: protocol era,
discover/catalog/tool/projection stage, operation/tool, outcome, and count.
Never label metrics by user, campaign, room turn, run ID, or raw arguments.

## Development, tests, and evaluations

From the repository root:

```bash
uv sync --all-packages --all-extras
uv run --package sagasmith-dnd-mcp pytest packages/mcp/tests
uv run ruff check packages/mcp
```

Focused protocol/quality verification:

```bash
uv run --package sagasmith-dnd-mcp pytest \
  packages/mcp/tests/test_mcp_2026_contract.py \
  packages/mcp/tests/test_mcp_tasks_extension.py \
  packages/mcp/tests/test_tool_contract_quality.py \
  packages/mcp/tests/test_read_only_evaluations.py \
  packages/mcp/tests/test_streamable_http_runtime.py \
  packages/mcp/tests/test_auth_context_protocol.py \
  packages/mcp/tests/test_combat_render.py
```

`evaluations/read_only.xml` contains ten independent, complex, deterministic,
read-only MCP Builder questions. `test_read_only_evaluations.py` creates a fixed
fixture, solves every question through read-only public tools, and verifies the
answers without a paid model or external service. Write-side tests separately
cover idempotency, authorization isolation, stale revisions, concurrency,
cancellation, restart recovery, and structured errors.

The opt-in real-provider corpus regression is documented in
[`docs/FULL_AGENT_REGRESSION.md`](docs/FULL_AGENT_REGRESSION.md). It is not part
of ordinary offline CI and must never commit provider credentials or production
data.

## Deployment, upgrade, and rollback

1. Pin matching Core, Domain/MCP, Agent, Web, and documentation commits.
2. Stop writers and take a consistent backup of the complete MCP home and
   matching component lock before schema changes.
3. Start one authority process per configured state owner. Do not let the
   gateway or UI create another in-process MCP authority.
4. Canary discover/catalog, one audience-safe read, one idempotent write, one
   combat image, structured error propagation, and—when negotiated—one Task
   over every configured transport.
5. Observe auth rejection, stale-revision, task recovery, media, latency, and
   low-cardinality transport/tool metrics before expanding traffic.
6. For Host rollback, use the explicit legacy adapter; never restore session
   identity as authority.
7. Before MCP rollback, stop new module tasks and complete/cancel active tasks.
   Restore database and matching versions together. Retain `mcp-tasks.sqlite3`
   until previous task TTLs have elapsed so older workers cannot lose resumable
   work.

Original code is Apache-2.0. Bundled SRD-derived content and translations retain
their own license/NOTICE terms. Repository visibility is not permission to
redistribute commercial books or user content.
