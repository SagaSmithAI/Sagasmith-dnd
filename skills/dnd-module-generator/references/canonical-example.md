# Canonical CoC 7e build example

This synthetic example demonstrates the current public sequence. Copy real
evidence receipts and revisions from tool responses; never copy placeholders.

## 1. Bind the system

Confirm the authoring campaign reports system_id coc7e, is in Lobby, and exposes
native module_draft and content_pack tools. Do not infer CoC from the title.

## 2. Author one source

~~~markdown
<!-- sagasmith-runtime-manifest
{
  "schema_version": 1,
  "module_key": "lantern-case",
  "entities": [{"id": "npc:caretaker", "kind": "npc", "name": "The Caretaker"}],
  "secrets": [{"id": "secret:sealed-room", "initial_knowers": ["npc:caretaker"]}],
  "clues": [{"id": "clue:misaligned-bricks", "trigger": "inspect the cellar wall"}],
  "plot_nodes": [{"id": "plot:open-room", "trigger": "open the sealed room", "consequences": []}],
  "foreshadowing": [{"id": "foreshadow:cold-draft"}],
  "branches": [{"id": "branch:trust-caretaker", "trigger": "share the clue", "consequences": []}]
}
-->
# The Lantern Case

This one-session 1920s scenario supports two to four investigators under
Classic Call of Cthulhu 7e. It includes no pregenerated investigators and is not
designed for solo play.

## Arrival

The investigators meet the caretaker.

### Core Clue

Misaligned bricks and a cold draft reveal a sealed room without requiring a
roll. A successful Spot Hidden roll also reveals recent tool marks.

## The Sealed Room

Opening the room exposes the investigators to the source of the cold.

### Sanity Check

Seeing the impossible flame costs 0/1D4 SAN.

## Ending: Room Sealed

Sealing the room ends the immediate threat.
~~~

## 3. Start the draft

~~~text
module_draft(
  campaign_id=<authoring campaign>,
  action="start",
  data={
    name: "lantern-case.md",
    content: <complete source>,
    title: "The Lantern Case",
    source_key: "lantern-case"
  },
  idempotency_key="lantern-case:start:v1"
)
~~~

Retain job_id, inactive module_id, parser profile, and revision. Require the
expected imported state and review all inspection/validation output.

## 4. Obtain evidence

~~~text
module_draft(
  campaign_id=<authoring campaign>,
  action="evidence",
  data={
    job_id: <job>,
    kind: "chunks",
    query: "two to four investigators",
    limit: 10
  }
)
~~~

Copy the returned source_ref. Obtain additional receipts when profile facts or
catalog facts occur in different chunks.

## 5. Save exact CoC Package decisions

~~~text
module_draft(
  campaign_id=<authoring campaign>,
  action="edit",
  data={
    job_id: <job>,
    operation: "package",
    note: "Reviewed CoC profile, clues, SAN evidence, catalogs, and ending.",
    version: "1.0.0",
    manifest: {
      title: "The Lantern Case",
      classification: "scenario",
      compatibility: {
        editions: ["7e"],
        required_capabilities: ["module_pack_v2"]
      },
      play_profile: {
        investigator_count: {
          minimum: 2,
          maximum: 4,
          source_refs: [<profile receipt>]
        },
        ruleset: {
          supported: ["classic"],
          recommended: "classic",
          source_refs: [<profile receipt>]
        },
        era: {value: "1920s", source_refs: [<profile receipt>]},
        estimated_sessions: {
          minimum: 1,
          maximum: 1,
          source_refs: [<profile receipt>]
        },
        pregenerated_characters: {
          available: false,
          applicability: "Reviewed; none are included.",
          source_refs: [<profile receipt>]
        },
        solo_play: {
          supported: false,
          source_refs: [<profile receipt>]
        }
      },
      continuity: {
        series_id: null,
        order: null,
        continues_from: null,
        state_policy: {}
      },
      activation: {mode: "campaign_attach", default_active: false}
    },
    catalogs: {
      clues: [{
        id: "clue:misaligned-bricks",
        source_refs: [<clue receipt>]
      }],
      handouts: [],
      encounters: [],
      hazards: [],
      tomes: [],
      spells: [],
      mechanics: [{
        id: "san:impossible-flame",
        success_loss: "0",
        failure_loss: "1D4",
        source_refs: [<SAN receipt>]
      }]
    },
    narrative: {
      dossiers: [{
        id: "npc:caretaker",
        name: "The Caretaker",
        role: "witness",
        want: "keep the room sealed",
        fear: "the investigators will open it",
        secret_refs: ["secret:sealed-room"]
      }],
      endings: [{
        id: "ending:room-sealed",
        trigger: "seal the hidden room",
        consequences: ["the immediate threat is contained"]
      }]
    },
    dependencies: [],
    metadata: {
      language: "en",
      license: "private",
      attribution: "Synthetic example"
    }
  },
  expected_revision=<current draft revision>,
  idempotency_key="lantern-case:package:v1"
)
~~~

Refresh the returned revision.

## 6. Finalize

~~~text
module_draft(
  campaign_id=<authoring campaign>,
  action="finalize",
  data={
    job_id: <job>,
    package_id: "coc7e.module.lantern-case",
    confirmation: {
      confirmed: true,
      note: "Reviewed complete source, CoC profile, scene structure, clues, SAN mechanics, catalogs, narrative, dependencies, and diagnostics."
    }
  },
  expected_revision=<current draft revision>,
  idempotency_key="lantern-case:finalize:v1"
)
~~~

Require state compiled and retain the artifact.

## 7. Inspect and deliver

~~~text
content_pack(
  action="get",
  campaign_id=<authoring campaign>,
  data={
    kind: "module",
    artifact: <artifact>
  }
)
~~~

Verify schema version 2, system_id coc7e, checksum, reviewer confirmation,
source binding, and component counts. Stop here by default.

## 8. Optional installation

~~~text
content_pack(
  action="import",
  campaign_id=<target campaign>,
  data={
    kind: "module",
    artifact: <artifact>
  },
  expected_revision=<fresh target campaign revision>,
  idempotency_key="lantern-case:import:<target>"
)
~~~

Import remains inactive. If activation was explicitly requested, refresh the
target campaign revision and activate the returned imported module_id.
