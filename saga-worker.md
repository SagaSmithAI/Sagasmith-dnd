# SagaSmith worker report

## #173 Battle Smith recovery

The requested runtime behavior is already present on `origin/main`.

- `82772c27` introduced typed subclass spell grants and the
  `spell_grants` runtime field consumed by MCP settlement.
- `ba08f520` added strict executable grant-method validation.
- `1835787f` added `current_class_feature_follow_up` with overdue-grant
  planning so selecting a subclass after reaching its level cannot strand
  class or subclass follow-ups.
- `packages/mcp/src/sagasmith_dnd_mcp/server.py:3923` validates canonical
  subclass grants; `:45977` materializes level-unlocked subclass spells;
  `:43781` refreshes spells during advancement; and `:44053` reports current
  class follow-ups including overdue grants.

This worker added a regression to
`packages/mcp/tests/test_artificer_play_official_archive_mcp.py` covering the
level-before-subclass path. It asserts that Battle Smith grants Heroism and
Shield as `always_prepared`/`class_prepared` spells and removes them from the
selected preparation list.

## Validation

- `uv run --project . ruff check tests/test_artificer_play_official_archive_mcp.py`
- `uv run --project . pytest tests/test_repair_subclass_grants.py -q`
- `uv run --project . pytest tests/test_level_advancement_mcp.py -q`
- `uv run --project . pytest tests/test_artificer_play_official_archive_mcp.py -q`
- `git diff --check`

The first three checks completed with exit code 0. The archive play test had
one passing protocol test and one skipped official-archive test because this
machine did not yet have a verified official content-library path configured.

## Content-library investigation

The canonical checkout at `D:\repo\repostew\SagaSmith-dnd-content-library`
was clean and one commit behind origin. An isolated worktree based on
`origin/main` was checked. The current verifier rejects the library while
validating a cross-system package actor; this is a compatibility defect in
the consuming runtime's package-verification boundary, not evidence that the
D&D archive source is invalid. No source text, package bytes, or canonical
library checkout was changed.

## Scope status

No external branch, issue, PR, or remote state was changed by this initial
test-only step. The follow-up worker step is integrating the existing
source-backed Tortle Claws intrinsic-attack implementation from commit
`45e1b577` and will record its validation and commit in this report.
