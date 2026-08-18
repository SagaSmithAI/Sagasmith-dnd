# Core: transactions

Before every write, read the current authoritative object and revision. Send
the required `expected_revision`, current `branch_id`, and a stable
`idempotency_key`. A retry of the same logical request reuses the same key and
payload; a changed request uses a new key.

Never imitate a successful mutation in narration, edit SQLite or artifacts
directly, or pay an action/resource cost twice. Dice and campaign random
positions are server-owned. Preserve returned random receipts, source
references, state revisions, and Snapshot heads.

After a conflict, restore, phase change, or response loss, query receipts and
current state before deciding whether to retry. Snapshot and branch writes must
not contaminate sibling timelines.
