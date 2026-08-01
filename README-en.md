# SagaSmith D&D

[中文](README.md) · [English](README-en.md) · [D&D MCP](https://github.com/SagaSmithAI/SagaSmith-dnd-mcp)

**The D&D 5e 2014/2024 rules runtime for SagaSmithAI.** This package implements testable character, spell, activity, rule-pack, spatial, and structured-combat logic, and registers the `dnd5e` plugin through `sagasmith.systems`.

> Skills teach an agent how to run the table. MCP controls capabilities. This package turns settled rules inputs into verifiable outcomes.

## Platform role

```mermaid
flowchart LR
    A[Agent] --> M[SagaSmith D&D MCP]
    M --> S[D&D Skills]
    M --> D[sagasmith-dnd]
    D --> C[sagasmith-core]
```

Agents should normally connect through [SagaSmith-dnd-mcp](https://github.com/SagaSmithAI/SagaSmith-dnd-mcp), not construct CLI commands or write databases directly. The CLI remains useful for development, diagnostics, and portable integrations; the Python package is the rules/content implementation layer.

## Implemented capabilities

- **Versioned core rule packs** — 2014/2024 packs, profiles, mechanic IR, extension composition, and provenance locks.
- **Character data** — D&D sheet validation, derived stats, weapons/ammunition, encumbrance, resources, proficiencies, and restorable state.
- **Character creation** — standard array, point buy, rolling, edition differences, and structured core class/species/background/feat content.
- **Spells** — spell data, casting resources, preparation, concentration, readied spells, targets, saves, and timing boundaries.
- **Structured combat** — initiative, turns, action economy, attack preflight/commit, typed damage, resistance/immunity, unconsciousness, death saves, reactions, and choice windows.
- **Spatial semantics** — module location evidence, temporary maps created when combat starts, movement, distance, reach, and opportunity attacks.
- **Non-combat activities** — checks, rests, resources, and common activities with guards against bypassing the combat state machine.
- **Content ingestion** — D&D module profiles, structured core content, and extension rulebook draft/validation flows.
- **Portable actors** — one D&D actor-card validator for PCs/NPCs/monsters, plus bundled SRD 2014/2024 creature preset packs.

## Unified actor cards and bundled creature packs

`build_dnd_actor_card` / `validate_dnd_actor_card` apply D&D sheet-v2 and
edition validation on top of the Core portable envelope. PCs, NPCs, and monsters
do not have separate sharing formats: each is a complete actor card, separated
only by `actor_type`, provenance, and card mechanics.

`build_srd2014_preset_pack` produces 317 cards from the bundled SRD 5.1
Markdown; `build_srd2024_preset_pack` produces 330 from SRD 5.2.1. Every card
retains the normalized statblock, source text/checksum/reference, parser
warnings, license, and attribution. Actionless 2014 creatures and speed 0 are
valid. A 2024 MOD/SAVE-only table uses a canonical representative score that
preserves the printed modifier/save exactly and records that normalization in
provenance instead of inventing an unsupported odd/even score.

Removing monster hardcoding means identities, statistics, attacks, traits, and
sources no longer come from creature-name tables or constructors. Generic
standard-rule implementations remain engine code—action economy, attacks,
saves, damage, defenses, and card-declared generic traits—but read their inputs
from cards rather than branching on monster names. Homebrew semantics still use
the source-bound first-use Agent interpretation and saved-solution boundary.

## Long-term memory boundary

- Objective world facts belong to Core CampaignMemory and use stable `fact_key`
  identities; the CLI exposes `memory upsert/revise` for diagnostics.
- A PC or NPC's memories, beliefs, rumors, and misconceptions belong to ActorKnowledge.
- `character.notes.memories` remains only for legacy character documents.
  `character memory migrate` emits ActorKnowledge candidates; new features must
  not treat the embedded list as authoritative memory.
- `continuity commit --payload ...` atomically persists a scene event, fact
  changes, actor knowledge, and an optional snapshot.

## Automation versus rulings

The engine automates mechanics only when rules inputs are settled: attack bonus, AC, dice expression, damage type, save DC, resources, and current state. It must not invent intent, targets, line of sight, cover, hidden state, missing distances, optional-rule selection, precedence, homebrew, NPC decisions, or narrative consequences.

The MCP layer represents uncertainty through preflight results, choice windows, and ruling-required responses. The active SagaSmith Agent acts as GM and supplies ordinary scene, module, spell, and narrative judgments by default before the engine commits deterministic effects. Only player-owned choices, owner approvals, permission changes, and missing or conflicting source evidence require external input.

## Install and CLI

Requires Python 3.11+:

```bash
pip install "sagasmith-dnd[all]"
sagasmith-dnd doctor --json
sagasmith-dnd --help
```

| Extra | Purpose |
|---|---|
| `documents` | PDF parsing |
| `dense` | sentence-transformers + ChromaDB |
| `all` | document, embedding, and vector dependencies |

## Extension rule packs

Extensions do not override the core through scattered conditionals. Ingestion produces a provenance-bearing draft pack, validates schema, dependencies, edition, and mechanic IR, then binds the pack to a campaign profile. Campaigns lock exact core/extension versions, and snapshot restoration requires the same dependency set.

This allows legally owned supplements to add subclasses, backgrounds, spells, and executable mechanics without losing the 2014/2024 core boundaries and regression fixes. Commercial book content is not distributed with this repository.

Supplements can also ship as a composed package family: keep rules in an
edition/dependency/source-locked rule pack; distribute pregenerated PCs, NPCs,
monsters, and summons in a portable `preset_pack`; place adventures, maps, and
scenes in a separate `module_pack`. Link them with explicit dependencies instead
of one giant package that bypasses rule-install approval.

## Development

```bash
pip install -e ".[all,dev]"
pytest --cov
ruff check .
```

Tests cover rule packs, core content, preserved rule boundaries, character schemas, spells, lifecycle, spatial behavior, and combat.

## Content and license

Original code is licensed under Apache-2.0. D&D 5e SRD-derived content follows the applicable CC-BY-4.0 terms; convenience translations retain upstream attribution. Non-SRD commercial content must be imported by an authorized user.
