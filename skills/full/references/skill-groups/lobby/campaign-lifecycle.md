# Campaign lifecycle

On the campaign-bound exposure, grant membership through `access_grant` and
keep the current branch explicit. Campaign administration is Owner/DM work;
never infer authority from a display name or model-authored principal.

Use these exact public payloads:

```json
{"scope":"campaign","campaign_id":"<campaign>","principal_id":"<principal>","payload":{"role":"player"}}
{"scope":"actor","campaign_id":"<campaign>","principal_id":"<principal>","payload":{"actor_id":"<actor>","can_control":true,"can_view_private":true}}
```

Actor grants accept only `actor_id`, `can_control`, and `can_view_private`.
Include each permission that this grant intends to change; an omitted permission
retains its existing value. Do not send role/control labels or a permissions
array, and always verify the returned booleans before entering Play.

Use `campaign_change` for campaign state, clock, advancement, party rest,
currency, loot, and world effects. Use `playthrough_manifest` for route and
ending audit state. Use branch and Snapshot facades for isolated alternatives;
restore only verified snapshots and immediately discard pre-restore context.

Campaign rules are locked by exact pack versions and fingerprints. Core relock
is an explicit checkpointed runtime upgrade, never an automatic recovery.
