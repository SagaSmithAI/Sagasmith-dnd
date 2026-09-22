---
name: sagasmith-dnd-suite
description: "Run D&D 5e 2014/2024 campaigns using SagaSmith's authoritative Runtime through MCP; route live play, characters, content authoring, continuity, and branch recovery to bounded task guidance."
---

# SagaSmith D&D Suite

## Startup

1. Read `sagasmith://bootstrap`; if resources are unavailable, use
   `skill_query(kind="skill", action="read", identifier="dnd.full")`.
   If required guidance reports `available=false`, repair the installation before play.
2. Read Host capabilities, `storage_status`, and `server_capabilities` once per
   connection. If no campaign is selected, call `campaign_query(view="list")`,
   then select one or create a new campaign. Only resume a known campaign with
   `campaign_query(view="resume", payload={"campaign_id":"<id>","detail":"summary"})`.
   Follow its `read_next` only for needed detail. Omitted state is not empty state;
   avoid a full historical state dump when resuming a long campaign.
   Keep campaign, branch, edition and audience explicit.
   The Host must verify `host_context_binding` and isolate context before inference.
3. Use the stable catalog directly. The Host selects the protocol adapter;
   only an explicitly selected legacy adapter loads `references/legacy-adapter.md`.
4. Load the relevant task group below with `outline`, `section`, or `search`.
   Never load entire rulebooks or all workflow references by default.
5. If MCP is unavailable, use the separate standalone Skill. Never claim
   Runtime transactions, persisted state or successful writes from standalone narration.

## New campaign fast path

- Create the campaign, then import the finalized Pack with
  `content_pack(action="import", payload={"campaign_id":"<id>","kind":"module",
  "source_path":"<archive>"}, expected_revision=<campaign revision>, idempotency_key="<key>")`.
  Activate the returned local `module_id`; do not rebuild a finalized Pack.
- Before creating PCs, read exactly
  `skill_query(kind="asset", action="read", identifier="dnd:full/skills/dnd-dm/references/CHAR_CREATION.md")`.
  Build once, then apply scores and active catalog artifacts. Never guess a full sheet.
  Use existing legal characters or applicable presets when the player chooses them.
- Reuse revision values from successful receipts. Actor writes use actor revisions;
  campaign writes use campaign revisions. Only reread after a conflict or missing state.
  Never send dependent writes for the same actor in parallel or predict revision increments.
- Keep setup proportional to the chosen party; there is no required party count.
  Read the opening scene, prepare only its participants, then begin play. Load later
  encounters and guidance when relevant. Never mark a scene complete without actual play.
- In combat, take the active actor and available action/reaction budget from the
  latest receipt or `combat_state`; never guess initiative order. After a rejected
  action, do not increment the revision or assume its payment was consumed. Use
  the receipt's continuation requirements before taking another action. Re-read
  once when the receipt lacks the next actor; keep dependent campaign writes sequential.
- For catalog equipment, apply once with its declared selection (usually `{}`),
  then equip using the returned owned inventory item ID. Preset Packs need import,
  not activation. Read exact per-action payloads before the first unfamiliar write.
- Search `character_query(view="catalog")` by name with `include_context=false`.
  Set `include_context=true` only when `query` is an exact returned artifact id;
  it is a detail lookup, not a broader search. An absent catalog option does not
  require rebuilding the module: use the source-bound actor preparation route below.
- Asset identifiers use `dnd:full/...`; skill identifiers use `dnd.full...`.
  On an unknown identifier, search/list once and copy the returned id rather than guessing.

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
| Prepare a newly encountered source NPC or monster | `references/module-image-content-review.md`; reuse existing actors/reviews, prepare only the next encounter in `lobby`, then return to play |
| Source-bound Pack authoring | `../dnd-module-generator/SKILL.md`; `references/parsing-agent-edit-loop.md` |
| Detailed mutation and recovery procedures | `references/runtime-workflows.md` |
| Tool parameters, phases and revision fields | `references/generated-operations.md`; exact schemas in `references/generated-operations.json` |
| Host integration and worker isolation | `references/host-sdk.md` |

Use `references/mcp-contract.md` and `references/workflows.md` for additional
examples. Generated operation schemas are the authoritative parameter reference.
