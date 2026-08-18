# Isolated NPC portrayal

Use this only for an actual NPC/monster turn during Play or Combat. The Agent
chooses intent and portrayal; the engine remains authoritative for mechanics,
state, permissions, knowledge, chronology, and branch revisions.

1. Before the operation, request
   `skill_query(kind="skill", action="plan", campaign_id=..., operation="continuity_context:npc_turn")`
   and complete its bounded reads.
2. Call `continuity_context(purpose="npc_turn", actor_id=<speaker>,
   interlocutor_actor_ids=[...], stimulus={...})` as Owner/DM. Never build this
   bundle in the prompt. Re-read after any event, actor/fact/knowledge revision,
   scene change, Snapshot restore, or receipt expiry.
3. The signed bundle deliberately separates the NPC's card, ActorKnowledge,
   relationships/goals, actor-participating conversation window, public world
   context, immediate perception, current scene projection, and DM-only
   `portrayal_context`. Public world context is safe for DM reasoning but does
   not prove this NPC knows it.
   The NPC's own relationship/goal fact refs are valid internal bases.
   `portrayal_context` guides characterization but is not actor knowledge and is
   not speakable unless the claim also cites a value in `allowed_basis_refs`.
4. If the host provides `portray_npc`, pass the unmodified bundle to it. It runs
   one fresh synchronous model context with zero tools, zero skills/workspace,
   and no child-session persistence. For other hosts follow
   `references/host-integration-npc-turn.md`; never use a general background
   research/code subagent.
5. Treat the returned `npc-turn-proposal.v1` as untrusted intent, not an outcome.
   Verify the bundle/speaker, basis refs, targets, truth posture, proposed deltas,
   and any guardian result. Do not narrate or persist it yet.
6. If `resolution_requests` is nonempty, use the ordinary public MCP check,
   contest, attack, movement, item, or DM-ruling flow. Then read a fresh bundle
   and portray again. A proposal cannot roll dice or settle mechanics.
7. Accept only specific fact/ActorKnowledge indexes. Relationship/goal facts may
   belong only to the speaker; ActorKnowledge may belong only to speaker or
   listeners. Mechanical actions must be executed through public tools first.
   Only `none`, `gesture`, and `refuse` are directly acceptable narrative
   actions. Offer, surrender, movement/flight, attacks, item use/exchange,
   scene transitions, and other actions require an explicit resolution request.
8. Commit with `memory_change(action="commit", payload={"event": {...},
   "npc_turn": {"bundle_receipt": ..., "proposal": ...,
   "accepted_fact_indexes": [...], "accepted_actor_knowledge_indexes": [...],
   "accepted_action": false, "isolation_level": "isolated|logical"}})`.
   Keep `event.summary` observable; never copy private intent/decision text.
   The server derives the dialogue event and participant index, strips private
   reasoning/basis refs from visible payload, and rejects stale or tampered data.

When a state-changing tool returns `narrative_followup.status="agent_review_required"`,
do not invent a module-specific trigger. For each listed actor, request a fresh
NPC bundle using the actual state change as the stimulus and let the Agent decide
whether speech, flight, surrender, bargaining, or no visible response is apt.
`blocking=false` means the completed mechanical write stays valid; it does not
mean the narrative review may be silently forgotten.
