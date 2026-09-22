# Local D&D completion work — 2026-09-22

This ledger follows the point-in-time organization issue audit. A closed item
requires implementation and applicable validation; pending items remain open.
Host-managed local execution must preserve Runtime authority, real choices,
source evidence, CAS, transactions and original-key replay.

## Completed

### #127 — require structured new Help declarations

New Help requests must identify attack or task Help. An omitted or empty payload
and an explicit legacy kind now fail before any persisted action spend or state
change. Existing legacy encounter flags remain readable; new requests cannot
create them through the compatibility path.

Validation: the complete `test_combat_engine.py` and
`test_search_action_mcp.py` suites passed, plus Ruff on changed Python files.
New regressions cover omitted/empty/legacy declarations, unchanged input and
campaign state, a successful corrected request with the same key, exact replay,
and stale-revision rejection. Existing bound-target, task-consumption and legacy
snapshot tests remain green. This is domain/MCP evidence, not a live LLM session.

## Next established gaps

1. #107 / #110 / #109: interruptible movement, per-weapon reach crossings and
   off-turn self-powered movement.
2. #133 / #139: execute the original stored Ready response and readied spell.
3. #116: resolve ordinary spell component eligibility before resource/RNG spend.
4. #158: source-bound object statistics and damage thresholds.

Other valid gameplay and content-publication gaps remain in the
[audited issue list](2026-09-22-local-first-issues.md). The broader Agent upstream
roadmap and upstream Chroma advisories have their own scope and evidence gates.
