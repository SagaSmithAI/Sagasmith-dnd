# Core: context isolation

Treat every SagaSmith campaign context as domain-private. The first campaign
result, normally `campaign_query(view="resume")`, returns an exact
`host_context_binding` for domain, campaign, authenticated-principal
fingerprint, role, audience, branch, memory policy, and context epoch.

When that binding differs from the host session's current binding:

1. stop the remaining tool calls from the same model response;
2. discard prior model messages, summaries, workspace/Dream memory, cached
   retrieval, old receipts, and old tool results for the next inference;
3. rebuild from the trusted host instructions, current user request, the MCP
   result that established the binding, and current bounded Skill fragments;
4. store only the binding metadata in the host session.

Repeat the barrier for campaign, principal, role, audience, branch, restore, or
checkout changes. Never carry DM context into a player-audience render. Campaign
history is `campaign_private`: do not consolidate it into global memory, Dream,
or another campaign's prompt.

For semantic reasoning, pass only the signed MCP bundle to a fresh zero-tool
evaluation. The result is a proposal, never authority. Validate it through
`bounded_evaluation(action="validate")`, execute mechanics through ordinary
public tools, and persist only accepted, actually occurring outcomes.
