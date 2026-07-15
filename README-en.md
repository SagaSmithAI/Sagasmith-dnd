# ⚔️ SagaSmith D&D

[中文](README.md) | [English](README-en.md)

**D&D 5e 2014/2024 runtime** — system plugin and portable JSON CLI for `sagasmith-core`.

> *"The rulebooks are scripture, the dice are the judge."*

`sagasmith-dnd` is a lightweight Python package that registers the `dnd5e` system profile on top of `sagasmith-core`. It is independent of any agent platform — platforms load `SagaSmith-dnd-skills` and operate the same `sagasmith-dnd --json` CLI through their normal shell capability.

---

## Ecosystem

| Repo | Role |
|------|------|
| ⚔️ **sagasmith-dnd** (this repo) | D&D 5e system plugin + CLI |
| 🏗️ [sagasmith-core](https://github.com/dajiaohuang/sagasmith-core) | General engine — DB, docs, RAG |
| 🎲 [SagaSmith-agent](https://github.com/dajiaohuang/SagaSmith-agent) | Complete AI DM runtime |
| 📦 [SagaSmith-dnd-skills](https://github.com/dajiaohuang/SagaSmith-dnd-skills) | D&D agent skill definitions |
| ✍️ [SagaSmith-module-gen-skills](https://github.com/dajiaohuang/SagaSmith-module-gen-skills) | Module generator |

---

## Features

- 🎲 **Rule Engine** — `sagasmith-core`-based retrieval with hybrid search (exact + FTS + semantic), BGE-M3 dense embeddings
- ⚔️ **Combat** — True d20 rolls, initiative, hit/damage, saves, crits, turn tracking, XP
- 🏛️ **Campaign Management** — Create, bind characters, bind rule sets, bind modules
- 👤 **Characters** — D&D 5e 2014/2024 dual-edition sheets, classes, races, spell slots
- 📖 **Modules** — PDF/Markdown import, structure-aware parsing, scene indexes, bilingual scene merging
- 🧩 **Scene Progress** — Scoped to `party` / `group:<id>` / `player:<id>`, transparent inheritance from party
- 💾 **Snapshot** — DAG save/load/verify, branch-aware memory, recap generation
- 🗂️ **Events & Memory** — Discovery event log, revisioned campaign memory, natural language query

---

## Quick Start

```bash
# Install
pip install "sagasmith-dnd[documents]"

# Check runtime health
sagasmith-dnd doctor --json

# Create a campaign
sagasmith-dnd campaign start --name "Gate of the Abyss" --edition 2024 --locale zh --json

# Import rules
sagasmith-dnd rules ingest --path ./srd/2024 --edition 2024 --locale en --json

# Import a module
sagasmith-dnd module ingest --campaign <id> --path ./module.pdf --json

# Query current scene
sagasmith-dnd module current --campaign <id> --scope party --json

# Update progress
sagasmith-dnd module set-progress --campaign <id> --scope party --scene <scene-id> --progress 50 --room "A1. Cellar" --state '{"visited_rooms":["A1"]}' --json

# Save campaign
sagasmith-dnd save create --campaign <id> --label "Before entering the dungeon" --json
```

---

## D&D Profile Scene Parsing

`DndModuleProfile` implements D&D-specific scene boundary detection in `scene_boundaries()`:

- **Automatic level detection** — H2 by default; promotes to H3 when H3 count >= H2 × 5
- **Preamble extraction** — content between chapter title and first scene becomes its own scene
- **Subsections & rooms** — one level below scene becomes subsection, two levels below becomes `room`
- **Bilingual merging** — adjacent CN/EN paired headings (e.g. `酒馆` / `Tavern`) merge into one scene
- **Tag classification** — auto-tags scene as `combat` / `exploration` / `dungeon` / `social` / `transition` based on title keywords

---

## Optional rule-pack runtime

The D&D package owns the safe mechanic IR in `schemas/mechanic-ir-v1.schema.json`.
It accepts only declared events, predicates, and whitelisted operations; it never
loads Python or expressions from an imported book. `validate_source_bound_mechanics()`
additionally verifies the canonical Core source id, source SHA-256, chunk id, heading
path, and page range supplied by the MCP import workflow.

At settlement, the immutable `dnd5e.core.2014` or `dnd5e.core.2024` provider and all
enabled optional packs compile into one `ResolutionContext`. The combined fingerprint
and every applied Core/extension mechanic receipt are persisted with the same atomic
mutation. Non-combat checks enter this path through MCP `character_check`; combat
checks use `combat_check`.

---

## Optional Extras

| Extra | Purpose |
|-------|---------|
| `dense` | sentence-transformers + ChromaDB vector retrieval |
| `documents` | PDF parsing |
| `all` | All extras |

Dense retrieval is optional and falls back to exact/lexical search when unavailable.

---

## Development

```bash
pip install -e ".[all,dev]"
pytest --cov
ruff check .
```

---

## Credits

- D&D 5e SRD 5.2.1 © Wizards of the Coast, used under [CC-BY-4.0](https://creativecommons.org/licenses/by/4.0/)
- [SagiriWWW/DND.SRD.zh-CN](https://github.com/SagiriWWW/DND.SRD.zh-CN) — D&D 5e SRD 5.1 Chinese translation

---

## License

MIT
