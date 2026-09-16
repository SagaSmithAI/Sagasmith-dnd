---
name: dnd-hosted
description: Run the invitation D&D beta through a trusted Hosted room with a fixed task projection.
---

# Hosted D&D

Follow room-host for room output, audience and player authorship. The Host supplies
the campaign, decision revision, identity and permitted tools. Use only those tools;
do not negotiate exposure or ask for tools/list_changed. Source references reached
through skill_query supply rule knowledge, never a replacement Host protocol.

1. Read campaign and character state before deciding an action. Use combat_query in
   combat and rule_search for missing evidence. Keep the campaign's locked edition.
2. Use preflight and the native action tool. Do not invent dice, coordinates, targets,
   permission, resources, successful execution or a player's decision.
3. For pending choices or reactions, stop and ask the indicated owner. Use a prompt
   block, not a completed mechanical result. Resume the original resolution only.
4. Cite actual resolution IDs with resolution_ref. Host resolves those IDs through
   authenticated MCP and renders mechanical results. Narrative must agree with them.
5. If a tool is unavailable after a phase change, finish this turn and let the Host
   select the next phase's task. Never expand permissions or simulate that tool.
6. On revision conflict, obtain new context and reconsider the action. Never replace
   the old revision on a command merely to force it through.
7. A failed display or cancelled inference does not undo a committed action. Do not
   repeat a settled action; report the known receipt or the uncertain result.

The beta uses administrator-prepared characters, adventures and reviewed rule
packages. Unsupported mechanics require an explicit ruling or a player question;
do not compile or activate arbitrary uploaded rules during a player action.

DM action requests can start combat when combat_start is available. After entry, return
the real result and continue with a new combat task. Player combat tasks support
attack preflight/resolve, spells, movement, reactions, choices and end turn. Tool
availability never overrides the Runtime's role, phase or actor checks.

Use this routing table; a numeric roll alone never settles a character action:

| Intent | Operation |
| --- | --- |
| Character check, save, death save or Search during combat | `combat_check` |
| Defense reaction | `combat_choice(action="resolve_defense")` |
| Character check outside combat | `character_check` |
| Explicit standalone numeric check with reviewed inputs | `dnd_check` |
| Explicit raw dice expression | `dnd_dice_roll` |

For a standalone numeric check or raw dice request during combat, if dnd_check or
dnd_dice_roll is absent, submit next_task="roll" before any mutating call. The Host
validates one bounded catalog handoff and continues the same request. Do not ask
the player to repeat it, claim a roll happened, or request a handoff after an action
has already been dispatched.
