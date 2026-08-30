# D&D Workbench Agent Guide

Follow the repository root `AGENTS.md`. This UI is a projection and control
surface for the authoritative sibling `packages/mcp` gateway.

- Never read or write the MCP database or artifact directories directly.
- Never accept a browser-selected authoritative principal.
- For modern MCP, consume the deterministic authorization-scoped catalog and
  let the Host project a bounded system/phase/task subset; ordinary writes must
  not trigger catalog refresh. Keep `tools/list_changed` handling only in the
  explicit legacy adapter, and never simulate unavailable tools.
- Render only audience-filtered server DTOs and resolution presentations.
- Keep demo mode explicit, read-only, and visually distinct. A live failure must
  never silently fall back to demo data.
- Keep content rights visible; catalog presence is not a license grant.

Validate UI changes from the repository root:

```powershell
npm ci
npm --prefix apps/ui test
npm --prefix apps/ui run build
```
