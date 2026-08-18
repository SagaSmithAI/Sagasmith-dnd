---
name: sagasmith-modulegen
description: "Create, revise, review, and finalize portable SagaSmith Module or rules Packs for the current sagasmith.content-package v2 contract through a system MCP. Use for D&D 5e or Call of Cthulhu 7e one-shots, scenarios, adventures, campaigns, solo modules, sandboxes, reviewed rulebooks, source revisions, and evidence-bound publication through module_draft, rulebook_draft, and content_pack."
---

# SagaSmith Content Pack Authoring

Build one reviewed source artifact for the active SagaSmith system and deliver
one immutable `sagasmith.content-package` schema-v2 Pack. Use `module_draft` for
Module Packs and `rulebook_draft` for supported rules Packs. Treat installation
and campaign activation as separate optional outcomes.

## Load the relevant references

- Read [pack-contract.md](references/pack-contract.md) before preparing Package
  decisions or calling authoring tools.
- Read [system-profiles.md](references/system-profiles.md) before choosing
  classification, compatibility, play-profile, catalog, or mechanical fields.
- Read [source-authoring.md](references/source-authoring.md) before writing or
  revising Module Markdown and its runtime manifest.
- Read [review-gates.md](references/review-gates.md) before draft start,
  finalization, and optional activation.
- Read [narrative-patterns.md](references/narrative-patterns.md) only when
  selecting a composition pattern or scaling a long work.
- Read [canonical-example.md](references/canonical-example.md) when an exact CoC
  end-to-end call sequence is useful.

## Keep one authority for each concern

- Own source interpretation, story meaning, semantic ids, Package decisions,
  source repair, and final confirmation as the Agent.
- Let Core own portable identity, sources, chunks, assets, reviews, checksums,
  archives, revisions, and transactions.
- Let the active system package own deterministic parsing, canonical mechanics,
  actor schemas, system-specific manifest validation, and Pack compilation.
- Let MCP own authenticated state, authorization, random streams, idempotency,
  draft revisions, immutable archives, installation, activation, and progress
  remaps.
- Never hand-build a final descriptor, checksum, blob path, source receipt,
  actor card, or scene atlas.
- Never promote one book's extraction or interpretation problem into Core, a
  system parser, or MCP. Record it in that draft's evidence and audit history.
- Never copy unrealized secrets, endings, or future branches into runtime
  campaign memory. Persist only outcomes realized during play.

## 1. Bind the current system contract

1. Classify the requested Pack as module or core_rules. Confirm the native tool
   list exposes `content_pack` and the matching `module_draft` or
   `rulebook_draft` facade.
2. Require Lobby phase and a host-bound authenticated campaign Owner or Keeper/
   DM. Never accept a model-supplied principal id.
3. Read the authoring campaign and bind its exact `system_id`. Never infer the
   system from genre, title, filename, or prose.
4. Select the matching profile in
   [system-profiles.md](references/system-profiles.md). This Skill currently
   documents `dnd5e` and `coc7e`; reject any unsupported system rather than
   borrowing another system's fields.
5. Compare the native input schema and capability response with the bundled
   contract. Treat the live server as authoritative and stop on material drift.
   Do not invent a compatibility path or call a fixed-superset fallback.
6. Default to **build only**. Create a campaign, install a Pack, or activate it
   only when the surrounding request authorizes that distinct state change.

## 2. Establish one authoring ledger

Record common decisions before expanding prose:

- exact `system_id`, portable `pack_id`, semantic version, title, language,
  license, attribution, and dependency identities;
- system-valid classification, compatibility, required capabilities, play
  profile, continuity, and activation policy;
- chapter and scene plan with stable semantic headings;
- entities, secrets, clues, plot nodes, foreshadowing, branches, factions, and
  endings with stable lowercase ids;
- every required catalog array and both narrative arrays;
- source evidence needed for every mechanically required profile decision;
- private/copyright status and which artifacts must remain local.

Then add the exact system ledger:

- For `dnd5e`, record editions, start/end levels, advancement, pregen review,
  optional party-size guidance, encounter mechanics, and exact statblocks.
- For `coc7e`, record 7e compatibility, investigator count, Classic/Pulp
  support, era, session estimate, pregen review, solo support, clues, handouts,
  SAN effects, Mythos tomes/spells, chases, and source-preserving statblocks.

Use composition labels such as one-shot, short, long, or sandbox only for
authoring cadence. Use only the selected system's Package classifications.

For core_rules, record exact source identity, title, language, license/privacy,
edition, portable Pack id/version, and the reviewed source areas that must be
searchable. Do not add module scenes, actors, catalogs, or narrative endings.

## Rules Pack branch

Use this branch only when the live system exposes rulebook_draft and the user
requested a rules Pack:

1. Stay in Lobby and start one user-authorized managed PDF, Markdown, or text
   source with a stable source_key and idempotency key.
2. Retain job id/revision, inspect normalization, and use evidence search to
   confirm representative rules and source checksums.
3. Repair source transcription only through supported evidence-bound draft
   operations. Never add one-book parsing heuristics to Core or the system.
4. Finalize the reviewed current revision as an immutable private core_rules
   schema-v2 Pack with a system-prefixed id and explicit confirmation.
5. Inspect the artifact with content_pack get. Stop at build-only unless import
   or activation was separately authorized.
6. When authorized, import inactive, refresh the campaign revision, activate,
   and verify the effective rule lock plus rule search/expand through the native
   rule query facade.

Keep copyrighted sources, normalized text, and private Pack archives local.
After completing this branch, skip the Module-only scene, actor, catalog, and
narrative workflow below.

## 3. Design before expanding

1. Build a scene graph with entrances, available evidence, consequential
   choices, failure paths, persistent consequences, and reachable endings.
2. Give every indispensable revelation multiple reasonable discovery paths.
   A failed check may alter cost, time, risk, or detail; it must not erase the
   only route through a mystery.
3. Separate standard system mechanics from module-specific meaning. Use exact
   validated mechanics where execution requires them and keep narrative-only
   actors explicitly non-executable.
4. Define each important NPC or faction's wants, fears, knowledge, response
   posture, and default action if players do nothing.
5. Record spatial facts only when the source establishes them. Do not synthesize
   maps or geometry from adjacency.
6. For CoC, distinguish obvious clues, roll-gated supplementary information,
   pushed-roll consequences, SAN triggers/loss expressions, and Keeper-only
   truth. Never turn investigation into a D&D-style majority group check.
7. Review the ledger with the user before expanding medium, long, campaign, or
   sandbox works when user review is available.

## 4. Author one canonical source

Write one UTF-8 Markdown document:

```markdown
<!-- sagasmith-runtime-manifest
{ "schema_version": 1, "module_key": "stable-module-key" }
-->
# Chapter
## Scene
### Scene subsection
#### A1. Numbered room, location, or source section
```

Follow [source-authoring.md](references/source-authoring.md):

- include at most one valid runtime manifest;
- keep ids globally unique and stable across revisions;
- use meaningful headings and avoid repeated generic titles;
- keep authored truth separate from discovered actor knowledge and public
  narration;
- put player-facing portable material in the handouts catalog without claiming
  that prose labels grant runtime access;
- include exact source-backed mechanics or mark the content narrative-only;
- integrate all sections into one source before starting a draft.

Run Gate A from [review-gates.md](references/review-gates.md).

## 5. Scale composition without splitting authority

Draft small works directly. For large works, delegation is optional only after
freezing the ledger.

When delegation is available:

- give each worker assigned ids, incoming state, required outgoing state, scene
  contract, system profile, and global constraints;
- prohibit workers from calling MCP, finalizing Packs, inventing dependencies,
  or creating separate runtime manifests;
- make the lead Agent merge the work, resolve duplicate ids/headings, verify
  clue routes and transitions, and produce the single canonical source.

Use sequential drafting when delegation is unavailable.

## 6. Start the editable draft

Call `module_draft(action="start")` only after the canonical source passes its
authoring gate. Supply either `source_path` or generated `name+content`, never
both. Generated work normally includes `name`, complete `content`, `title`,
and stable `source_key`.

Use one idempotency key for the exact request. Retain `job_id`, inactive
`module_id`, state, revision, inspection, validation, and parser profile. A
successful first pass normally reaches `imported`.

If the first pass is interrupted, resume only through the documented
`edit:advance` operation. If validation fails, repair the canonical source or
an applicable draft field from evidence. Do not add a single-book parser
heuristic or weaken validation.

## 7. Review and repair from exact evidence

1. Refresh with `module_draft(action="get")`.
2. Read managed chunks with `module_draft(action="evidence", kind="chunks")`.
3. Copy returned evidence receipts verbatim. Never invent a source key, page,
   chunk hash, dependency checksum, or actor identity.
4. Review scene boundaries, runtime-manifest advisories, chunks, statblocks,
   assets, OCR/transcription diagnostics, checks, and progress impact.
5. Apply the narrowest valid edit:
   - `source_text` for evidence-backed staged-source transcription repair;
   - `content` or `statblock` for reviewed structured content;
   - `asset` for managed source assets;
   - `actor` for already validated actor bindings;
   - `package` for manifest, catalogs, narrative, dependencies, metadata, or
     version decisions;
   - `advance` to resume the mechanical first pass.
6. Pass the latest `expected_revision` and a request-specific idempotency key
   for every write. Refresh after every successful edit.
7. Record a concise evidence/ruling note for every semantic decision.

Resolve errors that affect identity, evidence, required mechanics, source
binding, scene structure, or portability. Treat optional presentation fields,
portraits, and advisory readiness as non-blocking.

## 8. Save exact system Package decisions

Submit only these common Package decision areas through
`module_draft(action="edit", operation="package")`:

`manifest`, `catalogs`, `narrative`, `dependencies`, `metadata`, and
`version`.

The manifest must contain exactly the common semantic fields documented in
[pack-contract.md](references/pack-contract.md), while its classification,
compatibility, play profile, and catalogs must match the selected system profile
exactly. Do not send D&D fields to CoC or CoC fields to D&D.

All required CoC play-profile sections require real source receipts. D&D
requires sourced start/end levels, advancement, and pregen review; party-size
guidance is optional. Use exact current dependencies or an empty array.

## 9. Finalize the immutable Pack

Run Gate C, then call `module_draft(action="finalize")` with the current
`job_id`, system-prefixed portable `pack_id`, final version, and explicit
Agent confirmation.

The confirmation note must name the reviewed surfaces. Never confirm unresolved
required evidence, invalid mechanics, missing required endings, unknown
dependencies, stale revisions, or conflicting source truth.

Require a `compiled` draft and immutable artifact. Inspect it with
`content_pack(action="get", kind="module")` and verify schema-v2 identity,
system, checksum, finalization metadata, source binding, and component counts.

## 10. Deliver, then optionally install

Report:

- artifact handle, Pack id, version, `system_id`, schema, and checksum;
- source key/checksum and final draft job/revision;
- scene, asset, review, actor, catalog, dossier, and ending counts;
- material warnings, local/private handling, and finalization note;
- whether the result is built only, imported, or active.

Stop at the built artifact by default.

When installation is explicitly requested, call
`content_pack(action="import", kind="module")`; import remains inactive. When
activation is also requested, refresh the target campaign revision and activate
the imported `module_id`.

For a replacement, review progress impact and supply only explicit
`from_scene_id` to `to_scene_key` remaps with reasons. Never activate the
mechanical draft, guess a remap, or discard realized progress.
