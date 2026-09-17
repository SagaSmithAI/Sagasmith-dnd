# Native SagaSmith campaign regression — in progress

## Execution boundary

The user requires `gpt-5.6-luna` and `deepseek-flash` inside SagaSmith's own
AgentLoop/AgentRunner and NPC worker pool. External Codex/Claude CLI campaign
drivers were stopped. Their earlier evidence is not native-host acceptance.

The private harness runs two isolated campaign sessions through one native
AgentLoop, with immutable per-session provider runtimes and filtered MCP
registries. NPC activation uses the native private transport and zero-tool actor
workers; public audit hooks do not record private capsules or credentials.

Campaigns:

- Luna / Lost Mine of Phandelver: `668aff20-df4c-4bd3-8722-05205c2d7d87`.
- DeepSeek Flash / Descent into Avernus: `54574023-85bb-41d3-83c6-8ab9d6e402df`.

Neither campaign has reached a verified ending. Do not treat a model's final
summary, a pause, or an iteration limit as completion.

## Repairs discovered by native execution

1. Agent's separate OAuth cache was expired although the local Codex login was
   valid. An explicit read-only `codexAuthFile` provider setting now supports that
   credential source, re-reading rotations without modifying either cache.
2. Agent unconditionally hid campaign and idempotency inputs even when no trusted
   room envelope supplied them. Local sessions now retain missing inputs; trusted
   supplied fields remain hidden and overwrite model arguments.
3. NPC output examples described object arrays using string elements. The worker
   now sees empty arrays and explicit actor ownership/object requirements.
4. `combat_query` was unavailable in Play, blocking read-only combat audit after
   an encounter ended. Play now permits it; view-specific authority checks remain.
5. `character_check` advertised a facade envelope with `action`, but returned a
   direct committed resolution. The output schema now describes the real result.
   Pending rulings also have a distinct schema alternative, rather than failing
   client validation or being confused with committed outcomes.
6. Guidance now distinguishes `current` scene selection from `in_progress`,
   explains index filtering, exact hazard quotations and condition IDs, and
   noncombat damage parts. Empty optional Skill search filters are normalized.

## Verified live milestones

- Luna's snare save committed once: natural 14, total 15 against DC 10, campaign
  revision 158. A native client output-schema error occurred after commit; the
  original idempotency receipt recovered the outcome without another roll.
- The following pit perception/save/fall damage were exercised with public
  receipts. The agent reached a checkpoint after resolving the trail hazards.
- DeepSeek corrected the earlier unsubstantiated Tarina clue record, then used
  native NPC activation. Publication `23b38539-2c4c-4ce7-aad2-0ff0b2685ad9`
  was generated, validated and published in conversation
  `bdc8d715-e456-4f22-aab8-9a1e40bbef11`.
- Avernus checkpoint slot 6 (`86a5b5d7-b31f-4245-b96f-26addc8feb52`) verified
  at campaign revision 85 after arrival at the bathhouse courtyard.

## Validation and remaining work

- Agent focused provider/MCP/NPC suite: 166 passed, 1 skipped.
- Scene-save integration validates the actual committed response against the
  output schema, including conditional save traits: 2 passed.
- Pending-ruling schema tests: 4 passed; malformed committed results still fail.
- 2,057 captured successful structured responses across 54 tools validate against
  the repaired output schemas. This is retrospective contract evidence, not a
  substitute for continuing native play.

Remaining: complete both source-supported campaigns, audit new failures and
unsupported narrative claims, measure native wait/error rates, and check NPC
lease recovery after interruption. A claimed activation can remain leased after
a hard process stop; that behavior is not yet declared repaired.
