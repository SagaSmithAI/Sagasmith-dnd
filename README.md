# SagaSmith D&D UI

`/library` 是 schema-v2 Content Pack 控制台，统一管理 core rules/addon/module/preset。
Catalog 模式展示 checksum、许可、组件、来源、规则/场景记录、资产和 `actor-card.v3`；
Installed 模式读取 MCP 权威库存并在 Lobby 中执行导入、激活、停用、导出和移除；Drafts
模式只观察 `rulebook_draft` 与 `module_draft` 的 Agent 创作状态。默认 Catalog 是
`https://sagasmithai.github.io/SagaSmith-dnd-content-library/content-library/index.json`，也可用
`PUBLIC_SAGASMITH_LIBRARY_URL` 或 `?source=` 指向兼容索引。

Every structured rule, scene, narrative entry, content review, and actor card has
an independent expandable view; the full package descriptor remains available
for checksum-level auditing.

**Alpha DM Workbench for the SagaSmithAI D&D reference stack.** The interface brings live-table continuity, current scenes, actors, scoped knowledge, rule provenance, and snapshot lineage into one local operations view.

## Product boundary

This UI is not a second D&D backend and does not write the SagaSmith database directly.

```text
DM Workbench
    ↓ principal-aware HTTP/SSE adapter
SagaSmith D&D MCP
    ↓
D&D runtime + SagaSmith Core
```

- The MCP remains authoritative for campaign, branch, actor, rule, and combat state.
- Player/GM visibility must be filtered by the server before it reaches the browser.
- Combat movement writes call the MCP-backed gateway with principal, revision, branch, and idempotency contracts; the browser never writes coordinates directly to storage.
- Content Pack writes are Lobby-only and go through the MCP `content_pack` facade with revision and idempotency contracts. Import never implies activation.
- Catalog and Gateway failures are reported separately. The content and rule control planes do not replace failed live reads with demo data.

## Implemented views

- **Live table** — active campaign, `lobby`/`play`/`combat` phase, current scene, party, actor-knowledge counts, and suggested MCP groups.
- **Campaign archive** — edition, locale, status, phase, revision, and dossier navigation.
- **Campaign dossier** — actors, modules, current scene, scene index, knowledge boundaries, and snapshot ancestry.
- **Scene Atlas** — stable module/chapter order, deep links, scoped progress, source pages, and a graph of only the spatial evidence actually present in the module.
- **Combat map** — encounter-local five-foot grid, audience-filtered tokens, initiative, blocked/difficult cell rendering, drag-to-propose movement, and MCP rejection feedback.
- **Actor dossier** — abilities, combat values, skills, spell resources, equipment, and an explicit actor-knowledge boundary.
- **Rule evidence** — indexed sources, edition filters, hybrid-search candidates, provenance, and the reminder that retrieval is not authoritative state.
- **Content control plane** — public Catalog discovery, exact archive SHA-256 checks, MCP import, campaign-aware inventory, kind-specific activation/removal, export download, schema-v2 audit views, and preset Actor creation.
- **Campaign content** — a dedicated dossier tab that groups the four Pack kinds and refreshes with campaign revisions.
- **Demo mode** — coherent local data when no compatible gateway answers, visibly labeled throughout the interface.

## Run

Requires Node.js 22.12+.

```bash
npm install
npm run typecheck
npm test
npm run dev
npm run build
npm run preview
```

Start the adjacent `SagaSmith-dnd-mcp` gateway (or use `SagaSmith-agent/start.bat`). The default URL is `http://127.0.0.1:8766`. Override its address and audience at build/dev time:

```powershell
$env:PUBLIC_SAGASMITH_API_BASE = "http://127.0.0.1:8766"
# Only when the gateway is configured with SAGASMITH_DND_GATEWAY_TOKEN:
$env:PUBLIC_SAGASMITH_API_TOKEN = "local-development-token"
npm run dev
```

The adapter returns `{ data, meta }` envelopes and pushes campaign revision events over SSE. Server projection is the security boundary: the UI does not use CSS to conceal GM-only scenes, hidden combatants, blocked cells, world patches, or private Pack archives. Content reads remain available during Play/Combat; writes are rejected outside Lobby.

## Static routing

Campaign and character detail pages use query IDs so a static Astro build can open arbitrary runtime identifiers without pre-generating every ID:

```text
/campaigns/detail?id=<campaign-id>
/campaigns/detail?id=<campaign-id>&tab=scenes&scene=<scene-id>&scope=party
/characters/detail?id=<character-id>
/combat?campaign=<campaign-id>
```

## Development notes

- Shared layout and visual tokens live in `src/layouts/BaseLayout.astro` and `src/styles/global.css`.
- Runtime calls and demo fixtures live in `src/lib/api.ts`.
- Scene and combat components live under `src/features/`; placeholder assets are explicitly non-mechanical in `public/placeholders/manifest.json`.
- React islands own data loading; Astro owns static routing and the application shell.
- Do not add direct SQLite, Chroma, or filesystem access to the browser.

## Status and license

Active Alpha. The UI, local principal-aware gateway, Scene Atlas, SSE refresh, and MCP-backed combat movement are ready for integrated local testing. Production identity issuance, TLS termination, and broader mutation workflows remain deployment work. Licensed under Apache-2.0.
