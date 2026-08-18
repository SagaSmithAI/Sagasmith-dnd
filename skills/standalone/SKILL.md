---
name: dnd-dm-lite
description: "D&D 5e 2014/2024 standalone — no pip install needed. File-based persistence, zero dependencies."
---

# D&D Dungeon Master (Standalone)

Standalone mode uses **Python stdlib only** (`portable.py`) + plain Markdown files.
No `sagasmith-dnd` runtime required.

## Quick Start

```powershell
python portable.py doctor
python portable.py campaign start --name "Campaign" --edition 2024
python portable.py character create --campaign <id> --name "Aragorn" --sheet '{"hp":10}'
python portable.py module ingest --campaign <id> --path "module.md" --title "Keep"
python portable.py module current --campaign <id> --scope party
```

Data lives in `~/.sagasmith/`. Create a campaign first.

## Turn Loop

1. Resolve scope (`party`/`group:<id>`/`player:<id>`). Run `module current`.
2. Read that scope's scene. Search if the scene lacks the needed fact.
3. Ask player intent.
4. Search rules via `rules search` before resolving disputed mechanics.
5. Roll openly.
6. Narrate without changing established facts.
7. Merge new state into that scope's progress.
8. Persist events, state, memory.
9. Save at decision points and chapter transitions.

## Commands

### Campaign

```powershell
python portable.py campaign start --name "Campaign" --edition 2024
python portable.py campaign list
python portable.py campaign get --campaign <id>
```

### Characters

```powershell
python portable.py character create --campaign <id> --name "Aragorn" --sheet '{"str":15,"dex":14,"con":13,"int":10,"wis":12,"cha":8}'
python portable.py character list --campaign <id>
python portable.py character get --campaign <id> --name "Aragorn"
```

Portable character files are unvalidated JSON drafts. They are not the Runtime
`sheet v2` / `notes v2` contract and do not provide granular inventory, wallet,
equipment, prepared-spell, effect, resource, or NPC-memory mutations. For a
complete PC, NPC, or monster card, use the Full Runtime skill and
`references/character-schema-v2.md` there.

### Modules

```powershell
python portable.py module ingest --campaign <id> --path "module.md" --title "Module"
python portable.py module index --campaign <id>
python portable.py module current --campaign <id> --scope party
python portable.py module read-scene --campaign <id> --scene <id>
python portable.py module search --campaign <id> --query "<situation>" --limit 5
python portable.py module set-progress --campaign <id> --scope party --scene <id> --progress 50 --room "A1" --state '{"discovered":["key"]}'
```

Import Markdown only. Convert PDF first.

### Rules

```powershell
python portable.py rules search --campaign <id> --query "<question>" --limit 5
```

Searches the bundled SRD in `skills/dnd-dm/srd/`. Chinese queries auto-expand
with English equivalents.

### Dice

```powershell
python portable.py roll dice --expression "2d6+3"
python portable.py roll check --dc 15 --score 16 --proficient --level 5
python portable.py roll check --dc 12 --score 14 --advantage
python portable.py roll attack --ac 17 --score 18 --proficient --level 5
```

### Events & Memory

```powershell
python portable.py event add --campaign <id> --type discovery --summary "Event" --payload '{}'
python portable.py memory upsert --campaign <id> --fact-key location:key:state --type fact --subject "Key" --content "Value" --evidence-event <event-id> --expected-revision 0
python portable.py memory search --campaign <id> --query "key"
python portable.py memory history --campaign <id> --fact-key location:key:state
```

Standalone memory is an append-only revision log. Reuse the same stable
`--fact-key` when truth changes and pass the revision returned by the prior write
as `--expected-revision`; a mismatch is a conflict that requires a fresh read.
`memory list` and `memory search` project only each fact's current revision, while
`memory history` exposes its provenance. This local ledger is not synchronized
with Full Runtime CampaignMemory.

### Save / Restore

```powershell
python portable.py save create --campaign <id> --label "Before boss"
python portable.py save list --campaign <id>
python portable.py save restore --campaign <id> --slot 3
```

## Reference

- `skills/dnd-dm/references/DM_RULES.md` — adjudication guide
- `skills/dnd-dm/references/DM_TEMPLATES.md` — output templates
- `skills/dnd-dm/references/CHAR_CREATION.md` — character creation
- `skills/dnd-dm/references/MODULE_INDEX.md` — scene navigation
- `skills/dnd-dm/references/MODULE_ARC.md` — narrative structure

## Limitations

- No PDF import — convert to Markdown first
- No SQL FTS5 — lexical scoring with CJK query expansion
- No ChromaDB vector search
