# Rule execution architecture

## Boundaries

| Layer | Responsibility | Entry point |
| --- | --- | --- |
| Domain primitives | Deterministic sheet transitions and resolved instruction validation | `rule_primitives.py` |
| Edition strategies | Explicit 2014/2024 spell-turn and exhaustion policies | `edition_policy.py` |
| Rule registration | Edition selection, identity/source validation, duplicate rejection | `rule_registry.py` |
| Authoring | Event predicates/operations and source-bound typed multi-step plans | `rule_engine.py`, `resolution_plan.py` |
| Execution | Immutable instructions, dependency stages, conflict selection, receipts | `resolution_ir.py`, `rule_schedule.py` |
| Runtime | Authorization, encounter context, random streams, pause/resume, revisions, atomic persistence | `sagasmith_dnd_runtime` |

Healing, temporary HP, conditions, effects, resources and spell slots have one
sheet-transition implementation. Native character/combat functions retain their
source-specific checks and bonuses, then delegate to these primitives. Semantic
encounter adapters resolve authorized actors and delegate to the same domain
functions. Damage, attacks, movement and checks keep their existing domain
implementations; they require encounter context and are not sheet-only operations.

Complex class features remain explicit Python domain functions. This is not a
universal rule language or a claim that every edition-specific content function
has become a data record. The registry unifies identity and edition selection;
it does not replace established native feature dispatchers or Runtime adapters.

## Extension authoring

1. Use event mechanics for bounded predicates and sheet operations. Declare
   `after: [mechanic_id]` when a predicate must observe another rule's result.
2. Use semantic plans for source-reviewed encounter sequences, typed slots and
   backward result references. Results are validated again after substitution,
   before the next primitive runs; failures roll back through the Runtime adapter.
3. Both forms lower to `ResolutionInstruction` and share resolved primitive
   validation. Event aliases (`hp.heal`, `condition.add`, `effect.add`) remain
   supported. Effect IDs must agree with the containing instruction.
4. Optional `editions` restricts applicability. Event mechanics default to the
   active pack edition; existing plans default to both supported editions. Runtime
   binds a plan against the encounter edition and rejects incompatible plans.
5. Core remains the only authority for pack compatibility, dependencies and
   checksum-guarded replacement patches. Do not add a `replaces` bypass. After
   Core composes a patch, dependencies on its old target resolve to the patch ID.

## Deterministic event semantics (compiler version 2)

Only mechanics for the current event participate. Dependencies must refer to
mechanics in that event and must be acyclic. Ready mechanics form a stage:

- Every predicate in a stage reads the same entry state.
- Matching instructions resolve conflicts within that stage, then execute in
  `(priority, id)` order, preserving operation order within each mechanic.
- The next dependency stage reads the resulting state. `after` orders evaluation;
  it does not require the dependency's predicate to have matched.
- Conflict keys are local to an event stage. A later stage is a new settlement,
  not a retroactive replacement of an already executed operation.
- Choices/rulings return the original sheet and mark receipts uncommitted.
  Exceptions never modify the caller's sheet.

This removes the old global conflict switch, where a conflict in an unrelated
event could change predicate evaluation. Rules that formerly relied on priority
alone to observe mutations must declare `after`; priority only breaks ties.

## Immutability and upgrade

Compiled mechanics, plans, bound plans, options and facts own canonical JSON
snapshots. Public dictionary/list access returns detached copies. Mutating a
returned copy is allowed but does not modify the compiled object. Existing wire
shapes and constructors remain compatible.

Event fingerprints include compiler version, selected compiled definitions and
options as well as Core/effective-pack fingerprints. Facts are invocation context,
not rule identity. Default semantic-plan template fingerprints remain compatible;
explicit edition restrictions participate in the template fingerprint.

The existing implementation digest and checkpointed `core_relock` upgrade flow
remain authoritative for saved campaigns. Back up the old environment, explicitly
adopt the new implementation at a checkpoint, and rebuild pending plans/preflights.
Do not edit historical fingerprints or reinterpret stored receipts in place.

## Verification

`test_rule_architecture.py` covers dependency settlement with unrelated conflicts,
snapshot isolation, edition selection, patch dependency remapping, shared
transitions, resource failure atomicity and distinct edition policies.
`test_resolution_plan.py` covers plan/binding isolation, edition rejection and
post-substitution validation with rollback. Existing Domain, Runtime and MCP
suites exercise native rules, encounter settlement and persisted continuations.

Local verification on 2026-09-17 (Windows):

| Check | Result |
| --- | --- |
| Full Domain suite | 1,608 passed |
| Full Runtime suite | 8 passed |
| Full MCP suite | 1,458 passed, 29 skipped |
| Semantic-plan integration after final primitive validation | 15 passed (overlaps MCP) |
| Domain/Runtime Ruff, generated contract, diff whitespace | Passed |
| Domain and Runtime wheel/source builds | Passed |

MCP emitted three upstream Pydantic `TypedDictExtraConfigWarning` warnings.
These results are local verification, not hosted deployment acceptance.
