# SagaSmith D&D UI

`/library` 是统一的 core rules/addon/module/preset 浏览器：它显示包 checksum、许可、组件、来源、
规则记录、场景/资产、统一 PC/NPC/怪物卡及包内角色图，并保留完整统一包描述符 JSON
供审计。默认读取 `https://sagasmithai.github.io/SagaSmith-dnd-content-library/content-library/index.json`；可用
`PUBLIC_SAGASMITH_LIBRARY_URL` 或 `?source=` 指向另一份兼容索引。浏览器只读，
不会安装或启用规则内容。

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
- Other mutation workflows remain Agent/MCP operations. The UI keeps a visibly labeled demo fallback for disconnected design and product testing.

## Implemented views

- **Live table** — active campaign, `lobby`/`play`/`combat` phase, current scene, party, actor-knowledge counts, and suggested MCP groups.
- **Campaign archive** — edition, locale, status, phase, revision, and dossier navigation.
- **Campaign dossier** — actors, modules, current scene, scene index, knowledge boundaries, and snapshot ancestry.
- **Scene Atlas** — stable module/chapter order, deep links, scoped progress, source pages, and a graph of only the spatial evidence actually present in the module.
- **Combat map** — encounter-local five-foot grid, audience-filtered tokens, initiative, blocked/difficult cell rendering, drag-to-propose movement, and MCP rejection feedback.
- **Actor dossier** — abilities, combat values, skills, spell resources, equipment, and an explicit actor-knowledge boundary.
- **Rule evidence** — installed sources, edition filters, hybrid-search candidates, provenance, and the reminder that retrieval is not authoritative state.
- **Demo mode** — coherent local data when no compatible gateway answers, visibly labeled throughout the interface.

## Run

Requires Node.js 22.12+.

```bash
npm install
npm run dev
npm run build
npm run preview
```

Start the adjacent `SagaSmith-dnd-mcp` gateway (or use `SagaSmith-agent/start.bat`). The default URL is `http://127.0.0.1:8766`. Override its address and audience at build/dev time:

```powershell
$env:PUBLIC_SAGASMITH_API_BASE = "http://127.0.0.1:8766"
$env:PUBLIC_SAGASMITH_PRINCIPAL_ID = "system:local"
# Only when the gateway is configured with SAGASMITH_DND_GATEWAY_TOKEN:
$env:PUBLIC_SAGASMITH_API_TOKEN = "local-development-token"
npm run dev
```

The adapter returns `{ data, meta }` envelopes and pushes campaign revision events over SSE. Failed health or data requests switch to demo data after a short timeout. Server projection is the security boundary: the UI does not use CSS to conceal GM-only scenes, hidden combatants, blocked cells, or world patches.

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
