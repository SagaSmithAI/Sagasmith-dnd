# Adding rule extensions

New content should compose stable engine capabilities. Adding another book,
class, creature or item should not add another branch to a Runtime dispatcher.

## 1. Discover the installed contract

Run `sagasmith-dnd capabilities` and inspect `rule_primitives`, or read
[`generated-primitives.json`](../skills/full/references/generated-primitives.json).
The manifest is generated from the same registry used for plan parameter schemas
and encounter dispatch. It identifies required/allowed fields and execution context.

An opcode being registered does not make it valid in every context. For example,
`attack.ac_bonus` is consumed by the paid attack-defense path, not a general action
plan. General plans are checked for installed handlers before their first step.

## 2. Choose the authoring form

- Event mechanics: bounded predicates, modifiers and sheet transitions. Use `after`
  for dependencies and explicit conflict policies for competing operations.
- Semantic plans: source-bound sequences with typed slots, conditions and backward
  result references. Agent bindings cannot add executable steps.
- Existing native feature implementations: use their reviewed content identity and
  source contract. Do not reproduce a core feature as an Agent-written plan.

Both data forms accept optional exact capability requirements:

```json
{"requires": {"resource.spend": 1, "healing.apply": 1}, "editions": ["2014", "2024"]}
```

Unsupported names or versions fail compilation. Requirements are immutable and
included in the compiled identity. Omitting `requires` preserves existing authoring
compatibility; authors of new packages should declare it. Requirements do not
enable disabled packs, grant permissions, or override rule sources.

[`field-medic.plan.json`](../examples/extensions/field-medic.plan.json) is an
executable synthetic example: validate visibility, consume a supply, then heal.
It is a plan template, not a distributable content package. Its example citation
must be replaced by the actual reviewed source when authoring real content.
The actor must own the recorded resource and activity; Runtime requires the normal
paid action commitment before execution. Failures roll back the current execution
segment. A player-owned choice checkpoints the segment before returning.

### Target facts for semantic plans

`target.validate` never assumes visibility when evidence is absent. Grid range
uses recorded coordinates. Agent positioning uses reviewed distance facts without
creating coordinates. Bind these facts in the source-bound `agent_ruling` of the
paid commitment (the IDs below are placeholders):

```json
{
  "target_facts": {
    "encounter_id": "encounter-id",
    "scene_id": "scene-id",
    "campaign_revision": 12,
    "steps": {
      "validate-target": {
        "source_actor_id": "caster-id",
        "targets": {"target-id": {"visible": true, "distance_ft": 15}}
      }
    }
  }
}
```

Each step key must name a bound `target.validate` step; targets and source must
match its actor bindings. Visibility is boolean and distance is a nonnegative
integer in feet. Runtime validates scene, encounter and revision before payment.
Settlement accepts that same commitment after the payment's single revision
increment. After another UI or Agent write, refresh `target_facts` against the
current revision and omit the obsolete `bound_plan_fingerprint`. Keep the same
application, compiled plan, bindings, decision, and source evidence: Runtime
matches that paid identity without charging again. Missing facts produce `pending_ruling`.
Facts are included in the bound-plan fingerprint and cannot alter executable
steps, bypass known invisibility, or override grid distances.

Semantic `attack.resolve` checkpoints attack dice before a defensive reaction,
and damage before a required concentration save. `execute_plan` returns
`pending_choice` with `application_id` and `waiting_choice_ids`. The owning player
resolves the recorded reaction or concentration save through the normal command.
Then call `execute_plan` with the same application, current revision, refreshed
facts if needed, and a new request key. Completed steps and dice are reused,
including after a service restart. A retry of the same request uses its original
key and returns its original receipt. Ordinary actions remain blocked until the
application finishes; each resumed segment commits atomically.

## 3. Compose packages without global side effects

Use package-qualified rule IDs, effect IDs, counter keys and link kinds, such as
`myaddon.feature.effect`. Shared keys are an explicit cross-package contract;
the engine does not silently rename legacy keys. Declare package versions and
checksums through Core dependencies. Core alone resolves checksum-guarded patches;
there is no last-loaded-package-wins override and no imported Python execution.

Optional installed rule providers remain an operator-managed allowlist. Their
entry-point name must match provider ID; ABI must match. Mechanics are read once
at startup into an immutable snapshot. Changes require installation/restart and
the campaign's normal explicit implementation upgrade. A provider exposes data
mechanics, not permission to register arbitrary opcode handlers.

## 4. Add genuinely new capabilities at the engine boundary

If composition cannot express a mechanic, add a reviewed domain function first.
Register its capability, version and semantic fields in `primitive_contracts.py`;
add the corresponding thin Runtime handler if it needs encounter context. Domain
functions calculate results; Runtime resolves authorized actors, supplies recorded
randomness and persists changes. Keep source-specific features as domain functions
when composition would obscure their rules.

Edition strategies are grouped by family: spell turns/HP, rest recovery, and d20
exhaustion. Source-specific class preparation and feature rules continue to use
their existing domain implementations and profiles. These are not an arbitrary
data-script system; new engine semantics require reviewed code and tests.

## 5. Verify before activation

Test both editions where supported, missing resources, competing extensions,
dependency cycles, unsupported capability versions, source/slot validation and
atomic rollback. Preserve checkpoint, restore and continued-play verification
when execution semantics change. Regenerate contracts with
`python -m sagasmith_dnd_runtime.publish` and verify with `--check`.

Package archives, source evidence, licenses, dependency locks and branch activation
retain the existing content-package workflow. Passing compilation does not imply
authorization, source completeness, branch activation or hosted acceptance.
