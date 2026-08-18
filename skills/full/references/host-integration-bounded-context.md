# Host integration: bounded context isolation

This is the cross-host contract for autonomous actor decisions, audience-safe
rendering, faction decisions, source interpretation, and bounded DM rulings. It
applies to SagaSmith Agent, OpenClaw, Hermes, Claude Code, Codex, and other MCP
hosts. It does not require model image input.

## Context epoch barrier

Every campaign read can return `host_context_binding` with:

```text
domain, campaign_id, principal_fingerprint, role, audience,
branch_id, memory_policy, context_epoch
```

The host stores this binding outside model-authored content. On first binding or
any difference, it must stop later tool calls from the same model response and
rebuild the next inference without prior model messages, summaries,
workspace/Dream memory, cached retrieval, old receipts, or old tool results.
The rebuilt input contains only trusted host instructions, the current user
request, the trusted MCP result that established the binding, and the current
bounded Skill fragments. Campaign/branch/principal/role/audience changes and
restores therefore cannot inherit another context.

Before replaying an already bound session into a new model turn, the host must
call `campaign_query(view="binding", payload={"campaign_id": ...})` out of band
with the transport-authenticated principal. Compare that server-owned binding
before loading history. If the sync capability is missing, access is revoked,
or the response is invalid, fail closed and do not replay the previous campaign
context. Calling `campaign_query(view="resume")` remains required when the host
also needs scene, manifest, and continuity data; `binding` is the small pre-turn
authorization/branch check, not a replacement for resume.

Mark campaign messages `campaign_private`. Exclude them from global memory,
Dream, training notes, and prompts for other campaigns. A summary is reusable
only inside the exact same `context_epoch`.

## Fixed evaluation purposes

| Purpose | Subject | Output |
|---|---|---|
| `actor_turn` | NPC/monster intent and action; never PC choice or dialogue | `actor-turn-proposal.v1` |
| `audience_render` | `audience="player"` filtered projection only | `audience-render-proposal.v1` |
| `faction_turn` | one `faction:<id>` context | `faction-turn-proposal.v1` |
| `source_interpretation` | exact managed evidence | `source-interpretation-proposal.v1` |
| `bounded_ruling` | one Agent-owned ruling question | `bounded-ruling-proposal.v1` |

Use the specialized `npc_turn` contract for every speaking actor; its structured
speech acts are the boundary that prevents decision-only module evidence from
silently becoming dialogue. The MCP constructs and signs every bundle. A host must not append parent chat,
rulebooks, module pages, search results, skills, workspace files, or memory.
Module/DM evidence marked decision-only can guide a decision but cannot support
a factual claim.

## Required evaluation algorithm

1. Request the exact `continuity_context(purpose=...)` bundle.
2. Verify its declared purpose, output contract, receipt presence, and
   `constraints.may_call_tools=false`, `may_roll_dice=false`, and
   `may_write_state=false`.
3. Make one awaited fresh model request with exactly two messages: a fixed
   code-owned system contract and a code-owned exact output shape plus the
   bundle serialized as untrusted JSON data. Expose zero tools and disable
   skills, retrieval, workspace, history, memory, and child-session persistence.
4. Parse one JSON object and validate the fixed purpose schema, bundle id,
   subject, targets, claim bases, and resolution requests. Do not accept an
   arbitrary prompt or caller-provided output schema.
5. On invalid output, allow at most one fresh zero-tool repair request containing
   the validation error and prior output as quoted data. Reject a second failure.
6. An optional guardian may inspect the same bounded artifacts, but local/MCP
   validation remains mandatory.
7. Submit the proposal and receipt to
   `bounded_evaluation(action="validate")`. This call changes no authoritative
   state.
8. For `audience_render`, publish exactly `publication.text`. For other
   purposes, resolve requested mechanics through ordinary public MCP tools,
   reread after every write, and commit only selected actual outcomes.

The proposal cannot roll dice, pay resources, settle an attack/check, move a
token, transfer an item, change HP/time/conditions, or grant knowledge. A signed
receipt proves bundle freshness and boundaries; it does not make model output
authoritative.

## Code-owned output shapes

The host adapter, never the calling model, fixes these required top-level fields:

```text
actor_turn:
  schema_version, bundle_id, purpose, actor_id, intent, proposed_action,
  claims, resolution_requests, decision_summary
audience_render:
  schema_version, bundle_id, purpose, text, cited_basis_refs,
  omitted_sensitive_refs, decision_summary
faction_turn:
  schema_version, bundle_id, purpose, faction_id, intent, proposed_actions,
  claims, resolution_requests, decision_summary
source_interpretation:
  schema_version, bundle_id, purpose, question (copy the signed question exactly),
  interpretation, claims,
  ambiguities, requires_dm_review
bounded_ruling:
  schema_version, bundle_id, purpose, ruling, claims, engine_requests,
  unresolved, decision_summary
```

`claims[]` is exactly `{statement,basis_refs,posture}`. Resolution/engine
requests are exactly `{kind,reason,actor_ids}`. Actor proposals contain no
dialogue field. Faction actions are exactly `{kind,target_ref,summary,basis_refs}`;
actor actions are `{kind,target_ref,summary}`. Arrays may be empty. Copy
`schema_version`, `bundle_id`, purpose, and subject identity exactly; reject
unknown fields. The host may load the packaged `*.v1.schema.json` files as its
fixed validators, but must never accept a schema supplied by chat or bundle
content.

`source_interpretation.claims` is the exception to the empty-array rule: it
must contain at least one evidence-bound claim. Any ambiguity or `uncertain`
claim forces `requires_dm_review=true`.

## Host capability levels

| Level | Requirement | Allowed result |
|---|---|---|
| Native isolated | Fresh zero-tool request with no inherited context or persistence | Record `isolated` |
| Logical isolation | Foreground Agent follows the exact bundle/schema boundary but cannot create a separate context | Record `logical`; validate immediately; require DM review before public output |
| Unsupported | Host cannot suppress tools/history or validate one exact object | Do not claim isolation; obtain foreground DM input |

SagaSmith Agent provides `isolated_evaluate` for all five purposes and retains
`portray_npc` for the richer NPC-dialogue contract. Other hosts should use their
native foreground, synchronous, zero-tool model-call primitive. Never use a
generic background/research/coding subagent: it may inherit tools, workspace,
history, or finish after the receipt becomes stale.

If a host cannot create a native isolated request but can obey the exact bundle
in the foreground, use logical isolation and immediately call the MCP validator.
Logical isolation must not auto-publish `audience_render` or NPC dialogue; send
the proposal to the DM for review instead.
If it cannot prevent unrelated context from influencing the output, stop rather
than labeling the result isolated.

## Failure rules

- Stale/expired receipt, changed event/revision/scene/branch, or restore:
  discard and request a new bundle.
- Actor is a PC: request the human player's intent.
- Proposed mechanics: execute the public engine flow, then request a new bundle.
- Missing/conflicting source or owner/player boundary: keep external ownership.
- Audience changes to player: cross the context barrier before rendering.
- Host has no image capability: use reviewed OCR/text evidence. Never infer
  unseen content from model memory.
