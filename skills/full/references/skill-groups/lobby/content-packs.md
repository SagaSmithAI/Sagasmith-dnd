# Unified content packages

## One archive, four semantic kinds

Every shareable unit uses `sagasmith.content-package` v2 in a
`.sagasmith-pack` archive. The physical fields are always `manifest`,
`dependencies`, `sources`, `assets`, `content_reviews`, `actors`, `content`,
and metadata. The `kind` remains semantically strict:

- `core_rules`: built-in rule definitions and their evidence;
- `addon`: optional rule definitions and actor presets, installed globally and
  enabled per branch by an Owner/DM;
- `module`: Scene Atlas, catalogs, narrative context, maps, cast, monsters, and
  pregenerated PCs, attached to one campaign;
- `preset`: reusable PC/NPC/monster cards with no campaign state.

Do not accept `sagasmith.portable`, loose JSON cards, `*_pack` envelopes,
release manifests, or `.sagasmith-module` archives. Do not inline source text or
image bytes in a descriptor. Every byte belongs to one content-addressed asset.

## Actor cards and images

PC, NPC, and monster share `sagasmith.actor-card.v3`; `actor_type` selects the
role. Owner-dependent statblock templates retain the same optional presentation
reference on their source card until runtime instantiation. Either card may
reference one package `actor_image` asset by `asset_key`.
Runtime character rows and snapshots never store that image. Import always
creates a fresh runtime identity and never transfers ActorKnowledge, branches,
random streams, or campaign state.

Actor-card exchange happens only through finalized `preset` or `module` Packs.
Use `content_pack(action="list"|"get", kind="preset")` to inspect reusable
cards, and import an archive with `content_pack(action="import",
kind="preset")`. Create a runtime actor from the returned exact artifact/card
identity; never reconstruct a creature by name or use a character-query export
side door.

Image extraction is conservative. A source page must contain the actor heading
(exact or a bounded letter-spacing OCR equivalent) and a low-text illustration
region. Evaluate every page in the actor's evidence, keep only the strongest
crop above the floor, and record page/crop/method/confidence. Text/statblock
crops and unrelated art are rejected. A proven absence of source illustration
is an explicit non-blocking result; a missing heading, invalid page, undersized
candidate, or uncertain crop requires review and blocks release. Never fill an
absence with invented imagery.
Never reuse a crop by actor name alone. Reuse requires the same normalized name
and the same ordered source/chunk/page evidence; different module, edition,
variant, or duplicate-name evidence must be extracted independently.

Only a complete, rules-legal character document may become a pregenerated PC
actor card. Preserve an incomplete or partially filled sheet as
`player_reference`, record the review gap, and send it through normal completion
and character-creation workflow instead of inventing missing class, level,
ability, HP, or equipment fields.

## Sources and evidence

Each source has exactly one UTF-8 normalized-document blob. Sections and chunks
store offsets and hashes into that blob, not duplicate prose. `source_ref`
contains an exact `source_key`, `chunk_key`, optional page, and note. Original
PDFs may be retained as `original_document` assets. On import, Core verifies all
hashes and recreates local source/chunk ids while preserving stable citations.

A distributed file that was not normalized or cited is an auxiliary asset, not
evidence. Keep maps and player/character reference files as typed `map` or
`player_reference` assets with their corpus-relative logical path. Never attach
them to `source.original_asset_keys` or claim their prose was indexed. A public
catalog may expose authorized browser assets by checksum; install only from the
complete verified archive.

Treat package construction and public release as different gates. A local
private package may use `distribution="private"` and `license="user-supplied"`;
never rewrite those values merely because the software repository is Apache-2.0.
A public package requires a supported open license, exact HTTPS
`license_evidence`, matching licenses on every asset, and source-specific
attribution. A self-asserted authorization boolean is not evidence. Commercial
documents and crops extracted from them stay private unless a separate grant is
actually supplied; an SRD card must drop any commercial illustration before
public release.

## Import and activation

Every `content_pack` request declares one of the four route kinds
`core_rules|addon|module|preset`; the server never infers it from other fields
or from archive contents. Its complete action set is
`list|get|import|export|activate|deactivate|remove`.

Use `content_pack(action="import", kind="addon")` for an addon,
`kind="core_rules"` for a core-rules archive, and `kind="preset"` for a preset.
Provide exactly one managed `artifact` or allowlisted `source_path`; inline
descriptors are not accepted because they cannot carry verified blobs.
For a public-catalog download, verify both the descriptor checksum and the
index's whole-archive SHA-256/byte size before opening or importing it.

Use `content_pack(action="import", kind="module")` for a module. Import validates the
archive, edition, dependencies, sources, images, every actor, sourced play
profile, and `metadata.agent_finalization`, then creates fresh cast identities
and bindings. Activation remains an explicit Owner/DM operation.

Import never implies branch activation. Enable an addon with revision-
safe Owner/DM `content_pack(action="activate", kind="addon")`. Module activation
uses `kind="module"` and remains a separate campaign operation. Snapshot locks keep exact package versions and
checksums; one package kind cannot borrow another kind's authority.

Before distribution, validate a cold archive round trip, source/chunk evidence,
dependency locks, actor image ownership, fresh actor identity, and branch-safe
activation. Keep non-redistributable sources private.
