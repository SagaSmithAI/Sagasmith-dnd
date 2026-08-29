# D&D 5e Package profile

The live D&D MCP schema and deterministic D&D validator are authoritative.
These notes constrain Agent decisions; they do not replace native schemas.

## Identity

- `system_id`: `dnd5e`
- portable Module id prefix: `dnd5e.module.`
- supported Package content classifications: `adventure`, `campaign`,
  `emergent_seed`, and `emergent_episode`
- runtime-manifest v2 classifications: `authored_module`, `emergent_seed`, and
  `emergent_episode`
- editions: `2014`, `2024`, or both, exactly as supported by the source
- current Module capability: `module_pack_v2` when required by the source
- authored campaigns require at least one reachable ending
- emergent seeds and episodes may remain open, but must declare lineage and
  playable scene links through the current runtime-manifest schema
- playthrough campaign modes are `authored_module`,
  `authored_with_extensions`, and `emergent`

## Required play-profile review

Use real source receipts for:

- `starting_level.value`
- `expected_end_level.value`
- `advancement.modes` and `advancement.recommended`
- `pregenerated_characters.available` and applicability

Party-size minimum and maximum are optional when the source gives no guidance;
null bounds with no invented receipt are valid.

## Catalogs and mechanics

Use the native D&D Package arrays for items, encounters, hazards, handouts, and
mechanics. Review exact edition mechanics, DCs, rewards, encounter actors,
spells, resources, and complete actor-card statblocks. Keep module meaning in
the Pack and reusable deterministic mechanics in the D&D package.

Do not finalize when identity, source binding, required evidence, actor
mechanics, dependencies, edition compatibility, or a required ending remains
undefined or conflicting.
