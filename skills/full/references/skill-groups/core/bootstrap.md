# Core: bootstrap

Use SagaSmith only through the public MCP contract. On a cold connection:

1. Read `sagasmith://bootstrap` when MCP resources are available.
2. Call `storage_status`, `server_capabilities`, and
   `campaign_query(view="list")`.
3. For an existing campaign call `campaign_query(view="resume")`.
4. Call `exposure(action="open")` for the trusted principal. Before campaign
   creation, add only campaign-bootstrap tools; after creation reopen with the
   new `campaign_id`.
5. Search for exact tool ids, then add or remove them with
   `exposure(action="set")`.
6. Refresh `tools/list` after `tools/list_changed` and call listed domain tools
   directly.
