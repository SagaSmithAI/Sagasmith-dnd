# Module Visual Atlas Review

Use this workflow when imported module text identifies locations but a map,
diagram, or handout carries topology that the text parser cannot safely infer.
Run it in `lobby` while preparing a module, or in `play` before relying on a
missing edge. Load owner/DM `lobby.modules` or `play.scene_control` first; the
player-safe `play.scene` group intentionally cannot render keeper pages or write
reviewed topology.

## Review sequence

1. Call `module_query(view="index")` and inspect `spatial.locations` and
   `spatial.connections`. An empty connection list means unknown topology.
2. Call `module_query(view="assets", payload={"module_id": ...})`. Select the
   imported `application/pdf` asset; do not read an arbitrary local path.
3. Locate a candidate page from scene page ranges or expanded source text, then
   call `module_draft(action="evidence")` with `campaign_id` and a payload
   containing `module_id`, `page_number`, and optional `source_asset_id`.
   Inspect the returned image itself. Text extraction, room
   numbering, heading order, and a generic cross-reference are not visual proof.
   Inspect the image content block returned by the native MCP tool; extracted
   text or an inline base64 payload is not visual review.
4. Re-read `module_query(view="current" | "progress")` and use the current
   scene/scope `state_version` (`0` only when no row exists).
5. Call `module_set_progress` with a fresh idempotency key and
   `spatial_review`. Omit `status` and `progress` when they should remain
   unchanged. Use `mode="merge"` to add/correct the submitted edges or
   `mode="replace"` only after reviewing the complete replacement set.
6. Re-read the scene or current progress. Use an edge only when it returns with
   `confidence="reviewed_image"` and evidence containing the asset checksum,
   source page, reviewer, and active branch id. Create a snapshot after a
   material atlas review.

## Optional Pack combat-grid templates

While the module is still a draft, a reviewed scene may also carry reusable
`metadata.profile_data.combat_grid_templates`. Treat this as Pack authoring,
not as campaign progress:

1. Read the draft scene and call `module_draft(action="evidence")` for the
   supporting chunks or managed image page. Inspect the actual evidence. A map
   image is an asset and proof source; it is never authoritative topology by
   itself.
2. Make the semantic judgment yourself, then call the single authoring facade
   `module_draft(action="edit", operation="combat_grid")` with `change="upsert"`,
   the draft `scene_id`, the complete candidate template, the current job
   `expected_revision`, and a fresh idempotency key. Copy every draft
   `source_ref` verbatim. To delete a candidate, use `change="remove"` with its
   stable `template_id` and evidence-bound `source_refs`.
3. Re-read the draft scene and validate the canonical result before finalizing.
   Only square five-foot cells, bounds, blocked cells, difficult cells,
   deployment zones, an optional managed image `map_asset_key`, and source refs
   are mechanical. Do not encode walls, line of sight, cover, elevation, token
   identities, or module-specific rulings.
4. Finalize only after the candidate matches the evidence. A finalized Pack is
   immutable; any later correction requires a new draft and greater Pack
   version.

At runtime, pass exactly one authority to `combat_start`: either
`battle_map_template_id` or an explicit `battle_map`, never both. For a template
with deployment zones, assign each campaign actor a `deployment_zone_id` and a
position at start. Actor ids are encounter-local and never enter the portable
Pack. The server copies the template into a new encounter map, records an
authority receipt, and keeps later `combat_map_patch` changes out of the Pack.

```json
{
  "campaign_id": "campaign-id",
  "scene_id": "current-or-spatial-scene-id",
  "scope_id": "party",
  "expected_state_version": 3,
  "idempotency_key": "review-dungeon-map-page-22-v1",
  "spatial_review": {
    "schema_version": 1,
    "mode": "merge",
    "source_asset_id": "imported-pdf-asset-id",
    "page_number": 22,
    "note": "Reviewed the printed dungeon plan.",
    "connections": [
      {
        "from": "d5-welcome-to-the-dungeon",
        "to": "d6-bloated-corpse",
        "bidirectional": true,
        "kind": "passage",
        "observation": "The map visibly draws an open corridor between D5 and D6."
      }
    ]
  }
}
```

Allowed connection kinds are `passage`, `door`, `secret_door`, `stairs`,
`portal`, and `other`. Each endpoint must name exactly one location in the same
module. The observation must describe only what is visible on that page; do not
smuggle inferred secrets, encounter outcomes, or geometry into it.

## Persistence and visibility

Reviewed traversal topology is stored in scoped scene progress, so snapshot restore and
branch checkout restore the corresponding atlas review. It is not written back
into immutable imported module metadata and does not leak into sibling branches.
Pack combat-grid templates instead live in the immutable finalized Module Pack;
each combat receives a fresh encounter-local copy. Every Agent/session must
open its own exposure, but all authorized Agents read
the same branch state through MCP.

The rendered page is DM/owner evidence. Do not show it or keeper-only topology to
players unless the module and campaign state establish that the party possesses
that map or handout. A reviewed scene edge helps scene traversal and combat-map
provenance; it does not establish walls, cover, line of sight, difficult terrain,
blocked cells, or exact token positions. Supply those facts separately when the
source or DM establishes them.
