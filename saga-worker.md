# SagaSmith runtime acceptance audit

Date: 2026-09-09 (Asia/Singapore)

## Scope and state

- Runtime checkout: `D:\repo\repostew\Sagasmith-dnd-fix-173-next`
- Branch: `fix/173-artificer-followup-order`
- Head: `70eba6b3` (`Complete source-bound Bladesinging runtime`), pushed to origin.
- Runtime PR: [SagaSmithAI/Sagasmith-dnd#201](https://github.com/SagaSmithAI/Sagasmith-dnd/pull/201), open.
- Official content checkout: `D:\repo\repostew\SagaSmith-dnd-content-library-schema-check`, branch `fix/runtime-locked-archives-v1`, commit `36bd249`.
- Content dependency: [SagaSmithAI/SagaSmith-dnd-content-library#20](https://github.com/SagaSmithAI/SagaSmith-dnd-content-library/pull/20), open. Runtime tests use the exact locked checkout and indexed package checksums; no unpublished package is assumed.

## Delivered runtime behavior

The branch now provides source-bound acceptance paths for the 2014 Artificer/Battle Smith, City Watch Watcher's Eye, Tortle Claws, and SCAG Bladesinging content.

- Artificer/Battle Smith materialization persists reviewed starting equipment, artisan-tool choices, subclass grants, Steel Defender relations, replay, and restart state.
- Watcher's Eye and Tortle Claws execute through exact locked archive cards with provenance, receipts, replay, restart, and ingress protections.
- Bladesinging materializes Training in War and Song, Bladesong, Extra Attack, Song of Defense, and Song of Victory from the exact SCAG package. Training grants light armor, Performance, and a selected weapon through the reviewed choice contract.
- Bladesong is a bonus action with proficiency-bonus-scaled uses and long-rest recovery. Its active effect settles the Intelligence AC bonus, speed increase, Acrobatics advantage, and concentration-save bonus; voluntary dismissal and armor, shield, incapacitation, and two-handed attack termination are persisted.
- Extra Attack settles two attacks and exactly one source-authorized cantrip replacement. Song of Defense creates a reaction choice and atomically pays the selected spell slot for five times slot level damage reduction. Song of Victory contributes the Intelligence modifier once as a flat eligible melee damage part.
- Whole-sheet replacement cannot manufacture, erase, or alter an active source-bound Bladesong effect. Failed non-Elf application and stale-CAS paths preserve the prior sheet atomically.

## Issue disposition

### #172 — Artificer character materialization and starting equipment

**Bounded acceptance delivered.** Locked setup/play coverage verifies class and subclass materialization, source-defined equipment and tool selection, receipts, replay, restart, and Steel Defender persistence. The issue remains open for broader class-level and invalid-selection matrices outside this bounded path.

### #173 — Battle Smith grants and subclass application order

**Bounded acceptance delivered.** Late subclass grant settlement, source-bound spell grants, Defender relation persistence, and replay/order behavior are covered. Broader all-level and tool/component matrices remain outside this change.

### #176 — City Watch Watcher's Eye

**Bounded acceptance delivered.** The exact SCAG artifact, source facts, pending ruling behavior, receipts, replay, restart, and City Watch versus Investigator distinction are covered. Additional malformed and replacement matrices remain open.

### #182 — Tortle Claws

**Bounded acceptance delivered.** The exact intrinsic unarmed-strike profile, empty/occupied-hand behavior, transfer/mutation rejection, receipts, replay, and restart are covered. Broader species replacement concurrency remains open.

### #183 — SCAG Bladesinging

**Core runtime acceptance delivered against the locked indexed SCAG archive.** The path covers the species prerequisite and DM-only signed override, Training in War and Song, proficiency-bonus resource scaling, long-rest recovery, Bladesong activation/effects/dismissal, source-defined termination, Extra Attack and cantrip substitution, Song of Defense reaction/slot payment, Song of Victory flat damage, CAS/replay/restart, and whole-sheet ingress protection. The archive remains the locally repaired immutable package version `1.0.5-local.subclass-grants.1`; publication and merge of the content-library dependency are separate.

## Validation evidence

- Domain focused regression after the final test cleanup: `88 passed`.
- Locked archive groups passed: official expansions (`15 passed`), Artificer equipment/play (`3 passed`), Tortle Claws/breathing (`2 passed`), and Evasion/Bladesinging (`5 passed`) against the verified library.
- `ruff check` over all changed runtime and test files: passed.
- Python compilation and `git diff --check`: passed.
- PR CI run `34308469740`: all four jobs passed. UI `23s`, Skills `7s`, Python 3.11 `38m1s`, and Python 3.12 `41m21s`; both Python jobs passed lint, domain tests, and full MCP test suites.

No issue was closed, no pull request was merged, no remote branch or fork was deleted, and no source archive was published by this task.


