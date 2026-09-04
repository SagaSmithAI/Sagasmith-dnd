# Build the locally repaired official expansion library

The shipped metadata-only lock now pins repaired private packages for Eberron,
Ravnica, Sword Coast, Tasha and Wayfinder. It does not contain or download their
commercial text. The library source commit identifies canonical **inputs**, not
a public release of repaired archives; each changed package records its exact
input digest and ordered local repair steps.

Run from a matching D&D workspace environment:

```sh
python packages/mcp/scripts/build_official_expansion_library.py \
  --source-library /private/canonical/content-library \
  --output /private/repaired-content-library
```

The supplied canonical library must contain the exact eleven locally supplied
inputs (ten expansions and the PHB dependency). The builder applies subclass
grants, Artificer ASI and context repairs in the pinned order; six unaffected
packages retain their original bytes. Output archive hashes, package identities,
inner definition checksums and catalog counts must match the shipped lock.
The builder performs default-lock library verification before writing
`repair-report.json`. Existing outputs and source/output overlap are refused.
If interrupted, a partial new directory is retained, not activated; use a new
output path for another attempt. No source archive or existing save is deleted.

After the report confirms `verification.verified`, configure the MCP host's
existing `official_content_library` setting to this output directory and restart
the host. Use the existing `content_pack` activation flow to select the desired
new expansion version for a campaign. Building alone does not activate anything
or alter a campaign lock. There is no legacy-save whitelist or automatic repair
of old selections. Local version suffixes and provisional source-review metadata
are retained honestly; runtime acceptance is not a vendor endorsement or a
redistribution licence.

Do not upload generated archives, source assets or private verification output.
Only these source-only recipes and metadata belong in the public runtime repo.
The source-free builder tests exercise hash, step, path and overwrite guards;
real local archive generation and MCP activation require private integration
validation. Passing library validation is not evidence that all 2014 gameplay
mechanics or a full character build have been implemented.
