# D&D Pack authoring workflow

## Establish the ledger

Before expanding prose, record the portable id, version, title, language,
license, attribution, exact D&D editions, dependencies, classification,
required capabilities, level range, advancement, pregen review, scene graph,
actors, encounters, hazards, rewards, secrets, branches, and endings. Record
which facts require source evidence and which assets must remain private.

For rules Packs, record source identity, edition, privacy, and the reviewed
areas that must be searchable. Do not add Module scenes, actors, catalogs, or
endings to a rules Pack.

## Author one source

Create one UTF-8 Markdown source with at most one
`sagasmith-runtime-manifest`. Use stable lowercase ids and meaningful headings.
Keep authored truth separate from discovered actor knowledge and public
narration. Include exact source-backed mechanics or explicitly mark content as
narrative-only; do not synthesize geometry the source does not establish.

Ensure every required revelation has more than one reasonable discovery path.
A failed check may change cost, time, risk, or detail, but must not erase the
only route to a legal ending.

## Draft and repair

1. Confirm Lobby, authenticated authority, `system_id=dnd5e`, edition, current
   revision, and the native authoring tools.
2. Start one draft with either a managed source path or generated name plus
   complete content. Keep the returned job, inactive module id, state, parser
   profile, and revision.
3. Inspect the draft and obtain exact evidence receipts from the native
   evidence action.
4. Repair the narrowest draft field. Use source-text edits for transcription,
   structured content/statblock edits for reviewed mechanics, asset edits for
   managed assets, actor edits for validated bindings, and Package edits for
   manifest, catalogs, narrative, dependencies, metadata, or version.
5. Pass the latest expected revision and a request-specific idempotency key on
   every write, then refresh before the next write.
6. Record concise evidence and ruling notes for semantic decisions. Treat
   optional presentation fields and unavailable portraits as non-blocking.

## Finalize and deliver

Validate source identity, scene reachability, exact mechanics, actor cards,
dependencies, evidence, edition compatibility, catalogs, and endings. Finalize
only the current compiled draft with explicit confirmation. Inspect the
immutable artifact through `content_pack` and verify its schema, `dnd5e`
identity, checksum, source binding, and component counts.

Stop at the built artifact unless the user separately requested installation.
Import inactive. Activate only against a fresh campaign revision, and never
guess progress remaps for a replacement.
