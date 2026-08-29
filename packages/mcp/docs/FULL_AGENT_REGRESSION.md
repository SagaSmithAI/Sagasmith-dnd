# Full Agent corpus regression

`scripts.regression_agent_corpus` is the opt-in, long-running real-Agent module
playthrough audit. It regenerates the dynamic corpus inventory and coverage
matrix, then runs separate authenticated DM and player sessions against a fresh
MCP home for every runnable campaign line. It never calls an internal service or
supplies the story result itself.

This is release evidence, not ordinary PR feedback. Default CI tests the driver,
fixtures, fail-closed accounting, and protocol contracts without a paid model or
external service.

## Modern Hosted contract

The Agent configuration must use MCP `2026-07-28`, `server/discover`, fresh
per-request D&D-targeted delegation-v2, and the stable deterministic catalog.
The Host connects only the D&D MCP for these campaigns and projects at most 16
sorted task-relevant tools to the model. An ordinary domain write must not alter
the catalog.

Legacy initialize/session exposure and `tools/list_changed` may be tested as an
explicit compatibility lane, but they are not accepted as identity, authority,
or the modern success path.

The regression also preserves native MCP content. A `party_public` combat render
must deliver standard `ImageContent` without hidden actors or server-local
paths. When the module authoring path negotiates
`io.modelcontextprotocol/tasks`, task polling/cancellation uses fresh narrow
authorization and survives an MCP restart.

## Run

From `packages/mcp` in a fully pinned checkout:

```powershell
python -m scripts.regression_agent_corpus `
  --output-dir ..\.runs\full-agent-corpus `
  --agent-config-template C:\secure\nanobot-regression.json `
  --run-id nightly-2026-08-29 `
  --fail-fast
```

The template must define the real `sagasmith_dnd` MCP server and an authorized
model provider. The runner rewrites only per-campaign workspace, fresh MCP home,
module roots, Skills path, principal/delegation injection, and Host tool
projection. Keep credentials outside the checked-in template and output tree.
Never use production campaign data.

Use `--campaign <dynamic-campaign-line-id>` for a focused rerun and `--resume`
to retain already completed campaign reports. `--inventory-only` validates the
catalog contract but is not gameplay evidence.

The real-provider pytest lane is explicitly enabled:

```powershell
$env:SAGASMITH_RUN_FULL_AGENT_CORPUS = "1"
$env:SAGASMITH_AGENT_CONFIG_TEMPLATE = "C:\secure\nanobot-regression.json"
pytest -m full_agent tests/test_regression_agent_corpus_driver.py
```

## Required evidence

Each campaign result retains:

- raw DM/player JSONL transcripts and process stdout/stderr;
- protocol/discovery and model-facing tool-projection timelines;
- requester/acting-Host/campaign/room-turn binding changes without secrets;
- phase, revision, idempotency, random, and authority receipts;
- Task create/get/update/cancel/recovery history when the long workflow is used;
- standard media block/artifact routing evidence for combat renders;
- matrix gaps and the authoritative legal-ending audit.

A model's prose never closes a gap. Success requires real native tool calls,
authoritative ending state, deterministic catalog/projection limits, correct
identity isolation, and preserved standard results. Redact credentials and
private fixture content before retaining or sharing a report.
