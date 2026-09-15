# Runtime workflow reference

## Invariants

- Keep the active `campaign_id`, edition, and locale explicit.
- Never mix 2014 and 2024 rules unless the user explicitly requests comparison.
- Search first, then expand only the selected rule or module chunk.
- Trust MCP tool results; do not emulate a successful write.
- Use `lobby` outside play, `play` for live non-combat scenes, and the automatic
  `combat_start`/`combat_end` transitions for combat. The Runtime enforces phase
  authorization on every call; the Host selects task-relevant catalog subsets.
- Runtime character state uses `sheet v2` / `notes v2`; load
  `character-schema-v2.md` before creating or mutating a PC, NPC, or
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
  `rulebook-import.md`; never make an imported PDF executable without
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
  `parsing-agent-edit-loop.md`; rerun the issue loop after every edit
  and call `rulebook_draft(finalize)` only after all hard blockers are resolved
  and the Agent has explicitly confirmed the current draft. Accepted/rejected draft dispositions
  are not frozen decisions. Use `module_draft` for module books; after reviewing
  the current draft and evidence, finalize with an explicit Agent confirmation.
  A Pack contains no caller-authored publication matrix: descriptor validation and
  `metadata.agent_finalization` are the publication boundary. Use `content_pack` only after either
  draft is finalized, and always provide its route `kind` explicitly.
- For module maps or diagrams, follow `module-visual-atlas.md`.
  Text parsing remains fail-closed; only an inspected page image may support a
  `reviewed_image` connection.
- For a real campaign rehearsal or corpus regression, follow
  `../skills/dnd-dm/references/CAMPAIGN_REGRESSION.md`; each campaign must exercise
  source-bound lobby preparation, play settlement, combat, continuity, and
  branch/Snapshot isolation instead of treating successful PDF import as play coverage.
- For creature cards present only as PDF images, follow
  `module-image-content-review.md`; review the managed page before
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
- For long-running PC/NPC continuity, read
  `continuity_context(purpose="actor_memory")`. Its identity, motivational,
  semantic, and episodic tracks are bounded retrieval projections, not a source
  of player intent or a second persistence ledger.
- Treat campaign messages as domain-private. When campaign, authenticated
  principal, role, audience, branch, or restore changes, discard old model
  history, summaries, workspace/Dream memory, cached retrieval, receipts, and
  tool results before continuing. Follow
  `host-integration-bounded-context.md`.
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
  `host-integration-npc-conversation.md`. Use the single
  `npc_conversation` facade; before every ingest/publication, let the Agent rule
  who perceived, understood, and should respond from current scene evidence.
  Dispatch only selected opaque activations, keep one actor-isolated Host worker
  per NPC, and publish only MCP `publication`. Before any authoritative mechanic,
  scene mutation, phase transition, or combat start, close or abort the whole
  conversation atomically and release every worker. Resolve the requested
  mechanic through ordinary public tools, then open a new conversation and
  ingest the result as a new stimulus if dialogue continues. Use the
  signed single-turn `npc_turn` path only for a standalone reaction or Combat;
  follow `host-integration-npc-turn.md` for that current single-turn
  boundary.
- For autonomous actor, player-audience rendering, faction, campaign expansion, source
  interpretation, or Agent-owned ruling isolation, request the matching
  `continuity_context` purpose, run the fixed zero-tool evaluation, and submit
  the proposal to `bounded_evaluation(action="validate")`. Human-owned PCs
  always require the player's intent. The validator changes no state; resolve
  mechanics with ordinary public MCP tools and persist only actual accepted
  outcomes. SagaSmith Agent uses `isolated_evaluate`; other hosts follow
  `host-integration-bounded-context.md`.
- For an emergent campaign, close conversations, leave Combat, and return to
  Lobby before requesting `purpose="campaign_expansion"`. Treat the result as a
  reviewed proposal only, then follow
  `../../dnd-module-generator/references/emergent-campaign.md` to author and append
  an immutable seed or episode shard.
- A complete authored Module is not a world boundary. If players choose a
  source-consistent off-Atlas location, use the same Lobby expansion path and
  append an `emergent_episode` under
  `campaign_mode="authored_with_extensions"`; never mutate the authored root or
  present table-created material as publisher canon.
- A returned `narrative_followup` is a generic Agent review request caused by a
  consequential named-NPC state change. It is not a hard-coded module trigger
  and never authorizes movement, speech, surrender, or item transfer by itself.
- Use `rule_seed_status` before the first rules lookup on a fresh server. Use
  `branch_query(view="compare")` before explaining divergent timelines.

For the complete cross-repository ownership, persistence, adjudication, retrieval,
time, knowledge, manifest, and restore model, read
`long-form-narrative-architecture.md`. See
`mcp-contract.md` and `workflows.md` for the exact public
contract and ordered operations.
