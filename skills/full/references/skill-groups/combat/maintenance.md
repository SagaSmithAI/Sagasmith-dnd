# Combat runtime maintenance

Runtime maintenance is Owner/DM work, not a combat tactic. Before upgrading the
built-in Core, end or checkpoint unsafe work, create and verify a Snapshot, and
inspect the current campaign rule fingerprint.

Use only the explicit `campaign_rules(action="core_relock")` path supported in
the current phase. Preserve the prior lock, new lock, reason, revision,
idempotency receipt, and Snapshot ancestry. Never relock automatically because
a provider is missing or a test fails.

After relock, re-read derived character and combat state and run the relevant
standard-mechanic regression before continuing.
