# Module Index and Play

Full Runtime uses MCP-managed Markdown artifacts. Generate module text or provide a
configured source path, then call `module_draft(start)`, repeat `evidence/edit`,
call `finalize`, and activate through `content_pack`. Do not let the agent read arbitrary local module
paths in Full Runtime.

Every stage uses the same D&D parser profile. Pass a stable stage-specific
`idempotency_key` and reuse it only for the exact same retry.

Use `module_query(view="index")` to choose scenes. For each player action call
`module_query(view="current")` with `scope_id`, then
`module_query(view="scene")`. Search is only a
selector: `module_search` must be followed by `module_expand` before a result is
used as module truth.

An expanded document chunk is not automatically a scene record. PDF chunks may
have no `scene_id`, may span adjacent headings, and can match a repeated room
name elsewhere. For a check, participant manifest, or combat map, select the
indexed scene, read that exact scene with `module_query(view="scene")`, verify its
module id/page range/location key, and copy the evidence substring from the
returned scene content. The runtime normalizes PDF control characters, soft
hyphens, smart quotes, and dash variants for containment; this does not make a
paraphrase, translation, or cross-scene hit valid evidence.

For encounter preflight, `required_count` describes the complete current group,
not the number of cards that happen to exist. Derive it from an exact printed
count, persist the result of a source random table/roll, or record an explicit
branch-local DM composition fact. Create every required PC/NPC/monster card in
lobby. If the scene also names another hostile, reserve, or optional group,
manifest it or record why it is absent before combat. A shorter substring must
never be used to hide a larger printed composition.

Scopes are `party`, `group:<id>`, and `player:<id>`. A player or group inherits the
party current scene until its own `module_set_progress` call establishes a separate
progress record. Update progress with the complete merged `state`, `current_room`,
status, and percent; do not reveal rooms or facts outside that scope.

If topology exists only in a PDF map or diagram, do not manufacture edges here.
Follow `../../../references/module-visual-atlas.md`: query managed assets, render
and inspect the page, then use the validated `spatial_review` path. A review-only
write may omit `status` and `progress` so their current values remain unchanged.

If the scene text names a creature but its appendix card is missing because the
PDF stored it as an image, follow
`../../../references/module-image-content-review.md`. Render and inspect the
managed page, validate an immutable `module_draft(action="edit", operation="content")`, then create exact
actors from its review id in lobby. Do not substitute another creature or create
the missing card after combat starts.

When leaving a scene, write it as `completed` with progress `100`, then set the
next scene to `current`. Record a snapshot before chapter transitions.
