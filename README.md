# SagaSmith D&D

D&D 5e system package for `sagasmith-core`, with optional nanobot tools and
workspace skills.

This is a new implementation. It does not preserve the database schema or
behavior of earlier SagaSmith repositories.

## Install

```bash
pip install "sagasmith-dnd[nanobot]"
sagasmith-dnd install --workspace ~/.nanobot/workspace
nanobot gateway
```

Dense retrieval is optional:

```bash
pip install "sagasmith-dnd[nanobot,dense]"
```

The package registers four nanobot tools:

- `dnd_campaign`
- `dnd_character`
- `dnd_rules`
- `dnd_module`

Game-neutral persistence, ingestion, and retrieval are provided by
`sagasmith-core`. This package owns D&D validation, mechanics, terminology,
tools, and agent instructions.

