# Agent Pack review loop

Use this workflow for an unfinalized rulebook or module Pack. Core+D&D owns
document extraction, mechanical normalization, the first candidate set, and
structural validation. The Agent owns semantic review and editing until explicit
finalization.

## Rulebook loop

1. Call `rulebook_draft(action="start")` once. Treat the output as editable
   draft material, never executable content.
2. Read the current workspace, issues, and exact evidence with
   `rulebook_draft(action="get"|"evidence")`.
3. Find both false positives and false negatives. Use `edit(operation="candidates")`
   to include, exclude, reopen, split, merge, or replace; use `operation="catalog"`
   for a missed source-bound entity.
4. Re-read issues and inventory after every edit. Fix hard blockers from exact
   evidence and judge advisories in context. Fine edits do not require revision
   or idempotency ceremony.
5. After hard blockers are zero and the current draft has been reviewed, call
   `finalize` with the latest revision, idempotency key, completion note, and
   explicit confirmation. Finalization excludes remaining unselected candidates,
   compiles, freezes, and saves the immutable Pack.
6. Inspect or activate later with `content_pack`, always using an explicit route
   kind. Finalization never activates a campaign.

## Hard blockers and advice

Block only when the requested artifact cannot be trusted or built:

- structurally invalid accepted content;
- missing or conflicting source identity/evidence;
- an explicitly failing declared test;
- compilation or immutable-package integrity failure.

Keep incomplete coverage, catalog suggestions, pending/rejected draft choices,
semantic uncertainty, and future automation opportunities as visible advice for
Agent review. Do not require every possible future ruling to be automated before
finalization.

## Agent authority before finalization

The Agent may revise identity, kind, ownership, boundaries, structured fields,
and earlier decisions; add missed source-bound candidates; split or merge by
replacement and explicit exclusion; repair source-proven transcription damage;
and continue editing for as many passes as needed.

The Agent must not mutate source files, checksums, validators, or immutable
evidence; invent missing facts; relabel a hard blocker as advice; or activate a
Pack merely because it finalized.

## Reusable review patterns

Treat these as inspection prompts, never automatic parser rules:

- Repeated headings or identical paths do not prove occurrence boundaries.
- Physical proximity does not prove ownership, subclass, dependency, or scene.
- Overlapping spans do not prove duplicates; compare complete evidence.
- A generic-looking title does not prove rejection.
- Repair a damaged modifier or label mechanically only when remaining source
  values uniquely establish it.
- A structurally valid candidate may still be semantically wrong, and a missing
  candidate may still be source-established.

For each correction, save candidate ids, evidence/page range, replacement or
disposition, decision, and reason. Inspect downstream inventory instead of
assuming an edit had the intended effect.

## Where decisions live

Keep book-specific decisions in draft/Pack dispositions, artifacts, issues,
source bindings, and edit history. Put only reusable cross-book inspection and
correction procedures in this Skill. Promote a pattern into Core or D&D only
when it is a demonstrated deterministic invariant across sources.

For a module book, apply the same loop through `module_draft`. Save reviewed
content and package decisions with `edit`; finalize only after inspecting the
actual draft and sending explicit confirmation. Runtime progress remains campaign
state, not Pack content. Corrections after finalization require a new Pack version.
