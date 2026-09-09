# SagaSmith runtime acceptance audit

Date: 2026-09-09 (Asia/Singapore)

## Scope and state

- Runtime checkout: `D:/repo/repostew/Sagasmith-dnd-fix-173-next`
- Branch: `fix/173-source-defined-bladesong`
- Runtime implementation commit: `798d3a90` (`fix: honor source-defined bladesong resource`), based on merged PR #201.
- Runtime PR: follow-up regular PR for [SagaSmithAI/Sagasmith-dnd#173](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/173), opened after focused validation.
- Official content checkout: `D:/repo/repostew/SagaSmith-dnd-content-library` main at `fe7b14643136f42c6a9f61f2618f3c3b2cd9632c`; the test source checkout `D:/repo/repostew/SagaSmith-dnd-content-library-schema-check` at `36bd249` has the identical package tree.
- Content dependency: [SagaSmithAI/SagaSmith-dnd-content-library#20](https://github.com/SagaSmithAI/SagaSmith-dnd-content-library/pull/20) is merged. Post-merge library validation passed in run `34354558633`; the immutable package is available for runtime activation.

## Delivered runtime behavior

The branch carries the post-merge SCAG Bladesong source correction on top of the accepted 2014 Artificer/Battle Smith, City Watch Watcher's Eye, Tortle Claws, and SCAG Bladesinging runtime.

- Artificer/Battle Smith materializes the reviewed class and subclass grants, initial spells and preparation, source-defined equipment and tool choices, Steel Defender relations, replay, restart, and receipt-backed state.
- Watcher's Eye and Tortle Claws execute through exact locked archive cards with provenance, receipts, replay, restart, background replacement, and ingress protections. Watcher's Eye remains a narrative/source-fact capability and returns a GM result when campaign facts are absent. Tortle Claws remain intrinsic unarmed strikes and cannot be removed, equipped, transferred, or spent as carried inventory.
- Bladesinging materializes Training in War and Song, Bladesong, Extra Attack, Song of Defense, and Song of Victory from the indexed SCAG archive. Training grants light armor, Performance, and a selected one-handed melee weapon through the reviewed choice contract.
- Bladesong materialization now reads its single reviewed resource grant from the source card, preserving the source-defined two uses and short-rest recovery instead of installing an inferred proficiency-scaled profile. The focused acceptance test verifies the resulting capacity, recovery, and cantrip-substitution boundary.
- While active, Bladesong contributes Intelligence to AC, speed, Acrobatics advantage, and concentration-save context. Extra Attack settles two weapon attacks; the SCAG card does not authorize cantrip substitution, and the acceptance path rejects that request. Song of Defense creates a reaction choice and atomically pays the selected spell slot for five times slot level damage reduction. Song of Victory contributes Intelligence once as a flat eligible melee damage part and is not doubled by a critical hit.
- Whole-sheet replacement and character ingress preserve source-bound authority. A replacement cannot manufacture, erase, or alter active SCAG Bladesong or authoritative intrinsic species anatomy, and stale-CAS or forged provenance requests leave the prior state unchanged.
- The Right Tool for the Job uses the reviewed Eberron feature card through a DM-only declaration. It requires tinker's tools in hand, an unoccupied space within 5 feet, and one completed hour on the campaign timeline; it materializes a selected nonmagical artisan's-tools artifact, replaces the prior generated set, records source hashes and rule receipts, and replays across restart.

## Issue disposition

### #172 — Artificer character materialization and starting equipment

Acceptance is implemented and exercised through the locked archive setup and play paths. Public MCP evidence covers class and subclass materialization, source-defined equipment alternatives including gold, tools, armor, ammunition and receipts, the DM-only Right Tool for the Job materialization and replacement path, unavailable-package fail-closed behavior, missing and invalid class choices, missing and invalid cantrip and prepared-spell choices, Intelligence and level scaling, follow-up planning, CAS rollback, replay, restart, and regression-driver rejection of an unsettled Artificer build. The immutable package is published by content-library PR #20, and post-merge library validation passed.

### #173 — Battle Smith grants and subclass application order

Acceptance is implemented and exercised for source-bound always-prepared subclass spells, exact-level grants, subclass selection after level advancement, Steel Defender binding and lifecycle, replay, restart, CAS, multi-actor settlement, and receipt-backed state. The immutable package is published by content-library PR #20, and post-merge library validation passed.

### #176 — City Watch Watcher's Eye

Acceptance is implemented and exercised for exact City Watch and Investigator identity, source excerpt and rule references, explicit narrative/source-fact capability, present and absent campaign facts, pending GM results, malformed or spoofed source rejection, replay, restart, replacement, and receipt persistence. The immutable package is published by content-library PR #20, and post-merge library validation passed.

### #182 — Tortle Claws

Acceptance is implemented and exercised for the finalized intrinsic `Claws` profile, empty-hand and occupied-hand combat, remove/update/equip/transfer/spend rejection, species replacement, replay, restart, CAS, direct/template/content-actor/whole-sheet provenance guards, and unarmed-strike receipts. Module and addon routes use the same character ingress validator. The immutable package is published by content-library PR #20, and post-merge library validation passed.

### #183 — SCAG Bladesinging

Acceptance is implemented and exercised against the indexed SCAG archive version `1.0.5-local.subclass-grants.1`. Coverage includes the Elf/Half-Elf prerequisite, signed DM-only override and forged override rejection, level gating, Training in War and Song, the source-defined two-use short-rest resource, active benefits, voluntary dismissal, armor/shield/incapacitation/two-handed termination hooks, Extra Attack's two-weapon-attack contract and cantrip-substitution rejection, Song of Defense reaction and slot payment, Song of Victory flat damage, CAS/replay/restart, and whole-sheet authority protection. The immutable package is published by content-library PR #20, and post-merge library validation passed; runtime activation uses the same indexed package tree.

## Validation evidence

- Latest locked SCAG archive MCP test: `1 passed` with `SAGASMITH_DND_TEST_OFFICIAL_CONTENT_LIBRARY` set to the verified content-library checkout.
- Latest locked Artificer play MCP tests: `3 passed` with `SAGASMITH_DND_TEST_OFFICIAL_CONTENT_LIBRARY` set to the verified content-library checkout; the equipment/play group is `4 passed`.
- Focused locked archive groups previously passed on this branch: official expansions (`15 passed`), Tortle Claws/breathing (`2 passed`), and Evasion/Bladesinging (`5 passed`).
- Domain focused regression: `88 passed`.
- `ruff check`, Python compilation, and `git diff --check` passed.
- CI run `34349854857` for the pushed acceptance head `b56dbfa63c9a5cc26b25b56804066f49320b2674` passed all four jobs (locked-py311, compatibility-py312, skills, and ui).
- PR #201 was already merged before this follow-up; this correction opens a separate regular PR and does not close issues, delete branches or forks, or publish source archives.
