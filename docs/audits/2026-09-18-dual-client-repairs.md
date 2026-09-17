# D&D UI / Agent concurrency repairs

Scope: the five findings reproduced against `51319ce8` on 2026-09-17.

## Changes

1. **Primary write receipts survive projection failures.** Gateway POST responses
   no longer query campaign metadata after the primary write. Movement returns
   its authoritative combat receipt. A timeout or full queue during a later view
   refresh cannot relabel that write as never dispatched. UI distinguishes a
   movement awaiting adjudication from a completed movement.
2. **Aggregated reads have a consistent authority revision.** Gateway compares
   campaign revision and branch before and after constructing a projection.
   It reuses the first metadata snapshot within that projection and retries up
   to three times. Sustained contention returns `409 read_snapshot_conflict`.
   Single-response rendered images, artifact streams and SSE are separate paths.
3. **Paid plans tolerate intervening writes.** A refreshed current-revision
   `target_facts` object may replace stale evidence without another payment.
   The application, compiled plan, actor bindings, decision and source evidence
   must still match the paid declaration. Stale facts and changed decisions fail.
4. **Semantic attacks resume after defensive reactions.** Runtime persists
   the actual attack roll and a player-owned defense window before damage.
   The normal defense command resolves the recorded attack, caches its result,
   and permits continuation without rerolling or paying again.
5. **Multiple attacks pause for each concentration save.** Each execution segment
   commits damage, pending choices and completed-step results atomically. Later
   segments reuse those results. A failed segment rolls back its own changes;
   previously committed choices are preserved. Ordinary actions and other
   semantic applications cannot bypass an unfinished continuation.

Domain defines the execution-segment pause contract. Runtime owns persistence,
authority and continuation state; MCP and HTTP remain adapters. Private
continuation state is removed from player and public combat projections.

## Regression evidence

- `test_dual_client_consistency.py` inserts a real MCP movement between the
  Gateway combat query and metadata check. The returned position and revision
  agree. The paid Agent plan then succeeds with refreshed evidence and the same
  application; stale evidence and a changed decision are rejected.
- `test_gateway.py` injects timeout and queue failures into any hypothetical
  post-write query. The successful movement receipt is still returned. A
  constantly changing authority revision produces a bounded conflict.
- `test_semantic_continuations_mcp.py` uses real persistence and restarts the
  server between checkpoint and response. It covers declining both reactions,
  accepting Uncanny Dodge, and two concentration saves. It verifies exact-key
  replay, attack roll counts, final HP, early-resume rejection and movement
  blocking. Attack rolls are deterministic test fixtures, not provider evidence.
- Domain tests verify both pre-result and post-result pause receipts; Runtime
  tests verify segment rollback and persisted concentration windows.
- Headless Edge with real IndexedDB confirms reload recovery retains JSON
  payloads, uploaded bytes and original request keys against a synthetic server.

## Acceptance boundary

Local validation: Domain 1577, Runtime 89, MCP 1469 and UI 15 tests passed
(3150 total); MCP retained 29 skips. Domain's full run preceded the two added
pause cases, which passed in a focused follow-up. Final Gateway and continuation
follow-ups passed after the full MCP run began. Ruff on all three Python
packages, UI typecheck/build, Skill publish consistency, diff checks and all six
Python wheel/sdist builds passed. The final Gateway follow-up also eliminated
the request-key deprecation warning observed by the earlier full-run workers;
the dependency's Pydantic warning remains.

These are local code, integration, browser-storage and packaging checks.
Hosted domain, server and model configuration remain unset. This report does
not claim deployed HTTPS or live model-provider acceptance.
