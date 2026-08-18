---
name: dnd-dm
description: "Run D&D 5e 2014 or 2024 sessions through SagaSmith's source-bound MCP runtime. Use for live scenes, checks, combat, rests, character resources, module evidence, DM rulings, continuity, actor knowledge, and campaign playthrough regression."
---

# D&D Dungeon Master

Use the `sagasmith_dnd` MCP runtime. Do not emulate a successful state change,
roll, rule settlement, or snapshot in prose or through direct database/CLI
access.

When the Host exposes `submit_room_turn`, also load and follow the system-neutral
`room-host` Skill. Submit attacks, saves, checks, damage, initiative, death
saves, and rerolls as `resolution_ref` blocks using only MCP-returned ids. A
pending reaction, target choice, save, or damage settlement is a `prompt`, not
a completed performance. In Grid mode narrate only authoritative coordinates;
in Agent spatial mode present the DM ruling without inventing coordinates.

## Start with the runtime

1. Read this Skill and only the task-relevant deep reference.
2. Resume with `campaign_query(view="resume")`, then call
   `exposure(action="open")` for that campaign.
3. Search for the smallest useful tool set and change it with
   `exposure(action="set")`.
4. Refresh after `tools/list_changed` and call the listed native tool directly.

Server-owned phase, trusted principal, campaign role, actor grants, revision,
idempotency, source validation, and transactions remain authoritative.

## Route by phase and capability

| Work | Search/add these native tools | Read deeper only when needed |
|---|---|---|
| Scene evidence and narration | `module_query`, `module_search`, `module_expand`, `continuity_context` | `references/RUNTIME_DEEP_REFERENCE.md` |
| Scene/world/knowledge writes | `memory_change`, `campaign_event`, `actor_knowledge_change` | `../../references/memory-ownership.md` |
| Checks and contests | `character_check`, `dnd_check`, `dnd_dice_roll` | `references/DM_RULES.md` |
| Character resources and rests | `character_*`, `campaign_change` | `../../references/character-schema-v2.md` |
| Enter/end combat | `combat_start`, `combat_end`, `combat_join` | `references/RUNTIME_DEEP_REFERENCE.md` |
| Observe, turns, and actions | `combat_query`, `combat_turn`, `combat_*` | `references/DM_RULES.md` |
| Tactical map or Agent spatial facts | map tools or action-specific Agent facts | `references/DM_MAP_SYS.md` |
| Campaign/module preparation | `module_draft`, `content_pack`, `character_*` | `references/MODULE_INDEX.md`, `references/MODULE_ARC.md` |
| Full campaign regression | tools required by the current phase | `references/CAMPAIGN_REGRESSION.md`; for missing mechanical opposition, read `references/OPPOSITION_HYDRATION.md` |

Use `skill_query(action="search"|"section")` for these deep references; do not
load a whole large document by default.

## Keep the adjudication boundary explicit

- Standard locked mechanics execute in the engine. Do not reinterpret them
  from prose.
- Module, addon, and homebrew semantics use exact source evidence. Import,
  review, or card construction must persist an exact-source Agent-ruling
  boundary before the content can be published or used. A constrained typed
  plan may be authored then or compiled by the DM Agent on first use in Lobby,
  Play, or Combat; the engine itself never interprets prose.
- Unique narrative situations default to Agent DM reasoning, followed by
  ordinary public MCP mutations.
- Player choices, owner approval, permission changes, and missing/conflicting
  source evidence remain external-input boundaries.
- Module-authored behavior is DM context, not an executable trigger language.
  Retrieve current context, decide from live state, and persist only the outcome
  that actually occurred.

## Preserve campaign truth

- Treat every PC, NPC, and monster as an independent Character with independent
  ActorKnowledge.
- Carry current campaign/character revisions and stable idempotency keys.
- Let `combat_start` and `combat_end` own Combat phase transitions.
- Use server dice and the campaign random stream.
- Snapshot meaningful boundaries and branches, not every roll or turn.
- After restore, discard old context and revisions, consume `tools/list_changed`,
  refresh the native list, resume again, then use `exposure(search/set)` on the
  existing binding to load the needed current-phase tools. Reopen only for a
  genuinely new campaign/principal binding.
- Keep `standalone/` separate; never silently downgrade Full Runtime.

For exact facade payloads, inspect the selected tool/action and use
`../../references/mcp-contract.md`. For the retained detailed operating manual,
search or section-read `references/RUNTIME_DEEP_REFERENCE.md`.
