# Full Runtime MCP Contract

`full/` is MCP-first. Connect the `sagasmith_dnd` server and call the raw tool
names below; a client may expose them with a prefix such as
`mcp_sagasmith_dnd_`. Do not require a local `sagasmith-dnd` executable.
`server_capabilities.rulebook_import` is the machine-readable contract for the
ordered import stages, canonical citation fields, and play/combat settlement tools.

## Core turn loop

| Intent | MCP tool |
|---|---|
| Health and owned storage | `storage_status`, `storage_migrate`, `server_capabilities` |
| Campaign | `campaign_create`, `campaign_query(list/get/party/resume)`, `campaign_change`, `access_grant(campaign/actor)` |
| Rules | `rulebook_draft(start/get/evidence/edit/finalize)`, `rule_search`, `rule_expand`, `rule_seed_status`, `rule_seed_bundled`, `content_pack(list/get/import/export/activate/deactivate/remove)`, `campaign_rules(get_profile/set_profile/core_relock/explain/receipts)`, `character_content_apply`, `content_solution(query/compile)` |
| Roll | `dnd_dice_roll`, `dnd_check`, `dnd_ability_roll`, `character_check(action="check" \| "group" \| "contest" \| "reroll")` |
| Module artifact | `module_draft(start/get/evidence/edit/finalize)`, `content_pack(import/export/activate)` |
| Scene play | `module_query(list/index/scene/current/progress/assets/content/candidates/preflight/actors)`, `module_search`, `module_expand`, `module_set_progress` |
| NPC conversation | `npc_conversation(open/get/ingest/publish/close/abort)`; the Host-only authenticated transport is intentionally absent from the model surface; requirements are advertised by `server_capabilities.npc_conversations` |
| Chronology | `memory_change(add/upsert/revise/supersede/commit)`, `campaign_event(add/list)`, `memory_query(list/search/diagnostics)`, `actor_knowledge_change(add/revise)`, `actor_knowledge_query(list/search)`, `continuity_context`, `bounded_evaluation(validate)` |
| Snapshot | `snapshot_create`, `snapshot_query(list/verify/lineage/recap/core)`, `snapshot_restore`, `branch_query(list/compare)`, `branch_change(create/checkout/create_core_upgrade)` |
| Audit | `state_revision(history/receipt/undo/redo)` |

Do not call retired names or emulate aliases client-side. Pack authoring and
inspection are exposed only in Lobby through `rulebook_draft`, `module_draft`,
and `content_pack`; finalized Pack reads do not bypass that phase boundary. The
consolidated calls include:

- `chase(action="start" | "query" | "take_turn" | "end")`;
- `character_check(action="check" | "group" | "contest" | "reroll")`;
- `campaign_rules(action="core_relock")`;
- `rulebook_draft(action="evidence" | "edit")`;
- `module_draft(action="evidence" | "edit")`;
- `memory_change(action="commit")` and `memory_query(view="diagnostics")`;
- `combat_choice(action="on_hit_ruling" | "execute_plan")`; the former only
  dismisses an exact-source, Agent-reviewed no-op, while executable custom
  effects use the latter;
- `content_solution(action="query" | "compile")`; DM-only compilation is
  available in Lobby, Play, and Combat so a previously unseen custom card can
  be compiled on first live use and then reused from its persisted card. It is
  not an authoring path for locked standard content.

For `chase(action="start")`, a module-authored contextual speed change belongs
in that participant's `participant_config` as `speed_adjustment_ft` plus an
exact `source_excerpt` contained in the reviewed chase excerpt. The server
applies it only to the chase snapshot. For every
`chase(action="take_turn")`, callers must explicitly send `turn_action`,
`complication_choice` (the empty string is meaningful when no choice applies),
`stand_from_prone`, and an exact boolean `quarry_visibility` map covering every
quarry. Initiative, movement, Dash limits, checks, damage, time, and random
receipts remain engine settlements; choosing actions, legal alternatives, and
current theater-of-the-mind visibility remains an Agent/DM decision.

Every action uses only its documented payload fields. Unknown fields are an
error, and a facade action retains the original role, phase, revision,
idempotency, source-evidence, and random-stream boundary. In particular,
`content_pack` always requires one explicit route `kind` from
`core_rules|addon|module|preset`; it never guesses from `pack_id`, source id,
or archive contents. Its complete action set is
`list|get|import|export|activate|deactivate|remove`. The
published action-payload contract is the single exact-field whitelist; handlers
add domain checks but do not maintain a second allowed/required field table.
all `rulebook_draft`, `module_draft`, and `content_pack` actions are Lobby-only
and DM-only. Chase, contests, and Heroic
Inspiration rerolls are Play-only. On-hit rulings are
Combat-only. Loading a facade through a lower-risk group does not authorize its
other actions outside those action-level boundaries. `playthrough_manifest` and
`combat_join` remain separate tools because their save/audit and turn-boundary
transactions are independent.

`module_search` only selects candidates. Call `module_expand` or
`module_query(view="scene")` before narrating a module fact. Always provide the active
`scope_id` to `module_query(view="current")` and `module_set_progress`.

### Agent-owned DM ruling boundary

The word "DM" names an adjudication role, not a requirement for a separate
human. Unless a workflow explicitly requires player intent, owner approval,
missing-source review, or permission escalation, the SagaSmith Agent performs
the DM ruling itself from the locked rules, exact expanded source, current
scene, canonical actor cards, and branch state. This is the default for
`status="pending_ruling"`, preflight `manual_rulings`, descriptive source
activities, generic spell effects, Ready releases, environmental facts, and
module-specific procedures.
For combat tactics backed only by a reviewed descriptive feature/activity or
an unstructured hydrated innate spell, the full-playthrough encounter driver
accepts one source-bound Agent turn ruling. It binds the reviewed card and
current-scene excerpt, pays the real activity, generic `improvise` action, or
innate spell cast, sends any printed save through `combat_check`, and persists
the decision and outcome as a temporary-map world patch. Hydrated innate
spells must use `spell_id`: the runtime pays `N/day` resources, honors at-will
access, and starts or replaces concentration from the spell card before the
Agent settles its prose effect. A failed-save target directive is recovered
from that patch after a process restart and is closed only by the actual public
attack or a declared source-authored termination boundary. This is
orchestration of existing public tools, not a new creature-specific mechanic
or an excuse to bypass action economy.
When the ruling performs a module encounter procedure, the request, paid action
result, returned ruling receipt, and temporary-map world patch all retain the
same nonempty `procedure_id`. Procedure progress and ending checks must consume
those receipts; prose summaries are not evidence that a ritual, countdown, or
other repeated procedure occurred.
`server_capabilities.ruling_policy` publishes this split for cold-start Agents;
use it instead of treating every `pending_ruling` as the same kind of missing
input. Native domain results carry the ruling resolution and policy reference.
The default resolver is `agent`; a result explicitly classified as one of the
external-input exceptions instead names `external_input`. This
annotation assigns the next work; it does not claim that the effect has already
settled.

The domain result itself also carries `default_resolver`, `ruling_kind`, and
`policy_ref`, including native dynamically exposed calls. Compact facades must
copy the nested ruling classification to their own top level; never discard an
external-source exception and then fall back to generic Agent adjudication.
Classify the complete nested `pending`, `ruling_requirement`, and
`ruling_requirements` set, not only the outer envelope. Any genuine external
exception in that set takes precedence over Agent-owned work; when no such
exception exists, an unclassified DM ruling defaults to Agent reasoning.
Reject or normalize a contradictory `default_resolver`/`ruling_kind` pair
instead of trusting whichever field happens to be outermost.
The D&D rule engine owns the ruling-kind vocabulary and the precedence used
when one result contains multiple requirements. Agents, facade clients, and
regression drivers must consume the server-returned top-level classification;
they must not copy, alphabetize, or independently prioritize a local list of
ruling kinds. This preserves player intent and approval boundaries when a
single result also contains Agent-owned or source-review work.
The ownership contract also covers lobby review states that are not yet live
actions. `rulebook_draft(action="get")` returns
`job.review_resolution` plus `job.review_requirements`, and each pending or
needs-revision candidate carries a `ruling_requirement`. A
`module_query(view="candidates")` entry with `execution_state="review_ready"`
likewise names the Agent as the source-or-scene resolver. These are ordinary
Agent reviews when exact chunks are present. A blocked/manual-review candidate
with `ruling_kind="missing_or_conflicting_source_review"` retains external
ownership, and that nested external requirement takes precedence in the job
aggregate.
An engine `NeedsRuling` boundary that is reached before commit returns the same
structured `pending_ruling` contract with `committed=false`, its missing facts,
and a retry contract for the classified resolver. Treat an Agent-owned result as
control returning to the Agent, not as a failed campaign action or a request to
ask a separate human. A missing range, unresolved hydration, or unsupported
source contract remains `missing_or_conflicting_source_review`; the generic
catch boundary must not relabel that source defect as Agent permission to invent
a mechanic.

The Agent must preserve the transaction boundary. It reads whether the first
call already paid an action, Reaction, use, slot, or other resource; adjudicates
without paying it twice; uses only public domain tools for dice and state; and
records the exact source reference/excerpt, facts, decision, rolls, revision,
and resulting continuity/manifest changes. `combat_choice` is required only
when an owned pending choice window exists. Agent reasoning is not permission
to fabricate a window, edit a raw sheet, write the database, override a player
choice, infer missing source text, or approve an owner-only rule-pack change.

Regression drivers are consumers of this contract and must return the same
ownership metadata to the acting Agent. In particular, an attack
`pending_ruling` with `missing=["direct_sunlight"]` is a pre-commit
environmental adjudication, not an on-hit choice. The Agent derives the fact
from current scene and time evidence, records its reasoning, and retries the
same public attack at the current revision. Only a response with an actual
pending on-hit `choice_id` may be settled. Query or compile the exact card with
`content_solution`, then send its fingerprinted runtime commitment to
`combat_choice(action="execute_plan")`. Use `on_hit_ruling` only to dismiss the
exact quoted effect after Agent review finds that no structured mutation applies.
This applies to party construction, source checks and contests, level
advancement, and catalog application as well as encounters. A regression
driver must write a machine-readable stopped report with top-level
`status="pending_ruling"`, `default_resolver`, and the complete
`ruling_requirements`; it must not flatten the domain result into a generic
exception string. An unclassified DM ruling defaults to Agent adjudication,
while an explicitly external player choice or missing/conflicting-source review
retains that owner. Declarative rule-pack `ruling.require` operations follow the
same Agent default; `choice.require` remains a player-owned external input.
This includes `character.validate` and `character.derive`: validation pauses
return a typed, pre-commit ruling instead of a generic schema error, while
derived cards expose structured `ruling_requirements` alongside
`unresolved_rules`. Consumers must follow the declared resolver rather than
inferring ownership from the word "review."

## Structured content catalog

For a campaign locked to 2014, the installed `dnd5e.content.srd2014` catalog
provides source-linked class, subclass, species, background, feat, spell, and
item records. Discover selection artifacts through rule/content retrieval for
the campaign's effective Core and enabled Addon Packs. Do not inspect an
inactive Pack and then apply its option by id. Keep the returned Pack identity,
selection requirements, and source citation with the chosen artifact.

`character_content_apply` safely records catalog spells, feats, backgrounds,
and a selected subclass on a character card, preserving its pack version and
source references. Catalog presence is not a claim that every narrative effect
is executable: an item, species, class, or an option with unresolved choices
returns `pending_ruling` rather than inventing a settlement. If the response
identifies a missing character-build or player choice, obtain that choice; if
it identifies an actual DM adjudication, the Agent resolves it under the
Agent-owned ruling boundary above. Use a source-bound rule pack mechanic only
when the rule has been reviewed and validated.

During Play, an Owner/DM may also apply a source-awarded `activity`, `feat`,
`feature`, `item`, or `spell`. The write must include exact
`grant={kind, reason, source_ref}` where `kind` is `story_reward`, `training`, or
`module_reward`, and `source_ref` resolves to the artifact's rule evidence or
current active module evidence. Other character-building grants remain Lobby-
only; a narrative promise without exact evidence does not authorize the write.
Non-numeric feat prerequisites and source-bound statblock spell/component
boundaries retain a structured `ruling_requirement`/`ruling_requirements`
record on the card. The record names the Agent or the true external-input
boundary, so callers must not infer ownership from prose such as "DM review."
A reviewed rule-statblock response applies the same contract to every parser
warning through `validation.ruling_requirements` and
`validation.default_dm_resolver`; do not reduce those warnings to an unowned
`warnings` list.

For a 2014 custom background, use one enabled base background artifact and pass
`custom_name`, exactly two `skills`, the base artifact's required
language/tool choices, and the retained package's `equipment_item_ids`.
`background_grants.choices` records the base background, customization flag,
and selected skills; the equipment ids must already exist on the validated
inventory. This is the Core customization rule, not permission to name or apply
an inactive extension background. Character setup must also retain every class
equipment choice and pack, the complete background package and wallet grant,
and the background characteristics in `notes.profile`.

Supply `selection.source_class` and an appropriate spell grant `method` when a
spell has class eligibility, `selection.target_class_name` for a multiclass
subclass choice, and all required background choices. The runtime rejects a
spell outside the selected class list or class-level limit and never silently
assigns a subclass to the first class on a multiclass card.

For a 2024 background, "all required choices" includes a legal [2,1] or
[1,1,1] `ability_score_increases` distribution among the three listed
abilities, its tool choice, and `equipment_package="A"|"B"`. Package A
materializes the exact source-listed stacks plus its remaining GP; package B
materializes 50 GP. Do not pre-create those items or substitute caller-supplied
inventory ids. A Magic Initiate background additionally sends
`origin_feat_selection={spellcasting_ability, cantrip_artifact_ids,
level_1_spell_artifact_id}`; the background's fixed Cleric/Druid/Wizard list is
catalog evidence and cannot be replaced by the caller. The write is atomic
across abilities, Constitution-derived HP, proficiencies, equipment and wallet,
the feat card, spell cards, and its once-per-Long-Rest free-cast resource.

Repeated 2024 class choices must include `grant_level`. ASI/Epic Boon grants
send `feat_choice={artifact_id, selection}`. Eldritch Invocations send the
exact number of entries for that level; a repeatable blast entry carries its
known cantrip `target_artifact_id`, and Lessons of the First Ones carries an
Origin feat target plus nested `option_selection`. The service validates level,
required invocations, non-repeatability, distinct repeat targets, feat
prerequisites, and exact option source cards before committing any part.

A discovered physical spellbook is a party or character inventory item, not a
free content grant. Store its resolved `spell_ids` and preserve catalog misses in
`unresolved_spell_names`; unresolved names remain non-executable. During `play`,
use `character_content_apply` with `method="spellbook_copy"`, exact
`source_owner`/`source_item_id`, and explicit exact coin payment. The transaction
includes the 2014 deciphering process, eligibility, payment, elapsed time,
matching actor/world duration expiry, and applicable rule modifiers such as
Evocation Savant. Ordinary `method="spellbook"` grants belong to lobby setup or
level advancement. A failure commits none of those state changes.

Campaign travel, waiting, and other material elapsed intervals go through the
public playthrough driver's `advance-time` path. Exact source-defined durations
retain their scene excerpt and `source_ref`. If narrative source timing needs an
exact conversion, retain that evidence and add a settled Agent-as-DM ruling. If
the module leaves duration to the DM, use that ruling without attaching
unrelated prose as false evidence. A time ruling is a strict
`default_resolver="agent"`, `ruling_kind="agent_dm_adjudication"` object with a
concrete decision and reason; its `period` and `count` must exactly equal the
clock mutation. The committed continuity event stores both evidence channels,
and the authoritative clock, timed effects, actual-witness ActorKnowledge,
checkpoint policy, and manifest sync must agree.

## Modules, space evidence, and temporary combat maps

Module re-imports are revisions: earlier sources are retained for snapshots and
scoped scene progress, while normal `module_query(view="index")` results show only the newest
active revision. Re-import orchestration must version parser behavior and restore
the entry tool phase if any staged refresh step fails; a rejected import must not
strand a live campaign in `lobby`. A D&D scene can contain conservative `spatial.locations`
evidence recovered from room headings and stated dimensions. Its optional
`spatial.connections` contains only edges supported by explicit route prose or
reviewed structured authoring, with confidence and source evidence; neither room
number order nor generic room references establish adjacency. Set
`current_location_key` with `module_set_progress` only when it names a location
in the current scene or exactly one spatial location elsewhere in the same
module. This lets an encounter scene reference a separately indexed room scene
without merging their narrative content; ambiguous or cross-module keys fail.
When the location lives in another scene, also persist its exact scene id as
`state.location_scene_id`. A later combat start resolves that recorded scene
first and fails if it is cross-module or does not contain the recorded key; it
must not silently select a different duplicate key elsewhere in the module.

For visual evidence, an owner/DM calls `module_query(view="assets")`, then
`module_draft(action="evidence")` for one page of the imported PDF. The tool returns an MCP
image and registers a content-addressed derived asset. After inspecting that
image, submit only observed edges through `module_set_progress.spatial_review`.
Core validates same-module unique endpoints, the PDF/rendered asset and page,
connection kind, reviewer, and active branch. Returned edges carry
`confidence="reviewed_image"` plus the asset checksum, page, reviewer, and branch
id. The review lives in scoped scene progress, so snapshots and branch checkout
restore it without mutating immutable imported metadata. A review-only write may
omit `status` and `progress`; existing values are preserved. See
`module-visual-atlas.md` for the full sequence and schema.

Staged rulebooks and modules also expose a pre-ingest transcription-review path.
`rulebook_draft(action="evidence")` returns `transcription.normalized`,
`transcription.native_text`, and two local `ocr.variants` beside the image;
`module_draft(action="evidence")` returns the equivalent bundle for a
module import job. A text-only Agent may consume these textual fields without
claiming visual inspection. After the job is `inspected`, submit a batch of
unique exact replacements for that physical page with
`rulebook_draft(action="edit", operation="source_text")` or
`module_draft(action="edit", operation="source_text")`, the normalized page SHA-256, current
import revision, rationale, fresh idempotency key, and one evidence basis:

- `cross_text`: the proposed text occurs in at least two returned text sources;
- `agent_context`: only a bounded spelling, case, spacing, or Markdown heading
  repair; the before/after digit sequences must be identical;
- `rendered_page`: the reviewer really inspected the image and supplies its exact
  checksum.

The server constructs the evidence record, applies revisions only to the
normalized in-memory view, and reruns inspection. The managed source and every
OCR cache entry stay immutable. Include all known repairs in each call and copy
heading depth from adjacent entries of the same kind. If reparsing exposes an
error, an unpublished inspected job may add another version bound to the new
page hash, up to eight versions per page; refresh the job revision each time.
An ingested source is immutable and requires a new import job for correction.
These actions repair transcription/structure, not missing mechanics; unresolved
or conflicting source facts remain an external review boundary.

For a 2014 creature card that exists only in the PDF image layer, first call
`module_draft(action="edit", operation="statblock")` with the exact managed PDF page. The
server performs checksum-bound layout OCR and requires the embedded text or a
second OCR scale to corroborate the complete critical fingerprint. This requires
no model vision. A custom Multiattack first returns
`requires_agent_fill=true`, `review=null`, the recovered normalized text, and
exact `agent_fill_requirements`; the Agent reasons over that returned text and
retries the same action with a fresh idempotency key plus
`payload.agent_fill`. Only the filled retry creates the immutable review. If OCR
remains ambiguous, an image-capable DM may render and
inspect its managed page, then call `module_draft(action="edit", operation="content")`.
The MCP validates the normalized 2014 statblock before Core stores immutable
module/scene/page/asset evidence. Never route a 2024 campaign through this 2014
OCR grammar. For a 2024 card, submit complete indexed text with
`content_kind="dnd5e_2024_statblock"`, or have an image-capable reviewer
transcribe the managed page through `submit_content`; both paths are parsed by
the edition-matching 2024 statblock parser. If neither exact text nor capable
visual review is available, leave the card unresolved.
Re-read it with `module_query(view="content")` and create campaign actors with
`character_create_from(mode="module_statblock")`. See
`module-image-content-review.md`; missing text extraction is not evidence that a
printed card is absent, and visual transcription is not permission to fill gaps.

In `lobby`, run `module_draft(start)` -> repeated `evidence/edit` -> `finalize`,
then activate the immutable Pack through `content_pack`. `start` accepts either generated
`payload.name` + `payload.content`, or a user document in `payload.source_path`.
The latter is limited to `SAGASMITH_DND_MCP_MODULE_IMPORT_ROOTS`; the server
copies PDF/Markdown/text into checksum-addressed MCP storage before Core reads it.
Inspect exposes the Core PDF-to-Markdown page/bookmark statistics and complete
scene/spatial preview. Ingest remains inactive, and only activation changes the
campaign's active revision under optimistic concurrency.

Module finalization requires the Agent's exact
`confirmation={confirmed: true, note: ...}` after it reviews the current draft,
issues, evidence, and saved package decisions. Callers do not submit publication
dimensions. The server validates the descriptor and source references, records
the reviewer and note in `metadata.agent_finalization`, and emits a Pack with no
publication-matrix field.

For PDFs, `preview.valid` also requires every scene to have an in-bounds
`page_start`/`page_end`; the preview exposes those fields together with source
line ranges. The normalized revision identity is the source checksum plus
`parser_profile` plus `parser_version`. Start a new draft after a parser upgrade
even when the document checksum and semantic scene diff are unchanged, repeat
the Agent evidence/edit loop, finalize it, then activate its immutable Pack and
verify the inactive and active indexes before starting play.

`combat_start` fixes one `positioning_mode` for the encounter. In `grid` mode,
the server requires a position for every participant and exactly one map
authority: `battle_map_template_id` from a finalized active Module Pack or an
explicit `battle_map`, never both. A template is copied into a fresh
encounter-local map and each participant is explicitly assigned to any declared
deployment zone; actor ids never enter the portable Pack. Explicit mechanical
overrides produce a DM authority receipt. Runtime map patches do not mutate the
Pack. Movement, range, cover, visibility, areas, obstruction, and opportunity
geometry are engine-owned. A missing map or coordinate is invalid input, never
an Agent fallback. `combat_map_patch` records reviewed world changes in the
encounter audit and scene runtime state. In `agent` mode, the request must
contain no battle map or coordinates. The Agent decides theater-of-the-mind
reachability, range, cover, visibility, obstruction, friendly fire, and
threatened movement, then supplies the exact structured `spatial_facts`
required by the action. D&D still owns dice, action economy, damage, resources,
effects, and state commits. The mode never changes before `combat_end`.

`combat_query(view="render")` is a read-only native MCP media response over an
already audience-filtered combat snapshot. `payload.audience_projection="caller"`
preserves the caller's authorized view; `"party_public"` starts from that view and
can only remove information, so an Owner/DM request cannot elevate the shared
image. The response contains structured metadata, accessible `alt_text`, and one
PNG `ImageContent`. It never advances a revision or becomes combat authority.
Grid renders show only projected map/token data. Agent-mode renders state that no
mechanical geometry exists and never invent a grid. Source-backed portraits are
loaded only through the actor's checksum-bound managed `portrait_ref`; otherwise
the renderer uses a deterministic initials token.

Call `module_draft(action="get")` whenever current state is needed; every edit uses
the same D&D parser profile. Every write carries a stable operation-specific `idempotency_key`;
an exact retry returns the original result. A player may read `party` scope or
their own authorized `player:<actor_id>` scope only. Keeper scene reads expose
only the redaction marker, and player combat-map views omit blocked cells,
difficult terrain, world patches, checksums, and DM overrides.

## Actor cards and shared party state

| Intent | MCP tool |
|---|---|
| Create from direct/build/template/statblock, unified content actor, or exact narrative identity evidence | `character_create_from(mode=...)` |
| Read campaign actors, reusable library, or classify a support document | `character_query(get/list/library/document)` |
| Replace a complete reviewed card | `character_sheet_replace` |
| Inventory | `inventory_change(add/update/remove/equip/recharge/consume_ammunition)`, `inventory_transfer` |
| Wallet, spell, effects, resources, advancement | `wallet_change(adjust/transfer)`, `character_spell_prepare(set/replace_all)`, `campaign_change(party_rest/stable_recovery/advancement_configure/experience_award/loot_acquire/currency_spend/item_spend/consumable_use)`, `character_state_change(effect_add/effect_remove/resource_set/level_advance/stand)` |
| Ability scores | `dnd_ability_roll`, `character_ability_apply` |
| Actor-scoped knowledge | `actor_knowledge_change(add/revise)`, `actor_knowledge_query(list/search)` |
| Shared stash/wallet | `campaign_query(view="party")`, `inventory_change`, `inventory_transfer`, `wallet_change` |

### Unified core-rule, addon, module, and preset packages

`sagasmith.content-package` v2 is the only cross-installation boundary. Every
`.sagasmith-pack` has the same physical fields and content-addressed blob layout;
`kind=core_rules|addon|module|preset` preserves distinct authority. Legacy loose
JSON cards, `*_pack` envelopes, release manifests, and `.sagasmith-module` are
not accepted.

PC, NPC, and monster use `sagasmith.actor-card.v3`. Cross-installation actor
cards travel only inside finalized `preset` or `module` Packs. Import the Pack
through `content_pack`, then create a runtime actor from its exact artifact/card
identity. The target gets a fresh Character id and empty ActorKnowledge.
Optional card art references one package `actor_image` asset; image bytes never
enter Character state or snapshots.

The active edition's standard actors are preset-package cards, not engine
constructors or name lookups. Browse the relevant finalized Preset Pack with
`content_pack(action="list"|"get", kind="preset")`.

A module package contains compatibility and play profile, normalized sources,
Scene Atlas, catalogs, narrative dossiers/endings, reviews, maps/assets, cast,
monsters, pregenerated PCs, and final Agent confirmation. Export through
`content_pack(action="export", kind="module")`; import through
`content_pack(action="import", kind="module")` using exactly one managed `artifact` or
allowlisted `.sagasmith-pack` path. Core verifies every descriptor/blob hash and
replays the stored structure with fresh runtime actor ids. Campaign progress,
world state, ActorKnowledge, random streams, branches, and snapshots are never
packaged. A valid finalized package may activate only through an explicit Owner/DM action.

Source-authored Rule/Addon and Module Packs retain the Agent's final confirmation
in `metadata.agent_finalization`. Their `metadata.authoring_review` also retains
the candidate dispositions or package-edit history from the final draft, so
source-specific corrections and exclusions remain auditable after export/import.

Core-rule and addon packages contain flat rule definitions, selection artifacts,
mechanics, resolutions, sources, and optional actor cards. Stable source/chunk
keys are rebound to fresh local ids on import. Use
`content_pack(action="import", kind="addon")` for an addon, `kind="core_rules"` for
core_rules, or `kind="preset"` for a preset, providing one
archive artifact or allowlisted path plus an idempotency key. Inline descriptors
are rejected because they cannot prove their blobs.

Author new Addon Packs through `rulebook_draft`; Preset Packs must already be
finalized by the trusted system/content pipeline. There is no public Pack builder:
`content_pack` only manages immutable finalized archives and never accepts an
untyped payload bag or a component envelope.

Import does not grant campaign authority. Addons require a
separate revision-safe Owner/DM `content_pack(action="activate", kind="addon")`; module
activation remains campaign-specific. Exact package dependencies use the whole
descriptor checksum; embedded rule definitions use `definition_checksum` for
their immutable semantic identity. No release-manifest layer exists.

The campaign instance is authoritative. After any actor or party mutation, read
`character_query(view="get")` or `campaign_query(view="party")` and use returned `derived` values. Do not use
`character_sheet_replace` for a one-field mutation.

`character_create_from(mode="narrative_npc")` is the only creation path for an
important named module NPC whose cited chunk provides identity/role but no
combat statblock. It requires an active module id, exact scene/chunk/page range
and content hash, an excerpt containing the exact actor name, a role, and a
summary. The result stores source evidence in notes and reports
`combat_statblock="not_imported"` and `combat_eligible=false`; its actor card is
tagged `narrative_only` and `source_bound`. Those sentinel mechanics must not be
used for checks or combat. Create it directly in `play` with required payload
fields `campaign_id`, `name`, `role`, `summary`, `source_ref`, and
`source_excerpt`, plus a stable top-level `idempotency_key`; upsert the actor into
`manifest.npcs` and verify a checkpoint. A later distinct instance from the same
source chunk needs a new idempotency key.
When the source defines several anonymous instances under one printed identity,
create a separate actor for each one with `source_identity` equal to that exact
source label and a stable `instance_key`. By default, store the canonical name
`<source_identity> [<instance_key>]`; the card gains
`anonymous_source_instance`. If the Agent assigns a distinct proper name, send a
settled `identity_agent_ruling` with
`default_resolver="agent"`, `ruling_kind="agent_dm_adjudication"`, and exact
`assigned_name`, `source_identity`, and `instance_key` bindings. The service
adds `agent_named_source_instance` and rejects any mismatch or unbound name.
This preserves independent NPC state and knowledge without weakening source
identity, count, source evidence, or mechanics.
When a narrative-only actor later receives an exact combat statblock, call the
same rule/reviewed statblock creation mode with `replace_character_id` and
`expected_revision`. In-place materialization must preserve the existing actor
name, summary, notes, Actor ID, and ActorKnowledge while appending the new
statblock provenance. A replacement that renames or rewrites the narrative
identity is rejected; never create a parallel combat actor.
The service canonicalizes the returned `source_ref` to its verified module,
scene, chunk, page, heading, and content-hash fields. A regression verifier must
compare that canonical field set, while retaining optional asset-path, asset
hash, and purpose fields in its continuity evidence; it must not treat removal
of those unexecuted audit annotations as a failed actor creation.
Every `source_ref` embedded in a full-playthrough manifest is resolved through
that same managed-chunk contract when the manifest is written. Heading paths are
ordered paths: collapse only adjacent duplicate parser headings and preserve a
later re-entry into a same-named section. Manifest excerpts may omit intervening
prose while retaining source order, but every retained fragment must occur in
the cited chunk; runtime event, memory, progress, and ruling excerpts remain
contiguous exact evidence. A schema-valid but unresolved manifest citation is a
hard source error, not deferred documentation.
Before a public regression action mutates state, the driver calls
`module_expand` for the cited `chunk_id` and compares the complete canonical
module/scene/chunk/page/heading/hash field set. It then validates the runtime
excerpt against that expanded chunk. Scene-wide containment is insufficient
because adjacent chunks are concatenated in the scene view.

Full-playthrough scene transitions replace the snapshot-managed manifest through
`playthrough_manifest(action="replace")`. The driver requires an explicit stable
occurrence id for the transition; the complete normalized target manifest is
request payload, not identity. Replaying the same transition therefore submits
the same key and payload, while any later visit to a town, hub, or headquarters
uses a new id even when the payload is identical.
`record-outcome` treats its `world_state` argument as an additive object patch:
nested objects merge recursively and retain omitted siblings, while arrays and
scalars are complete replacement values. Do not resend one nested episode object
as though it were the whole world state, and do not infer an ending from a
previously overwritten sibling.

Source-defined conclusions use the public full-playthrough driver's
`configure-ending` action to store exact source evidence and machine checks
against authoritative manifest, fact, actor/NPC, quest, and world-state paths.
After the final sourced outcomes are checkpointed, `verify-ending` evaluates
those checks, marks the achieved ending and manifest completed, and creates and
verifies the terminal Snapshot DAG node. A nonempty historical combat projection
is not active combat: only its authoritative `active=true` flag blocks ending
verification. Preserve a completed encounter with
`snapshot_role="historical_final_encounter"` and
`combatant_state_is_current=false`; never erase it merely to make an ending pass.

Before passing an allowlisted PDF/text file to module import, use
`character_query(view="document")` when it may be a character sheet,
pregenerated-PC packet, or ability-score option document. The result stages and
normalizes the artifact, reports its checksum and `document_kind`, preserves
manual-input choices, and explicitly sets `module_draft_allowed=false` for
character documents. Never force such a document through the module parser.

For the initial campaign party, classify all supplied character documents before
creating any generated PC. Use every applicable module pregenerated character
first and preserve its exact source reference/checksum. Build a generated PC only
when no suitable active PC exists or an explicit player choice calls for one;
never build seats merely to reach a printed recommendation or initial plan. This
precedence is a provenance quality gate, not a fixed party count.
If complete text search plus visual review proves that the module states no
party-size range, record that absence and any Agent-selected positive initial
plan, but do not block party construction or play on completing the recommendation
review. Never silently present four, or a semantically unrelated search hit, as
the module's recommendation.
The manifest preserves this as `party_size_review` with
`default_resolver="agent"` and `ruling_kind="source_or_scene_fact"` while the
Agent performs the DM review. Missing recommendation evidence is diagnostic, not
an external play boundary; mechanically indispensable actor data remains subject
to its ordinary validation.

For a dead, missing, or departed PC, prefer an applicable unused module
pregenerated character and otherwise create one new legal character through the
same public Lobby tools. A replacement is a distinct actor and must have empty
ActorKnowledge before joining; never duplicate the predecessor's sheet or
knowledge. Preserve the predecessor and its independent ledger. A full-playthrough
driver may transition `play` to `lobby` for this build only through `game_phase`
and must restore the entry phase after either success or failure. Back in `play`,
register the replacement at the manifest's current Scene Atlas location with one
atomic continuity event. If the module explicitly prescribes that arrival, cite
the exact source excerpt and reference. Otherwise the Agent acting as DM decides
whether and how the new adventurer can plausibly join from the current scene and
world state, then supplies a settled
`default_resolver="agent"`, `ruling_kind="module_specific_procedure"` decision
and reason. These evidence paths are mutually exclusive: never cite merely
adjacent module prose to legitimize a DM-authored arrival. Give the replacement
only the joining fact it witnessed and explicit `told_by` handoff propositions,
replace the predecessor's active manifest slot, retain both actor ids and the
handoff event in replacement history, then create and verify a post-manifest
checkpoint.

`inventory_transfer(mode="character_to_character")` mutates two private actor
documents and therefore requires the caller to control both actors (an owner/DM
satisfies both checks), plus current campaign/source/target revisions. A failed
authorization or stale revision moves nothing. Party transfers instead use
`party_to_character` or `character_to_party`; the facade maps those directions
to one atomic shared-stash/actor mutation.

A charged spellcasting magic item is one `kind="magic_item"` inventory record
with an exact module `source_key`, structured `charges`, optional source-declared
`mechanics.charge_rules`, and `mechanics.spellcasting.spells[].artifact_id`.
`inventory_change(action="add")` resolves each artifact exactly once from the
active content lock and embeds its pack/version/rule references. Item-specific
casting times and component, attunement, and class-list requirements remain
authoritative. Call `character_action(action="cast_spell")` or
`combat_cast_spell` with `source_item_id`; the Runtime pays item charges instead
of spell slots and commits the applicable action, effect, and last-charge check
atomically. Use `inventory_change(action="recharge")` only when the recorded
trigger occurs; its server roll and clamp are part of that mutation. Direct dice
calls followed by charge patches are not equivalent.
The full-playthrough driver's `provision-source-item` action first validates the
exact module scene, excerpt, chunk hash, and matching
`module-chunk:<chunk_id>` item/charge source keys. It then uses only public
`inventory_change(add/equip)`, re-reads the hydrated actor, and verifies a
checkpoint. An imported statblock's printed AC override remains its base
calculation: an explicitly held magic-item AC bonus still applies, and a valid
Mage Armor effect may select its better unarmored calculation before that bonus.
If custody of that unique item changes, `transfer-source-item` validates the
scene evidence and calls public `inventory_transfer(mode="character_to_party")`
with current campaign and actor revisions. It moves the original record,
including charges and condition; acquiring a second copy as generic loot is not
equivalent.

Prepared-spell selection is edition- and class-aware. In `lobby`, use
`character_spell_prepare(mode="replace_all")` with the complete selected list and
`event: setup` only before the campaign first enters live play; `mode="set"` is
also only an initial setup edit. Returning to `lobby` does not reopen setup. In live
`play`, use `campaign_change(action="party_rest")` and supply the complete
`prepared_spell_ids` list on that member so recovery, campaign time, and a legal
long-rest replacement commit atomically.
In 2024, Cleric/Druid/Wizard may replace any number after a Long Rest,
Paladin/Ranger may replace one, and Bard/Sorcerer/Warlock replace one only when
gaining a class level. In 2014, Cleric/Druid/Paladin/Wizard may change their list
after a Long Rest, while Bard/Ranger/Sorcerer/Warlock use spells known. A 2014
level advance never accepts `event: level_up`: `method="class_prepared"` only
hydrates a legal class-list card with `access.prepared=false`, and a Wizard's
two level choices enter the spellbook without changing the prepared list. The
service derives rest timing from rest type, duration, and source-granted
Trance; callers do not submit a sleep/light-activity schedule. Always-
prepared spells and cantrips never occupy selections. Wizard selections must be
in the spellbook. Multiclass eligibility uses each spell's `grant.source_key`
and that class's own level. Campaign-bound characters inherit campaign edition.

`campaign_create` records `advancement_mode="milestone" | "xp"` in campaign
settings. Use `campaign_change(action="advancement_configure", payload={mode})`
only in `lobby`, outside active combat, with the current campaign revision. A
campaign missing this setting cannot award XP or advance until it is configured.

`campaign_change(action="experience_award")` is DM-authorized and valid only for
PCs in XP mode, outside active combat. Its payload contains nonempty `reason` and
`source_ref`, plus unique `awards` entries with `character_id`, positive `amount`,
and that PC's `expected_revision`; the call also requires the current campaign
revision and branch. All PC totals and a branch-local award record commit
atomically. It returns cumulative thresholds and `eligible`, but never changes a
level. Milestone mode rejects this action.

`campaign_change(action="loot_acquire")` is the play-phase transaction for one
source-defined treasure parcel. Supply a stable branch-local `acquisition_id`,
positive denomination amounts in `coins`, normalized shared-stash `items` with
stable ids, a nonempty reason, and the exact JSON `source_ref` from the selected
module chunk. The Runtime verifies that the chunk belongs to the campaign,
module, and scene and that `content_sha256` matches its expanded text. Currency,
items, and the branch-local acquisition record commit atomically; an exact
idempotent retry returns the original parcel, while a second key cannot reuse the
same acquisition id. Use separate `wallet_change` or `inventory_change` calls
only for genuinely separate in-world transactions, not to decompose one chest.
Regression orchestration may distinguish the occurrence scene/location from the
source scene for a delayed promised reward. The transaction's exact `source_ref`
still identifies the original promise chunk, while continuity records the later
scene and a location that exists in that scene's atlas.

The same split applies to public full-playthrough `record-event` and
`record-outcome` orchestration. `scene_id` and `location_key` identify where the
event actually occurs and where scene progress is written; optional
`source_scene_id` identifies the scene whose expanded chunk must contain the
exact excerpt and match `source_ref`. Continuity retains both ids. This supports
delayed rescue returns, deliveries, promises, and quest completion without
rewriting progress in the original scene or fabricating a return there.
Source, actor, and prospective manifest-schema preflight checks must finish
before the first mutation. The multi-tool outcome workflow is a resumable saga:
if delivery stops after matching scene progress commits, retry the identical
stable outcome payload so the driver resumes the remaining public commits. If
public progress contains a different partial outcome from a previously defective
client, preserve that lineage and create a child from the last verified parent
snapshot; never patch storage or overwrite the conflicting outcome.
When a module-specific consequence is left to the DM, attach a settled
`--event-agent-ruling-json` with `default_resolver="agent"`, a concrete decision
and reason, and `ruling_kind="agent_dm_adjudication"` or
`"module_specific_procedure"`. It may accompany exact source evidence when the
text establishes the situation but not its consequence, or stand alone for an
ordinary source-independent DM event. At least one evidence channel is required;
an Agent-only event must not name a fake `source_scene_id`, excerpt, or ref.
Scene progress and continuity preserve the committed ruling unchanged.

`campaign_change(action="currency_spend")` is the play-phase transaction for one
shared-wallet bill. Supply a stable branch-local `spend_id`, a nonempty `coins`
object whose denomination amounts are positive integers, a reason, the exact
module chunk `source_ref` that establishes the offered expense, and a nonempty
Core/Skill `rule_ref` or reviewed price basis. The Runtime validates the module,
scene, chunk hash, denominations, and available exact balances before committing
the new wallet and branch-local `currency_spends` audit together. An exact
idempotent retry returns the first result; another key cannot reuse the spend id.
Insufficient funds in any denomination leave every balance unchanged. Use the
regression `spend-coins` path to bind the occurrence Scene Atlas location,
witness ActorKnowledge, manifest sync, and verified checkpoint. Do not emulate
one payment with multiple negative `wallet_change` calls.

`campaign_change(action="item_spend")` is the play-phase transaction for a
source-cited bargain, tribute, gift, handoff, or destruction that permanently
removes a non-consumable item from the shared stash. Supply a stable
branch-local `spend_id`, exact existing `item_id`, positive `quantity`, reason,
and exact module chunk `source_ref`. The Runtime validates the chunk ownership
and hash, removes the item stack amount, and stores the removed item plus the
branch-local `item_spends` audit in one commit. An exact retry returns the first
result; missing items, excessive quantities, stale revisions, or duplicate ids
leave the stash unchanged. Use the regression `spend-item` path so the actual
Scene Atlas location, witness ActorKnowledge, manifest sync, and checkpoint are
committed. Do not record the narrative outcome while leaving the item in the
canonical inventory.

Outside combat, use `campaign_change(action="consumable_use")` to drink one
standard identified `Potion of Healing` from the shared stash. Supply a stable
branch-local `use_id`, the stack `item_id`, target PC id and current character
revision, reason, current campaign revision, and idempotency key. The 2014/2024
Core boundary supplies `2d4+2`; the service rolls it from the campaign random
stream and atomically removes one potion, heals with maximum-HP clamping, stores
the use record, advances the random position, and emits a rule receipt. During
combat, use the combat action path instead; never bypass action economy with this
play-phase transaction. Do not emulate potion use with separate inventory removal,
dice, and healing calls.

`character_state_change(action="level_advance")` is DM-authorized and valid only
in `lobby`, outside active combat. It requires the current actor revision, a fresh
idempotency key, the exact existing `class_name`, `hp_method` (`fixed` or
`rolled`; never provide `hp_roll`), and nonempty `reason` and `source_ref`. In XP
mode it requires the actor's current cumulative XP to meet the next-level
threshold; milestone mode relies on the cited trigger. It currently advances a
2014 or 2024 single-class actor exactly one level; multiclass advancement remains
an explicit stop condition. The atomic mutation raises
maximum HP without healing current damage, adds the new Hit Die, adds only newly
gained spell-slot capacity to available slots, recalculates preparation maximum,
and applies source-bound per-level HP modifiers from installed content to both
maximum HP and the matching HP-growth ledger entry. Its
`advancement.follow_up` lists eligible feature artifacts, subclass options, and
spell-choice counts. Those are mandatory subsequent catalog operations; after a
subclass choice, query again for its features. Finish with a complete
actor re-read and snapshot before returning to `play`. For a prepared caster in
either edition, `method="class_prepared"` only hydrates legal class-list cards;
a Wizard's reported level choices enter the spellbook. Do not submit a prepared
list during advancement; reconcile it through a later completed Long Rest.

### Single-authority state map

Treat the following as authorities and every paired field as a read-only
projection, receipt, or display label. Never repair a
projection with a second write.

| Concept | Sole authority | Non-authoritative representation |
|---|---|---|
| Rules edition and locale | Core campaign Rule Profile | Bound character `sheet.edition` is projected on every write |
| Elapsed campaign time | `campaign.state.game_time.elapsed_ticks` | `world_time` is an optional anchored calendar projection; wall-clock timestamps and exposure TTLs are operational time |
| Runtime phase | Active `campaign.state.combat.active`, otherwise `campaign.state.game_phase` (`lobby` or `play`) | The campaign view's `effective_game_phase` is the server-owned derivation consumed by drivers; exposure refreshes from it |
| Active module | Core active `ModuleSource` revision set, captured as exact `module_activations` in a snapshot | Import-job `activated` is a workflow receipt; do not store `module_drafts.active` in campaign state |
| Rule source revision | Immutable `RuleSource` id and chunks | A reimport retires the prior revision; default search selects the active revision, while an exact historic source id/citation remains auditable |

Public `rule_search` and `rule_expand` always require `campaign_id`. Their
source set is server-derived: matching bundled core, that campaign's Lobby
rulebook imports, and sources cited by the current branch's active Pack lock.
The optional `filters` object may narrow this set but never add another
campaign's source; the first lookup normally omits it, and a chunk id selected
under one campaign must not be expanded under another.
| Current branch | `campaign.active_branch_id` | Public `is_current` is derived; no independent branch boolean exists |
| Snapshot head | Each branch's `head_snapshot_id` | Public `snapshot.is_head` is derived; no independent snapshot boolean exists |
| Current scene | Core scoped `SceneProgress` whose scene belongs to an active module revision | Playthrough manifest chapter/scene is synchronized projection; `ModuleChapter.status` describes indexing only, and `current_room` is a label while `current_location_key` is spatial identity |
| Subjective belief | Branch-local `ActorKnowledge` revision head per actor | Objective `CampaignMemory` is a different ledger |
| Actor conditions | Validated character-card condition state plus active effect-source ownership | Encounter combatants are synchronized mirrors; removing one cause must not clear a condition still supplied by another effect, and immunity is checked by the shared condition engine |
| HP and expendable resources | Validated character sheet, mutated through shared HP/resource functions | Encounter combatants mirror condition/position state only; CLI or generic campaign patches cannot create a second combat/HP/resource ledger |
| D&D roll, rest, and spell arithmetic | `sagasmith-dnd` attack/check, exhaustion, lifecycle, effective-ability, healing-expression, save-damage reduction, HP, and bounded-resource helpers | MCP facades and regression drivers validate orchestration inputs but must not repeat ability-modifier, exhaustion, rest-duration, healing-scaling, half-damage, or resource-capacity formulas |
| Exact module evidence | Core exact-source field order, document suffix catalog, and source-evidence normalizer | D&D manifests may add purpose/asset/excerpt fields, but public calls project only the Core exact citation and must not maintain a second typography or hash-field contract |
| ASCII lookup keys and slugs | Core compact-key and slug normalizers | Each domain owns its fallback, maximum length, and ID prefix, but must not repeat the base case-fold/separator algorithm |
| Playthrough ending | Coupled manifest `status` and `ending` verification | A campaign row's administrative `status` is not a module ending |
| Audited mutations | One state transaction containing entity changes, revision group, rule receipts, random position, and idempotent response | Never update an entity and append its revision or replay receipt in a later transaction |
| Integrity encoding | Core canonical JSON plus SHA-256 | Snapshot checksum, rule-pack checksum/fingerprint, map checksum, and idempotency request hash keep their domain names but must not implement separate JSON encoders |
| Local service identity | Core `system:local` principal identity and campaign membership | CLI, MCP, gateway, and regression-driver defaults are projections of that principal, not separately invented service accounts |

Snapshot restore must restore the exact active module revision set
before restoring the current scene; it must never make a retired module scene
current merely because that scene id still exists for historical citation.
An absent Rule Profile is a hard configuration error: combat, character, import,
and item paths must not select their own 2014/2024 fallback.
Operational UTC timestamps and exposure expiry are produced by the MCP
operational wall clock, taking one timestamp per mutation or lease update. They
must never be copied into, compared with, or used to advance the campaign's
six-second game-time tick stream.

Facade maintenance follows the same rule: a compact action may keep a strict
schema, permission check, phase guard, revision, and idempotency boundary, but
its deterministic D&D result must delegate to the owning engine function.
Regression drivers may derive expected values with those public library
functions and must still perform every state mutation and die roll through the
public MCP tools. A new local formula is a contract regression even when its
current result happens to match.

## Branch-aware continuity

World facts, chronology, and subjective actor knowledge are different ledgers.

| Ledger | MCP tool |
|---|---|
| Branches | `branch_query(list/compare)`, `branch_change(create/checkout)` |
| World facts | `memory_change`, `memory_query(list/search)` |
| Events | `campaign_event(add/list)` |
| One actor's belief/knowledge | `actor_knowledge_change(add/revise)`, `actor_knowledge_query(list/search)` |
| Safe retrieved context | `continuity_context` with one shared `budget_chars` limit |
| Atomic post-scene write | `memory_change(action="commit")` |
| Continuity health | `memory_query(view="diagnostics")` (owner/DM only) |

Pass `branch_id` for an explicit historical branch. For player-safe narration,
use `continuity_context` with the acting `actor_id`, `scope_id`, and audience;
never substitute a broad `memory_query(view="search")` result for that context.

For owner/DM calls, `continuity_context.related_refs` accepts explicit
`actor|faction|item|location|module|quest|scene:<id>` links. The MCP also adds
current combat actors plus current manifest scene, module, and active quests.
Matching active `context_anchor` facts return `module_evidence` before lexical
facts, events, and actor knowledge. Identical chunk/excerpt bindings are
deduplicated, source hashes and campaign ownership are revalidated, and pinned
evidence is never silently evicted by the shared budget. The response reports a
pinned overflow; more than the safety cap fails with a request to narrow
`related_refs`. Player calls always receive an empty `module_evidence` list.

A `context_anchor` is a non-executable, DM-only source index. It has an empty
predicate and metadata containing exactly `schema_version=1`, a short `purpose`,
`related_refs`, and one or more `{source_ref, source_excerpt}` bindings. The
excerpt is exact normalized text from the immutable managed chunk. Fields such
as `trigger`, `condition`, `action`, `result`, guidance, paraphrase, or a
narrative expression are invalid. The returned purpose is not Agent evidence;
only the cited source excerpt is. Use the Agent to interpret that text against
live state, then invoke standard engine operations. Persist only the actual
result through the ordinary event, fact, knowledge, inventory, character, and
manifest contracts.

Use the three ledgers deliberately:

- `campaign_event` records what happened. For a witnessed subset, set
  `audience_scope="actor"`, list `known_by_actor_ids`, and set
  `knowledge_disclosure_scope="owner"`; use `party` only when every party member
  may know it.
- `memory_change` records objective world facts worth retrieving later. Prefer
  `action="upsert"` with a deterministic `fact_key`; revising an existing key
  requires its current `expected_revision_id`. Use `supersede` or a revised status
  instead of deleting history. Omitted revision fields remain unchanged. Keep the
  same deterministic key when the same fact is independently established on a
  sibling branch: Core reuses the stable fact identity but creates or advances
  only that branch's fact head. Do not append a branch suffix to evade
  visibility checks. A `fact_key` permanently identifies the same `kind`,
  `subject`, `subject_ref`, and `predicate`; changing any of those fields under
  an existing key is an identity conflict, not a revision.
- `actor_knowledge_change` records one PC or NPC's belief, inference, secret, or
  misinformation. Revising one actor must never revise another actor's ledger.

After a meaningful scene or combat outcome, call one `memory_change(action="commit")` with the
event, accepted fact changes, per-actor knowledge changes, and an optional
snapshot. The transaction rolls back as a unit if any actor, revision token, or
fact is invalid. Require a fresh `idempotency_key`; existing fact or knowledge
revisions require their current `expected_revision_id`. The MCP adds the installed
D&D and module-generation Skill SHA-256 manifest to the event payload, and the
snapshot captures that provenance.

`continuity_context` also returns a process-signed `context_receipt` bound to
the campaign, checked-out/read branch, authenticated principal, campaign
revision, requested refs, and the exact pinned module sources that were
returned. If a continuity commit cites a module source that is also pinned by
an active matching `context_anchor`, put this receipt in
`payload.context_receipt`. The MCP rejects a missing, forged, expired,
wrong-principal, wrong-branch, stale-revision, or source-incomplete receipt.
This turns the important anchored narrative boundary into an enforced
read-before-source-bound-commit contract without making narrative text
executable. A response-lost retry of an already committed idempotency key still
replays the original result.

Every campaign continuity response also carries an exact
`host_context_binding`: domain, campaign, authenticated-principal fingerprint,
role, audience, branch, `memory_policy="domain_authoritative"`, and their
derived `context_epoch`. A host must cross a hard context barrier on first bind
or any change before further inference or tools. Old model messages, summaries,
workspace/Dream memory, cached retrieval, receipts, and tool results cannot
enter the rebuilt domain prompt. See
`host-integration-bounded-context.md`.

### Generic bounded semantic evaluations

`continuity_context` supports six proposal-only purposes in addition to the
specialized NPC contract:

| Purpose | Boundary |
|---|---|
| `actor_turn` | NPC/monster intent/action only; rejects PCs and dialogue |
| `audience_render` | requires `audience="player"`; renders an already filtered projection |
| `faction_turn` | one faction's `faction_state` and `faction_knowledge` only |
| `campaign_expansion` | Lobby-only, review-only proposal for an emergent line or an authored Module's off-Atlas extension |
| `source_interpretation` | interpretation of current exact managed evidence |
| `bounded_ruling` | one Agent-owned semantic ruling question |

Each response is `bounded-evaluation-bundle.v1` with a fixed purpose-specific
output contract, signed receipt, allowed claim/decision bases and targets, and
explicit `may_call_tools=false`, `may_roll_dice=false`, and
`may_write_state=false`. Context, NPC, bounded-evaluation, and validation
receipts bind the authenticated principal by SHA-256 fingerprint and never
return its raw host identifier. Module evidence may be decision-only and therefore may
not support a proposal claim. Actor state is scoped to `actor:<id>`; faction
state and knowledge are scoped to `faction:<id>` and do not include objective
world truth merely because it names that faction.

The host evaluates only that bundle in a fresh zero-tool context and submits
the result to `bounded_evaluation(action="validate")`. The facade validates
signature, TTL, campaign/branch/head/revision/event sequence, principal,
subject, bases, targets, and the exact output schema. It never writes state or
settles mechanics. A source interpretation must copy the signed question,
provide at least one evidence-bound claim, and require DM review for any
ambiguity or uncertain claim. Actor/faction actions that need dice, checks, attacks,
movement, items, time, resources, or other state changes return explicit
resolution requests for ordinary public tools. After such a write, the bundle
is stale and must be reread. `audience_render` returns the exact safe
`publication.text`; publish that value unchanged.

### Persistent NPC conversation contract

During Play, Owner/DM opens one conversation with explicit same-campaign
participants. The MCP stores a durable draft journal and one private logical
runtime for every NPC. The Agent rules perception, comprehension, partial
renditions, and which NPCs should respond from current scene evidence. MCP
validates those facts, projects separate redacted actor inboxes, and returns
opaque activation refs only for selected responders. The Director never
receives private actor context, transport credentials, or leases.

An isolated host NPC subagent checks out its own capsule, retaining the same
`conversation_id + actor_runtime_id` model context across activations. It has
zero tools and returns only `npc-conversation-proposal.v5`. Every speakable byte
is inside an utterance segment with required `text` and `content_mode`.
`grounded`, `deception`, and `uncertain` segments require actor-owned basis refs; all supplied
refs and targets are validated against the capsule. Actions declare narrative or mechanical
settlement. MCP is the sole semantic validator. A Host may repair structured
validation failures inside the same lease, then receives a publication candidate.

The Director supplies overall and, when needed, per-segment audience facts to
`publish`; only then does the publication enter the journal. MCP derives
listener knowledge candidates from understood published segments without
asserting the speaker's claim is true. `close` explicitly selects actor-owned
and listener candidates and atomically commits the exact transcript.

Every write carries conversation revision and idempotency. Unrelated campaign
events do not stale the session. Branch/scene changes invalidate it; an actor
revision refreshes only that actor runtime. Before an authoritative mechanic,
scene mutation, phase transition, or combat, the Director closes or aborts the
whole conversation and releases every worker. If dialogue continues after the
public mechanic, it opens a new conversation and ingests the result as a new
stimulus. After a process restart, public `list` returns only active handles
owned by the authenticated principal on the checked-out branch; `get` expands
one handle without exposing private capsules or proposals. MCP never owns the
model or provider KV. See
`host-integration-npc-conversation.md`.

### Isolated single NPC turn contract

During Play or Combat, Owner/DM may call `continuity_context` with
`purpose="npc_turn"`, one NPC/monster `actor_id`, explicit
`interlocutor_actor_ids`, and a normalized stimulus. The result is a bounded
`npc-turn-bundle.v3`: the actor's sanitized card/self-state, its own
four-track ActorMemoryContext, exact actor-state relationship/goal/commitment
heads, public facts, relevant events where it is an indexed participant, immediate outward perception, a
small scene projection, and DM-only world/module portrayal context. Public
world facts and module evidence are marked non-epistemic and excluded from
`allowed_basis_refs`; only ActorKnowledge, the actor's own relationship/goal
state, self/identity, stimulus, perception, and participating past events can
support factual speech.

The `bundle_receipt` is process-signed and binds campaign/branch/head Snapshot,
campaign and actor revisions, latest event sequence, scene id/version, principal,
interlocutors, stimulus, exact fact and ActorKnowledge heads, allowed basis refs,
and module source digests. Any intervening relevant write, event, scene change,
restore, expiry, or principal change invalidates it.

An isolated model returns only `npc-turn-proposal.v1`. This is proposed intent,
speech, visible cues, possible action, resolution requests, and possible deltas;
it is not a die result or state mutation. If resolution is requested, use the
ordinary public engine tools and read another bundle. To commit, submit
`payload.npc_turn` with the signed receipt, proposal, accepted fact indexes,
accepted ActorKnowledge indexes, accepted narrative-action flag, and recorded
isolation level. Accepted actor-state facts are restricted to the speaker's
DM-only relationships/goals; knowledge is restricted to the speaker/listeners;
mechanical actions cannot be accepted through this commit.
Only none/gesture/refuse are narrative-only actions. Offer, surrender,
move/flee, attack, item use/exchange, scene transition, and other actions must
request public resolution. Factual/deceptive assertion, reveal, or lie acts
require an allowed basis ref, and every speech target must be a signed actor in
the bundle.

The server derives an `npc_dialogue_turn` event and participant rows for speaker
and listeners. Player actor-scoped event visibility uses this participant index
without inventing ActorKnowledge. The visible payload contains the utterance,
delivery, language, accepted visible action, and hashes—not private basis refs,
DM evidence, or decision summary. See `host-integration-npc-turn.md` for native
and logical isolation requirements.

Consequential writes involving a context-anchored named NPC can return a
non-blocking `narrative_followup` with actor ids and generic reasons such as HP,
condition, status, or position change. This does not execute source text. It
asks the Agent to read a fresh NPC bundle and decide the narrative response.

Use the individual event, memory, knowledge, and snapshot tools only for isolated
administrative work or when the server does not advertise
`atomic_continuity_commit`; never present a partially completed fallback as a
successful scene save. A snapshot contains a full restorable payload; its recap is
the branch delta. Before a restore call `snapshot_query(view="verify")`; after
restore verify the new head and refresh campaign, characters, module progress,
events, and continuity context.

Use `memory_query(view="diagnostics")` to inspect active/inactive ledger counts, orphan
source-event references, unsnapshotted events, latest checkpoint size, recap
provenance, and Skill-manifest drift. It returns health metadata rather than
narrative content. A non-null drift or growing unsnapshotted count is an
operational signal, not permission to rewrite history automatically.

## Deliberate boundaries

- `full` uses MCP-owned SQLite, optional ChromaDB, and managed module artifacts.
- `standalone/` remains a separate portable file workflow; it does not call MCP.

## Session exposure and game phase

The MCP, not Agent configuration, owns tool exposure. Every connection starts
with exactly 7 core tools: `exposure`, `server_capabilities`, `storage_status`,
`campaign_query`, `game_phase`, `resolution_presentation`, and `skill_query`.

Call `exposure(action="open", campaign_id?, principal_id?)`, discover exact tool
ids with `action="search"`, and change the session's loaded ids with
`action="set"` plus `add_tool_ids`/`remove_tool_ids`. `action="get"` returns the
current campaign, principal, phase, expiry, and loaded ids. Opening again replaces
the session binding and clears its loaded tool ids. A campaign phase change,
snapshot restore, branch checkout, or state undo/redo keeps the binding, crops
incompatible tools, and may change the authoritative phase or branch. After the
notification, refresh the native list and use `exposure(search/set)` to load the
needed tools; do not call `open` merely to refresh phase exposure.

Every successful open/set sends MCP `tools/list_changed`. The Host must refresh
the native list and call newly listed domain tools directly. This is the only
exposure layer. Use the native tool schema plus runtime validation as the input
contract. A Host that cannot refresh mutable native schemas does not implement
this D&D contract. Use bounded `skill_query(outline/section/search)` only for
task-specific Skill depth.

| Phase | Intended state |
|---|---|
| `lobby` | setup, imports, Pack authoring, campaign administration, character building |
| `play` | live non-combat exploration, downtime, dialogue, checks, and continuity |
| `combat` | active structured encounters in Grid or Agent spatial mode |

The server initialization capability advertises `tools.listChanged=true`.
Single-user hosts should set `SAGASMITH_DND_MCP_BOUND_PRINCIPAL_ID`; the server
then overwrites model-authored principal fields. Multi-user hosts must instead
hide and inject the authenticated principal per request. `system:local` is only
safe inside an explicitly trusted local process.

`campaign_query(view="resume")` is the recommended first campaign-bound call
because its continuity projection includes the exact `host_context_binding`.
Hosts must stop later tool calls emitted in the same model response when that
binding establishes or changes a context epoch. A repeated identical binding
does not loop. Audience, role, principal, campaign, branch, checkout, and
restore transitions intentionally establish a new epoch.

An exposure without `campaign_id` may add only bootstrap/storage tools allowed
by their policy. After campaign creation or selection, reopen with the campaign
id. Campaign administration and rule/module authoring additionally require
owner/DM membership. A campaign-bound exposure rejects arguments that
target a different campaign, including character ids resolved to that campaign.
Objective memory, actor-knowledge writes, snapshot history, state history, rule
receipts, scene progression, combat start/end/join, and map patches are likewise
kept under owner/DM authorization. Tool visibility never replaces actor and
operation authorization.

The runtime enforces the same boundary even when a caller bypasses progressive
discovery and invokes a facade directly. In particular, players cannot inspect
snapshot labels or lineage, reversible state history, or settlement receipts.
Non-local reusable-character library reads retain the reusable sheet but omit
private template notes.

Use `game_phase(action="set", tool_profile="lobby" | "play")` only for the
non-combat transition. Leaving Play requires every chase and persistent NPC
conversation to be closed. `combat_start` likewise requires no active chase or
conversation, moves the campaign to `combat`, and
`combat_end` returns it to `play`; the server removes incompatible loaded tools.
Authorization, revision, idempotency, and engine checks apply even if a
client presents a stale schema.
Direct character-card mutations (sheet replacement, inventory, wallet, effects,
resources, rests, non-combat casts, and activities) are rejected while an
encounter is active. Do not use a profile mismatch to bypass combat action
economy.

## Source-bound actors and scene preflight

Create likely combatants and reinforcements in `lobby`, before combat. For a
creature present in an imported rule source, use
`character_create_from(mode="statblock")` with the imported `source_id` and, when
needed, reviewed `chunk_ids`. The parser preserves source provenance, exact AC,
HP, abilities, defenses, senses, weapon attacks, and structured Multiattack.
When PDF layout text splits the selected card across sibling columns, also pass
the exact printed heading as `payload.source_statblock_name` while retaining the
campaign instance name in `payload.name`. On direct-parse failure, the server
selects that named creature core, stops before the next creature core,
reconstructs the card from deterministic text chunks, and narrows provenance to
the retained `source.text_layout_recovery.chunk_ids`. This first recovery path
does not render a page and works for an Agent without image capability.
If required source facts remain absent or conflicting for a 2014 card, call DM-only Lobby
`rulebook_draft(action="edit", operation="statblock_recovery",
payload={job_id, name, page_number?, agent_fill?})`.
Use the exact printed heading for `name`; keep a differently named campaign
instance in the later actor-creation payload.
The server, not the Agent, finds the physical page, performs geometry-aware local
OCR, isolates one target column, rejects low-confidence identity/core facts, and
corroborates identity, AC, HP, Speed, all six ability scores, and Challenge
against the target embedded-text segment. If that segment is unavailable, a
second OCR scale must agree on the complete critical fingerprint. Use the
returned checksum-bound `review_id` with
`character_create_from(mode="reviewed_rule_statblock")`. This 2014 OCR route
requires no image understanding by the Agent and rejects a 2024 import rather
than coercing it through 2014 syntax. For 2024, use exact edition-matching
indexed text with `review_mode="agent_text"`, or an image-capable
edition-matching visual review. Ambiguous headings, missing page hints,
low-confidence facts, evidence disagreement, unsupported statblocks, or parser
warnings do not authorize repair from model memory.
When one structurally selected 2014 card instead contains bounded OCR damage,
the same `recover_statblock` action accepts `statblock_slot` and
`ocr_corrections`. Corrections require an exact physical page and slot.
`ocr_corrections.abilities` maps only `str|dex|con|int|wis|cha` to a complete
`score (+/-modifier)` cell. `ocr_corrections.text_replacements` contains 1-20
objects with exactly `old` and `new`; the old normalized span must match once in
the selected OCR card and the new span must be corroborated by the staged page
text. Corrections are applied to every OCR model/scale attempt before parsing,
are included in the recovery evidence, and are bound into idempotency and review
lineage. Changing a correction requires a new key. This lets a text-only Agent
repair OCR by comparing server-returned text streams while preventing model
memory from changing source mechanics. An exact retry under the same key returns
the stored complete recovery/review response before entering any OCR provider;
it must not repeat model-assisted review. If only the rendered image proves the
fact, use an actually image-capable `review_mode="visual"` reviewer instead.
Successful exclusion of trailing creature prose or page furniture is returned
separately as `normalization_notes`. These notes preserve the audit trail but are
not executable uncertainty: they must not create `ruling_requirements`, change
`settlement` away from `automatic` on their own, or invalidate a whole card.
When recovered rulebook text includes an ordinary weapon-only Multiattack, the
standard D&D parser and engine own its exact composition. `agent_fill` is
rejected. An open, conditional, or special-action composition instead remains
an exact-source direct Agent/DM ruling without an executable Multiattack
mechanic reference. This does not make
every creature-specific passive or rider a new generic engine rule: exact
source prose remains evidence on the unified actor/content card and may carry a reviewed
generic plan. Otherwise the DM Agent compiles that plan on first use while the
engine continues to own payment, rolls, damage, action economy, and timing.
Text-layout recovery also compares every explicit
`Melee/Ranged [Weapon|Spell] Attack:` source marker with the parsed weapon and
identified statblock-spell actions. One successfully parsed attack cannot hide
a later OCR-damaged action. Context-bounded generic repairs may normalize a
mismatched action-qualifier bracket or an OCR-corrupted numeric range, but an
uncovered marker rejects the card and enters bounded OCR recovery. This
coverage check is independent of creature name.

When OCR is structurally ambiguous but the indexed rule source contains the
complete card in one exact-page, ordered contiguous chunk segment, DM-only
`rulebook_draft(action="edit", operation="statblock_review")` also accepts
`review_mode="agent_text"` and `evidence_chunk_ids`. The import job must belong
to that same `source_id`. A driver may deterministically choose among historical
jobs only when every candidate has the same nonempty artifact name and checksum;
different artifact identities require explicit source review and
`--source-job-id`. The server binds the review to
the staged PDF checksum and page render checksum, verifies source/page/ordinal
membership, parses the normalized card, rejects every normalized fact absent
from the selected text, and rejects omission of any selected statblock evidence.
The review content kind and parser must match the locked 2014 or 2024 edition.
If that reviewed rulebook card contains an ordinary weapon-only Multiattack or
another standard mechanical card, `rulebook_draft(action="edit", operation="statblock_review")`
accepts it only when the engine produces a complete structured implementation.
An open/special composition must instead preserve the exact direct Agent-ruling
boundary described above. The review rejects
`payload.agent_fill`, including `additional_actions` and derived-review fills.
A standard passive/action with missing or conflicting facts, or a missing
generic transaction, is an importer or engine defect; do not hide it with
creature-name checks, ad hoc `once` flags, or post-damage HP edits. Exact
creature-specific prose must at least retain a source-bound direct Agent-ruling
requirement during import. A reusable typed plan may be persisted during
import/review or compiled by the DM Agent on first use. Standard, addon,
module-authored, and homebrew cards must enter play with a native mechanic or
that exact-source ruling boundary; they must never enter as unsupported prose.
The indexed-text validator permits only position-bound OCR confusables
`l/I↔1`, `o↔0`, and digit-bounded `f↔/` in a numeric range. All other numeric
tokens, DCs, bonuses, dice, damage types, and rule terms must remain exact.
The layout recovery may recompute a corrupted redundant ability modifier from
its visible score and may infer one missing ability label only from a complete
six-score row plus the other five labels in unique canonical order. It never
invents a score. Explicit `Actions for Type ...` sections create separate
portable actors rather than a union of mutually exclusive action sets.
The stored review uses `confidence="reviewed_text"` and retains per-chunk
checksums. Visual review remains `review_mode="visual"` and
`confidence="reviewed_image"`. Missing or conflicting indexed facts remain an
explicit external source-review boundary.
Catalog projection treats `statblock_catalog_recovery.complete_pages` as
whole-page authority. A later Agent-named review on an incomplete page replaces
only one same-name or mechanically identical candidate; otherwise it is added
without deleting sibling cards, and any ambiguous OCR debris must be explicitly
rejected at catalog review. Preset export is stricter: every source review must
map to exactly one accepted catalog artifact, and only the strongest review may
claim that artifact. Unmatched and duplicate historical reviews are audited but
never exported as actors.
For an image-only module card, use the reviewed visual workflow and
`mode="module_statblock"` instead. Every module-authored Multiattack is an
Agent-review gate. `module_query(view="candidates")` returns
`agent_fill_requirements` with the exact activity excerpt and available parsed
weapon ids; `module_draft(action="edit", operation="content")` rejects a parser-only
composition until `payload.agent_fill` covers every listed activity exactly
once. This remains true when phrase matching happened to produce the correct
candidate. Each requirement allows `structured` or `agent_ruling`: the latter
accepts an exact excerpt and reason without options, removes any executable
parser proposal, and preserves the source procedure as an Agent-owned ruling
when selected. Current automatic import supports reviewed English 2014
SRD-style and 2024 SRD 5.2.1-style statblocks with complete numeric weapon or
spell attacks. A spell-only card without numeric attack facts, an edition
mismatch, an ambiguous card, or another unsupported block must
remain unresolved; do not replace it with a similar SRD creature or invent a card.
An attack roll whose `Hit` clause applies only a condition or other effect is
still a legal attack even when it prints no damage dice. Preserve an empty
damage expression and the complete source excerpt; never invent zero damage, an
ability-modifier damage value, or a substitute attack. At first use, an
unresolved custom clause opens a source-bound pending window. The DM Agent reads
the exact card and indexed evidence, authors one generic `resolution_plan`, and
persists it with `content_solution(action="compile")`. The engine then validates
its source-card id, actor revision, fingerprint, bindings, random stream, and
generic opcodes through `combat_choice(action="execute_plan")`. Later uses reuse
that plan. If the generic vocabulary cannot express the clause, keep the result
at an explicit Agent/DM ruling boundary; do not add a creature-name switch,
phrase parser, or monster-specific facade field. A candidate blocked solely
because an effect-only attack lacks damage dice remains an importer defect.
A custom AC-changing Reaction uses the same compilation boundary but a contextual
transaction: the persisted plan has `trigger="attack.after_hit"` and exactly one
static `attack.ac_bonus` step. The attack then opens `pending_reaction`, and
`combat_choice(action="resolve_defense")` spends the Reaction/card use, applies
the reviewed bonus, and completes the stored attack. Raw
`choices.reaction_defense` data never creates a candidate. Standard `Shield`
continues to use its locked spell implementation.
When a complete statblock action repeats a known spell, the action is authoritative
for that creature. Hydration preserves the Core card's components and provenance
but overlays the displayed effect/range and structured attack resolution together.
Clients must reject a newly prepared actor if those views disagree; do not show a
base-spell range while the engine enforces the statblock override.
When the source card prints `Spellcasting`, `module_query(view="candidates")`
must validate it as structured spellcasting before review/creation. A
`Spellcasting: descriptive passive is not automatically settled` warning blocks
that actor from combat. After `mode="module_statblock"` creation, compare the
source casting ability, slot maxima, and exact spell-name set with
`sheet.spellcasting`, `sheet.content.spells`, and
`derived.spellcasting.prepared_spell_ids`. Empty or incomplete hydration is not a
DM ruling boundary; repair/refresh the parser and recreate the actor from a clean
snapshot rather than patching its sheet.
The text candidate normalizer must retain a named `Spellcasting` entry when its
description begins with the creature's proper name, and must not mistake prose
inside a `Hit` clause or surrounding monster lore for a new trait/action.
Context-bounded OCR repair is allowed only when the token is mechanically
impossible and the printed rule is unambiguous; unresolved spell names disable
the card's `spellcasting` capability with
`incomplete_statblock_spell_hydration` without invalidating unrelated attacks.

Before `combat_start`, call `module_query(view="preflight")` with a manifest:

```json
{
  "schema_version": 1,
  "groups": [
    {
      "key": "dead_three",
      "label": "Dead Three attackers",
      "role": "combatant",
      "required_count": 8,
      "actor_ids": ["canonical campaign actor ids"],
      "source_scene_id": "same-module scene id",
      "source_excerpt": "exact normalized module excerpt, 8 to 500 characters"
    },
    {
      "key": "tavern_reserves",
      "label": "Bribable tavern patrons",
      "role": "reinforcement",
      "required_count": 5,
      "actor_ids": ["canonical campaign actor ids"],
      "source_scene_id": "same-module scene id",
      "source_excerpt": "exact normalized module excerpt, 8 to 500 characters"
    }
  ]
}
```

Required combatants must be present in the initial `participant_ids`.
Reinforcements must not be initial participants. Preflight is false only when a
required actor is missing or its whole card is invalid. Each combat card separates
`card_valid`/`hard_blockers`, `state_flags`/`can_take_turn`, and
`available_capabilities`/`disabled_capabilities`. Dead or 0 HP is state; missing
range, ammunition, or semantic settlement disables only the affected capability.
Manual rulings are surfaced but do not falsely disappear. Each combat card also
returns structured `ruling_requirements`: ordinary adjudications identify the
Agent as the default resolver, while player intent and missing/conflicting source
identify their external boundary. A missing ranged or thrown range disables that
attack rather than licensing an invented distance. When the exact
source replaces a numeric range with a complete positional target restriction
(for example, one target directly below the attacker), preflight instead reports
that restriction as an Agent-owned manual ruling and keeps the card valid. The
Agent may select the action only when current scene/map positions satisfy the
printed restriction; it must not invent a generic numeric range. The excerpt must be an
exact normalized substring of that same module scene; it is not a fuzzy query and
a paraphrase or another occurrence of the room label is rejected. `ready=true`
means that the actor may enter combat, not that every card entry is automatic.
`required_count` is the complete branch-local group count established by the
source, a recorded source-table roll, or an explicit Agent-as-DM composition
fact. It is
not derived from the current length of `actor_ids`; missing cards must keep the
group blocked in preflight. If an excerpt names a larger count or additional hostile groups,
manifest the complete composition or record the source-supported branch change
that removed them. Never select a shorter substring merely to evade that count.
`automatic_spell_ids` describes structured effect settlement, while component,
targeting, passive, and on-hit uncertainty can still appear in `manual_rulings` or
`ruling_spell_ids`. A scene offer such as
“pay at least 10 gp, then DC 15 Charisma (Persuasion)” is resolved with an
action-bound `combat_check(action="improvise", ability="persuasion", dc=15)` and
advantage only when the stated offer condition is satisfied. On success call
`combat_join`; the canonical actor enters at the next round boundary with a full
turn. On failure no actor joins.

Rule text retrieval and executable rules are separate. For user documents, use
`rulebook_draft(action="start")`; it accepts only configured import roots, and every
later action reads only the checksum-addressed MCP-managed artifact. Core performs
the shared PDF/Markdown normalization and records the original checksum, parser
warnings, and per-chunk page ranges. Direct ingestion helpers are internal and are
not part of the public contract. Core+D&D produce the first mechanical draft;
the Agent reviews and edits only source-bound candidates through
`rulebook_draft` until explicit finalization. Only the finalized Pack's
validated declarative IR can settle mechanics; arbitrary Python, expression
evaluation, network access, and database paths are forbidden. Import does not
enable a Pack. A DM explicitly pins a validated version per branch, and snapshots keep
the exact version/checksum lock. Missing locked versions never fall back to a
newer version. Use `campaign_rules(action="explain")` to audit applied mechanic
ids, citations, and the deterministic fingerprint. Use
`campaign_rules(action="receipts")` for the fingerprint and citations stored
atomically with historical settlements.
Every `selection_ready` spell artifact is compiled through the same canonical
definition validator used when that spell is hydrated onto an actor. Range,
duration, components, unknown fields, level, class eligibility, and any
structured resolution must therefore fail before pack activation, not later
during character or statblock creation. OCR repair may join only bounded
mechanically identifiable split tokens from the managed text; the Agent must
review the exact candidate and source chunk rather than reconstruct a spell from
memory. This import path is text-only and does not require host-model image
understanding.
For a user-imported executable rule, finish the `rulebook_draft` evidence/edit
loop and finalize it as `core_rules` or `addon`. Citations must be imported
chunk ids and are resolved server-side to the exact source id, document
checksum, heading path, and page range. Use `character_check` outside
combat and `combat_check` during combat when an enabled `check.before` rule needs
DM-established `rule_facts`.

When a 2014 source requires a contest, do not invent a DC or compare two
unrelated client-side rolls. Use `character_check(action="contest")` with both campaign actors,
both appropriate abilities or skills, and each side's printed or reviewed
advantage/disadvantage. The server rolls both sides in one branch-scoped
transaction, compares totals, and records a tie as `tie_no_change`, as required
by the 2014 contest procedure. Use the full-playthrough driver's
`resolve-contest` action when the contest is part of campaign regression.

For a 2024 PC that has Heroic Inspiration, an ordinary Play check records a
`resolution_id`, every original d20 in roll order, and its random-stream
position. Immediately after that roll, call
`character_check(action="reroll")` with the exact `resolution_id`,
`roll_index`, and `expected_original_roll`. The server rejects stale or changed
rolls, death saves, non-PCs, non-2024 actors, combat use, and a second spend; it
consumes Heroic Inspiration, rolls exactly one replacement die, and requires the
new result. Never rerun the whole check, choose between old and new dice, or
reroll both dice from Advantage or Disadvantage.

For source-cited playthrough checks, an idempotent retry means the exact same run,
scene, Scene Atlas location, actor, kind, ability/skill, DC, proficiency,
advantage/disadvantage, and checksum-addressed source chunk. Include that complete
identity in progress, `character_check`, `memory_change(action="commit")`, ActorKnowledge, and
manifest-sync keys. A later check elsewhere in the same indexed scene is a new
roll even when its actor, ability, and DC happen to match.

Treat rule-profile and branch rule-pack changes as campaign writes. First read
`campaign_rules(action="get_profile")`, then pass its latest `campaign_revision`
as `expected_revision` together with a stable `idempotency_key` to
`campaign_rules(action="set_profile")` or `content_pack(action="activate" | "deactivate", kind="core_rules"|"addon")`. Reuse the
same key only for an exact retry; a stale revision requires a fresh read and review.

The base engine is not an implicit fallback. Every new campaign locks either
`dnd5e.core.2014` or `dnd5e.core.2024`, including a fingerprinted coverage list
for the existing combat, movement, reaction, damage, rest, spell, character,
and MCP mutation boundaries. Optional packs layer on top of that core provider.
If a runtime upgrade changes the locked core fingerprint, settlement fails until
the DM explicitly reviews and relocks the campaign profile. During active combat,
first finish the current atomic write, call `snapshot_create`, and require
`snapshot_query(view="verify")` to report that checkpoint valid and current. Then
call `campaign_rules(action="core_relock")` with the exact old Core fingerprint from
`campaign_rules(action="get_profile")`, current branch id, campaign revision,
checkpoint head id, a bounded reason, and a fresh idempotency key. Re-read the
effective profile, add a DM-visible maintenance event, and create/verify a second
snapshot before resuming settlement. This tool changes only the built-in Core
lock; edition, locale, publications, user options, and optional pack activations
remain unchanged. Never use `campaign_rules(action="set_profile")` to bypass this
checkpointed combat path.
The public regression driver's relock identity includes both the prior Core
fingerprint and verified checkpoint head. A replay is valid only at that same
boundary; after a restore, new head, or another Core change, re-read the profile
and use the newly derived request identity instead of replaying an earlier
maintenance mutation.

The public campaign and full-playthrough regression drivers both require
`--core-relock-reason`. Supply a concise reason specific to the active campaign,
the tested rule change, and whether any data migration occurs. Never hard-code or
reuse a reason from another module: the exact text is persisted in the relock
receipt and is part of the campaign audit trail.

Snapshot restore and branch checkout check the saved Core lock before changing
live state. A save requiring an unavailable Core fingerprint needs an explicit
conversion path and is never silently upgraded. First verify the snapshot and call
`snapshot_query(view="core")`. After reviewing the runtime change, call
`branch_change(action="create_core_upgrade")` with the target slot, new branch
name, exact saved/runtime Core fingerprints, current campaign/branch guards, a
bounded reason, and a fresh key. The transaction preserves the current branch,
leaves the source snapshot payload/checksum immutable, materializes the target
only on a new branch, replaces only its Core lock while retaining
edition/locale/publications/user options/optional activations, and immediately
captures a converted child snapshot.

## Integrity and identity contract

Campaign creation accepts `principal_id`; the server creates an owner membership
for that principal. Platform users must be resolved to stable principal IDs before
calling sensitive tools. Roles and actor grants are checked by MCP, not supplied by
the model as trusted claims.

`campaign_create` and every `character_create_from` mode require a fresh
`idempotency_key`; their entity rows, initial branch/owner membership where
applicable, and replay receipt commit atomically. A replay of build mode
returns its original library template and campaign instance as one pair.

Use `access_grant(scope="campaign")` for campaign roles and
`access_grant(scope="actor")` for explicit PC/NPC control or private-sheet
visibility. A player's `player_name` field is descriptive
only and is never an authorization mechanism.
The actor payload accepts only `actor_id` plus at least one explicit boolean
`can_control` or `can_view_private`. Omitted permission booleans retain their
current values; `role`, string `control`, and `permissions` are unsupported.

All campaign-bound granular character writes require the character's current
`expected_revision` and a fresh `idempotency_key`. Inventory transfer additionally
requires `expected_campaign_revision`, `expected_source_revision`, and
`expected_target_revision`.

`memory_change(action="commit")`, append-only `campaign_event(action="add")`, every
`memory_change`, and `actor_knowledge_change(action="add")` require a fresh
`idempotency_key`. Existing `memory_change(action="upsert" | "revise" |
"supersede")` targets require the current fact `expected_revision_id`.
`actor_knowledge_change(action="revise")` also requires the current knowledge
`expected_revision_id`; neither token is the character-card revision.

Branch creation/checkout and snapshot restore require the current campaign
revision, active `expected_branch_id`, and a fresh key. Snapshot creation requires
the current campaign revision, current `expected_head_snapshot_id` (use `""` when
the branch has no head), and a fresh key. `state_revision(action="undo" | "redo")`
requires the current `expected_history_sequence` from
`state_revision(action="history")` plus a fresh key.

Branch creation advances the campaign revision even when it does not checkout
the new branch. Use the returned `campaign_revision` or reread the campaign
before the next guarded write; never reuse the pre-create revision.

A continuity commit that combines campaign/character documents with events,
facts, ActorKnowledge, progress, or rule receipts is deliberately
non-reversible. `state_revision(action="undo" | "redo")` must refuse that
mutation group; recover through a verified snapshot or branch instead.

`branch_change(action="create", payload.checkout=true)` returns both the new branch and the materialized
snapshot; pointer changes and state restoration are one transaction.
`branch_change(action="create_core_upgrade")` likewise returns the converted
branch and child snapshot plus both Core locks and the reviewed reason.
The transfer is one mutation group, so one `state_revision(action="undo")` restores every affected
wallet, item stack, and character revision together. Retrying the same key replays
the original result; reusing it with different arguments is an error.

Use `branch_query(view="compare")` before discussing alternate timelines. There is no implicit
branch merge: world facts and each actor's subjective knowledge require explicit
conflict decisions.

Fresh MCP storage automatically seeds the compact bundled SRD corpus. Use
`rule_seed_status` and `rule_seed_bundled` to inspect or re-run the idempotent seed.
Structured combat is an auditable preflight/commit surface. Initiative, turn
budgets, movement spend, canonical derived weapon attacks, typed damage,
resistances, vulnerabilities, immunities, temporary HP, concentration windows,
healing, and death saves are automatic. Surprise is a supplied scene fact and
is interpreted by the selected 2014/2024 ruleset. The DM may provide a
`participant_config` at `combat_start` with token position, disposition, reach,
`hidden`, explicit `visible_to_actor_ids`, surprise, and initiative. Omit
`visible_to_actor_ids` for an ordinarily visible creature; provide it when known
special senses let only named participants see a hidden or Invisible creature.
When the expanded encounter text explicitly declares that a participant begins
combat under a condition, include `participant_config.source_conditions`. Each
entry requires a supported condition, `duration="encounter"`, the immutable
expanded-chunk `source_ref`, and an exact `source_excerpt`. The server verifies
module, scene, chunk hash, pages, headings, and excerpt, applies all cited
conditions atomically with `combat_start`, keeps the canonical cards synchronized
during combat, and removes only conditions added by that encounter at
`combat_end`. Never use a complete character-sheet replacement to inject or clear
an authored opening condition.
An owner/DM Agent may end one of these conditions before combat end through
`combat_common_action(action="interact_object")` only when an ordinary removable
object is its exact authored cause. The payload must name the object and
`interaction="remove"`, identify `remove_source_condition`, reproduce the exact
stored `source_ref` and `source_excerpt`, and carry a settled
`default_resolver="agent"`, `ruling_kind="agent_dm_adjudication"` decision and
reason. The server requires the condition to be active, encounter-added, and
owned by that actor, consumes only the current turn's object-interaction budget,
marks that one source record inactive, and removes the character condition only
when no other active effect still owns it. Players cannot self-author this ruling,
and neither the Agent nor the regression driver may patch the sheet directly.
When a source-bound weapon records unconditional typed damage parts, one
successful hit rolls every part and applies per-type defenses as one simultaneous
damage instance. Conditional custom damage, save riders, persistent conditions,
and unusual triggers must not enter attack arguments as ad hoc ruling fields.
They stay on the exact source card, compile once to a persisted generic plan,
and execute through the owned pending window.
A nonempty `on_hit_ruling` is source evidence only; it is not an
executable recipe or permission to repeat the hit.

Standard D&D mechanic references registered in the campaign's active rule lock
remain server implementations; a core-looking string alone is not executable.
Accounting, phase, or transaction mechanics also do not prove that a card's
authored outcome is implemented. If a locked standard card lacks both an
outcome implementation and a persisted exact-source content clause, the
pre-payment result is
`semantic_solution.status="engine_implementation_required"`; fix and test the
engine rather than compiling that standard rule as homebrew. A reviewed
spell/item/creature-specific Agent clause is not a replacement for standard
action economy or accounting: it is the durable settlement of that exact card
and is applied only through public engine operations.
When a standard spell does have a persisted `agent_ruling` clause, its first
`combat_cast_spell` call without a declaration returns the exact
`agent_ruling_contract` before payment. Its `submission_parameter` and
`submission_shape` are authoritative: resubmit that same cast as
`declaration={"agent_ruling": {...}}`, with the exact source excerpt and bounded
Agent decision. Do not flatten those fields under `declaration` or use
`component_ruling` for the spell effect. MCP atomically pays the recorded
action/resource, starts any source-defined concentration, and returns
`semantic_solution.status="agent_ruling_committed"`. An innate/statblock spell
must omit `signature_free_cast` so its recorded innate resource is authoritative.
For imported or homebrew content without such an implementation, import/review
preserves the exact source evidence and may store a reviewed source-bound plan.
If no plan exists, the first live use returns
`semantic_solution.status="content_authoring_required"` before the custom rider
is executed. The DM Agent may call `content_solution(action="compile")` in
Lobby, Play, or Combat, using the exact actor revision and card identity. The
plan is persisted with that exact content artifact and the same paid window resumes through
`combat_choice(action="execute_plan")`; retries and later uses reuse its
fingerprint and never reinterpret prose.
Never put arbitrary Python names or state patches in a plan. If an occurrence is
genuinely unique, cannot be expressed by available engine calls, or depends on
unstructured scene judgment, keep it in the explicit Agent/DM ruling boundary
instead of inventing a reusable recipe.
For 2014 surprise, first satisfy any imported scene prerequisites that merely
avoid automatic detection, then resolve each hiding actor's canonical Stealth
check and compare the individual results with each opposing creature's passive
Perception. An opponent that notices any threat is not surprised. Surprise is
therefore a per-participant scene fact, not the result of applying the ordinary
half-success group-check rule to the party. `hidden` and `surprised` are distinct
facts. Store the source prerequisite and comparison matrix in an auditable
campaign event before supplying `participant_config.surprised`.
If the source itself explicitly declares that a named participant can be
surprised on the chosen route, the full-playthrough driver may supply that
per-participant scene fact with the exact excerpt and no invented d20 check.
When the declaration is in a predecessor scene, the driver must consume a
passed public source-bound `record-event` or `record-outcome` report through
`--source-surprise-report`. It verifies the campaign, event, `source_ref`, and
exact excerpt before applying the separately enumerated surprised actor ids;
the current encounter's hostile-manifest excerpt remains independent.
Multiattack is an explicit action choice. `derived.attacks_per_action` represents
the actor's ordinary Attack action (including a real Extra Attack feature); it is
not inflated from a monster's Multiattack card. Pass a canonical
`multiattack_option_id` only when selecting that structured Multiattack and omit
it for one ordinary weapon attack. A descriptive Multiattack without executable
options requires an Agent-performed DM ruling only if selected and must not
disable ordinary attacks. During Lobby review, the Agent must read every exact
module-specific composition and attach
`payload.agent_fill.multiattack_options` to
`module_draft(action="edit", operation="content")`. Each declaration is bound to the
activity id and exact source excerpt and may contain only existing parsed weapon
ids, compatible modes, and explicit counts. The server validates it and stores
the normalized Agent attribution in immutable review metadata. This is a
semantic review result, not a generic sheet patch or a reason to grow
phrase-specific parser exceptions. Parser-produced options are suggestions only
for module cards and cannot bypass this fill gate. If the source combines a
special activity, recharge/choice procedure, or another unsupported semantic,
submit `resolution="agent_ruling"` without `options`; the server removes parsed
options and keeps the exact action as an Agent-owned DM boundary.
In grid mode, `combat_movement(action="move")` verifies the declared grid
distance and creates an owned `opportunity_attack` reaction window only when a
mover leaves an eligible hostile's reach; `combat_reaction_attack` settles that
window and its attack in one mutation. Collision, terrain, reach, visibility,
and geometry are evaluated from the encounter map. `movement_mode="forced"`
and `movement_mode="teleport"` are effect-driven position changes: they may
move a combatant outside its turn, do not spend its voluntary movement pool,
and do not open opportunity-attack windows. Teleportation accepts a destination
but no traversed `path`. In agent mode, movement has
no destination coordinates and requires exactly `decision_id`, `reason`,
`destination_legal`, `distance_ft`, `difficult_terrain_extra_ft`,
`moves_farther_from_turn_source`, `enters_turn_source_30_ft`,
`moves_closer_to_visible_fear_source`, and
`opportunity_attack_actor_ids` in `spatial_facts`.

Agent-mode attacks require the structured facts `decision_id`, `reason`,
`targetable`, `in_range`, `long_range`, `cover_degree`,
`attacker_can_see_target`, `target_can_see_attacker`,
`target_within_5_ft`, `close_threat_actor_ids`, `helper_actor_ids`, and
`target_adjacent_ally_actor_ids`; cleave eligibility is optional. Agent-mode
area effects require exactly `decision_id`, `reason`, `affected_target_ids`,
`excluded_actor_ids`, `line_of_effect_clear`, and
`friendly_fire_included`. Grid-mode actions reject these Agent facts.
Narrative consequences remain Agent-performed DM adjudications.
Use the relevant public map, check, dice, state, memory, and manifest tools;
use `combat_choice` only when an owned pending window already exists, and never
fabricate a window merely to store the ruling.
`combat_common_action` settles the action payment for Dash, Disengage, Dodge,
Help, Hide, Search, Influence, Study, Utilize/Use an Object, one ordinary object
interaction, and non-spell Ready without inventing their narrative result;
`combat_query(view="reactions")` exposes an eligible actor's pending reaction windows.
For a scene-defined skill use that consumes an action, `combat_check` accepts the
skill name as `ability` and one matching `action` payment. A 2014 improvised
action uses `action="improvise"`. The server derives the named skill from the
actor card and rejects caller-supplied proficiency or bonus values. The check
and action payment commit together even when the check fails.

`combat_join` queues an existing canonical campaign actor as a reinforcement.
By default it joins next round; an exact source-authored later boundary may be
passed as future `participant_config.join_round`. The queued actor remains
outside `combatants` until that round boundary, is omitted from player combat
views, and cannot act, be targeted, or participate in reaction geometry early.
At the boundary it is inserted by initiative without changing the actor whose
turn was already in progress.
Joining initiative ties require an explicit `tie_breaker`. Create likely scene
participants and their source-bound cards during lobby import, not during an
active encounter.
An Agent-owned encounter driver may provide a unique `tie_breaker` before the
server rolls initiative. It is inert unless a tie occurs and represents only
the Agent's DM ordering, never a caller-supplied initiative. Safe pre-commit
Agent ruling boundaries rewind only their unpersisted random suffix, so a retry
at the same campaign revision reproduces the original roll after the missing
ruling is supplied.
The full-playthrough encounter driver has no implicit combat AI.
`--agent-target-priority-json` declares a same-side actor set and an exact
complete order of opposing participants; it can refine but never contradict an
authored source priority. `--agent-spell-priority-json` declares ordered
supported structured spells, target policy, and lowest-available-slot policy.
`--agent-weapon-priority-json` declares ordered weapon/mode pairs and an
optional compatible structured Multiattack. Each declaration retains a bounded
Agent decision and reason. A turn with no applicable source action or explicit
Agent action policy returns a pre-commit `pending_ruling`; actor index,
inventory order, creature/class name, and hard-coded spell preferences are not
valid fallbacks.
`combat_end` accepts an optional structured outcome with a bounded public
`summary` and a status of victory, defeat, withdrawal, surrender, truce, or
interrupted.
It persists that outcome on the final encounter audit. Unsettled living actors
at 0 HP are returned in `post_combat_recovery`; Play continues their death saves
or a conscious assisting actor stabilizes them with an explicit reason.
Source-directed retreat orchestration must distinguish an exact defeated-actor
trigger, a defeated-count threshold, cumulative damage actually applied, and a
server-settled critical hit. Preserve authored alternatives such as “24 damage
or one critical hit” as parallel OR triggers. For damage, accumulate
`damage.applied_amount` after resistance, immunity, vulnerability, and temporary
hit points; never use the input roll, caller arithmetic, or a current-HP
difference. Include structured spell damage such as each Magic Missile dart.
Recover the accumulator and critical-hit evidence from the public combat log
when resuming an interrupted encounter. A satisfied trigger makes only the
designated retreating actor attempt departure on its own turn; it does not end
the encounter, skip other turns, or resolve other living hostiles. Count a
retreater as resolved only after the public combat-map departure commits, and
add it to a later encounter only from that recorded departure.
For an authored surrender threshold, the driver must verify that the named actor
is alive at or below the threshold and every required no-escape predicate is
true. It ends before another attack and preserves the exact surrender excerpt.
Medicine stabilization is not a generic narrative check. Call
`combat_check(kind="stabilize", ability="wisdom", target_id=...)`; the server
requires the acting turn, recorded adjacency within 5 feet, and a living target
at 0 HP, then derives DC 10 and the actor card's Medicine modifier. It consumes
the main action whether the check succeeds or fails. Success atomically clears
death-save successes/failures and records Stable without healing; failure leaves
the target unchanged. A client must not supply a replacement DC, proficiency,
bonus, or manual condition patch.
Death saves are discovered separately from ordinary actions. At the start of the
current combatant's turn, `combat_query(view="available_actions", actor_id=...)`
returns only `death_save` when the canonical card is at 0 HP, the encounter grants
death saves, and the actor is neither Dead nor Stable. Call
`combat_check(kind="death_save")`; `ability`, target, client bonus, proficiency,
DC, and `rule_facts` are absent. A successful write marks the turn's save used and
returns the natural roll, tally, and outcome. Do not infer eligibility from a
nonexistent Dying condition. If a rescuer must move into range, resolve every
opportunity-reaction window from that movement before attempting stabilization.
The generic Ready action rejects spell payloads. Use the dedicated spell-ready
transaction instead:

1. `combat_ready(action="ready_spell")` accepts only a spell with an Action casting time. It pays
   the action and the spell slot or other casting resource immediately, replaces
   any prior concentration, and records an explicit perceivable trigger.
2. `combat_ready(action="trigger_spell")` is a DM/owner confirmation that the trigger has
   occurred and opens an owned reaction window. It does not infer trigger truth
   from prose.
3. `combat_ready(action="resolve_spell")` either releases the spell and consumes the
   caster's reaction, or declines that occurrence without spending the reaction.
   Declining rearms the same held spell for a later occurrence before expiry.

For a generic non-spell Ready action, use `combat_common_action(action="ready")`,
then let the Agent acting as DM confirm the trigger with
`combat_ready(action="trigger_action")`
and let the actor release or decline with `combat_ready(action="resolve_action")`. Releasing pays
the reaction and returns `pending_ruling`; it never fabricates the declared
effect.

The held spell always requires concentration, including a spell that normally
does not. Concentration loss, the start of the caster's next turn, or combat end
dissipates it without effect. When a normally-concentration spell is released,
its original concentration duration continues; otherwise the holding effect
ends. Release returns `pending_ruling`, because targeting, spell attacks, saves,
damage, areas, and narrative consequences still require the relevant settlement
tools and Agent-performed DM decisions. Reaction spells and activities otherwise require an
owned pending reaction window; they cannot be invoked merely because it is not
the actor's turn.
Numeric attack modifiers and damage formulas supplied by a client are ignored;
they must come from `derived.inventory.weapon_attacks` or an explicit
Agent-performed DM ruling.

Every combat write should provide `expected_revision` and `idempotency_key`.
`combat_preflight_attack` never mutates; `combat_resolve_attack`,
`combat_movement`, `combat_end_turn`, `combat_check`, `combat_use_activity`,
`combat_ready`,
`combat_concentration_check`, and `combat_hp_change` commit one
atomic mutation group. Sensitive combat writes require both
`expected_revision` and `idempotency_key`. Player views are filtered by campaign
membership; keeper logs, target mechanics, and rulings are not exposed to
players.
`campaign_query(view="get" | "list")` is also audience-filtered: a non-DM sees
only the whitelisted party/game-phase/clock state, audience-visible world
effects, and the already-redacted combat projection. It cannot be used as a raw
state back door around domain-specific visibility checks.

For `combat_hp_change(action=heal)`, `payload.amount` is the rolled or otherwise
resolved base healing. Spell healing additionally carries `source_actor_id`,
`spell_id`, and `spell_level`. The server verifies the spell on that source card,
rejects illegal cast levels, applies source-linked modifiers such as 2014
Disciple of Life, clamps once to maximum HP, and returns the base, bonus, effective,
and actually restored amounts separately.

Treat an `idempotency_key` as unique for the whole campaign, not merely one
tool name. A successful state mutation is recorded in the same transaction as
its revision group. If a process stops before its rich response receipt is
stored, retry returns the safe opaque replay `{status: committed,
response_recovery: read_current_state}` rather than applying the mutation
again; then read the relevant campaign, character, or combat state.

`character_action(action="use_activity")` and `combat_use_activity` work with
normalized `content.activities`, `content.features`, and `content.feats` cards.
Locked standard activities use their registered implementation and normal
resource/action payment. A custom mechanical activity with no plan returns
`semantic_solution.status="content_authoring_required"` before its custom
outcome is paid or executed. The DM Agent queries the exact card, compiles one
source-bound generic plan in the current phase, and retries through public tools;
the engine validates bindings, spends resources, rolls, and commits mutations.
A module-only narrative procedure may remain an explicit Agent/DM ruling backed
by its exact scene evidence, but it must not become a creature-specific action,
state field, or save/damage shortcut.
Do not seed a second `sheet.resources` counter for a feature whose structured
card has an empty `resource_key`: that card-local `uses` counter is
authoritative. `character_state_change(action="resource_sync")` recomputes
declared card-local level/ability scaling but never guesses that a similarly
labelled top-level resource is a removable duplicate.
The canonical 2014 and 2024 Fighter Action Surge ids are narrow Core exceptions:
`combat_use_activity` consumes the edition-bound card use and atomically grants one current-turn
`extra_action`. It rejects off-turn or twice-on-one-turn activation, and any
unused extra action is cleared when the actor's next turn begins. Its Core receipt
is `dnd5e.core.activity.action_surge`; clients must not edit the turn budget.

The exact 2014 and 2024 Fighter Second Wind cards share one engine-owned base
activation: `combat_use_activity` pays the Bonus Action and card use, rolls
`1d10 + Fighter level`, applies clamped healing, and returns a Core receipt.
Their use counts and Short/Long Rest recovery remain edition-bound card state;
do not copy the 2014 counter onto a 2024 Fighter. Later 2024 features such as
Tactical Mind and Tactical Shift are separate cards and are not proof that
their additional settlement is implemented.

The 2024 Channel Divinity card exposes two engine-owned options. Divine Spark
uses `declaration={option:"divine_spark",target_id,mode:"heal"|"damage",
damage_type?:"necrotic"|"radiant"}`. Combat derives visibility and 30-foot
range from the map; Play additionally requires the target revision plus
Agent-as-DM `can_see=true` and `within_30_ft=true`. The same atomic mutation
spends Channel Divinity, rolls the level-scaled d8s plus Wisdom, and either
heals or rolls the target's Constitution save for full/half damage. Turn Undead
uses one explicit perception entry for every living Undead within 30 feet. Its
2014 result is the Turned procedure; its 2024 result is Frightened plus
Incapacitated and ends on target damage or when the source dies or becomes
Incapacitated. A source-bound level 5 2024 Cleric may set
`sear_undead=true`; the engine rolls one shared Wisdom-modifier number of d8s,
damages only failed-save targets, and does not end the newly applied Turn.

Preserve Life also remains edition-bound. Submit all target allocations in one
call; the engine enforces the five-times-Cleric-level pool, 30-foot range, and
the half-maximum-HP ceiling. The 2014 card excludes Undead and Constructs. The
2024 Life Domain card does not contain that exclusion, so the engine must not
reintroduce it from 2014 memory. Never spend Channel Divinity first and then
patch target HP. The 2024 Rogue Cunning Strike family is not yet an executable
attack rider: do not reduce Sneak Attack dice, apply its conditions, or invent a
manual post-hit mutation until the generic attack-window implementation and
Core tests exist.

`combat_preflight_attack` and `combat_resolve_attack` accept
`multiattack_option_id` for the first attack of a structured Multiattack. The
engine pays the Action once, stores the remaining source-defined weapon/mode
entries in turn state, and rejects substitutions or excess attacks. For a melee
weapon with the Thrown property, `attack_mode` defaults to `melee`; send
`attack_mode: "ranged"` to use its thrown range. The selected mode also determines
whether melee-only modifiers apply. For a Two-Handed or Versatile weapon, the
attack plan also records `weapon_grip`. Send `weapon_grip: "two_handed"` to
select a printed Versatile alternate; the engine uses that complete alternate
damage expression exactly once and rejects the mode while a shield is wielded.
Omit it for the legal default (`two_handed` for a Two-Handed weapon, otherwise
`one_handed`). Never add a Versatile die to ordinary damage or repeat additional
dice already folded into the printed alternate.

Standard monster activities remain engine-owned. `Aggressive` is paid through
`combat_use_activity`; spend only its separate grant with
`combat_movement(action="move", payload.movement_mode="aggressive")`, which
must move every submitted path segment toward the recorded visible hostile.
`Battle Cry (1/Day)` consumes its card-local daily use and main action. Its
declaration uses `targets=[{actor_id,can_hear,reason}]`; the Agent supplies the
current hearing fact, while the engine enforces range, Deafened, duration,
attack advantage, and the source's optional bonus-action attack.
For engine-owned point-radius, self-line, and Wing Attack save areas,
`combat_use_activity.declaration` includes the exact `origin` or `endpoint`,
optional Wing Attack `destination`, and a complete
`target_contexts=[{target_id,cover}]` list for every living actor in the
geometric area. Cover is `none`, `half`, `three_quarters`, or `total`; the
runtime applies +0/+2/+5 to the Dexterity save and excludes Total Cover. The
Agent supplies this scene fact and must not calculate the numeric bonus.

Parsing a sentence is not proof that its transaction exists. False Appearance
remains `manual_ruling.kind="descriptive_passive"` because motionlessness and
identification are descriptive encounter facts. Legendary Resistance likewise
remains at the Agent boundary until the runtime owns a post-failure choice
window that can replace the failed save and spend the card use atomically.
Clients must not activate it after an already-applied save effect or treat a
parsed `N/Day` label as evidence that the override is implemented.

An Owner/DM may supply a current-scene Agent ruling in `action.context` for
terrain- or position-dependent attack facts. Relative cover uses
`cover.degree="half" | "three_quarters" | "total"` plus an `agent_ruling`
containing a stable application id, `default_resolver="agent"`,
`ruling_kind="source_or_scene_fact"`, decision, reason, the exact active-scene
`source_ref`, and an excerpt verified against that chunk. Cover is specific to
the declared attacker, target, and attack mode. The server rejects player-supplied
tactical context, stale or foreign citations, missing Agent evidence, arbitrary
numeric cover bonuses, and unknown degrees. The D&D engine derives +2 AC, +5 AC,
or the total-cover targeting prohibition; callers must never calculate or inject
those numbers.

A successful attack may return `status: pending_reaction` with no damage. The
engine has committed its attack roll, Action/attack payment, ammunition use, Help
consumption, and hidden-attacker reveal, while blocking further actions. The
target reads its owned candidate list through `combat_query(view="reactions")`
and calls `combat_choice(action="resolve_defense")` with that choice id and either
a listed reaction activity id or `decline`. The resolver spends the Reaction only
when used, re-evaluates the stored roll against the structured AC bonus, then
rolls/applies damage at most once. Generic `combat_choice(action="resolve")`
rejects this window. Non-DM reaction reads omit the stored attack plan and raw
mechanical internals. A source-bound Core `Shield` spell candidate additionally
returns `cast_levels`; select its spell id with an explicit `cast_level`. That one
mutation pays the Reaction and canonical casting resource, persists the +5 AC
effect with `turn_start: 1`, re-evaluates the stored attack, and never rolls
damage twice. An unavailable/unprepared spell, exhausted slot, incapacitated
caster, spent Reaction, or edition spell-per-turn conflict removes the candidate.
The attack-hit window does not represent Shield's separate `Magic Missile`
targeting trigger; clients must not synthesize one from prose. A source-bound Core
Magic Missile instead uses `combat_cast_spell(..., target_allocations=[...])`.
Allocations contain `target_id` and `darts`; their total is three at level 1 plus
one for each higher slot. Targets must be current combatants visible to the caster
and within 120 feet on the recorded grid. The cast pays the caster's action and
resource once, then opens an owned `magic_missile_targeted` reaction window for
each target with a legal Shield cast. Resolve each through
`combat_choice(action="resolve_defense")`; no dart is rolled until all such windows
are settled. Active or newly cast Shield negates every dart allocated to that
target. Remaining darts are rolled and applied as separate force-damage instances,
so concentration and 0-HP consequences are per dart. Never merge them into one
damage packet or manually patch HP.

For a structured area spell, provide one `target_contexts` entry, including
cover, for every non-Dead combatant whose recorded position lies in the declared
area. This complete set includes Stable, Unconscious, or otherwise living actors
at 0 HP; only the Dead condition removes a creature from enumeration. The server
derives the affected set from the map and rejects omitted or injected targets.

Core 2014 `Hypnotic Pattern` instead accepts exactly
`declaration={origin:{x,y},cube:{min:{x,y},max:{x,y}}}`. On the normal 5-foot
grid, `min` and `max` are inclusive bounds for exactly 6 by 6 cells and
`origin` is a boundary cell on one cube face. The Runtime validates the
120-foot point-of-origin range and enumerates all living positioned combatants;
the caller cannot inject or omit targets. A Blinded creature does not see the
pattern. Charmed immunity prevents the entire effect. All other creatures make
server-side Wisdom saves. Failed targets receive source-owned Charmed and
Incapacitated conditions plus speed 0 for up to ten rounds, linked to the
caster's exact concentration effect. Positive damage ends a target link;
concentration loss ends all remaining links. An adjacent creature can spend
its action through
`combat_common_action(action="shake_hypnotic_pattern", target_id=...)` to end
all active Hypnotic Pattern effects on that target. These are standard-rule
transactions, not generic Agent rulings.

A source-bound structured spell attack uses a two-stage contract. Call
`combat_cast_spell` once without a target declaration. A successful cast pays its
action and casting resource once and returns `status="pending_resolution"`, an
opaque `resolution_id`, `attack_count`, and `remaining_attacks`. For each attack,
call `combat_resolve_attack` with the chosen `target_id` and
`action={"spell_resolution_id": resolution_id}` using the latest campaign
revision. The engine derives the spell attack bonus, range, damage, and critical
dice from the source-bound card; the per-attack calls do not pay another action or
slot. A pending Shield defense is resolved through its actor-owned reaction window
before the next attack. Pending spell attacks block `combat_end_turn` and
`combat_end`; both become legal only after `remaining_attacks` reaches zero. Never
model the attacks as weapon actions, repeat the cast, combine damage packets, or
patch HP.

`character_query(view="rest")` preflights v2-card Short Rest recovery. Before
the atomic Short Rest write,
call `character_query(view="rest")` for every member with the exact
`hit_dice_spends` keys/counts, optional `arcane_recovery` or
`natural_recovery` allocation, optional 2024
`sorcerous_restoration_points`, and, for each eligible 2014 Song of Rest recipient,
`song_of_rest_source_actor_id`. This is a read-only authoritative preflight: it
validates remaining dice, the actor's current card, the service-owned day
ordinal derived from game-time ticks, Arcane
Recovery allowance/usage, Natural Recovery's declared meditation and
once-per-Long-Rest use, automatic 2014 level-20 Sorcerous Restoration, the
2024 level-5+ declared half-class-level recovery and once-per-Long-Rest use, the source
Bard's campaign membership, conscious state, source-bound feature,
level-scaled die, and rule semantic validation.
Only after every member reports `ready=true` may orchestration call one
`campaign_change(action="party_rest",
payload={rest_type:"short_rest",members,duration_minutes})`. The runtime—not the
caller—advances time and effects once, settles every member atomically, rolls each requested Hit Die,
applies Constitution and the edition's minimum, rolls one Song of Rest die for
each hearing creature that spent at least one Hit Die, and returns both roll
audits. The source Bard must participate in the same Short Rest; callers must
not add the bonus once per spent die or patch HP after the rest.
A full-playthrough driver requires one explicit stable Short Rest occurrence id
and uses it for that rest's party-rest, knowledge, continuity, and
manifest-sync idempotency keys. Complete normalized member choices, duration,
and reason remain request payload and must match on an exact retry. A later rest
must use a distinct occurrence id even when its choices and reason are
identical; a payload-derived, run-wide constant, or actor-only key would
incorrectly replay an earlier rest.

2014 Long Rest may require an explicit
`hit_dice_recovery` allocation across multiclass pools. A 2024 Long Rest restores
all expended Hit Dice; exhaustion falls by one. In 2014 exhaustion recovery needs
the Agent-as-DM-confirmed `food_and_drink=true` condition, derived from current
state rather than requested from a separate human by default. Timed card effects
advance at the ending actor's turn; any narrative rest consequence remains an
Agent-performed DM ruling. A
resource marked `recovers_on: short_rest` also recovers on a Long Rest; the marker
means the earliest rest that restores it, not that a longer rest fails to do so.
When a 2014 prepared caster changes its complete list, record light preparation
activity equal to at least the sum of all selected spell levels. A bare
240-minute Trance schedule contains no such time and must be extended before it
can carry a changed list.

A Stable creature at 0 HP cannot benefit from a rest. When the party can safely
wait for the automatic recovery, call
`campaign_change(action="stable_recovery")` once for the complete simultaneous
member set. The engine rolls each actor's `1d4` hours, advances the authoritative
campaign timeline by the longest concurrent wait, restores exactly 1 HP, clears
Stable and Unconscious, preserves unrelated conditions such as Prone, and stores
the Core receipts atomically. Do not manually set HP, choose the recovery
duration, or invoke a per-character recovery clock. A recovered, conscious actor
above 0 HP may then use `character_state_change(action="stand")`; this narrowly
clears Prone under the Core movement boundary and does not permit arbitrary
condition edits.

Except for source-bound spell workflows such as Core Fly and Magic Missile,
`character_action(action="cast_spell")` and `combat_cast_spell` settle only timing, casting
resources, concentration, and recorded components. Generic spells return
`pending_ruling` for targets and effects. Cantrips and rituals cannot be upcast; a ritual cannot
complete in active combat. Costly or consumed material components require
`component_ruling.material_confirmed=true` before resources are spent. Pact Magic
uses the recorded `pact_magic.slot_level` and is counted as a slot expenditure.
A custom source-bound statblock spell whose component details were not present in
the reviewed card requires `component_ruling.source_components_confirmed=true`
before it pays an action, slot, or concentration. Confirm this only from an
explicit Agent-performed DM ruling or an active exact spell rule; the later
`pending_ruling`
still covers targets and effects.

The exact 2014 SRD Fly card is engine-owned. A noncombat cast supplies equal
`target_character_ids` and `willing_target_ids` in the `character_action`
payload. A combat cast supplies equal `target_ids` and `willing_target_ids` in
`declaration`; all targets must be controlled by the caller, alive, recorded in
the encounter, and within 5 feet. The Runtime validates one willing target at
3rd level plus one for every higher slot, pays the slot/action, starts the
caster's unique 10-minute concentration, applies a 60-foot flying speed to each
target, and binds those target effects to the exact concentration effect. A
later concentration replacement or failed concentration save ends the bound
effects. The Agent owns willingness, target choice, and descriptive movement;
it never supplies the speed, duration, target scaling, or dependency. Without
recorded elevation, the map must not infer that a token is aloft or fabricate a
fall.
When a hidden caster has perceivable components,
`combat_cast_spell` may instead return the pre-commit missing fact
`spell_casting_perception`. The encounter driver must preserve that boundary and
accept an explicit `--agent-casting-perception-json` observer matrix. Each entry
names the observer, a boolean `perceived` result, and its reason; the enclosing
declaration retains the Agent decision and reasoning in the regression report.
Absence of a recorded wall, silence, or total-cover fact is not proof of
perception. The driver must not generate the matrix from missing evidence.

`module_set_progress` requires the current `expected_state_version` for that
scene/scope row (`0` for its first write) and a fresh idempotency key.
When a module revision can no longer remap a progressed scene,
`module_draft(action="start")` or a later `edit(operation="advance")` exposes
`diff.progress_impact[action="needs_dm_review"]` with a structured
`ruling_requirement`. Its default resolver is the Agent because selecting the
best source-supported remap or retirement is a DM continuity decision. At
`content_pack(action="activate", kind="module")`, pass each settled decision as
the old `from_scene_id`, the finalized candidate `to_scene_key`, and a nonempty
reason. Draft-local target scene ids are not portable. If old/new source
evidence is missing or contradictory, retain the external source-review
classification instead. The aggregate validation `ruling_requirements` must
preserve the same records.

Every campaign owns one monotonic `state.game_time.elapsed_ticks` chronology;
one tick is six seconds. `state.world_time` is only an optional branch-local
calendar projection anchored to that chronology. `campaign_change(action="clock_set")`
anchors its day, hour, and minute without resetting ticks.
`campaign_change(action="clock_advance")` advances an explicit
`minute`, `hour`, `day`, `round`, or `encounter` count. Narrative-time advances
update `state.game_time`, update an anchored `state.world_time`, and settle effect durations by the
actual elapsed interval across all campaign actors and `state.world_effects` as
one atomic group. Thus 60 minutes, one hour, and two consecutive 30-minute
advances have the same result for both round- and hour-duration effects. Any
subminute/sub-hour/sub-day remainder is service-owned. A `round` advances one
tick; `encounter` advances only encounter-bound lifecycle state because an
encounter has no fixed elapsed duration. Game time can advance before a calendar
is anchored. The calendar cannot be set during active combat, and conversation
time is never elapsed campaign time. Once anchored, a different `clock_set`
instant is rejected; use `clock_advance`.
Every `minute`, `hour`, or `day` `clock_advance.payload` supplies the canonical
`expected_elapsed_ticks` target. An older caller may supply only
`expected_world_time={day,hour,minute,elapsed_minutes}` while a calendar is
anchored; new full-playthrough calls use ticks and may also supply those four
calendar fields as a projection guard. Each supplied target must equal the
server-computed result; otherwise the entire mutation is rejected before game
time, actor effects, or world effects change. A campaign-specific travel-day
index is not an elapsed-day
count: first project all source-defined rest days and calendar offsets, then
derive the duration from the current public clock and bind the resulting target.
The public full-playthrough driver additionally accepts branch-current narrative
preconditions for `advance-time`: `--prerequisite-scene-id` and
`--prerequisite-outcome-id` must identify an outcome already present in public
scene progress, while repeated `--prerequisite-actor-id` values must resolve to
actors in the same campaign. These are orchestration guards, not replacements for
the atomic clock transaction. A missing prerequisite is rejected before any
clock mutation. Prepare every actor required by the destination event before the
time write, then name those actors as prerequisites; actor preparation after time
advancement recreates a cross-tool partial-failure window.

The state mutation group and the exact public clock response are persisted in
the same database transaction. When an exact-target `advance-time` committed
but delivery of its response
or following continuity write was interrupted, retry the same occurrence and
payload. If `state.game_time.elapsed_ticks` already equals the supplied
`--time-expected-after-ticks`, the driver may call the same idempotent
`clock_advance` request once to recover its atomically stored original response;
it reconstructs the pre-advance game time from that exact target and duration
and binds continuity to the recovered clock
mutation's original campaign revision. Older mutation groups without an exact
response may be recovered only by matching their public request hash, branch,
entity revisions, and exact current target. An
intervening campaign mutation makes the continuity revision guard fail. Never
change the occurrence id, omit the exact target, accept a merely similar clock,
or patch the clock/continuity records directly.

Every other public operation that moves narrative time—Short/Long party rest,
Stable recovery, completed out-of-combat spell/ritual casting, and source-bound
spellbook copying—uses the same atomic replay
boundary. Its clock, actor/world effects, character changes, random-stream
position where applicable, entity revisions, and exact response commit or roll
back together.

`campaign_change(action="party_rest")` is the full-playthrough write for both
Short and Long Rests. Its
`members` array contains `character_id`, that actor's `expected_revision`, and
rest-type-specific choices. Long Rest members may supply
`prepared_spell_ids`, `hit_dice_recovery`, and `food_and_drink`; Short Rest
members may supply Hit Dice spends, Arcane/Natural Recovery, Song of Rest,
attunement, and activity fields. `duration_minutes` defaults to 480 for a Long
Rest and must be at least 60 for a Short Rest. The MCP advances time and
all timed actor/world effects once, then settles the named actors and records
their completion tick, exact dice receipt, entity revisions, and replay
response in the same transaction. It rejects 0-HP/dead starters and a second
Long Rest benefit less than 14,400 ticks (24 hours) after the previous one. Do not split a
Short Rest into a clock advance followed by individual actor writes.

Every completed combat or chase round advances the same tick stream. Ten rounds
accumulate to one minute even when split across multiple encounters. At a crossed
minute boundary, the turn transaction advances all campaign actors'
elapsed-time effects (including noncombatants), and world effects in the turn
transaction. Replaying an older turn response projects that stored encounter
revision rather than substituting the latest live combat.

If an atomic party rest succeeds but response delivery or the following
continuity checkpoint fails,
retry the exact request. When changed actor revisions make that retry conflict,
an owner or DM may read the stored campaign mutation through
`state_revision(action="receipt", payload={"idempotency_key": ...})`. Treat this
as recovery evidence only when its branch and before/after entity-revision
evidence match the current campaign and actors, and its request hash matches the
exact pre-rest request reconstructed from those before revisions and all member
choices. Its member ids, duration, campaign revision, canonical game time, and
optional calendar projection must also exactly match current public state. Each
member's `rest_history` must match the implied start/completion ticks, and its
prepared-spell receipt must match the
authoritative cards. For a random-capable Short Rest, require the stored exact
response, each requested Hit Die roll, and its matching random-stream receipt;
current HP alone is not enough to reconstruct dice. Then add only the missing occurrence-scoped continuity and
checkpoint writes; do not repeat the rest or patch storage.
The continuity commit uses the atomic party-rest response's campaign revision,
not a fresh later revision; any unrelated write between the rest and continuity
therefore conflicts instead of receiving a falsely attached rest narrative.
For a Short or Long Rest that follows a sourced outcome, the full-playthrough
driver applies the same scene/outcome and actor prerequisite guards before the
rest write. `--rest-expected-start-clock-json` binds the exact current
day/hour/minute/elapsed-minute state and rejects a stale, skipped, or
cross-branch sequence before `clock_advance` or `party_rest` can mutate state.

Use `campaign_change(action="effect_add" | "effect_remove")` for a structured
effect on a campaign, scene, location, or object. Each effect has a stable id,
source, target, active flag, duration period/remaining count, and visibility
`public|party|dm`. Timed effects bind to `state.game_time` and therefore do not
require an anchored calendar. Do not
store a timed Light, hazard, ward, weather effect, or similar object state only
inside arbitrary scene-progress JSON, because that bypasses the duration engine.

## Player-safe module reads

`module_query(view="scene" | "index")` and `module_search` accept `principal_id`.
DM/owner reads may include restricted content; player reads are filtered to `public` or
`group` visibility and restricted content is replaced with a redaction marker. A
player cannot select another player or group scope merely by knowing its ID.
