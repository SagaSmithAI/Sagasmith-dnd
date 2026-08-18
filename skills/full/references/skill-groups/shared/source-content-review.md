# Source content review

Use native text, reconstruction, and bounded OCR before vision. Keep checksum,
page/chunk, heading, raw/normalized text, warnings, and evidence together.
Reviews are checksum-bound views.

Compare server-provided variants and submit exact, unique replacements through
`rulebook_draft(edit, operation="source_text")` or
`module_draft(edit, operation="source_text")`. A text-only Agent uses `cross_text` when two
sources agree, or `agent_context` for bounded spelling/case/heading repair with
unchanged digits and quantities. Only an Agent or human that inspected the image
may use `rendered_page`.

If every extractor omitted a page, `rendered_page` may submit one replacement
with `old: ""` and a complete transcript. This requires an empty page, one
replacement, and the exact image checksum.
Text-only Agents may consume the result later, but cannot claim vision.

If the rendered page contains the suspect wording, preserve it as a source typo.
Build the semantic card from stronger same-page evidence; never rewrite source
prose to make it correct.

For a damaged 2014 statblock field, call draft `evidence`, then draft `edit`
with the statblock recovery operation, exact page, slot, printed name, and corrections. A
text-only correction must already occur in immutable `staged_text`; a visual
correction must bind the rendered checksum. Ability corrections require complete
`score (modifier)` cells. Text replacement needs an exact unique old span and a
replacement present in staged evidence. The server reruns parsing and records
the review; identical idempotent replay must not rerun OCR.

Route by locked edition. Statblock recovery accepts only 2014 grammar. Reviewed
2024 text uses `content_kind="dnd5e_2024_statblock"`; image-only 2024 content
needs literal visual review. Never parse one edition with the other grammar.

For custom semantics, the Agent fills constrained facade fields from returned
evidence; the server stores a versioned solution. Do not
add creature-specific parser patches, infer missing/conflicting numbers, or turn
OCR output into a Core mechanic. If no verified text or inspected image proves a
fact, stop for external review.
