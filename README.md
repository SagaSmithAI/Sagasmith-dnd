# SagaSmith D&D

[Domain](packages/domain/README.md) · [MCP](packages/mcp/README.md) ·
[Skills](skills/README.md) · [Workbench](apps/ui/README.md) ·
[Platform](https://github.com/SagaSmithAI/.github/blob/main/profile/README.md)

SagaSmith D&D is the vertical monorepo for the D&D 5e 2014/2024 product line.
It keeps each runtime artifact independent while versioning the deterministic
domain package, authoritative MCP server, Agent Skills, gateway, and UI in one
commit.

## Repository layout

```text
packages/domain/              sagasmith-dnd Python package and CLI
packages/mcp/                 sagasmith-dnd-mcp server and gateway
apps/ui/                      D&D workbench
skills/                       D&D Agent procedures and host metadata
skills/dnd-module-generator/  D&D Pack authoring procedure
```

`sagasmith-core` remains an independent system-neutral dependency. Package,
MCP, Skill, UI, container, process, and data boundaries remain independent;
only repository versioning and CI are shared.

This repository is the current source of truth for every D&D component listed
above. The former standalone MCP, Skills, UI, and generic Module Generator
repositories are archived read-only; issues, releases, integrations, and docs
for current D&D development belong here.

## Development

Install and test the Python workspace from this repository root:

```bash
uv sync --all-packages --all-extras
uv run --package sagasmith-dnd pytest packages/domain/tests
uv run --package sagasmith-dnd-mcp pytest packages/mcp/tests
uv run ruff check packages/domain packages/mcp
```

Build the UI independently:

```bash
npm --prefix apps/ui ci
npm --prefix apps/ui run build
```

The package-specific documentation remains in
[`packages/domain/README.md`](packages/domain/README.md),
[`packages/mcp/README.md`](packages/mcp/README.md), and
[`skills/README.md`](skills/README.md).
