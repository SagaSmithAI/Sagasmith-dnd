# Core: identity and audience

`principal_id` is authorization data, never a character choice or a value
inferred from chat. A multi-user host hides it from the model and injects the
authenticated platform identity. A trusted single-user stdio process binds it
with `SAGASMITH_DND_MCP_BOUND_PRINCIPAL_ID`.

Campaign roles, actor ownership, branch readability, and audience projections
are enforced by the MCP. Never reuse a DM read in player narration. Tool
visibility is not an authorization grant.

Actor knowledge is private per actor. Death, replacement, branch restore, or
party membership never transfers it automatically.
