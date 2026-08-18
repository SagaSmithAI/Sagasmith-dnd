# Module drafts and packs

Call `module_draft(start)` with a managed module book. Core+D&D stage, inspect,
validate, and mechanically import an inactive editable module workspace in one
operation. A failed validation remains a draft; inspect its page with
`module_draft(evidence)`, submit a checksum-bound
`module_draft(edit, operation="source_text")`, then use `operation="advance"`.
Retain the returned `result.job_id` and `result.module_id` from the receipt and
reuse them for the rest of this draft lifecycle. Do not start another draft or
guess either identifier when a large inspection payload is persisted by the
host.

After a process/session restart, recover authoring state before calling
`start`: call `module_draft(action="get")` with no payload to list the campaign's
public draft handles. Resume an unfinished matching job with
`module_draft(action="get", payload={"job_id":"..."})`, matching the managed
artifact checksum/source identity and intended Pack revision. Do not assume an
empty conversational context means no draft exists. The list receipt explicitly
declares `order="newest_first"`; when several unfinished handles have the same
artifact checksum, source identity, and intended revision, resume the first
matching handle. If those identities or the intended Pack revision conflict,
stop on that genuine source/version ambiguity instead of silently creating
another draft.

Before repairing or finalizing saved Pack decisions, use the bounded
`module_draft(action="get", payload={"job_id":"...","view":"package"})`
read. It returns the current draft handle and `pack_draft` without the large
mechanical inspection payload. It also returns a bounded `finalized_package`
summary when that job has already been finalized; import its exact `artifact`
instead of starting another draft or repeating review. Preserve the whole
returned decision object and replace only fields justified by current evidence;
in particular, copy every `source_ref` verbatim from `module_draft(evidence)`
rather than substituting a coverage-fixture hash or reconstructing a partial
manifest.

The package view is an import-job/package-decision projection; it is not the
authority for content reviews stored on the editable module. After a successful
`module_draft(edit, operation="content")`, verify that review with
`module_query(view="content", module_id=<returned editable module id>)`. An empty
`statblock_reviews` or content-like field in the package view does not mean the
content edit was lost and never authorizes another `start`. Continue the same
job and editable module through package edit and finalization.

When the previous cycle already finalized the intended Pack and only import or
activation remains, inspect every matching source job from that list until the
job with a non-empty `finalized_package` is found, then import that exact
artifact. Do not select a newer unfinished duplicate, repeat editorial review,
or start another draft merely because the finalized job is not first in
`newest_first` order.
The compact list now exposes `finalized_artifact` directly. For matching source
identity/checksum, a non-empty value takes precedence over every newer
`resumable=true` duplicate; pass that exact artifact to `content_pack(import)`.

When a finalized Pack needs an explicit new revision from the same managed
source, `start` returns a fresh editable `job_id`, but its mechanical first pass
may reuse the same draft `module_id` and report an inner import as `skipped`.
That reuse does not finalize or block the new job. Re-read the returned fresh
`job_id`; `status="editing"` without that job's own `finalized_package` is the
authority to continue edits. Never retry an edit against the older finalized
job merely because both jobs reference the same mechanically imported module.
Choose and persist an explicit version greater than the active finalized Pack
before finalizing this revision; never rely on the `1.0.0` default for a changed
Pack. After `content_pack(import)`, require `skipped=false` and retain the module
id returned by that exact import. If import reports `skipped=true`, stop on the
Pack identity/version conflict: do not activate the old module id or claim that
the new review entered runtime.

Before activation, retrieve draft evidence only through these exact shapes:

```json
{"action":"evidence","payload":{"job_id":"<job id>","kind":"chunks","limit":100}}
{"action":"evidence","payload":{"job_id":"<job id>","kind":"chunks","query":"<one short exact source phrase>","limit":20}}
{"action":"evidence","payload":{"job_id":"<job id>","kind":"page","page_number":2,"include_ocr_text":true}}
```

There are no `top_k`, `queries`, `pages`, or `scene_keys` fields on this action.
Do not call `module_search` with the editable draft id: search/expand addresses
an imported active Pack revision after finalization. For Pack decisions, copy a
returned chunk's complete `source_ref` object verbatim. A page result includes
`citation_candidates`; choose a candidate whose excerpt/heading supports the
reviewed fact and copy its `source_ref` verbatim. Never construct a reference
from the page transcription checksum, image checksum, editable module id, or a
separately copied `content_hash`.
These draft citations are authoring-only. After finalizing, importing, and
activating the Pack, never reuse a draft `source_ref` or its portable chunk key
in campaign progress, a playthrough manifest, continuity, or mechanics. Search
the active runtime module with `module_search`, select a hit, and copy the exact
runtime `source_ref` returned by `module_expand`.

Use `module_draft(edit)` for reviewed content, statblocks, assets, actor
bindings, and optional combat-grid templates. Extract party range, levels, advancement, endings, scenes,
encounters, actors, items, maps, clues, and exact references. Prose is not
executable; incomplete editorial coverage remains visible advice unless it
causes structural corruption, missing/conflicting source identity, explicit
test failure, or compilation failure.

For a reusable combat grid, first inspect chunk or managed-page evidence, then
submit `operation="combat_grid"` with `change="upsert"`, the exact draft
`scene_id`, and the complete canonical candidate. It supports only stable
id/title/location, square five-foot grid, bounds, blocked/difficult cells,
deployment zones, optional managed-image `map_asset_key`, and copied draft
`source_refs`. Re-read the scene and validate before finalization. Use
`change="remove"` plus evidence-bound refs to remove one candidate. Every edit
requires the current import-job revision and a fresh idempotency key. Never add
a parallel map-authoring facade, participant actor ids, inferred image
topology, walls, line of sight, cover, elevation, or module-specific mechanics.
After finalization, create a new draft/version for any correction.

When a route must instantiate a creature or NPC whose exact statblock exists
only in the module, a manifest `content_summary`, narrative dossier, or catalog
label is not a mechanical card. Before finalization, submit an evidence-backed
`operation="content"` or `operation="statblock"` edit for that source slot,
then re-read the draft and retain the returned content `review_id`. Do not
finalize a route-required opposition repair until the review is present and can
be consumed later by `character_create_from(mode="module_statblock")`. On that
creation call, pass the exact printed card name as `payload.source_identity` and
verify the returned `statblock.source_identity`. `payload.name` is the runtime
instance name and may differ; repeated creatures need distinct stable instance
names. Do not pass ignored pseudo-evidence fields such as `content_id`, `role`,
`scene_id`, `source_ref`, or `source_excerpt` to this mode.
Before choosing a name, page, or source slot, call
`module_query(view="candidates", payload={"module_id":"<editable draft module id>"})`.
A `review_ready` candidate supplies the exact `scene_id`, `content_key`,
`normalized_content`, `source_chunk_ids`, edition content kind, validation, and
Agent-fill requirements for `operation="content"`; copy those values rather
than rebuilding the card. A blocked 2014 candidate may route to
`operation="statblock"` using its exact candidate name/scene/page, followed by
the returned recovery/validation contract. A creature mentioned in encounter
narrative does not prove that its statblock is printed on that narrative page.
`content_key` is a Pack-local stable source-slot key, not an opaque database id.
When an exact managed image page contains a complete printed card but no text
candidate exists, the Agent creates the key deterministically from that exact
printed identity using lowercase ASCII words joined by hyphens (for example,
`Master of Souls` -> `master-of-souls`) and keeps it unchanged across retries.
This names the reviewed Pack slot; it does not replace the required managed
page, scene, printed name, OCR validation, or fresh idempotency key.
If no candidate or rendered page contains that card, use another source-backed
route opponent or discover the exact enabled standard rule source; never guess
a statblock page from an encounter mention.
Use the current facade shapes, with request controls outside `payload`:

```json
{"action":"edit","payload":{"job_id":"<job id>","operation":"statblock","scene_id":"<draft scene id>","content_key":"<stable source slot key>","name":"<printed name>","page_number":59,"source_asset_id":"<only when returned by draft evidence>"},"expected_revision":3,"idempotency_key":"..."}
{"action":"edit","payload":{"job_id":"<job id>","operation":"content","scene_id":"<draft scene id>","content_key":"<stable source slot key>","normalized_content":"<complete source statblock as one text string>","observation":"<evidence-backed review note>","source_chunk_ids":["<returned draft chunk id>"],"content_kind":"dnd5e_2014_statblock"},"expected_revision":3,"idempotency_key":"..."}
{"action":"edit","payload":{"job_id":"<job id>","operation":"content","scene_id":"<draft scene id>","content_key":"<stable source slot key>","normalized_content":"<complete source statblock as one text string>","observation":"<evidence-backed visual review note>","source_asset_id":"<returned managed asset id>","page_number":59,"content_kind":"dnd5e_2014_statblock"},"expected_revision":3,"idempotency_key":"..."}
```

There are no `candidate_id`, `review_mode`, `source_ref`, or `source_excerpt`
fields on these edit operations. A content review requires exactly one evidence
mode: either `source_chunk_ids`, or the paired `source_asset_id` plus
`page_number`. Never send both modes in one request. Copy the selected evidence
fields and draft `scene_id` from the current job/evidence response; do not reuse
active runtime scene ids or construct authoring identifiers.
`normalized_content` is the complete evidence-backed statblock text, not a JSON
card. Use the edition-specific content kind returned by the current facade
(`dnd5e_2014_statblock` for a 2014 campaign or `dnd5e_2024_statblock` for a
2024 campaign), never the generic word `statblock`. Omit `agent_fill` on the
first request. Only when the response reports `requires_agent_fill=true`, copy
its exact `agent_fill_requirements` and submit the required semantic declaration
with a fresh idempotency key; an empty object and transcription-repair fields
such as `source_text`, `abilities`, or `ocr_corrections` are not semantic fills.

When `operation="statblock"` returns a partial `recovery.normalized_content`,
use that returned canonical Markdown as the base for any evidence-backed
`operation="content"` completion. Preserve its parser structure instead of
rewriting it as plain text or an ad-hoc JSON card. In particular, a 2014 card
uses a Markdown creature heading, italic identity, bold field labels, the
six-column ability table, a real Markdown action heading, and bold-italic
action names with italic attack/Hit markers, for example:

```markdown
# PRINTED CREATURE NAME

*Medium humanoid, neutral evil*

**Armor Class** 13
**Hit Points** 22 (4d8 + 4)
**Speed** 30 ft.

| STR | DEX | CON | INT | WIS | CHA |
|---:|---:|---:|---:|---:|---:|
| 14 (+2) | 12 (+1) | 12 (+1) | 8 (-1) | 12 (+1) | 8 (-1) |

## Actions

***Printed Action.*** *Melee Weapon Attack:* +4 to hit, reach 5 ft., one target.
*Hit:* 6 (1d8 + 2) damage.
```

Copy every completed fact from current draft evidence and retain the exact
source spelling in the stored transcription. Do not turn `## Actions` into a
bold paragraph, change `***Action.***` to `**Action.**`, or remove the italic
attack markers: those transformations make a mechanically complete-looking
card non-executable. If the exact evidence cannot complete a truncated action
or spell, keep the opponent blocked rather than inventing it.

Save manifest, catalogs, narrative, dependencies, and metadata with
`module_draft(edit, operation="package")`. Each write enters the Pack edit
history, so one-book Agent decisions travel with the draft instead of becoming
parser heuristics. Reserve revision/idempotency requirements for durable start
and finalization boundaries, not every fine edit.

The public request shape puts each decision field directly beside `operation`
inside `payload`; do not add a `package` wrapper and do not put request-control
fields inside `payload`. The only package decision fields are `manifest`,
`catalogs`, `narrative`, `dependencies`, `metadata`, and `version`. Each supplied
field is a complete replacement, not a deep patch. Therefore a manifest edit
must preserve or submit the full reviewed manifest: `title`, `classification`,
`compatibility`, `play_profile`, `continuity`, `activation`, and
`content_summary`. Its structural request is
`module_draft(action="edit", payload={job_id, operation:"package", manifest:{title, classification, compatibility, play_profile, continuity, activation, content_summary}}, expected_revision=..., idempotency_key=...)`.
Use the current module schema inside that manifest: `classification` is the
source-reviewed `adventure` or `campaign`; `compatibility` contains `editions`
and `required_capabilities`; `continuity` contains `series_id`, `order`,
`continues_from`, and `state_policy`; and `activation` contains `mode` and
`default_active`. Omit `catalogs` or `narrative` when there is no reviewed
structured decision to replace. If supplied, every catalog value is an array,
and `narrative` contains the two arrays `dossiers` and `endings`; never replace
those structures with opening/ending prose strings. A source-defined legal
ending belongs as a cited structured entry in `narrative.endings`.

Build `play_profile` with `party_size={minimum, maximum, source_refs}`,
`starting_level={value, source_refs}`, `expected_end_level={value, source_refs}`,
`advancement={modes, recommended, source_refs}`, and
`pregenerated_characters={available, applicability, source_refs}`. Obtain a
current chunk-evidence receipt (or a page `citation_candidates` entry). Before
the package edit, repeat each selected chunk lookup with its exact heading or a
short exact excerpt and `limit=1`; if that does not return the intended unique
chunk, refine the query instead of carrying a reference out of a broad or
offloaded result. Reuse each single-result `source_ref` verbatim as
`{source_key, page, chunk_hash, note}`; never retype, splice, or infer its
checksum. Re-read the draft after the edit and verify the complete stored
decision before finalization.

After reviewing the current draft, its issues, evidence, imported scenes, and
saved package decisions, call `module_draft(finalize)` with
`payload={job_id, pack_id, confirmation:{confirmed:true, note:...}}`; `version`
is optional and defaults to `1.0.0` only for the first Pack release. A revision
of an already finalized Pack must pass an explicit greater version. `pack_id` is the stable Agent-selected Pack
identity (for example `dnd5e.module.<slug>`), is required only at finalization,
and is not a package-edit field. Keep `idempotency_key` at the tool-call top
level. The confirmation is the Agent's final editorial decision; do not
manufacture or submit any other publication dimensions.
The server validates the finalized workspace, stores `metadata.agent_finalization`
with the reviewer and note, freezes the workspace, and writes the immutable
Module Pack archive. A module may carry or depend on a companion Addon Pack for
new monsters, items, spells, or rules; do not duplicate those entries as scene
prose.

Use `content_pack(import, kind="module")` for an existing archive and
`content_pack(activate, kind="module")` only with Owner/DM authority. Packs exclude campaign
progress, memories, branches, and Snapshots. If an active revision has progress
on a scene whose stable key changed, the Agent must review both indexes and pass
`progress_remaps` entries containing the old `from_scene_id`, the finalized
candidate `to_scene_key`, and an evidence-backed `reason`; never copy a draft
scene id into an immutable Pack activation.
