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
2. Use the Host's valid authoritative context and phase. Resume with
   `campaign_query(view="resume")` only when that context is missing or invalid.
3. Use the Host-selected subset of the stable public catalog.
4. Call the selected tool directly using its native schema.

Server-owned phase, trusted principal, campaign role, actor grants, revision,
idempotency, source validation, and transactions remain authoritative.

## Route by phase and capability

| Work | Use these public tools | Read deeper only when needed |
|---|---|---|
| Scene evidence and narration | `module_query`, `module_search`, `module_expand`, `continuity_context` | `references/RUNTIME_DEEP_REFERENCE.md` |
| Scene/world/knowledge writes | `memory_change`, `campaign_event`, `actor_knowledge_change` | `../../references/memory-ownership.md` |
| Checks and contests | `combat_check` for combat actors; `character_check` outside combat; `dnd_check` for numeric calculations; `dnd_dice_roll` for raw dice | `references/DM_RULES.md` |
| Character resources and rests | `character_*`, `campaign_change` | `../../references/character-schema-v2.md` |
| Enter/end combat | `combat_start`, `combat_end`, `combat_join` | `references/RUNTIME_DEEP_REFERENCE.md` |
| Observe, turns, and actions | `combat_query`, `combat_end_turn`, `combat_*` | `references/DM_RULES.md` |
| Tactical map or Agent spatial facts | map tools or action-specific Agent facts | `references/DM_MAP_SYS.md` |
| Campaign/module preparation | `module_draft`, `content_pack`, `character_*` | `references/MODULE_INDEX.md`, `references/MODULE_ARC.md` |
| Full campaign regression | tools required by the current phase | `references/CAMPAIGN_REGRESSION.md`; for missing mechanical opposition, read `references/OPPOSITION_HYDRATION.md` |

Use `skill_query(action="search"|"section")` for these deep references; do not
load a whole large document by default.
Use `kind="asset"` for referenced documents; `kind="skill"` searches the Skill
entry itself. For a tool's documentation, section-read
`dnd:full/references/generated-operations.md` with `heading` equal to its tool
name. Combat procedure lives at
`dnd:full/references/skill-groups/combat/turn.md`.
Search line numbers refer to the source document, not a Host spill file. A
spilled JSON envelope can contain that document as one escaped string: do not
pass source line numbers to `read_file` on the envelope. Request the matching
section through `skill_query` instead.

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
- Keep provenance and disclosure aligned: a DM-only event may back only
  DM-scoped ActorKnowledge. `owner`, `party`, `player`, and `public` knowledge
  must cite a player-visible event (or an actor-targeted event). This applies to
  `campaign_event` atomic knowledge, direct `actor_knowledge_change`, and
  `memory_change(action="commit")`; omitted revise fields preserve and validate
  their current source/disclosure values.
- In local-authority mode the Host manages identity, campaign/character revisions,
  branch and stable operation IDs. Supply business intent using the presented schema.
  Other clients must carry the explicit protocol fields their schemas require.
- Use committed affected-state slices and returned inventories directly. Read again
  only when required fields are absent or the binding/revision has become invalid.
- A complete ordinary attack uses `combat_resolve_attack` directly; its internal
  preflight, dice, damage and commit do not require separate model calls. Preserve
  real pending reactions, choices and source rulings.
- Let `combat_start` and `combat_end` own Combat phase transitions.
- Use server dice and the campaign random stream.
- Snapshot meaningful boundaries and branches, not every roll or turn.
- After restore the Host crosses the new timeline binding and rebuilds context.
  Never reuse old facts; read only what the rebuilt authoritative slice lacks.
- Local ambient narration (gesture, tone, explanation) may use valid context without
  a tool or NPC proposal when it introduces no world fact, disclosure, promise or
  state change. Those changes still require authoritative commits. Important NPCs
  retain restricted knowledge and independent evaluation when configured.
- Keep `standalone/` separate; never silently downgrade Full Runtime.

For exact facade payloads, inspect the selected tool/action and use
`../../references/mcp-contract.md`. For the retained detailed operating manual,
search or section-read `references/RUNTIME_DEEP_REFERENCE.md`.
