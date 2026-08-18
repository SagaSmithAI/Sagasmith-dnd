# Phase: Play

Play is live non-combat scene resolution. At each turn:

1. Query the current scene, branch, campaign revision, relevant continuity, and
   actor knowledge for the intended audience.
   Cross a changed `host_context_binding` before continuing. Use the matching
   bounded purpose for autonomous actors, factions, source interpretation,
   rulings, or player-facing rendering; never reuse a DM bundle for a player.
2. Retrieve exact module/rule evidence before factual narration or settlement.
3. Resolve automatic standard mechanics with the engine; use Agent DM reasoning
   only at the declared ruling boundary.
4. Apply state changes through the exposed facade.
5. At meaningful scene completion, atomically record event, facts, actor
   knowledge, manifest progress, and a proportionate checkpoint.

For connected NPC dialogue, open one MCP conversation, retain one isolated host
worker per NPC, and publish only MCP-validated publications. Unrelated Play
operations may continue. Close or abort before mutating a participant, the
bound scene, or the current branch; always close or abort before starting a
Chase, starting Combat, or leaving Play. After any concurrent write, call
`npc_conversation(action="get")` and honor actor refresh or stale status before
continuing.

A Chase is an exclusive structured procedure inside Play, not another phase.
No Conversation may remain active when it starts. End and re-query the Chase
before calling `combat_start`; never combine both transitions in one write.

Start Combat only from reviewed canonical actors and scene evidence. Do not
carry a pre-restore context bundle into resumed narration.
