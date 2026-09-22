# Local-first issue audit — 2026-09-22

Scope: every open issue in this repository at the start of the audit (45).
Local-first means Host-managed protocol metadata over a single stdio authority.
It preserves source provenance, permissions, branch isolation, CAS, transactions,
idempotency and real player choices. A faster transport does not complete missing
gameplay rules. Catalog entries do not prove executable mechanics.

No gameplay issue is obsolete merely because the architecture became local-first.
The implemented candidates below require acceptance against their complete issue
body, not just a matching function or passing synthetic fixture. In particular,
#110 requires weapon-specific reach boundaries; outermost-reach coverage is not
sufficient. #172 requires complete materialization choices; equipment alone is
not sufficient. #111 must follow the actual Rage source conditions rather than
blindly treating every sentence of the issue as authoritative.

| Issue | Requested behavior | Current disposition |
| --- | --- | --- |
| [#182](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/182) | Model 2014 Tortle Claws as an intrinsic unarmed strike | Closed completed: main and scoped acceptance verified; evidence recorded on the issue. |
| [#176](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/176) | Preserve and execute 2014 City Watch Watcher Eye feature | Closed completed: main and scoped acceptance verified; evidence recorded on the issue. |
| [#173](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/173) | Verify exact-source Battle Smith grants and subclass-order parity | Open (P1, partial): Battle Smith has executable Eberron grants and Steel Defender flows; exact Tasha-source acceptance and complete ordering/rebuild evidence remain unverified. |
| [#172](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/172) | Complete 2014 Artificer character materialization and starting equipment | Closed completed: main and scoped acceptance verified; evidence recorded on the issue. |
| [#170](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/170) | Implement source-correct 2014 Lay on Hands settlement | Closed completed: main and scoped acceptance verified; evidence recorded on the issue. |
| [#169](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/169) | Implement source-correct 2014 adventuring gear settlement | Open (P2, missing): Generic consumables and selected official item effects exist; there is no complete source-bound mundane gear action catalog covering the listed items. |
| [#168](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/168) | Enforce 2014 Sunlight Sensitivity on attacks and sight checks | Open (P1, missing): Sunlight Sensitivity is descriptive content and synthetic test input; no production trait consumer derives attack/sight-check disadvantage from scene sunlight. |
| [#165](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/165) | Implement source-correct 2014 working-together checks | Open (P2, missing): Group checks and combat Help are separate existing features; no noncombat helper/leader/task eligibility and productive-collaboration transaction exists. |
| [#164](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/164) | Implement generic source-correct 2014 passive checks | Open (P1, partial): Derived passive Perception and chase/hide consumers exist; a generic ability/skill passive resolver with net +/-5, secret output and a shared modifier path does not. |
| [#163](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/163) | Implement 2014 lifestyle and downtime activity settlement | Open (P2, missing): Campaign time exists; source-bound 8-hour activity days, crafting costs/proficiency, profession, recuperation, research and training settlement are absent. |
| [#162](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/162) | Implement source-correct 2014 madness effects and cures | Open (P2, missing): No short/long/indefinite madness table executor, source-owned effect lifecycle, suppression or cure procedure was found; Crown of Madness vocabulary is unrelated. |
| [#161](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/161) | Implement source-bound 2014 trap detection, disabling, and settlement | Open (P2, missing): Generic scene hazards/source attacks are not a persisted trap model with detection, disarm, trigger, one-shot and complex-initiative lifecycle. |
| [#160](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/160) | Implement source-correct 2014 poison delivery and lifecycles | Open (P2, missing): Typed poison damage/conditions exist, but doses/coatings, four delivery gates, named poison counters, midnight and protected-damage lifecycle do not. |
| [#159](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/159) | Implement source-correct 2014 disease lifecycles | Open (P2, missing): Generic effects/rests do not implement Cackle Fever, Sewer Plague or Sight Rot incubation, recurring saves, transmission, rest restrictions and exact cures. |
| [#158](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/158) | Fix 2014 scene-object attack and damage-threshold settlement | Open (P0, confirmed gap): Object input still excludes damage_threshold, takes immunities from request data and validates a source citation without binding the supplied numerical object model. |
| [#157](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/157) | Implement source-correct 2014 travel pace and forced-march settlement | Open (P2, missing): The clock advances time but has no party travel distance/pace ledger, passive-perception pace modifiers or hourly forced-march settlement. |
| [#152](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/152) | Implement source-correct 2014 vision, obscuration, and light settlement | Open (P1, partial): can_see handles blinded/hidden/invisible and explicit visibility lists; it does not derive illumination, obscuration, darkvision/blindsight/truesight range. |
| [#151](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/151) | Implement 2014 food, water, and starvation exhaustion lifecycle | Open (P1, partial): Long rests use food_and_drink for exhaustion recovery; daily ration/water debt, hot-weather intake, source-owned deprivation and day-boundary saves are absent. |
| [#150](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/150) | Implement source-correct 2014 long-jump and high-jump settlement | Open (P2, missing): Movement charges path/travel/crawl costs but has no bound run-up and Strength-derived long/high jump declaration or landing-check procedure. |
| [#149](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/149) | Enforce 2014 occupied-space and squeezing movement rules | Open (P1, partial): Willingly occupied destinations already fail; hostile traversal by size, occupied-path terrain cost and squeezing state/attack/save modifiers remain absent. |
| [#148](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/148) | Implement source-correct 2014 underwater combat settlement | Open (P1, missing): Swim movement speed exists; submerged attack exceptions, beyond-normal-range automatic miss and fully immersed fire resistance are not integrated. |
| [#147](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/147) | Implement source-correct 2014 mounted combat lifecycle | Open (P2, missing): Dependent-actor support is not a rider/mount contract; mounting cost, controlled/independent turns and forced dismount saves remain absent. |
| [#146](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/146) | Implement 2014 holding-breath and suffocation lifecycle settlement | Closed completed: main and scoped acceptance verified; evidence recorded on the issue. |
| [#145](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/145) | Implement source-correct 2014 falling damage and prone settlement | Closed completed: main and scoped acceptance verified; evidence recorded on the issue. |
| [#139](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/139) | Execute the predeclared 2014 readied spell on release | Open (P0, confirmed gap): Release still accepts a new declaration, changes the holding effect/reaction and returns ready_release_effect pending_ruling instead of executing the stored spell. |
| [#133](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/133) | Bind Ready release to and execute the predeclared response | Open (P0, confirmed gap): Release still logs/returns caller declaration and spends reaction without executing the original stored action response. |
| [#129](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/129) | Implement source-correct 2014 multiclass advancement | Open (P1, missing): advance_single_class_level still enforces one existing class and equal total/class level; adding a second class and combined spell-slot settlement is not supported. |
| [#128](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/128) | Implement remaining high-level 2014 Rogue mechanics | Open (P1, missing): No source-bound settlement was found for Reliable Talent, Blindsense, Slippery Mind, Elusive or Stroke of Luck; resource/display text cannot satisfy those mechanics. |
| [#127](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/127) | Close the unbound legacy-input gap in 2014 Help | Open (P0, partial): Structured attack target binding and task Help consumption now exist and pass tests. A NEW empty Help payload still creates unbound legacy Help; reproduced in the audit probes. |
| [#123](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/123) | Implement 2014 Rogue Uncanny Dodge as a hit reaction | Closed completed: main and scoped acceptance verified; evidence recorded on the issue. |
| [#116](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/116) | Enforce 2014 spell components before casting | Open (P0, confirmed gap): consume_spell_cast still emits ordinary V/S/M as ruling_required after settlement; authoritative speech/free-hand/focus eligibility is not a complete pre-spend gate. |
| [#113](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/113) | Implement 2014 Paladin Divine Smite settlement | Open (P1, missing): Paladin source content exists but there is no source-bound post-hit Divine Smite choice, slot spend and radiant damage settlement. |
| [#112](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/112) | Implement actual 2014 Bardic Inspiration settlement | Open (P1, partial): Bard use pools and die scaling exist; grant/range/hearing, ten-minute recipient state and post-d20/pre-outcome spend windows are absent. |
| [#111](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/111) | Implement actual 2014 Barbarian Rage settlement | Open (P1, incorrect requirement): Rage still needs an executor. Correct the issue: heavy armor gates the listed benefits, not activation itself; honor the 15th-level Persistent Rage exception to early ending. |
| [#110](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/110) | Bind opportunity-attack triggers to weapon reach | Open (P0, partial): Weapon IDs/reach are recorded, but max(reach) selects the outermost crossed boundary. Probe: whole path first offers long; split path first offers short/unarmed. Full path equivalence is not fixed. |
| [#109](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/109) | Model off-turn self-powered movement for opportunity attacks | Open (P0, confirmed gap): Voluntary/aggressive movement remains current-turn-only; forced/teleport movement bypasses opportunity attacks. Off-turn self-powered action/reaction movement has no independent payment contract. |
| [#107](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/107) | Pause movement before resolving opportunity attacks | Open (P0, confirmed gap): Movement assigns the final destination before adding reaction windows. Probe records x=3 while its unresolved reaction boundary is x=2; no resumable movement continuation exists. |
| [#106](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/106) | Execute all selected 2014 Fighting Styles | Open (P1, partial): Dueling and Two-Weapon Fighting integration exist; remaining Archery, Defense, Great Weapon Fighting and Protection effects are not fully implemented. The old claim that all but Dueling do nothing is stale. |
| [#104](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/104) | Implement 2014 Legendary Resistance settlement | Open (P1, missing): Legendary Resistance text survives import, but no reviewed use resource and failed-save choice window integrated across save paths was found. |
| [#101](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/101) | Enforce 2014 rest activity and interruption rules | Closed completed: main and scoped acceptance verified; evidence recorded on the issue. |
| [#100](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/100) | Allow per-attack Strength or Dexterity choice for finesse weapons | Closed completed: main and scoped acceptance verified; evidence recorded on the issue. |
| [#97](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/97) | Implement 2014 grapple and shove attack replacements | Open (P1, missing): Grappled/Restrained conditions and generic escape exist; source-owned 2014 grapple/shove attack replacements, contests, release and dragging transitions do not. |
| [#88](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/88) | Support the 2014 one-spell replacement option for known casters on level up | Closed completed: main and scoped acceptance verified; evidence recorded on the issue. |
| [#87](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/87) | Strictly validate nested boolean inputs before game-rule settlement | Closed completed: main and scoped acceptance verified; evidence recorded on the issue. |
| [#51](https://github.com/SagaSmithAI/Sagasmith-dnd/issues/51) | Track unfixed ChromaDB authorization and code-injection advisories | Open (P1, upstream blocked): All three GitHub advisories still return first_patched_version=null and affected ranges through 1.5.9. Default text-only local installs do not establish that optional Chroma server deployments are fixed. |

## Evidence and limits

The follow-up [organization-wide audit](https://github.com/SagaSmithAI/.github/blob/main/docs/audits/2026-09-22-all-issues.md)
checked all 41 issues still open across the organization, closed five completed
Narrative/CoC/organization items, and verified the exact remaining set of 36.
All 34 D&D rows above now identify a concrete remaining gap, evidence limitation
or upstream blocker. The linked report gives pinned source references, next
acceptance steps, individual issue comments and three reproduced counterexamples.
D&D code head a8ae77e passed all four jobs in CI run 35697690302; this does not
resolve the remaining gameplay gaps or replace private-Pack/live acceptance.

- Core actor recall now filters eligible event IDs in SQL before materializing
  payloads. Source/branch visibility remains checked; Core main contains 910bd56.
- Local transfers preserve authoritative slices for both actors. Reused
  post-commit bindings reduce transfer queries from 128 to 108 and binding queries
  from 36 to 19. Five-iteration Host/stdio measurements do not establish an
  end-to-end latency win or LLM improvement.
- The subsequent actor-context change reads all actor revisions as scalar columns,
  then loads only the selected sheets. The 27-actor Host/stdio sample uses 109
  transfer queries and 19 binding queries: one extra query buys bounded sheet
  materialization. Unselected actor mutations still invalidate the state token.
- A real complete local content library initially failed import because Tortle
  1.0.2 had unassigned native clauses and inconsistent inner manifest metadata.
  The exact-hash local repair preserves source blobs and card data; repaired
  archives are not published by this work.
- Real archive acceptance now passes Claws, both armored/unarmored Shell Defense
  cases and Hold Breath. The latter caught and fixed current embedded features
  inheriting the species mechanic refs: their exact current shape now receives
  the 600-round timer. Partial/forged reference shapes remain rejected.
- Chroma advisories GHSA-2wm9-hf6c-p5cr, GHSA-xph7-9rjv-w5fr and
  GHSA-36p7-vc44-83pf still reported no first patched version when checked on this
  date. The default text-only path does not load Chroma, but optional exposure
  remains an upstream blocker; #51 must remain open.
- Remote CI and real local archive acceptance are distinct. Private archive tests
  skipped by public CI must not be reported as passed.

## Related repositories

Core #13 was closed after the SQL recall change and scoped tests passed. Content
library #18 was closed after current SCAG archive checksum validation and source
repair documentation were reconciled. Content #10 remains valid: the public
freshness marker is older than the current source catalog. Content #12 was closed
after four real archive scenarios passed for Artificer equipment, build-to-Defender,
spell choices/guards and SCAG Watcher Eye (plus a separate public protocol RNG
scenario). Agent #13 is a broad upstream
cherry-pick roadmap; its unrelated channels/UI items do not become local DND
requirements automatically.
