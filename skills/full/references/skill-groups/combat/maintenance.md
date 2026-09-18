# Combat runtime maintenance

Runtime maintenance is Owner/DM work, not a combat tactic. Before upgrading the
built-in Core, end or checkpoint unsafe work, create and verify a Snapshot, and
inspect the current campaign rule fingerprint.

Use only the explicit `campaign_rules(action="core_relock")` path supported in
the current phase. Preserve the prior lock, new lock, reason, revision,
idempotency receipt, and Snapshot ancestry. Never relock automatically because
a provider is missing or a test fails.

Check both the Core pack fingerprint and the runtime implementation identity.
An unchanged pack fingerprint does not prove the recorded implementation build
is available. After a verified software upgrade, `get_profile` may return
`effective_error` stating that the historical implementation is unavailable and
the campaign is read-only. A Snapshot reporting `conversion_required=false`
only settles the pack-conversion question. At a legal maintenance boundary, use
the verified current head and current campaign revision to explicitly adopt the
new implementation through `core_relock`; do not restore old story state or wait
for an ordinary gameplay write to fail first. Record the actual upgrade reason.

After relock, re-read derived character and combat state and run the relevant
standard-mechanic regression before continuing.
