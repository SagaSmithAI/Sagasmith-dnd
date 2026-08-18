# Bounded faction evaluation

Use `continuity_context(purpose="faction_turn", subject_ref="faction:<id>")`.
The bundle includes only that faction's `faction_state` and
`faction_knowledge`; an objective world fact about the same faction is not
automatically the faction's knowledge.

Evaluate as `faction-turn-proposal.v1`, cite only signed bases, and target only
signed refs. Validate with `bounded_evaluation(action="validate")`. Resolve
checks, attacks, transfers, travel, time, or other mechanics through ordinary
public tools. Write actual consequences through stable faction-scoped facts,
events, and per-actor knowledge; never treat the proposal as a completed
faction action.
