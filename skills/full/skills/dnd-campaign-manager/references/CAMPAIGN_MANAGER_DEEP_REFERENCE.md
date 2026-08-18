---
name: dnd-campaign-manager
description: "Create and maintain D&D campaigns through the SagaSmith D&D MCP server."
---

# D&D Campaign Manager Deep Reference

This manual was moved intact from the parent
`dnd-campaign-manager/SKILL.md`. Resolve every relative path written below
against `full/skills/dnd-campaign-manager/`, its original base; the concise
parent Skill and the MCP native tool list are the current entry points.

## Contents

- Start and modules
- Characters
- Saves, branches, and audit
- Continuity

`full/` is MCP-first. Use the raw MCP names below (a client may prefix them),
not shell `sagasmith-dnd` commands. Search or read only the task-relevant sections of
`../../references/mcp-contract.md` and
`../dnd-dm/references/DM_RULES.md`.

Open an MCP session exposure when resuming a campaign. Search for exact tool ids
and change the native list with `exposure(action="set")`; use Lobby tools for
setup/import/building and Play tools only for live play. One session/principal
has one active exposure, and reopening replaces its campaign binding. Refresh
the native schema after every `tools/list_changed` notification.

## Start and Modules

1. Call `campaign_create` with name, edition, locale, `advancement_mode`
   (`milestone` or `xp`), and optional description.
   Choose `2014` or `2024` from the adventure and table contract before importing
   modules or building actors; never accept either server default without checking
   it. For an existing campaign whose mode is absent or must change, switch to
   `lobby` and use `campaign_change(action="advancement_configure",
   payload={mode})`.
2. Persist the returned `campaign_id`.
3. Read `campaign_rules(action="get_profile")` and
   `campaign_rules(action="explain")`. Confirm the locked Core provider matches
   the selected edition. For an existing campaign, change the profile only in
   `lobby`, with the fresh campaign revision and explicit campaign-owner/DM
   approval, before any character option is applied. The Agent must not infer
   this approval from its adjudication role.
4. Resolve the caller's stable `principal_id`; use
   `access_grant(scope="campaign" | "actor")` for access instead of treating
   `player_name` as authorization.
5. Classify character support documents before module staging with
   `character_query(view="document", payload={campaign_id, source_path,
   expected_checksum?})`. Route character sheets, pregenerated-PC packets, and
   ability-score option files to character creation; they are not modules. Keep
   the returned `manual_input` option available to the player. If a directory
   contains one adventure plus appendices or map/resource PDFs, keep them in one
   campaign and import each physical module document as its own immutable revision.
6. Use `module_draft(start)`, repeat `evidence/edit`, then call `finalize`; activate
   the resulting Pack through `content_pack`. For generated content, start with
   `payload.name` and `payload.content`. For a user PDF/Markdown/text module, start with
   `payload.source_path`; it must be inside the server-configured module import
   roots. The server copies it into checksum-addressed MCP storage, and Core
   performs PDF-to-Markdown normalization. Never bypass staging with a direct path.
7. Review `preview.valid`, parser warnings, scene/spatial evidence, and the revision
   diff before ingesting. For a PDF, reject the preview if any scene lacks a valid
   `page_start`/`page_end` within the source page count. Treat document checksum,
   `parser_profile`, and `parser_version` together as the normalized module
   revision identity: a parser-version change requires a new inspect, validate,
   ingest, and activate cycle even when the source checksum and scene diff are
   unchanged. Activation additionally requires the fresh campaign
   `expected_revision`; keep a stable idempotency key per stage.
8. After ingest and again after activation, use `module_query(view="index")` to
   confirm chapter/scene counts, stable keys, page ranges, and spatial evidence.
   Room-heading order is not a map. A `spatial.connections` edge is usable only
   when it carries explicit source evidence (for example, prose stating that a
   stair leads from D4 to D5); an empty connection list means topology is still
   unknown, not that the listed rooms are isolated or sequentially adjacent.
   When a PDF map carries the missing topology, follow
   `../../references/module-visual-atlas.md`: list managed assets, render and
   inspect the exact page, then submit only visually observed edges through
   `module_set_progress(spatial_review=...)`. The returned review is scoped,
   branch-aware, and snapshot-restorable; do not edit immutable import metadata.
   If a required 2014 creature card exists only as a PDF image, follow
   `../../references/module-image-content-review.md`: first call the
   service-owned `module_draft(action="edit", operation="statblock")`, re-read
   `module_query(view="content")`, and create the actor with
   `mode="module_statblock"` before play or combat. Only if recovery remains
   ambiguous may an image-capable reviewer render, inspect, and submit the page.
   When the host Agent has no image capability, use this deterministic 2014
   text-layout recovery for module cards and the equivalent bounded 2014 OCR
   path for rule statblocks. The
   service renders and recognizes the managed page, checks critical facts against
   indexed text or a second OCR scale, and returns a checksum-bound review; the
   Agent reasons only over that exact returned text. A 2024 card bypasses the
   2014 OCR facade: use complete edition-matching indexed text with
   `dnd5e_2024_statblock`/`review_mode="agent_text"`, or capable visual review.
   If neither path yields
   reliable evidence, keep the gate in explicit source review rather than asking
   the text-only model to infer pixels.
   Inspect `module_query(view="candidates")` as a separate evidence gate.
   `review_ready` text candidates must retain their exact `source_chunk_ids` in
   `module_draft(action="edit", operation="content")`. A blocked 2014 candidate requires
   `module_draft(action="edit", operation="statblock")` first; a blocked 2024 candidate
   requires complete edition-matching indexed text or capable visual review. A rendered managed page
   and literal visual transcription is only the image-capable fallback. Never
   fill OCR gaps from memory. For a
   module-authored or homebrew Multiattack, inspect
   `agent_fill_requirements`. 2014 OCR recovery may first return
   `requires_agent_fill=true` with `review=null`; this is a corroborated text
   draft, so have the Agent read that returned text and retry the action with a
   fresh idempotency key plus a source-bound
   `payload.agent_fill.multiattack_options` to the immutable module review,
   using only parsed weapon ids, modes, and explicit counts. This is required
   even when the parser proposed a composition. For standard rulebooks, the
   parser and engine are authoritative, Agent fills are rejected, and any
   unparsed standard mechanic must be implemented and tested before play. When a custom
   procedure cannot be represented by
   weapon/count options, submit `resolution="agent_ruling"` with no options so
   the parser proposal is removed and the action remains an Agent DM boundary.
   If the same managed source gives the creature a complete numeric weapon
   action outside the selected base card (for example, an adjacent printed
   variant), attach it through `payload.agent_fill.additional_actions` with its
   name, exact managed `source_ref`, exact action excerpt, and Agent reason.
   The canonical parser derives the weapon id and mechanics; the Agent cannot
   supply a sheet patch, attack bonus, damage, or condition fields separately.
   A Multiattack fill may then cite that parser-derived weapon id. Preserve any
   returned source evidence. If the custom rider has no plan, compile one
   source-bound `content_solution` on first live use and reuse it thereafter.
   Do not solve individual monster prose by adding phrase-specific parser cases
   or by patching the resulting actor sheet.
   Then choose a scene and use `module_set_progress` with an explicit
   `scope_id` to enter it. Do not narrate from a `module_search` snippet until
   `module_expand` or `module_query(view="scene")` has been called. If an
   encounter scene occurs in a room indexed under a different scene, set its
   same-module, unique spatial key as `current_location_key`; never copy the
   room geometry into the encounter or guess an ambiguous key. Also persist the
   exact spatial scene id as `state.location_scene_id`. At combat start, keep
   the current progress scene, that spatial evidence scene, and any explicit
   encounter `scene_id` distinct; do not recover a duplicate key by scanning a
   different scene when `location_scene_id` was recorded.
9. After every resource document has activated, verify the combined module list,
   scene counts, page ranges, atlas location counts, and snapshot the campaign
   baseline once. Do not checkpoint a partial folder import as the playable
   baseline.

Before combat, create a participant manifest from the expanded source scene and
call `module_query(view="preflight")`. Every group records its role, required
count, canonical actor ids, same-module `source_scene_id`, and exact normalized
`source_excerpt`. Initial participants must satisfy every required `combatant`
group. Actors in a `reinforcement` group stay out of the initial list and may enter
only through `combat_join` after the source condition succeeds. Missing required
actors or a whole-card `card_valid=false` result blocks combat start. Dead/0 HP
actors remain valid participants with `can_take_turn=false`, while mechanically
unresolved entries disable only the affected capabilities. Surfaced manual
rulings require review but must not be erased from the preflight report. By
default the SagaSmith Agent performs that DM review from exact source and current
state; player-owned choices, missing evidence, and owner approvals remain their
respective boundaries. A
`ready: true` manifest can still have `settlement: mixed`: inspect per-card
`state_flags`, `disabled_capabilities`, `available_capabilities`, `manual_rulings`,
structured `ruling_requirements`, and `ruling_spell_ids` before switching to combat. Resolve entries marked
`default_resolver="agent"` through Agent reasoning and public tools. Missing
ranged or thrown ranges disable those attacks and are not discretionary DM ranges;
when the exact statblock instead supplies a complete positional restriction such
as "one target directly below", treat it as Agent-owned targeting, require the
current map positions to satisfy it, and never invent a numeric range;
the universal `unarmed-strike` fallback does not make an incomplete imported
weapon or spell automatically settled.

Keep each required count independent of the selected actor list. Derive it from
the expanded source group or from a recorded, server-rolled encounter-table result,
and pass both that count and its source basis to the regression driver. Never use
the number of actors already selected by a filter as the expected count: that
would allow an omitted combatant to validate the same incomplete manifest.

When one indexed scene contains many numbered rooms, advance by the imported room
chunk rather than assuming every room is a top-level scene. Search the exact room
identifier with `module_search` (for example `D13`), require the first result's
final `heading_path` element to name that room, and retain its chunk id and page
range as the source for events, statblocks, and spatial review. A same-looking
identifier inside ordinary prose or a different scene is not the room source.

## Characters

| Need | MCP tool |
|---|---|
| Create a direct actor | `character_create_from(mode="direct")` |
| List templates | `character_query(view="library")` |
| Instantiate a template | `character_create_from(mode="template")` |
| Atomically create PC template + instance | `character_create_from(mode="build")` |
| Create from an imported exact statblock | `character_create_from(mode="statblock")` |
| Create from a reviewed image-only module statblock | `character_create_from(mode="module_statblock")` |
| Create an exact named noncombat identity with no statblock | `character_create_from(mode="narrative_npc")` |
| List or read live actors | `character_query(view="list" | "get")` |
| Inspect a character/ability document | `character_query(view="document")` |
| Full reviewed replacement | `character_sheet_replace` |
| Ability generation | `dnd_ability_roll`, `character_ability_apply` |

All live actors use `sheet v2` and `notes v2`. Read
`../../references/character-schema-v2.md` before creation or mutation. Do not
persist an unconfirmed draft. Build mode requires one stable
`idempotency_key`; an exact retry must return the original template and campaign
instance rather than creating another pair.

Ability scores always pass through `character_ability_apply`. `manual` with all
six assignments is a first-class path for physical rolls and user-entered values;
do not remove it because standard array or point buy is available.

For imported modules, distinguish narrative and mechanical provenance. The module
may name an NPC, describe its intent, and assign fixed possessions while an
inspected rule source provides its statblock. If the supplied module has no
pregenerated PCs, label separately built PCs as player or regression fixtures
instead of claiming module provenance. Use statblock mode only with an exact
imported source and retain its source/chunk provenance. If the exact creature is
present only as a PDF image, use the reviewed module-content workflow and retain
its asset/page/review provenance. Exact 2024 SRD 5.2.1-style cards are supported
through the edition-matching parser. If the card is absent, ambiguous,
edition-mismatched, spell-only without numeric attack facts, or otherwise
unsupported, keep it unresolved; never substitute a similar SRD creature.
Do not pre-resolve random treasure.

If an important named NPC has an authored identity but no combat statblock, bind
its exact active module/scene/chunk/page/hash and name-bearing excerpt through
`mode="narrative_npc"`. The resulting `narrative_only` card may participate in
relationships, notes, goals, and ActorKnowledge, but its default mechanics are
sentinels and it cannot make a check or enter combat until an exact statblock is
later imported.

When an imported adventure introduces a combat-capable actor already at 0 HP,
Unconscious, and Stable, first create the source-bound actor with
`current_hit_points: 0`, then call
`character_state_change(action="source_state",
payload={state: "stable_unconscious", source_ref: "module-chunk:<id>", reason:
"<source-grounded reason>"})`. This is a narrow DM initialization path: the actor
must already be at 0 HP, the citation must resolve to managed campaign content,
and the state cannot be used for arbitrary conditions or a Dead actor. Record the
source event as DM-only unless a PC has actually witnessed or learned it. Never
fabricate a heal followed by nonlethal damage merely to obtain the same sheet
conditions.

During ordinary combat, a capture uses the Core knockout rule at the instant a
melee attack would reduce the target to 0 HP. Preflight and resolve that exact
melee attack with `action.knock_out=true`; never record a narrative capture after
a ranged attack, lethal spell, or already-completed kill. In 2014 the resulting
creature must be at 0 HP, Unconscious, Stable, and not Dead. Keep the captured
actor and its ActorKnowledge ledger intact, and use the combat result as the
source for any later interrogation or prisoner quest state.

When the fiction needs a number of prisoners but does not identify which
combatants survive, express an Agent-selected minimum capture objective instead
of preselecting exact victims. Every eligible target must still be resolved by a
real melee knockout through the public combat tools. After combat, count only
the canonical actor cards that are actually at 0 HP, Unconscious, Stable, and
not Dead; lethal casualties do not invalidate the scene once the minimum is
met. Use exact target ids only when the source itself identifies the required
survivors or every selected target must be captured.

Do not interrogate that creature while it remains Unconscious. Under the 2014
condition it is incapacitated and unaware of its surroundings, so a social check
cannot make it answer. Restore consciousness through an actual legal effect such
as healing, or use the server random stream for the stable creature's printed
1d4-hour recovery to 1 HP. Persist the spent spell slot, time, HP, and remaining
conditions before the check. A failed interrogation is final for that attempt:
record only the refusal and do not reroll until the fiction supplies a genuinely
new attempt. Do not leak the hidden answer into any actor's knowledge ledger.

For normal play, mutate only the affected structure:

```text
inventory_change | inventory_transfer | wallet_change
character_state_change | character_action | character_metadata_update
campaign_query(view="party")
```

Use `character_spell_prepare(mode="replace_all", event="setup")` only during
initial lobby setup. Returning to lobby after live play starts does not reopen
setup. During live play, pass the complete new list as `prepared_spell_ids` on
that member of the atomic `campaign_change(action="party_rest")` transaction;
do not toggle preparations one by one.

For milestone advancement, record and settle the source-bound award immediately
when its trigger occurs, before entering a later sourced scene. For XP advancement,
use one atomic `campaign_change(action="experience_award")` with the exact
source/reason and each PC's amount and current revision. Never synthesize encounter
XP for a milestone module, award XP to NPCs, or change `progression.xp` by sheet
replacement. Re-read the returned eligibility; XP award never auto-levels.

Once a milestone is earned or XP reports `eligible=true`, end combat, switch to
`lobby`, and use `character_state_change(action="level_advance")`. XP mode rejects
the call below the next cumulative threshold. Never patch the actor sheet.
Treat the returned `advancement.follow_up` as a blocking checklist: all eligible
features, subclass/player choices, and spell gains must be reconciled from the
active catalog before returning to `play`. A prepared-class spell in either edition
application hydrates a legal unprepared card; the prepared list itself changes
only through a later completed Long Rest. Re-read the actor and create a
post-advancement snapshot. Current 2014 and 2024 single-class support is
explicit; unsupported multiclass advancement stops for review.
Any user-imported `selection_ready` spell must pass the same canonical spell
definition schema during pack compilation that actor hydration later uses.
Reject the pack before activation when OCR leaves an invalid range, duration,
component, class-list, level, unknown field, or structured resolution; never
wait for party preparation or statblock creation to discover the mismatch.

After each actor or party mutation call `character_query(view="get")` or
`campaign_query(view="party")`. Use their
derived values rather than recalculating weapon attacks, AC, or encumbrance in
prose.

Before every write, read the matching optimistic token and send a fresh
`idempotency_key`. Character tools use the actor revision; scene progress uses
`state_version`; knowledge revisions use `revision_id`; branch/snapshot tools use
the campaign revision plus the current branch or snapshot-head ID. The exact
fields are listed in `../../references/mcp-contract.md`.

## Saves, Branches, and Audit

| Need | MCP tool |
|---|---|
| Commit a scene and optional save | `memory_change(action="commit")` |
| Create/list an administrative save | `snapshot_create`, `snapshot_query(view="list")` |
| Validate / inspect lineage | `snapshot_query(view="verify" | "lineage")` |
| Restore without deleting future history | `snapshot_restore` |
| Regenerate recap | `snapshot_query(view="recap")` |
| List/create/switch timeline | `branch_query`, `branch_change` |
| Audit / undo / redo | `state_revision(history/undo/redo)` |
| Inspect continuity health | `memory_query(view="diagnostics")` |

Restore is a branch fork, never destructive overwrite. Verify the target first,
then refresh campaign actors, party state, scene progress, events, and continuity
context after restoring.

## Continuity

Use `memory_change/query` for branch-scoped durable world facts and
`campaign_event(add/list)` for immutable chronology. Prefer stable fact keys and
revision-safe upserts; supersede obsolete facts instead of deleting them. Use
`actor_knowledge_change/query` only for one PC/NPC/monster's subjective knowledge.

After a resolved scene, use one `memory_change(action="commit")` for the event, objective fact
revisions, actor-specific knowledge, and optional snapshot. Require a fresh
idempotency key, plus current fact/knowledge revision ids for updates. A failed
member rolls back the whole continuity unit. The individual ledgers remain valid
for isolated administration, but never emulate an atomic scene save with a
partially completed sequence.

For player-safe retrieval, call `continuity_context` with `actor_id`, `scope_id`,
audience, and optionally `branch_id`. Do not substitute broad
`memory_query(view="search")` for
that context; it can expose DM facts or sibling-branch history.

For DM adjudication of module-specific narrative behavior, also pass
`related_refs` for the current actors, scene/location, active quests, and key
items. `module_evidence` contains exact, pinned, branch-scoped source selected by
DM-only `context_anchor` facts. It is evidence for Agent reasoning, never an
executable trigger or a player-visible fact. A restore or branch checkout
invalidates cached context; reread it before continuing.
