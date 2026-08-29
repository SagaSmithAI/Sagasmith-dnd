# SagaSmith D&D Workbench

[Repository](../../README.md) · [MCP](../../packages/mcp/README.md) ·
[SagaSmith Web](https://github.com/SagaSmithAI/SagaSmith-Web) ·
[Public content library](https://github.com/SagaSmithAI/SagaSmith-dnd-content-library)

> Current source: `sagasmith-dnd/apps/ui`. The former standalone D&D UI
> repository is archived and is not a release input.

The D&D Workbench is an Alpha local operations UI for campaigns, scenes,
actors, audience-scoped knowledge, rules, Content Packs, snapshots, and combat.
It is not a second D&D backend.

## Security and data boundary

```text
Browser
  -> principal-aware Workbench gateway
  -> SagaSmith D&D MCP
  -> sagasmith-dnd + sagasmith-core
```

- The MCP is authoritative for campaign, branch, actor, phase, rule, random,
  revision, idempotency, and combat state.
- The browser never reads SQLite, ChromaDB, MCP task state, or artifact
  directories directly.
- Server projection removes GM/private state before it reaches the browser; CSS
  visibility is not an access control.
- Browser-supplied identity is not authority. Hosted identity is issued by the
  trusted Host as a short-lived, D&D-targeted delegation; a local gateway binds
  identity in server configuration or an authenticated upstream session.
- Writes preserve one end-to-end idempotency key and authority revision. A stale
  revision is a recoverable conflict, not permission to silently retry as a new
  operation.
- Catalog and campaign data are separate caches. Ordinary game writes update
  revisioned projections; they do not refresh the modern deterministic MCP tool
  catalog.

## Implemented views

- **Live table**: active campaign, lobby/play/combat phase, current scene,
  party, scoped knowledge counts, and suggested workflow groups.
- **Campaign archive and dossier**: edition, locale, revision, actors, modules,
  scene index, content, and snapshot ancestry.
- **Scene Atlas**: stable module/chapter order, source pages, scoped progress,
  and only the spatial evidence present in the module.
- **Combat map**: audience-filtered tokens, initiative, blocked/difficult cells,
  drag-to-propose movement, and MCP conflict/error feedback.
- **Actor dossier**: abilities, combat values, skills, resources, equipment,
  spells, and an explicit actor-knowledge boundary.
- **Rule evidence**: indexed sources, edition filters, retrieval candidates, and
  provenance; retrieval is not authoritative state.
- **Content control plane**: Catalog discovery, archive SHA-256 verification,
  MCP import/export, campaign-aware inventory, Lobby-only activation/removal,
  schema-v2 audit views, and preset Actor creation.
- **Demo mode**: coherent local fixtures, always visibly labeled. A failed live
  read never silently falls back to demo data.

`/library` supports `core_rules`, `addon`, `module`, and `preset` Packs. Catalog
visibility is not a license grant. The default public index may be configured
with `PUBLIC_SAGASMITH_LIBRARY_URL`; `?source=` may select another explicitly
authorized index. Import validates a Pack but does not activate it.

## Run locally

Node.js 22.12 or newer is required. From the repository root:

```bash
npm --prefix apps/ui ci
npm --prefix apps/ui run typecheck
npm --prefix apps/ui test
npm --prefix apps/ui run dev
```

Build and preview production assets:

```bash
npm --prefix apps/ui run build
npm --prefix apps/ui run preview
```

Start the repository-local gateway separately:

```powershell
pip install "sagasmith-dnd-mcp[gateway]"

$env:SAGASMITH_DND_MCP_TRANSPORT = "streamable-http"
$env:SAGASMITH_DND_MCP_HTTP_PORT = "8767"
sagasmith-dnd-mcp

$env:SAGASMITH_DND_MCP_URL = "http://127.0.0.1:8767/mcp"
$env:SAGASMITH_DND_UI_DIST = "C:\path\to\sagasmith-dnd\apps\ui\dist"
sagasmith-dnd-gateway
```

The gateway listens on `127.0.0.1:8766` by default. Development can override
the browser-visible base URL:

```powershell
$env:PUBLIC_SAGASMITH_API_BASE = "http://127.0.0.1:8766"
# Only if the gateway itself is configured with SAGASMITH_DND_GATEWAY_TOKEN:
$env:PUBLIC_SAGASMITH_API_TOKEN = "local-development-token"
npm --prefix apps/ui run dev
```

Gateway bearer authentication protects the local HTTP adapter; it does not
replace per-request MCP authorization in a Hosted deployment.

## Media and errors

Combat rendering is performed by the MCP after audience projection. The
gateway transports the native MCP image result through its media route without
reconstructing hidden state in the UI or exposing a local artifact path.

Keep protocol errors, structured tool errors, media failures, and campaign
conflicts distinct. The UI should surface safe recovery guidance and retryability
from `structuredContent.error`; a render failure does not invalidate a committed
combat operation.

## Static routes

Detail pages use query IDs so a static Astro build can open arbitrary runtime
identifiers:

```text
/campaigns/detail?id=<campaign-id>
/campaigns/detail?id=<campaign-id>&tab=scenes&scene=<scene-id>&scope=party
/characters/detail?id=<character-id>
/combat?campaign=<campaign-id>
```

## Development notes

- Shared layout and tokens: `src/layouts/BaseLayout.astro`,
  `src/styles/global.css`.
- Runtime adapter and demo fixtures: `src/lib/api.ts`.
- Scene/combat components: `src/features/`.
- Placeholder assets are explicitly non-mechanical in
  `public/placeholders/manifest.json`.
- React islands own data loading; Astro owns static routing and the shell.

Before merging UI changes, run `npm --prefix apps/ui test`, typecheck, and build.
The Workbench is Apache-2.0 licensed. Production TLS, identity issuance, secret
management, and process supervision are deployment responsibilities.
