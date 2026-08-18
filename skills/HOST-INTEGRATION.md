# Host integration

SagaSmith Full Runtime requires both this Skills repository and the
`sagasmith_dnd` MCP server.

## Required boundaries

- Inject the authenticated principal in Host code. Never expose authorization
  identity as a model-controlled argument.
- Treat `campaign_query(view="resume")` and its `host_context_binding` as the
  context barrier. Rebuild model context when campaign, principal, role,
  audience, branch, restore state, or context epoch changes.
- Use fresh, zero-tool evaluation for signed bounded semantic bundles.
- Keep the private NPC transport out of model schemas and use one zero-tool
  context per `conversation + NPC`.
- Preserve native MCP content blocks. A combat render returns text metadata plus
  `ImageContent`; store or send that media through the Host's ordinary attachment
  path instead of flattening it into JSON or exposing a local artifact path.

## Mutable native tools

Each MCP connection owns one session-aware exposure:

1. Verify initialization advertises `capabilities.tools.listChanged=true`.
2. Verify the first `tools/list` contains only `exposure`,
   `server_capabilities`, `storage_status`, `campaign_query`, `game_phase`, and
   `skill_query`.
3. Call `exposure(action="open", campaign_id=...)`.
4. Discover exact tool ids with `exposure(action="search")`.
5. Add or remove them with `exposure(action="set")`.
6. On `tools/list_changed`, replace native schemas and call listed tools
   directly.

The mutable native list is the sole exposure mechanism. A host that cannot
refresh it does not support the D&D runtime contract.

## Host smoke test

1. Read `sagasmith://bootstrap`, or use `skill_query(read/search/section)`.
2. Resume a campaign and open its exposure.
3. Search for one domain tool, add it, and verify native refresh.
4. Switch Play -> Combat -> Play and verify incompatible tools disappear.
5. Verify a forged principal cannot replace the authenticated identity.
6. Change branch or audience and verify the Host context resets once.
7. Validate one zero-tool bounded proposal and reject it after its receipt is
   stale.
8. Run one NPC activation and verify parent history and another actor's private
   knowledge are absent.
9. In Combat, dynamically load `combat_query`, request a `party_public` render,
   and verify the Host can send its native image content without revealing a
   hidden actor. Treat render failure as a media failure, not a combat failure.

## Supported discovery layouts

Codex uses `.codex-plugin/plugin.json` and `skills/sagasmith-dnd-suite/`.
Claude Code uses `.claude-plugin/plugin.json`, `skills/`, and `.mcp.json`.
OpenClaw and Hermes may install the repository root as a Skill. In every case,
configure the MCP connection and principal binding separately.
