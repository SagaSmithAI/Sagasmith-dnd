# Design audit implementation status

Source: the user-selected conversation **审计并重构设计**
(`6aa8a2ea-bae4-83ec-ad27-a9d912ff92b9`).

The full four-stage refactor is **not complete**. This ledger separates working
changes from the architecture still requiring implementation.

## Implemented

1. Gateway preserves the rejected `CallToolResult` and structured recovery data
   in HTTP responses. Stable catalog dispatch is the default; legacy exposure
   is explicitly selected. The transport still uses the SDK's `ClientSession`
   lifecycle; this is not a new handshake-free transport implementation.
2. Gateway has a bounded queue, admission backpressure, request deadlines, skips
   cancelled queued requests, resolves waiters on shutdown, and never blindly
   retries a dispatched operation. Unknown dispatch results carry the original
   idempotency key. Catalog refresh failure cannot replay an already returned
   business result. Different browser connections no longer share a startup lock.
3. Skills retain immutable document/asset bytes and checksums in one snapshot.
   Refresh builds and swaps a replacement under a lock. The manifest includes
   all installed text assets, including examples and README guidance; package
   hashes change when those dependencies change. Escaping resolved paths and
   hidden installation mirrors are excluded. Text reads preserve universal
   newline behavior; checksums identify the original captured file bytes.
4. Full Skill startup delegates protocol selection to the Host and moves legacy
   exposure procedures into an explicit compatibility reference.
5. Rule-pack tests run for each declared edition and reject assertion-free
   examples. They can assert sheet values, status, modifiers, and pending results.
6. `packages/runtime` owns the existing atomic random/state/receipt mutation
   service, with MCP compatibility imports referring to the identical class.
   A fresh-process test exercises the receipt guard without importing MCP.

## Outstanding acceptance work

| Stage | Work still required |
| --- | --- |
| Contract fixes | Full Host propagation/recovery integration; modern transport adapter; concurrent pool/deadline fault-injection coverage; binary/referenced dependency closure policy |
| Runtime | Extract authorization, revisions, settlement handlers and durable continuations; establish the common trusted command execution boundary; test application workflows without MCP |
| Domain kernel | Register content-specific handlers; unify both rule authoring forms through typed execution primitives; explicit conflict semantics; implementation build digest and version-bound replay/migration |
| Skills publication | Generate tool/phase/error references from one Operation Registry; finish task/bootstrap/Host documentation separation; immutable published workflow bundles |
| End-to-end acceptance | Both protocol generations, real Host identity/branch isolation, reaction recovery without rerolls, extension disable/migration, and read-only shadow settlement comparison |

These items have not been verified and must not be represented as delivered.
No PR, merge, release, or deployment has been performed.

## Validation evidence

- Domain suite: 1,584 passed; after adding modifier/pending assertions, the
  focused rule-engine suite passed 21 tests.
- Runtime independence: 1 passed.
- Final MCP random-stream/config/snapshot/gateway selection: 40 passed.
- Final Gateway selection with explicit legacy coverage: 14 passed (overlaps
  the previous selection; these counts must not be added together).
- Rule-pack/Skill reference/parity selection: 15 passed and one assertion-free
  fixture failed. After replacing that fixture with an exact modifier assertion,
  the failed test passed individually.
- Ruff on all changed Python files and `git diff --check`: passed.

Environment: Python 3.12.13. The required sibling `sagasmith-core` checkout was
missing and was cloned from SagaSmithAI/Sagasmith-core. Its current dependency
metadata raises the pypdf minimum to 6.18.0, reflected in `uv.lock`.
