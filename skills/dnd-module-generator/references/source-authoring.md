# Module source authoring

Create the one UTF-8 Markdown source consumed by the selected system's module
profile. This is authoring discipline, not a second Package schema.

## Document hierarchy

Use a stable generated hierarchy:

~~~markdown
# Chapter or major section

Operating context.

## Playable scene

Situation, evidence, actors, choices, mechanics, and consequences.

### Scene subsection

Clue, check, handout, encounter, hazard, or Keeper/DM note.

#### A1. Numbered room, location, or source node

Source-backed spatial or node evidence.
~~~

Generated content should use level-two scene headings. A system profile may
recognize deeper source hierarchies during import, but do not imitate irregular
book extraction in new content. Avoid empty headings and repeated generic names.

The compiler derives stable scene keys from heading paths. Treat a semantic
heading rename as an identity change and review progress remaps when replacing
an active Pack.

## Runtime manifest v1

Place zero or one manifest near the beginning:

~~~markdown
<!-- sagasmith-runtime-manifest
{
  "schema_version": 1,
  "module_key": "lantern-case",
  "entities": [
    {"id": "npc:caretaker", "kind": "npc", "name": "The Caretaker"}
  ],
  "secrets": [
    {
      "id": "secret:sealed-room",
      "truth": "The cellar wall conceals a sealed room.",
      "initial_knowers": ["npc:caretaker"]
    }
  ],
  "clues": [
    {
      "id": "clue:misaligned-bricks",
      "truth_ref": "secret:sealed-room",
      "source_scene": "chapter-one-arrival",
      "trigger": "inspect the cellar wall",
      "consequences": ["the investigator can locate the concealed opening"]
    }
  ],
  "plot_nodes": [
    {
      "id": "plot:open-room",
      "trigger": "open the sealed room",
      "consequences": ["change access to the hidden chamber"]
    }
  ],
  "foreshadowing": [
    {
      "id": "foreshadow:cold-draft",
      "setup": "a cold draft crosses the cellar",
      "payoff": "the air comes from the hidden chamber"
    }
  ],
  "branches": [
    {
      "id": "branch:trust-caretaker",
      "trigger": "share the evidence with the caretaker",
      "consequences": ["unlock the caretaker's testimony"]
    }
  ]
}
-->
~~~

Recognized collections are entities, secrets, clues, plot_nodes,
foreshadowing, and branches. Keep ids lowercase and globally unique. Current
validation requires:

- schema_version equal to 1 and a stable lowercase module_key;
- every present collection to be an array;
- every entry to be an object with a stable lowercase id;
- secrets.initial_knowers to be an array when present;
- clues, plot_nodes, and branches to have a trigger;
- plot_nodes.consequences and branches.consequences to be arrays.

Reuse ids only while their meanings remain the same. The runtime manifest
records possibilities and semantic identity; it does not grant knowledge,
advance progress, realize branches, or write campaign continuity.

## Common scene contract

For every playable scene, state:

- purpose and situation on arrival;
- participating NPCs and immediate goals;
- immediately perceivable evidence separately from hidden truth;
- likely actions and system-valid checks with consequences;
- redundant discovery paths for indispensable revelations;
- transitions and delay/escalation effects;
- encounter or chase composition when mechanically executable;
- persistent effects and endings as possibilities, not realized state.

Include exact source-backed mechanics or bind an already validated actor. Never
invent a partial statblock and expect MCP to infer the missing rules.

## D&D authoring

Use canonical 2014/2024 terminology for the selected edition. Review levels,
advancement, DCs, encounter actors, rewards, spells, and exact statblocks.
Describe tactics, retreat, and surrender when combat is expected. Keep
unresolved geometry as Agent-facing evidence and let runtime choose grid or
Agent spatial mode.

## CoC authoring

Use source-preserving CoC 7e terminology:

- Mark Core Clue and Clue subsections clearly, but do not make heading labels
  alone grant knowledge.
- State what is found automatically, what additional information a successful
  roll reveals, and what a failed or pushed roll can cost.
- State difficulty only when the source establishes regular, hard, or extreme.
- For a combined roll, state the traits and whether any or all must succeed.
- For group Luck, identify eligible present investigators but leave the
  lowest-Luck actor and tied selection to authoritative runtime state.
- Record SAN loss as exact success/failure expressions and preserve trigger
  context. Do not convert it into already-applied loss.
- Preserve exact NPC/creature values, chase evidence, tomes, spells, handouts,
  and Classic/Pulp distinctions. Mark absent mechanics unknown rather than
  completing them from genre expectations.
- For solo works, preserve numeric node headings and authored transitions.

The CoC parser may recognize investigation, combat, chase, social, travel,
handout, reference, and solo-node scenes plus clue, SAN, NPC, creature, and
timeline subsections. Treat parser output as a mechanical first pass that the
Agent must review against source evidence.

## Spatial evidence

State dimensions, routes, doors, elevation, obstruction, cover, hazards, or
chase connections only where authored. Do not infer a tactical map from
narrative adjacency or consecutive headings.

## Secrets, knowledge, and visibility

Distinguish authored truth, initial authored knower, runtime discovered
knowledge, and audience-specific narration. Do not pre-reveal secrets through
summaries or handouts. A source heading or label does not replace runtime
authorization or audience settlement.

Use only canonical scene visibility: `restricted` for Keeper/DM-only source,
`group` for an authorized player group, and `public` for openly presentable
material. System terms are authoring semantics, not extra Core visibility enum
values. Preserve system scene details under `profile_data` in the compiled
Scene Atlas.

## Integration pass

1. Verify every runtime id has one meaning.
2. Resolve repeated chapter/scene headings.
3. Verify each scene's incoming and outgoing state.
4. Verify every indispensable revelation has viable routes.
5. Reconcile clocks, factions, chases, branches, and endings across chapters.
6. Verify dossiers, catalogs, handouts, and endings use current ids.
7. Verify system mechanics against the selected profile and source.
8. Produce one canonical source and one runtime manifest.
