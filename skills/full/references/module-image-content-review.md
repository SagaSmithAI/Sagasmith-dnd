# Image-only Module Content Review

Use this workflow when an imported module exposes a review-only statblock
candidate or a PDF visibly contains a creature statblock that its text layer
cannot recover. This is a source-recovery path, not permission to invent
mechanics. Perform actor preparation in `lobby` and never create or repair a
required actor after combat has begun.

First call `module_query(view="candidates")` for the exact module. Route by the
returned `execution_state`:

- For `review_ready`, inspect `normalized_content`, validation, scene, and every
  source chunk. Submit that exact normalized text to `module_draft(action="edit", operation="content")` with
  the returned `scene_id`, `source_chunk_ids`, and edition-matching
  `content_kind` (`dnd5e_2014_statblock` or `dnd5e_2024_statblock`); do not
  replace text evidence with a page-memory reconstruction.
- For a blocked 2014 card, do not submit the candidate as text evidence. First call
  `module_draft(action="edit", operation="statblock")` with its exact module, scene,
  stable content key, printed name, managed page, and optional asset id. The
  service performs local layout OCR, verifies the imported PDF checksum, and
  independently corroborates all critical facts. This route works for a
  text-only Agent. If it cannot establish one unambiguous complete card, use the
  visual workflow below only with an image-capable reviewer, or leave the actor
  unresolved. `recover_statblock` is intentionally 2014-only. A blocked 2024
  card must instead use complete exact indexed text through `submit_content`, or
  the visual workflow with an image-capable reviewer; never coerce it through
  the 2014 OCR normalizer.

## Ordered workflow

1. Use `module_query(view="index" | "scene")` to locate the appendix scene and
   confirm that the normalized text does not contain an executable statblock, or
   that the candidate was explicitly blocked by the evidence gate.
2. For a 2014 card, use `module_query(view="assets")`, select the managed PDF,
   and first call `module_draft(action="edit", operation="statblock")`. If the recovered card has no
   module-authored Multiattack semantic gap, re-read its immutable review and
   continue at step 5; do not re-transcribe it. If the response instead has
   `requires_agent_fill=true` and `review=null`, read only
   `recovery.normalized_content` plus
   `validation.agent_fill_requirements`. Have the Agent map every listed exact
   excerpt to the returned parsed weapon ids, modes, and explicit counts (or
   `resolution="agent_ruling"` for an unsupported custom procedure), then repeat
   `recover_statblock` with a fresh idempotency key and that
   `payload.agent_fill`. The first response is a checksum-bound OCR draft, not
   an immutable review or permission to infer missing text.
   For a 2024 card, skip this step and retain its edition-matching text evidence.
3. If 2014 recovery fails, or a 2024 card lacks complete indexed text, and the
   Agent can inspect images, call
   `module_draft(action="evidence")` for the cited page and inspect the
   returned image itself. A text-only Agent must stop here.
4. Transcribe only visible card facts into canonical English statblock Markdown
   for the campaign's locked 2014 or 2024 edition. Preserve the exact name,
   size/type/alignment, AC, HP formula, speed,
   six abilities, listed saves/skills/defenses/senses/languages, CR/XP, headings,
   attack bonus, reach/range, damage dice, damage bonus, and damage type. Do not
   fill an absent field from memory or a similar creature.
5. For an image-capable manual transcription, call
   `module_draft(action="edit", operation="content")` with the appendix `scene_id`, stable
   `content_key`, normalized Markdown, managed PDF or rendered-image asset,
   1-based page, a literal visual observation, and a fresh idempotency key. If
   the card contains any module-authored Multiattack, inspect the returned
   `agent_fill_requirements` and include the source-bound semantic fill described
   below in `payload.agent_fill`. This is mandatory even when the parser proposed
   executable options; do not add another phrase-specific parser rule.
6. Stop if validation rejects the card. If it returns `mixed`, review every
   warning and keep unresolved mechanics visible. `automatic` means only that
   the transcribed mechanics represented by the current engine are executable.
6. Re-read the immutable record with `module_query(view="content",
   payload={"review_id": ...})`; verify its asset checksum, page, scene, reviewer,
   content checksum, and `confidence="reviewed_image"`.
7. Create each required campaign actor with
   `character_create_from(mode="module_statblock", payload={"campaign_id": ...,
   "review_id": ..., "name": ..., "character_type": "monster"})`. Re-read the
   actor and verify AC, HP, attacks, source refs, and unresolved rules before
   adding its canonical id to the scene-preflight manifest. If the printed
   statblock repeats a known spell as a numeric action, that action is an explicit
   creature-specific override: the hydrated spell card's displayed effect/range
   and structured resolution must agree with the printed action, even when the
   base Core spell has a different range. Treat any display/settlement mismatch as
   a lobby blocker; never narrate the base spell value while settling the override.

If a room names a standard rule statblock and then states a small instance change,
import the exact standard source and use `character_create_from(mode="statblock")`
with `payload.variant`. Do not transcribe a second whole card or patch the resulting
sheet. Every variant requires a module `source_ref`; the runtime accepts only
explicit current/maximum HP, AC, languages, action removal, and narrow weapon-action
overrides. For example, a wounded unarmored Noble can set `current_hit_points`,
`armor_class`, `languages`, and `remove_actions`, while an animated gauntlet based
on Flying Sword can rename its weapon and change only the cited damage type.

```json
{
  "mode": "statblock",
  "payload": {
    "campaign_id": "campaign-id",
    "source_id": "exact-noble-rule-source-id",
    "name": "Klim Jhasso",
    "character_type": "npc",
    "variant": {
      "source_ref": "module-chunk:d12-banes-altar",
      "current_hit_points": 1,
      "armor_class": 10,
      "languages": ["Common", "Elvish"],
      "remove_actions": ["rapier"]
    }
  },
  "idempotency_key": "create-klim-jhasso-d12-v1"
}
```

Use only a managed `module-chunk:<id>`, `module-review:<id>`, or matching-edition
`rule-chunk:<id>` as `source_ref`; the MCP resolves it and returns structured
`variant_evidence`. Reject a variant if the cited source does not explicitly
establish every change, if an action id is ambiguous, or if the desired change is
outside the whitelist. The base source and variant source must both remain visible
in actor provenance.

```json
{
  "campaign_id": "campaign-id",
  "module_id": "module-id",
  "scene_id": "appendix-creature-scene-id",
  "content_key": "necromite-of-myrkul",
  "content_kind": "dnd5e_2014_statblock",
  "normalized_content": "# Necromite of Myrkul\n\n*Medium humanoid (human), neutral evil*\n...",
  "source_asset_id": "managed-pdf-or-rendered-page-asset-id",
  "page_number": 181,
  "observation": "The complete Necromite of Myrkul card is visible in the upper-left column.",
  "idempotency_key": "review-necromite-page-181-v1"
}
```

For a 2024 campaign the same request must use
`"content_kind": "dnd5e_2024_statblock"`; the MCP rejects a content kind that
does not match the campaign edition.

Numeric melee, ranged, weapon, and spell attacks with explicit to-hit, range or
reach, dice, bonus, and type can settle automatically. For every exact
module-specific Multiattack, the Agent must submit:

```json
{
  "agent_fill": {
    "multiattack_options": [
      {
        "activity_id": "multiattack-action",
        "source_excerpt": "The drake attacks twice, once with its bite and once with its tail.",
        "reason": "The exact source names one use of each parsed weapon.",
        "options": [
          {
            "id": "bite-and-tail",
            "attacks": [
              {"weapon_id": "bite", "attack_mode": "melee", "count": 1},
              {"weapon_id": "tail", "attack_mode": "melee", "count": 1}
            ]
          }
        ]
      }
    ]
  }
}
```

The excerpt must exactly match the reviewed activity, every weapon id and mode
must already exist on the parsed card, and the declarations must cover every
Multiattack activity exactly once. A parser-recognized option is only a candidate
until this submission. The stored fill is attributed to the Agent with
`module_specific_procedure`; it is not a raw sheet patch.
Multiattacks that replace attacks with special activities, narrative traits,
incomplete spellcasting, recharge/choice semantics, or other unsupported
effects use the same declaration with `resolution="agent_ruling"` and no
`options`. This explicitly removes any parser proposal and leaves the action as
an Agent DM ruling when selected. Player choices and
missing-image/source review still pause at their own boundaries. Never erase a
warning to make preflight pass.

The review belongs to the imported campaign module and is immutable provenance;
it is not branch-scoped narrative state. Actors created from it retain the review
id, module/scene ids, page, and asset checksum in their notes. A corrected visual
transcription creates a new review record and new actor preparation; do not mutate
the old evidence or silently rewrite an active combatant.

For a regression import, checkpoint in `lobby`, create and checkout a disposable
branch, build the actor from the immutable review, and verify every spell card's
displayed range against its structured range override. Snapshot that branch and
checkout the source branch again. The newly created actor must not appear in the
source branch; this verifies new-import behavior without migrating or rewriting
old campaign actors.
