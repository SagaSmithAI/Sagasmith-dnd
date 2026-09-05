# Local Artificer context-boundary repair

This offline script accepts only the three exact private archives listed in
`repair_artificer_context.py`. Eberron and Tasha require the earlier
`local.artificer-asi.2` QA repair; Wayfinder uses its original locked `1.0.1`
archive. It contains hashes and transformation logic, not the books themselves.

```text
python packages/mcp/scripts/repair_artificer_context.py --archive /private/input.sagasmith-pack --output /private/new-output.sagasmith-pack
```

The output path must not exist. The new `local.artificer-context.1` versions:

- Retain the Eberron and Tasha specialist directory headings as non-selectable
  catalog context, without combining their child subclass bodies into a grant.
- Separate Tasha's descriptive spellcasting sidebar from its interrupted tools
  clause; restore the latter's text and exact citations to Spellcasting.
- Keep the real 3rd-level Artificer Specialist feature selectable, correcting
  its display name so it is not confused with the plural directory heading.
- Remove Wayfinder's erroneous Artificer/level binding from mixed dragonmark
  context. The independently stored Storm/Warding species cards are unchanged.

Context cards explicitly use `selection_applicability=not_applicable` and a
non-materializing selection contract. Subsequent authoring must not promote
them back into character options. All original source assets remain intact.
Package versions and contained rule-definition versions are separate versioned
identities; their checksums and manifest versions are rebuilt independently.

No downloads, uploads, campaign activation, lock changes, or save migration are
performed. Publication and production lock promotion remain separate work.
Preserving a species or Spellcasting card does not prove that all its runtime
mechanics are implemented; this repair addresses source boundaries only.

Run focused tests with:

```text
python -m pytest packages/mcp/tests/test_repair_artificer_context.py -q
```

Set `SAGASMITH_CONTEXT_REPAIR_LIBRARY` to an explicitly supplied private input
library (with `index.json`) to enable the three real-archive tests. Without it,
those tests are skipped. They verify source preservation and classification,
not a complete character build.
