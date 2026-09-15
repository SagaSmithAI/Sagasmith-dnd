# Legacy exposure adapter

Load this reference only when the Host explicitly selects legacy exposure.
The Host owns initialize, session lifecycle, and catalog notifications.

1. Open exposure for the authenticated principal and current campaign.
2. Search and set task-relevant tool IDs, then refresh schemas after
   `tools/list_changed` before calling those tools.
3. Reopen exposure when moving from bootstrap into a campaign. One legacy
   session/principal has one active exposure; phase changes can crop it.

Exposure never grants authority. Every business request still requires current
authorization, revision checks, and the original idempotency key for recovery.
Stable-catalog clients do not perform this workflow.
