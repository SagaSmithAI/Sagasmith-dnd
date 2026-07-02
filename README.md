# SagaSmith D&D

D&D 5e 2014/2024 Runtime for `sagasmith-core`.

The package is independent of nanobot. Agent platforms load
`SagaSmith-dnd-skills` and operate the same compact JSON CLI through their
normal shell capability.

```bash
pip install "sagasmith-dnd[dense]"
sagasmith-dnd doctor --json
sagasmith-dnd campaign start --name Keep --edition 2014 --locale zh --json
```

Rules are imported explicitly:

```bash
sagasmith-dnd rules ingest --path ./srd/2014-zh --edition 2014 --locale zh --publication srd-5.1-zh --json
```

PDF support comes from the `documents` extra of `sagasmith-core`. Dense retrieval
is optional and falls back to exact/lexical search.
