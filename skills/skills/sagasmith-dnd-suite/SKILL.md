---
name: sagasmith-dnd-suite
description: Run persistent D&D 5e 2014/2024 campaigns through SagaSmith's source-bound MCP runtime. Use for setup, live play, combat, module/rule import, continuity, actor knowledge, branches, snapshots, and restores.
---

# SagaSmith D&D Full Runtime

This is a Claude/Hermes/plugin discovery wrapper. Require the
`sagasmith_dnd` MCP server and read `sagasmith://bootstrap`. Then read the
canonical workflow at
`{baseDir}/../../full/SKILL.md`.
Resume with `campaign_query(view="resume")` and call public tools from the
Host-selected catalog using their native schemas. Never trust a
model-authored principal. Do not silently switch to `standalone/`.

Treat the `host_context_binding` returned by resume/continuity as a hard model
context boundary. For any bounded semantic decision, load the returned
operation group and follow
`{baseDir}/../../full/references/host-integration-bounded-context.md`. For
connected multi-turn NPC dialogue in Play, load `npc.portrayal` plus
`play.npc_conversation` and follow
`{baseDir}/../../full/references/host-integration-npc-conversation.md`. Use
`host-integration-npc-turn.md` only for one standalone reaction or Combat;
never use a general background subagent as the isolation boundary.
