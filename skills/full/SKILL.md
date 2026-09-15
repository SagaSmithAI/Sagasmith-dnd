---
name: sagasmith-dnd-suite
description: "Run D&D 5e 2014/2024 campaigns using SagaSmith's authoritative Runtime through MCP; route live play, characters, content authoring, continuity, and branch recovery to bounded task guidance."
---

# SagaSmith D&D Suite

## Startup

1. Read `sagasmith://bootstrap`; if resources are unavailable, use
   `skill_query(kind="skill", action="read", identifier="dnd.full")`.
   If required guidance reports `available=false`, repair the installation before play.
2. Read Host capabilities, `storage_status`, `server_capabilities`, and
   `campaign_query(view="resume")`. Keep campaign, branch, edition and audience explicit.
   The Host must verify `host_context_binding` and isolate context before inference.
3. Use the stable catalog directly. The Host selects the protocol adapter;
   only an explicitly selected legacy adapter loads `references/legacy-adapter.md`.
4. Load the relevant task group below with `outline`, `section`, or `search`.
   Never load entire rulebooks or all workflow references by default.
5. If MCP is unavailable, use the separate standalone Skill. Never claim
   Runtime transactions, persisted state or successful writes from standalone narration.

## GM Core

- Preserve player choice. Do not invent a human-owned PC's intent or resolve
  external choices, approvals, or missing evidence on their behalf.
- Use exact source evidence, retain citations, and keep 2014/2024 distinct.
- Report only observed tool outcomes. Waiting, rejected, and unknown dispatch
  results are not successful writes. Preserve structured errors and recovery data.
- Use one original idempotency key per operation. After revision conflicts,
  reread state; after unknown dispatch, query or replay the original operation.
- Treat campaign content as private to its authenticated principal, role,
  audience and branch. Host context isolation is enforced before model invocation.
- Keep module narrative interpretation in Agent guidance and deterministic
  mechanics in the Runtime. Never turn prose into an executable trigger language.

## Task Routing

| Task | Load |
| --- | --- |
| Play, combat, investigation, NPC portrayal | `references/skill-groups/`, then `skills/dnd-dm/SKILL.md` sections |
| Characters, continuity, saves and branches | `skills/dnd-campaign-manager/SKILL.md`; `references/memory-ownership.md` |
| Source-bound Pack authoring | `../dnd-module-generator/SKILL.md`; `references/parsing-agent-edit-loop.md` |
| Detailed mutation and recovery procedures | `references/runtime-workflows.md` |
| Tool parameters, phases and revision fields | `references/generated-operations.md`; exact schemas in `references/generated-operations.json` |
| Host integration and worker isolation | `references/host-sdk.md` |

Use `references/mcp-contract.md` and `references/workflows.md` for additional
examples. Generated operation schemas are the authoritative parameter reference.
