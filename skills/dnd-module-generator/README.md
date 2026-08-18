# SagaSmith Module Pack Builder

[Website](https://sagasmithai.github.io) · [Platform overview](https://github.com/SagaSmithAI/.github/blob/main/profile/README.md) · [Hosted service](https://github.com/SagaSmithAI/SagaSmith-service) · [Content catalog](https://github.com/SagaSmithAI/SagaSmith-dnd-content-library)

An AI-native Skill for building reviewed SagaSmith Module Packs with the current
sagasmith.content-package schema version 2.

The Skill is system-aware. It currently documents and validates authoring
decisions for:

- D&D 5e, system_id dnd5e, editions 2014 and 2024;
- Call of Cthulhu 7e, system_id coc7e, Classic and Pulp profiles.

It does not convert one system's manifest into another. The authoring campaign
selects the system, Core owns the portable Package boundary, the system package
owns deterministic parsing and validation, MCP owns authoritative draft state,
and the Agent/Skill owns semantic review.

## Current workflow

~~~text
canonical source
  -> module_draft(start)
  -> module_draft(get/evidence/edit)
  -> module_draft(finalize)
  -> content_pack(get)
  -> optional content_pack(import/activate)
~~~

The default result is a built immutable artifact. Installation and activation
are separate explicit operations.

## Trust boundary

- Never hand-build a final Package descriptor or checksum.
- Never fabricate evidence receipts, dependency checksums, actor identities, or
  scene keys.
- Keep single-book interpretation and repair in the draft evidence/history.
- Keep campaign state, permissions, progress, knowledge, random streams,
  snapshots, and branches outside the portable Pack.
- Keep private and commercial source material local unless lawful distribution
  is explicitly authorized.

## Repository contents

- SKILL.md: canonical operational procedure.
- references/pack-contract.md: common authoring facade and ownership.
- references/system-profiles.md: exact D&D and CoC Package decisions.
- references/source-authoring.md: canonical source and runtime-manifest rules.
- references/review-gates.md: trust-boundary validation.
- references/canonical-example.md: current CoC end-to-end example.
- scripts/validate_skill.py: repository-specific static validator.

## Validation

~~~powershell
python scripts/validate_skill.py .
~~~

The repository is licensed under Apache License 2.0. Source modules and generated
Packs retain their own licenses and distribution constraints.
