# Snapshots and branches

Create checkpoints at meaningful scene, combat, chapter, restore-risk, or
branch points rather than after every trivial call. A Snapshot captures the
campaign, character state, manifest, continuity, random position, rule locks,
module revisions, and parent DAG identity.

Verify before restore. After restore or checkout, discard cached state and
consume `tools/list_changed`; refresh `tools/list`, call
`campaign_query(view="resume")`, cross any changed host context binding, and
read the current campaign, branch, scene, characters, and continuity again.
Keep the existing exposure binding and use `exposure(search/set)` to load the
needed current-phase tools. Call `open` only when campaign or principal binding
actually changes.

Use a parent Snapshot to create isolated alternative routes. Checkout and
restore never merge sibling state implicitly. Compare branches through the
public facade and keep excluded route choices explicit in the manifest.
