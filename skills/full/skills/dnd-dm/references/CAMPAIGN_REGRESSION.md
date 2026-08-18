# Real Campaign Rehearsal and Corpus Regression

Use this workflow to rehearse one imported adventure or regression-test a corpus
without bypassing the same MCP exposure available to a live Agent. The run is a
gameplay audit, not only a parser benchmark. Never import server modules, read the
database directly, raw-patch actor sheets, or infer missing module facts.

## Corpus inventory and coverage matrix

Before creating campaigns, discover the corpus from current public Pack
catalogs, every configured `.sagasmith-pack` archive, declared fixture/import
roots, and modules installed or active through the public MCP facade. Inspect
package kind, descriptor checksum, source checksum, finalization/readiness, and
activation state. Scan configured raw adventure roots too, but classify each
source from evidence; a PDF extension alone does not make an adventure runnable.

Write a machine-readable inventory with one record for every discovered
candidate. Each record is either a runnable coverage unit or an exclusion with
`reason_code`, source/checksum, and evidence. Distinguish companion material,
player sheets, maps, rule/monster supplements, synthetic mechanism fixtures,
obsolete package transports, cache duplicates, system mismatch, and
source-only drafts lacking a finalized current Pack. Never use a handwritten
module whitelist: a newly discovered candidate must become runnable or an
explicit pending/excluded record, never disappear from the report.

From that inventory, generate a source-backed matrix across module/campaign
line, scene or chapter, key mechanism, `grid|agent` positioning, `dm|player`
audience, and `normal|restore` path. Each runnable module must reach at least one
legal complete ending. Cover mutually exclusive alternatives with focused
source-cited scenarios rather than a Cartesian product; a companion volume need
not invent an independent ending. Store route decisions and module-specific
audit evidence with the edited Pack or its regression fixture, not in Core,
D&D, MCP, or a generic driver heuristic.

## Per-campaign gate

Run every step through one campaign-bound MCP session/exposure at a time.

1. Cold-start each DM and player session from exactly the six native core tools:
   `skill_query`, `campaign_query`, `exposure`, `game_phase`,
   `server_capabilities`, and `storage_status`. Use Skill guidance plus
   `exposure(open/search/set)`, consume every real `tools/list_changed`, refresh
   `tools/list`, and call only tools present in that session's native list. Do
   not call an unexposed facade, emulate a result, or use an alias fallback.
   Issue one short capability phrase or exact tool id per exposure search; a
   concatenated list of tool names plus narrative text is an invalid discovery
   probe. After `set` and `tools/list_changed`, call the newly listed native tool
   itself. Core `campaign_query` must never proxy a `character_query`,
   `combat_query`, or mutation by carrying synthetic `tool`/`action` fields.
   Open once for the campaign/principal binding; after phase, restore, checkout,
   undo, or redo, keep that binding and use `exposure(search/set)` to load the
   needed current-phase tools. Record native tool, phase, exposure, and
   `host_context_binding` timelines. A player session must prove that DM-only
   tools cannot be loaded and that module, continuity, and combat projections
   are player-safe.

   Never issue `game_phase`, `combat_start`, `combat_end`, restore, checkout,
   undo, or redo in the same parallel tool batch as an `exposure(set)` built
   from the old native list. Wait for the authoritative transition, consume its
   `tools/list_changed`, refresh `tools/list`, then search and set the next
   phase's tools. Parallelizing the transition with stale exposure is a host
   ordering bug, not a recoverable discovery shortcut.

   Before campaign creation, read the edition and selected advancement mode
   from the discovered inventory unit, current Pack descriptor, or source
   manifest. Pass both explicitly to `campaign_create`; never accept tool
   defaults, silently mix 2014 and 2024, or replace source-selected XP with
   milestone advancement. The transcript must retain the source-declared
   profile and successful create arguments so the corpus report can reject an
   omitted or mismatched value.

   In `lobby`, verify storage, server capabilities, that campaign edition, the
   locked Core fingerprint, and the active module revision. Complete the staged
   module import and explicitly resolve any warning gate before activation. Give
   every parser behavior change a new parser version before refreshing. A refresh
   may enter `lobby`, but on any stage/inspect/validate/ingest/activate failure it
   must restore the phase that was exposed on entry. Its stage idempotency identity
   must include the logical source key, active parent module id, actual source
   content hash, and resolved title. An exact retry reuses the staged job; changed
   content or a later active parent creates a new revision instead of colliding
   with the earlier refresh.

   For the current module authoring facade, retain `result.job_id` and
   `result.module_id` from `module_draft(start)`; never restart or guess after a
   host persists the large inspection payload. After evidence review and Pack
   edits, finalize with
   `payload={job_id, pack_id, version, confirmation:{confirmed:true,note:...}}`
   and a
   top-level `idempotency_key`. `pack_id` is the stable Pack identity chosen by
   the Agent at this trust boundary, not a package-edit field. Import the
   returned archive through `content_pack(import, kind="module")`. For a revision
   of an active Pack, require an explicit greater version and `skipped=false`;
   otherwise stop on the identity/version conflict and do not activate the old
   module. Activate the module id returned by that import rather than the editable draft id.

   On every fresh process that still has source review, opposition hydration,
   directly proven missing/corrupted ending source, or another Pack-authoring obligation, stay in or return to
   `lobby`, load `module_draft`, and call `module_draft(action="get")` with no
   payload before creating actors, starting another draft, or entering Play.
   The result is newest-first: resume the matching unfinished job and preserve
   its job/module/review ids. Start a new draft only when the public list proves
   no matching resumable job exists or the supported lifecycle explicitly
   requires a new version of a finalized Pack.
   A package-view read does not enumerate content reviews stored on the editable
   module. Verify a successful content edit with `module_query(view="content")`
   on the retained module id; never interpret an empty package projection as a
   lost edit or permission to start a duplicate job.

   After the finalized Pack is imported and its returned module id is active,
   initialize `playthrough_manifest` with schema version 2. Put the full object
   at `payload.manifest`; keep `expected_revision`, `branch_id`, and
   `idempotency_key` at the tool-call top level. Use the active imported module
   id, not the Pack id or editable draft id. The complete empty Lobby shape is:

   ```json
   {
     "schema_version": 2,
     "run_id": "<run id>",
     "campaign_line_id": "<inventory campaign-line id>",
     "module_ids": ["<active imported module id>"],
     "status": "lobby",
     "source_refs": ["<exact validated module source reference>"],
     "current": {"module_id": "", "chapter_id": "", "chapter_title": "", "scene_id": "", "scene_title": "", "objective": ""},
     "traversal": {"reachable_scene_ids": [], "visited_scene_ids": [], "excluded_scenes": [], "branch_decisions": []},
     "party": {"party_size_status": "source_confirmed", "recommended_minimum": "<source integer>", "recommended_maximum": "<source integer>", "selected_size": "<positive Agent selection>", "party_size_review": {}, "use_pregenerated_first": true, "members": [], "replacements": []},
     "npcs": [],
     "quests": [],
     "clues": [],
     "world_state": {},
     "snapshot_dag": {"active_branch_id": "", "head_snapshot_id": "", "nodes": []},
     "random_stream": {"algorithm": "", "seed_fingerprint": "", "position": 0},
     "ending": {"status": "pending", "conditions": [], "achieved_condition_id": "", "verification": []},
     "review_blocks": []
   }
   ```

   Replace quoted integer placeholders with JSON integers. Recommended minimum
   and maximum are advisory source facts: they never block a positive explicit
   `selected_size`, even when the selection is outside that range. Require only
   at least one active PC. `selected_size` records the initial plan, not a
   permanent invariant: the active party may grow or shrink during the campaign.
   A top-level source
   reference uses `purpose`, managed `asset_path`, `asset_sha256`, exact
   `page_start`/`page_end`, ordered `heading_path`, service-owned
   `content_sha256`, active `module_id`, required resolved `scene_id` and
   `chunk_id`, and exact `excerpt`. Resolve those identifiers from
   `module_search` followed by `module_expand` against the active imported Pack;
   inventory coordinates alone cannot replace the current chunk receipt. After
   every rejected manifest mutation, refresh with
   `campaign_query(view="resume")` and rebuild against the returned revision;
   never copy the revision number out of an error string.

   After establishing a non-empty active party, call
   `playthrough_manifest(action="replace")` with the complete manifest returned
   by `get`, adding one full `party.members` record per chosen PC. Each record
   carries `actor_id`, current `name`, `status="active"`, `source` (exactly
   `pregen`, `generated`, or `replacement`),
   `source_asset_path`, current `level`, `xp`, `hit_points`, `resources`,
   `wallet`, `equipment`, and `knowledge_scope_actor_id` equal to its
   `actor_id`. Preserve every unrelated manifest field and source reference.
   Then call `sync` without invented party fields and require the returned
   manifest to become `ready` with at least one active member
   before entering Play. `sync` only refreshes actors already registered in the
   manifest; `payload.party_actor_ids` is not a registration mechanism and must
   not be used. An empty manifest is not evidence that no campaign actors exist.
   Before any PC build, call `character_query(view="list")` and retain every
   campaign-bound PC instance id. When at least one suitable active PC already
   exists, do not build another merely to match a recommendation or the initial
   selected size; register the current active actors. A later join, departure,
   death, missing status, reserve move, or replacement changes the active count
   without invalidating the campaign. Do not create a bench/reserve PC solely to
   satisfy a historical count.

   For every repeatable driver mutation whose authored content may legitimately
   be identical later, supply a non-empty stable `--occurrence-id`. Reuse it only
   to retry that exact occurrence; use a new id for a later identical check,
   scene advance, event, stand, source-state initialization, time advance,
   Short/Long Rest, stable recovery, XP award, narrative-NPC creation,
   source-item transfer, explicit checkpoint, or manual manifest sync. Human
   summary/reason text and mutable request fields are payload, not occurrence
   identity. Actions with an explicit business
   id (`roll-id`, outcome, acquisition, spend, consumable-use, damage-event, or
   activity-event id) use that id instead.
2. Read `module_query(view="index")`. Visit every non-reference/non-overview
   scene through `module_query(view="scene")`; require readable content, valid PDF
   page ranges, and stable scene ids. After `module_search`, take the immutable
   chunk `source_ref` from `module_expand`, including the service-owned
   `content_sha256`; a missing reference is an import/exposure defect, not
   permission to calculate or invent a client-side hash. Exercise every available atlas location by
   writing scoped progress on a disposable branch. Do not invent topology for a
   scene without reviewed or explicit connections. A campaign may revisit the
   same scene after world, quest, party, or objective state changes. Give each
   `advance-scene` visit a stable `--occurrence-id`: an exact transport retry
   must reuse that id and the same target manifest, while a later visit receives
   a new id even if the payload is identical. A target-scene-only or
   target-manifest-derived key is invalid because hubs, towns, and headquarters
   are intentionally revisited and mutable payload is not occurrence identity.
   In a public full-playthrough run, use the driver's `read-scene` action when
   the indexed scene id is already known. It calls
   `module_query(view="scene", scope_id="dm")` directly and validates the returned
   id. Reserve `query-source` for locating an unknown chunk, then expand only the
   selected hits. Do not inflate `top_k` or repeat broad searches to reconstruct
   manifest evidence. Every manifest citation is exact-resolved on write against
   the same managed module revision. Preserve ordered heading paths, collapsing
   only adjacent parser duplicates; a later same-named section is a distinct path
   step. Manifest excerpts may select ordered source fragments, whereas event,
   memory, progress, and ruling excerpts must remain contiguous exact text.
   a scene that the exact scene query can return in one request.
3. Before creating or completing any PC, read the exact
   `dnd:full/skills/dnd-dm/references/CHAR_CREATION.md` asset and follow its
   executable bootstrap, ability-score, catalog-application, metadata-profile,
   and final audit sequence. `character_create_from(mode="build")` does not
   accept shorthand class/species/background ids: create the bootstrap actor,
   then use the returned campaign instance id with the dedicated public tools.
   Classify and import every module-supplied PC document before building seats.
   Fill every applicable party seat from those pregenerated PCs first, up to the
    positive initial party choice; only then build the
   remaining legal seats from active content catalog ids. A present applicable
   pregen may not be skipped for a generated optimization. Preserve each pregen's
   source reference and document checksum. If extraction cannot find a party-size
   range, search the complete normalized document, expand every plausible hit, and
   visually inspect the introduction and character-creation pages. A semantic
   search miss or unrelated numeral hit is not a source range. If the module is
   genuinely silent, stop the source-confirmed gate and have the SagaSmith Agent
   acting as DM record an explicit review before building any PC. The review
   must retain the reviewed module
   pages, search terms, exact fallback rule reference and checksum, selected
   count, and `represented_as_module_recommendation=false`. A completed review
   may use an exact enabled-Core design baseline, but it must not relabel that
   number as the module's recommendation; never silently default to four. Use a
   manifest `party_size_review` with `default_resolver="agent"` and
   `ruling_kind="source_or_scene_fact"` for this Agent-owned review; image/source
   evidence that still cannot be inspected remains an explicit external gate.
   Use a
   level appropriate to the adventure segment. Exhaust advancement follow-ups,
   prepared spells, features, derived-state re-reads, and a verified snapshot
   before returning to `play`.
   The same gate must verify complete class and background equipment, starting
   wallet, and background characteristics. If the enabled 2014 catalog exposes
   only a sample background, use the Core custom-background rule through the
   public content-apply path to create distinct legal backgrounds; do not either
   clone the sample across the whole party or import an inactive setting option.
   Treat cantrips as known level-zero spell cards, not prepared-list entries.
   The public party report must separately retain `cantrip_spell_ids`,
   `known_spell_ids`, `prepared_spell_ids`, and `spellbook_spell_ids`; verify the
   class cantrip count against the character's level and keep any source-granted
   species cantrip in addition to that class count. Never infer that cantrips are
   absent merely because the prepared list or Wizard spellbook contains only
   level-one-and-higher spells.
4. Prepare every important named NPC and every NPC/monster required by the
   selected encounter. When the module provides only a narrative identity and no
   combat statblock, use the public driver's `prepare-narrative-npc` path: cite
   the active module/scene/chunk/page/hash and an excerpt containing the exact
   name and assign the creation a stable `--occurrence-id`. Close or abort any
   active NPC conversation, return to `lobby`, consume `tools/list_changed`, and
   load `character_create_from` through the current exposure before creating the
   actor with `mode="narrative_npc"`. Verify `combat_eligible=false` plus the
   `narrative_only`/`source_bound` tags, register the actor in the manifest, then
   return to `play`, consume the new native list, and verify its checkpoint.
   The exact payload requires `campaign_id`, `name`, `role`, `summary`, the
   complete active `source_ref`, and `source_excerpt`; use the stable occurrence
   identity as the top-level `idempotency_key`. Do not add separate module/scene
   ids, `occurrence_id`, caller-owned status tags, or `expected_revision`.
   For a source-counted anonymous group, create one actor per actual instance.
   Set `--narrative-npc-source-identity` to the exact printed group label and a
   distinct stable `--narrative-npc-instance-key`. Use
   `<source identity> [<instance key>]` by default. If the Agent needs distinct
   proper names for real play, pass
   `--narrative-npc-identity-agent-ruling-json` with a settled Agent DM decision
   whose `assigned_name`, `source_identity`, and `instance_key` exactly match the
   request; require both `anonymous_source_instance` and
   `agent_named_source_instance`. This naming decision cannot change the printed
   count, ancestry, role, mechanics, or source evidence. Do not assign names
   merely for database uniqueness, and do not collapse several NPCs into one
   knowledge scope.
   A `prepare-statblock` failure at candidate lookup, visual review, validation,
   creation, or verification must restore both the entry branch and entry phase
   before surfacing the error. Re-read the public phase after a failed review;
   never leave the campaign in `lobby` and repair it out of band.
   Treat every rule-source discovery or statblock-preparation driver command as
   a campaign-state operation even when its requested content is read-only:
   while entered from `play`, the command temporarily performs
   `play -> lobby -> play`. Run at most one such command for the same
   `home + campaign_id` at a time. The public driver enforces this with a
   cross-process campaign phase lock covering entry inspection, both phase
   transitions, the requested operation, and failure recovery. Never bypass or
   remove that lock to parallelize same-campaign discovery. Commands for
   different campaigns may still run concurrently. After a batch, verify the
   authoritative phase, clock, branch, snapshot head, and campaign revision;
   a content-only batch must not change the clock or snapshot head.
   Such a card supports identity, notes, relationships, and ActorKnowledge; its
   default mechanical shell is not an authored statblock and must never enter
   combat. If a later encounter makes that same actor mechanical, use
   `prepare-rule-statblock` or `prepare-statblock` with
   `--replace-actor-id` to materialize the exact rule/reviewed statblock in
   place. Verify that Actor ID, name, summary, prior notes, and ActorKnowledge
   remain intact; do not create a duplicate combat identity. If the managed
   rule artifact exists but its source id is not in the run manifest, use the
   read-only `discover-rule-sources` action, which also returns retained
   rulebook import jobs, and then `discover-rule-chunks`; match `source_id` to
   the exact job before requesting layout-OCR recovery. The public driver may
   combine exact `--chunk-id` selections with a source-established
   `--source-page`: chunks remain the first text-layout attempt and the page is
   the bounded OCR fallback. A single-field ambiguity such as
   `statblock INT score is ambiguous` must enter this generic fallback just as a
   missing six-score table does. Standard rulebook Multiattack and other
   mechanical cards must be parsed and executed by the D&D engine; never supply
   `--agent-statblock-fill`. If the parser cannot structure the printed rule,
   add a generic engine implementation and source-backed test, relock the
   campaign's built-in Core pack explicitly, and retry. Never add a
   named-monster special case.
   In a native Agent session, resolve any missing source-backed opposition with
   the complete public-facade procedure in `OPPOSITION_HYDRATION.md`. Read that
   focused reference in full before choosing rule-source hydration, reviewed
   rulebook repair, or a new module Pack review. It owns the exact-id,
   localized-canonical-source, module-only review, preflight, and phase-return
   requirements; do not reconstruct the workflow from this larger section.
   When the scenario covers Agent-owned spell semantics, inspect the resulting
   preflight `ruling_spell_ids` and hydrated source cards instead of avoiding
   them. Choose one exact source-backed card. A standard card with a persisted
   `agent_ruling` clause must not use `content_solution`: first call the ordinary
   spell facade without a declaration to read its exact ruling contract, then
   resubmit the cast as `declaration={"agent_ruling": {...}}`, with that exact
   source excerpt and the Agent's bounded decision. Do not flatten the ruling
   fields into `declaration` or place the effect ruling in `component_ruling`.
   Require authoritative action/resource payment and
   `semantic_solution.status="agent_ruling_committed"`. For a statblock/innate
   spell, omit `signature_free_cast`; MCP consumes the recorded innate resource.
   A custom/imported card
   with no persisted plan instead uses `content_solution(compile)`, pays through
   the ordinary spell facade, and settles the returned bound plan through
   `combat_choice(execute_plan)`. MCP owns validation, random results, resources,
   revisions, and mechanical state. A parser-damaged spell name that produced
   no hydrated card remains a source-repair diagnostic and is never
   reconstructed from memory.
   A `pending_ruling` response only supplies the declaration contract and spends
   nothing; it is not a completed Combat action. Before submitting the corrected
   declaration, read `combat_query(status)`. If another actor is current, call
   `combat_end_turn` only for that returned actor with the latest revision, then
   query status again after every committed turn write. Never guess, cache, or
   count through the initiative sequence. Treat actor, branch, encounter, and
   spell ids as opaque exact values: copy them character-for-character from the
   latest successful native result and never reconstruct, shorten, or retype
   them from narration or memory. If a call reports that an actor does not
   belong to the campaign, immediately re-read `combat_query(status)` and
   `character_query(list)`, compare the complete returned ids, and retry only
   with an exact returned value; a one-character mismatch is an Agent input
   error, not an MCP identity conflict. Submit the declaration only when the
   selected caster is current and require `status="committed"`; do not end the
   encounter after a rejected or merely pending cast.
   On resume, before the first Combat mutation, compare the active encounter's
   immutable participants and source manifest with the still-unmet evidence.
   If Combat coverage and every remaining Combat-specific mechanism are already
   satisfied, an encounter left active by an interrupted regression process is
   no longer part of the route. Treat them as satisfied only when the audit has
   one bounded encounter containing the qualifying source-backed `combat_start`,
   at least one successful engine-owned attack/activity/spell execution before
   its `combat_end`, and `combat_query(view="render")` when rendering is required.
   Participants, a ready source manifest, coordinates, or `combat_start` alone do
   not prove execution. A qualifying active encounter at round 1 with no such
   action receipt is unfinished: resume it and execute the remaining Combat
   mechanisms. Only when the receipts already exist should you query status and
   immediately call `combat_end` with a truthful
   `outcome.status="interrupted"`; do not replay the completed encounter before
   returning to the first remaining Play or ending gap.
   Take participant ids from `combat_query(status)`, then load
   `character_query` and read each required actor individually with `view=get`;
   do not assume a host's bounded summary of the nested encounter exposed every
   hydrated card or `ruling_spell_ids` entry. If that encounter cannot
   qualify because it contains the wrong actor revision, lacks the required
   hydrated card, or used non-matching source evidence, end it through
   `combat_end` and rebuild the qualifying encounter once from current actors.
   Search and load the exact `combat_end` tool, then close immediately with a
   truthful structured `outcome.status="interrupted"` and a summary naming the
   nonqualifying evidence. `combat_end_turn` is a different tool: it only passes
   the current actor's turn and must not be repeated to simulate ending the
   encounter. Resolve a genuinely blocking pending window first, but do not
   play out otherwise irrelevant turns before this interrupted close.
   Do not spend later cycles resolving otherwise irrelevant turns merely to
   preserve an encounter that cannot satisfy the regression contract. Never
   replace participants inside active Combat or patch actor state directly.
   A corpus combat is not covered by starting an encounter containing only
   party PCs. Before `combat_start`, expand the exact encounter evidence,
   prepare and preflight every required source-backed combatant, and include at
   least one canonical opposing NPC/monster actor together with the party.
   Narrative-only actors are not combatants. The transcript must retain the
   participant manifest and source references so the regression can distinguish
   a real encounter from an empty or all-allied combat shell.
   When the card slot is structurally proven but one OCR cell or action line is
   damaged, render that exact page and compare its native/normalized/OCR text
   streams. A text-only Agent may persist an exact page+slot
   `ocr_corrections.abilities` or `ocr_corrections.text_replacements` entry in
   the book regression manifest. The new value must be present in staged page
   text; otherwise only an image-capable reviewer that actually inspected the
   checksum-bound render may use visual review. Never copy a corrected value
   from a similar SRD monster or model memory.
   Repeated decorative/narrative copies of a creature
   heading are valid when exactly one copy is immediately bound to a complete
   creature core. If OCR still cannot isolate the card but those exact indexed
   chunks form one complete, ordered, contiguous segment on that page, use
   `prepare-rule-statblock --agent-rule-statblock-review <normalized.md>` with
   every ordered `--chunk-id`, `--source-id`, `--source-page`, and
   `--review-observation`. The driver accepts several historical jobs only when
   all share one nonempty artifact name and checksum, then selects the
   lexicographically first job id and reports every equivalent id. If artifact
   identities differ, review them and pass the intended `--source-job-id`.
   The driver sends `review_mode="agent_text"`; the MCP rechecks page ownership,
   ordinal continuity, full evidence coverage, and absence of invented facts.
   It may repair only numeric-position OCR confusables (`l/I↔1`, `o↔0`) and a
   digit-bounded range separator (`f↔/`); it must still reject changed DCs,
   bonuses, dice, damage types, and rule terms. When a continuous PDF segment
   interleaves a preceding or
   adjacent creature column, submit
   `--agent-evidence-exclusions <exclusions.json>`. Each entry must name a
   selected `chunk_id`, quote one exact source substring, and give a reason.
   Exclude only the contaminating span, including a prefix when the target
   resumes later in the same chunk. The MCP requires an exact single match,
   rejects overlapping ranges, retains the source-chunk checksum plus exclusion
   offset/hash/reason, and still requires every nonexcluded source sentence in
   the normalized card. This lets a text-only Agent restore semantic statblock
   order without pretending the broken two-column reading order is authoritative.
   A gap, unexplained exclusion, missing retained source fact, or conflict
   remains blocked and cannot be filled from model memory. Never inspect the
   database or re-import a managed artifact from outside the configured roots.
   For encounter participants, use exact rule statblocks or reviewed module
   cards and retain all warnings. When a 2014 module candidate is blocked by damaged
   page layout, `prepare-statblock` must call
   `module_draft(action="edit", operation="statblock")` against its exact managed PDF
   page before any visual override. The server performs and corroborates OCR, so
   a text-only Agent can consume the returned text. If the response reports
   `requires_agent_fill=true`, the Agent must read the exact OCR draft and
   requirements, submit a fresh-key retry with `payload.agent_fill`, and wait
   for that retry's immutable review before actor creation. The regression
   driver surfaces those exact requirements as
   `--agent-statblock-fill` guidance. Only a failed or
   ambiguous recovery may enter the image-capable `--review-override` path. A
   2024 candidate must skip the 2014 OCR facade and use complete
   edition-matching indexed text or the image-capable review override; if neither
   is available, skip that module or keep its encounter blocked. A
   module candidate's parser output
   is transcription support, not final semantic authority. When one reviewed statblock must
   create several source-identical actors, create every actor separately with an
   idempotency identity scoped by the run, review, actor name, actor type, and
   source variant. Retrying one actor must recover that actor, while the next
   actor must not collide with the previous creation. A descriptive passive or
   action is an Agent-as-DM boundary only when it becomes relevant; it does not authorize
   replacing the creature or blocking unrelated automatic attacks. Before any prepared
   spellcaster enters combat, a printed `Spellcasting` entry must have parsed as
   structured spellcasting rather than a descriptive passive. Compare its
   source-printed ability, slot maxima, and exact spell-name set with the created
   card, and require the prepared spell ids to cover that executable set. OCR
   tokens such as a broken ordinal are an importer regression to fix and refresh,
   not permission to accept an empty spell list or patch the actor manually.
   A reviewed `selection_ready` spell must pass the same canonical definition
   validation during rule-pack compilation that actor hydration later uses;
   invalid duration/range/components, unknown fields, class lists, levels, or
   structured resolution block activation before the encounter is prepared.
   Apply the same completeness rule to actions: count every explicit
   `Melee/Ranged [Weapon|Spell] Attack:` marker in the normalized source and
   require it to belong to a parsed weapon action or an identified statblock
   spell action. A card with at least one working attack still fails when another
   source attack marker was swallowed into the preceding description. The text
   layout normalizer may repair only context-bounded punctuation and range OCR
   inside a generic action signature; any remaining mismatch must trigger the
   bounded OCR/review path. Never approve a partial card merely because
   `attack_count >= 1`, and never add a creature-name-specific parser exception.
   Before any prepared monster enters combat, compare every printed
   Multiattack with `derived.multiattack_options`. For reviewed standard
   rulebooks, the parser result is authoritative and `agent_fill_requirements`
   reports `parser_authoritative=true`; any missing composition is an engine
   implementation gap. For module-authored or homebrew cards, the parser remains
   transcription support only: have the Agent read the exact reviewed source
   and submit `module_draft(action="edit", operation="content")` with
   `payload.agent_fill.multiattack_options`. If a composition includes a
   special activity or unsupported module procedure, submit
   `resolution="agent_ruling"` without `options`; this preserves the custom
   action for Agent adjudication at selection time. If an exact managed source assigns the
   creature a complete numeric weapon action outside the base card, add
   `additional_actions` to the same fill. Each entry supplies only the action
   name, exact managed `source_ref`, exact action excerpt, and Agent reason.
   The server validates same-source/page evidence and lets the canonical parser
   derive all mechanics and the stable weapon id; Multiattack options may then
   cite that derived id. Do not use a free-text source, submit mechanical fields,
   or patch the created sheet. Unresolved on-hit prose must still be settled
   through the public Agent ruling path. A generic
   “N melee/ranged [weapon] attacks” composition (where “weapon” may be omitted
   in the source) is deterministic only when the actor card has exactly one
   compatible weapon for that mode. When multiple compatible weapons remain,
   the Agent performs the DM review from the exact statblock and current
   loadout; missing or conflicting source evidence remains external review.
   A retained standard-rule review never receives a later Agent semantic fill.
   If a newly enforced standard mechanic is missing, implement it in the engine,
   relock Core explicitly, and recreate the actor from the same checksum-bound
   transcription.
5. In `play`, select one source-printed non-combat check. Read the exact scene,
   preserve its ability/skill and DC, resolve it through `character_check`, and
   commit the event, stable facts, per-witness ActorKnowledge, and snapshot with
   one `memory_change(action="commit")`. A skill label belongs in the cited evidence; use
   `kind="check"` unless the tool contract explicitly defines another kind.
   Assign each check an explicit stable `--occurrence-id`. The run, scene, Scene
   Atlas location, check kind, ability/skill, actor, DC, proficiency,
   advantage/disadvantage, and exact source chunk are immutable retry payload,
   not identity. Separate rolls must never reuse progress, dice, continuity,
   knowledge, or manifest-sync keys even when every payload field is identical.
   When all participating characters are attempting one task and the outcome
   applies to the party as a whole, use
   `character_check(action="group")` (or the driver's
   `resolve-group-check`) with every actor id. Core rolls each actor using the
   canonical card and succeeds when at least half succeed. An individual
   failure must not be promoted to an automatic group failure. This procedure
   does not apply to surprise, which compares each hidden creature against
   each observer.
   If the 2014 source directly opposes two creatures' efforts, use
   `character_check(action="contest")` (or the driver's `resolve-contest`) with both actors and
   both abilities/skills. Never replace the contest with an invented fixed DC.
   The target and source roll modes are independent; a source instruction such
   as "the enemies make a check with advantage for the group" applies advantage
   only to that enemy side. Compare totals atomically, and preserve
   `tie_no_change` rather than declaring either side successful on a tie.
5a. Exercise connected Play dialogue through `npc_conversation`, with the Agent
   supplying `audience_facts` for every ingest and segment audience for every
   publication. Verify that the Director receives no private capsule, lease,
   raw proposal, intent, or basis-only content. Allow one unrelated Play write,
   then re-query and prove the conversation remains usable. Before a requested
   mechanic or any participant/scene/branch mutation, atomically `close` or
   `abort` the conversation and release every worker. Always do so before Chase,
   phase transition, or combat start. Then execute the public mechanic; if
   dialogue continues, wait for that mechanic to commit before opening a new
   conversation and ingesting the actual result as a new stimulus. For `open`,
   put every PC and NPC campaign runtime id together
   in the single `payload.participant_actor_ids` array and put
   `idempotency_key` in that same payload. At least one listed runtime must
   resolve to an NPC or monster. There are no `npc_actor_ids`, `npc_ids`,
   `actor_id`, or `npc_identity` aliases. Read
   `dnd:full/references/skill-groups/play/npc-conversation.md` for the exact
   ingest/publish/close shapes before the first conversation call. Cover both
   conversation -> mechanic -> conversation and
   conversation -> rejected combat -> close/abort -> combat.
   The latter is a controlled negative invariant probe. Open the authoritative
   conversation, but do not ingest, activate a worker, or close it before the
   probe. Submit an otherwise valid source-backed `combat_start` and require it
   to fail specifically because the conversation is active; an unrelated
   revision, participant, map, or coordinate failure is not evidence. The
   rejected call cannot mutate state, so it does not violate the normal
   close-before-mechanic rule. Then `get` and close/abort the conversation,
   release any worker, retry the same valid combat start at the refreshed
   revision, require success, and truthfully end that now-covered encounter as
   interrupted before returning to Play.
   Before opening the probe conversation, rebuild every participant and manifest
   actor id from a fresh `character_query(view="list")`; never copy an id from a
   failed request or narration. Construct the combat payload once and reuse it
   verbatim for the rejected and successful calls, changing only
   `expected_revision` and `idempotency_key`.
   On every fresh Host or Agent process that resumes an existing campaign in
   Play, load `npc_conversation` and call `action="list"` before the first
   authoritative Play mutation. If it returns an active public handle, call
   `get` with that returned id and either resume the workflow or explicitly
   `abort` it before starting a replacement dialogue, creating another NPC, or
   attempting a mechanic or phase transition. An empty process-local worker
   registry is not evidence that no authoritative conversation remains active.
5b. When the active route invokes the 2014 DMG chase rules, run
   `scripts.regression_chase` through the public stdio MCP exposure. Bind
   `chase(action="start")` to the exact expanded scene `source_ref`, excerpt, quarry,
   pursuers, and printed starting distance. Require `mode="theater_of_the_mind"`
   and the absence of a battle map. The Agent must explicitly rule the
   theater-of-the-mind starting distance, provide every participant's
   `turn_action`, `stand_from_prone`, and legal choice for each possible
   complication result, and state the current boolean visibility of every
   quarry. Never let the driver choose Dash, select the actor's numerically best
   skill, stand automatically, or assume a quarry remains visible. A printed
   contextual speed modifier, such as dragging a heavy sack, must be supplied
   as a signed `speed_adjustment_ft` with an exact excerpt inside the reviewed
   chase source. It applies only to the chase snapshot and must not patch the
   canonical character sheet. Let `chase(action="take_turn")` own initiative,
   distance, movement from the declared action, Dash counts, extra-Dash
   Constitution checks, chase exhaustion, Urban Chase Complications, damage,
   and the server random stream. A module
   transition such as a quarry ducking into a destination is legal only when
   the `close_transition` carries its own exact same-scene `source_ref` and
   `source_excerpt`; require its `summary` to equal that normalized excerpt,
   including when the transition is stored in a different chunk from the
   starting-distance evidence. Seal the
   completed chase and its manifest/world-state update with one checkpoint;
   never checkpoint each chase turn or replace the chase with a fabricated
   outcome event. A successful turn may itself return the authoritative chase
   as inactive with a terminal outcome such as `caught` or `escaped`; that is
   already the required end receipt and must not be followed by a redundant
   `chase(action="end")`. If the authoritative chase remains active, call
   `chase(action="end")` before `combat_start`. In either case, re-query and
   prove the chase inactive, consume any resulting native-list change, and load
   the needed Play/combat transition tools. Separately assert that starting
   Combat while a chase is active is rejected and that no state ever contains
   two active structured workflows.
6. Before combat, read the exact encounter scene and its location. Call
   `module_query(view="preflight")` with every source/DM-established group.
   The `participant_manifest` object has only `schema_version`, `groups`, and
   optional `notes`. Each group uses this exact public shape; party PCs belong
   in `combat_start.participant_ids`, not in a separate manifest field:

   ```json
   {
     "schema_version": 1,
     "groups": [{
       "key": "stable-source-key",
       "label": "Source group label",
       "role": "combatant",
       "required_count": 1,
       "actor_ids": ["canonical-campaign-actor-id"],
       "source_scene_id": "same-module-scene-id",
       "source_excerpt": "Exact normalized source substring"
     }]
   }
   ```

   `required_count` is the complete group count, not `len(actor_ids)`: derive it
   from an exact printed count, a persisted random-table roll, or an explicit
   branch-local DM composition fact, and prepare all required cards. Include other
   printed hostiles as initial, reinforcement, or optional groups, or record the
   scene-supported reason they are absent.
   `participant_manifest.source_excerpt` is encounter evidence; the actor's
   content review is mechanical statblock evidence. These are separate passages
   and need not match. A failed or stale participant manifest is not evidence of
   Pack corruption, and adding an identical statblock review cannot repair
   encounter prose. Re-read the route's exact managed encounter excerpt and the
   Pack copy of that same passage. If that Pack passage contains demonstrable
   mojibake, replacement characters, omissions, or reordered text absent from
   managed source, do not copy it merely because preflight accepts it. Return to
   Lobby, create an explicit new draft/version, repair that bounded scene,
   finalize/import/activate it, and then rebuild the participant manifest from
   the corrected scene. Keep the source-specific replacement and
   evidence with that Pack; do not add a book-specific parser heuristic or
   weaken the regression excerpt comparison.
   A still-active PC does not have to be forced into every encounter. When the
   current world state and Agent-as-DM adjudication establish that the PC remains
   elsewhere (for example, stable and unconscious at the keep), pass one
   `--agent-party-absence-json` entry with that `actor_id` and a concrete
   `ruling_reason`. The driver must verify that participants plus declared
   absences equal the live active manifest party. The absent actor remains in the
   party and snapshot, is not added to combat, and receives no encounter
   knowledge. Do not relabel absence as death, departure, or a player-owned
   choice merely to satisfy the driver.
   When the source says a group starts outside the fight and must climb, cross,
   arrive, or otherwise spend time before joining, pass those actor reports as
   delayed reinforcements. Keep them out of `combat_start`; queue each through
   public `combat_join` so the engine admits them only at the correct round
   boundary. For a printed later round, pass `--reinforcement-round`; otherwise
   queue after the trigger for next-round entry. Do not place them on the initial
   map or let the auto-runner target them before they enter.
   Keep their side explicit: hostile reports use
   `--reinforcement-hostile-report`, while printed rescuers and other friendly
   NPCs use `--reinforcement-ally-report` and never join the registered party.
   For a semantic condition such as "in danger of being overwhelmed", inspect
   the live combat and pass `--agent-reinforcement-trigger-json` with the exact
   excerpt, a future `trigger_round`, the Agent decision, and current-state
   reasoning. Do not turn that module phrase into a new universal threshold.
7. Start combat from `play` and require the automatic transition to `combat` plus
   an encounter-local temporary map whose encounter, spatial scene, module, and
   location provenance agree. Across the generated corpus matrix, cover both
   immutable positioning modes. A `grid` encounter supplies a coordinate for
   every participant, exercises authoritative geometry, and requests a render;
   preserve the native image and accessible alt text, using package-owned
   portraits or the deterministic fallback. Rendering failure is non-blocking
   and must retain the text/alt projection. An `agent` encounter supplies no map
   or coordinates and records the Agent's action-specific `spatial_facts`.
   After `combat_start` and `combat_end`, consume `tools/list_changed`, verify
   the native list, then `exposure(search/set)` the required current-phase tools;
   phase refresh never auto-loads the next phase's tools. Exercise at least one structured automatic path
   and any relevant owned reaction/choice window. End with a structured outcome;
   never stop while a spell resolution, reaction, death save, or concentration
   obligation is pending. When a hostile selects a structured Multiattack, pass
   its option id only on the first attack, resolve every remaining source-defined
   attack separately, and do not end that actor's turn while its Multiattack
   attack budget/remaining sequence is nonempty.
   Resolve Surprise from the source positioning and the authoritative actor cards.
   A defeat may end combat normally, but the encounter driver must not write a
   caller-named success checkpoint for it. Either preserve it under an explicit
   defeat label or restore the previous valid snapshot before exercising a
   different source-supported route.
   When the encounter text itself explicitly says that this route surprises a
   named participant, preserve the exact excerpt and use the driver's
   source-declared-surprise input for only that participant; do not invent a
   Stealth or scout check. If that grant is in a predecessor scene, first commit
   it through public `record-event` or `record-outcome`, then pass the resulting
   report with `--source-surprise-report` alongside the exact
   `--source-surprised-actor-id` values. The encounter keeps that report's
   `source_ref` and excerpt instead of falsely attributing Surprise to the
   hostile-manifest excerpt in the new scene. Otherwise, when multiple hostiles hide, call public
   `character_check` for each hostile's Dexterity (Stealth), preserving its
   derived skill modifier and automatic armor disadvantage, then compare every
   result with each opponent's passive Perception. An opponent is surprised only
   when it detects none of the hiding
   threats; a tied passive score detects that threat. Never hardcode a generic
   Stealth modifier or substitute one creature's profile for another. Use one
   shared hostile roll only when the exact encounter text explicitly says to roll
   once for the group, and require identical Stealth profiles before doing so.
   Preserve detection separately for every hostile-observer pair: a hidden
   combatant's `visible_to_actor_ids` includes each opponent whose passive score
   detected that combatant. Detecting one hider neither reveals the others nor
   makes the detected hider untargetable.
   In a mixed group, pass only the source-named hiders as hidden actor ids. Do
   not hide their visible allies or include those allies in a shared Stealth
   profile. If the source says a present NPC waits until a later round, keep it
   in the initial participant set but suppress its earlier turns with the exact
   delayed-action excerpt; reserve `combat_join` for actors actually outside the
   fight.
   Preserve source-authored NPC tactics as ordered opening casts with exact
   excerpts. Charged item spells must call `combat_cast_spell` with the actual
   `source_item_id`; never copy the spell into the NPC's ordinary prepared list
   or patch charges. A spell printed as cast before initiative must instead use
   public noncombat `character_action(action="cast_spell")` before
   `combat_start`, paying its slot and starting concentration. A printed Core
   Fly pre-cast must also carry equal exact `target_actor_ids` and
   `willing_target_ids`; the driver forwards them to the engine and must not
   synthesize a speed effect. Bind a printed
   Invisibility effect to the exact spell card and condition; it ends after the
   invisible actor makes an attack or casts any spell, when its duration expires,
   or whenever concentration ends, while the triggering attack still receives
   the unseen-attacker benefit. Incapacitated—and therefore Paralyzed,
   Petrified, Stunned, or Unconscious—ends concentration. Bind a printed
   first attack to that actor's reviewed weapon rather than allowing generic
   weapon preference to override it. When an effect-only custom hit opens an
   on-hit window, query the exact card with `content_solution`. Compile a generic
   source-bound plan on first use when none exists, then resume the same paid
   window through `combat_choice(action="execute_plan")`; later occurrences reuse
   the fingerprint. `on_hit_ruling` only dismisses an exact-source no-op after
   Agent review. If the generic plan vocabulary cannot represent the mechanic,
   leave it at the explicit Agent/DM boundary instead of adding a creature-name
   allowlist, phrase patch, or custom CLI field. If a source says a living
   NPC surrenders at an HP threshold
   only when escape is impossible, confirm both predicates from current state and
   end with `status="surrender"` before another attack. Do not relabel surrender
   as defeat, death, or a generic truce.
   A module-specific encounter procedure does not need a new Core mechanic.
   Preserve its exact source excerpt, invoke the reviewed action through the
   public tool, and let the SagaSmith Agent perform the resulting DM ruling.
   If the action returns `pending_ruling`, inspect its payment and latest
   revision before applying any generic public dice/state/continuity writes;
   never pay the action twice or invent a `combat_choice` window. Do not report
   the narrated effect as applied merely because its slot/use payment committed.
   Preserve the pending ruling and complete the bounded evaluation plus explicit
   public settlement, or choose a different legal recovery such as
   `character_state_change(action="stabilize")`; never fabricate an effect roll
   or healing amount.
   Assign a stable `procedure_id` and require it to survive in the paid action,
   Agent ruling receipt, and temporary combat-map patch. Reconstruct repeated
   procedure counts and ending predicates from those receipts; a narration-only
   ritual action is not regression evidence.
   If an encounter requires at least one living prisoner but the source does not
   preselect an identity, use the driver's minimum hostile-knockout objective.
   Leave all source-valid hostiles eligible unless the Agent has a grounded
   reason to narrow the set. The driver may attempt melee knockouts, but it must
   judge success from the final public character cards and report the actual
   surviving unconscious ids. Do not fail a legal scene merely because one
   arbitrarily preselected candidate died when the required minimum still
   survived; retain exact-id mode for source-identified prisoners.
   A spell or activity pause with no payment is pre-commit: return it to the
   named resolver before rolling healing, applying an effect, starting combat,
   or assuming a charge/slot was consumed. A paid generic-effect pause is
   post-payment and may be completed by the Agent without paying again.
   Assert that both a native result and any compact facade preserve
   `default_resolver`, `ruling_kind`, and `policy_ref`. A pre-commit
   `NeedsRuling` response must expose `committed=false`, missing facts, and a
   retry contract; supply Agent-owned facts and retry at the same revision.
   Missing/conflicting source review and player-owned choices must not be
   relabelled as Agent adjudication.
   Before unattended turn execution, make every tactical choice explicit.
   `--agent-target-priority-json` may cover either side, must enumerate every
   opposing participant exactly once, and preserves the Agent's exact order,
   decision, and reason. When source prose already constrains target priority,
   the Agent declaration may refine ties but cannot reverse the source order.
   Use `--agent-spell-priority-json` for ordered spell, targeting, and
   lowest-available-slot policies, and `--agent-weapon-priority-json` for
   ordered weapon, attack-mode, and optional structured Multiattack choices.
   A source-authored opening action may take precedence once. When no legal
   declared spell remains and no source opening or Agent weapon policy exists,
   stop with `pending_ruling`; never choose from inventory order, actor index,
   class name, or a hard-coded spell list.
   The encounter driver must preserve that same classification. It must not
   interpret every `pending_ruling` from attack preflight or resolution as an
   on-hit choice. A stopped auto-run reports `status`, resolver, missing facts,
   attempted actor/target/action, and retry contract to the Agent. Scene facts
   such as lighting remain Agent judgments grounded in current scene, clock, and
   source evidence, then retry the same generic public action. A real custom
   on-hit window instead carries exact source-card identity and is settled only
   by its persisted content solution. Never substitute one boundary for the
   other or parse pending prose inside the driver.
   When a hidden caster's perceivable spell components require an observer
   matrix, do not infer perception merely because no sound-blocking or
   total-cover fact was recorded. Retry with
   `--agent-casting-perception-json`, naming every adjudicated observer, its
   boolean result and reason, plus the Agent's overall decision and reasoning.
   The first call remains pre-commit and must not spend the action or spell slot.
   When module prose makes the current terrain, tactic, or fictional position
   grant advantage, disadvantage, or relative cover without defining a new rule
   procedure, have the Agent settle that fact with
   `--agent-attack-context-json`. Bind one exact acting participant and `melee`
   or `ranged` mode to the current scene's immutable `source_ref`, an exact
   excerpt contained in the encounter evidence, a concrete decision, and its
   ruling reason. Advantage/disadvantage may apply actor-wide for that attack
   mode. Cover must additionally name the distinct target because it is relative
   to one attacker-target relationship, and its degree is strictly `half`,
   `three_quarters`, or `total`. The public MCP validates the exact active-scene
   evidence; the rules engine alone converts those degrees to +2 AC, +5 AC, or
   an untargetable result. Never provide a numeric `cover.ac_bonus`. Do not add
   creature-, room-, or phrase-specific engine exceptions, and do not let a
   melee or target-specific ruling leak into another attack.
   Do not use that persistent context for a target-owned reaction. When reviewed
   source text lets the target react to a matching attack and modify its roll,
   use `--agent-target-reaction-context-json` with the reacting actor, triggering
   attack mode, immutable source reference, exact excerpt, Agent decision, and
   reasoning. The encounter driver must open and resolve a public
   `combat_choice` reaction window before the attack, spend the target's actual
   reaction budget, and apply the modifier only to that triggering attack.
   Subsequent attacks receive no modifier until the rules engine refreshes the
   reaction; never emulate this with a creature-name exception or a static
   once-per-round counter.
   For a source-authored tactic that selects a reviewed descriptive
   feature/activity or an unstructured hydrated innate spell, use
   `--agent-turn-ruling-json`. It must identify one actor and round, exactly one
   reviewed `feature_id`, `activity_id`, or `spell_id`, the current scene's
   immutable `source_ref`, exact actor-card and encounter excerpts, and the
   Agent's concrete decision and reason. The driver pays the reviewed activity,
   a generic `improvise` action, or the innate spell's structured at-will or
   `N/day` resource. It starts concentration from the hydrated spell card,
   then uses public `combat_check` only for a printed save with no damage and
   stores the adjudication on the temporary combat map. For printed save damage,
   the driver must bind the complete canonical `agent_ruling_commitment` to that
   paid action and call `combat_hp_change(action="save_damage")` once with the
   identical card, ordered targets, save/DC, damage terms, exact mechanics
   excerpt, and current-scene ruling. The MCP verifies the payment and source;
   Core rolls shared damage once, rolls each target's save, rounds half damage
   down, and updates all sheets atomically. Never roll or divide damage in the
   driver, mutate targets individually, reuse the payment with a changed
   contract, or settle the same application twice. Never route a successfully
   hydrated innate spell through its containing passive feature, because that
   would bypass use accounting and concentration.
   A failed-save forced target must survive process restart and be consumed by
   the actual later attack. Declare `ends_if_source_incapacitated` only when the
   source rule establishes that termination boundary. This is the common route
   for module-specific monster tactics; never add creature-name branches or
   silently replace them with an ordinary weapon attack.
   When exact map cells are a visible movement hazard and the Agent decides
   informed actors will not enter them, first record a public
   `movement_hazard_marked` event with exact scene evidence and actor-local
   knowledge that names and avoids every cell. Pass that report through
   `--source-avoidance-report`; the pathfinder must inspect the entire voluntary
   path. If a preferred Multiattack is illegal at range, retry one legal
   ordinary Attack before considering movement. Never cross the marked cells
   solely to preserve Multiattack, and never apply voluntary avoidance to
   forced movement or teleportation.
   Apply the same rule to every regression driver, not only encounter attacks.
   Party catalog application, checks, contests, and level-up subclass, feature,
   or spell application must stop with a structured output report carrying
   top-level `status="pending_ruling"`, `default_resolver`, and the original
   `ruling_requirements`. The acting Agent reads that report, adjudicates
   Agent-owned entries, and resumes the public operation; a generic
   `RuntimeError` is not an acceptable handoff. Rule-pack `ruling.require`
   defaults to the Agent, whereas `choice.require` and explicitly classified
   source/approval exceptions retain external ownership.
   Audit pre-action review states too. A rule import job in `review_required`
   must expose `review_resolution` and every candidate requirement; a
   `review_ready` module statblock candidate must identify the Agent as resolver.
   If any nested requirement is a missing/conflicting-source review, preserve
   that external owner in the aggregate rather than accepting the candidate.
   When module prose establishes relative placement without a numeric map (for
   example, creatures "clustered tightly" around a door), the Agent may map that
   fact onto the temporary combat grid through repeated
   `--agent-position-json` declarations. Every declaration must name a canonical
   participant, unique in-bounds cell, exact encounter excerpt, and explicit
   ruling reason. The driver passes those positions only through public
   `combat_start`, records the ruling in its report, and rejects unsupported,
   overlapping, or uncited placements.
   For a source-authored abstract casualty cohort, pass the printed initial
   count, hostile activity, casualty dice, and recharge instruction through the
   encounter driver's source-casualty declaration. Require a descriptive
   activity card, server-side recharge/casualty rolls, a bounded idempotent
   manifest projection, and no attacks against PCs while that procedure is
   active. For a source-authored minimum separation, pass the exact distance
   excerpt through the source-separation declaration; keep the hostile at or
   beyond it and do not make melee-only actors approach illegally.
   Before initiative, have the Agent inspect the canonical equipped attacks
   against that geometry. If an owned ranged or thrown weapon is present but
   not equipped, declare a pre-combat party loadout and let the driver call the
   public inventory facade in Play. Do not treat backpack ownership as an
   executable combat attack, equip during active combat for free, or bypass
   ammunition and range settlement. Keep the short participant-identity excerpt
   distinct from any longer multi-sentence encounter-procedure excerpt.
   If a source designates one actor to retreat after any printed number of other
   hostiles fall, configure that actor with the defeated-count threshold. The
   threshold neither ends combat nor skips intervening turns: the designated
   actor attempts to leave on its own turn, and other living hostiles keep
   fighting. Bind retreat to one defeated actor id only when the source names
   that exact trigger. A downstream encounter receives the actor as a
   reinforcement only when the recorded source departure actually succeeded.
   If retreat instead triggers after cumulative server-settled damage or a
   single critical hit, configure the printed damage threshold and critical
   trigger together with their exact excerpt. Count only committed applied
   damage and server-confirmed critical attacks, resume from the bounded combat
   log after interruption, and test the trigger immediately after damage
   settlement, before another actor can act. A living actor whose source trigger
   is met departs through the public combat-map path at that boundary. Keep this
   distinct from a defeated-count trigger that says the actor attempts to leave
   on its own turn; one timing rule must not silently rewrite the other.
   Automated party spell tactics may select only currently prepared spells or
   spells the actor actually knows. A spellbook entry alone is not castable.
   Choose the lowest available legal slot at or above the spell's level; when
   lower slots are empty, preserve the public higher-slot cast and its scaling
   rather than falling back to a weapon while usable slots remain.
   For a structured area spell, submit a target context and cover for every
   map-positioned combatant in its area that is not Dead. Stable, Unconscious,
   and other living 0-HP actors remain in that complete set even though they
   cannot take turns. Never trim them with the ordinary active-target filter.
   If an attack returns a defensive reaction window, stop before ending the
   attacker's turn. For automated Shield tactics, use the lowest offered slot
   only when the candidate's projected AC changes the hit to a miss; decline an
   attack that still hits, but use available Shield against Magic Missile.
   When surrender or defeat moves a unique equipped item into party custody,
   use the public `transfer-source-item` path (`character_to_party`) with both
   current revisions and the exact scene evidence. Do not create a duplicate
   loot record; preserve the original item's charges, condition, and source key.
8. Back in `play`, persist the public outcome and only the knowledge actually
   gained by each PC/NPC/monster. Re-read actor cards rather than treating the
   historical final combat projection as current state. On `record-event` or
   `record-outcome`, cite exact source evidence when the text defines the event.
   If the source establishes a situation but leaves its consequence to the DM,
   also submit `--event-agent-ruling-json`; if it is an ordinary source-independent
   DM event, submit the ruling alone rather than attaching adjacent prose. Use
   `default_resolver="agent"`, a concrete decision and reason, and
   `ruling_kind="agent_dm_adjudication"` or
   `ruling_kind="module_specific_procedure"`. Keep
   `--event-knowledge-cause witnessed` only for actors
   directly present and capable of perceiving the information. If the party
   later briefs an absent, unconscious, newly joined, or replacement actor, run
   a separate source-cited handoff with `--event-knowledge-cause told_by` and
   name only the actual recipient actor ids. Update the manifest clue's
   `known_by_actor_ids` projection to match the resulting ledgers; never copy
   knowledge merely because the party collectively has it.
   Treat `record-outcome --world-state-json` as a recursive object patch:
   nested objects preserve siblings omitted by the new outcome, while lists and
   scalars replace their prior value. Re-read the complete manifest after every
   patch and verify that earlier episode, prisoner, ritual, item, and ending
   siblings remain intact.
   For every named non-combat skill check or contest side, pass the skill name
   as `ability` and omit client proficiency/bonus overrides. The Play facade and
   regression driver must derive the complete modifier from the current actor
   card, including half proficiency, ordinary proficiency, expertise, and
   persistent skill bonuses. A boolean `proficient=true` is not a safe encoding
   of expertise and must be rejected rather than merely recorded in progress.
   For a source table with external modifiers, first expand the whole procedure
   and enumerate every currently applicable modifier in a branch-local ledger.
   Give each source its own `modifier_id`, `value`, `kind`, `lifetime`,
   `state_key`, and evidence-bearing `basis`, then pass each object separately
   with `--roll-modifier-json`. The driver rejects duplicate ids, shared state
   keys, invalid lifetimes, and a ledger total that differs from the expression's
   trailing modifier. A cumulative "per previous roll" modifier and a
   "next qualifying roll" modifier are separate even when both equal `+1`.
   Commit count increments, use consumption, and newly granted modifiers through
   `record-outcome` before another table roll. If this audit changes a historical
   result, preserve the defective branch, restore the last verified parent
   snapshot, and replay the server random stream instead of editing the outcome.
9. When the resolved scene yields treasure, select and expand the exact treasure
   chunk and acquire the complete parcel through
   `campaign_change(action="loot_acquire")`. Use one stable acquisition id,
   stable item ids, the printed denominations and quantities, and the exact
   content hash. Currency, items, and the branch-local audit record must commit
   in one public transaction. Record the discovery only for living or otherwise
   present witnesses, sync the playthrough manifest, and verify the resulting
   checkpoint before consuming or transferring any acquired item. For a reward
   promised in an earlier scene and paid at a later destination, cite the original
   promise chunk but validate the event against the current scene and its actual
   Scene Atlas location. Treat missing named businesses, inns, farms, or other
   authored locations as an import/atlas defect to repair and refresh, not as
   permission to reuse an unrelated fallback location. Apply the same gate when
   OCR preserves numbered areas in scene prose but omits them from the Atlas:
   the refreshed parser must reconcile Markdown headings, unmarked numbered
   lines, and OCR-spliced display headings into one source-ordered location
   list. Verify the actual authored range and order after refresh; never let
   document-absolute and scene-relative line coordinates reorder the rooms.
10. Pay source-presented lodging, services, supplies, or other shared expenses
    through the public regression driver's `spend-coins` path. Supply one stable
    spend id, exact positive denominations, the current or explicitly separate
    source scene, actual Scene Atlas location, exact chunk `source_ref`, and the
    Core/Skill `rule_ref` or reviewed price basis. The public
    `campaign_change(action="currency_spend")` transaction must atomically reject
    insufficient funds or commit the full payment and branch-local spend audit.
    Commit witness ActorKnowledge, sync the manifest, and verify the checkpoint;
    never decompose one bill into negative `wallet_change` calls.
11. If a source-cited bargain, tribute, gift, handoff, or destruction removes a
    non-consumable party or character item, use the public regression driver's
    `spend-item` path. Supply one stable spend id, exact item id and positive
    quantity, actual Scene Atlas location, exact source excerpt and chunk
    reference, and every witness actor id. Pass `--item-actor-id` for a privately
    owned item; the driver must bind the owner's current revision, and the facade
    must reject a character id without that revision or vice versa. Omit both
    only for shared party inventory. Verify the atomic owner decrement,
    branch-local `item_spends` audit, ActorKnowledge, manifest sync, and
    checkpoint. Never represent the disposition only in prose while the item
    remains in either canonical inventory.
12. Exercise a source-acquired standard healing potion when a living PC is
    wounded: call `campaign_change(action="consumable_use")` once, then verify the
    stack decrement, service-owned `2d4+2` random receipt, HP clamp, Core rule
    receipt, ActorKnowledge recipients, manifest sync, and checkpoint. A dead PC
    is not a valid recipient and must not gain knowledge from the use.
    For a charged magic item that grants spells, add one source-bound item with
    its exact charge maximum, recovery and last-charge formulas, casting-time
    overrides, attunement/class-list restrictions, and active spell artifact ids.
    Cast it through the public spell tool with `source_item_id`; verify one atomic
    action/charge payment, automatic effect, Core receipts, and any service-owned
    last-charge roll. At an actually reached printed recovery trigger, call
    `inventory_change(action="recharge")` and verify its random-stream receipt.
    Never add the item spell to the actor's ordinary spell list, pay a spell slot,
    pre-roll the resource, or patch the charge count.
13. Give every source-cited scene event an explicit stable `--occurrence-id`.
    Before writing progress, merge the
    new entry into the existing `full_playthrough_events` map; never replace the
    map or reuse an occurrence id for a later event, even when its scene, event
    type, and summary are identical. Re-read progress after the checkpoint and
    verify that earlier events from the same run and scene remain present.
    If an exact retry happens after later events changed the same scene row,
    recover the saved event only when both its complete progress record and its
    matching public campaign-event entry exist. Return that current recovered
    state without resubmitting the old occurrence's progress, continuity, or
    ActorKnowledge writes under their historical idempotency keys. A mismatched
    saved event is a hard conflict, not permission to overwrite it.
    Every `advance-scene` must cite the exact transition text from the manifest's
    current scene through `--source-scene-id`, `--source-ref-json`, and
    `--source-excerpt`. The driver persists that evidence under the occurrence id
    and rejects an arbitrary jump, a stale source scene, or a changed retry.
14. When a resolved event changes an NPC, quest, clue, or machine-verifiable
    world condition, use the public regression driver's `record-outcome` path.
    Give it a stable outcome id and exact source reference or the settled
    Agent-ruling evidence described above; use both when source-defined premises
    require a DM-decided consequence. It must atomically
    commit the event, stable world facts, and cause-scoped ActorKnowledge,
    upsert (not replace) the manifest NPC/quest/clue projections, merge world
    state, then sync and verify a checkpoint containing the resulting manifest.
    For an outcome fulfilled in a later scene, pass the actual occurrence scene
    and Scene Atlas location separately from `source_scene_id`: validate the
    excerpt and exact reference against the original source scene, but write
    progress and location only to the occurrence scene. Preserve both scene ids
    in the continuity event; never move the party back to the source scene merely
    to make a delayed rescue, delivery, promise, or return condition validate.
    The driver must validate the complete prospective manifest before the first
    mutation. It must also re-expand the exact cited chunk and validate its
    canonical source metadata, digest, and contiguous excerpt before writing
    scene progress; containment in the combined scene text is not sufficient.
    If transport fails after scene progress commits, retry the same
    stable outcome id and identical outcome/fact payload: matching saved progress
    is a resume boundary, not a reason to rewrite it with a changed state version.
    If the matching public campaign event and, when requested, the outcome
    checkpoint also exist, treat the whole outcome as recovered and preserve all
    later campaign revisions. Do not resubmit its continuity, fact, manifest, or
    checkpoint calls under historical idempotency keys.
    Narrative event text alone is not a restorable NPC or quest state.
15. Award one source-defined encounter XP parcel to the exact actors who
    participated in earning it. A participant who dies later in that encounter
    keeps the earned share on the retained actor record; death does not erase XP.
    A replacement or relief party earns only the creatures and objectives that
    group actually resolves, and never inherits a predecessor's award or
    progression. Exclude an actor who did not participate, left before the
    encounter, or joined afterward rather than using a historical party count.
    Use one public `award-xp` call when all recipients receive the same integer
    share. If equal division produces a fractional result but the public schema
    accepts only integer XP, have the Agent acting as DM select and record an
    explicit rounding policy from the locked advancement rules.
    A total-conserving deterministic remainder is acceptable only when the
    audit records the ordered remainder recipients, no two shares differ by
    more than one XP, and no allocation is silent. Give each public award call a
    stable `--occurrence-id`; a split remainder therefore uses distinct ids for
    its distinct recipient groups. The exact retry keeps the same id and payload.
16. Advance each eligible survivor through the public regression driver's
    `advance-level` path one target level at a time. Supply the exact source
    reference that established the XP or milestone, an explicit fixed/rolled HP
    method, the intended return phase, and every caller-owned choice. The driver
    must enter `lobby`, replay the stable level transaction when resuming,
    exhaust all returned and newly applicable class/subclass feature artifacts,
    validate any subclass and known/spellbook choices against the active catalog,
    verify that newly level-eligible always-prepared subclass spells were
    materialized, add any newly chosen prepared-class spell cards with
    `method="class_prepared"`, replace the complete prepared-spell list when the
    follow-up requires it,
    re-read and verify the actor, and restore `play`. For a single advancement,
    sync the manifest and verify its checkpoint. For a contiguous group of
    eligible party members advancing from the same source-cited scene or
    downtime boundary, pass `--defer-checkpoint` only after each actor's complete
    advancement can be verified, then call one public `checkpoint` after the
    final actor and verify the aggregate party state before entering another
    sourced scene. Raising maximum HP does not heal current HP. A newly built
    replacement advanced to the module's source gate therefore keeps its
    pre-advancement current HP until a legal rest, spell, feature, potion, or
    other public healing path changes it. Never edit the raw sheet, silently
    choose a subclass or feature, advance an ineligible/dead actor, treat the
    level integer alone as a complete advancement, or patch current HP to the
    new maximum. In the resulting HP progression ledger, treat `source` as the
    short human-readable level label only; the complete normalized citation
    remains in `source_ref` and the milestone explanation remains in `reason`.
    Never concatenate or truncate those machine-audit fields to fit the display
    label.
    During the post-advance verification, distinguish shared top-level resources
    from card-local uses. A feature with an empty `resource_key` spends its own
    `uses`; never create or trust a second same-label `sheet.resources` counter.
    If an older generated/imported actor contains both, use the driver's public
    `sync-character-resources` Lobby transaction with the actor id, an audited
    reason, the intended return phase, and `--defer-checkpoint` only when it is
    part of the same verified advancement batch. Confirm the report removed only
    an unreferenced semantic shadow and that the authoritative card capacity and
    recovery cadence match the locked class rules.
17. Advance campaign time through the public regression driver's
    `advance-time` path whenever travel, waiting, or a source-triggered interval
    matters. Give each interval a stable `--occurrence-id` and supply a positive
    minute/hour/day count. When the module states the exact interval, cite its
    exact scene chunk and excerpt. When text such as "late in the day" needs an
    exact conversion, preserve that source evidence and also submit
    `--time-agent-ruling-json`. When the module establishes the journey or wait
    but leaves its duration wholly to the DM, do not borrow adjacent prose as
    fake timing evidence; submit the settled Agent ruling alone. It must use
    `default_resolver="agent"`, `ruling_kind="agent_dm_adjudication"`, a concrete
    `decision` and `reason`, and `period`/`count` exactly matching the requested
    clock advance. Always pass
    `--time-expected-after-ticks=<elapsed_ticks>`, derived from the current
    service-owned `state.game_time.elapsed_ticks` plus the exact interval. When
    an optional calendar is anchored, also pass
    `--time-expected-after-json={day,hour,minute,elapsed_minutes}` as a projection
    guard. Read the current public state and derive the interval and exact
    destination; never equate a travel-day
    difference with elapsed days or hand-copy a large minute constant without
    this target. The driver rejects a mismatch before the public write and
    `campaign_change(clock_advance)` verifies it again atomically. The
    service-owned `state.game_time.elapsed_ticks` (six seconds per tick),
    optional anchored `state.world_time`,
    continuity event, actual-witness ActorKnowledge, snapshot, and manifest sync
    must all agree. Never update only the manifest's projected clock or invent a
    duration without an explicit audited ruling. Missing or conflicting source
    evidence remains an external review boundary only when the disputed source
    fact itself must be recovered; ordinary DM estimation belongs to the Agent.
    Campaign, playthrough, and encounter regression commands hold one
    cross-process lock for the entire command, keyed by MCP home and campaign.
    Do not bypass that lock or run two commands against the same campaign in
    parallel; separate campaigns remain independently runnable.
    Before the first write in a multi-action event, validate every local source
    and actor report, the current manifest event ordinal, and the public world
    clock. If the interval follows an already recorded result, pass
    `--prerequisite-scene-id` with `--prerequisite-outcome-id`; if it requires
    already prepared narrative or combat actors, pass every id separately with
    `--prerequisite-actor-id`. These checks read current branch state through
    public MCP tools and must reject before `clock_advance` when an outcome or
    actor is missing. A passed JSON report from another attempt or branch is not
    proof that the current branch satisfies the prerequisite. Instantiate and
    validate the complete destination-event cast before advancing time; doing so
    creates no party knowledge, while preparing it afterward leaves a
    cross-tool partial-failure window.
    The state mutation, entity revisions, and exact public response persist in
    one transaction. If the exact-target clock write committed but response delivery or continuity
    commit was interrupted, retry the identical occurrence and payload. The
    driver recognizes only an exact match with
    the canonical expected tick target (and the calendar target when supplied),
    replays the original public idempotency key,
    reconstructs the pre-advance instant from the duration, and uses the
    recovered clock response's original campaign revision for continuity. With
    no matching receipt, the service rejects the second advance at its expected
    target; with an intervening mutation, continuity rejects the stale original
    revision. Do not use a new occurrence id or treat any later current clock as
    recovery evidence.
    Treat that count as actual elapsed time rather than an effect-unit selector.
    `60 minute`, `1 hour`, and two consecutive `30 minute` advances must expire
    the same round- and one-hour actor/world effects exactly once. Completed
    combat and chase rounds accumulate on that same stream across encounter
    boundaries; completed out-of-combat casts include their printed casting time
    and a ritual adds ten minutes. The public receipt and
    subsequent actor/campaign reads must agree on every advanced or expired effect;
    never round or directly patch a subminute/sub-hour/sub-day remainder.
18. Before advancing time for a Short Rest, preflight every participant through
    `character_query(view="rest")` with that actor's exact Hit Die keys/counts
    and optional Arcane Recovery or Natural Recovery allocation. Natural
    Recovery also requires declared meditation in `rest_activity_minutes`; it
    resets on a Long Rest rather than on a campaign-day boundary. A source-bound
    2014 level-20 Sorcerer's four-point Sorcerous Restoration is automatic. A
    2024 level-5+ Sorcerer instead supplies optional
    `sorcerous_restoration_points`, capped by half Sorcerer level rounded down
    and actually missing points; using it spends the feature's once-per-Long-Rest
    allowance. When a
    conscious source-bound 2014
    Bard performs Song of Rest, include that participating Bard's actor id as
    `song_of_rest_source_actor_id` only for members who spend at least one Hit
    Die and can hear the performance. Include any Ki meditation under
    `rest_activity_minutes`, and one
    `attune_item_id` when that rest is devoted to a source-required item. The DM
    must verify the item's exact source prerequisite against the actor card and
    pass `attunement_prerequisite_confirmed=true`; an unproven prerequisite
    blocks the rest mutation.
    All preflights must report ready
    before the first write. Submit all members and choices through one
    `campaign_change(action="party_rest", rest_type="short_rest")`; that
    transaction advances the clock and effects, settles every member, records
    random receipts, and stores the exact replay response. Never call
    `clock_advance` and then loop `character_state_change(rest)`.
    Use the keys currently exposed by each authoritative
    actor card; never derive a class-prefixed key from an older fixture or another
    actor. The server rolls spent Hit Dice, applies Constitution, checks remaining
    dice, Arcane Recovery's once-per-day allowance, Natural Recovery's
    once-per-Long-Rest allowance, the edition-specific Sorcerous Restoration
    amount and use,
    and the level-scaled single Song of Rest die per eligible creature, and
    records the random receipt. A
    failed preflight or settlement must leave both clock and actors unchanged. Give
    each Short Rest a stable `--occurrence-id` and reuse it across that
    occurrence's party-rest, knowledge, continuity, and manifest-sync
    mutations. A later rest needs a new id even when its normalized members,
    duration, and reason are exactly identical. Reusing an id with changed
    choices must fail rather than create another rest.
    The Short Rest's atomic party-rest write must also advance minute/hour/day actor
    and world effects by the actual elapsed minutes. In particular, an established
    one-hour source-bound condition expires after a legal 60-minute Short Rest
    (or two 30-minute advances), while unrelated conditions remain.
19. Resolve every Long Rest through the atomic public
    `campaign_change(action="party_rest")` surface. Supply one stable
    `--occurrence-id` and use it for party-rest, clock, ActorKnowledge,
    continuity, and manifest-sync identities. A later rest needs a new id even
    when its complete normalized member choices, duration, and reason are
    identical. If that rest commits but its following
    continuity checkpoint fails, retry the exact request first. A stale-revision
    idempotency conflict means the rest may already exist: read its owner/DM-only
    receipt with `state_revision(action="receipt")`. Require its branch and
    before/after entity-revision evidence to match the current campaign and
    actors, reconstruct the exact pre-rest request from those before revisions
    and all member choices, and require its hash to match the receipt. Then
    require the receipt's members, duration, campaign revision, canonical game
    time, and optional calendar projection to equal current public state. Also
    require every member's `rest_history` completion/start ticks and any
    prepared-spell receipt to match the
    authoritative card. Only after all checks pass may the driver commit the
    missing continuity event and checkpoint. Bind that continuity commit to the
    atomic party-rest response's exact campaign revision; do not re-read and use
    a later revision, because that would attach the rest narrative across an
    unrelated intervening write.
    The facade payload uses `members`, not `actor_ids`. Each member object
    requires the authoritative `character_id` and that actor's exact current
    `expected_revision`; `duration_minutes` and `rest_type` are siblings of
    `members`. Re-query every member after any preceding stabilization or
    `character_action`. For a plain Long Rest, omit optional prepared-spell and
    Hit Die choices unless the current card and Agent decision require them.
    When corpus coverage requires resource settlement, first query the current
    party card and commit one actually available, source-bound noncombat
    activity or spell through `character_action`; never invent an activity just
    to satisfy coverage. Then construct the one atomic party rest from the
    resulting current actor revisions.
    Never run the rest twice, edit the database, or accept a receipt from an
    intervening campaign mutation.
    When the rest closes a sourced event, require that event's recorded outcome
    with `--prerequisite-scene-id` and `--prerequisite-outcome-id`, and bind the
    exact pre-rest clock with `--rest-expected-start-clock-json`. A branch whose
    clock already advanced but whose prerequisite outcome is absent must reject
    the rest; it is not made valid by a locally cached passed report. Execute
    `prepare -> resolve -> rest` in one fail-fast orchestration process, or check
    every child exit code explicitly before starting the next action. Validate
    all dependent reports before the first time write. Preserve each failed
    attempt under a distinct report path so a successful retry cannot overwrite
    the evidence needed to audit or recover it.
    The service derives rest timing from the shared duration and each actor's
    source-bound features; do not submit a sleep/light/Trance schedule or infer
    Trance from a race name. For a changed 2014 prepared list, put the complete
    selected list in that member request; the Long Rest validates it atomically.
20. When a manifest PC is dead or departed, build one replacement through the
    public party driver. Prefer an applicable unused module pregen; otherwise
    select one legal audited profile, give it a new identity, enter `lobby`
    through `game_phase`, and restore the entry phase even when construction
    fails. Then use `register-replacement` in `play` at the current Scene Atlas
    location. Cite exact module text only when it actually prescribes the
    arrival. For an ordinary DM-authored meeting, have the Agent inspect the
    current scene, party and world state and submit
    `--replacement-agent-ruling-json` with a concrete `decision`, `reason`,
    `default_resolver="agent"`, and
    `ruling_kind="module_specific_procedure"`; do not borrow an unrelated source
    excerpt. The source-reference and Agent-ruling paths are mutually exclusive.
    The new actor must start with empty ActorKnowledge; the joining event may add
    only its witnessed join and explicit `told_by` handoff facts. Keep the
    predecessor actor and its independent knowledge unchanged, replace only its
    active manifest party slot, append the predecessor, replacement, and
    handoff-event ids to replacement history, and verify a checkpoint after the
    manifest update. Re-read every ending condition after registration: an
    active-party `sheet.progression.level` check must follow the replacement
    party slot, while every other actor check remains attached to the
    predecessor unless its own source condition says otherwise.
21. Advance to the exact indexed conclusion scene only after its source-defined
    prerequisites are true in authoritative runtime state. Record the decisive
    conclusion facts, NPC state, quest state, world state, and actual-witness
    ActorKnowledge through public outcome and manifest paths with exact source
    references. Narrative prose by itself is not a machine-verifiable ending.
    Through the native MCP facade, select the indexed conclusion with
    `module_set_progress` and persist every decisive sourced proposition through
    `memory_change(action="upsert")` (or one atomic `commit` when event,
    audience facts, and ActorKnowledge are settled together). Give each fact a
    stable `fact_key`, exact content, and source metadata; refresh an existing
    fact before supplying its `expected_revision_id`. Call
    `playthrough_manifest(action="sync", ...)` to project the authoritative
    current scene. A direct native ending may then check that proposition with
    `kind="memory_fact"`, the exact `fact_key`, empty `path`, and an exact
    content comparison. Do not reconstruct or replace the complete manifest to
    write one outcome or current-scene field.
    Treat a regression fixture's Pack-local `ending_prerequisites` as mandatory
    receipt expectations. Re-read their managed source evidence, then perform
    each prerequisite through its named public facade before recording the
    conclusion: source items require ordered, committed source-bound acquisition
    and surrender receipts. For an `item_spend` surrender, use a new top-level
    idempotency key and put a new stable `spend_id`, the exact matched
    acquisition `item_id`, quantity, reason, and the supported managed
    `source_ref` inside `payload`. Do not put `excerpt` in that source ref or
    reuse a spend id from a rejected attempt.
    Resolve every receipt against its own declared page range and heading. Do
    not reuse the acquisition source reference for a later presentation or
    reducer event merely because the same item or ending is involved.
    Source-defined checks require a committed
    engine-owned check receipt with exact scene evidence, skill/DC, required
    result, and authoritative random receipt. Put that check evidence directly
    in `payload.source_scene_id` and `payload.source_excerpt`; a nested
    `payload.source_evidence` object is not a substitute for those public
    receipt fields. For a reduced check, also send `payload.base_dc` and the
    fixture's exact `payload.applied_reducer_ids`. Preserve separate ability and
    skill fields exactly; a Charisma (Persuasion) check uses
    `payload.ability="Charisma"` and `payload.skill="Persuasion"`, not
    `ability="persuasion"` with the skill omitted. When the source changes that DC
    because an Agent-adjudicated condition is true, first commit each declared
    semantic prerequisite as its own source-bound event and fact using
    `memory_change(action="commit")`; the returned fact must cite that returned
    event id. The atomic payload uses the plural array
    `payload.event={"event_type":"source_semantic_event",
    "audience_scope":"party","payload":{"reducer_id":"...",
    "source_ref":{...}}}` plus
    `payload.facts=[{"kind":"memory_fact","fact_key":"...",...}]`; put the
    audience on `payload.event.audience_scope`, not on a fact. Singular
    `payload.fact` only commits the event and can never satisfy this receipt.
    If the stable fact key already exists from a prior rejected or
    superseded attempt, first use public `memory_query` to read its current
    `revision_id`; then include that exact value as the fact's
    `expected_revision_id` in the same source-bound atomic commit. Do not switch
    to a different fact key, drop the fact, or fall back to an unlinked upsert.
    For a standalone correction with `memory_change(action="revise")`, put both
    `memory_id` (the returned stable fact `id`) and `expected_revision_id` (the
    returned `revision_id`) inside `payload`; only the campaign CAS
    `expected_revision` remains top-level. Campaign memory `content` is always a
    string. If an ending uses a `kind="memory_fact"` equality condition, copy the
    successful write's returned `fact.content` exactly as its value; string
    `"True"` does not equal boolean `true`, and the verifier does not coerce it.
    Then calculate the check DC from the fixture's base DC and exactly
    those preceding reducer receipts. A bare added/upsert fact, an unreferenced
    narrative assertion, or a reducer recorded after the check is not evidence
    for the reduced DC. The event may establish source-defined presence,
    alliance, presentation, or another semantic condition, but it cannot assert
    that the later engine check succeeded. A fact, scene-progress
    flag, or manifest field written by the Agent in the same conclusion batch
    cannot prove the event that the Agent just asserted. Persist those
    projections only after the independent receipt exists, and keep the receipt
    in the regression transcript.
    While the current ordered receipt audit says
    `ready_for_verification=false`, do not call `verify_ending`, do not narrate
    the ending as complete, and do not treat an older completed manifest as
    evidence. After the required source lookup, the first authoritative write
    must be the audit's exact `first_missing_id` receipt.
    If the runner supplies a non-empty `MANDATORY_FIRST_ENDING_MUTATION`, treat
    its machine-derived tool, action, expected object, and safe source query as
    the current turn's binding execution target. Do not call any
    `playthrough_manifest` action until that mutation succeeds.
    When that object includes `write_ids`, copy its exact fresh
    `idempotency_key` and `spend_id`; do not derive them from a fixture receipt
    id or reuse a historical attempt. For `item_spend`, use the single
    `matched_acquisition_item_ids` value as `payload.item_id`; never substitute
    another same-named item from inventory or history.
    If an immutable historical ending condition contradicts the completed
    receipt chain—for example, requiring inventory to remain truthy after the
    source item was surrendered—never reacquire the item to satisfy it. Create
    a new condition id from an exact source-bound fact/content plus current
    runtime checks, and verify that condition instead. After the required source
    lookup, that replacement `configure_ending` is the first authoritative write;
    do not verify the old condition or repeat any prerequisite receipt first.
    If the public facade rejects replacement because the invalid historical
    ending already made that branch immutable, preserve it as negative evidence.
    Enter Lobby, verify the latest source-branch snapshot from before that invalid
    completion, and use `branch_change(create)` with `from_snapshot_id` and
    `checkout=true` to make one recovery branch. After the binding refresh,
    re-run the source prerequisites and configure/verify the corrected ending on
    that branch; never patch storage or mutate/delete the historical branch.
    Keep the indexed conclusion's Scene Atlas progress `status="current"`
    through ending verification; its progress may be 100. Marking the only
    selected scene `completed` removes the authoritative current-scene selector
    and correctly blocks an otherwise active playthrough from ending.
22. Configure each source-defined ending through the public regression driver's
    `configure-ending` action. Its `source_ref` must use the manifest source
    schema and preserve the asset/checksum, module, scene, chunk, page, content
    hash, and exact excerpt used as evidence. Define checks against specific
    manifest paths, world facts, actor/NPC state, quest state, and other public
    projections needed by that ending; do not use a broad narrative string as a
    substitute for the printed conditions. Before activating a parser-backed
    module refresh, resolve every removed scene that owns progress through the
    Agent-default DM review returned by validation. An exact unique
    chapter/title/page-range match may settle the remap automatically; otherwise
    inspect the candidate index and submit an explicit old/new scene ruling.
    Pass the same reviewed mapping to the activation transaction and the
    playthrough manifest remap so progress, traversal, current scene, and
    Snapshot recovery cannot disagree. After a parser-backed module refresh,
    require each ending citation for that same source asset to resolve to exactly
    one new chunk with the same content hash and excerpt. Re-read the condition
    and require its module, scene, chunk, pages, heading, and any
    `current.scene_id` check to reference the active revision. The refresh must
    fail closed on zero or multiple matches and must scope idempotency to the
    exact refreshed manifest payload. Reimport must retain the old module or
    rule source as an immutable retired revision so historic snapshots and exact
    citations still resolve. Default search and current-scene selection must use
    only the active revision.
    When operating through the native MCP facade instead of the regression
    driver's convenience action, first call
    `playthrough_manifest(action="get")` and check the registered condition
    ids. Submit each new condition with
    `playthrough_manifest(action="configure_ending",
    payload={"condition": <condition>}, ...)`; do not reconstruct and replace
    the complete, potentially offloaded manifest merely to add one condition. A
    condition has exactly `id`, `label`, `source_ref`, and a non-empty `all_of`. Every
    `all_of` entry has exactly `kind`, `path`, `actor_id`, `fact_key`,
    `operator`, and `value`; `kind` is one of `manifest_value`,
    `campaign_state_value`, `actor_value`, or `memory_fact`, and `operator` is
    one of `equals`, `not_equals`, `in`, `at_least`, `at_most`, or `truthy`.
    Supply the actor id only for `actor_value` and the fact key only for
    `memory_fact`; retain the other fields as empty strings. Configure it with
    the current `expected_revision`, current `branch_id`, and a stable
    idempotency key. At least one check must prove a source-defined outcome
    state; phase, inactive Combat, current position, and manifest readiness are
    settlement prerequisites, not an ending. If a not-yet-completed condition
    was registered incorrectly, submit the corrected condition under the same
    id through `configure_ending` before verification. After a successful
    mutation, refresh the campaign
    revision and call `playthrough_manifest(action="verify_ending",
    payload={"condition_id": <exact registered id>}, ...)` with a new stable
    idempotency key. Never discover this schema by weakening checks or by
    repeatedly guessing unsupported field names.
    An indexed, resolvable source-defined ending in the active Pack is not
    missing Pack content merely because runtime prerequisites or verification
    are incomplete. Re-read and correct the runtime condition; do not start,
    finalize, import, or activate another module draft unless a direct Pack
    content query proves the cited ending itself is absent or corrupted.
23. Call `verify-ending` without deferral. Require every returned check to pass,
    the selected ending id to be achieved, the manifest and ending state to be
    `completed`, and a verified terminal checkpoint to become the Snapshot DAG
    head. Only `combat.active=true` is an active-combat blocker. A retained
    `combat_query(view="status")` projection with
    `snapshot_role="historical_final_encounter"` and
    `combatant_state_is_current=false` is audit evidence and must not block a
    conclusion.
24. Ordinary final-scene outcome writes may defer their individual checkpoints
    only as one immediately closed terminal batch. End that batch with one
    public checkpoint before `verify-ending`. Never defer ending verification
    or its terminal checkpoint, and never reuse a final-scene batch key for a
    later retrospective correction.
25. When two published volumes form one continuous campaign line, do not create
    a new campaign, run, branch, or party after the first volume's verified
    ending. Call the public regression driver's `continue-segment` action with
    that exact achieved condition, the next module's indexed opening scene, its
    exact opening source evidence, the terminal scene occurrence, and a new
    stable transition occurrence. The driver archives the complete verified
    ending, terminal scene, terminal Snapshot DAG head, random-stream position,
    and canonical game/world clocks under `world_state.completed_segments`;
    reopens only the current ending as pending; commits the authoritative
    `SceneProgress` transition; replaces the manifest projection; and creates
    the first checkpoint in the next volume. Require the same actor ids, levels,
    XP, hit points, resources, wallets, equipment, ActorKnowledge scopes, random
    stream, active branch, and inherited world state before and after the
    handoff. On interrupted delivery, retry the same occurrence: the action may
    resume only the exact archived transition and must not duplicate the segment
    record, scene visit, or checkpoint. Configure the next volume's ending
    conditions only after this handoff succeeds.

## Exact scene evidence

`module_search` selects a document chunk; `module_expand` proves what that chunk
contains. Neither proves that the text belongs to a chosen scene. A PDF chunk can
have no scene id, overlap adjacent headings, or match another occurrence of the
same room name. Before using a DC, participant excerpt, or map location:

1. select the scene from `module_query(view="index")`;
2. read it with `module_query(view="scene")`;
3. verify module id, scene id, page range, and location key;
4. expand the selected chunk and require its canonical module/scene/chunk/page/
   heading/hash fields to match the retained `source_ref`;
5. copy the contiguous evidence substring from that expanded chunk, while also
   requiring it to belong to the selected scene.

The preflight check normalizes PDF control characters, soft hyphens, typographic
quotes, dash variants, case, and whitespace. This only compensates for extraction
artifacts. It never makes a paraphrase, translation, truncated count, or text from
another scene acceptable.

When a source rule calls for a random encounter check or table roll, use the
public driver's `roll-source` action with a stable occurrence-specific roll id,
the exact dice expression, Scene Atlas location, expanded chunk reference, and
verbatim rule excerpt. The action advances the server-owned random stream,
records the receipt and result in scene progress and continuity, and syncs the
manifest. Use a DM audience for hidden encounter checks. If the result triggers
a second table roll, give that roll a different id and perform it through the
same action; never generate either result client-side. For a source-defined
sequence of identical independent checks, such as one hidden road-event check
per travel day, pass `--roll-count` and one stable roll-id prefix. The driver
keeps one MCP process but expands the sequence into independently identified
public server-dice calls with ordinal suffixes, separate idempotency keys,
continuity events, and random-stream receipts. It does not combine the dice or
generate them client-side. Use `--defer-checkpoint` for the sequence only when a
single public scene checkpoint immediately closes the complete batch.

A noncombat check or contest can occur in one scene while its printed table or
procedure is indexed under another. Keep `--scene-id` and `--location-key`
bound to the actual occurrence scene, and pass the indexed rule scene through
`--source-scene-id`. The public driver validates the exact reference and excerpt
against that source scene while writing progress, continuity, and
ActorKnowledge to the occurrence scene. Never move the party to a rules-only
scene or copy the source text into the current scene merely to satisfy
validation.

For scene advances, narrative-NPC creation, source-cited noncombat checks,
`record-event`, `stand-up`, `initialize-source-state`, `advance-time`,
`transfer-source-item`, and XP awards, pass the explicit
`--occurrence-id` described above. For environmental damage, use one distinct
`--damage-event-id` for each actual damage occurrence; for `use-activity`, use
one distinct `--activity-event-id` for each use, including another use after a
legal rest in the same scene. Retry an interrupted occurrence with the same id
and unchanged payload. Never derive these ids only from scene, actor, expression,
activity, summary, reason, member choices, or recipient set.

A scene advance must update the branch-scoped authoritative SceneProgress before
the manifest transition is replaced. Record the occurrence-specific transition
evidence in that target scene's progress state, preserve an existing completed
scene on a revisit, and bind any supplied location to its Scene Atlas. A manifest
write by itself is not a scene move: manifest synchronization will correctly
project the old SceneProgress current scene back over it. If SceneProgress
committed before the manifest response was lost, retry the same occurrence id;
the driver must recognize the exact progress record and finish the manifest
write without advancing the scene twice.

## Snapshot and branch-isolation audit

Run destructive rehearsal steps on a disposable branch created from a verified
source checkpoint. Carry fresh campaign/actor/scene revisions and idempotency keys
through every mutation.

Run this audit through a real MCP session and capture notifications. Exercise
an exact idempotent retry, a stale revision conflict followed by authoritative
refresh and request rebuild, process/session restart plus resume, verified
`snapshot_restore`, branch create/checkout, and `state_revision` undo/redo.
After every operation that can change phase or checkout, require an immediate
`tools/list_changed`, refresh the native list before the next domain call, use
`exposure(search/set)` on the retained binding, and prove the next legal call
succeeds. Within the same Host process, never call `exposure(open)` after one of
these transitions: `open` replaces the session exposure and is not a refresh.
Only a new MCP session or a genuinely changed campaign/principal binding opens
again. Cross a changed host context binding for checkout/restore; do not
mistake a phase-only transition for a context-epoch barrier.

Every checkpoint must capture the exact active module revision set. After a
restore or branch creation, verify those module ids before reading the current
scene, and require the current scene to belong to one of them. Public
`branch.is_current` and `snapshot.is_head` are projections of the campaign's
active-branch pointer and the branch's head pointer; never attempt to repair a
second boolean. Treat chapter status as import/indexing metadata only—play
progress and the current scene come from scoped `SceneProgress`.
The restored snapshot also defines the exact optional rule-pack lock and actor
revisions at that point. If a pack activation, spell hydration, or actor
replacement occurred only after the restored parent, repeat those changes
through their public MCP workflows on the child branch before resuming; do not
assume later branch state leaked backward and do not patch storage.

Use scene-level checkpoint batching on a campaign's main timeline. Pass
`--defer-checkpoint` only to repeated `prepare-statblock` calls on the main
timeline and to these public playthrough-driver actions:
`prepare-narrative-npc`, `resolve-check`, `record-event`, an intermediate
`record-outcome`, `advance-time`, `roll-source`, `initialize-source-state`,
`stand-up`, `use-activity`, `provision-source-item`, `transfer-source-item`,
`acquire-loot`, `spend-coins`, `spend-item`, and `use-consumable`.
`apply-damage` may defer only while the authoritative resulting HP remains above
0; the driver must force a snapshot when damage reaches 0 HP even when deferral
was requested. `advance-level` may also defer only as part of one contiguous
same-scene or same-downtime party-advancement batch; every actor must complete
and verify all required follow-up before the next actor, and one aggregate public
checkpoint must immediately close the batch. Each action must still commit its authoritative state, exact
source reference where applicable, event/facts, ActorKnowledge, and manifest
mutation before returning; only its action-local snapshot is omitted. After the
related preparation, checks, events, loot, expenses, consumables, and ordinary
time advances are complete, call the public `checkpoint` action once with a
stable label that identifies the scene and outcome plus a distinct stable
`--occurrence-id`, then verify that snapshot. Reuse the occurrence id only for an
exact retry. A later visit may reuse the reader-facing label, but it must use a
new occurrence id and create a new DAG node.
Re-read the public manifest and require the returned snapshot id in
`snapshot_dag.nodes` and as `snapshot_dag.head_snapshot_id`; seeing it only in
the separate runtime projection does not close the scene. A deferred scene is
not complete until this terminal checkpoint exists. If transport or the process
stops first, resume the same idempotent actions, re-read public state, and create
the missing scene checkpoint; never repair the database or fabricate a manifest
head.

An interrupted encounter command may have committed one or more public
transactions before the caller timed out. Before retrying, read combat, actors,
manifest, current branch, random-stream position, and snapshot head through
public queries. If the committed state is no longer a legal retry point, preserve
that lineage and create a disposable child from the last verified parent
snapshot through `branch_change`; never rewind or patch the active database.
Recovered automatic movement uses an idempotency identity that includes actor,
target, sequence, destination, distance, and full path. An exact retry must
replay; a pathfinder replan at the same turn sequence must produce a distinct
movement request so the earlier destination cannot be replayed accidentally.

Never defer a combat-end checkpoint, PC death or stable recovery, replacement
handoff, standalone level advance, Short or Long Rest, major branch point,
module transition, or campaign ending. A deferred party-advancement batch is
incomplete and must not enter another sourced scene until its aggregate
checkpoint verifies. Never combine both `--defer-checkpoint` and an
isolated `prepare-statblock` branch: an isolated branch requires its own actor
checkpoint so it can close and return without contaminating the source branch.
For branch regression, keep only a verified parent checkpoint and the completed
branch checkpoint unless an intervening key event above requires another one.
Do not create one snapshot for every ordinary roll, narrative note, loot line,
or repeated source-identical actor.

Create and verify additional checkpoints after key combat and during genuinely
long scene walks where recovery would otherwise require repeating substantial
play. Then:

An exact checkpoint retry may encounter a newer manifest revision after its
sync. Recover only through that occurrence's owner/DM idempotency receipt. Require
the receipt request hash, label, branch, response snapshot id/slot/parent, current
branch head, public snapshot list, integrity verification, and manifest DAG head
to agree. Never recover by label search: distinct checkpoints may legitimately
share a reader-facing label, and a same-named older or sibling-branch snapshot is
not the retried occurrence.

If the parent snapshot's built-in Core fingerprint is unavailable in the current
runtime, do not relock the live branch and retry a normal restore. Inspect the
target with `snapshot_query(view="core")`, review the old/new fingerprints, and
rerun `branch-from-snapshot` with an explicit Core-conversion reason. The public
driver must use `branch_change(action="create_core_upgrade")`, preserve the old
snapshot checksum, and verify the converted child checkpoint before play resumes.
A snapshot with no recorded Core lock remains blocked for an edition migration.

1. end combat and switch the disposable branch to `lobby`;
2. create and verify its closing snapshot;
3. checkout the original source branch through `branch_change`;
4. restore its original phase;
5. create and verify a new source-branch head;
6. compare branches and re-read current scene/progress, actor HP/resources,
   campaign facts, ActorKnowledge, and active combat.

The source branch passes only when its scene/progress, actor state, facts, and
knowledge are unchanged and no combat remains active. Interrupted disposable
branches must be closed and returned through the same public MCP sequence before
retrying; do not delete them or repair the database.

When replaying an objective outcome on a sibling branch, reuse its deterministic
`fact_key`. The commit must create or revise a branch-local head for the shared
stable fact identity while leaving the sibling head unchanged. A visibility
error is a branch-isolation defect to fix; inventing a branch-suffixed key is not
a valid workaround. Verify both branches through `memory_query` or
`branch_query(view="compare")` after the replay checkpoint.

## Corpus completion report

For every campaign, retain machine-readable reports for inventory/disposition,
the generated coverage matrix, import/index, all-scene walk, PC preparation,
hostile preparation, Agent decisions, non-combat resolution, NPC dialogue,
chase where applicable, combat, ending, recovery, and final read-only audit.
Retain a structured transcript, tool timeline, phase/exposure/native-list
timeline, authoritative random receipts, audience projections, ending checks,
and minimum diagnostic failure evidence. A corpus is complete only when all
runnable campaigns satisfy:

- every non-reference/non-overview scene was read and progressed on an isolated
  branch;
- a source-bound PC and all selected encounter actors are complete;
- one source-cited non-combat check and one structured combat path committed;
- one legal source-defined ending completed, saved, and restored;
- the matrix as a whole proves Grid and Agent combat, DM and player audience,
  normal and recovery paths, and every discovered exclusion has a reason code;
- ActorKnowledge exists only on the rehearsal branch for actual witnesses;
- HP/resources, scene progress, current scene, facts, and knowledge are restored
  on the source branch;
- the final branch is the expected source branch in `play`, with no active combat
  and a valid head snapshot.

Keep parser warnings, normalization notes, and review-only candidates in the
report. A normalization note records text the parser safely excluded and must not
be counted as a ruling or blocker. A warning that
demotes source-printed Spellcasting to a descriptive passive blocks that
spellcaster from combat until the importer is repaired and the actor is recreated
from a clean parent snapshot. Warnings are evidence of fail-closed behavior, not
permission to fabricate missing content. A successful
corpus result means the exercised public workflows passed; it does not claim that
every optional rule or every possible encounter path was executed.
