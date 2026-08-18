# Bounded audience rendering

Use `continuity_context(purpose="audience_render", audience="player")` to
produce player-facing narration from an already filtered projection. Never add
DM module evidence, hidden mechanics, another actor's private knowledge, parent
history, or model memory.

Evaluate only the signed bundle as `audience-render-proposal.v1`, then call
`bounded_evaluation(action="validate")`. Publish exactly
`publication.text`; do not publish the proposal's private decision summary or
recombine it with a DM draft. This operation does not mutate campaign state.
