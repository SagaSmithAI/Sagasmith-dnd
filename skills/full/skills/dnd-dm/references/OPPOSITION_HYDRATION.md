# Source-backed opposition hydration

Use this workflow when a campaign preflight or regression reports missing
source-backed opposition, or when a narrative actor must become mechanical.
Do not treat that gap alone as proof that the active Pack needs a new review.

## Establish the source path

1. Move to `lobby` before authoring or actor creation. Consume
   `tools/list_changed`, refresh the native list, and use
   `exposure(search/set)` to load `rule_search`, `rule_seed_status`,
   `rulebook_draft`, `character_create_from`, and module authoring tools only as
   needed.
2. Re-read `character_query(view="list")`. Reuse every existing actor whose
   returned `statblock.source_identity` matches the required printed card, and
   create only the exact shortfall.
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

## Hydrate module-only opposition

1. When the exact creature exists only in the active module, inspect the
   current Pack's immutable content reviews. Use
   `character_create_from(mode="module_statblock")` only with the returned
   `review_id`, and pass the exact printed card as `payload.source_identity`.
2. If a finalized Pack lacks the required review, create an explicit new
   draft/version from the same managed source. Select an explicit version greater
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
   content review. Call `module_query(view="assets")` for the draft module and
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

Restore the entry phase after preparation, consume the native tool-list change,
refresh the list, and use `exposure(search/set)` for the next phase. Stop for
external input only after the exact rule, reviewed rulebook, and module-review
paths are absent, contradictory, or unavailable.
