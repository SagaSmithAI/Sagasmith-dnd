# Historical D&D / CoC convergence handover

> Historical record only. This handover described the August 2026 convergence
> work before the MCP 2026-07-28 migration. Its repository layout, commit pins,
> mutable session exposure, and remaining-gap statements are not current
> implementation or deployment instructions.

The completed convergence established shared Core authority concepts, actor
lifecycle and knowledge boundaries, revision/idempotency behavior, private NPC
conversation transport, and deterministic D&D/CoC mechanics. Subsequent work
moved the maintained repositories to the modern request-scoped boundary.

For current instructions use:

- [repository architecture and deployment](../../../README.md);
- [D&D MCP protocol and operations](../README.md);
- [Host integration](../../../skills/HOST-INTEGRATION.md);
- [Full Agent regression](FULL_AGENT_REGRESSION.md);
- [long-term memory contract](long-term-memory.md).

## Current superseding contract

- MCP `2026-07-28` with `server/discover` is the Hosted target.
- HTTP has no authoritative transport session; each request carries fresh,
  target- and audience-bound delegation-v2 identity.
- The MCP catalog is deterministic and stable for an authorization scope. The
  Host connects only the active campaign system and projects a bounded tool
  subset to the model.
- `exposure` is owner-bound expiring catalog guidance on the modern path; it
  neither mutates `tools/list` nor grants access.
- Legacy initialize, session exposure, and `tools/list_changed` remain only as
  an explicit compatibility/rollback adapter.
- Cross-call state uses campaign/revision fields, explicit expiring handles, or
  negotiated Tasks for a genuinely long operation.
- requester/resource-owner/acting-Host/acting-character identities are separate
  trusted fields, never model-authored prompt text.
- stdio and Streamable HTTP use the same handlers, tool schemas, authorization,
  structured errors, standard media results, trace propagation, and metrics.

The original detailed gap list was intentionally removed because it named
archived repositories, obsolete commits, and session-authority behaviors that
could mislead an operator into deploying an unsupported topology. Git history
retains the audit artifact when historical investigation is required.
