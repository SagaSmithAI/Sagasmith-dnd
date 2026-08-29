# Host integration: persistent NPC conversations

Use this Play-only protocol for connected multi-turn dialogue. MCP owns the
durable journal, validation, actor-scoped context, and atomic close. The Agent
owns scene interpretation and audience rulings. The Host owns isolated model
workers and ephemeral provider caches.

## Required Host boundary

Require `server_capabilities.npc_conversations.schema_version=3`,
`proposal_contract="npc-conversation-proposal.v5"`, and
`host_transport="private_authenticated_unlisted"`.

- Keep one zero-tool message context per `conversation_id + actor_runtime_id`.
- Never give an NPC worker parent history, workspace access, Skills, another
  actor's context, or state-writing tools.
- Keep `npc_conversation_transport` in Host code only. It must not appear in
  model tool definitions or prompts. Authenticate it with the per-connection
  Host token.
- Give the Director only the public MCP `npc_conversation` facade. The Host's
  private dispatcher accepts an opaque `activation_ref`; neither its transport
  nor bootstrap, lease, raw proposal, private intent, truth posture, or basis
  refs enter Director tool definitions or context.
- Dispose worker contexts after close/abort. KV cache is performance state,
  never campaign authority.

If the Host cannot enforce these guarantees, do not open a conversation.

## Director workflow

1. Call `npc_conversation(action="open")` once with explicit participants and
   an `idempotency_key`. Record `conversation_id` and
   `conversation_revision`.
   On Host/Agent restart, establish the new native MCP session and its campaign
   exposure binding once. Within a retained session, never call
   `exposure(open)` as a phase/restore refresh; consume `tools/list_changed`,
   refresh the native list, and use `exposure(search/set)` instead. Discover
   current-branch public recovery handles with
   `npc_conversation(action="list", payload={})`, then `get` the selected id;
   never scrape a transcript or persist private transport state as campaign
   authority. Recreate workers only from returned activation refs. If an
   interrupted provider call cannot be safely resumed, release any local worker
   context and `abort` the listed conversation using its current revision.
2. For every player/scene stimulus, first rule `audience_facts`, then call
   `action="ingest"` with the current revision and a new idempotency key.
3. Let the Host dispatch only returned activations through its authenticated,
   unlisted transport. An observed NPC is not automatically activated; only
   `response_actor_ids` produce work.
4. A worker result with `publication_ready` is not yet public. Rule audience
   for the publication and call `npc_conversation(action="publish")`. For
   mixed delivery/language, provide `segment_audience_facts`, one entry per
   `utterance_segments` item. Publish only the MCP-derived `publication`.
5. A `resolution_request` ends this conversation transaction. Inspect listener
   and actor-owned candidates, call `action="close"` with only the changes to
   persist (or `action="abort"` to discard the draft), and release every Host
   worker before settling that requested mechanic. Also close or abort before a
   participant/scene/branch mutation, Chase, phase transition, or combat start.
   An unrelated Play operation may continue without globally closing this
   conversation; re-read conversation status afterward and honor actor refresh
   or stale invalidation.
6. Resolve the request through ordinary public mechanic tools. If dialogue
   continues, open a new conversation only after the mechanic commits, then
   ingest the actual result as a new stimulus. Do not carry the prior
   conversation revision or worker context across that write.

Every write requires `expected_conversation_revision` and `idempotency_key`.
Replay an identical request with the same key; on
`CONVERSATION_REVISION_CONFLICT`, call `action="get"` and review current state.
Do not overwrite.

## Agent audience ruling

For each event or segment, submit:

```json
{
  "decision_id": "unique stable id",
  "resolver": "agent",
  "perceived_actor_ids": [],
  "understood_actor_ids": [],
  "response_actor_ids": [],
  "partial_renditions": {},
  "basis_refs": [],
  "reason": "short scene-specific ruling"
}
```

Apply current range, occlusion, barriers, noise, delivery, language, senses,
effects, and explicit source mechanics. Treat declared targets as intent, not
proof of perception or comprehension. Keep `understood_actor_ids` and
`response_actor_ids` within `perceived_actor_ids`; responses must name NPC
runtimes. Use `partial_renditions[actor_id]` only for the limited meaning that
actor obtained.

Do not infer audience from a fixed `whisper` rule, assume all participants hear
normal speech, or assume Common when an actor has no language list. If exact
mechanics are absent, make a bounded DM ruling and cite the current scene facts.
If evidence is insufficient for a consequential decision, request the missing
scene fact or resolve an ordinary check; do not broaden the audience.

MCP projects a separate inbox per actor:

- not perceived: no event;
- perceived only: generic sensory cue, never raw content;
- partial: only the Agent-supplied rendition;
- understood: full allowed content.

## Proposal v5 and knowledge ownership

The NPC worker returns `npc-conversation-proposal.v5`. Every speakable byte
belongs to `utterance_segments`, and each segment requires `text` plus a
`content_mode`: `nonfactual`, `grounded`, `deception`, or `uncertain`.
Grounded, deceptive, and uncertain claims require actor-owned `basis_refs`; those refs and
all targets must remain within the actor capsule constraints. `speech_act`,
truth posture, language, and delivery remain optional expression metadata.
`proposed_action` uses
`summary`, `target_refs`, `settlement` (`narrative` or `mechanical`), and
`mechanic_hint`; a mechanical action requires a resolution request.

MCP is the only semantic validator. The Host checks JSON and capsule identity,
then submits. On `validation_failed`, retain the same lease, give only the
structured issues back to that NPC worker, and retry. Cancel the lease on Host
failure.

An NPC proposal may change only its own actor-state, ActorKnowledge, goals,
relationships, and commitments. It may not write what another actor learned.
After publication, MCP mechanically creates listener knowledge candidates only
for actors that understood a segment. These record “speaker said X” with
provenance; they never assert X is true. The Director explicitly selects any
candidate at close.

## Authority and invalidation

Conversation authority is local: branch, scene/version, participant actor
revisions, and the facts/knowledge actually loaded for an actor. An unrelated
campaign event or revision does not stale the whole conversation. A changed
actor invalidates and rebuilds only that actor runtime; branch or scene changes
invalidate the conversation. After an actor refresh, dispatch only new
activation refs and let the Host create a fresh worker context.
