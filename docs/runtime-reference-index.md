# Runtime Reference Index

SagaSmith D&D runtime is document-driven. Runtime state is written through
Actor, Item, Activity, Effect, Scene, Token, Region, Combat, and Combatant
documents, with AI-DM narration layered on top of structured CLI results.

This index records how the local `reference/` workspace is used when
implementing the ten-step D&D 2014 runtime plan.

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

## Step Alignment

1. Ruleset schema: Foundry dnd5e data shape plus 5e-bits schema boundaries.
2. Actor/Item/Activity documents: Foundry dnd5e document model.
3. Action economy: Foundry dnd5e activities/activation plus 2014 SRD action
   rules from 5e-bits where available.
4. Rolls, attacks, damage, saves: Foundry dnd5e roll/activity flow and 5e-bits
   SRD formulas/data.
5. Spells: 5e-bits SRD spell data, Foundry dnd5e spell item/activity shape.
6. Effects/duration: Foundry ActiveEffect and duration semantics adapted to
   declared AI-DM periods.
7. Scene/token/region map runtime: Foundry core scene/token/region concepts,
   with later UI informed by map/tileset references.
8. Rest/recovery/death saves: 2014 rules from 5e-bits and Foundry dnd5e
   resource recovery patterns.
9. AI-DM skills: claude-dnd-skill flow plus SagaSmith CLI contracts.
10. Test matrix: fixtures drawn from Foundry-style documents and 5e-bits SRD
    public data.
