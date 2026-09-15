# Host integration boundary

The Host chooses the transport, supplies authenticated identity, and enforces
context isolation before the model sees campaign data. A Skill is not an
authentication, authorization or context-isolation mechanism.

1. Select modern discovery or explicit legacy initialization. Expose a stable,
   bounded task catalog; catalog visibility never grants permission.
2. Hide authoritative principal fields from model choice and bind verified
   delegation on each network request. Do not trust `player_name` or narrative roles.
3. Before inference, compare the signed `host_context_binding` with the active
   campaign/principal/role/audience/branch. On changes, discard old model history,
   summaries, retrieval, receipts and worker context. On failed verification,
   do not invoke the model with retained campaign context.
4. Preserve the entire structured error envelope through the UI and Agent.
   Queued cancellation prevents dispatch; dispatched timeout is an unknown
   outcome. Persist the original operation arguments/key for recovery and never
   mint a new key to retry it. The Gateway does not replay writes automatically.
5. Follow `host-integration-bounded-context.md`,
   `host-integration-npc-conversation.md`, and `host-integration-npc-turn.md` for
   isolated worker lifecycle and proposal validation. Worker proposals do not
   mutate authoritative state until the Runtime accepts them.

Workflow bundles are immutable content-addressed ZIPs. Their manifest covers
entry Skills, references, templates, scripts and binary dependencies. Load a
single installed snapshot per run; explicitly refresh after switching bundles.
