# Exact Tasha Battle Smith local repair

The transform accepts only the hash-locked private Tasha archive produced by
`repair_artificer_context.py`. It emits a new immutable
`1.0.2-local.battle-smith.1` archive and preserves every original source asset.
The source archive, normalized document, sections and chunks are verified before
rebinding cards. Neither the books nor their extracted text are checked into this
repository.

```text
python packages/mcp/scripts/repair_tasha_battle_smith.py --archive /private/tasha-input.sagasmith-pack --output /private/new-tasha-output.sagasmith-pack
```

The repair separates Battle Smith's Smith's Tools proficiency from the importer's
merged Artillerist card, normalizes the always-prepared spell table, fixes exact
feature levels, declares martial weapons and Extra Attack, and restores the
Defender's AC, action/reaction boundaries, owner entitlement and owner-death
policy. Its lifecycle follows this specific Tasha source; the earlier Eberron
archive retains its independently reviewed policy and attack formula.

The official-library builder composes this step after `artificer_asi` and
`artificer_context`. It checks the resulting package/archive against the shipped
target lock. Already verified target archives for other books can be reused.
The target path must be new. No campaign data, existing archive, production lock
or publication is changed by the repair script.

This archive has no Artificer class artifact. The public-protocol acceptance
uses the explicitly locked Eberron class together with the exact Tasha subclass.
Source-bound ruling cards remain source-bound ruling cards; rebuilding their
grants does not certify a new native executor for every subclass ability.

Set `SAGASMITH_DND_TEST_OFFICIAL_CONTENT_LIBRARY` to the rebuilt private library
and run `packages/mcp/tests/test_tasha_battle_smith_official_archive_mcp.py`.
Private-archive tests skip when that input is absent; the direct-provenance
guards and Domain scalar/weapon regressions run without commercial content.
