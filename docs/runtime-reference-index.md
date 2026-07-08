# Runtime Reference Index

SagaSmith D&D runtime is document-driven. Runtime state is written through
Actor, Item, Activity, Effect, Scene, Token, Region, Combat, and Combatant
documents, with AI-DM narration layered on top of structured CLI results.

This index records how the local `reference/` workspace is used when
implementing the ten-step D&D 2014 runtime plan.

Reference projects are inputs for design and data interpretation only. The
runtime does not expose Foundry, fvtt-cn, or 5e-bits schemas as supported
external contracts; their useful concepts are synthesized into SagaSmith's own
ruleset JSON, document services, CLI envelope, and tests.

## Primary Runtime References

- `reference/dnd5e`: Foundry D&D system behavior, especially Actor/Item
  embedded documents, activities, activation, consumption, effects, templates,
  and system data shape.
- `reference/foundryvtt`: Foundry core concepts such as document lifecycle,
  scenes, tokens, regions, combatants, messages, and package conventions.
- `reference/fvtt-cn`: Chinese localization vocabulary for Foundry and dnd5e
  labels. Runtime IDs remain stable English identifiers; Chinese text is used
  for narration/UI labels only.
- `reference/5e-bits/5e-database`: SRD 2014/2024 structured rules data and
  schemas. Use it for public rules content boundaries, spell/equipment/class
  data shape, and import fixtures.
- `reference/5e-bits/5e-srd-api`: API layer over 5e-bits data. Use it to check
  public endpoint/resource naming and queryable collection boundaries.
- `reference/5e-bits/docs`: 5e-bits documentation reference for public API/data
  usage expectations.
- `reference/5e-bits/awesome-5e-srd`: ecosystem reference for SRD-compatible
  public resources.
- `reference/5e-bits/dnd-img`: public image asset reference for later UI/media
  experiments.
- `reference/5e-bits/dnd-uptime`: operational reference only.
- `reference/5e-bits/infrastructure`: deployment/infrastructure reference only.
- `reference/claude-dnd-skill`: AI-DM text-play workflow reference. Use it for
  session flow, narrative expectations, and skill-facing contract examples, not
  as runtime authority.

## Supporting References

- `reference/black-flag`: Alternative 5e-family rules organization reference.
  Use only to test extension-ruleset seams after 2014 core is stable.
- `reference/crucible`: Foundry-adjacent package structure reference for module
  style and data packaging patterns.
- `reference/foundryvtt-cli`: Foundry tooling/package workflow reference.
- `reference/foundryvtt-premium-content`: Content packaging and compendium
  layout reference; do not copy protected content.
- `reference/hexploration`: Hex/travel exploration module reference for later
  overland movement and region mechanics.
- `reference/dungeon-tilesets`, `reference/pixels`, `reference/pdfjs`: Asset,
  rendering, and document-display references for future map/front-end work.
- `reference/world-anvil`, `reference/worldbuilding`, `reference/Ferncombe`,
  `reference/restored-keep-levels`, `reference/unfulfilled-rolls`: Campaign,
  adventure, and world-state organization references for AI-DM content and
  scenario state, not core rules authority.
- `reference/pf2e`: Cross-system Foundry runtime reference. Use for generic
  document/effect/combat architecture comparisons, not D&D rules.
- `reference/.github`: Shared GitHub metadata reference only.

## Step Alignment And Current Status

1. Ruleset schema: complete. Built-in 2014/2024 rulesets validate through the
   SagaSmith schema and expose activity, economy, condition, duration, map,
   rest, death-save, and spellcasting semantic tables.
2. Actor/Item/Activity documents: Foundry dnd5e document model.
   Complete for Actor, Actor-owned Item, Activity, and ActiveEffect authority.
3. Action economy: complete for rules-driven action, bonus action, reaction,
   Action Surge, Second Wind, Extra Attack, Ready, and reaction windows.
4. Rolls, attacks, damage, saves: complete for Actor-based checks, attacks,
   saves, prepared actor math, resistance, immunity, vulnerability, cover, and
   concentration checks.
5. Spells: complete for cast activities, spell slot use, cantrip and slot
   scaling, ritual gating, concentration effects, and Actor spell attack/DC
   defaults.
6. Effects/duration: complete for ActiveEffect recalculation and declared
   AI-DM periods including turn, round, encounter, rest, scene, minute, hour,
   and day advancement.
7. Scene/token/region map runtime: complete for Scene, Token, Region, measured
   templates, token movement, opportunity windows, cover, active scene switching,
   and prepared token runtime summaries.
8. Rest/recovery/death saves: complete for Actor-document short rest hit dice,
   long rest recovery, spell slots, resources, activity uses, and death-save
   synchronization.
9. AI-DM skills: complete in `SagaSmith-dnd-skills/full`; standalone skills keep
   their bundled `portable.py` runtime contract.
10. Test matrix: complete for the current runtime slice; future work is content
    breadth, not legacy compatibility.

## Verification Matrix

- Core D&D CLI/runtime: run
  `..\sagasmith-core\.venv\Scripts\python.exe -m pytest` from `sagasmith-dnd`.
- Skill contract docs: run `git diff --check` from `SagaSmith-dnd-skills` and
  confirm `rg -n -- "--participants" full` returns no matches.
- Compatibility removal audit: full skills may mention `combat act`,
  `sheet.inventory`, or `inventory_managed` only as explicit prohibitions.
- Git publication: commit and push each completed runtime step in its owning
  repository.

## Remaining Rule Content Work

- Expand 2014 class, subclass, species/background, feat, spell, monster, and
  equipment data coverage inside SagaSmith rules JSON and fixtures.
- Add 2024 ruleset content breadth after the 2014 baseline is stable, using the
  same SagaSmith schema.
- Add front-end map UI on top of Scene/Token/Region/Combat documents without
  changing the document authority model.
