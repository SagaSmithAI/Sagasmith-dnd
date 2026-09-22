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

1. #133 / #139: execute the original stored Ready response and readied spell.
2. #116: resolve ordinary spell component eligibility before resource/RNG spend.
3. #158: source-bound object statistics and damage thresholds.

Other valid gameplay and content-publication gaps remain in the
[audited issue list](2026-09-22-local-first-issues.md). The broader Agent upstream
roadmap and upstream Chroma advisories have their own scope and evidence gates.

## #107 / #110 — interruptible movement and weapon reach

Movement now commits only the prefix before the earliest reach exit and retains
the remaining path in a durable continuation. Reactions are offered one at a
time in path order, with the weapon IDs that produced that specific reach exit.
Declining an inner reach does not erase later reach crossings. Whole and segmented
5/10/15-foot weapon paths produce the same options and movement spend.

Runtime resumes inside the existing reaction/choice transaction after nested
defense, damage, concentration and rescue choices have settled. Death, disabled
movement, changed position or newly illegal geometry cancel the remainder at the
committed boundary. Replay preserves the original result and continuation IDs;
no new Host remainder request is required. Current actor projections refresh
weapon options after sheet changes. Resumed movement carries source receipts and
reconciles Witch Bolt concentration in the same commit.

Coordinate-free movement requires explicit boundary distances/weapon IDs and
prefix terrain cost when needed. Missing facts produce a no-write pending ruling;
the Runtime never invents coordinates or puts the actor at an untraveled endpoint.

Validation: all 1,735 domain/Runtime cases passed at the broad verification
checkpoint, and 62 selected MCP cases passed. Additional focused coverage passed
for blocked continuation geometry, terrain billing and all three reach bands.
Public MCP cases exercise lethal/missed/declined reactions, later threats,
restart, exact replay, stale CAS, nested Shield defenses and Help/Sneak Attack
lifecycle. Ruff, generated operation references and whitespace checks passed.
These are executable local contract tests; no live LLM campaign is claimed.

## #109 — source-bound off-turn self-powered movement

Reviewed semantic movement steps can now require the mover's movement, action or
reaction independently of whose turn it is. Payment, volition and the distance
allowance are fixed source-template fields, not caller-controlled bindings.
Runtime binds the exact paid source/plan/application and records the mover's
payment once. Action/reaction allowances do not reuse or drain ordinary turn
movement; movement payment still uses its remaining pool. Compelled self-powered
movement still provokes; external push/pull and teleportation remain exempt.

The existing durable path continuation preserves the source grant across all
reach exits and cancels on death, immobilization, displacement or inadequate
remaining speed. Semantic execution waits for the complete movement, including
later reach windows after the first waiting ID has disappeared. Resumed results
contain the final position and outcome without paying or moving again.

Validation: 1,752 domain/Runtime tests passed, followed by 19 selected MCP tests
and a 38-case MCP movement/transaction checkpoint (overlapping suites). Public
tests cover all three payment types, two weapon reaches, source mismatch,
incapacitated mover no-write failure, stale CAS, restart, exact replay, and
dependent steps waiting until movement settles. Existing push/pull and
teleportation public tests passed. Ruff and generated references were checked.
This establishes the generic source-bound mechanism, not automation of every
spell that can cause movement or a live model campaign.
