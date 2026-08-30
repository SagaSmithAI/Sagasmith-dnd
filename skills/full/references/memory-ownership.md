# Memory Ownership and Routing

Use this reference whenever a player says "remember this", after a resolved
scene, or when two stores appear to contain the same information.

## Owner matrix

| Information | Authoritative owner | Write path | Never store as |
|---|---|---|---|
| User identity, accessibility needs, table tone, stable play preferences | SagaSmith Agent Dream | Dream consolidation into `USER.md` or workspace memory | Campaign fact or actor secret |
| What objectively happened | Campaign event ledger | `memory_change(action="commit").event` or isolated `campaign_event(action="add")` | Workspace memory or character notes |
| Current objective world truth | CampaignMemory revision ledger | Stable-key fact in `memory_change(action="commit").facts`; administrative `memory_change` | ActorKnowledge or duplicated prose note |
| What one PC/NPC believes, knows, forgot, or was told | ActorKnowledge revision ledger | Exact actor item in `memory_change(action="commit").actor_knowledge`; administrative `actor_knowledge_change` | World fact, another actor's ledger, or workspace memory |
| Current HP, resources, equipment, effects, spell preparation, durable actor state | Character sheet v2 | Granular character/party mutation tool | CampaignMemory or character notes |
| Published/generated module definitions | Immutable Module Pack revision | `module_draft(start/get/evidence/edit/finalize)` then `content_pack(activate, kind="module")` | Campaign fact before it occurs in play |
| Branch-local module progress and spatial review | Module progress ledger | `module_set_progress` | Edited module source |
| Restorable materialized campaign state | Snapshot | Snapshot member of `memory_change(action="commit")`; isolated `snapshot_create` for administration | Recap prose or workspace file |
| Player-facing summary of changes | Snapshot recap | Deterministic snapshot recap; optional reviewed presentation layer | Source of truth for facts or restore |

## Routing algorithm

1. Ask whether the statement is a user/table preference or campaign-scoped.
2. For campaign information, ask whether it is objective truth, one actor's
   knowledge, current mechanical state, immutable module definition, or progress.
3. Select exactly one authoritative owner. Cross-ledger references use ids and
   provenance; they do not copy the same mutable value into multiple stores.
4. Give objective facts deterministic keys such as
   `npc:harbin:relationship-to-party` or `location:cellar:door-state`.
5. On change, revise the existing key with its `expected_revision_id`. Supersede
   or retract obsolete assertions; do not append near-duplicates or erase history.
6. Cite source event ids and choose the narrowest disclosure scope. A fact's
   visibility never implies that any actor knows it.

## Scene boundary

After a resolved scene, submit one `memory_change(action="commit")` containing:

- one immutable event describing the observed outcome;
- accepted objective fact additions or revisions;
- separate knowledge additions/revisions for each actual knower;
- an optional full checkpoint and reviewed player-facing recap.

Use a fresh idempotency key. Existing fact and knowledge revisions require their
current revision ids. If validation fails, refresh the ledgers and rebuild the
whole unit; never finish only the remaining calls from a partially imagined save.

## Read boundary

- DM administration may query facts and inactive revision history directly.
- Player narration uses `continuity_context` with the exact actor and scene scope.
  For a long-running PC or NPC, use `purpose="actor_memory"` to obtain the
  branch-local identity, motivational, semantic, and episodic tracks under one
  deterministic character budget. When one candidate from every represented
  track fits, retrieval reserves that four-track floor before filling by global
  relevance. Player projections exclude DM-only actor-state and knowledge facts.
  Pass a bounded `event:<id>` current ref when an old, branch-visible event is
  already known to be relevant; exact actor/audience authorization is reapplied
  before it can outrank the recent window. This is retrieval only and never
  supplies an intent for the actor.
- DM narrative adjudication supplies the relevant actor/scene/location/quest/item
  `related_refs`; exact `module_evidence` selected by `context_anchor` facts is
  source context for Agent reasoning, not a trigger, world fact, or player belief.
- Never replace actor-scoped context with broad fact search.
- After branch checkout or snapshot restore, discard cached cards and context,
  then reread campaign, actors, module progress, events, and each actor's context.
- Use `memory_query(view="diagnostics")` for counts, orphan provenance, unsnapshotted events,
  recap evidence, checkpoint size, and Skill drift; it must not return narrative
  secrets or become a second write path.
