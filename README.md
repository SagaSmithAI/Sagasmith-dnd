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

## Local Agent Kit install profiles

The text-only authoritative MCP keeps the Local Kit baseline small:

```bash
pip install sagasmith-dnd-mcp
sagasmith-dnd-mcp
```

That baseline provides SQLite state, Markdown/text content, FTS retrieval, and
the native MCP contract. Install only the capability profiles a local host
actually uses:

```bash
pip install "sagasmith-dnd-mcp[documents]"  # PDF text and page handling
pip install "sagasmith-dnd-mcp[images]"     # portraits and combat PNGs
pip install "sagasmith-dnd-mcp[ocr]"        # scanned-PDF OCR
pip install "sagasmith-dnd-mcp[dense]"      # embeddings plus vector storage
pip install "sagasmith-dnd-mcp[all]"        # every optional runtime capability
```

Heavy document, image, OCR, embedding, and vector libraries load only when the
corresponding capability is called. Missing capabilities return an install
instruction instead of preventing text MCP startup. Cross-system Local Agent
Kit manifests remain owned by `SagaSmith-agent`; this vertical repository owns
only the D&D package/extras contract.

## Verified integration baseline

The 2026-08-20 hosted regression uses the current SagaSmith Agent and Service,
signed `sagasmith.auth-context/v1` principal context, session-scoped dynamic MCP
tools, and this repository's Domain/MCP/Skills revision. The D&D reference
campaign ran concurrently with the CoC reference campaign without a reported
regression gap and recorded a legal D&D ending. The catalog runner also records
every discovered module and any explicit exclusion in machine-readable output;
this reference result does not claim every mutually exclusive path was played.

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
