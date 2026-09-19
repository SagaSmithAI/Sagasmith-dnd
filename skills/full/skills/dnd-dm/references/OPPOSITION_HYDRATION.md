# Source-backed opposition hydration

Use this workflow when a campaign preflight or regression reports missing
source-backed opposition, or when a narrative actor must become mechanical.
Do not treat that gap alone as proof that the active Pack needs a new review.

## Establish the source path

1. Move to `lobby` before authoring or actor creation. Use `rule_search`,
   `rule_seed_status`, `rulebook_draft`, `character_create_from`, and module
   authoring tools from the public catalog only as needed.
2. Re-read `character_query(view="list")`. A matching
   `statblock.source_identity` establishes the mechanical card, not the identity
   of an individual creature. Reuse an actor only when the encounter record or
   established campaign events identify that same individual at this location.
   Otherwise create distinct instances for the source encounter's occupants,
   reusing the reviewed card rather than the actor ids. Give each instance a
   location-specific name and retain its actor id in the participant manifest.
   Never move, heal, resurrect, or duplicate an existing individual merely to
   fill a new encounter's count. A recurring creature retains its actual wounds
   and other state; a relocation needs established narrative evidence.
3. Search the exact printed creature identity first with only `campaign_id`,
   `query`, and optional `top_k`. Campaign binding already scopes the default
   edition, locale, and enabled sources. If a filtered search misses, retry this
   minimal shape once before starting any draft.
4. Use `rule_seed_status` only when source-level inventory is necessary. A
   returned rule hit `source_id`, or the matching source inventory `id`, is the
   only valid rule-source id. Module, Pack, scene, and document ids are different
   namespaces.
5. Keep the two source roles separate. `participant_manifest.source_excerpt`
   cites the encounter passage that establishes identity, count, role, or
   variant. A content review cites the creature's mechanical statblock. Those
   passages normally differ; wording or punctuation differences between them do
   not prove extraction corruption and never justify a duplicate content
   review. Copy the route's exact managed encounter excerpt into the participant
   manifest and bind the already reviewed actor separately. Only when the
   active Pack's copy of that same encounter passage has demonstrable mojibake,
   replacement characters, omissions, or reordered text relative to the
   managed source should you create a new draft/version to repair that bounded
   scene. Re-read both exact passages before deciding; a failed or stale combat
   manifest is not evidence of Pack corruption. Keep exact replacements and
   evidence with the Pack; never weaken the route comparison or add a
   book-specific parser heuristic.

## Hydrate from a canonical rule source

1. Treat every returned `source_id` and `chunk_id` as an opaque exact value.
   Copy complete ids character-for-character from one latest successful
   `rule_search` result. Never retype, normalize, splice, or reconstruct them.
2. Call `character_create_from(mode="statblock")` with that exact `source_id`,
   selected evidence in `payload.chunk_ids`, and the exact printed identity in
   `payload.source_statblock_name`. There is no `exact_chunks` field. Give
   repeated instances distinct `payload.name` values.
   Include the complete same-creature source subtree, including Actions,
   reactions, and other subordinate sections; a search hit containing only
   attributes and passive traits is not a complete card. If creation reports
   missing chunk ids, read and include those exact same-source chunks before
   retrying. After creation, compare the printed attacks and activities with
   the returned mechanics. A valid actor with no weapon attacks is not proof
   that the source creature has no attacks.
3. If creation reports a source/chunk mismatch, search again and compare the
   submitted JSON to one result. A one-character mismatch is Agent input error,
   not missing evidence and not grounds to weaken validation.
4. If an exact localized hit is readable but not mechanically hydratable, do
   not hand-copy its numbers and do not move a standard creature into a
   module-specific review. When current module evidence also prints the
   canonical English identity, make one explicit same-edition English lookup,
   for example:

   ```json
   {
     "campaign_id": "<campaign id>",
     "query": "<exact canonical English identity>",
     "filters": {"edition": "2014", "locale": "en"}
   }
   ```

   Verify the returned heading is the same creature, then hydrate only from
   that one English result's exact `source_id` and `chunk_ids`. This selects an
   enabled canonical source; it does not permit translation or a remembered
   substitute. If no mechanically usable equivalent exists, retain the source
   diagnostic and use the reviewed rulebook-draft lifecycle.
5. If module evidence applies a narrow instance change to that canonical card,
   call `module_search` with a distinctive exact heading or printed phrase,
   then `module_expand` on the selected hit. Copy the returned exact managed
   chunk id into `variant.source_ref` as `module-chunk:<id>`, or cite an
   immutable returned `module-review:<id>`. A route/scenario label, heading,
   page number, scene id, or remembered token is not a chunk id. Include only
   the printed override, such as `creature_type`; do not copy the entire card
   into Pack data or use a generic sheet patch.
6. Re-read every created actor and require `statblock.source_identity` to match
   the intended source card.
   Also compare the instance against every explicit encounter override. For
   example, `variant.damage_resistances` and `variant.darkvision_ft` carry
   printed resistance and vision changes; the base creature does not supply
   them automatically. Setting `variant.current_hit_points=0` alone does not
   establish stable/unconscious conditions. Use the public condition/recovery
   operations to represent the exact sourced state, then verify it before
   actions or conversation. Do not manufacture damage to obtain that state.
   An unconscious captive cannot provide clues merely because the DM has read
   the source: establish recovery and an actual published native NPC response
   before recording their testimony as party knowledge.

For repeated occupants, first create and verify one instance from the selected
card. Only after that succeeds, create the exact remaining shortfall. Do not
fan out an unverified transcription or parser path into many identical failures.
A shared source failure applies to all copies; repair that source path once.
Keep the campaign in `lobby` until all required creation calls and preflight
finish successfully. Switching to `play` depends on those results and must not
run concurrently with actor creation. After a partial failure, list existing
instances and retry only missing ones using their original operation keys.

## Hydrate module-only opposition

1. When the exact creature exists only in the active module, inspect the
   current Pack's immutable content reviews. Use
   `character_create_from(mode="module_statblock")` only with the returned
   `review_id`, and pass the card's exact printed creature name as the string
   `payload.source_identity` (for example `"Redbrand Ruffian"`), not the card
   text or a source-reference object. Use `payload.name` for the distinct
   instance name. A review id is never a source chunk id. The
   `reviewed_rule_statblock` mode instead requires a rulebook job; it is not
   the module-review route.
2. For an already installed module missing a review, use
   `module_query(view="candidates", payload={module_id, query:"<printed name>"})`.
   Read its managed chunks or request the actual page with
   `module_draft(action="evidence", payload={module_id, kind:"page", page_number:N})`.
   Submit `module_draft(action="edit", payload={module_id, operation:"content", ...})`
   using the returned review contract and evidence-bound transcription. Installed
   module content/statblock review accepts `module_id` directly without a draft
   job. Read back `module_query(view="content")`, then materialize its `review_id`.
   Do not create a draft or search for an editable handle solely for this review.
   `evidence(kind="chunks")` is a draft route; installed source chunks are read
   through `module_expand` using the candidate's exact chunk ids.

   If the source itself needs revision or a new distributable Pack is required,
   create an explicit new draft/version from the same managed source. Select an
   explicit version greater
   than the active Pack; never reuse its version or rely on the first-release
   default. Add only the evidence-backed
   missing review, re-read it, finalize it, import the new artifact, and
   require the import to return `skipped=false`. Activate only the module id
   returned by that import. If it returns `skipped=true`, stop on the
   identity/version conflict and do not reactivate the old module. Never edit a finalized
   Pack in place or guess a review id.
   Before `start`, list the public module-draft handles and resume the newest
   matching unfinished job. If duplicate handles already exist, choose one
   matching handle and create no more. Retain its job id and editable module id
   through the whole lifecycle. After a successful content edit, verify the
   stored review with `module_query(view="content")` on that editable module.
   `module_draft(get, view="package")` reports import-job/package decisions; an
   empty content-like field there is not evidence that the module content review
   was lost and is not permission to call `start` again.
   For an image-only card with no text candidate, `content_key` is the
   Pack-local stable slot selected by the Agent from the exact printed identity
   (lowercase ASCII words joined by hyphens, such as `master-of-souls`), not an
   opaque server id. Keep the same key across OCR/Agent-fill retries and still
   cite the exact managed page and scene.
   Submit the evidence-bound transcription once without `agent_fill`. When the
   response has `requires_agent_fill=true`, treat it as a read-only preview:
   copy `validation.agent_fill_requirements`, make the source-semantic decision
   yourself, and resubmit the same review with the completed `agent_fill`.
   Do not query the entire draft to rediscover that bounded contract, and do
   not trust a parser-proposed Multiattack composition as authoritative.
   Follow the returned `submission_schema`. A structured decision has this
   nesting (copy the actual activity, excerpt, and weapon ids from the preview;
   the example does not choose which attacks the source means):

   ```json
   {
     "agent_fill": {
       "multiattack_options": [{
         "activity_id": "<returned activity_id>",
         "source_excerpt": "<returned source_excerpt>",
         "reason": "<Agent's source-based reason>",
         "resolution": "structured",
         "options": [{
           "id": "<lowercase-option-slug>",
           "attacks": [{
             "weapon_id": "<returned weapon_id>",
             "attack_mode": "melee",
             "count": 1
           }]
         }]
       }]
     }
   }
   ```

   Keep unrelated open rulings out of this bounded fill; use an evidence-bound
   resolution plan or later ruling boundary when that separate mechanism is
   actually exercised. An `img_*` id returned by page rendering identifies a
   delivered media artifact, not a managed `source_asset_id`; omit it from the
   content review. Call `module_query(view="assets")` for the module being reviewed and
   select the PDF asset whose checksum exactly matches the managed source; its
   returned `id` is the valid `source_asset_id`. Bind an image-only review with
   that asset id plus the exact managed page. Use source chunks as additional
   evidence only when they actually contain the reviewed card; an encounter
   paragraph naming the creature is not the card's mechanical transcription.
3. An ending entry, dossier, encounter label, or `module_set_progress` value is
   narrative metadata and never substitutes for a mechanical content review.

## Verify and return to play

Run `module_query(view="preflight")`. Its `ready`, `card_valid`,
`hard_blockers`, and `disabled_capabilities` fields are the combat gate. A
usable attack card is not wholly blocked merely because unrelated source-backed
spells are disabled; retain those diagnostics and avoid only the unavailable
capability. Repair first when the whole card is invalid, the intended action is
disabled, or indispensable evidence is absent or conflicting.

Restore the entry phase after preparation and re-read authoritative state.
Use the Host-selected tools for that phase. Stop for
external input only after the exact rule, reviewed rulebook, and module-review
paths are absent, contradictory, or unavailable.
