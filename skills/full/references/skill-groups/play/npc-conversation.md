# Persistent NPC conversation

Use `npc_conversation` as the only Director-visible MCP facade and read
`../../host-integration-npc-conversation.md` before the first connected
multi-turn dialogue.

1. `open` with every PC and NPC runtime id together in the one
   `payload.participant_actor_ids` array. At least one listed actor must be a
   campaign-bound NPC or monster. Put `idempotency_key` inside the payload along
   with optional `scope_id`, `query`, and `branch_id`:
   `npc_conversation(campaign_id=..., action="open", payload={participant_actor_ids:[pc_id,npc_id,...], idempotency_key:...})`.
   There are no separate `npc_actor_ids` or `npc_ids` fields.
   After a Host or Agent process restart, do not guess or recover the id from a
   transcript artifact. Call `npc_conversation(action="list", payload={})` on
   the retained campaign binding, select the current-branch conversation from
   its public scope/scene/participants, then call `get` with that returned
   `conversation_id`. If more than one public handle could match, re-read the
   current scene and participants instead of choosing by list order. Resume the
   workflow or explicitly `abort` it before any mechanic or phase transition.
2. Rule `audience_facts` from current scene evidence before every `ingest`.
   Perception, comprehension, and response selection belong to the Agent; MCP
   never guesses them. Use the public payload shape exactly; do not invent
   aliases from prose fields:

   ```json
   {
     "conversation_id": "...",
     "event": {
       "type": "speech",
       "speaker_actor_id": "participant runtime id",
       "content": "...",
       "declared_target_actor_ids": []
     },
     "audience_facts": {
       "decision_id": "unique stable id",
       "resolver": "agent",
       "perceived_actor_ids": [],
       "understood_actor_ids": [],
       "response_actor_ids": [],
       "partial_renditions": {},
       "basis_refs": [],
       "reason": "scene-specific ruling"
     },
     "expected_conversation_revision": 1,
     "idempotency_key": "..."
   }
   ```

   `speech` and `action` events require a participant `speaker_actor_id`.
   `understood_actor_ids` and `response_actor_ids` must be subsets of
   `perceived_actor_ids`; response ids select NPC runtimes to activate, not
   every listener or addressed PC.
3. Let the authenticated Host dispatch only returned `activation_ref`
   descriptors through its private, unlisted transport. Pass each descriptor
   verbatim; do not reconstruct it or omit its revision/cursor fields. The
   Director never receives a transport tool.
4. Treat worker output as a candidate. Rule publication audience (per segment
   when necessary), call `publish` with the returned `publication_id`, current
   conversation revision, a new idempotency key, and the same complete
   `audience_facts` shape, then show only MCP `publication`.
5. If a proposal requests a mechanic, stop publication work, select the
   actor-owned and listener candidates that are already valid, and atomically
   `close` the conversation (or `abort` it when no draft should persist). Release
   every worker before settling that mechanic. Also close or abort before a
   participant/scene/branch mutation, Chase, phase transition, or combat.
   Unrelated Play operations may continue; re-query the conversation afterward
   and honor actor refresh or stale invalidation.
6. Resolve the request through ordinary public tools. If dialogue continues,
   open a new conversation and ingest the actual result as a new stimulus; never
   reopen until the authoritative mechanic has committed. Never keep the
   earlier conversation open across that mechanic or a write that invalidates
   its participant, scene, or branch authority.

Never expose the Host transport, private capsule, lease, raw proposal, intent,
truth posture, or basis refs. Never activate every witness. A perceived but
uncomprehended event must contain no raw speech in that actor's inbox.
