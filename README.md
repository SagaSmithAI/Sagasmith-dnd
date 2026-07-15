# SagaSmith D&D UI

**Alpha DM Workbench for the SagaSmithAI D&D reference stack.** The interface brings live-table continuity, current scenes, actors, scoped knowledge, rule provenance, and snapshot lineage into one local operations view.

## Product boundary

This UI is not a second D&D backend and does not write the SagaSmith database directly.

```text
DM Workbench
    ↓ compatible read-only/local gateway
SagaSmith D&D MCP
    ↓
D&D runtime + SagaSmith Core
```

- The MCP remains authoritative for campaign, branch, actor, rule, and combat state.
- Player/GM visibility must be filtered by the server before it reaches the browser.
- Writes should eventually call authenticated MCP-backed gateway operations with revision and idempotency contracts.
- Until that gateway is standardized, the UI ships a complete demo fallback and should be treated as an Alpha design/inspection client.

## Implemented views

- **Live table** — active campaign, `lobby`/`play`/`combat` phase, current scene, party, actor-knowledge counts, and suggested MCP groups.
- **Campaign archive** — edition, locale, status, phase, revision, and dossier navigation.
- **Campaign dossier** — actors, modules, current scene, scene index, knowledge boundaries, and snapshot ancestry.
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

The default gateway URL is `http://127.0.0.1:3000`. Override it at build/dev time:

```powershell
$env:PUBLIC_SAGASMITH_API_BASE = "http://127.0.0.1:3000"
npm run dev
```

The current adapter expects the read endpoints defined in [`src/lib/api.ts`](src/lib/api.ts). Failed health or data requests switch to demo data after a short timeout.

## Static routing

Campaign and character detail pages use query IDs so a static Astro build can open arbitrary runtime identifiers without pre-generating every ID:

```text
/campaigns/detail?id=<campaign-id>
/characters/detail?id=<character-id>
```

Legacy dynamic demo routes remain temporarily in source but new navigation uses the static-safe paths above.

## Development notes

- Shared layout and visual tokens live in `src/layouts/BaseLayout.astro` and `src/styles/global.css`.
- Runtime calls and demo fixtures live in `src/lib/api.ts`.
- React islands own data loading; Astro owns static routing and the application shell.
- Do not add direct SQLite, Chroma, or filesystem access to the browser.

## Status and license

Active Alpha. The UI is suitable for product validation and local visual testing; authenticated MCP-backed writes and a production gateway remain future work. MIT licensed.
