---
name: dnd-dm
description: "Run D&D 5e 2014 or 2024 sessions through the SagaSmith D&D MCP server."
---

# D&D Dungeon Master Runtime Deep Reference

This manual was moved intact from the parent `dnd-dm/SKILL.md`. Resolve every
relative path written below against `full/skills/dnd-dm/`, its original base;
the concise parent Skill and the MCP native tool list are the current entry points.

## Contents

- Runtime and turn loop
- MCP tool reference
- Module narrative context
- Actor cards and party state
- Combat boundary

## Runtime

This full skill is MCP-first. Start with `storage_status`, then call
`exposure(action="open")` for the active campaign. Search for exact tool ids,
change the native list with `exposure(action="set")`, refresh after
`tools/list_changed`, and call listed tools directly. The short fragments under
`../../references/skill-groups/` route ordinary work; this larger document is
an on-demand deep reference.
All raw tool names below may be prefixed by the host, for example
`mcp_sagasmith_dnd_`.
For user rulebook import, also require the structured/source-bound flags from
`server_capabilities.rulebook_import` before exposing that workflow.
If the server is unavailable, stop using this skill and load `standalone/` rather
than switching to a local CLI.

Do not read the entire MCP contract before every mutation. Search or read the
exact relevant contract section. Load
these deep references only when needed:

- actor creation or advancement: `references/CHAR_CREATION.md`
- actor, items, wallet, spells, effects, or resources:
  `../../references/character-schema-v2.md`
- module preparation or scene transitions: `references/MODULE_INDEX.md` and
  `references/MODULE_ARC.md`
- real campaign rehearsal or corpus regression:
  `references/CAMPAIGN_REGRESSION.md`
- module maps, diagrams, or missing topology:
  `../../references/module-visual-atlas.md`
- image-only module creature cards:
  `../../references/module-image-content-review.md`
- user PDF/Markdown rulebook import: `../../references/rulebook-import.md`
- catalogued core or extension character options:
  `../../references/content-catalog.md`
- tactical positioning or reusable narration: `references/DM_MAP_SYS.md` and
  `references/DM_TEMPLATES.md`

## Turn Loop

Outside play, select `lobby` with `game_phase(action="set")` for module writing/import,
campaign setup, and character creation. Before the first in-character scene,
switch to `play`. `combat_start` enters `combat` automatically and `combat_end`
returns to `play`; never simulate a phase transition only in narration.
Before module import or character building, read the campaign rule profile and
explain output. The locked `dnd5e.core.2014` or `dnd5e.core.2024` provider must
match the adventure/table edition; do not let a default edition silently define
the campaign.

1. Resolve `scope_id` (`party`, `group:<id>`, or `player:<id>`), then call
   `module_query(view="current")`. Player scopes inherit party progress until they have their own.
2. Read that scene through `module_query(view="scene")`. Use `module_search` only to select a
   candidate, then call `module_expand` before relying on a chunk. A search/expanded chunk can
   lack a scene id or straddle scene boundaries; never reuse its heading or page match as combat
   evidence until the exact indexed scene has been read. Carry the exact
   `source_ref` returned by `module_expand`, including its service-owned
   `content_sha256`. If expansion omits it, stop and repair the import/exposure
   path; never synthesize the hash client-side. Copy every runtime excerpt from
   that exact expanded chunk, not merely from the concatenated scene text. Before
   any mutation, expand the cited `chunk_id` again and require the returned
   canonical source metadata, digest, and excerpt to match.
3. Ask for intent when it is ambiguous. Never reveal unseen rooms, future twists,
   hidden motives, or sibling-branch facts.
4. Use campaign-bound `rule_search(campaign_id=...)` then
   `rule_expand(campaign_id=...)` for disputed or edition-sensitive rules. The
   server limits results to this campaign's core, Lobby imports, and active
   branch Pack sources; never reuse a chunk id from another campaign.
5. Imported rulebook text is evidence, not executable mechanics. In `lobby`, use
   `rulebook_draft(start)` -> `evidence/edit` -> `finalize`, then use
   `content_pack(get|activate, kind="core_rules"|"addon")` and search/expand the
   exact source. Core+D&D own the first mechanical pass; the Agent repeatedly
   reviews and edits source-bound candidates until it explicitly finalizes the
   immutable Pack. Enable an exact version only with explicit campaign-owner/DM
   approval. Never change the
   lock during combat or silently substitute a missing version.
   `campaign_rules(action="explain")` must also show the locked `dnd5e.core.2014` or
   `dnd5e.core.2024` provider; treat a missing or mismatched core fingerprint as
   a hard stop, not permission to bypass the existing engine boundaries.
   For an imported module PDF, also require every preview scene to carry a valid
   source page range. A parser profile/version change is a new normalized module
   revision even if the PDF checksum is unchanged; rerun the full staged import
   lifecycle and review the resulting index before play.
   If a reviewed unified content package already exists, use
   `content_pack(action="import", kind=<archive-route>)` instead of repeating source/OCR review.
   It must return imported definitions with exact dependency status and fresh
   source/chunk ids; Owner/DM campaign activation remains separate. Export with
   `content_pack(action="export", kind="core_rules"|"addon")`.
6. For character options, use rule/content retrieval and present only entries
   available to the campaign's locked Core edition and enabled branch Packs.
   Apply only a returned id through `character_content_apply`; respect a
   `pending_ruling` response for unresolved prerequisites or effects. Supply
   the legal spell source class and grant method, the target base class for a
   multiclass subclass, every required background/species choice, and every
   class/subclass feature whose minimum level is met. A class or species name
   without its granted feature cards and traits is an incomplete actor, not a
   usable shortcut. Never patch the raw sheet to bypass selection validation.
7. Resolve openly with `dnd_dice_roll` or `dnd_check`.
   For Play-phase `character_check` and full-playthrough `resolve-check`, pass a
   named skill such as `perception` or `persuasion` as `ability` and omit
   client `proficient`/`bonus` fields. The service derives none, half
   proficiency, proficiency, expertise, persistent skill bonuses, and the
   associated ability from the authoritative actor card. The same rule applies
   independently to both sides of `character_check(action="contest")`; a
   boolean facade must never collapse expertise into ordinary proficiency.
   When the whole party succeeds or fails as one group, explicitly use
   `character_check(action="group")` or full-playthrough
   `resolve-group-check` with every participant. Do not precompute the
   threshold: the 2014 Core engine rolls every actor and succeeds the group
   when at least half succeed. Do not use a group check for surprise, where
   each observer and hidden creature must be compared separately.
   Before a module table roll with external modifiers, build a branch-local
   modifier ledger from the complete expanded procedure. Keep every modifier
   source in its own entry with a stable id, numeric value, applicability,
   lifetime, consumption rule, and distinct world-state key. In particular, do
   not merge a cumulative count, a one-use next-roll bonus, and a static
   situational modifier merely because all three currently add the same number.
   Pass every applied entry through the public full-playthrough driver's
   repeatable `--roll-modifier-json`; its values must sum to the expression's
   trailing modifier, and independent entries must not share one state key.
   After the roll, atomically increment persistent counts, consume eligible
   limited-use entries, and add any newly earned modifier as separate
   `record-outcome` facts/world state before the next table roll.
8. Persist resolved scene continuity with one `memory_change(action="commit")`: one event,
   stable-key objective fact changes, exact per-actor knowledge changes, and an
   optional snapshot. Never infer who knows a fact from the fact itself. Mark
   direct observation as `witnessed`; when an absent, unconscious, newly joined,
   or replacement actor learns it through an explicit in-fiction briefing, mark
   only that recipient's new entry as `told_by`. Party membership alone never
   transfers ActorKnowledge.
9. Use an administrative `snapshot_create` only when no scene continuity unit is
   being written, such as immediately before a dangerous restore. Use
   `snapshot_query(view="verify" | "lineage")` before restore.
   During full campaign regression, batch ordinary scene actions with the
   public driver's supported `--defer-checkpoint` paths, then end the batch with
   one public `checkpoint` action with a distinct stable `--occurrence-id` and
   verify it. Reuse that id only for an exact retry; a later same-labeled
   checkpoint gets a new id. The following manifest `get`
   must project that snapshot id as both a DAG node and
   `snapshot_dag.head_snapshot_id`; a runtime-only node is insufficient. Do not defer combat end,
   death/stable recovery, replacement, rests, major branches, module
   transitions, or campaign endings. A contiguous same-scene party advancement
   may defer only the individual `advance-level` snapshots after each actor has
   been fully verified, then seal the whole advancement batch with one public
   checkpoint before entering another sourced scene. Follow
   `references/CAMPAIGN_REGRESSION.md` for the exact supported action list and
   interrupted-batch recovery.
   Post-advance verification must preserve every independently declared resource.
   A feature with empty `resource_key` owns its card-local `uses`; the runtime does
   not guess that a similarly labelled top-level resource is a removable shadow.
   For a multi-action event, validate every source/actor report, manifest event
   predecessor, and public clock before the first mutation. Bind following time
   or rest writes to the current branch with the driver's explicit prerequisite
   outcome/actor flags and, for a rest, its expected start clock. Run dependent
   actions in one fail-fast process; never let a failed prepare or outcome command
   fall through to a later time advance or rest.
   Every repeatable playthrough mutation must carry its explicit stable
   occurrence/business id. Reuse it only for an exact retry; a later identical
   scene visit, check, event, rest, recovery, XP award, checkpoint, explicit
   manifest sync, narrative-NPC creation, source-item transfer,
   environmental-damage event, or activity use gets a new id. Narrative reason
   text is not an occurrence id.
   At a source-defined conclusion, first execute every source-defined item,
   check, choice, and mechanic through its owning public facade and retain those
   independent receipts. Source-declared modifiers to a check require their own
   earlier source-bound semantic event/fact commits; derive the final DC only
   from the base DC and those exact receipts. A naked fact or prose assertion
   cannot apply a modifier, and a semantic event cannot certify the mechanical
   roll it precedes. Only then record exact outcome/world/NPC state; an
   Agent-authored fact or progress flag cannot certify the event it merely
   asserts. Then use the public regression driver's `configure-ending` and
   `verify-ending` actions. Require every configured check to pass and a verified
   terminal checkpoint to become the manifest DAG head. Historical final combat
   evidence does not block an ending; only a combat record whose authoritative
   `active` flag is true does.
   For consecutive published volumes in one campaign line, keep the same
   campaign, run, branch, party, ActorKnowledge, clocks, random stream, and world
   state. After verifying the earlier volume, use the driver's
   `continue-segment` action to archive that terminal evidence and enter the
   next module through its source-bound authoritative scene transition. Never
   reset or recreate continuity merely because the current volume ended.

### Agent adjudication is the default DM ruling

When a public tool returns `pending_ruling`, or preflight exposes a
`manual_rulings`/`ruling_requirements` entry about scene facts, eligibility,
observation, an unstructured source action, a spell effect, or a narrative
consequence, the SagaSmith Agent assumes the DM role and reasons through that
ruling by default.
Do not pause merely because the boundary is labelled "DM ruling", and do not
require a human to restate a decision that the active rules, exact module text,
current scene, actor cards, and branch state can support.
Read ownership from the domain result itself: a live `pending_ruling` must carry
`default_resolver`, `ruling_kind`, and `policy_ref`, even on a native dynamically
exposed call. A compact facade must preserve that same classification at its top
level. If an engine prerequisite returns before commit with `committed=false`
and a `retry_contract`, treat it as control returning to the named resolver;
an Agent-owned boundary keeps the current revision while the Agent supplies the
missing scene/rule fact and retries through public tools. Do not turn that
pre-commit pause into a failed fictional action.
When several nested requirements coexist, accept the rule engine's returned
top-level ownership classification. Do not keep a second client-side ruling-kind
list or derive precedence by sorting strings; the server owns that vocabulary
and priority so a player choice, approval, or source-review boundary cannot be
silently downgraded by an Agent-owned requirement.

Before deciding, read the exact expanded rule/module/scene evidence and the
current affected actors. Record the ruling's source reference or exact excerpt,
facts used, conclusion, any server-side rolls, and the public mutations that
commit the outcome. Prefer an existing structured public settlement whenever
one applies. Otherwise use only generic public dice, check, HP, condition, map,
continuity, knowledge, clock, and manifest operations; never turn Agent
reasoning into a direct database or raw-sheet write.

Keep these distinct:

- A DM adjudication is Agent-owned by default. This includes a module-specific
  procedure that the generic rules engine intentionally does not encode.
- A player-owned choice remains player-owned: targets, build options, prepared
  spells, reaction use/decline, and other character intent must not be silently
  chosen by the Agent.
- Missing or contradictory source evidence requires import/retrieval repair or
  explicit review. Owner-only pack activation, permission changes, and other
  approval boundaries remain owner/DM approvals rather than inferred facts.

Cards and import diffs use the same ownership contract. Preserve
`ruling_requirement`/`ruling_requirements` on descriptive activities, feat
prerequisites, source-bound spells, critical follow-ups, party-size reviews, and
`needs_dm_review` scene-progress impacts. Ordinary source-or-scene adjudication
defaults to the Agent; a missing ranged/spell range, incomplete hydration, or
other absent/contradictory source mechanic remains
`missing_or_conflicting_source_review` and cannot be invented.
Apply the same rule before a live action exists. A rule import job whose
`state="review_required"` publishes `review_resolution` and
`review_requirements`; a `review_ready` rule or module candidate publishes its
own `ruling_requirement`. The Agent reviews those entries from the exact text
chunks by default. A blocked/manual-review candidate whose requirement names
`missing_or_conflicting_source_review` remains external and must not be accepted
from rules memory.
Declarative extension rules follow the same distinction:
`ruling.require` defaults to Agent reasoning, while `choice.require` remains an
external player-owned choice. Regression drivers must preserve the typed ruling
as structured output instead of reducing it to a generic failure message.

`pending_ruling` may be returned after an action, use, slot, or reaction has
already been paid. Read the receipt and latest revision and never pay it again.
A descriptive reviewed statblock action carries
`choices.manual_ruling.kind="descriptive_activity"`; invoking it through
`combat_use_activity` pays its recorded timing and returns the exact source
excerpt for Agent adjudication. A module-specific ruling does not require
`combat_choice` unless the server actually opened an owned choice window.

## MCP Tool Reference

| Workflow | MCP tools |
|---|---|
| Campaign | `campaign_create`, `campaign_query`, `campaign_change`, `access_grant` |
| Rules | `rulebook_draft(start/get/evidence/edit/finalize)`, `rule_search`, `rule_expand`, `content_pack(list/get/import/export/activate/deactivate/remove)`, `campaign_rules`, `character_content_apply` |
| Module lifecycle | `module_draft(start/get/evidence/edit/finalize)`, `content_pack(import/export/activate)`, `module_query(list/index/assets/content/candidates/preflight)` |
| Scene play | `module_query(current/scene/progress)`, `module_search`, `module_expand`, `module_set_progress` including `spatial_review` |
| Rolls | `dnd_dice_roll`, `dnd_check`, `dnd_ability_roll`, `character_check(action="check" \| "group" \| "contest" \| "reroll")` |
| Chases | `chase(action="start")`, `chase(action="query")`, `chase(action="take_turn")`, `chase(action="end")` |
| World continuity | `memory_change(action="commit")`, `campaign_event`, `memory_change`, `memory_query` |
| Actor continuity | `actor_knowledge_change`, `actor_knowledge_query`, `continuity_context` |
| Saves and audit | `snapshot_create`, `snapshot_query`, `snapshot_restore`, `branch_query`, `branch_change`, `state_revision` |
| Combat | `combat_start`, `combat_join`, `combat_query`, `combat_preflight_attack`, `combat_resolve_attack`, `combat_movement`, `combat_common_action`, `combat_use_activity`, `combat_cast_spell`, `combat_ready`, `combat_reaction_attack`, `combat_end_turn`, `combat_check`, `combat_concentration_check`, `combat_hp_change`, `combat_map_patch`, `combat_end` |
| Owned pending combat windows | `combat_choice(resolve/resolve_defense/on_hit_ruling/execute_plan)` |
| Custom-content solution lookup/compilation | DM-only `content_solution(query/compile)` in Lobby, Play, or Combat |
| Agent DM adjudication without an owned window | Relevant public dice, check, map, state, memory, and manifest tools |

## Module Narrative Context

Do not compile module-authored motives, bargains, retreats, deceptions, or scene
consequences into mechanical cards or a narrative trigger DSL. Before the first
live use, create or update a stable DM-only world fact with
`memory_change(action="upsert")`:

```json
{
  "fact_key": "context:actor:<actor-id>:<module-scope>",
  "kind": "context_anchor",
  "subject_ref": "actor:<actor-id>",
  "predicate": "",
  "content": "Retrieval anchor only.",
  "metadata": {
    "schema_version": 1,
    "purpose": "Short retrieval label",
    "related_refs": [
      "scene:<scene-id>",
      "quest:<quest-id>",
      "item:<item-id>"
    ],
    "source_bindings": [
      {
        "source_ref": {
          "module_id": "...",
          "scene_id": "...",
          "chunk_id": "...",
          "page_start": 1,
          "page_end": 1,
          "heading_path": ["..."],
          "content_sha256": "..."
        },
        "source_excerpt": "Exact normalized module text."
      }
    ]
  },
  "importance": 5,
  "disclosure_scope": "dm"
}
```

Only the listed metadata fields are legal. `source_excerpt` must be present in
the cited immutable chunk. Never add `trigger`, `condition`, `action`, `result`,
Agent instructions, translations, or paraphrases. The anchor is a branch-scoped
index into source, not authority beyond the returned original text.

Before deciding the behavior of an active module NPC or the result of a
source-authored negotiation, call `continuity_context` as DM and pass
`related_refs` for every relevant current actor, scene/location, active quest,
and key item. Read `module_evidence` as non-executable source context. Combine it
with current HP, conditions, position, ownership, and committed events; the Agent
chooses intent and adjudicates the situation. Then use ordinary public movement,
check, combat, item, event, knowledge, and manifest tools. Server rules and RNG
remain authoritative. Save only the actual choice and outcome in the normal
event/fact/ActorKnowledge ledgers; do not save unchosen source branches as if
they happened. Module evidence is DM-only and must not enter a player's context
or ActorKnowledge until the character reasonably learns it.

When the saved event/fact/knowledge cites a module source pinned by an active
matching context anchor, copy the returned `context_receipt` into
`memory_change(action="commit").payload.context_receipt`. The receipt proves
that this principal read the exact pinned evidence on the current branch and
campaign revision. Do not reuse it after another write, phase/branch change, or
restore; reread `continuity_context`. Never fabricate or edit a receipt.

## Actor Cards and Party State

When a module invokes the 2014 DMG chase procedure, keep the campaign in
`play` and load `play.chase`; do not enter combat or create a battle map.
Read and expand the exact chase scene, then pass its service-owned
module/scene/chunk/page/hash `source_ref` and an exact excerpt to
`chase(action="start")`. Include the quarry and every pursuer as canonical actors,
preserve the printed starting distance, and add a `close_transition` only when
the same source explicitly redirects the chase when the quarry is nearly
caught or reaches a destination. That transition must carry its own exact
same-scene `source_ref` and `source_excerpt`, even when they point to a different
chunk from the chase start; its `summary` must equal that normalized excerpt.
Never submit a caller-authored destination summary without this second citation.
Advance only the actor returned by
`chase(action="query").result.current`. For a turn, put `actor_id`,
`turn_action`, complication choice, visibility, and the actor revision in that
action's payload. `chase(action="take_turn")` owns initiative order, movement,
the `3 + Constitution modifier` free Dash allowance, each later Dash's DC 10
Constitution check, chase exhaustion, the Urban Chase Complications d20 and
the next-participant rule. Supply a complication choice only from the printed
options; do not pre-roll, patch distance, add fatigue, or apply complication
damage separately. A visible quarry automatically fails the end-of-round
escape check; never invent a Stealth roll while the lead pursuer still sees it.
Chase exhaustion remains distinct evidence on the actor and every level gained
that way ends on the next Short or Long Rest, while unrelated exhaustion
remains. After the source-backed chase outcome, record the scene transition and
create one verified scene checkpoint; do not checkpoint every chase turn.
No Conversation may remain active when a Chase starts. A Chase is an exclusive
Play procedure, not a phase. Before Combat, end and re-query the Chase, then
call `combat_start` separately.

Every live PC, NPC, and monster is an authoritative v2 actor card. Use
`character_query(view="get")` after every write. Use granular facade tools instead of replacing a whole
sheet for a small change:

```text
inventory_change | inventory_transfer | wallet_change
character_spell_prepare | character_state_change | character_action
character_metadata_update | character_content_apply | character_ability_apply
campaign_query(view="party")
```

Use `character_create_from(mode="build")` for a PC when a library template and its first campaign
instance must be created atomically. Always supply a stable idempotency key so a
transport retry replays that same pair. Use `character_query(view="library")` and
`character_create_from(mode="template")` for existing templates. New subjective
information belongs in the actor-knowledge ledger.

For ability generation, `manual` preserves explicitly entered scores without
claiming a dice audit, while `standard_array` and `point_buy` enforce their rule
budgets. Rolled generation is two-phase through `character_ability_apply`: omit
assignments to let the engine persist the six 4d6-drop-lowest results, then assign
those exact results under the returned character revision. Never send `rolls` or
reroll a pending set. See `references/CHAR_CREATION.md` for the complete contract.
When presenting character creation choices, keep all four paths visible; never
remove `manual` merely because a rules-validated alternative is available.

For a module NPC or monster, use the exact imported standard statblock or an
immutable reviewed image card. If the module explicitly changes only HP, AC,
creature type, languages, or an action, pass a source-cited `variant` to the
statblock creation call. Never rebuild or raw-patch the full sheet for a small
module instance change. A type replacement such as beast to undead must use
`creature_type` and cite the exact managed module chunk or review that says so.
When a named NPC has authored identity and role but no combat statblock, create a
source-bound noncombat identity card with
`character_create_from(mode="narrative_npc")`; require the exact active
module/scene/chunk/page/hash and an excerpt containing the name. Treat its
`narrative_only` default mechanics as sentinels, never as authored AC/HP/ability
scores, and never include that actor in a check or encounter until an exact
statblock is imported.
For several anonymous NPCs sharing one source label, create distinct
`anonymous_source_instance` cards with the exact printed `source_identity` and
stable `instance_key`. The safe default display name is
`<source_identity> [<instance_key>]`. When distinct names materially help the
Agent run or remember those source-authored instances, the Agent may assign each
one a proper name only through a settled, strictly bound
`identity_agent_ruling`: its `assigned_name`, `source_identity`, and
`instance_key` must exactly match the creation request. Preserve the
`agent_named_source_instance` provenance tag. Never use naming to create extra
instances, invent mechanics, or merge their ActorKnowledge.
If that same narrative actor later needs mechanics and an exact rule or reviewed
module statblock is available, rebuild it in place with the statblock creation
path's `replace_character_id` and current revision. Preserve its Actor ID, name,
summary, prior notes, and ActorKnowledge; the service appends statblock
provenance while replacing only the sentinel sheet with authoritative mechanics.
Do not create a second combat double for the same person.

After item writes, treat `character_query(view="get").derived.inventory.weapon_attacks` and
`character_query(view="get").derived.inventory.encumbrance` as authoritative. Represent one
active concentration spell as one active effect with `concentration: true` and its
`source_spell_id`.
The same derived response applies actor weapon proficiencies and Finesse ability
selection, armor category proficiency, heavy-armor Strength speed penalties,
variant encumbrance speed and d20 penalties, and nonproficient-armor spellcasting
blocks. Do not duplicate those penalties in client-authored roll arguments.

When a source magic item casts spells from charges, add it through
`inventory_change(action="add")` with the exact module `source_key`, charge
resource, source-declared `charge_rules`, and active spell artifact ids under
`mechanics.spellcasting`. Preserve any item-specific casting time, component,
attunement, and class-spell-list requirements. The server hydrates the exact
locked spell cards; never copy those spells into the actor's ordinary known or
prepared list. Cast with `source_item_id` through `character_action` or
`combat_cast_spell`: the item charges, action economy, automatic self effect, and
last-charge check commit together. At the printed recovery trigger, call
`inventory_change(action="recharge")`; the service rolls the source formula and
clamps to the recorded maximum. Never roll or patch charges separately.
An item whose card says that attunement is required enters Play as
`attunement="required"`: do not add it as already attuned or change that field
with `inventory_change(action="update")`. Put its item id in the actor's Short
Rest request as `attune_item_id`. The Agent acting as DM checks the exact source
prerequisite and submits `attunement_prerequisite_confirmed=true`; do not pause
for a separate human when the locked rule and actor state answer the question.
The rest, three-item limit, duplicate-copy limit, and activation of the item's
magical properties commit together. An
unattuned shield still grants its ordinary shield benefit but no magical bonus.
Do not transfer an item while its state is `attuned`; transfer neither grants
the recipient attunement nor, by itself, satisfies any 2014 condition that ends
the original bond. If an attunement prerequisite cannot be proven from the
actor card and exact source, do not set the confirmation. The Agent first
searches the locked rules and imported source; only missing or conflicting
evidence goes to external source review.
For a source-authored combat sequence, keep each opening item cast in printed
order and call `combat_cast_spell` with `source_item_id`. Its item-specific
casting time remains authoritative even when the underlying spell card normally
uses another action type. A printed HP-threshold surrender fires only after the
NPC is still alive and the required no-escape predicate is confirmed; close the
encounter as `surrender` before resolving another attack.

When a searched and expanded module chunk grants one parcel containing currency
and/or multiple objects, read the exact scene and use
`campaign_change(action="loot_acquire")`. Give the parcel and every item stable
ids, pass the exact chunk `source_ref`, and then record ActorKnowledge only for
the witnesses. Do not credit the shared wallet and add the items in separate
writes: a transport failure must not leave half of a chest acquired.
For every weapon in that parcel, set `mechanics.proficient` explicitly from the
intended recipient's current class, species, feat, and other rule-backed
proficiencies. A monster is proficient with weapons in its own stat block, but
a PC who retrieves one uses the normal equipment proficiency rules; never copy
the monster's attack bonus or let an omitted field grant proficiency. If the
recipient is not yet known or proficiency is not proven, record `false`.

For lodging, supplies, services, and other shared-wallet expenses in `play`, use
`campaign_change(action="currency_spend")`. Pass one stable branch-local spend
id, the exact positive coin denominations paid, the scene chunk `source_ref`
that establishes the offered expense, and the active Core/Skill `rule_ref` or
reviewed price basis. The transaction must reject insufficient funds without
partially deducting any denomination and must persist the payment audit with the
new wallet in one commit. Then record only the participants who witnessed the
purchase, sync the playthrough manifest, and verify a checkpoint. Do not use a
sequence of negative `wallet_change` adjustments for one bill.

For an identified standard healing potion in the shared stash outside combat,
call `campaign_change(action="consumable_use")`. Keep its use id stable and pass
the target's current revision. The Runtime owns the Core `2d4+2` roll and commits
the reduced stack, healing, random-stream receipt, and rule receipt atomically.
Never call `inventory_change(remove)`, `dnd_dice_roll`, and
`character_state_change(heal)` as three substitutes for drinking one potion.

For a source-cited bargain, tribute, gift, handoff, or destruction that removes
a non-consumable party or character item, call
`campaign_change(action="item_spend")`. Provide a stable spend id, exact item id
and positive quantity, reason, and expanded module chunk reference. For a
character-owned item, also provide that character id and its current revision;
the facade requires both together and atomically revises the character card and
campaign audit. Omit both only for the shared party stash. Use the
full-playthrough `spend-item` path with `--item-actor-id` for private inventory
so the inventory removal, branch audit, witnessed ActorKnowledge, manifest sync,
and checkpoint remain linked. Never record only the narrative disposition while
the canonical owner still contains the item.

When a module yields a found spellbook, add one `kind="spellbook"` inventory item
for each physically distinct book. Preserve its edition, exact source scene/key,
copyability, owner mark, resolved catalog `spell_ids`, and
`unresolved_spell_names`. A name absent from the active content catalog stays
unresolved and non-executable; never drop it, substitute a similar spell, or
fabricate an artifact id. Record discovery through `memory_change(action="commit")`, with the
objective item fact and separate ActorKnowledge entries only for actual witnesses.

Discovery does not add spells to a Wizard's personal spellbook. During `play`,
copy exactly one returned spell id with `character_content_apply` using
`method="spellbook_copy"`, the source owner/item id, payment owner, and an exact
coin payment. The runtime validates Wizard/class-level eligibility, performs the
2014 decipher-and-copy process, advances time and all timed actor/world effects,
and applies source-bound discounts such as Evocation Savant. It does not invent
currency exchange or change. A missing source, unresolved spell name,
insufficient exact payment, unavailable Core lock, or failed validation must
leave character, wallet, clock, inventory, and effects unchanged.

During initial lobby setup, submit the complete level 1+ prepared list through
`character_spell_prepare(mode="replace_all", event="setup")`; use `mode="set"`
only for an initial setup edit. Once the campaign first enters live play,
returning to lobby does not reopen setup. During live play, a prepared-list
change must be a member choice in
`campaign_change(action="party_rest", prepared_spell_ids=[...])`. Do not
simulate a long rest by repeated toggles. The runtime enforces 2014/2024 class
timing and replacement count, class-level spell eligibility, Wizard spellbook
membership, always-prepared and cantrip exclusions, and multiclass
`grant.source_key` ownership. An unprepared level 1+ spell on a prepared caster's
card is not castable merely because its access record says it is known.

For a Short Rest, preflight every member, then submit one
`campaign_change(action="party_rest", payload={rest_type: "short_rest",
duration_minutes, members})` transaction. Put
`hit_dice_spends=[{"key": <recorded hit-die key>, "count": <positive integer>}]`
on the applicable member record.
Never submit a die result: the engine validates the available pool, rolls every
spent die, adds the card's Constitution modifier, and returns `hit_dice_rolls`
for audit. Long Rests use the same atomic party-rest surface. Never advance
either rest's clock separately or call individual actor rests; the retired
`character_state_change(action="rest")` path cannot preserve one transaction
across the clock and every participant.
Long rests reject `hit_dice_spends`; short rests
reject long-rest hit-die recovery allocations and `food_and_drink`. A creature
at 0 HP or Dead receives no rest benefit.
The service derives sleep/light-activity/Trance timing from rest type,
`duration_minutes`, and source-granted features. Do not submit a caller-authored
`rest_schedule`. A Monk regains Ki only if the same rest records at least 30
minutes of `rest_activity_minutes.meditation`; never infer that activity merely
because an hour elapsed.

If a Wizard chooses Arcane Recovery at the end of that short rest, include
`arcane_recovery={"<slot level>": <count>}` in the same rest call. The engine
requires the recorded feature, limits the combined recovered slot levels to half
the Wizard level rounded up, forbids level 6+ slots, records the use, and restores
only actually missing slots. For 2014, this is once per
campaign day, not once per long rest: the MCP requires the branch-local clock,
records the last-used day on the feature, and a long rest does not reset it. Do
not apply the rest first and patch spell slots afterward.

If a 2014 Circle of the Land Druid chooses Natural Recovery, include
`natural_recovery={"<slot level>": <count>}` plus an explicit positive
`rest_activity_minutes.meditation` declaration in that Short Rest call. The
engine enforces the source-bound subclass feature, the half-Druid-level
rounded-up limit, the level-6 exclusion, missing slots, and once-per-Long-Rest
use. A level-20 2014 Sorcerer's source-bound Sorcerous Restoration is automatic:
the same Short Rest restores exactly 4 missing Sorcery Points, capped at the
resource maximum. A level-5+ 2024 Sorcerer instead makes an explicit optional
choice: put `sorcerous_restoration_points=<positive integer>` on that member's
Short Rest record. The value cannot exceed half the Sorcerer level rounded down
or the actually missing Sorcery Points, and the source-bound feature use is then
unavailable until a Long Rest. Omit the field to decline the recovery; never
apply the 2014 automatic four-point rule to a 2024 card. Include the same field
in `character_query(view="rest")` preflight before the party write. Never use
Arcane Recovery's once-per-campaign-day timing for Natural Recovery or restore
all Sorcery Points.

For a 2014 Bard's Song of Rest, include
`song_of_rest_source_actor_id=<bard actor id>` in each same-rest member call
that spends at least one Hit Die and can hear the performance. The Bard must
participate in that Short Rest, remain conscious, and have the source-bound
feature. The engine validates the Bard level, rolls exactly one extra die per
eligible creature (d6, d8 at Bard 9, d10 at Bard 13, or d12 at Bard 17), caps
healing at the creature's maximum HP, and records the roll. Do not add one die
per Hit Die, apply the bonus to a member who spent none, or patch HP afterward.

Level advancement is a `lobby` transaction, not a sheet replacement. Confirm the
campaign's explicit `milestone` or `xp` mode. In XP mode, first use the atomic
`campaign_change(action="experience_award")`; it records source-bound awards and
returns eligibility without auto-leveling. In milestone mode, never invent
encounter XP. Settle either kind of trigger before entering a later sourced scene.
Preserve the exact award evidence and call
`character_state_change(action="level_advance")` with the existing class, fixed
or rolled HP method, `reason`, and `source_ref`. Never provide `hp_roll`: for the
rolled method the engine rolls the class Hit Die after idempotency, revision,
content, rules, and XP-threshold checks and returns the roll in
`advancement.hit_points.roll`. Current HP is not healed.
Then exhaust `advancement.follow_up`: apply eligible class features, resolve any
subclass and spell choices from the active content catalog, apply newly eligible
subclass features with every structured choice field and exact choice count
satisfied, verify that the level transaction materialized newly unlocked
always-prepared subclass spells, add newly selected prepared-class spell cards
with `method="class_prepared"`, and re-read derived state. That method only
hydrates a source-legal class-list card; it must leave `access.prepared=false`.
For 2014 Cleric, Druid, Paladin, and Wizard, gaining a level never changes the
prepared list. Wizard level choices add two spells to the spellbook only. Apply
any new prepared list through the next completed Long Rest, including newly
available preparation capacity. Always-prepared spells stay outside the
caller-selected list and do not consume its maximum. Snapshot before entering
another sourced scene.
When applying multiclass features, Channel Divinity is one shared resource,
Extra Attack uses the highest source-granted attacks-per-Action value rather
than adding class features together, and a character who already has the class
feature Unarmored Defense cannot gain it again. Alternative AC calculations
such as Draconic Resilience and Mage Armor remain alternatives; never sum their
formulas. A Constitution or per-class-level feature changes maximum HP only
unless an explicit initial-setup full-HP flag is accepted during Lobby. Under
2014 rules, level advancement increases the recorded base maximum but does not
remove exhaustion: at exhaustion level 4 or higher, the derived and manifest
maximum must remain half the new base maximum, rounded down, and current HP must
not exceed it. Re-read both the character's derived hit points and the synced
manifest projection after advancement.
For 2014 Wizard Spell Mastery, keep the selected level 1 and level 2 spell
prepared to use its lowest-level at-will casting; an upcast still spends a slot.
Replacing a mastered spell is a Play operation requiring 8 elapsed study hours.
Signature Spells are two level 3 spellbook spells, always prepared, with one
separate free level-3 cast each per Short or Long Rest; higher-level casting
always spends a slot.
When
several eligible party members advance together at the same downtime boundary,
the public regression driver may defer their individual snapshots and create one
verified aggregate party-advancement checkpoint after every actor passes this
complete audit. Follow the full
ordering and stop conditions in `references/CHAR_CREATION.md`.

## Combat boundary

Use `combat_preflight_attack` before every attack commit. The engine automatically
settles initiative, turn resources, canonical weapon attack data, attack nat-1/
nat-20, damage dice and typed trait ordering, temporary HP, concentration save
windows, healing, movement budget, and death saves. Surprise is an explicit scene
fact and follows the selected 2014/2024 ruleset. It does not infer missing map
geometry, line of sight, cover, hidden targets, or story consequences. It may
create an opportunity-attack window only from recorded positions, reach,
hostility, visibility, and movement mode.
Open a choice window for those decisions and resolve it explicitly; never encode
an unverified ruling as a character-card fact.

For a weapon with a recorded `ammunition_item_id`, attack preflight also requires
at least one unit in that exact linked ammunition stack. The commit consumes one
only after the declaration is otherwise valid; the final shot leaves the
auditable stack at quantity 0 so the weapon link remains valid. Do not substitute
another stack, delete the empty linked item, or roll before successful preflight.
An actor can always explicitly select `weapon_id: "unarmed-strike"`, including
while holding a weapon whose ammunition is exhausted. The Core attack is
proficient, uses Strength, has 5-foot reach, and deals `1 + Strength modifier`
bludgeoning damage. Do not require the actor to delete or unequip a weapon first.

A source-bound weapon can carry multiple unconditional typed damage parts. Let
the engine roll every recorded part and apply resistance, immunity, and
vulnerability per type as one hit; never collapse them into one type or manually
add the second part. A custom conditional rider stays bound to its exact card. On
first use, compile one persisted `content_solution` and resume the owned window
with `combat_choice(action="execute_plan")`; do not apply the hit again.

Multiattack is a distinct action choice. For a structured monster Multiattack,
pass `multiattack_option_id` on its first `combat_preflight_attack` and
`combat_resolve_attack`, then make only the exact weapon/mode/count sequence still
recorded by that option. Omit the id to choose one ordinary Attack. A descriptive
Multiattack without options remains an Agent-as-DM boundary only when selected and does not
block an ordinary weapon attack. Do not declare a raw `attacks_per_action`
override. Before any reviewed monster enters combat, inspect
its `agent_fill_requirements`. A complete ordinary weapon composition is a
standard engine transaction and must report `parser_authoritative=true` and
`default_resolver="engine"`. Agent fills are rejected for standard rulebook
cards. An open, conditional, or special-action composition is creature content,
not a new action-economy rule: keep its exact excerpt as a non-executable direct
Agent/DM ruling and attach no Multiattack mechanic reference. Other exact
creature-specific prose remains evidence on the unified actor/content card. Module-authored
and homebrew ordinary Multiattack composition can still use reviewed
`payload.agent_fill.multiattack_options`; unresolved mechanical riders use a
persisted `content_solution` at first live use. Cite the activity id and exact source
excerpt and use only existing parsed weapon ids, legal modes, and explicit
counts. When 2014 OCR recovery returns `requires_agent_fill=true` and `review=null`,
read the returned normalized text and requirements, then retry that recovery
with a fresh idempotency key and the fill; only the retry may create the
immutable review. For custom content, a parser-produced composition remains only a
candidate. If its exact procedure mixes a special activity or another
unsupported semantic, submit `resolution="agent_ruling"` with the exact excerpt
and reason instead of `options`; the actor remains usable and selecting that
action returns to Agent DM adjudication. When a managed module source assigns a complete
numeric weapon action outside the selected base statblock, use
`payload.agent_fill.additional_actions` with the action name, its exact managed
`source_ref`, exact excerpt, and Agent reason. The ordinary statblock parser must
derive the id, attack, damage, and on-hit text, and the MCP must revalidate the
evidence against the same source. A Multiattack may reference the new derived
weapon id in the same fill. Never use this path to provide arbitrary mechanical
fields or to copy an action from a different creature without source authority.
Do not encode that one creature as another parser
phrase exception or patch the actor sheet. A melee weapon with the Thrown property remains a
melee attack by default; pass `attack_mode: "ranged"` when it is actually thrown.
This distinction controls reach, range, disadvantage, and melee-only modifiers.
An automated encounter may prefer a valid Multiattack, but if that complete
option is illegal at the current range it must retry the same actor's legal
ordinary Attack choices before moving. A melee-only Multiattack never disables
one legal thrown or ranged weapon attack. Do not move into a known hazard merely
to preserve the preferred Multiattack.
On a positioned combat map, a ranged attack without a recorded normal range is
a missing-card/source boundary, not permission for a generic DM ruling to invent
distance. Repair the source-grounded card in lobby when the source states the
range. If the exact source instead replaces a numeric range with a complete
positional target restriction, such as "one target directly below the kobold",
preflight must surface that restriction as an Agent-as-DM ruling rather than a
missing-source blocker. Select that attack only when the current scene and
temporary-map positions satisfy the printed restriction; otherwise choose a legal
recorded mode such as melee or unarmed. If the source provides neither a range nor
a complete positional restriction, keep the attack blocked. Never invent a
generic distance or skip distance enforcement.

The bounded `recover_statblock` OCR grammar is 2014-only and must reject 2024
content. For a 2024 module or rule card, use complete edition-matching indexed
text with `content_kind="dnd5e_2024_statblock"` or
`review_mode="agent_text"`; otherwise an image-capable reviewer must transcribe
the exact managed page through the edition-matching visual review. Never use a
2014 normalization to make a 2024 card appear executable.

A reviewed monster action may replace an attack with a source-card activity,
but no creature gets a named runtime path, private state shape, or dedicated
facade fields. If the card has no plan, the DM Agent reads its exact evidence on
first selection, compiles a generic source-bound plan, and executes that plan
through the owned window. Knowledge transfer, possession, transformation, or
other semantics that the generic plan vocabulary cannot express remain an
explicit Agent/DM ruling plus ordinary public continuity and state operations;
the engine must not infer them from a monster name or prose fragment.

For a semantic `check.save`, record conditional-save classification in that
step's existing `args.source` object when authoring its plan. Include the exact
`source`, `source_ref`, and `source_excerpt` of one plan citation, plus
`save_source_kind` (`spell`, `magical_effect`, or `nonmagical_effect`),
`save_effect_conditions` (the conditions this save prevents), and strict boolean
`save_against_poison`. A spell card must use `spell`. The excerpt must occur in
that exact card's recorded effect text; another relevant citation elsewhere in
the plan is insufficient. Keep these values constant, with no slot/result
references. Runtime bindings select only the plan's declared variable inputs.
Interpret each save clause separately: damage type, the target's existing
conditions, or conditions applied by another step do not classify this save.
The engine uses the validated per-step facts for 2014 Dwarven Resilience, Fey
Ancestry, Gnome Cunning, and Brave, retaining the plan fingerprint and citations
in settlement. Legacy text-only sources carry no classification; a conditional
save requiring missing facts remains unresolved, not silently an ordinary roll.
Do not pass these engine-owned classification fields through generic
`rule_facts`, or bypass the source's action/resource payment with a generic check.

When an attack returns `status: pending_reaction`, no damage has been rolled or
applied. The target actor reads its owned window with
`combat_query(view="reactions")`, chooses a listed defense or `decline`, and calls
`combat_choice(action="resolve_defense")` with the returned choice id and current
campaign revision. Only that resolution spends the Reaction when used, updates AC
for the stored attack roll, and then resolves damage if the attack still hits.
Never roll damage early, manually patch HP, or use generic choice resolution for
this window. The same sequence applies when an opportunity attack opens a
post-hit defense. A custom card appears only after the DM Agent has compiled a
source-bound `attack.after_hit` plan with one static `attack.ac_bonus` primitive.
The result preserves the plan
fingerprint, source citations, Agent reason, Reaction payment, and card-use
payment. A source-bound `Shield` spell appears as a spell candidate only
when it is prepared/available, has a legal casting resource, and is legal under
the current turn's 2014/2024 spell limit. Select one of its returned
`cast_levels` by sending both its spell id and `cast_level`; the same transaction
spends the Reaction and slot, applies +5 AC to the triggering roll, and records
the +5 AC effect until the start of that caster's next turn. Do not model Shield
as an activity or add its AC manually.
An automated encounter loop must stop immediately when any attack returns this
window; it cannot call `combat_end_turn` first. Spend Shield at the lowest
offered slot only when its projected +5 AC changes the triggering attack from a
hit to a miss; otherwise decline. Against Magic Missile, use available Shield
because it blocks that target's darts rather than comparing an attack roll.

For a structured area spell, declare the complete map-derived target set and
cover. Include every combatant in the area that lacks the Dead condition,
including a Stable or Unconscious creature at 0 HP; do not use "active turn
available" as the target filter. The server rejects both omissions and additions.
Use the same complete `target_contexts=[{target_id,cover}]` contract for a
structured monster area activity such as Lightning Strike, a line breath
weapon, or Wing Attack. The Agent supplies the current cover degree; the engine
derives +2/+5 on Dexterity saves, excludes Total Cover, and rolls/applies the
standard save and damage. Never silently assume no cover.

A source-bound 2014 Core `Hypnotic Pattern` uses its own complete cube
declaration rather than generic area `target_contexts`. Call
`combat_cast_spell` with
`declaration={origin:{x,y},cube:{min:{x,y},max:{x,y}}}`. On a 5-foot grid the
inclusive bounds must be exactly 6 by 6 cells, the origin must lie on a cube
face and within 120 feet, and every living combatant must have a recorded
position. The Runtime enumerates the creatures, excludes a Blinded creature
from seeing the pattern, applies Charmed immunity, rolls every remaining
Wisdom save through the campaign random stream, pays the slot/action, starts
concentration, and records each failed target as Charmed, Incapacitated, and
speed 0. Damage ends that target's effect automatically. Ending the caster's
matching concentration ends every remaining dependent target effect. Another
adjacent creature wakes one affected target with
`combat_common_action(action="shake_hypnotic_pattern", target_id=...)`, which
spends its action. Never supply a hand-picked target list, roll the saves,
patch conditions/speed, or model these endings as Agent narrative rulings.

A source-bound 2014 Core `Sleep` uses
`declaration={origin:{x,y},target_contexts:[{target_id,cover}]}` in grid combat.
Declare every living creature in the 20-foot radius, including allies and
unconscious or immune creatures; the engine checks the point's 90-foot range
and applies eligibility itself. It rolls `5d8`, plus `2d8` for each slot above
1st, and spends the pool in ascending current HP order (temporary HP do not
count). Undead, Charmed-immune, already unconscious and source-bound magical
sleep-immune targets do not consume the pool. Sleep is not concentration and
lasts one minute on the persistent clock, including after combat ends. Positive
damage wakes the target even if temporary HP absorb it; zero damage does not.
Falling unconscious also applies Prone (unless immune). Waking ends Sleep, not
Prone: the creature must still stand up normally. For 2014 character updates,
the authoritative settlement also moves main-hand and off-hand objects (and
their contained items) into campaign `ground_items`, clears those hand slots,
and preserves external ownership/attunement references atomically. A shield in
the separate shield slot stays strapped on. Waking does not retrieve an object.
Positive-HP unconsciousness does not require a death save to end the turn.
Another adjacent creature can use
`combat_common_action(action="shake_sleep", target_id=...)` to spend an action
ending that target's Sleep effect, without removing unrelated unconsciousness.
For Agent-positioned combat, the Owner/DM supplies `declaration.spatial_facts`
instead of coordinates. Outside combat, use the same declaration inside
`character_action(action="cast_spell").payload`. Its exact fields are
`decision_id`, `reason`, `origin_description`, `campaign_revision`,
`origin_in_range=true`, `line_of_effect_clear=true`, `affected_target_ids`, and
`excluded_actor_ids`. The two ID lists must account for every living encounter
combatant (or campaign character outside combat) exactly once; put off-scene
creatures in the excluded list. Include the caster if in the area, and let the
engine exclude immune/unconscious creatures. The revision binds the judgment
to the current campaign snapshot. Missing facts return a ruling request before
payment; stale, malformed, or incomplete facts reject without state changes.
The engine owns dice, slot/action payment, target effects, and persistent time.
Never invent coordinates or override the spell's radius, range, or HP pool.

In Agent combat, the Owner/DM can shake a different sleeping target using
`combat_common_action(action="shake_sleep", target_id=...)` with
`payload.spatial_facts={decision_id,reason,campaign_revision,can_touch_target:true}`.
The DM judges physical contact, not a synthetic grid distance. The engine
requires a legal action payment and atomically ends that target's Sleep;
missing contact facts do not pay the action. Grid combat retains its adjacent
target check and does not accept this payload. A shake is not a free object
interaction, and retrying the same successful request must not pay again.

For 2014 ground custody in combat, use
`combat_common_action(action="drop_held", payload={})` to release held objects,
or `action="pickup_ground"` with `payload={ground_id,slot?}` to retrieve one
ground record. An optional slot must be an empty `main_hand` or `off_hand`.
Dropping costs no action; pickup spends the free object interaction if available,
otherwise an available main/extra action. Both require the conscious actor's
turn. Pickup in the same grid encounter checks the recorded drop position is
within 5 feet; it does not accept caller spatial overrides.

Outside combat, use `inventory_transfer(mode="character_to_ground" |
"ground_to_character")`. Its payload requires `campaign_id`, `character_id`,
`expected_campaign_revision`, and `expected_character_revision`; pickup also
requires `ground_id` and may supply `slot`. For Agent positioning, noncombat,
or a ground record from a previous encounter, the Owner/DM must supply pickup
`spatial_facts={decision_id,reason,campaign_revision,can_reach_ground_item:true}`.
The decision concerns the recorded drop location, not the source actor's current
location. Never synthesize coordinates. Missing facts request a ruling; stale,
malformed, false, or unauthorized facts do not move inventory or spend actions.
Character cards, ground records and payment share a revision-checked atomic
commit; replaying a successful request after restart must not repeat payment.
Common character/campaign settlements reject missing physical-item references
and conflicting attuned owners. Consequently, legacy removal/transfer paths
that cannot migrate the references are rejected, not silently repaired. These
guards do not yet establish complete cross-owner attunement lifecycle or
compatibility with every legacy inventory removal/transfer path; do not use
generic card replacement to repair or manufacture `inventory.external_items`.

A source-bound 2014 Core `Fly` is also engine-owned. Outside combat call
`character_action(action="cast_spell")` with equal explicit
`target_character_ids` and `willing_target_ids`; in combat call
`combat_cast_spell` with
`declaration={target_ids:[...],willing_target_ids:[...]}`. The Agent may decide
only the creature-owned fact that each target is willing. The Runtime enforces
touch range in combat, one target at 3rd level plus one per higher slot, the
60-foot flying speed, the 10-minute duration, the caster's single
concentration, and every target effect's dependency on that exact
concentration. Never patch a target's base speed or treat willingness as
permission to supply the spell's numeric rules. A combat map without an
elevation model records flying capability but must not invent an altitude or
falling damage.

A source-bound Core `Magic Missile` is the exception to generic spell
`pending_ruling`. Call `combat_cast_spell` with `target_allocations`, where every
entry supplies `target_id` and a positive `darts` count; the total must be three
at level 1 plus one per higher slot. The server validates current map distance and
recorded visibility. If a target can cast Shield, the cast returns
`pending_reaction` before any dart is rolled or damage applied. That target reads
its owned reaction and uses `combat_choice(action="resolve_defense")` with Shield
and one returned `cast_level`, or `decline`. The server settles all target choices,
then rolls and applies every unblocked dart as a separate force-damage instance;
each dart can cause its own concentration save or 0-HP failure. An already-active
Shield blocks that target's darts without spending another Reaction. Never roll
darts externally, combine them into `combat_hp_change`, or forge an attack-hit
window for this targeting trigger.

For a source-bound structured spell attack such as `Scorching Ray`, call
`combat_cast_spell` exactly once. That transaction pays the casting action and
spell resource once and returns `status="pending_resolution"`, a
`resolution_id`, and the authoritative attack count. Select each target only when
calling `combat_resolve_attack`; pass
`action.spell_resolution_id=<resolution_id>` and a freshly read campaign revision
once for every remaining attack. Never call `combat_cast_spell` per ray, substitute
a weapon id, roll damage externally, or patch HP. A hit can still open an owned
Shield reaction; resolve it normally before attempting the next attack. The
pending spell resolution blocks the caster's turn end and encounter end until its
remaining count reaches zero.

Declare 2014 Sneak Attack with `use_sneak_attack: true`; for a player Rogue the
engine checks the recorded feature, finesse/ranged weapon, advantage or adjacent
active enemy, disadvantage, once-per-turn token, and critical dice. A standard
monster statblock such as Spy instead uses its exact source trait and recorded
damage formula; do not add the player feature's weapon restriction when the
monster text omits it. The canonical 2014 and 2024 Fighter Second Wind base
cards are engine-owned: call `combat_use_activity` with the exact edition-bound
feature id. One atomic transaction consumes the card use and bonus action, rolls
`1d10 + Fighter level`, applies the clamped healing, and returns the roll,
before/after HP, applied amount, and Core receipt. The card itself supplies the
edition-specific use maximum and recovery schedule. Never roll it externally,
copy a 2014 resource onto a 2024 Fighter, or follow it with
`combat_hp_change`; that would double-settle the feature. Tactical Mind and
Tactical Shift are separate 2024 features and remain engine-implementation
gates until their check and movement transactions are available. For
levelled spell healing, pass `source_actor_id`, `spell_id`, and the
actual `spell_level`; this lets the engine validate the actor card and settle
source-linked features such as 2014 Life Domain's Disciple of Life. Never fold
that feature bonus into the base amount yourself. Halfling Lucky is
resolved automatically for attacks, checks, saves, death saves, and initiative;
retain its `rerolls` audit instead of rolling a second untracked check.
For 2024 Heroic Inspiration, immediately after a Play check call
`character_check(action="reroll")` with its exact `resolution_id`, one
`roll_index`, and `expected_original_roll`. It spends the single resource and
requires the replacement die. Never replay the whole check, reroll both
Advantage/Disadvantage dice, apply it to a death save, or keep the older result.
The canonical 2014 and 2024 Fighter Action Surge cards are engine-owned: call
`combat_use_activity` with the exact edition-bound feature id on the Fighter's turn. The same
transaction consumes one use and grants one `extra_action`; it returns
`committed`, not `pending_ruling`. Use the returned action normally. An unused
extra action expires at the next turn and Action Surge cannot be activated twice
on the same turn. Do not patch `turn_budget` or invent another Attack.

The canonical 2014 and 2024 Rogue Cunning Action cards are also engine-owned. Call
`combat_use_activity` with the exact edition-bound feature id and a declaration whose `action`
is `dash`, `disengage`, or `hide`. Dash spends the bonus action and adds the
actor's recorded Speed to remaining movement; Disengage spends it and records
the no-opportunity-attack turn flag. Hide spends the bonus action and records a
source-linked Hide declaration, but remains `pending_ruling`: the SagaSmith
Agent acting as DM decides whether the circumstances permit hiding and resolves
the Stealth/observer boundary by default. Never spend
a second main action for the same declaration, and never mark the actor Hidden
merely because the bonus action was paid.

Standard Orc `Aggressive` is engine-owned. Pay it with `combat_use_activity` and
`declaration={target_id: "..."}`, naming one recorded living, visible hostile
target, then spend that distinct movement grant with
`combat_movement(action="move", payload.movement_mode="aggressive")`; every
submitted path segment must move toward that target, while ordinary movement
remains a separate pool. Agent-positioned movement additionally records
`moves_toward_aggressive_target=true` in its spatial decision. Orc War Chief
`Battle Cry (1/Day)` is not engine-owned: its matching official source fragment
remains `catalog_only`. Keep it at the Agent/DM ruling boundary and do not mutate
the action budget, daily uses, attack advantage, or bonus-action availability
through `combat_use_activity` until an executable reviewed card and runtime
mechanic exist.

The canonical 2014 and 2024 Cleric Channel Divinity cards' `Turn Undead`
options are engine-owned. Call `combat_use_activity` with the exact
edition-bound activity id and declaration
`{option: "turn_undead", perception: [...], sear_undead?: true}`. The SagaSmith
Agent acting as DM must include exactly one perception entry for every living
Undead whose recorded battle-map position is within 30 feet:
`{target_id, can_see_or_hear, reason?}`. Use `reason` whenever an Undead is
excluded because it can neither see nor hear the cleric. Do not omit a hidden,
blinded, deafened, silenced, or obscured target instead of making that explicit
sensory ruling. The server derives the Cleric spell save DC, rolls every included
Wisdom save, spends the Magic Action and Channel Divinity, and atomically updates
all failed targets.

Under 2014, a failed target follows the Turned movement/action/reaction procedure;
damage or one minute ends it. Under 2024, a failed target gains Frightened and
Incapacitated, tries to move as far from the source as possible, and the effect
ends on target damage, source Incapacitation, source death, or one minute. The
runtime records that source-capability dependency rather than relying on Agent
memory. A source-bound level 5 2024 Cleric may set `sear_undead=true`; the engine
rolls one shared number of d8s equal to the Wisdom modifier (minimum one), applies
that Radiant damage only to failed-save targets, and deliberately does not end
the newly created Turn effect. Never roll saves or Sear damage separately, patch
conditions or reactions, or spend Channel Divinity before this call.

The 2024 card's other option, Divine Spark, is also engine-owned. Use
`{option:"divine_spark",target_id,mode:"heal"|"damage",
damage_type?:"necrotic"|"radiant"}`. Combat derives sight and 30-foot range from
the encounter; Play additionally requires the target's revision and explicit
Agent-as-DM `can_see=true` and `within_30_ft=true`. One transaction pays the
Magic Action/resource, rolls the Cleric-level d8 scaling plus Wisdom, and either
heals or rolls the target's Constitution save for full/half damage. The source
cannot target itself, and a failed preflight consumes nothing.

At the start of a current combatant's turn, treat `HP == 0`, that combatant's
`death_saves: true`, and the absence of Dead/Stable as the complete death-save
gate. Do not wait for or create a synthetic `Dying` condition. Confirm
`combat_query(view="available_actions", actor_id=...)` returns `death_save`, then
call `combat_check(kind="death_save")` with no `ability`, bonus, proficiency, DC,
or target. Resolve it before any other action or `combat_end_turn`. Refresh the
actor card and combat state immediately; a natural 20 can restore 1 HP and leave
the action available, three successes add Stable, and three failures add Dead.

To stabilize a dying creature with Medicine, call
`combat_check(kind="stabilize", ability="wisdom", target_id=...)`. The MCP
requires the current actor's turn, both creatures in the encounter, recorded map
positions within 5 feet, and a living target at exactly 0 HP. It derives the
fixed DC 10 and the actor card's complete Medicine modifier; do not pass a client
`bonus`, `proficient`, or alternate DC. The same atomic transaction spends the
main action. On success it resets the target's death-save tally and records
Stable while preserving 0 HP, Unconscious, and existing conditions such as
Prone; on failure it spends the action without changing the target. Do not patch
conditions or death saves by hand. Use Spare the Dying only when that exact
source spell is present and castable on the actor card; never grant it merely
because stabilization is needed.
If reaching the target requires movement, commit `combat_movement` first and
inspect all returned reaction windows. Resolve or explicitly decline every owned
opportunity attack before calling stabilization; a blocking reaction window must
never be skipped. Re-read both actors afterward because the rescuer can be
damaged or incapacitated before administering aid.

When an imported scene allows an action-bound social or investigative check,
pass the skill name as `ability` and the action payment in the same
`combat_check` transaction. For example, the Elfsong Tavern bribe is
`combat_check(kind="check", ability="persuasion", action="improvise", dc=15)`
(or `ability="deception"` when the offered reward is insufficient). The engine
derives the complete skill modifier from the actor card and spends the main
action whether the check succeeds or fails; do not pass `proficient` or `bonus`
for a named skill. Advantage from an offer of at least 10 gp is a verified scene
fact and may be supplied only when the actual offer meets that threshold.

If that check succeeds and the chosen NPC already has a canonical campaign actor
card, call `combat_join` with its explicit position, disposition, initiative (or
let the engine roll), and a `tie_breaker` whenever its initiative ties another
participant. The actor is stored under `reinforcements`, cannot act, be targeted,
or trigger reactions during the current round, and is inserted into initiative
only when the next round starts. When the exact source names a later round,
include that future `join_round`; the engine retains the actor outside combatants
until that boundary instead of approximating the delay with map distance. Do not
patch the combatant list, create a
mid-combat placeholder card, or queue the NPC after a failed check. Establish
potential participant cards during lobby/module preparation.
An automated Agent encounter driver may preselect a unique `tie_breaker` while
leaving `initiative` absent: this records the Agent's DM-owned ordering only if
the server roll ties, without replacing or predicting that roll. If a safe
pre-commit ruling is returned after a roll, keep the current revision; the
server discards that call's uncommitted random suffix so the Agent can supply
the ruling and replay the same roll through a new public call.

A Stable creature at 0 HP cannot take a short or long rest. If the established
scene permits waiting, use the public full-playthrough `recover-stable` path,
which commits `campaign_change(action="stable_recovery")` for the exact member
set. Give every occurrence a distinct `--occurrence-id`; keep both that id and
the exact request unchanged for a retry, and use a new id for a later recovery
even when actors and reason are identical. The engine rolls the source-required
`1d4` hours, restores exactly
1 HP, clears Stable and Unconscious, and keeps unrelated conditions such as
Prone. Never patch HP or choose the duration yourself. Once that actor is
conscious and above 0 HP, use the restricted
`character_state_change(action="stand")` to clear Prone; do not replace the
whole sheet or expose arbitrary condition deletion. Give each `stand-up`
occurrence a distinct `--occurrence-id` plus its audited reason and exact scene
evidence. An exact retry
must replay while a later stand by that actor in the same broad scene must not
reuse action, continuity, ActorKnowledge, or manifest-sync keys. `stand-up` may
defer its ordinary action-local checkpoint into the terminal scene checkpoint.

Before combat, call `module_query(view="preflight")` with source-grounded groups
for required combatants, reinforcements, and optional actors. Each group includes
canonical campaign actor ids, a same-module `source_scene_id`, and an exact
normalized `source_excerpt`. Required combatants must be in the initial participant
list; reinforcements must not be. Missing required actors and an invalid whole card
block the scene. A 0 HP/Dead actor remains a valid participant but receives
`can_take_turn=false`; unresolved executable rules disable only their affected
capabilities. Surface manual rulings without silently marking them resolved. Read
the returned `card_valid`, `hard_blockers`, `state_flags`, `can_take_turn`,
`disabled_capabilities`, `available_capabilities`, `settlement`, `manual_rulings`,
`normalization_notes`, structured `ruling_requirements`, `automatic_spell_ids`,
and `ruling_spell_ids`. A normalization note proves that
non-mechanical source text or page furniture was safely excluded and is audit-only;
it must not appear in `manual_rulings`, `ruling_requirements`, or group blocking.
Every ordinary DM adjudication requirement names
`default_resolver="agent"`; a player-owned choice or missing/conflicting source
names its distinct external boundary instead. Also inspect
`default_dm_resolver`, `agent_rulings`, and `external_source_gaps`;
`settlement="source_review_required"` identifies source-backed capabilities that
need repair; it is not by itself a whole-card or encounter blocker. Use
`card_valid`, `hard_blockers`, `disabled_capabilities`, and the scene-level
`ready` result as the authoritative gate. A card with a complete usable attack
may enter combat while unresolved spells remain explicitly disabled and audited;
repair is mandatory before using those disabled capabilities. `mixed` means the
card can enter combat with listed Agent adjudications still explicit.
Scene `ready` means all required actors exist and every whole card is valid; it
does not mean each actor can currently act or every capability is executable.
`unarmed_attack_id` remains available even when every recorded weapon is
unavailable. When an exact imported rule source contains the creature,
create it in lobby with `character_create_from(mode="statblock")`; never substitute
a similar creature when the named statblock is unavailable or unsupported. When
the exact card is visible only on a module PDF page, follow
`../../references/module-image-content-review.md` and use
`character_create_from(mode="module_statblock")` only after the reviewed record
validates. Never create or repair a required actor after combat begins.
For a standard rule card whose complete text exists but whose PDF columns defeat
automatic isolation, a text-only Agent may use only an exact same-page contiguous
segment through `rulebook_draft(action="edit", operation="statblock_review",
review_mode="agent_text")`. Supply every ordered evidence chunk, normalize the
full card, and let the MCP reject invented facts or omissions. Never use this
route when the indexed source itself is incomplete or conflicting, and never
describe it as visual review.
Do not reject or rewrite a source attack merely because its `Hit` clause has no
damage dice. Preserve an empty damage expression plus the exact source excerpt,
resolve the attack roll normally, and apply no fabricated HP damage. A hit that
returns `pending_on_hit_ruling_id` blocks the turn. Query the exact actor/card
with `content_solution`. If no plan exists, the DM Agent reads the bounded
source context, authors one generic plan, and compiles it against the current
actor revision in Lobby, Play, or Combat. Resume the same window with
`combat_choice(action="execute_plan")`; later occurrences reuse the fingerprint.
If a locked standard card has neither a registered engine mechanic nor a
persisted exact-source content clause and therefore reports
`semantic_solution.status="engine_implementation_required"`, stop before
payment and implement the missing generic standard mechanic in the engine; do
not send it through live custom-content compilation. A persisted clause may
cover only that exact card's authored outcome; engine action economy, payment,
rolls, damage, and timing remain authoritative.
`combat_choice(action="on_hit_ruling")` now only dismisses an exact-source
no-op after Agent review; it does not accept condition, save, damage, attachment,
or creature-specific selections. If the generic plan vocabulary cannot express
the reviewed outcome, leave the owned window pending and return to explicit
Agent/DM adjudication rather than adding a named monster rule. Ordinary escape,
save, damage, duration, and continuity work still uses the corresponding public
generic tools selected by the persisted plan or DM ruling.
If candidate validation
instead reports that this kind of action lacks supported Hit dice, stop in lobby,
repair and refresh the importer, and recreate the actor.
For the exact Invisibility spell, preserve unseen-attacker advantage on the
attack that reveals the actor, then end the effect after that attack resolves.
Casting any spell also ends Invisibility immediately; this applies to ordinary
and magic-item spell casts. Natural duration expiry, failed or replaced
concentration, and becoming Incapacitated also end the spell and remove its
Invisible condition. Paralyzed, Petrified, Stunned, and Unconscious all include
Incapacitated. Do not apply those termination rules to a different effect merely
because it also grants the Invisible condition.
If the printed card contains `Spellcasting`, a candidate warning that treats that
entry as a descriptive passive is a lobby blocker, not an optional DM boundary.
Review the cited page/chunks and repair or refresh the importer before continuing;
never recreate the spells by raw sheet edits. After actor creation, compare the
printed casting ability, every slot maximum, and the exact spell-name set with
the source, then verify that `derived.spellcasting.prepared_spell_ids` and the
source-bound spell cards contain the same executable list. A missing or extra
spell, empty slot map, or unresolved active-content binding blocks combat.
Also compare the normalized candidate's complete spell-name list with the
created card rather than trusting `review_ready`: a dropped named
`Spellcasting` trait or unresolved OCR spell name must surface
`incomplete_statblock_spell_hydration`. Repair and refresh the importer, then
create a fresh actor; do not keep or use the partial actor.
`required_count` is the complete group count established by the cited scene, a
recorded random-encounter roll, or an explicit branch-local DM composition fact.
It is never shorthand for `len(actor_ids)`. Prepare every required card in lobby
and keep preflight false while any required actor is missing. If the source names
other hostile groups, include them as combatant, reinforcement, or optional
groups, or first record the scene-supported reason they are not participants;
do not shorten the excerpt to conceal a printed count.
If the source places creatures outside the immediate fight and says they climb,
cross, arrive, or otherwise join only after combat begins, keep their canonical
actors in a `reinforcement` group. The full-playthrough encounter driver must
receive them as reinforcement reports with the exact entry excerpt, omit them
from initial `participant_ids`, and queue them through public `combat_join`.
They enter only at their queued round boundary and cannot be targeted or act
before then. If the source states an exact later round, pass
`--reinforcement-round`; otherwise the default is the next round. Never
approximate the delay by placing them on the initial temporary map.
Use `--reinforcement-hostile-report` for enemies and
`--reinforcement-ally-report` for source-authored helpers; neither kind becomes
a registered party member. If their arrival depends on semantic prose such as
"in danger of being overwhelmed", do not invent a numeric Core rule. Inspect
the live combat, let the Agent decide when that exact source condition is met,
and pass `--agent-reinforcement-trigger-json` with the exact excerpt, future
entry round, decision, and concrete observed facts. The driver records that
Agent ruling while `combat_join` remains the only settlement path.
When the statblock prints a complete numeric action for a known spell, its
creature-specific range, damage, and effect override the base spell for that actor.
After creation, verify that the spell card's displayed definition and structured
resolution agree; a mismatch is a lobby blocker because Agent narration and engine
settlement would otherwise contradict each other.
The excerpt is evidence, not a search hint: copy an exact normalized substring
from the expanded same-module scene or a verified `module_search` hit. Never
paraphrase, translate, or copy text from a different occurrence of the room key.
Preserve authored tactics before generic automation. If only some initial
hostiles hide, list exactly those actor ids; do not mark visible companions
hidden or include them in a shared Stealth roll. A shared roll is legal only
when the encounter text calls for one group roll and the selected actors have
identical Stealth profiles. If the source says an NPC casts a spell before
combat, call public noncombat `character_action(action="cast_spell")` before
`combat_start` so the slot and concentration are real, then cite the same source
when declaring its initial condition. For Core Fly, include the exact target
actor ids and the matching willing-target ids; do not replace them with an
Agent-written speed effect. If the NPC is present but joins the fray
in a later round, keep it on the initial map and explicitly delay its actions;
do not mislabel it as an off-map reinforcement. Bind every printed first-attack
choice to that actor and weapon. In 2014, the Invisibility spell grants the
attack's unseen-attacker benefit and ends after the attack is made, whether the
roll hits or misses; do not clear it before preflight or leave it active after
the attack.
If the source gives a special encounter procedure that is not a reusable Core
mechanic, keep it in the encounter driver as exact source evidence and transfer
its unresolved effect to the Agent adjudication boundary. For an abstract NPC
defender cohort, declare the printed initial count, the reviewed hostile
activity, casualty dice, recharge instruction, and exact excerpt. The driver
must call `combat_use_activity`, require its descriptive card to return
`pending_ruling`, roll all printed dice through the server, and store the
bounded/idempotent cohort state in the manifest; it must not redirect that
source activity onto PCs. For a printed minimum separation, declare the exact
distance excerpt and keep the hostile at that distance; an actor without a
legal ranged attack ends its action instead of walking into a source-prohibited
space. For a retreat triggered by cumulative server-settled damage or one
critical hit, use those exact source thresholds and check them on the retreating
actor's own turn. These are source procedures adjudicated by the Agent, not
permission to add a one-module mechanic to Core or fabricate a generic choice
window.
The same boundary applies when exact standard prose still depends on a
post-result choice or an unrecorded descriptive fact that the runtime cannot
settle atomically. For example, keep False Appearance as a descriptive passive,
and keep Legendary Resistance at the Agent boundary until a failed-save choice
window can replace the result and spend the daily use in one transaction.
Preserve the exact card and warning; do not mark either rule engine-owned merely
because its sentence can be parsed.
An actual defeat is also a valid simulated outcome, but it must not create a
caller-named success checkpoint. Preserve it only through an explicitly named
defeat snapshot or restore the last valid parent snapshot before trying a
source-supported branch such as conditional reinforcements.
Give each repeated procedure one stable `procedure_id` and require that id in
the action payload, Agent ruling receipt, and temporary-map world patch.
Round-by-round ritual/counter totals and ending checks must be reconstructed
from those receipts, not from narration or a client-only counter.
When a reviewed descriptive feature/activity or unstructured hydrated innate
spell is selected as the actor's turn, use the encounter driver's generic
Agent turn ruling rather than adding a creature-name or room-name branch. Bind
exactly one reviewed `feature_id`, `activity_id`, or `spell_id`, its exact
actor-card excerpt, the current scene's immutable `source_ref`, the exact
encounter tactic, and the Agent's decision and reason. The driver must pay the
real activity action, one generic `improvise` action, or the innate spell cast
before applying the ruling. A hydrated innate spell always uses `spell_id`, so
the runtime—not the Agent—pays its at-will or `N/day` access and starts or
replaces concentration from the spell card. Any printed save is rolled through
public `combat_check`; its success and failure meanings are recorded on the
temporary combat map. If a failed result directs a later attack, preserve that
target across process restart, consume it after the actual public attack, and
record any source-authored termination condition such as the originating actor
becoming incapacitated. Do not preselect a die result, silently skip the
printed tactic, pay the action twice, or convert the ruling into a reusable
Core mechanic.
For a source-bound statblock spell marked with components not repeated in its
reviewed card, have the Agent acting as DM confirm the components from the exact
source and pass
`component_ruling.source_components_confirmed=true` before casting. The engine
checks this before paying the action, slot, or concentration. Never spend first
and ask for the component ruling afterward.
If a hidden caster uses a spell with verbal, somatic, or source-unknown
components, include `component_ruling.casting_perception` before casting. It must
contain exactly one `{observer_id, perceived, reason?}` entry for every living
combatant that does not already know the caster's position; a negative ruling
requires `reason`. The Agent acting as DM owns this observer matrix. The MCP rejects an
incomplete matrix before spending the action or spell resource and then updates
per-observer visibility atomically with the cast. Do not leave `hidden=true`
unchanged after audible or visible casting. If visibility needs correction, use
`combat_map_patch` with a
`combatant_visibility` patch containing `actor_id`, `hidden` and/or
`visible_to_actor_ids`, and an explicit Agent-as-DM `reason`; never edit encounter state
outside MCP.

For a room such as `D13` nested inside a larger indexed location scene, call
`module_search("D13")` and verify that the first hit's last `heading_path` entry
is the exact room heading before using its content. Preserve that hit's chunk id,
scene id, and page range. Do not select another occurrence where `D13` merely
means a DC value or appears in unrelated prose.

End an encounter with a structured `combat_end.outcome`: `status` is one of
`victory`, `defeat`, `withdrawal`, `surrender`, `truce`, or `interrupted`, and `summary`
states the scene-supported reason and immediate public result. Do not close a
fight merely because the regression has enough samples. The engine rejects an
end while any death-save actor is still at 0 HP without Dead or Stable; settle
those actors first. Record longer consequences as post-combat events and memory,
not by hiding them inside the outcome summary.
For a source-authored retreat after any number of other hostiles are defeated,
pass the retreating actor plus the printed defeated-count threshold. Reaching
that threshold does not end combat or freeze the initiative: only the designated
actor attempts to leave on its own turn, and every other living hostile remains
in the encounter. Use a specific defeated actor trigger only when the source
names that exact creature, and record a later reinforcement only if the
retreating actor actually escaped.
When the full-playthrough runner chooses a party spell, restrict the choice to
the actor's current prepared list or genuinely known spells. A wizard's other
spellbook entries are not available. Use the lowest legal slot that still has
uses; if first-level slots are empty but a higher slot can cast the prepared
spell, pass that higher `cast_level` and apply its printed scaling (including
the additional magic-missile dart) instead of treating the caster as out of
spells.
After `combat_end`, `combat_query(view="status")` is a historical final encounter
record. Require `snapshot_role: "historical_final_encounter"` and
`combatant_state_is_current: false`, and read current HP, conditions, resources,
and recovery from `character_query`; never overwrite a recovered actor from the
historical combatant projection.

Encounter XP belongs to the actors who actually earned it. If an actor
participates and then dies, retain that actor's earned share; do not transfer it
to a replacement. A replacement or relief group earns only the enemies and
objectives it resolves. If equal division is fractional while the public XP
schema is integer-only, require an explicit audited rounding policy and record
any deterministic remainder recipients rather than silently dropping or
inventing XP.

Preserve the source spell card's canonical casting time during import. Standard
cards commonly use `1 action`, `1 bonus action`, or `1 reaction, ...`; do not
strip the leading count or replace these with a free-form timing label. In
combat, `combat_cast_spell` maps that card timing to the actor's matching action
budget. A bonus-action spell spends only the bonus action and leaves the main
action available, subject to the edition's same-turn spell restrictions; a
reaction spell still requires its owned pending reaction window.

Use `combat_common_action` for the core non-attack actions. It records their
action payment and tactical state; it deliberately does not fabricate the
outcome of a Hide, Search, or Help declaration. At encounter start, provide
DM-authored `participant_config` positions, disposition, reach, initiative, and
visibility (`hidden` and `visible_to_actor_ids`) when those facts are known. A
source condition caused by an ordinary removable object may end through
`combat_common_action(action="interact_object")` only after the Agent acting as
DM supplies the exact stored source reference/excerpt and a bounded
`agent_dm_adjudication`. The server consumes the object interaction, preserves
the main action, and removes only the matching owned condition. Never patch the
sheet, let a player self-declare the ruling, or add object/monster phrase
heuristics to the encounter driver. A
2014 surprise decision must compare every hiding creature's canonical Stealth
result against each opponent's passive Perception. Do not substitute the general
half-success group-check rule, and do not treat satisfying an adventure's
"careful/no light" prerequisite as guaranteed surprise unless the source says so.
When the encounter text explicitly states that a particular route surprises a
named participant, record that exact excerpt and set Surprise only for that
participant without fabricating a Stealth or scout roll. If the exact grant
belongs to an earlier scene, commit it through public `record-event` or
`record-outcome` and give that passed report to the encounter driver as
`--source-surprise-report`; enumerate only the actual surprised participants
with `--source-surprised-actor-id`. Do not relabel the current room's hostile
manifest as the source of Surprise.
An opponent that notices any threat is not surprised; record the comparison and
set `surprised` per participant. Hidden and surprised are separate facts. A
contextual observer feature such as Keen Smell changes passive Perception by
`+5` only when the Agent acting as DM confirms that the particular attempt can be perceived by
that sense; preserve that sensory ruling per observer instead of raising every
observer's passive score globally. For every d20 result, retain `roll_mode`,
`advantage_applied`, and `disadvantage_applied` with the raw `rolls` and rule
receipts. The effective roll-mode fields are authoritative: two raw d20 values
alone do not distinguish an applied mode from rerolls or other audited effects.
A current module scene produces a frozen temporary battle map. An encounter scene
may use `current_location_key` to reference exactly one spatial location in
another scene of the same module; persist that source scene as
`state.location_scene_id`, and preserve the current progress, encounter, and
spatial source ids as separate evidence. If the spatial evidence states no room
dimensions and the DM supplies no bounds, the temporary map uses a conservative
12-by-12-cell canvas; this is workspace, not inferred room geometry. The map
may render imported `spatial.connections` only when each edge is backed by
`confidence="explicit_text"` or `confidence="reviewed_image"` evidence. For a
PDF map, follow `../../references/module-visual-atlas.md`: render the managed
page, inspect the image, then persist the branch/snapshot-managed review through
`module_set_progress(spatial_review=...)`. Never
connect rooms by heading order, room number, or a generic cross-reference such
as an encounter's reinforcement note. An empty connection list is an explicit
unknown-topology state. The temporary map enforces only its explicit bounds and
blocked cells; it never turns
room prose into inferred walls, cover, line of sight, or terrain. Record a real door, hazard, or similar
post-combat world change through `combat_map_patch`, not by rewriting the module.
If indexed prose contains authored numbered areas that the Scene Atlas omits or
orders differently, stop play, repair the parser's shared heading normalization,
bump its revision, and refresh the module through the public import facade.
Verify that Markdown headings, unmarked numbered lines, and OCR-spliced display
headings yield one source-ordered location list before resuming.
If validation reports progress on a removed scene, let the Agent settle the
source/scene-fact remap. Accept an automatic remap only for one exact
chapter/title/page-range match; otherwise inspect and provide an explicit
old/new scene ruling. The activation transaction and manifest traversal must
consume that same mapping.
Player map views intentionally omit blocked cells, difficult terrain, DM
overrides, checksums, and world patches; do not disclose those fields from a DM
read or an earlier tool result.
A voluntary grid move cannot end in another living combatant's recorded space.
Set `participant_config.can_share_space=true` only when a source-bound trait
(for example, a swarm trait) or an explicit Agent-performed DM ruling permits it; preserve that
decision when the creature joins later. Passing through occupied spaces,
different creature sizes, and effect-specific forced/teleport overlap are not
inferred from endpoints. A forced or teleport destination that is already
occupied requires an explicit effect-specific ruling unless one participant has
the recorded sharing exception. A grid move that leaves an eligible
hostile's recorded reach opens an owned opportunity window; read it through
`combat_query(view="reactions")`, decline it with
`combat_choice(action="resolve")`, or settle it
atomically with `combat_reaction_attack`. Do not claim other map collision, terrain,
forced movement, line of sight, or a trigger not represented in encounter state.
Use `combat_movement(action="move")` with `payload.path` for bent grid routes.
Set `movement_mode` to `aggressive` only after the engine-owned Aggressive
activity has created that separate grant. Set it to `forced` or `teleport` when
the scene establishes an effect-driven position change. Those two modes can
move a combatant outside its turn, never spend its voluntary movement pool, and
do not provoke a normal opportunity attack. A teleport accepts only its
destination, never a traversed `payload.path`; do not encode terrain cost or
collision unless it is part of the supplied scene facts.
When the temporary battle map records `difficult_cells`, provide a cell-by-cell
`payload.path` for any move longer than one square. The engine charges one extra
foot per foot spent entering those reviewed cells and returns the reduced movement
budget with a Core receipt. Do not add that surcharge to `distance` yourself:
`distance` remains the geometric route length. For unmapped terrain, the Agent
performs the DM ruling from the exact scene and current state and records the
result through public map/state tools rather than inventing an unsupported cell
cost.
When a public event and per-actor ActorKnowledge identify exact hazard cells and
state that the actor will avoid them, pass that public report to the regression
driver and require its voluntary path search to exclude every known cell. An
endpoint that is safe is insufficient: audit the complete `payload.path`.
Use `event_type="movement_hazard_marked"` for a visible environmental hazard
that is not a trap; retain `trap_detected`/`trap_locations_shared` for actual
traps. The event summary and every actor-local proposition must name the exact
map cells, and every proposition must explicitly say the actor avoids them.
Knowledge is actor-local, so give hostile creatures a separate cited
`trap_locations_shared` event only when the module establishes that they know
the traps or an explicit Agent-performed DM review confirms that knowledge; never copy the
party's detection result to them. Do not apply this
voluntary avoidance to a shove, forced movement, or teleport, and do not invent
a hazard trigger when no submitted path or forced destination enters the
recorded cell.
If Prone, either use `combat_movement(action="stand")` (half speed, no action) or
use `combat_movement(action="move", payload.crawl=true)`; crawling costs double movement.

Every campaign, character, party, combat, rest, continuity, branch, snapshot,
scene-progress, and actor-knowledge mutation must carry the current optimistic
token exposed by that tool and a fresh `idempotency_key`. The token may be a
campaign/character revision, actor-knowledge revision ID, branch/head ID, history
sequence, or scene `state_version`; read the MCP contract for the exact field.
Re-read the relevant state after a conflict; never retry a changed payload under
the same key. Shared wallet and
inventory adjustments are campaign writes and follow the same contract.

Use `combat_use_activity` or `character_action(action="use_activity")` for cards in
`content.activities`, `features`, or `feats`. These tools pay a recorded use or
resource and the activation timing. They settle explicitly supported Core
mechanics such as Action Surge, Second Wind, Cunning Action, and Turn Undead;
other choices or prose outcomes return `pending_ruling`. Never treat a generic
`committed` payment as proof that unstructured prose was resolved.

Core Preserve Life is an exception with a complete deterministic contract. In
noncombat play, its `declaration.allocations` must contain every target's id,
current character revision, positive healing amount, and Agent-as-DM-confirmed
`within_30_ft: true`. Submit the whole allocation once. The MCP enforces the
five-times-Cleric-level pool and half-maximum-HP ceiling, then atomically spends
Channel Divinity and updates all target cards. Apply the Undead/Construct
exclusion only to the 2014 Life Domain card; the 2024 card has no such exclusion
and the engine must not import one from the older edition.
Never pay the activity first and heal targets through separate calls.

The 2024 Rogue Cunning Strike, Improved Cunning Strike, Devious Strikes, and the
Thief's Stealth Attack rider are currently explicit engine-implementation gates.
They require Sneak Attack dice reduction before the damage roll plus save,
condition, duration, movement, and post-hit windows. Do not approximate them by
editing the Sneak Attack result, calling a raw save afterward, or patching target
conditions; stop until the generic attack-rider transaction is implemented and
source-tested.

Reaction spells and activities require an owned pending reaction window. Do not
call them solely because it is another actor's turn. Do not hide a spell inside
the generic Ready payload. For a spell with an Action casting time, call
`combat_ready(action="ready_spell")`: it pays the action and spell slot or other casting resource
immediately, replaces any existing concentration, and arms one perceivable
trigger until the start of the caster's next turn. The SagaSmith Agent acting
as DM confirms that the
trigger occurred with `combat_ready(action="trigger_spell")`. The caster then uses
`combat_ready(action="resolve_spell")` to release the spell with its reaction or decline
that occurrence without spending the reaction; declining leaves the spell armed.
Losing concentration, reaching the caster's next turn, or ending combat makes the
held spell dissipate without effect. A released spell returns `pending_ruling`:
resolve its targets, attack, save, damage, area, and narrative consequences with
the appropriate combat tools and Agent-performed DM ruling rather than treating release as the
spell's complete effect.

Before a module can branch on opening hours, daylight, watches, or travel time,
remember that `state.game_time.elapsed_ticks` is the only advancing chronology
(one tick is six seconds); `state.world_time` is an optional calendar view. Anchor
that view with
`campaign_change(action="clock_set", payload={day, hour, minute, label})` and cite
the narrative/source assumption. After the DM establishes elapsed time, use
`campaign_change(action="clock_advance",
payload={period, count, expected_elapsed_ticks, expected_world_time?})`. Minute, hour,
day, and round advances update the same tick stream and settle all elapsed
round/minute/hour/day actor and campaign-space durations in the same mutation.
An encounter-only advance has no fixed real duration and affects only
encounter-bound lifecycle state. Never infer elapsed time from chat
pacing, and never set or advance the narrative clock during active combat. Once
set, do not jump the clock with another `clock_set`; use `clock_advance` so no
duration is skipped.

For every minute, hour, or day advance, include
the canonical `payload.expected_elapsed_ticks`. Re-read
`state.game_time.elapsed_ticks` and derive both `count` and the exact destination.
When a calendar is anchored, also include
`payload.expected_world_time={day,hour,minute,elapsed_minutes}` as a projection
guard; do not
manually reuse a travel-day
difference as an elapsed-day difference, because rest days and event timing
anchors can diverge. The server must reject a count that cannot reach the exact
target before advancing any clock or timed effect.

Treat every narrative-time interval as actual elapsed time, not as an effect-unit
selector. `60 minute`, `1 hour`, and two consecutive `30 minute` advances must
settle the same round- and one-hour actor/world effects exactly once. Short/Long
party rest, Stable recovery, completed out-of-combat casting (including the
ritual's added ten minutes), and spell-copying obey the same rule. Completed
combat and chase rounds use the same ticks; any ten completed rounds, even
across separate encounters, advance one minute and
settle elapsed effects for combatants, noncombatants, and world objects in the
same turn-ending transaction.
The service owns every subminute/sub-hour/sub-day remainder; never round a duration or patch
that bookkeeping into a card or manifest.

Resolve every completed Short or Long Rest through
`campaign_change(action="party_rest",
payload={rest_type, members, duration_minutes})`, even
for a one-character party. Each member supplies its current character revision
and only the choices valid for that rest type. A changed 2014 prepared list is
validated as part of the Long Rest; callers do not preallocate a light-activity
schedule. For a Short Rest, put Hit Dice, Arcane/Natural Recovery, Song of Rest,
attunement, and activity choices in the same member records. This one write advances
the campaign clock once, advances timed effects for every campaign actor and
world object, applies benefits to only the named members, records completion on
each card in canonical ticks, and for Long Rests enforces the
one-benefit-per-24-hours rule. An anchored calendar is not required. A creature must have at
least 1 HP at the start. Do not perform a separate per-character rest mutation
for either rest type or advance any rest duration separately before/after
`campaign_change(action="party_rest")`.

An effect on a room, object, scene, or the campaign belongs in structured
`campaign_change(action="effect_add")`, not an arbitrary `module_set_progress.state`
blob. Give it a stable id, target, duration, source, and `visibility` of
`public`, `party`, or `dm`; dismiss it with `effect_remove`. Player campaign
reads are audience projections, but never reuse a DM `campaign_query` result in
player narration or assume an exposure layer repairs already-read private data.
