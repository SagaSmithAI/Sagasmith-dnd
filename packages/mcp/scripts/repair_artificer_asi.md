# Local 2014 Artificer ASI repair

`repair_artificer_asi.py` repairs two **exact, privately supplied** inputs. It
contains source identifiers, hashes and mechanical data, not the source books.
It neither downloads nor publishes archives, installs rules, changes campaign
locks, migrates saves, nor authorizes redistribution.

Supported inputs:

- Eberron `1.0.3-local.subclass-grants.1`, after the separately reviewed local
  subclass-spell correction. The original legacy archive is **not** accepted.
- Tasha `1.0.1`, identified by the full archive hash in the script.

Run in the repository's Python environment with both D&D packages available:

```text
python packages/mcp/scripts/repair_artificer_asi.py --archive /private/input.sagasmith-pack --output /private/new-output.sagasmith-pack
```

The output must not exist. The script verifies the exact archive and normalized
source section hashes, adds Eberron's missing source-native ASI or replaces
Tasha's non-materialized ASI, and rebuilds the selection review and checksums.
It preserves other artifacts and all source assets. Both cards use the existing
feature materializer: levels 4/8/12/16/19, either +2 or +1/+1, with a score cap of
20. They retain their own source identities and citations.

Outputs use new `local.artificer-asi.2` QA versions. They are **not** a published
library release or a complete official-content repair. Eberron's prerequisite
repair and subsequent index/lock promotion remain separate steps; the current
production lock is not automatically updated. Unrelated misclassified features
and other Artificer mechanics are outside this script's scope.

Focused tests:

```text
python -m pytest packages/mcp/tests/test_repair_artificer_asi.py -q
```

Two real-archive checks are skipped unless `SAGASMITH_ASI_REPAIR_LIBRARY` names a
private library directory containing `index.json` and the exact input archives.
Those checks rebuild in memory and verify preservation, source binding and
deterministic output; they do not test a complete character build or activate
the resulting packages.
