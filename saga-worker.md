# SagaSmith runtime acceptance audit

Date: 2026-09-09 (Asia/Singapore)

## Scope and state

- Runtime checkout: `D:/repo/repostew/Sagasmith-dnd-fix-173-next`
- Branch: `fix/173-artificer-followup-order`
- Runtime implementation commit: `b0d505f4` (`Close Bladesong two-handed and rest transitions`), pushed to origin; this audit is carried by the later documentation commits on the same branch.
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

## Issue disposition

### #172 — Artificer character materialization and starting equipment

Acceptance is implemented and exercised through the locked archive setup and play paths. Public MCP evidence covers class and subclass materialization, source-defined equipment alternatives including gold, tools, armor, ammunition and receipts, missing equipment as a blocking choice, replay, restart, and regression-driver rejection of an unsettled Artificer build. The remaining external prerequisite is publication of the immutable package in content-library PR #20.

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
- Focused locked archive groups previously passed on this branch: official expansions (`15 passed`), Artificer equipment/play (`3 passed`), Tortle Claws/breathing (`2 passed`), and Evasion/Bladesinging (`5 passed`).
- Domain focused regression: `88 passed`.
- `ruff check`, Python compilation, and `git diff --check` passed.
- CI for the new head is run `34316711159`; all four jobs were queued at audit time. The previous full CI run `34311338849` passed all four jobs.
- No issue was closed, no pull request was merged, no remote branch or fork was deleted, and no source archive was published by this task.
