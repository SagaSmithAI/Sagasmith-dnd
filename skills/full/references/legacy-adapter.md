# Legacy exposure adapter

Load this reference only when the Host explicitly selects legacy exposure.
The Host owns initialize, session lifecycle, and catalog notifications.
Enable it with `SAGASMITH_DND_MCP_LEGACY_EXPOSURE=1` on the server and the matching
Host adapter. A pre-2026 protocol alone does not enable dynamic exposure. Default
catalogs are stable for all supported protocol versions, including Codex and
Claude clients; do not select this adapter if the Host cannot refresh tools.

1. Open exposure for the authenticated principal and current campaign.
2. Search and set task-relevant tool IDs, then refresh schemas after
   `tools/list_changed` before calling those tools.
3. Reopen exposure when moving from bootstrap into a campaign. One legacy
   session/principal has one active exposure; phase changes can crop it.

Exposure never grants authority. Every business request still requires current
authorization, revision checks, and the original idempotency key for recovery.
Stable-catalog clients do not perform this workflow.
