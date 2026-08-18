---
name: dnd-module-generator
description: Create, review, revise, and finalize D&D 5e 2014/2024 Module or rules Packs through the authoritative SagaSmith D&D MCP authoring facade.
---

# D&D Module Generator

Build one reviewed `sagasmith.content-package` v2 artifact for an authoring
campaign whose authoritative `system_id` is `dnd5e`.

Read [workflow.md](references/workflow.md) before starting or editing a draft.
Read [system-profile.md](references/system-profile.md) before saving Package
decisions. Read [narrative-patterns.md](references/narrative-patterns.md) only
when choosing the composition shape for a long adventure or campaign.

## Boundaries

- Stay in Lobby and use only the current native `module_draft`,
  `rulebook_draft`, and `content_pack` facades exposed by D&D MCP.
- Bind the campaign's exact edition and rule profile. Do not infer them from
  title, genre, filename, or prose.
- Let the Agent own source interpretation and semantic repair; let the D&D
  package own deterministic mechanics and validation; let MCP own revisions,
  evidence receipts, idempotency, finalization, installation, and activation.
- Keep single-book interpretation in the draft evidence and audit history. Do
  not add a book-specific parser heuristic to Core, the D&D package, or MCP.
- Never fabricate evidence, checksums, actor identities, dependencies, scene
  keys, statblocks, or mechanical fields.
- Default to building the immutable artifact only. Installation and campaign
  activation require separate user authorization.

## Completion

Deliver the artifact handle, Pack id and version, checksum, edition, source and
draft revisions, reviewed component counts, material warnings, and whether the
artifact is built, imported, or active.
