# SagaSmith runtime acceptance audit

Date: 2026-09-09 (Asia/Singapore)

## Scope and state

- Runtime checkout: `D:/repo/repostew/Sagasmith-dnd-fix-173-next`
- Branch: `fix/173-artificer-followup-order`
- Runtime implementation commit: `0ff6d6afeed6e0e9b1a2a8933af0d4a22525146e` (`test: complete bladesinging acceptance contract`), pushed to origin; this audit is carried by the later acceptance-test commit on the same branch.
- Runtime PR: [SagaSmithAI/Sagasmith-dnd#201](https://github.com/SagaSmithAI/Sagasmith-dnd/pull/201), open.
- Official content checkout: `D:/repo/repostew/SagaSmith-dnd-content-library-schema-check`, branch `fix/runtime-locked-archives-v1`, commit `36bd249`.
- Content dependency: [SagaSmithAI/SagaSmith-dnd-content-library#20](https://github.com/SagaSmithAI/SagaSmith-dnd-content-library/pull/20), open. Runtime tests use the verified indexed package checksums from that checkout; the package has not been published or merged by this task.

## Delivered runtime behavior

The branch provides source-bound public MCP acceptance paths for the 2014 Artificer/Battle Smith, City Watch Watcher's Eye, Tortle Claws, and SCAG Bladesinging content.

- Artificer/Battle Smith materializes the reviewed class and subclass grants, initial spells and preparation, source-defined equipment and tool choices, Steel Defender relations, replay, restart, and receipt-backed state.
- Watcher's Eye and Tortle Claws execute through exact locked archive cards with provenance, receipts, replay, restart, background replacement, and ingress protections. Watcher's Eye remains a narrative/source-fact capability and returns a GM result when campaign facts are absent. Tortle Claws remain intrinsic unarmed strikes and cannot be removed, equipped, transferred, or spent as carried inventory.
- Bladesinging materializes Training in War and Song, Bladesong, Extra Attack, Song of Defense, and Song of Victory from the indexed SCAG archive. Training grants light armor, Performance, and a selected one-handed melee weapon through the reviewed choice contract.
- Bladesong is a bonus action with proficiency-bonus-scaled uses and long-rest recovery. The acceptance test now verifies DM authorization and forged-override rejection, active effects, short-rest non-recovery, long-rest recovery, voluntary dismissal, and two-handed attack termination with the persisted `two_handed_attack` reason.
- While active, Bladesong contributes Intelligence to AC, speed, Acrobatics advantage, and concentration-save context. Extra Attack settles two attacks and one eligible cantrip replacement. Song of Defense creates a reaction choice and atomically pays the selected spell slot for five times slot level damage reduction. Song of Victory contributes Intelligence once as a flat eligible melee damage part and is not doubled by a critical hit.
- Whole-sheet replacement and character ingress preserve source-bound authority. A replacement cannot manufacture, erase, or alter active SCAG Bladesong or authoritative intrinsic species anatomy, and stale-CAS or forged provenance requests leave the prior state unchanged.
- The Right Tool for the Job uses the reviewed Eberron feature card through a DM-only declaration. It requires tinker's tools in hand, an unoccupied space within 5 feet, and one completed hour on the campaign timeline; it materializes a selected nonmagical artisan's-tools artifact, replaces the prior generated set, records source hashes and rule receipts, and replays across restart.

## Issue disposition

### #172 — Artificer character materialization and starting equipment

Acceptance is implemented and exercised through the locked archive setup and play paths. Public MCP evidence covers class and subclass materialization, source-defined equipment alternatives including gold, tools, armor, ammunition and receipts, the DM-only Right Tool for the Job materialization and replacement path, unavailable-package fail-closed behavior, missing and invalid class choices, missing and invalid cantrip and prepared-spell choices, Intelligence and level scaling, follow-up planning, CAS rollback, replay, restart, and regression-driver rejection of an unsettled Artificer build. The remaining external prerequisite is publication of the immutable package in content-library PR #20.

### #173 — Battle Smith grants and subclass application order

Acceptance is implemented and exercised for source-bound always-prepared subclass spells, exact-level grants, subclass selection after level advancement, Steel Defender binding and lifecycle, replay, restart, CAS, multi-actor settlement, and receipt-backed state. The remaining external prerequisite is publication of the immutable package in content-library PR #20.

### #176 — City Watch Watcher's Eye

Acceptance is implemented and exercised for exact City Watch and Investigator identity, source excerpt and rule references, explicit narrative/source-fact capability, present and absent campaign facts, pending GM results, malformed or spoofed source rejection, replay, restart, replacement, and receipt persistence. The remaining external prerequisite is publication of the immutable package in content-library PR #20.

### #182 — Tortle Claws

Acceptance is implemented and exercised for the finalized intrinsic `Claws` profile, empty-hand and occupied-hand combat, remove/update/equip/transfer/spend rejection, species replacement, replay, restart, CAS, direct/template/content-actor/whole-sheet provenance guards, and unarmed-strike receipts. Module and addon routes use the same character ingress validator. The remaining external prerequisite is publication of the immutable package in content-library PR #20.

### #183 — SCAG Bladesinging

Acceptance is implemented and exercised against the indexed SCAG archive version `1.0.5-local.subclass-grants.1`. Coverage includes the Elf/Half-Elf prerequisite, signed DM-only override and forged override rejection, level gating, Training in War and Song, proficiency-bonus uses, long-rest-only recovery, active benefits, voluntary dismissal, armor/shield/incapacitation/two-handed termination hooks, Extra Attack and cantrip substitution, Song of Defense reaction and slot payment, Song of Victory flat damage, CAS/replay/restart, and whole-sheet authority protection. The remaining external prerequisite is publication of the immutable SCAG package in content-library PR #20; runtime behavior cannot claim published-archive activation until that dependency lands.

## Validation evidence

- Latest locked SCAG archive MCP test: `1 passed` with `SAGASMITH_DND_TEST_OFFICIAL_CONTENT_LIBRARY` set to the verified content-library checkout.
- Latest locked Artificer play MCP tests: `3 passed` with `SAGASMITH_DND_TEST_OFFICIAL_CONTENT_LIBRARY` set to the verified content-library checkout; the equipment/play group is `4 passed`.
- Focused locked archive groups previously passed on this branch: official expansions (`15 passed`), Tortle Claws/breathing (`2 passed`), and Evasion/Bladesinging (`5 passed`).
- Domain focused regression: `88 passed`.
- `ruff check`, Python compilation, and `git diff --check` passed.
- CI run `34349854857` for the pushed acceptance head `b56dbfa63c9a5cc26b25b56804066f49320b2674` passed all four jobs (locked-py311, compatibility-py312, skills, and ui).
- No issue was closed, no pull request was merged, no remote branch or fork was deleted, and no source archive was published by this task.

## 2026-09-10 recovery update

The live state has advanced since this handoff. Content-library PR #20 is merged at `fe7b14643136f42c6a9f61f2618f3c3b2cd9632c`; runtime PR #201 is merged at `95ce0d56a4b715f3a50a9d61d746850cd4b841c5`; runtime PR #202 is merged at `70e1283dab742fbb50d7be479990c5609e0d08c8`. Issues #172, #173, #176, and #182 remain open; #183 is closed.

The current isolated follow-up is `fix/full-official-runtime` at `e431fdda` (`perf: avoid repeated official archive plan copies`) with PR [#213](https://github.com/SagaSmithAI/Sagasmith-dnd/pull/213). The runtime now walks freshly copied archive values in place while rebuilding resolution-plan fingerprints, retaining the immutable archive boundary and avoiding repeated deep copies during large official archive verification. `skills` and `ui` CI jobs passed; the two Python jobs were pending at the last poll.

With `D:/repo/repostew/worktrees/saga-content-library-runtime-lock` and the current `D:/repo/repostew/Sagasmith-core/src` first on `PYTHONPATH`, the locked Eberron Artificer acceptance test passed end to end, including dependency rebind, source-bound class/subclass materialization, Defender combat/lifecycle, replay, restart, and CAS. The domain archive/package focus passed 19 tests; Ruff, Python compilation, and `git diff --check` passed. The root pytest environment otherwise resolves an older installed core package and can report a false missing dependency; the explicit editable core source path is required for local validation.

The canonical runtime clone and registered worktree resources remain preserved with their pre-existing dirty or untracked state. No issue was closed, no pull request was merged by this recovery, and no branch, fork, or archive was deleted.
