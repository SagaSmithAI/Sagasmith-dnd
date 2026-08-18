# Bounded actor evaluation

Use `continuity_context(purpose="actor_turn")` for an autonomous NPC or monster
decision. Human-owned PCs are never eligible: obtain their player's intent.

Evaluate only the returned signed bundle with the fixed
`actor-turn-proposal.v1` contract. The actor may use its own state and knowledge;
module evidence can guide the DM decision but cannot become a factual claim or
ActorKnowledge. Cite only allowed basis refs and target only allowed refs. This
generic contract proposes intent and action, not dialogue; use the stricter
`npc_turn`/`npc.portrayal` contract whenever the actor will speak.

Submit the proposal to `bounded_evaluation(action="validate")`. An attack,
move, flight, item operation, surrender, offer, scene transition, or other
mechanical/consequential action must request public resolution. Use the normal
engine tool, re-read after state changes, and persist only accepted facts,
knowledge, and observable events.
