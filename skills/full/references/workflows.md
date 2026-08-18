# Runtime Workflows

Full Runtime uses the `sagasmith_dnd` MCP server. See `mcp-contract.md` for the
complete public facade and mutation contract. Never call an internal/retired tool
name copied from an old prompt.

Read `long-form-narrative-architecture.md` for the complete cross-layer ownership
model and the distinction between immutable source, Agent adjudication, engine
settlement, continuity ledgers, the playthrough manifest, and Snapshot recovery.

## Exposure and session start

1. Call `storage_status`, then `campaign_query(view="list")` and select a campaign.
2. Call `exposure(action="open", campaign_id=...)`. Its phase is authoritative.
3. Call `exposure(action="search")`, then add or remove exact tool ids with
   `exposure(action="set")`. Authorization and phase filtering remain server-owned.
   Keep each search to one short capability phrase or exact tool id. Never join
   several tool ids and narrative requirements into one query; empty matches are
   not evidence that the phase has no tools.
4. Refresh `tools/list` after `tools/list_changed`, then call listed domain tools
   directly. A core tool is not a proxy for a newly exposed domain tool: after
   loading `character_query` or `combat_query`, call that native tool rather than
   placing its name or action inside `campaign_query`.
5. In `play`, read `module_query(view="current")`, expand the exact scene with
   `module_query(view="scene")`, read recent `campaign_event(action="list")`, and
   call `continuity_context` separately for each acting PC or NPC. For a DM
   deciding module-authored behavior, pass `related_refs` for every relevant
   actor, scene/location, active quest, and key item, then read the returned
   exact `module_evidence`.
6. Refresh `campaign_query(view="party")` and relevant
   `character_query(view="get")` cards. Never carry a card or revision across a
   write, phase transition, branch checkout, or restore.

An exposure belongs to one MCP session and principal. Every other Agent opens its
own exposure. Changing one session's native tools must not expose them to another.

## Module-authored narrative behavior

1. Search and expand the authoritative module chunks. Keep exact excerpts for
   the whole nearby narrative sequence, not just the first sentence that happens
   to match the current query.
2. Upsert a stable, DM-only `context_anchor` whose subject and `related_refs`
   point to the NPC, scene/location, quest, faction, and key item. Store only the
   exact managed source bindings. Do not encode conditions or actions.
3. Immediately before adjudication, refresh actor cards, current scene,
   inventory/ownership, events, and `continuity_context`. The Agent interprets
   `module_evidence` against that live state.
4. Carry out the decision through existing public checks, movement, combat,
   inventory, time, event, fact, knowledge, and playthrough tools. Standard
   mechanics and random outcomes remain server-owned.
5. Commit only the path that happened. Do not copy DM source into player context,
   transfer one actor's knowledge to another, or persist hypothetical branches.
   After snapshot restore or branch checkout, discard the prior assembled context
   and retrieve it again.

## New campaign and module PDF

1. Without a campaign, open an exposure and add `campaign_create`; after creation,
   reopen the exposure with the new campaign id.
2. Lock the correct Core edition with `campaign_rules`. Do not silently use a
   different edition or optional publication.
3. Inventory every allowlisted file before importing. Call
   `character_query(view="document")` for character sheets, pregenerated-PC
   packets, and ability-score option files. Its classification and checksum are
   authoritative; these documents never enter `module_draft`. Keep explicit
   `manual` score entry available even when the document supplies arrays.
   For a campaign directory, group every document below the same top-level
   campaign folder into one campaign while retaining one immutable module
   revision per physical document. A root-level adventure remains its own
   campaign. Do not create one campaign per appendix, map packet, or supplement.
4. Load `lobby.modules`. For each module PDF call `module_draft(start)` once.
   Core+D&D stage, inspect, normalize, and mechanically construct the first
   editable workspace. Keep the same `job_id`; read the current draft and its
   issues, use `evidence` plus revision-checked `edit` operations until the
   Agent is satisfied, then send an explicit `confirmation` to `finalize`.
   Repair damaged PDF text only through one checksum-bound
   `edit(operation="source_text")` batch per page. Text-only Agents use
   two-source agreement or bounded context with unchanged digit sequences;
   only a reviewer that actually saw the image may use rendered-page evidence.
   Re-read the updated draft and revision after every edit. Finalization creates
   an immutable Pack but never activates it; activate later through
   `content_pack(kind="module")`.
5. Review `module_query(view="index")`. Search only selects candidates; expand the
   chosen scene before using its facts. Verify scene boundaries, restricted/public
   visibility, encounter participants, exact source excerpts, spatial locations,
   explicit-evidence spatial connections, and parser warnings. Never treat room
   heading order as connectivity; an empty `spatial.connections` list means the
   parser found no source-backed topology.
   If a PDF map contains required topology, use
   `module-visual-atlas.md`: `module_query(view="assets")` ->
   `module_draft(action="evidence")` -> visual inspection ->
   `module_set_progress(spatial_review=...)`. Never infer an edge from room order.
   If a 2014 appendix statblock is image-only, use
   `module-image-content-review.md`. First call
   `module_draft(action="edit", operation="statblock")`; the server performs
   layout OCR and independent critical-fact corroboration without requiring model
   vision. If it returns `requires_agent_fill=true`, the Agent reads the returned
   normalized OCR text and exact requirements, supplies the semantic
   `payload.agent_fill` under a fresh idempotency key, and only then re-reads the
   immutable review. Then use
   `character_create_from(mode="module_statblock")`. Only when recovery remains
   ambiguous may an image-capable reviewer render, inspect, and submit the page
   manually. Do not send a 2024 card through this 2014 OCR grammar. Submit a
   complete indexed 2024 candidate with
   `content_kind="dnd5e_2024_statblock"`, or use an image-capable literal visual
   transcription; otherwise leave the card unresolved.
    Also inspect `module_query(view="candidates")`. A `review_ready` candidate may
    be submitted to `module_draft(action="edit", operation="content")` only with its exact
     `source_chunk_ids`. Read its structured `ruling_requirement`: complete-text
     review defaults to the Agent, so do not pause merely because the workflow
     calls it a DM review. If `agent_fill_requirements.required` is true, the
     Agent must cover every listed Multiattack as `structured` or
     `agent_ruling`; parser-produced options are never authoritative for module
     creatures. A `blocked` candidate whose requirement names
    `missing_or_conflicting_source_review` is a stop condition. For 2014, first
    use `module_draft(action="edit", operation="statblock")` with its managed PDF page.
    For 2024, require complete edition-matching indexed text or capable visual
    review. If ambiguity remains, an image-capable
    reviewer may transcribe only observed fields, or leave it unresolved. A
    text-only Agent cannot claim to have inspected a returned image. Never repair
    OCR from rules memory or silently relabel blocked evidence as reviewed text.
6. Set scoped progress with `module_set_progress`, including
   `current_location_key` and `state.location_scene_id` when the spatial room is a
   separate scene. The location key must be copied from the expanded scene's
   `spatial.locations`; a slug, display label, or guessed room id is not valid.
   Never merge narrative text merely because two scenes refer to the same encounter.
7. If the scene changes by opening hours, daylight, watches, or travel duration,
   treat `state.game_time.elapsed_ticks` as the one elapsed-time authority and
   `state.world_time` as its optional calendar projection. Establish
   `campaign_change(action="clock_set")` before resolving a calendar-dependent branch.
   Advance only source- or DM-established elapsed time with
   `campaign_change(action="clock_advance")`; it updates the tick stream,
   optional anchored calendar, and timed effects atomically. Every minute, hour,
   or day advance should include the canonical target
   `payload.expected_elapsed_ticks`; derive it from the current public
   `game_time.elapsed_ticks` plus the reviewed interval. When a calendar is
   anchored, also include
   `payload.expected_world_time={day,hour,minute,elapsed_minutes}` as a projection
   guard. Never hand-copy a large tick or minute literal without deriving both
   targets. The MCP rejects a missing target or a duration that would land
   anywhere else before changing the timeline or any timed effect. Completed
   combat/chase rounds and out-of-combat spell/ritual casting use the same tick
   stream; do not add a second narrative clock write for them.
   For a completed Short or Long Rest, use
   `campaign_change(action="party_rest")` instead:
   it advances the clock once and settles all named members atomically. Never
   advance the rest clock separately or loop individual actor rests.
8. Load `lobby.characters`. Use `character_create_from(mode="build")` for confirmed
   PCs and `mode="direct"`, `mode="template"`, `mode="statblock"`, or
   `mode="module_statblock"` for mechanically authoritative NPCs and monsters.
   Either statblock mode must cite exact imported evidence; unsupported or absent
   creatures remain unresolved instead of being replaced by a similar one. For
   an important named NPC whose exact module chunk supplies identity but no
   statblock, use `mode="narrative_npc"` with the active source reference and a
   name-bearing excerpt. Its `narrative_only` default mechanics are sentinels and
   cannot be used for a check or combat.
   When the module modifies a named standard creature, import that exact rule source
   and use its source-bound `variant` whitelist; never replace the whole actor sheet.
   If rule-source statblock creation fails because the indexed text split a card
   across columns, retry `mode="statblock"` with the source-established
   page/neighborhood `chunk_ids` and the exact printed heading in
   `payload.source_statblock_name`. Keep the campaign instance name in
   `payload.name`. The deterministic text-layout result must cite only chunks
   from that heading through the next creature core and report
   `source.text_layout_recovery`; it does not require Agent vision. If required
   facts are still absent or conflicting on a 2014 card, use
   `rulebook_draft(action="get")` to find the retained `job_id`
   whose `source_id` exactly matches the selected source, then call
   `rulebook_draft(action="edit", operation="statblock_recovery", payload={job_id, name, page_number?})`.
   `name` is the exact printed creature heading, not a differently named campaign
   instance.
   The server performs 2014 local layout OCR, uses the adjacent creature core to
   disambiguate repeated decorative/narrative copies of the same heading, and
   corroborates critical facts without asking the Agent to inspect an image. Retry with
   `mode="reviewed_rule_statblock"` and the returned `review_id`. Stop for explicit
   source review on low confidence or disagreement. A 2024 card instead uses
   exact edition-matching indexed text with `review_mode="agent_text"`, or an
   edition-matching visual review; `recover_statblock` must reject it. If OCR is structurally
   ambiguous but one exact indexed page still contains the complete card as an
   ordered contiguous chunk segment, a text-only Agent may normalize that segment
   through `rulebook_draft(action="edit", operation="statblock_review",
   payload={job_id,page_number,normalized_content,observation,
   review_mode:"agent_text",evidence_chunk_ids:[...]})`. The MCP verifies source,
   page, ordinal continuity, no invented normalized fact, and no omitted selected
   evidence. This path is forbidden when the indexed facts themselves are missing
   or conflicting. That remaining boundary may require an image-capable reviewer;
   never fill the card from memory or substitute a similar creature.
   Read `module-image-content-review.md` for the distinction between an image-only
   full card and a standard card with module instance changes.
   For an already reviewed shared actor, use
   `character_create_from(mode="content_actor")`. PC, NPC, and monster share one
   card format; import creates a new runtime identity and an empty ActorKnowledge
   ledger. Browse bundled standard monsters/NPCs in the relevant finalized
   Preset Pack through `content_pack(action="list"|"get", kind="preset")`. Never choose a creature by a
   host-maintained name table.
9. Apply every confirmed class/subclass feature and complete species/background
   card, then re-read each actor's `derived` values and unresolved rules.
10. Prepare legal spells with `character_spell_prepare(mode="replace_all")`.
    When setup or advancement should finish with a completed long rest, use one
    atomic `campaign_change(action="party_rest")` for all named members; an
    anchored calendar is not a prerequisite. Do not call individual long rests.
11. Only after every campaign resource has activated and all actors have passed
    their completeness checks, record the opening with one `memory_change(action="commit")`:
    include the opening event,
    deterministic-key objective facts, per-actor knowledge only for actual
    witnesses, and the initial snapshot. Supply a fresh `idempotency_key` and the
    current campaign revision. This requires owner/DM authorization.

## Share or migrate structured content

1. Enter Lobby and add the relevant authoring tools. Cross-installation content
   moves only through finalized `sagasmith.content-package` v2 archives. Actor
   cards belong in a `preset` or `module` Pack; there is no public character
   export side door.
2. Before module finalization, call `module_draft(action="edit", operation="actor")` for every cast
   NPC, encounter monster, and pregenerated PC. Use stable content actor ids,
   binding kinds, roles, and actual Scene Atlas keys. Verify
   `module_query(view="actors")`.
3. Review the current draft and evidence, save package decisions through
   `module_draft(edit, operation="package")`, and finalize with explicit Agent
   confirmation. Inspect or transfer the saved archive through
   `content_pack(action="get"|"export", kind="module")`. Do not package progress,
   continuity, ActorKnowledge, branches, random state, or Snapshots.
4. On the target installation, call
   `content_pack(action="import", kind="module")` with exactly one managed artifact or
   allowlisted `.sagasmith-pack` path. Treat returned actor ids as new
   identities. Re-read the imported index, actor bindings, assets, reviews, and
   validation and Agent finalization before play.
5. For an actor library, use `content_pack(action="list"|"get"|"export",
   kind="preset")` and retain its exact artifact ids. Optional rule dependencies
   still need normal reviewed import and campaign activation.
6. For a reviewed Core/Addon Pack, call
   `content_pack(action="export", kind="core_rules"|"addon", payload={campaign_id,
   pack_id, version, ...})`. The exporter replaces local source/chunk ids
   with stable keys and embeds the complete indexed sources. Keep distribution
   private unless the owner supplies an explicit license and attribution. The
   result is a unified `core_rules` archive, not loose rule-pack JSON.
7. On the target installation, call
   `content_pack(action="import", kind="core_rules"|"addon")` with exactly one managed artifact or
   allowlisted `.sagasmith-pack` path and a stable idempotency key. Verify exact
   dependencies. Import stores the finalized definitions but does not activate
   them for a campaign; Owner/DM activation remains separate.
8. If an extension also ships actors, keep them in the Addon package's `actors`
   collection. Publish an independently runnable adventure as a Module package
   and connect it with an exact dependency. Do not create a release manifest.

## Scene preflight and temporary combat map

1. Build a source-grounded participant manifest from the expanded encounter scene.
   Each group has a stable key, role (`combatant`, `reinforcement`, or `optional`),
   required count, canonical campaign actor ids, same-module `source_scene_id`, and
   an exact normalized `source_excerpt`.
2. Call `module_query(view="preflight")`. Do not start while a required actor is
   missing or its whole card is invalid. Dead/0 HP is actor state; missing range,
   ammunition, or semantic settlement disables only that capability. `source_excerpt`
   is an evidence assertion and must be an exact normalized
   substring of the expanded same-module scene; use a verified `module_search` hit
   when needed, never a paraphrase. Review surfaced manual rulings and their
   structured `ruling_requirements` rather than hiding them. The Agent resolves
   entries marked `default_resolver="agent"`; missing/conflicting source and
   player-owned choices keep their declared boundary. `ready=true` authorizes
   entry only: automatic effect settlement and component, targeting, passive, or
   on-hit rulings remain separate.
3. Required `combatant` actors go into initial `participant_ids`.
   `reinforcement` actors must stay out and join later through `combat_join`.
   A source group that must climb, cross, arrive, or otherwise spend time before
   joining is a delayed reinforcement, not an initially distant combatant. Pass
   its exact entry excerpt and canonical actor reports to the full-playthrough
   encounter driver; it queues the actors through public `combat_join`. If the
   source names a later round, pass `--reinforcement-round`; otherwise they enter
   at the next round boundary. They are neither targetable nor acting before
   their queued round.
   Use separate hostile and ally reinforcement reports so source-authored
   rescuers remain friendly without becoming party members. When the source
   uses a semantic arrival condition, have the Agent inspect the live combat and
   submit `--agent-reinforcement-trigger-json` with the exact excerpt, future
   round, decision, and observed-state reason. Keep the semantic judgment at
   the Agent boundary and the actual entry in generic `combat_join`.
4. Call `combat_start` only after preflight succeeds and choose one immutable
   encounter `positioning_mode`. For `grid`, supply or compile a real temporary
   battle map and a coordinate for every participant; missing geometry is an
   input error. For `agent`, send no map or coordinates and let the Agent supply
   structured spatial facts on each relevant action. Load the owner/DM
   `play.combat_control` group for this transition. If
   the selected source chunk explicitly says a participant starts under a
   condition, pass it in that actor's `source_conditions` with
   `duration="encounter"`, the service-returned immutable `source_ref`, and the
   exact excerpt. Apply the group in this one start mutation; do not issue
   per-actor sheet replacements. The server retains the condition through combat
   synchronization and removes the encounter-added condition on `combat_end`.
   If an ordinary removable object is the exact authored cause of that condition,
   the Agent acting as DM may later declare
   `combat_common_action(action="interact_object")` with the object, `interaction`
   `"remove"`, the exact active condition, unchanged `source_ref` and excerpt, and
   a bounded `agent_dm_adjudication` decision and reason. This spends that actor's
   one object interaction for the turn rather than its main action. The server,
   not the driver, verifies ownership and deactivates only the matching source
   condition; the Agent must not patch the sheet or infer that unrelated owners
   of the same condition also ended.
5. Resolve surprise before `combat_start`, but do not turn an adventure's approach
   prerequisite into automatic surprise. A requirement such as "approach carefully
   and without light" only avoids the adventure's automatic alert unless its text
   explicitly promises more. Under 2014 rules, roll each hiding creature's Stealth
   with its canonical card, including armor disadvantage, and compare those results
   against each opposing creature's passive Perception. An opponent that notices
   any approaching threat is not surprised. Determine `surprised` separately for
   every participant; surprise never uses the general group-check rule. Outside
   surprise, when the party is making one collective attempt whose consequence
   applies to everyone, the Agent acting as DM may explicitly call a 2014 group
   ability check. Resolve all participants atomically with
   `character_check(action="group")`; the engine, not the Agent, applies each
   actor card and the "at least half succeed" threshold. Record the comparisons
   and source condition in a campaign event.
6. After `combat_start`, consume `tools/list_changed`, refresh the native list,
   and keep the existing exposure binding. The server phase is now `combat`;
   use `exposure(search/set)` to load `combat.observe`, `combat.turn`, or
   `combat.actions` for an acting player.
   Load `combat.control`, `combat.save`, or `combat.map` only for an owner/DM.
   When the host can send MCP image content, request
   `combat_query(view="render", payload={audience_projection:"party_public"})`
   for the shared table channel. Use `caller` only for the same authorized private
   audience. Send the returned native image content and use its `alt_text`; do not
   reconstruct the image from status JSON.

## Combat turn loop

1. Read `combat_query(view="status")` and
   `combat_query(view="available_actions", actor_id=...)`. Use the returned current
   actor, revision, budgets, conditions, positions, and derived attacks.
   Render on request and after a meaningful spatial change such as combat start,
   a map patch, reinforcement entry, or a tactically important move. Do not render
   after every write. Rendering is read-only and non-blocking: if it fails, continue
   from authoritative combat state and explain that only the image failed. A grid
   render uses server geometry; an Agent-mode render is explicitly nonspatial and
   must never imply coordinates, range, obstruction, or line of sight.
   For automated execution, the Agent must also declare the actor's tactics.
   `--agent-target-priority-json` lists every opponent in exact order and works
   for PCs, allies, and hostiles. `--agent-spell-priority-json` orders supported
   structured spells and their target policies.
   `--agent-weapon-priority-json` orders exact weapon/mode pairs and any selected
   structured Multiattack. These are Agent decisions retained in the report,
   not driver defaults. If none applies, return `pending_ruling` instead of
   selecting an inventory entry or spell by hidden code order.
2. For every attack, use `combat_preflight_attack` immediately before
   `combat_resolve_attack`. Never supply replacement attack bonuses or damage
   formulas. Multiattack is a distinct action choice, not a passive increase to
   `derived.attacks_per_action`. To choose a source statblock Multiattack, pass one
   `derived.multiattack_options` id on the first attack and consume only its
   remaining source-defined entries. Omit the id to choose one ordinary Attack.
   An unstructured/descriptive Multiattack remains an Agent-as-DM adjudication
   boundary but never blocks that ordinary single weapon attack.
   If an exact reviewed passive adds conditional custom damage or another rider,
   keep it on the exact unified actor/content card. On first use, the DM Agent compiles one
   source-bound generic plan and persists it with `content_solution`; the driver
   supplies only generic bindings and the returned fingerprint to
   `combat_choice(action="execute_plan")`. Do not add a dedicated CLI flag or
   apply the rider later with `combat_hp_change`.
   In grid mode, the engine derives targeting, range, cover, visibility,
   adjacency, and geometry from coordinates and the battle map. In agent mode,
   send no coordinates: the Agent supplies the facade's exact attack, movement,
   or area `spatial_facts`, including a stable decision id and reason. Never
   calculate a numeric cover bonus, omit allies from an area decision, or mix
   both positioning modes.
3. When an attack returns `pending_reaction`, read the target's
   `combat_query(view="reactions")`, then use
   `combat_choice(action="resolve_defense")`. Do not roll or apply damage twice.
   Do not end the attacker's turn while this window is pending. Automated Shield
   tactics use the lowest offered slot only when +5 AC changes the hit to a miss;
   otherwise decline. Available Shield should block Magic Missile.
   When a committed hit instead returns `pending_on_hit_ruling_id`, the Agent
   reads the exact card and bounded source context. Query or compile its
   `content_solution`, then settle the paid window through
   `combat_choice(action="execute_plan")`. `on_hit_ruling` only dismisses an
   exact-source no-op after Agent review. Never classify by creature name, parse
   prose in the driver, silently dismiss a real rider, or repeat the hit.
4. Resolve movement with `combat_movement`, checks with `combat_check`, common
   actions with `combat_common_action`, spells with `combat_cast_spell`, activities
   with `combat_use_activity`, and damage/healing with `combat_hp_change`.
   Standard structured areas use their locked standard implementation. A custom
   creature area uses its persisted source-bound plan with Agent-supplied generic
   target bindings; do not add a named area action to the driver or server.
   A locked standard card that lacks both a registered generic mechanic and a
   persisted exact-source content clause, and therefore reports
   `semantic_solution.status="engine_implementation_required"`, must stop before
   payment and be implemented in the engine. A build-time clause may settle only
   exact spell/item/creature-specific prose; it cannot replace action economy,
   payment, rolls, damage, or timing. Do not require every card-specific outcome
   to become an automatic plan. Import or review must nevertheless attach an
   exact-source direct Agent-ruling contract when a typed plan is unavailable.
   Every public actor-card write path performs
   this settlement before persistence, including direct creation, build,
   template instantiation, sheet replacement, and inventory changes. Therefore
   `semantic_solution.status="content_authoring_required"` is an invalid
   data invariant failure, not a normal workflow or a prompt: stop, return to
   Lobby, migrate or reimport the card, and re-run preflight. Runtime never
   authors that contract. A genuinely one-off
   descriptive activity, unstructured spell, or scene procedure with printed
   save damage remains a two-call recoverable transaction with one immutable
   semantic identity. Before paying, place the complete canonical
   `agent_ruling_commitment` in that action's declaration/payload. Settle it with
   `combat_hp_change(action="save_damage")` using the identical target order,
   source card, save/DC, damage terms, exact mechanics excerpt, and active-scene
   Agent ruling. The server alone rolls one shared damage result, rolls every
   target save, rounds half damage down, and applies all sheets atomically.
   Never roll the damage in the driver, divide it there, or follow the paid
   activity with separate per-target damage calls.
   Do not promote parseable prose to a standard engine rule without its complete
   timing transaction. False Appearance remains a descriptive Agent ruling;
   Legendary Resistance remains an Agent decision until a failed-save window
   can both replace the outcome and spend the limited use atomically.
   When a predeclared Agent object interaction ends an exact encounter-source
   condition, execute it before choosing the actor's action, re-read combat and
   character state, then continue the same turn with the remaining main-action
   budget. Do not encode the object, creature, or source phrase as driver logic.
   After movement, settle every returned opportunity-reaction window before the
   next action. A rescue move can damage or incapacitate the rescuer before a
   Medicine attempt, so re-read both actor cards after the reaction.
   For a structured multi-attack spell, cast once and keep its returned
   `resolution_id`. Resolve each attack separately with `combat_resolve_attack` and
   `action.spell_resolution_id`, refreshing `expected_revision` after every write.
   The cast spends its action and slot once; the individual attacks spend neither.
   Resolve any owned Shield window before the next attack. Do not end the caster's
   turn or the encounter until `remaining_attacks` is zero.
   Automated tactics must read the current prepared/known spell projection, not
   every spellbook card. Select the lowest available legal slot; when only a
   higher slot remains, pass that `cast_level` and preserve the spell's scaling.
5. A source offer such as “10 gp grants advantage on DC 15 Persuasion” requires
   the stated payment/offer fact and
   `combat_check(action="improvise", ability="persuasion", dc=15)`. Only on success
   call `combat_join` through `combat.control`; the canonical reinforcement
   appears at the next round boundary with a full turn.
6. At the start of a death-save combatant's turn, if its card is at 0 HP and has
   neither Dead nor Stable, require
   `combat_query(view="available_actions", actor_id=...) == ["death_save"]`, then
   call `combat_check(kind="death_save")` without an `ability` or target. Do not
   require or write a synthetic Dying condition. Refresh state before continuing;
   a revived actor may still act, while a pending result may only end its turn.
7. End each completed turn with `combat_end_turn`, using the latest revision and a
   fresh idempotency key. Refresh status after every write. A committed attack,
   spell, movement, common action, or death save does not itself end the turn
   unless the returned authoritative status has already advanced it. Repair a
   rejected, still-intended action before ending the turn; do not silently turn
   a missing `weapon_id`, malformed allocation, or other payload error into a
   deliberate pass.
8. Call `combat_end` through owner/DM `combat.control` with a structured outcome
   when the encounter is actually over. It returns unfinished 0-HP actors in
   `post_combat_recovery` and moves the campaign to `play`; consume
   `tools/list_changed`, refresh the native list, and use
   `exposure(search/set)` on the existing binding before calling
   `character_state_change(death_save|stabilize)` until each is settled.
9. After combat, a Stable actor at 0 HP cannot rest. If the scene permits the party
   to wait, call `campaign_change(action="stable_recovery")` once with every
   simultaneously waiting Stable actor; the engine rolls each `1d4`-hour delay,
   advances the campaign timeline by the longest wait, and restores 1 HP. Do not
   patch HP, supply a roll, or run separate per-character clocks.
   When conscious and above 0 HP, clear the retained Prone condition only with
   `character_state_change(action="stand")`.

## Source-bound level advancement

1. Read the campaign's explicit advancement mode. For a milestone module, verify
   the exact trigger and do not synthesize encounter XP. For XP mode, atomically
   apply the reviewed source-bound PC awards with
   `campaign_change(action="experience_award")`, fresh campaign/actor revisions,
   and a fresh idempotency key. It does not auto-level; use its returned
   `eligible` status. Add a campaign event with the same exact source reference.
2. Settle the trigger before entering a later sourced scene. End combat, switch
   to `lobby`, re-read the actor revision, and call
   `character_state_change(action="level_advance")`. This advances an exact
   2014 or 2024 single-class actor by one level; multiclass remains a stop
   condition. Use the fixed HP value unless
   the table selected rolled HP; the engine owns that roll, so never supply a roll
   value. XP mode rejects advancement below its cumulative threshold.
3. Inspect `advancement.follow_up`. Apply its base-class and existing-subclass
   feature ids through `character_content_apply`. Resolve a listed subclass choice
   with the player, apply it, then query the catalog again for subclass features.
4. Select only the reported number of legal cantrips, prepared-list additions,
   known spells, or spellbook spells from the active edition's catalog. Apply
   Wizard additions as `method: spellbook`. A prepared-class
   `method: class_prepared` selection hydrates a legal card only and must remain
   unprepared until selected through the rest workflow.
5. Do not change a prepared list during advancement. Re-read the actor and
   verify all resources and derived values; submit any revised complete list
   through the next completed `campaign_change(action="party_rest")`.
6. Create a snapshot, switch back to `play`, consume `tools/list_changed`, and
   use `exposure(search/set)` on the existing binding to load the needed Play
   tools. Stop if the runtime reports unsupported multiclass state or any
   catalog item remains unresolved.

## Feature settlement examples

- For 2014 or 2024 Sneak Attack, declare `use_sneak_attack: true` in preflight and resolve;
  let the engine validate eligibility and its once-per-turn token.
- For the canonical 2014 or 2024 Action Surge feature id, call `combat_use_activity` on
  the Fighter's turn. The committed result consumes its card use and grants one
  current-turn `extra_action`; never patch the turn budget, and never carry an
  unused extra action into a later turn.
- For 2014 or 2024 Second Wind, call `combat_use_activity` with its exact
  edition-bound feature id. The same
  transaction pays its bonus action and use, rolls the source formula, and applies
  clamped healing. Never roll it externally or follow it with `combat_hp_change`.
- For healing from a levelled spell, send rolled base `amount`, `source_actor_id`,
  `spell_id`, and actual `spell_level`; do not pre-add source-linked modifiers.
- Halfling Lucky needs no extra write. Preserve returned reroll evidence and
  narrate only the selected final d20.
- For 2024 Heroic Inspiration, immediately reroll exactly one recorded die with
  `character_check(action="reroll", resolution_id=..., roll_index=...,
  expected_original_roll=...)`. The replacement is mandatory; never replay the
  whole check or keep the better result.
- For 2024 Divine Spark, select heal or damage in one Channel Divinity activity
  call. The engine owns level scaling, Wisdom, the Constitution save, half
  damage, target HP, and the resource receipt.
- For Turn Undead, use the edition-bound Channel Divinity card. In 2024 a failed
  save produces Frightened plus Incapacitated and depends on the source remaining
  alive and capable; `sear_undead=true` is legal only with the source-bound level
  5 feature. In 2014 use the Turned action/movement procedure instead.
- Preserve Life uses one complete allocation. Apply the Undead/Construct
  exclusion only to the 2014 card; the 2024 text has no such exclusion.
- Cunning Strike is currently an explicit engine-implementation gate, not an
  Agent permission to subtract Sneak Attack dice or patch post-hit conditions.

## Rulebook to executable optional pack

1. Load `lobby.rules`; run `rulebook_draft` in order:
   `start` -> repeated `get/evidence/edit` -> `finalize`. Core+D&D own source
   normalization, the first mechanical candidates, deterministic repair, and
   validation. The Agent may repeatedly find, add, reopen, split, merge, include,
   exclude, and replace source-bound candidates before finalization.
2. Review exact chunks/pages, every issue, and the current revision. One-book
   decisions remain in the draft/Pack edit history; do not add them as Core or
   D&D parser heuristics. Candidate extraction is not approval, and missing or
   conflicting evidence is never permission to invent content.
3. Finalize only after reviewing the complete current draft, resolving actual
   blockers, and explicitly confirming publication. Finalization validates and
   stores one immutable `core_rules` or `addon` Pack; corrections use a new
   draft/version.
4. Inspect or activate through `content_pack(get|activate,
   kind="core_rules"|"addon")`. Activation requires explicit campaign-owner/DM
   approval and a fresh campaign revision; finalization never implies it.
5. Settle checks with `character_check` in play or `combat_check` in combat. For
   a 2014 opposed check, use one atomic `character_check(action="contest")` call instead of
   inventing a DC or comparing client-side rolls. Then audit
   `campaign_rules(action="receipts")`.

## Post-scene continuity and save

Load owner/DM `play.scene_control` before the following chronology and save
writes. A player Agent uses `play.scene` and receives only audience-safe events,
continuity, and its authorized actor knowledge.

1. Build one `memory_change(action="commit")` payload from the structured `combat_end` or scene
   outcome. Include exactly one event, accepted objective fact changes, each
   affected actor's knowledge changes, and the snapshot request.
2. Use `audience_scope="actor"` and owner-scoped ActorKnowledge for a witnessed
   subset. Use `party` only when every party actor may know the event. Never infer
   actor knowledge from a world fact.
3. Give objective facts deterministic keys such as
   `location:cellar:door-state`. Existing keys and knowledge revisions require
   their current `expected_revision_id`; the commit itself requires a fresh
   `idempotency_key` and the current campaign revision.
4. Submit once. If any write fails, refresh all affected revisions and rebuild the
   entire unit; do not retry only the missing tail or claim a partial save.
5. Verify with `snapshot_query(view="verify")` and inspect
   `snapshot_query(view="lineage")`.

## Restore, branches, and audit recovery

1. Before restore call `snapshot_query(view="verify")` and inspect lineage.
2. Explain that `snapshot_restore` forks history; perform it with current guards.
3. Verify the new head, then refresh campaign, characters, party, module progress,
   events, and each actor's continuity context. Discard pre-restore assumptions.
4. Use `branch_query(view="compare")` before discussing alternate timelines.
   There is no implicit merge of world facts or actor knowledge.
5. `state_revision(action="history")` inspects audited mutation groups.
   `state_revision(action="undo" | "redo")` uses the latest history sequence and
   does not delete snapshots. Mixed continuity groups containing events, facts,
   ActorKnowledge, progress, or receipts are non-reversible; use a verified
   snapshot or branch recovery when the server reports that boundary.

For destructive or stateful regression, enter `lobby`, create and verify a source
checkpoint, then create-and-checkout a disposable branch. Return to `play`, refresh
the native list and use `exposure(search/set)` on the existing binding, run the scene/combat workflow, record actor-scoped knowledge and a full
snapshot, then return through `lobby`. The phase change dirties the disposable
branch, so create and verify a second lobby checkpoint before checkout; otherwise
the clean-branch guard must reject the switch. Checkout the source branch. Refresh
the native list and use `exposure(search/set)` after every phase or branch change;
call `open` only for a genuinely different campaign/principal binding. Verify source HP/resources and query
each actor's knowledge on both branches; a branch comparison must show the test
memory and subjective knowledge only on the disposable branch. There is no merge.
