# Continuity reads

Use campaign events for what happened, campaign memory for current objective
truth, actor knowledge for one actor's beliefs, and scoped scene/module state
for local progress. Workspace memory is not campaign state.

Call `continuity_context` with the current branch, audience, actor, scope, and
relevant entity references. DM context may include pinned exact module
evidence. Keep the returned signed context receipt with the context bundle.

On a new session, restore, checkout, or phase transition, re-query authoritative
campaign state and continuity. Never narrate from a cached bundle belonging to
another branch, principal, actor, audience, or revision.
