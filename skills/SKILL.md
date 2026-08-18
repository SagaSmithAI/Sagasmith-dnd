---
name: sagasmith-dnd-suite
description: Run persistent D&D 5e 2014/2024 campaigns through SagaSmith's source-bound MCP runtime. Use for setup, live play, combat, module/rule import, continuity, actor knowledge, branches, snapshots, and restores.
---

# SagaSmith D&D Full Runtime

This repository root is a host-discovery wrapper for the Full Runtime skill.
It is not the portable standalone runtime.

1. Require the `sagasmith_dnd` MCP server. Read `sagasmith://bootstrap` when
   the host exposes MCP resources; otherwise use
   `skill_query(kind="skill", action="read", identifier="dnd.full")`.
2. Call `storage_status`, `server_capabilities`, and `campaign_query`. For an
   existing campaign, use `campaign_query(view="resume")`.
3. Call `exposure(action="open", campaign_id=...)`, search for the smallest
   relevant tool set, and add or remove tool ids with `exposure(action="set")`.
   Refresh native schemas after `tools/list_changed` and call listed domain
   tools directly. A host without mutable native tool lists is unsupported.
4. Never let model-authored text select an authorization principal. A
   multi-user host must inject its authenticated principal; a single-user
   process should set `SAGASMITH_DND_MCP_BOUND_PRINCIPAL_ID`.
5. Read the canonical workflow at `{baseDir}/full/SKILL.md`. Use bounded
   `skill_query(action="section"|"search")` reads for task-specific depth.
   For any isolated semantic decision, follow the returned operation group and
   `{baseDir}/full/references/host-integration-bounded-context.md`. For connected
   multi-turn NPC dialogue in Play, load `npc.portrayal` plus
   `play.npc_conversation` and follow
   `{baseDir}/full/references/host-integration-npc-conversation.md`. Use
   `{baseDir}/full/references/host-integration-npc-turn.md` only for one
   standalone reaction or Combat.
6. Do not silently switch to `{baseDir}/standalone/`. Ask before accepting
   that explicit loss of MCP persistence, permissions, rule locks, combat
   transactions, actor knowledge, and Snapshot guarantees.
