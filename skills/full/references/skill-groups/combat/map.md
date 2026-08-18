# Combat positioning

`combat_start` fixes `positioning_mode="grid"|"agent"` for the whole
encounter. Never switch modes mid-combat.

In grid mode, provide a coordinate for every participant and choose exactly one
map authority. Prefer `battle_map_template_id` when the finalized active Module
Pack contains a reviewed combat-grid template. Otherwise provide a bounded
explicit `battle_map`; its DM override must produce an authority receipt. Never
send both sources. When a selected template declares deployment zones, give
each runtime campaign actor an explicit `deployment_zone_id` and a position in
that zone; portable templates never contain participant actor ids.

Patch the encounter map only through `combat_map_patch` from reviewed scene/map evidence or an
explicit bounded DM spatial ruling. The engine owns movement distance, range,
line/area geometry, obstruction, cover, adjacency, threats, and friendly fire.
Missing coordinates are invalid grid input.

In agent mode, provide neither a battle map nor coordinates. The Agent infers
whether movement and targets are legal, what is in range or blocked, who
threatens whom, and whether an area includes allies. Supply those decisions as
the exact action-specific `spatial_facts`; the engine still owns rolls, action
economy, damage, resources, effects, and commits.

Keep public and DM-only layers separate. Walls, blocking, difficult terrain,
occupancy, elevation, cover, hazards, and actor placement must retain source or
ruling provenance. A decorative image is not mechanical geometry.

`combat_start` copies a Pack template into a fresh encounter-local map. Runtime
patches, movement, snapshot/branch operations, undo, and redo affect encounter
state only and never write back to the finalized Pack. Correct a released
template by creating and finalizing a new Pack draft/version.

In grid mode, map revision participates in movement and reaction validation.
Re-query the map after any patch, join, restore, or movement conflict.

Rendering is a projection, not another map model. Request it through
`combat_query(view="render")`. A grid result may depict the current projected
geometry and package-owned actor portraits. An Agent-mode result must remain a
nonspatial initiative card with no invented coordinates. Send `party_public`
images to shared channels; do not send a private `caller` render there.
