# Private subclass spell repair prerequisite

`repair_subclass_grants.py` reproduces the previously reviewed local archive
repair from exact Eberron, Guildmasters' Guide to Ravnica and Sword Coast
Adventurer's Guide inputs. Both input and output archive SHA-256 identities
are pinned in the script. It requires locally supplied archives; it downloads
and publishes nothing and contains no book prose.

```sh
python packages/mcp/scripts/repair_subclass_grants.py \
  --archive /private/canonical-input.sagasmith-pack \
  --output /private/new-subclass-repair.sagasmith-pack
```

Use the D&D workspace environment with the matching core dependency. Existing
output paths (including the input path) are refused. The source archive and its
embedded assets remain unchanged. Neither campaign locks nor saved games are
modified, and this is not a legacy-save compatibility mechanism.

The repair replaces `always_prepared_spells` with canonical `spell_grants`
entries retaining each spell's level and access method. Existing known grants
remain known; conflicting grants fail closed. Empty legacy lists are removed
without inventing spells. Sixteen subclass cards are normalized: three in
Eberron, two in Ravnica and eleven in Sword Coast. Only seven have nonempty
always-prepared lists; removing an empty list is not an added class feature.

The seven source clause/table references were checked against the corresponding
normalized 2014 source assets: Eberron sections 364–365, 373–374, 387–388;
Ravnica 174, 184–185; Sword Coast 472, 528–529. This is not a claim of PDF visual
verification or complete implementation of every feature in these books.

Output versions retain the original local QA identities so Eberron's output can
feed `repair_artificer_asi.py`, then `repair_artificer_context.py`, without a new
unnecessary intermediate version. Output checksums bind the original review
metadata; reproducing them does not grant permission to publish source assets.
Promotion to an official runtime lock requires separate integration validation.

Focused synthetic tests need no book material. To also check all three exact
private archive outputs, set `SAGASMITH_SUBCLASS_REPAIR_LIBRARY` to the directory
containing the canonical `index.json`, then run only
`packages/mcp/tests/test_repair_subclass_grants.py`.
