---
name: sagasmith-dnd-suite
description: Run persistent D&D 5e 2014/2024 campaigns through SagaSmith's source-bound MCP runtime. Use for setup, live play, combat, module/rule import, continuity, actor knowledge, branches, snapshots, and restores.
---

# SagaSmith D&D Full Runtime

This is a discovery wrapper. Require the `sagasmith_dnd` MCP server, read
`sagasmith://bootstrap`, then call
`skill_query(kind="skill", action="plan")` and read every `required_now`
document. Read the canonical workflow at `{baseDir}/../../../full/SKILL.md`
and use bounded section/search reads only for additional depth.
Stop if the plan reports `available=false`; repair the installed Skills/MCP
pairing before live play.

For an existing campaign call `campaign_query(view="resume")`, open one
campaign-bound exposure, read its phase plan, and load only task-relevant
groups. Read every returned `skill_plan_delta`. Use
the refreshed native list after every `tools/list_changed`. A host that cannot
refresh mutable native tool lists is unsupported; there is no alias facade or
fallback call. Never trust a model-authored principal; bind identity in the host or with
`SAGASMITH_DND_MCP_BOUND_PRINCIPAL_ID`. Do not silently use `standalone/`.

Treat the `host_context_binding` returned by resume/continuity as a hard model
context boundary. For any bounded semantic decision, load the returned
operation group and follow
`{baseDir}/../../../full/references/host-integration-bounded-context.md`. For
connected multi-turn NPC dialogue in Play, load `npc.portrayal` plus
`play.npc_conversation` and follow
`{baseDir}/../../../full/references/host-integration-npc-conversation.md`. Use
`host-integration-npc-turn.md` only for one standalone reaction or Combat; a
generic background subagent is not a safe isolation boundary.
