# Bounded DM ruling

Use `continuity_context(purpose="bounded_ruling")` when the engine explicitly
returns an Agent-owned semantic boundary. Include the current question, exact
evidence, live targets, and state refs; do not use it to replace a player
choice, owner approval, missing/conflicting source review, or a standard
mechanic marked `engine_implementation_required`.

Evaluate as `bounded-ruling-proposal.v1` and validate through
`bounded_evaluation(action="validate")`. The proposal decides semantics only.
Every die, payment, action, HP/resource change, time advance, movement, item
transfer, and condition is executed by the corresponding public engine tool.
After any write, discard the old bundle and read a fresh one.
