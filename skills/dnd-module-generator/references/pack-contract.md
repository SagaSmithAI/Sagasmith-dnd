# Current Content Pack contract

Use this reference for the common authoring protocol. The authoritative artifact
is sagasmith.content-package schema version 2, normally kind module and, where
the native system supports it, kind core_rules. The campaign and active MCP
determine system_id; never choose it from source prose.

Read [system-profiles.md](system-profiles.md) for system-specific manifest,
catalog, evidence, and finalization requirements.

## Ownership

| Final Package area | Owner | Agent behavior |
|---|---|---|
| format, schema_version, kind, system_id, checksum | Core | Never hand-build |
| id, version | Agent decision, server validated | Keep portable and stable |
| manifest | Agent decision plus server identity fields | Submit through draft |
| sources, chunks, normalized document | Core plus system | Use managed evidence |
| assets and blob paths | MCP/Core | Attach managed assets only |
| content_reviews | Draft review operations | Never fabricate |
| actors | System-validated actor bindings | Never write raw actor-card v3 |
| content.scene_atlas | System parser/compiler | Stabilize source headings |
| content.catalogs, content.narrative | Agent decisions | Submit as Package edits |
| metadata.agent_finalization | MCP from Agent confirmation | Confirm after review |

Final Module scene entries use the exact current Scene Atlas shape. Generic
visibility is `restricted`, `group`, or `public`; system-only fields such as CoC
clues, checks, SAN expressions, transitions, and solo node ids live below
`metadata.profile_data`, never as a fixed top-level Core scene superset. Read
those values only through the selected system profile.

Each finalized `content_reviews` entry is normalized by the MCP and must target
an existing Scene Atlas key. It uses exactly one evidence mode: either managed
visual asset plus 1-based page, or one or more source refs. The accepted review
also carries the authenticated reviewer and a non-empty observation. Never
fabricate, partially hand-build, or carry a draft review shape into the Pack.

Campaign ids, runtime character ids, permissions, actor knowledge, scene
progress, world state, random streams, branches, snapshots, and undo history are
never portable.

## Public authoring facade

Use only:

~~~text
module_draft(start|get|evidence|edit|finalize)
rulebook_draft(start|get|evidence|finalize)
content_pack(list|get|import|export|activate|deactivate|remove)
~~~

Choose exactly one draft authority. module_draft owns scene/module authoring;
rulebook_draft owns reviewed rule-source ingestion. Never convert a rulebook
into a fake module merely to reuse scene fields.

Authoring writes require Lobby, a host-bound authorized principal, a stable
idempotency key, and the latest applicable revision. The semantic facade is
shared, but the current native request envelope is system-specific:

- dnd5e module_draft puts operation fields in payload; content_pack also uses
  payload and places campaign_id plus kind inside it.
- coc7e module_draft and content_pack put operation fields in data and take
  campaign_id as a top-level argument.

Use the native schema exactly. Do not silently send both wrappers.

### Start

Generated D&D source:

~~~json
{
  "campaign_id": "<authoring-campaign>",
  "action": "start",
  "payload": {
    "name": "<module>.md",
    "content": "<complete UTF-8 Markdown>",
    "title": "<title>",
    "source_key": "<stable-source-key>"
  },
  "idempotency_key": "<stable exact-request key>"
}
~~~

Generated CoC source:

~~~json
{
  "campaign_id": "<authoring-campaign>",
  "action": "start",
  "data": {
    "name": "<module>.md",
    "content": "<complete UTF-8 Markdown>",
    "title": "<title>",
    "source_key": "<stable-source-key>"
  },
  "idempotency_key": "<stable exact-request key>"
}
~~~

For a user-managed source, replace name plus content with source_path. Never
send both. Retain job_id, inactive module_id, state, inspection, validation, and
revision. Resume an interrupted mechanical first pass only with the current
edit:advance operation.

### Get and evidence

Call get without a handle to list resumable drafts or with the current wrapper's
job_id for one draft. Call evidence with kind chunks, an optional query or scene
id, and a bounded limit.

A draft receipt has this exact semantic shape:

~~~json
{
  "source_key": "<returned source key>",
  "page": null,
  "chunk_hash": "<returned SHA-256>",
  "note": "<review note>"
}
~~~

The compiler translates it to the final source_ref and managed chunk_key. Copy
the receipt; never predict the final key.

### Draft edit

Use the narrowest supported operation:

| operation | Purpose |
|---|---|
| source_text | reviewed source transcription repair |
| content | reviewed structured content |
| statblock | reviewed system statblock |
| asset | managed source asset |
| actor | validated actor binding |
| package | manifest, catalogs, narrative, dependencies, metadata, version |
| advance | resume the mechanical first pass |

Package decisions are limited to manifest, catalogs, narrative, dependencies,
metadata, and version. Set operation to package in payload for dnd5e or data for
coc7e. Include at least one decision and a concise note. Supply
expected_revision and refresh it after every successful write.

### Common manifest

The Agent supplies exactly these semantic areas:

~~~json
{
  "title": "<title>",
  "classification": "<system profile value>",
  "compatibility": {},
  "play_profile": {},
  "continuity": {
    "series_id": null,
    "order": null,
    "continues_from": null,
    "state_policy": {}
  },
  "activation": {
    "mode": "campaign_attach",
    "default_active": false
  }
}
~~~

Do not add catalogs or narrative inside manifest. The selected system profile
defines classification, compatibility, play_profile, and catalog fields.

### Narrative

Supply both arrays:

~~~json
{
  "dossiers": [],
  "endings": []
}
~~~

Dossier and ending semantics are Pack-specific Agent decisions. Keep their ids
aligned with source prose and runtime-manifest ids. Supply required reachable
endings according to the selected system profile.

### Dependencies

Each dependency must contain exactly:

~~~json
{
  "kind": "core_rules",
  "id": "<pack-id>",
  "version": "<exact-version>",
  "checksum": "<exact lowercase SHA-256>",
  "optional": false
}
~~~

Discover dependencies from actual Packs. Never invent a checksum. Use an empty
array when there is no portable dependency.

### Metadata

Use metadata for language, license, attribution, authorship, publication, and
Pack-specific review/ruling records. Never store campaign state or duplicate
server-derived fields. Keep private or commercial source artifacts local unless
the user explicitly authorizes lawful distribution.

### Finalize

For dnd5e:

~~~json
{
  "campaign_id": "<authoring-campaign>",
  "action": "finalize",
  "payload": {
    "job_id": "<job-id>",
    "pack_id": "<system-id>.module.<portable-id>",
    "confirmation": {
      "confirmed": true,
      "note": "Reviewed source, system profile, scene structure, catalogs, narrative, dependencies, and diagnostics."
    }
  },
  "idempotency_key": "<finalize key>"
}
~~~

For coc7e, the same decision is carried in data and the current identity key is
package_id:

~~~json
{
  "campaign_id": "<authoring-campaign>",
  "action": "finalize",
  "data": {
    "job_id": "<job-id>",
    "package_id": "coc7e.module.<portable-id>",
    "confirmation": {
      "confirmed": true,
      "note": "Reviewed source, CoC profile, scene structure, catalogs, narrative, dependencies, and diagnostics."
    }
  },
  "expected_revision": "<current draft revision>",
  "idempotency_key": "<finalize key>"
}
~~~

The draft must be mechanically imported. The request confirmation contains
exactly `confirmed` and `note`; the MCP binds the authenticated reviewer and the
final Pack stores exactly `confirmed`, `reviewer`, and `note` in
`metadata.agent_finalization`. Finalization writes an immutable archive and
moves the draft to compiled. Extra confirmation fields, incomplete Scene Atlas
evidence, and stale or malformed content reviews are hard failures.

## Final artifact and optional runtime handoff

Inspect the artifact with content_pack get, kind module. For dnd5e put
campaign_id and kind in payload. For coc7e pass campaign_id top-level and kind
in data. Build completion ends there by default.

Import explicitly with exactly one artifact or managed source path. Import
remains inactive. Activate only after refreshing the target campaign revision.
When replacing an active revision, use progress remaps only as exact objects
containing from_scene_id, to_scene_key, and reason after reviewing impact.

## Core rules Pack branch

Follow the live native schema; the current CoC facade uses campaign_id and
action at top level with operation fields in data.

1. rulebook_draft start one managed PDF/Markdown/text source with source_path,
   source_key, and title.
2. Use evidence search to review representative rules and normalization before
   finalization.
3. Finalize the current draft revision with package_id, version, title, and an
   explicit confirmation object. A portable CoC id uses the coc7e.rules.
   prefix.
4. Inspect/import through content_pack with kind core_rules. Import remains
   inactive; activation is a separate guarded campaign write.
5. Verify the active lock and retrieved source through rule_query
   sources/search/expand/effective.

The rules compiler binds reviewed Core sources and checksums; it does not infer
book-specific meaning. Store interpretation decisions in reviewed Pack
artifacts/mechanics when the live schema supports them, not in Core parsing
heuristics. Do not include campaign state, module scenes, runtime actors, or
private source text in distributable metadata.
