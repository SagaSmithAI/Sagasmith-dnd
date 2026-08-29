# SagaSmith D&D Domain

[中文](README.md) · [English](README-en.md) · [Install guide](INSTALL.md) ·
[MCP](../mcp/README.md) · [SagaSmith Web](https://github.com/SagaSmithAI/SagaSmith-Web)

> Current source: `sagasmith-dnd/packages/domain`. Archived split repositories
> are not release inputs.

`sagasmith-dnd` is SagaSmith's deterministic D&D 5e 2014/2024 rules and content
runtime. It registers the `dnd5e` plugin through `sagasmith.systems` and provides
the repository-local MCP with tested actor, spell, activity, rule-pack,
content-package, spatial, and combat mechanics.

The Domain package does not own Agent identity or Hosted authorization and is
not a direct browser write API. Skills teach the Agent how to run the game; MCP
owns campaign/actor/role/phase/revision/idempotency authority; Domain settles
already-determined rules inputs into verifiable results.

## Capability boundary

- versioned 2014/2024 core rules, campaign profiles, mechanic IR, and provenance
  locks;
- D&D sheets, derived values, equipment, encumbrance, resources, proficiency,
  spell preparation, and concentration;
- standard array, point buy, rolls, and edition-specific character creation;
- initiative, turns, action economy, attacks/saves/damage, resistance/immunity,
  dying, reactions, and choice windows;
- engine-owned Grid coordinates/geometry and validated Agent-spatial facts;
- non-combat checks, rests, resources, chases, and common actor activities;
- `.sagasmith-pack` schema-v2 content and `sagasmith.actor-card.v3`;
- rulebook, module, actor-card, SRD preset, provenance, license, and checksum
  validation.

The engine automates mechanics only when rules inputs are settled. Intent,
targets, missing line of sight/cover/distance, optional-rule conflicts,
homebrew, NPC choices, and narrative consequences require Agent/GM evidence or
a ruling. MCP preserves that boundary with preflight, choice-window, and
ruling-required results.

## Install and CLI

Python 3.11+ is required:

```bash
pip install sagasmith-dnd
sagasmith-dnd doctor --json
sagasmith-dnd --help
```

The baseline is text-only and does not silently install PDF, image, embedding,
vector, or Torch dependencies:

| Extra | Purpose |
|---|---|
| `documents` | PDF parsing |
| `images` | PyMuPDF + Pillow actor-image extraction |
| `embedding` | Sentence Transformers embeddings |
| `vector` | ChromaDB vector storage |
| `dense` | `embedding` + `vector` |
| `all` | all Domain optional capabilities |

```bash
pip install "sagasmith-dnd[documents]"
pip install "sagasmith-dnd[images]"
pip install "sagasmith-dnd[dense]"
```

Scanned-document OCR belongs to `sagasmith-dnd-mcp[ocr]`, not the Domain
baseline. Agent and Hosted integrations should use the repository-local
[D&D MCP](../mcp/README.md) instead of having a model compose CLI commands or
edit a database.

## Database upgrade and rollback

```bash
sagasmith-dnd database upgrade --json
```

Stop all writers and take a consistent backup before upgrading. Snapshot schema
v8 stores each complete state document as an independent, bounded, checksummed
`zlib-1` record. There is no in-place downgrade. Rollback restores the database,
matching Core/D&D versions, and component lock as one unit.

## Content, actor cards, and memory

`core_rules`, `addon`, `module`, and `preset` use
`sagasmith.content-package` v2 / `.sagasmith-pack` while retaining separate
install and activation authority. Import verifies descriptors, dependencies,
source/blob/asset hashes, D&D semantics, and license metadata; import does not
activate a Pack.

PCs, NPCs, and monsters share `sagasmith.actor-card.v3`. Actor images reference
content-addressed Pack assets and are not copied into runtime actor state or
snapshots. Bundled SRD catalogs and preset builders have exact coverage tests.
Commercial rulebook text is not distributed in the Apache-2.0 software package;
public redistribution requires separately verifiable authorization.

The runtime ships a metadata-only compatibility lock for the ten current
`official_supplement` / `official_legacy` D&D addons in the SagaSmith Content
Library. It covers 2,007 artifacts, including one additional class and 77
subclasses, without embedding any commercial text. Verify an authorized local
library checkout before import or release qualification:

```bash
sagasmith-dnd content verify-official-expansions \
  --path /path/to/SagaSmith-dnd-content-library --json
```

Verification checks the immutable archive identities, current D&D package
semantics, declared artifact totals, and all 1,134 selection-ready materializer
contracts. It performs no download, copies no content, and grants no license.

Objective world facts belong to CampaignMemory and use stable `fact_key`
identities. PC/NPC knowledge, beliefs, rumors, and misconceptions belong to
ActorKnowledge. Character notes and Agent workspace memory are not
authoritative campaign memory.

## Development

From the repository root:

```bash
uv sync --all-packages --all-extras
uv run --package sagasmith-dnd pytest packages/domain/tests
uv run ruff check packages/domain
```

For package-local development:

```bash
pip install -e ".[all,dev]"
pytest --cov
ruff check .
```

Original code is Apache-2.0. SRD-derived content, translations, and third-party
sources retain their applicable NOTICE and license terms.
