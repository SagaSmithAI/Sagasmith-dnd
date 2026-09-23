# Local D&D completion work — 2026-09-22

This ledger follows the point-in-time organization issue audit. A closed item
requires implementation and applicable validation; pending items remain open.
Host-managed local execution must preserve Runtime authority, real choices,
source evidence, CAS, transactions and original-key replay.

## #113 - source-bound 2014 Divine Smite (local batch)

Qualifying melee weapon hits offer a persisted `pending_hit` choice before damage.
The owner selects an exact available ordinary or Pact Magic slot, or declines.
The saved command reuses its original attack dice and atomically commits the slot,
per-part radiant damage, target HP, attack payment and receipts. Restart, CAS,
authorization and immutable-intent replay use the existing owned-choice boundary.

Slot scaling caps at 5d8, with one extra die for authoritative undead/fiend types;
critical hits double the dice. Spell/ranged/unarmed attacks and misses do not
offer the feature. Each Extra Attack or reaction hit has its own choice, without
an additional action charge. The Agent procedure now preserves `pending_hit`
ownership. See [the caller contract](../divine-smite-2014.md).

Validation: 55 focused Domain cases passed (Smite, Fighting Styles and Rage).
All 36 focused MCP cases passed (8 Smite plus Bardic Inspiration and Legendary
Resistance regression coverage). The new public cases cover actual source
selection, accepted/declined choices, restart/replay, invalid slots, CAS rollback,
critical damage caps, radiant immunity/resistance/vulnerability, Extra Attack and
readied reaction attacks. Ruff, publisher consistency and whitespace checks pass.
No full local suite or remote CI wait was performed. This is temporary-database
contract evidence, not live-model/deployed-stack acceptance. Core is 1.94.0,
SRD 2014 is 1.48.0, standard 2014 is 1.20.0 and both presets are 2.16.0.
The issue remains open until the requested combined push reaches main.

## #111 - source-bound 2014 Barbarian Rage (local batch)

The selected source feature activates and voluntarily ends through the combat
bonus-action contract. Class-level uses, long-rest recovery and unlimited uses at
level 20 follow the 2014 source. Heavy armor suppresses the three benefits without
preventing activation. Strength checks/saves, Strength melee weapon damage and
per-part physical resistance share the deterministic engine paths. Casting and
new concentration are rejected; activation ends existing concentration and
dissipates held spells without refunding their already-spent slots.

Hostile attack attempts, including misses and attempts before activation, and
actual damage sustain Rage through the next own-turn boundary. Inactivity,
unconsciousness, voluntary ending and the ten-round limit are enforced; selected
Persistent Rage removes only the inactivity ending. A private activation ledger
rejects copied/reset effects. See [the caller contract](../rage-2014.md).

Validation is focused only: 45 related Domain cases passed, followed by all 12
Rage Domain cases including two added missed-attack regressions. All 16 public
MCP Rage cases passed, covering source selection, scaling, armor, concentration,
spell rejection, rest, expiry, restart/replay and injected CAS rollback. No full
local suite was run for this change. These are temporary-database contract tests,
not live-model or deployed-stack acceptance. Core is 1.93.0, SRD 2014 is 1.47.0,
standard 2014 is 1.19.0, and both presets are 2.15.0. The issue remains open until
the requested combined push reaches main.

## #112 - source-bound 2014 Bardic Inspiration (local batch)

The selected Bard feature grants another creature one die, with exact class-level
scaling, hearing/range facts, Charisma uses and Font of Inspiration recovery.
Combat grants spend a bonus action. A private campaign ledger binds the recipient,
source, die, spent state and canonical expiry; copied or reset effects cannot
authorize a roll. Gifts expire after 100 six-second ticks.

Checks, initiative, attacks and saves pause after the d20 for an owned decision.
The projection withholds outcome/DC/AC; accepting rolls one die and consumes it
even on failure, while declining retains it. Natural 1/20 rules remain intact.
Group/contest, object, reaction, concentration, death-save and semantic paths use
the same settlement. A still-failed save can subsequently offer Legendary
Resistance without rerolling earlier dice. Core savepoints support suspension
inside phase transitions; final use, roll effects, receipts and CAS commit
together. The resolving player receives their own settled roll without the DM's
private operation reply. See [the caller contract](../bardic-inspiration-2014.md).

Validation: all 2,161 Domain/Runtime cases and 1,655 MCP cases passed; 32 optional
MCP integrations were skipped. The final recipient-result projection also passes
the focused Bardic Inspiration and Legendary Resistance suites. Coverage includes
public creation and source selection, scaling/rest, combat budgets, expiry, exact
replay, restart, ownership/privacy, sequential choices and injected CAS failures.
Ruff and publisher consistency pass. This is local temporary-database protocol
evidence; live-model campaigns and deployed-stack acceptance are not claimed.
Core is 1.92.0, SRD 2014 is 1.46.0, standard 2014 is 1.18.0, and both presets are
2.14.0. The issue remains open until the requested combined push reaches main.

## #104 - source-bound 2014 Legendary Resistance (local batch)

All 23 bundled leaf statblocks with the exact reviewed trait import their printed
X/Day pool, source references and engine capability. The 2014 Limited Usage rule
restores that pool on a completed long rest. A failed ordinary, automatic, death,
concentration, spell or source-plan saving throw can now suspend its command for
an owned choice. Acceptance changes failure to success without a reroll or an
action/reaction cost; declining retains the original failure.

The attempted command rolls back before failed-save effects settle. Only its
private declaration, recorded dice, guarded revisions and owned decision commit.
Resumption replays the immutable declaration and exact dice prefix, supports
multiple targets and reserves accepted uses until atomic final settlement.
Current authorization, branch, actor revisions and campaign RNG are rechecked;
a current DM can finish a declaration after its former DM loses access. Public
views exclude private continuations and other callers' original operation replies.
See [the caller contract](../legendary-resistance-2014.md).

Validation: all 2,148 Domain/Runtime cases passed, followed by 38 focused Domain
cases covering the final automatic-failure correction. The broad MCP run passed
1,634 cases with 32 optional integrations skipped and five failures. Those five
exposed early entity lookup masking existing parameter/random-stream validation;
the corrected lookup order passes all 29 affected and Legendary Resistance MCP
cases. These include real bundled rulebook import, deterministic RNG, restart,
exact replay, ownership, revoked access, death saves, concentration, two-target
Fireball/Hypnotic Pattern, source-plan continuation and injected CAS rollback.
Ruff, publisher consistency and whitespace checks pass. Evidence is local
temporary-database protocol validation, not live-model campaign acceptance.
Core is 1.91.0, SRD 2014 is 1.45.0, standard 2014 is 1.17.0, and both presets are
2.13.0. The issue remains open until the requested combined push reaches main.

## #106 - selected 2014 Fighting Styles (local batch)

Canonical Fighter/Paladin/Ranger and Champion Additional Fighting Style choices
now share one edition-bound recognizer. Archery adds +2 to ranged weapon cards
and actual attacks once, including attack-ability/override paths. Thrown melee
weapons and spells do not qualify. Existing Defense remains armor-dependent and
non-stacking. Dueling now recognizes all supported classes; Two-Weapon Fighting
accepts canonical `choices.option` as well as the existing legacy representation.

Great Weapon Fighting is an explicit per-attack opt-in. It rerolls each eligible
weapon 1/2 once, retains the replacement even when low, includes critical weapon
dice, and excludes separate additional damage. Receipts retain original/replaced
values and actual RNG order. Shared combat, reaction/Ready, source-plan and
noncombat object paths enforce the selected style and two-handed prerequisites.

Protection opens durable, owned choices before attack RNG, action or ammunition
payment. Grid distance uses full footprints; Agent mode requires complete DM
scene facts. Acceptance guards current participant cards under CAS and spends
only the protector's reaction. Requested actor identity must match window
ownership even when a caller controls a different actor. All choices must finish
before the original declaration resumes; target, weapon and context substitution
are rejected. Pending movement stays suspended through nested reactions. Paid
semantic plans resume the same step. Released Ready spells preserve their paid
state and return a complete resume operation to the Host. Private intents stay
outside player/party views. As with #149, Core's unchanged character writes are
currently used for read guards. See [the caller contract](../fighting-styles-2014.md).

Validation: complete Domain/Runtime passed 2,136 cases. Complete MCP passed
1,626 cases with 32 optional integrations skipped. After the ownership and
resume-request hardening, all 25 focused MCP cases passed, including 13 new
style/Protection cases and the existing Ready-spell release suite. Real seeded
RNG, stale participant CAS rollback, restart, exact replay, owner spoofing,
missing Agent facts, nested movement, spell/semantic continuation and separate
damage dice are covered locally. These are temporary-database protocol tests;
no live campaign, paid-provider or deployed-stack acceptance is claimed.
The final 382-case Domain/Runtime regression, Ruff, publisher and whitespace
checks also pass.
Core is 1.90.0, SRD 2014 is 1.44.0, standard 2014 is 1.16.0, and both presets are
2.12.0. The issue stays open until the requested combined push reaches main.

## #149 - source-correct 2014 creature spaces and squeezing (local batch)

Encounter resolution projects effective actor size and the full square footprint.
Grid routes check every crossed footprint, including cells away from a Large
creature's anchor. Occupied spaces cost difficult terrain once, even when the
ground is also difficult; hostile traversal requires two size categories of
difference and willing occupied endpoints fail before any write. Agent routes
require complete reviewed space segments with actual occupants, passage width
and ground terrain. The engine derives eligibility and costs from current cards.
Only physical obstacles establish grid squeezing; map cropping never implies a
narrow passage. Initial agent passages are explicit encounter facts.

Squeezing persists through exact movement prefixes and restart. Its extra cost,
outgoing/incoming attack modifiers and Dexterity-save disadvantage use shared
Core resolution. Grid attacks and opportunity reaches measure the nearest
occupied footprint cells. Forced displacement respects complete obstacles and
updates squeezing without consuming movement or triggering opportunity attacks.
Normal and source-plan movement bind all current participant cards under CAS;
resumption refreshes and guards those cards before settling the saved remainder.
Since Core currently has no read-only character guard, these unchanged guarded
cards receive revisions in the same transaction. Original-key replay remains
before mutable reads. Source receipts accompany ordinary, semantic and resumed
movement. Agent/Host instructions describe the reviewed inputs and returned state.
Geometry refresh preserves encounter-owned speed effects instead of re-deriving
unrelated turn state from unchanged cards. Player and party-public projections
omit private movement continuations, including hidden future occupants.

Validation: the complete Domain/Runtime suite passed 2,104 cases. The broad MCP
run passed 1,600 cases with 32 optional integrations skipped and 12 failures.
Eight failures were obsolete illegal-path/missing-space fixtures; four exposed
the geometry refresh resetting encounter-owned speed. After the repair, all 75
cases in the affected public regression files passed. A further 22-case movement
checkpoint passed, including all nine new space protocol cases, paused slowing,
resume CAS rollback, source movement and nested opportunity reactions. The new
privacy regression passed. Ruff, publisher consistency and whitespace checks pass.
Core is 1.89.0, SRD 2014 is 1.43.0, SRD 2024 is 1.13.0, standard 2014 is 1.15.0,
and both presets are 2.11.0. Evidence remains local contract/protocol validation;
no paid-model campaign or live-stack rollout is claimed. The issue stays open
until the requested combined push reaches main.

## #148 - source-correct 2014 underwater combat (local batch)

`environment_change(action="water")` records explicit source-reviewed water
facts for canonical actors and approved scene objects. It requires a DM, an
exact active-module excerpt and reason, strict booleans, campaign/actor CAS,
and an original-key receipt in the same transaction. Ordinary character
creation/patching cannot forge this state. Pending combat choices must finish
before their environment changes. Actor cards contain the public rule receipt;
the original module excerpt stays in a DM-only resolution. Local Host fills
metadata and returns all affected actor slices, including multiple actors.

Shared weapon preflight uses the selected card's weapon type, bound SRD/base
weapon source and effective swimming speed. It applies melee and ranged
exceptions, ordinary advantage cancellation, and a no-dice automatic miss
beyond normal range. The attempted attack still spends its action/ammunition.
Agent geometry requires an explicit range classification. Source-object ranged
attacks use a signed DM range ruling without invented coordinates. Spell
attacks retain their separate rules. Fire resistance for fully immersed actors
and objects uses the existing typed-damage ordering, once per damage type,
after a successful save's reduction. It retains environmental provenance and
source-boundary receipts. Object transitions preserve their signed original
profiles while projecting the current immersion state. Dry and 2024 behavior
remain separate; movement and breathing are not inferred from immersion.

Validation: 2,065 Domain/Runtime cases passed, including 43 water mechanics
cases. Seven public Runtime/MCP/Host cases passed with real temporary storage,
real deterministic RNG, restart/replay, stale CAS, receipt rollback, invalid
facts, source-object defenses and combat budget/ammunition spending. The broad
MCP run passed 1,601 cases with 32 optional integrations skipped; its only three
failures were catalog assertions still expecting 79 tools. Updating those to
80 passed all 12 catalog/real-stdio protocol cases. Ruff, publisher consistency
and whitespace checks passed. Core is 1.88.0, SRD 2014 is 1.42.0, SRD 2024 is
1.12.0, standard 2014 is 1.14.0, and both presets are 2.10.0. These are local
contract results, not live-model campaign acceptance. This commit stays on
local main for the requested batch; the issue is not closed ahead of a push.

## #165 — source-correct 2014 Working Together (local batch)

`character_check(action="working_together")` binds every participant to one
exact active-module task. The DM records productive collaboration and the task's
tool, skill and feature prerequisites; Runtime checks eligibility from current
cards instead of accepting supplied bonuses, proficiency or helper eligibility.
Incapacitated helpers and unavailable required senses fail before any roll.
The declared leader, or the unique highest applicable ability modifier, makes
one aided check. A tied selection remains a player/DM choice. Different-ability
skills preserve proficiency; tool checks derive proficiency/expertise from the
card without stacking it. The shared ordinary-check pipeline still owns current
conditions, effects, source sunlight and advantage/disadvantage cancellation.

Leader/helper cards, campaign state, engine RNG, source/rule receipts and the
original-key response commit in one transaction. Injected stale leader/helper
revisions and receipt failure roll back the complete operation, including its
random position. Retrying recovers the same dice; restart recovers the exact
committed result even after a helper's condition changes. A subsequent new
attempt uses the changed card. No noncombat action budget is spent, and combat
requests must use the paid Help task procedure. No unreviewed 2024 behavior is
enabled. DM guidance and generated operation contracts describe both this
procedure and the separate group/passive procedures.

Validation: 2,022 Domain/Runtime cases passed. Four focused public protocol cases
passed, including 2024 rejection and local Host Drow perception with visual and
nonvisual tasks, invisible metadata binding, RNG receipts and restart replay.
The full MCP suite passed 1,597 cases with 32 optional integrations skipped.
Ruff, immutable lock/generated-reference consistency and whitespace checks pass.
Core is 1.87.0, SRD 2014 is 1.41.0, SRD 2024 is 1.11.0, standard 2014 is 1.13.0,
and both preset packs are 2.9.0. These results are local contract/protocol proof;
paid-model and live-campaign acceptance remain separate. The implementation is
committed locally on main for the requested batch, without a new issue/PR or push.

## #164 — authoritative 2014 passive checks (local batch)

`character_check(action="passive")` resolves an exact active-module task with
the ordinary actor check modifier pipeline, without consuming RNG. It supports
all six abilities, all skills, DM-reviewed different-ability skill variants,
proficiency/expertise/half proficiency and Jack of All Trades, equipment,
exhaustion, conditions, active effects and locked rule-pack modifiers. Net
advantage/disadvantage contributes +5/-5 and cancels normally. The task carries
its source excerpt, reason, DC or opposing actor; caller-computed totals and
proficiency/bonus overrides are rejected. Opposed ties preserve the situation.
Blinded/deafened observers require explicit sensory task facts; reliance on the
unavailable sense fails automatically instead of producing a numeric comparison.

Secret checks default to a DM-only resolution receipt and presentation. Player
campaign views omit the resolution, source and hidden DC. The campaign, all
observed actor revisions, audit and retry response commit atomically. The same
original key recovers the result after restart; a stale card or receipt failure
rolls the entire transaction back. Passive checks cannot bypass combat procedures.

Derived passive Perception, paid Hide observers and chase pursuers use the same
resolver. Context-dependent card values are explicitly unresolved rather than
invented. Chase scores refresh from current actor cards, including exhaustion;
an unknown passive score only blocks an actual unseen-quarry escape comparison.
Automatic sensory failures are distinguished from missing facts. Source-bound
sunlight context is accepted for Hide/chase and its local revision metadata is
filled by the Host. The generic sourced task also covers trap detection; trap
trigger, damage and disarming automation remain the separate open #161 scope.

The shared effect reader also fixes a suffix bug that treated a disadvantage
effect as advantage. Empty effect lists no longer trigger repeated full-sheet
validation in each modifier reader. A local 100-card cProfile probe dropped from
700 to 400 sheet validations (1.643 s to 1.029 s); this is a synthetic microprobe,
not a paid-model or live-campaign latency claim.

Core is 1.86.0, SRD 2014 is 1.40.0, SRD 2024 is 1.10.0, standard 2014 is 1.12.0,
and both preset packs are 2.8.0. Immutable catalog locks and generated operation
contracts are refreshed. Changes are retained locally for the user-requested
batch; no new issue/PR or individual push is part of this work. CI is not a
completion gate for this batch.

Validation: all 1,998 Domain/Runtime cases passed. The full MCP run passed 1,589
cases with 32 optional integrations skipped and one existing save-damage test
double failing because it assumed every actor check was a target saving throw.
Scoping that double to saving throws restored its focused regression. Three
additional public Drow passive-check cases passed, including local Host metadata
binding, nonvisual checks and restart replay. The final passive Domain suite also
covers automatic escape when every pursuer's sourced perception task fails due
to an unavailable sense. Ruff, publisher consistency and whitespace checks pass.
These are local protocol and contract results, not paid-provider or live-session
acceptance.

## #173 — exact Tasha Battle Smith source and lifecycle

The immutable local Tasha repair verifies its input archive, normalized source
asset, section and chunk hashes, and preserves the original assets byte for byte.
It separates the merged Tool Proficiency records, binds Smith's Tools and martial
weapons, normalizes all five always-prepared spell thresholds, and restores the
3/5/9/15 feature levels and Extra Attack grant. Battle Ready recognizes only the
reviewed Eberron or Tasha feature/package identity; direct actor ingress cannot
forge either grant. The source cards for Arcane Jolt and Improved Defender retain
their explicit Agent ruling contract. This does not certify new native executors
for those abilities.

The reviewed Tasha Defender uses the owner's spell attack modifier and its own
PB expressions. Scalar save, skill and passive totals are evaluated without
collapsing damage/healing dice or joining source fields. Its source Hit Dice grant
survives the HP expression parser and tracks Artificer level; owner advancement
preserves spent Hit Dice, current HP and daily resources. Runtime accepts the
exact reviewed Tasha template hash while keeping source receipts, owner
entitlement, branch authorization and the independently reviewed Eberron policy.
Tasha owner death perishes the Defender; the older Eberron policy stays independent.

The real catalog acceptance also exposed a missing SRD Component Pouch inventory
contract. The catalog compiler now binds that exact item to the reviewed material
role. Purchasing it through public content application enables ordinary material
components; a mundane pouch still cannot substitute, and cost/consumption and
hand constraints remain in the shared component resolver.

These changes version Core at 1.85.0, SRD 2014 at 1.39.0, SRD 2024 at 1.9.0,
standard 2014 content at 1.11.0 and both preset packs at 2.7.0. The new private
Tasha archive is 1.0.2-local.battle-smith.1; the official lock and deterministic
builder compose its source repair. No commercial archive is committed here.

The exact-source acceptance explicitly combines the locked Eberron base Artificer
class and PHB spell source with the Tasha subclass: the Tasha archive has no class
artifact. Private-library tests must pass separately from public synthetic tests;
neither category is paid-provider or live-campaign acceptance.

Validation: the two real private-library protocol cases passed. They cover
missing class choices, exact 3/5/9/15 grants and all spell thresholds through
level 17, full subclass-before/after-level card parity with distinct equipment
identities, mundane/magic Battle Ready weapons, entitlement and duplicate
Defender rejection, scaling, initiative/command/Dodge, Rend/Repair/Deflect,
Mending, rest/revival, long-rest replacement, injected receipt-failure rollback,
same-rest rejection, owner death, stale revisions, original-key replay, restart
and snapshot integrity. Failed harness attempts remain separate from this final
fresh-database lifecycle pass; no database edits or authority bypass were used.
All 1,879 Domain/Runtime cases are covered by the full run plus the corrected
version-assertion suite (8 passed). Full MCP regression passed 1,586 cases with
32 optional integrations skipped. Ruff, immutable lock/generated references and
whitespace checks passed. Private source and test databases remain local.

## #168 — source-bound player Drow Sunlight Sensitivity

The standard 2014 Drow build grants a passive reviewed mechanic with the PHB
page 24 source and explicit observer-or-subject scope. This corrects an ambiguity
in the issue's source summary: the bundled SRD monster wording is self-only and
must not be broadened into the player trait. Unknown/legacy variants require
source reconciliation. The reviewed player mechanic is recorded in Core 1.84.0;
dependent built-in catalogs/presets and immutable lock bindings are versioned.

Weapon, spell, reaction, readied and scene-object attacks derive disadvantage
from the current authorized observer and target facts. Wisdom (Perception)
checks identify their subject and sight reliance; nonvisual checks are not
penalized. Group checks and contests validate every affected observer before
any dice. Normal advantage/disadvantage cancellation remains shared with other
conditions, visibility and source-reviewed advantage.

Runtime requires an exact active-module chunk/excerpt plus a bounded DM ruling,
or an authentic signed review. It rejects invented excerpts, computed modifiers,
wrong subjects/branches, forged signatures and player-authored illumination.
Reviews expire when the campaign or observer revision changes. Local Host binds
these revisions before freezing the operation journal; original-key replay
preserves the committed result. Observers stay under CAS even when a check has
no card expenditure; a failed CAS rolls back the check, action and random stream.
The engine does not infer illumination from coordinates or narrative prose.

Ready release can refresh only current illumination: `sunlight` for a weapon
attack, `sunlight_contexts` for stored rays, then fresh context on each remaining
ray. Original targets, source, payment and all other attack context stay fixed.
Expired facts return `pending_ruling` without spending the held reaction/energy.

Validation: all 1,856 Domain/Runtime tests and 34 focused public Runtime/MCP,
Ready and payload-contract tests passed. The focused checks include real Drow
content application, restart, target-only sunlight, object attacks, source
forgery, movement expiry, nonvisual Perception, CAS rollback, local Host metadata,
multi-ray release and exact settlement replay excluding Host telemetry.
The full MCP suite passed 1,582 tests with 30 optional integrations skipped and
no failures/errors. The final focused Domain suite passed 28 cases, including
unseen and reviewed Pack Tactics advantage cancellation. Ruff, generated
references, immutable content lock and whitespace checks passed.
This evidence does not represent paid-model or live campaign acceptance.

The exact pushed commit `eaad5c4b02f32d7b7d58d1f5c6f83420732b3fce`
also passed all four jobs in [CI run 35741122085](https://github.com/SagaSmithAI/Sagasmith-dnd/actions/runs/35741122085),
including locked Python 3.11, compatibility Python 3.12, Skills and UI.

## Completed

### #127 — require structured new Help declarations

New Help requests must identify attack or task Help. An omitted or empty payload
and an explicit legacy kind now fail before any persisted action spend or state
change. Existing legacy encounter flags remain readable; new requests cannot
create them through the compatibility path.

Validation: the complete `test_combat_engine.py` and
`test_search_action_mcp.py` suites passed, plus Ruff on changed Python files.
New regressions cover omitted/empty/legacy declarations, unchanged input and
campaign state, a successful corrected request with the same key, exact replay,
and stale-revision rejection. Existing bound-target, task-consumption and legacy
snapshot tests remain green. This is domain/MCP evidence, not a live LLM session.

## Next established gaps

The P0 object settlement gap #158 is implemented below. The remaining P1/P2
gameplay items in the audit still require their own implementation and evidence.

Other valid gameplay and content-publication gaps remain in the
[audited issue list](2026-09-22-local-first-issues.md). The broader Agent upstream
roadmap and upstream Chroma advisories have their own scope and evidence gates.

## #158 — source-bound 2014 scene-object settlement

Object damage has a dedicated Domain resolver. It applies automatic poison and
psychic immunity, reviewed damage-type and tool applicability, resistance then
vulnerability, and a nonnegative threshold against the adjusted total of one
attack/effect. Equal threshold damage applies in full. Critical dice still
double, while objects never acquire creature death saves, concentration, target
conditions or generic on-hit effects. Unsupported special weapon effects and
matching attack-after extensions stop before RNG or payment. Large-or-smaller
sections retain independent HP; the Runtime does not infer parent destruction.

First use requires DM review of the complete profile with an exact active-module
chunk and source excerpt. Runtime signs the immutable AC/maximum HP, material,
size/resilience, threshold, defenses and applicability. A character controller
may subsequently reference that profile but cannot replace it or self-author
attack advantage/disadvantage. Circumstance modifiers require a fresh signed DM
ruling bound to the exact attack. Source facts inherit across forks; mutable HP,
CAS, RNG and operation receipts remain branch-scoped. Legacy unsigned records
require review without resetting HP or replacing original source/AC/maximum HP.
Player responses exclude object statistics, source excerpts, DM rulings, private
character data and full transaction receipts. This projection also applies to
replay after restart and after a former DM loses that role.

Ammunition, Recharge uses, actor CAS, object HP, random progress and the exact
replay response commit together. Even an attack with no resource expenditure
checks the attacker revision inside that transaction. Local Host now supplies
the previously missing nested campaign revision for source-object attacks, in
addition to the actor revision, and freezes both with the original operation ID.

Core 1.83.0 records the exact bundled 2014 Objects rule. The dependent built-in
catalog/preset versions and runtime source bindings were refreshed together;
original imported source packages and campaign locks were not rewritten.

Validation: all 1,830 Domain/Runtime tests passed, including 42 focused object
rule/authority cases. The broad MCP checkpoint passed 1,571 tests and skipped 30
optional integrations. One Windows worker crashed in addon selection and one
starting-equipment restart raised a TypeError during content
loading; the complete affected files passed independently (21 and 1 tests).
That broad run is not recorded as a clean pass. After the player projection was
added, all 10 object MCP cases passed, including ordinary and role-downgraded
replay, local Host lost-response recovery, and attacker CAS with and without
resource spend.
All 108 Runtime tests passed again after that projection change.
Ruff, generated references, official content lock and whitespace checks passed.
Remote CI run 35735904338 at commit dd6f19ca also passed all four jobs, including
both Python environments, Skills and UI.
This is local contract evidence, not a live model campaign acceptance claim.

## #139 — settle the original paid Ready spell

Ready stores the original source card, paid cast level, component/payment
receipt, declaration and Magic Missile allocation. Holding energy only starts
holding concentration; automatic protective effects are deferred until release.
Release rejects replacement declarations and validates the stored contract
against the current encounter through the normal spell resolver. It spends the
reaction and settles healing, saves, native effects, or the first spell attack
in the same transaction, without a second component/slot/action payment.

Remaining rays keep their source snapshot and original target/context sequence.
They use the normal attack resolver after real defense and concentration choices.
The first ray's resolution ID is stable across rollback and retry. Decline
rearms the same commitment; lost concentration, missing reactions and next-turn
expiry retain the existing authority checks. Unsupported and legacy holds return
an explicit no-write ruling while preserving energy, declaration and reaction.

Validation includes six native/structured release paths across restart and
replay, stale revision and declaration rejection, multi-ray target/context
integrity, nested Shield, campaign-stream rollback, failed transaction receipt
rollback, and a sourced Ready mechanic receipt. Existing concentration-loss,
decline/retrigger and expiry coverage remains in the integrity suite.
The real local Runtime Host also executes all three stored Scorching Ray attacks
off turn with caller actor/revision/branch fields omitted, and replays the same
random and affected-state receipts. Owned continuations select their persisted
caster/window owner instead of accidentally selecting the other active actor.
The combined domain/Runtime checkpoint passed 1,787 tests; after the Host
continuation addition, all 101 Runtime tests passed. The final affected domain
suites passed 322 tests and all 12 Ready spell MCP cases passed. The broader MCP
run passed 1,561 tests and skipped 30 unavailable optional integrations; its one
new save-test failure was a nondeterministic fixture assumption, corrected before
the final 12-case rerun. This is not a claim of one clean final full-MCP run.

The preceding component commit's remote CI also exposed an out-of-date bundled
SRD dependency checksum after the Core version changed. Catalogs now receive
new immutable versions (SRD 2014 1.36.0, SRD 2024 1.6.0, standard 2014 1.8.0;
presets 2.4.0). Original expansion package/source checksums are unchanged.
`publish --check` checks this exact native binding before the long CI suites;
`publish --refresh-builtin-lock` requires a new content version for changed
content. Existing campaign locks still require an explicit relock.

This is local automated authority/transaction evidence, not real-LLM campaign
acceptance or a claim that the remaining audited issues are finished.

## #107 / #110 — interruptible movement and weapon reach

Movement now commits only the prefix before the earliest reach exit and retains
the remaining path in a durable continuation. Reactions are offered one at a
time in path order, with the weapon IDs that produced that specific reach exit.
Declining an inner reach does not erase later reach crossings. Whole and segmented
5/10/15-foot weapon paths produce the same options and movement spend.

Runtime resumes inside the existing reaction/choice transaction after nested
defense, damage, concentration and rescue choices have settled. Death, disabled
movement, changed position or newly illegal geometry cancel the remainder at the
committed boundary. Replay preserves the original result and continuation IDs;
no new Host remainder request is required. Current actor projections refresh
weapon options after sheet changes. Resumed movement carries source receipts and
reconciles Witch Bolt concentration in the same commit.

Coordinate-free movement requires explicit boundary distances/weapon IDs and
prefix terrain cost when needed. Missing facts produce a no-write pending ruling;
the Runtime never invents coordinates or puts the actor at an untraveled endpoint.

Validation: all 1,735 domain/Runtime cases passed at the broad verification
checkpoint, and 62 selected MCP cases passed. Additional focused coverage passed
for blocked continuation geometry, terrain billing and all three reach bands.
Public MCP cases exercise lethal/missed/declined reactions, later threats,
restart, exact replay, stale CAS, nested Shield defenses and Help/Sneak Attack
lifecycle. Ruff, generated operation references and whitespace checks passed.
These are executable local contract tests; no live LLM campaign is claimed.

## #109 — source-bound off-turn self-powered movement

Reviewed semantic movement steps can now require the mover's movement, action or
reaction independently of whose turn it is. Payment, volition and the distance
allowance are fixed source-template fields, not caller-controlled bindings.
Runtime binds the exact paid source/plan/application and records the mover's
payment once. Action/reaction allowances do not reuse or drain ordinary turn
movement; movement payment still uses its remaining pool. Compelled self-powered
movement still provokes; external push/pull and teleportation remain exempt.

The existing durable path continuation preserves the source grant across all
reach exits and cancels on death, immobilization, displacement or inadequate
remaining speed. Semantic execution waits for the complete movement, including
later reach windows after the first waiting ID has disappeared. Resumed results
contain the final position and outcome without paying or moving again.

Validation: 1,752 domain/Runtime tests passed, followed by 19 selected MCP tests
and a 38-case MCP movement/transaction checkpoint (overlapping suites). Public
tests cover all three payment types, two weapon reaches, source mismatch,
incapacitated mover no-write failure, stale CAS, restart, exact replay, and
dependent steps waiting until movement settles. Existing push/pull and
teleportation public tests passed. Ruff and generated references were checked.
This establishes the generic source-bound mechanism, not automation of every
spell that can cause movement or a live model campaign.

## #133 — execute the stored non-spell Ready response

New Ready requests validate a complete supported response or an explicit ruling
contract. Release binds the original action, target, weapon and movement route;
a replacement declaration is rejected before payment or dice. Dash, Dodge,
Disengage, structured Help, one weapon attack and up-to-speed movement execute
with the reaction in one mutation. Attack settlement shares the existing
ammunition, damage, source receipts and nested defense path. Movement uses the
source grant mechanism and retains every opportunity boundary. Unsupported
source-specific effects preserve their exact ruling contract and reaction in a
no-write pending state.

Ignoring a trigger now correctly rearms the persisted record rather than a
discarded copy. Reaction loss and next-turn expiry are enforced; readied
Disengage expires at the end of the triggering turn.

Validation: 1,761 domain/Runtime cases and 58 existing MCP regression cases passed
at the broad checkpoint. Seven new public MCP cases passed, including stored
Dash/Attack/movement/ruling responses, decline/retrigger, no-write replacement
and invalid-range rejection, restart/replay, dice rollback with the campaign
stream, and nested Shield. Two local Host attack cases (ordinary and Ready)
passed with Host-managed revisions/branches and persisted dice receipts. Ruff,
generated references and whitespace checks passed. This does not close the
separate readied-spell issue #139 or claim live LLM campaign acceptance.

## #116 — 2014 components before casting payment

Ordinary, item, reaction and readied casting now check effective V/S/M before
payment. Noncombat preflight also precedes elapsed time, so an existing silence
restriction cannot expire during the attempted illegal cast. Source-bound active
effect metadata records speech/hand prevention; functional anatomy and equipped
hand/shield slots determine remaining hand use. Restrained alone does not imply
bound hands. S+M can share its material hand; an S-only spell cannot use a hand
occupied by a focus. The edition-specific Core boundary is published in 1.81.0.

Reviewed inventory roles bind component pouches, class-appropriate 2014 SRD
focuses and exact spell materials. Cost/value and consumed exceptions require
the real inventory item. One casting's component unit is removed in the same
transaction as the slot/use/action and effect, with equipment references cleaned
when the stack is exhausted. Wallet money is never substituted for an object.
Source-unknown statblocks require an explicit DM-reviewed component definition;
confirmation booleans cannot bypass inventory or source checks. Existing reviewed
innate/feature/item waivers remain authoritative. Unsupported source-specific
focus types are not inferred from an item name. Hidden-casting perception stays
separate and uses the effective components after the source waiver. The 2024
confirmation path remains explicitly separate.

Local Host now distinguishes a confirmed no-write ruling from a paid effect
ruling. The former can be reconsidered with current revisions after the missing
facts are supplied; the latter remains a durable replay receipt. Unknown writes
still replay their exact original arguments/revision. Legacy cached no-write
rulings are recovered with the same authorization/timeline checks.

Validation: all 1,786 domain/Runtime cases passed. The broad MCP pass covered
1,578 cases (30 environment-gated skips); its 32 failures were obsolete caster
fixtures or confirmation expectations, and all 32 passed after explicit fixture
updates. All 18 component-specific public MCP cases passed, including combat and
noncombat rejection, stale revision, source forgery, material/slot/effect rollback,
restart/replay, corrected-request retry, Ready, and item component waivers.
The domain/Runtime suite includes real Local Authority execution, cached-ruling
recovery, paid-ruling replay and existing lost-response/dice recovery. Ruff,
generated references and whitespace checks passed. This is local executable
contract evidence, not live LLM campaign acceptance.
