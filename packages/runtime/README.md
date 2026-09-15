# D&D Runtime

Protocol-independent application services between the deterministic domain and
transport adapters. Runtime may depend on `sagasmith-core` and `sagasmith-dnd`;
it must not import MCP, HTTP, gateway, or Host code.

## Current migration boundary

`RandomStateMutationService` owns the existing atomic state/random/replay-receipt
commit and cross-actor inventory custody validation. MCP's `random_state` module
re-exports the same service for compatibility. This preserves one implementation
and one transaction boundary.

Permissions, revision preflight, reactions, and most application handlers still
live in MCP `server.py`. This package is the first extraction, not a completed
Runtime migration or a new public command API.

## Validation

Run `uv run --no-sync pytest packages/runtime/tests` for a fresh-process check
that the receipt guard executes without importing MCP. Existing MCP random-stream
tests verify the same service's end-to-end persistence and replay behavior.
