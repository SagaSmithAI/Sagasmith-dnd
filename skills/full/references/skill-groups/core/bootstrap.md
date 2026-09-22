# Core: bootstrap

Use SagaSmith only through the public MCP contract. On a cold connection:

1. Read `sagasmith://bootstrap` when MCP resources are available.
2. Call `storage_status`, `server_capabilities`, and
   `campaign_query(view="list")`.
3. For an existing campaign call `campaign_query(view="resume")`.
4. Use the stable public catalog with trusted request identity injected by the Host.
5. Select the task-relevant public tool and follow its native schema.
6. Re-read authoritative state after transitions; cross changed host context bindings.
