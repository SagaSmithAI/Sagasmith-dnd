# D&D Workbench Agent Guide

Follow the repository root `AGENTS.md`. This UI is a projection and control
surface for the authoritative sibling `packages/mcp` gateway.

- Never read or write the MCP database or artifact directories directly.
- Never accept a browser-selected authoritative principal.
- Refresh the real native tool schema after `tools/list_changed`; do not keep a
  fixed tool catalog or simulate unavailable tools.
- Render only audience-filtered server DTOs and resolution presentations.
- Keep demo mode explicit, read-only, and visually distinct. A live failure must
  never silently fall back to demo data.
- Keep content rights visible; catalog presence is not a license grant.

Validate UI changes from the repository root:

```powershell
npm --prefix apps/ui ci
npm --prefix apps/ui test
npm --prefix apps/ui run build
```
