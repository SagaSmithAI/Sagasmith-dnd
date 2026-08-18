# Module Pack review gates

Run these gates at trust boundaries. Block only for authority, evidence,
identity, portability, conflicting required truth, or indispensable mechanics.

## Gate A: before module_draft start

- The authoring campaign's exact system_id and current native schemas are known.
- The selected profile in [system-profiles.md](system-profiles.md) matches.
- The source is one UTF-8 Markdown document.
- Exactly zero or one runtime manifest exists; when present it is valid v1.
- Runtime ids are lowercase, stable, globally unique, and aligned with prose.
- Chapter, scene, subsection, and numbered-location headings are coherent.
- Scene titles are meaningful and stable.
- Indispensable revelations have viable discovery paths.
- Executable actors/checks have exact system-valid mechanics.
- Spatial and chase facts are stated only where sourced.
- The ledger contains common identity, profile, continuity, catalogs, narrative,
  dependencies, publication data, and system-specific decisions.

Repair source failures before starting the draft.

## Gate B: after the mechanical first pass

- The draft has job_id, inactive module_id, state, and current revision.
- The state is imported before finalization work.
- The reported parser profile matches the campaign system.
- Validation errors are empty.
- Scene count, chapter assignment, stable keys, and chunk boundaries match the
  source.
- Every content-bearing scene has a unique stable key, valid source span, and
  non-empty managed source refs; system semantics are under profile_data.
- No empty, swallowed, duplicated, or accidentally split scene exists.
- Runtime-manifest identities and references align with derived scenes.
- Spatial locations and connections remain conservative.
- Statblock, OCR/transcription, asset, check, and progress-impact diagnostics
  have been reviewed.

If a source cannot be repaired through an applicable draft edit, repair the
canonical source and start a corrected draft. Do not add a book-specific parser
heuristic.

## Gate C: before Package finalization

### Authority, identity, and evidence

- pack_id uses the exact system prefix and version is intentional.
- Every profile field required by the selected system has exact source receipts.
- No source key, hash, page, dependency checksum, or actor identity was invented.
- Every edit used the current revision and a request-specific idempotency key.
- The complete current draft, not an earlier revision, was reviewed.

### Common Package decisions

- Manifest contains title, classification, compatibility, play_profile,
  continuity, and activation only.
- Catalog fields match the selected system and every value is an array.
- Narrative contains dossiers and endings arrays.
- Dependencies have exact kind, id, version, checksum, and optional flag.
- Metadata contains publication/review decisions, not campaign state.
- Scene visibility is canonical restricted/group/public; system scene fields
  are confined to metadata.profile_data.
- Every accepted content review targets an existing scene and has exactly one
  evidence mode, an authenticated reviewer, and a non-empty observation.

### D&D gate

- compatibility editions, levels, advancement, pregen review, DCs, encounter
  actors, rewards, spells, and statblocks use the intended D&D edition.
- Start/end levels, advancement, and pregen review have evidence.
- Missing party-size advice remains null/unsourced rather than guessed.
- A campaign has at least one reachable ending.

### CoC gate

- compatibility includes 7e and all six play-profile sections have evidence.
- investigator and session ranges are valid; ruleset recommendation is supported.
- classification and solo support agree.
- All playable scenario/campaign/solo works have a reachable ending.
- Core clues have viable routes and supplementary clues/check consequences are
  unambiguous.
- SAN expressions, pushed-roll stakes, combined-roll requirement, group-Luck
  intent, chase evidence, tomes/spells, and source statblocks are preserved when
  present.
- No D&D level, party-majority, AC, class, or spell-slot assumption leaked in.

### Confirmation

- The confirmation note names source, system profile, scenes, mechanics,
  catalogs, narrative, dependencies, and diagnostics.
- No unresolved permission, stale revision, conflicting required source fact,
  or mechanically indispensable field remains.
- Optional portraits and presentation polish do not block a valid Pack.

## Gate D: after finalization

Inspect the artifact through content_pack get and verify:

- format is sagasmith.content-package, schema_version is 2, kind is module;
- system_id matches the authoring campaign and pack_id prefix;
- id, version, manifest identity, checksum, and finalization metadata agree;
- metadata.agent_finalization contains exactly confirmed=true, reviewer, and
  note; the Scene Atlas and content_reviews pass the strict final contract;
- source, asset, review, actor, scene, catalog, dossier, and ending counts are
  plausible;
- the draft state is compiled;
- campaign ids, permissions, progress, knowledge, random state, branches,
  snapshots, and undo history are absent.

Build completion ends here unless installation was explicitly requested.

## Gate E: optional import and activation

- Import exactly one finalized artifact or managed source path.
- Verify the imported module_id; do not activate the mechanical draft.
- Refresh the target campaign revision immediately before activation.
- Review replacement progress impact.
- Supply explicit from_scene_id, to_scene_key, reason remaps for removed realized
  scenes; never guess or discard progress.
- Confirm the resulting active identity and next legal native call.

## Retry rules

- Reuse an idempotency key only for an exact retry.
- Use a new key whenever any payload field changes.
- Refresh the draft or campaign revision after a stale-revision failure.
- Do not turn authorization, evidence, system mismatch, or revision failures
  into warnings.
