# SagaSmith D&D Agent Guide

## Repository boundary

This is the current vertical source repository for the D&D product line:

- `packages/domain` owns deterministic D&D 5e mechanics and canonical schemas.
- `packages/mcp` owns authoritative state, authorization, revisions, random
  streams, idempotency, settlement, and request-scoped tool enforcement.
- `skills` owns reusable Agent procedures and module-authoring review.
- `apps/ui` is the D&D Workbench and must use the authenticated gateway/MCP
  contract rather than direct state access.

The former standalone D&D MCP, Skills, UI, and generic Module Generator
repositories are archived. Do not restore them as dependencies, mirrors,
compatibility paths, or documentation authorities.

## Placement rules

- Keep source interpretation, perception, audience, narrative geometry, and
  module-specific meaning in the Agent/Skills or Pack evidence.
- Put only reusable, deterministic D&D mechanics in `packages/domain`.
- Put one authoritative write check at the MCP boundary; do not duplicate it in
  UI or Skills.
- Preserve the deterministic, cacheable MCP catalog. Host-side phase/task
  selection may present a stable subset to a model, but `tools/list` must not
  change because another call mutated exposure state. Legacy list-changed
  behavior is a compatibility adapter, never an authority boundary.
- In agent spatial mode, never synthesize coordinates. Grid mode alone owns
  engine-resolved coordinates and geometry.

## Validation

```powershell
uv sync --all-packages --all-extras
uv run --package sagasmith-dnd pytest packages/domain/tests
uv run --package sagasmith-dnd-mcp pytest packages/mcp/tests
uv run ruff check packages/domain packages/mcp
npm --prefix apps/ui ci
npm --prefix apps/ui test
npm --prefix apps/ui run build
```

Run only the checks proportional to the changed boundary, plus an integration
path when a public contract changes.
