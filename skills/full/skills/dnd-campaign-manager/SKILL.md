---
name: dnd-campaign-manager
description: "Create and maintain source-bound SagaSmith D&D campaigns. Use for campaign setup, membership, modules and rules, characters, advancement, continuity, actor knowledge, manifests, snapshots, branches, restores, and audits."
---

# D&D Campaign Manager

Use the `sagasmith_dnd` MCP runtime. Campaign truth belongs to the server, not
workspace memory, prose, a local CLI, or direct database writes.

## Start with the runtime

1. Read this Skill and only the task-relevant deep reference.
2. For an existing campaign, call `campaign_query(view="resume")` and discard
   pre-restore or pre-resume assumptions.
3. Call `exposure(action="open")`, search for the smallest relevant tool set,
   and change it with `exposure(action="set")`.
4. Refresh after `tools/list_changed` and call listed native tools directly.

## Route campaign work

| Work | Search/add these native tools | Read deeper only when needed |
|---|---|---|
| Create/list a campaign | `campaign_create`, `campaign_query` | `references/CAMPAIGN_MANAGER_DEEP_REFERENCE.md` |
| Membership, manifest, snapshots, branches | `access_grant`, `playthrough_manifest`, `snapshot_*`, `branch_*` | `references/database-contract.md` |
| Build/import/advance characters | `character_*`, `content_pack` | `../dnd-dm/references/CHAR_CREATION.md` |
| Import and lock rules | `rulebook_draft`, `content_pack`, `campaign_rules` | `../../references/rulebook-import.md` |
| Import modules and assets | `module_draft`, `content_pack`, `module_query` | `../dnd-dm/references/MODULE_INDEX.md` |
| Continuity/knowledge | `memory_*`, `campaign_event`, `actor_knowledge_*`, `continuity_context` | `../../references/memory-ownership.md` |
| Save/restore | `snapshot_*`, `branch_*`, `state_revision` | `references/database-contract.md` |

Use `skill_query(action="search"|"section")` for deep references. Let the MCP's
current native tool list remain the routing source of truth.

## Campaign invariants

- Choose and verify 2014/2024 edition, locale, advancement mode, and locked Core
  provider before importing content or building characters.
- Treat the module's party-size recommendation as advisory. Select any explicit
  positive initial party size and prefer reviewed pregenerated PCs. Never block
  setup merely because the selection is below or above a printed recommendation.
  The selection is planning metadata, not a permanent count: members may join,
  leave, die, go missing, or move to reserve during the campaign. Require at
  least one active PC, and require only the actors participating in a specific
  mechanic to have that mechanic's indispensable data.
- Keep PC/NPC/monster cards, actor access, and ActorKnowledge independent.
- Use exact source references for module metadata, scene progress, rewards, and
  endings.
- Keep rule/module import in Lobby and pass every validation/finalization gate.
- Use current revisions and stable idempotency keys for retriable writes.
- Snapshot meaningful boundaries. Fork important alternatives from a parent
  snapshot; never let sibling branches contaminate each other.
- After restore, verify the new head, consume `tools/list_changed`, refresh the
  native list, resume again, and use `exposure(search/set)` on the existing
  binding for the needed current-phase tools. Reopen only for a genuinely new
  campaign/principal binding. Reread campaign, characters, module progress,
  continuity, and actor knowledge.
- Treat the playthrough manifest as progress/audit state, not an alternative
  mutation channel.
- Keep `standalone/` separate and never claim it has MCP persistence or
  transaction guarantees.

For exact facade contracts, inspect the chosen operation and consult
`../../references/mcp-contract.md`. For the retained detailed procedure,
section-read `references/CAMPAIGN_MANAGER_DEEP_REFERENCE.md`.
