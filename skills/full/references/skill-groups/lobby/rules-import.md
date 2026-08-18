# Rulebook drafts and packs

Use only three authoring facades: `rulebook_draft`, `module_draft`, and
`content_pack`. Read `dnd:full/references/parsing-agent-edit-loop.md` before
editing rulebook candidates.

Call `rulebook_draft(start)` once with the managed source and Pack identity.
Core+D&D stage, inspect, index, mechanically extract, normalize, and check the
first candidate set. If it returns `source_review_required`, inspect the exact
page with `rulebook_draft(evidence, kind="page")`; submit checksum-bound text
repairs through `rulebook_draft(edit, operation="source_text")`, then use
`operation="advance"`. Never mutate the PDF or OCR cache.

Use `rulebook_draft(edit, operation="candidates")` repeatedly for semantic
changes. Use `operation="catalog"` for a missed source-bound entity and the
statblock operations only against exact managed evidence. Every response
reruns Core+D&D checks. Accepted and rejected dispositions remain editable.

Call `rulebook_draft(finalize)` with the latest revision, idempotency key,
completion note, and explicit confirmation after all hard blockers are resolved.
Remaining unselected candidates become excluded. It freezes, compiles, and saves
the immutable Pack atomically. Inspect it with `content_pack(get)` and
activate it with `content_pack(activate)`, using `kind="core_rules"` or
`kind="addon"`; finalization never activates a campaign.

For an already finalized v2 archive, skip parsing and call
`content_pack(import, kind="core_rules"|"addon")`. Preserve exact citations,
checksums, dependency locks, and receipts. OCR repair must not invent numbers,
identities, or rules.
