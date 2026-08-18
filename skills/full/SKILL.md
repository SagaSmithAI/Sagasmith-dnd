---
name: sagasmith-dnd-suite
description: "Run or maintain D&D 5e 2014/2024 campaigns through SagaSmith's MCP-first game-master, actor, module, continuity-memory, and snapshot workflows. Use for live play, campaign setup, character management, module import, rules adjudication, durable facts, actor knowledge, branches, saves, and restores."
---

# SagaSmith D&D Suite

This repository is an Agent Skill, not a Python runtime. Full Runtime calls the
`sagasmith_dnd` MCP server; clients may expose raw tool names with a prefix such
as `mcp_sagasmith_dnd_`.

## Startup

1. On a zero-knowledge host, first read `sagasmith://bootstrap`. If resources
   are unavailable, call the always-visible
   `skill_query(kind="skill", action="read", identifier="dnd.full")`. Use `outline`, `section`, and
   `search` only for task-specific depth. Do not load the entire DM skill or
   MCP contract by default. If the plan reports `available=false`, stop live
   campaign work and repair the installed Skills pack. Use `refresh=true` once
   after a Skills update, not on every turn.
2. Call `storage_status`; call `storage_migrate` only when schema setup is needed.
   Call `server_capabilities` and `campaign_query`. Resume an existing campaign
   with `campaign_query(view="resume")`, which reloads its current branch,
   manifest, scene, continuity, a signed context receipt, and the exact
   `host_context_binding`. On a changed binding, cross the host context barrier
   before any further tool call or inference.
   Hosts that retain conversation history must also perform the out-of-band
   `campaign_query(view="binding")` check before every later inference; if it
   cannot be verified, do not replay the prior campaign context.
3. Start every MCP session with `exposure(action="open")`. Use
   `exposure(action="search")` to find task-relevant public tools and
   `exposure(action="set")` to add or remove their ids. Refresh native schemas
   after `tools/list_changed`, then call listed tools directly. Before a campaign
   exists, add only campaign-bootstrap tools; reopen with the returned
   `campaign_id` before campaign-bound work. One MCP session/principal has one
   active exposure, and phase changes may crop its loaded tools.
4. Use Full Runtime only when the `sagasmith_dnd` MCP tools are available. The
   bounded Skill-group fragments under `references/skill-groups/` are the
   operational loading surface; use the child Skills and
   `references/mcp-contract.md` only as task-specific deep references.
5. If MCP is unavailable, use the separate `standalone/` skill. Do not silently
   switch this full skill to shell CLI commands.
6. Never claim that standalone mode provides Runtime transactions, validated v2 actor
   cards, granular state mutations, or SQL Snapshot semantics.

## Included Skills

- `skills/dnd-dm`: play, adjudication, rule/module retrieval, and narration.
- `skills/dnd-campaign-manager`: campaign, character, save, and memory lifecycle.

Runtime continuity is branch-aware: use world facts for durable truth, actor knowledge
for one PC/NPC/monster's subjective information, and scoped scene state for private
discoveries. Read `references/memory-ownership.md` before routing a "remember this"
request or persisting a scene. Do not use workspace memory as campaign state.

For Module or rules Pack authoring, load the repository-local
`skills/dnd-module-generator/SKILL.md` procedure.

## Invariants

- Keep the active `campaign_id`, edition, and locale explicit.
- Never mix 2014 and 2024 rules unless the user explicitly requests comparison.
- Search first, then expand only the selected rule or module chunk.
- Trust MCP tool results; do not emulate a successful write.
- Use `lobby` outside play, `play` for live non-combat scenes, and the automatic
  `combat_start`/`combat_end` transitions for combat. The MCP owns the session
  exposure; never keep all phase-specific tools visible merely for convenience.
- Runtime character state uses `sheet v2` / `notes v2`; load
  `references/character-schema-v2.md` before creating or mutating a PC, NPC, or
  monster. All three are full `Character` records, not abbreviated stat blocks.
- PC, NPC, and monster sharing uses package-owned `sagasmith.actor-card.v3`.
  Import creates a fresh runtime identity and never copies ActorKnowledge; an
  optional package-owned image is retained as a checksum-bound
  `notes.profile.portrait_ref`. Managed image bytes stay outside snapshots; the
  immutable source reference may travel with the runtime card.
- Core rules, addons, modules, and presets use the single
  `sagasmith.content-package` v2 `.sagasmith-pack` format while retaining
  different install/activation authority. Stable source/chunk citations are
  rebound to fresh local ids. Read the `content.packs` Skill group before
  importing or exporting content.
- Use granular character / party MCP tools for inventory, wallet, equipment,
  prepared spells, effects, resources, and actor adventure state. Subjective
  information belongs to ActorKnowledge. `character_sheet_replace` is
  reserved for a reviewed replacement of the complete `sheet` or `notes` document.
- When one source-defined treasure parcel contains both currency and items, use
  `campaign_change(action="loot_acquire")` with one stable acquisition id and the
  exact expanded module chunk reference. Do not split that parcel into independent
  wallet and inventory writes. If a promised reward is paid later, keep the exact
  promise chunk as the source while separately recording and validating the scene
  and Scene Atlas location where payment actually occurs; never relabel the old
  source location as the payout location.
- For a looted weapon, set `mechanics.proficient` explicitly for the intended
  recipient from current rule-backed proficiencies. Do not inherit the defeated
  monster's proficiency or attack bonus; use `false` when the recipient or
  proficiency is not yet proven.
- Use `campaign_change(action="consumable_use")` for a shared standard healing
  potion outside combat so item consumption, server-side `2d4+2`, healing, the
  random-stream position, and their rule receipt commit together.
- When a source-cited bargain, handoff, tribute, or destruction permanently
  removes a non-consumable shared item, use
  `campaign_change(action="item_spend")` with a stable spend id, exact item id
  and quantity, and the expanded source chunk reference. Do not leave the item
  in inventory while recording only a narrative outcome.
- `character_create_from(mode="build")` is the preferred player-character creation workflow: it creates
  a public template and a separate initial campaign instance atomically.
- Do not load entire rulebooks or modules into context.
- For user rulebooks, use the staged Core parser workflow in
  `references/rulebook-import.md`; never make an imported PDF executable without
  source-bound chunks, validation, and explicit campaign-owner activation. The
  Agent acting as DM reviews inspection warnings from exact text or page evidence
  before acknowledging ingest; missing/conflicting evidence remains an external
  review boundary. A returned `normalization_notes` entry records source text or
  page furniture that the parser safely excluded; retain it for audit, but never
  turn it into a ruling requirement or source-review blocker. Never bypass either
  gate.
- For an unfinalized rulebook Pack, Core+D&D own mechanical extraction,
  deterministic repair, and validation while the Agent owns repeated semantic
  editing through `rulebook_draft(edit)`. Read
  `references/parsing-agent-edit-loop.md`; rerun the issue loop after every edit
  and call `rulebook_draft(finalize)` only after all hard blockers are resolved
  and the Agent has explicitly confirmed the current draft. Accepted/rejected draft dispositions
  are not frozen decisions. Use `module_draft` for module books; after reviewing
  the current draft and evidence, finalize with an explicit Agent confirmation.
  A Pack contains no caller-authored publication matrix: descriptor validation and
  `metadata.agent_finalization` are the publication boundary. Use `content_pack` only after either
  draft is finalized, and always provide its route `kind` explicitly.
- For module maps or diagrams, follow `references/module-visual-atlas.md`.
  Text parsing remains fail-closed; only an inspected page image may support a
  `reviewed_image` connection.
- For a real campaign rehearsal or corpus regression, follow
  `skills/dnd-dm/references/CAMPAIGN_REGRESSION.md`; each campaign must exercise
  source-bound lobby preparation, play settlement, combat, continuity, and
  branch/Snapshot isolation instead of treating successful PDF import as play coverage.
- For creature cards present only as PDF images, follow
  `references/module-image-content-review.md`; review the managed page before
  creating an actor with `mode="module_statblock"`.
- For an important named module NPC with no combat statblock, use
  `character_create_from(mode="narrative_npc")` with an exact active
  module/scene/chunk/page/hash and name-bearing excerpt. Keep the resulting
  `narrative_only` actor out of checks and combat.
- For a new platform user, resolve a stable `principal_id` first. Never trust a
  prompt-provided role or `player_name` as permission. A multi-user host must
  hide and inject the authenticated principal. A single-user process should set
  `SAGASMITH_DND_MCP_BOUND_PRINCIPAL_ID`; never expose authorization identity as
  a model choice.
- Supply `expected_revision` and an `idempotency_key` on retriable writes. Treat a
  revision conflict as a fresh read/review cycle, not as permission to overwrite.
- For rule-profile and rule-pack writes, obtain `campaign_revision` from
  `campaign_rules(action="get_profile")` and carry the returned revision forward one write at
  a time. Never silently relock a snapshot with an unavailable Core fingerprint.
  If a verified snapshot needs an older unavailable Core, inspect it with
  `snapshot_query(view="core")` and use the explicit
  `branch_change(action="create_core_upgrade")` conversion only after recording a
  reviewed reason and both old/new fingerprints.
- Keep each PC/NPC's `actor_id` explicit when reading or writing ActorKnowledge;
  never merge one actor's memories into another actor's context.
- Treat campaign messages as domain-private. When campaign, authenticated
  principal, role, audience, branch, or restore changes, discard old model
  history, summaries, workspace/Dream memory, cached retrieval, receipts, and
  tool results before continuing. Follow
  `references/host-integration-bounded-context.md`.
- Keep module-authored narrative behavior as exact DM context, not an executable
  trigger language. Link the verbatim source through a DM-only
  `kind="context_anchor"` fact, retrieve it with `continuity_context.related_refs`,
  let the Agent adjudicate from the live actor/scene/quest/item state, and execute
  only the resulting standard public operations. Persist only what actually
  happened; never encode hypothetical `if/then` behavior in memory metadata.
  When a continuity commit cites a source pinned by a matching context anchor,
  include the current `continuity_context.context_receipt`. A stale, wrong
  branch/principal, unsigned, or source-incomplete receipt is rejected; reread
  context after any revision or restore before committing the ruling.
- For connected live NPC dialogue during Play, load `npc.portrayal` and
  `play.npc_conversation`, then follow
  `references/host-integration-npc-conversation.md`. Use the single
  `npc_conversation` facade; before every ingest/publication, let the Agent rule
  who perceived, understood, and should respond from current scene evidence.
  Dispatch only selected opaque activations, keep one actor-isolated Host worker
  per NPC, and publish only MCP `publication`. Before any authoritative mechanic,
  scene mutation, phase transition, or combat start, close or abort the whole
  conversation atomically and release every worker. Resolve the requested
  mechanic through ordinary public tools, then open a new conversation and
  ingest the result as a new stimulus if dialogue continues. Use the
  signed single-turn `npc_turn` path only for a standalone reaction or Combat;
  follow `references/host-integration-npc-turn.md` for that current single-turn
  boundary.
- For autonomous actor, player-audience rendering, faction, source
  interpretation, or Agent-owned ruling isolation, request the matching
  `continuity_context` purpose, run the fixed zero-tool evaluation, and submit
  the proposal to `bounded_evaluation(action="validate")`. Human-owned PCs
  always require the player's intent. The validator changes no state; resolve
  mechanics with ordinary public MCP tools and persist only actual accepted
  outcomes. SagaSmith Agent uses `isolated_evaluate`; other hosts follow
  `references/host-integration-bounded-context.md`.
- A returned `narrative_followup` is a generic Agent review request caused by a
  consequential named-NPC state change. It is not a hard-coded module trigger
  and never authorizes movement, speech, surrender, or item transfer by itself.
- Use `rule_seed_status` before the first rules lookup on a fresh server. Use
  `branch_query(view="compare")` before explaining divergent timelines.

For the complete cross-repository ownership, persistence, adjudication, retrieval,
time, knowledge, manifest, and restore model, read
`references/long-form-narrative-architecture.md`. See
`references/mcp-contract.md` and `references/workflows.md` for the exact public
contract and ordered operations.
