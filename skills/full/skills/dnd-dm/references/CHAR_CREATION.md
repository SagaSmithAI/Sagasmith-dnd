# Character Creation and Advancement

Apply ability scores only through `character_ability_apply`; never write the six
ability fields or their derived modifiers directly. The supported creation paths
have distinct provenance:

- `method="manual"` plus all six `assignments` records explicit user-entered
  scores from 1 through 30. This supports physical dice, existing characters,
  and intentional overrides, but its empty `rolls` list must never be described
  as an engine roll.
- `method="standard_array"` and `method="point_buy"` require all six assignments
  and are validated against the selected edition.
- `method="roll_4d6_drop_lowest"` is two phase. First omit `assignments`; the MCP
  rolls and persists one immutable six-score set and returns `pending_choice`.
  Show those rolls to the player. Then re-read the character revision and call
  the same method with an assignment of every returned score exactly once. Never
  submit a `rolls` argument, reroll a pending set, or generate scores through
  `dnd_ability_roll` (which is an ordinary d20 check, not score generation).

For a new PC, bootstrap a validated default card first; do not guess a complete
sheet in the opaque `payload` object:

```json
{
  "mode": "build",
  "payload": {
    "campaign_id": "<campaign id>",
    "name": "<character name>",
    "summary": "<public one-paragraph description>"
  },
  "idempotency_key": "<stable build occurrence>"
}
```

Take the campaign actor from `result.instance` (not the library template). Apply
the six scores next with
`character_ability_apply(character_id, method, assignments,
expected_revision, idempotency_key)`; the `ruleset` is service-owned and must
not be sent. Then query the exact active `class`, `species`, and `background`
artifacts with `character_query(view="catalog")` and apply them through
`character_content_apply`, satisfying every returned selection requirement.
Apply the class first so its reviewed materializer owns level-one class
progression, HP/Hit Dice, proficiencies, and any structured spellcasting profile;
apply species and background next so their reviewed grants own traits,
background choices, wallet, and provenance. Then resolve every selected class
and background equipment entry to its exact active `item` artifact and apply it
through `character_content_apply`; never reconstruct a catalogued item in an
opaque inventory payload. Apply every returned level-one feature and required
spell artifact through the same catalog facade.

Do not prewrite or replace those rule-backed mechanical fields with
`character_sheet_replace`: that is a superseded parallel build path and can
silently conflict with catalog materialization. Use
`character_metadata_update` for the re-read `notes.profile` copy containing the
confirmed public description and background characteristics. Finally re-read
the actor and verify its class/species/background selections, abilities,
HP/Hit Dice, proficiencies, equipment, wallet, features, spells, and profile
before Play.

Do not pass a partially authored `sheet` or `notes` to `mode="build"`, do not
place `name` inside `sheet.identity`, and do not hand-author
`ability_generation`. A complete already validated current-schema sheet is a
direct/imported-card boundary, not a second bootstrap workflow. Use
`character_create_from(mode="direct")` for a direct NPC/monster instance,
`mode="template"` to copy an existing library template, and `mode="statblock"`
to create an NPC/monster from an exact imported rule source.

When the user supplies a character sheet, pregenerated-PC packet, or ability
option document, first call `character_query(view="document")` with the campaign
id and allowlisted source path. Use its classified fields and checksum as review
input; do not send it through `module_draft`. The inspection is not permission to
invent missing sheet fields: complete and confirm them before build. Its
`manual_input` contract preserves manual six-score entry alongside any extracted
arrays.

When preparing an imported module, first verify whether the supplied artifact
actually contains pregenerated PCs. When it does, import applicable, complete
pregenerated PCs before generating replacements, retain each actor's provenance,
and do not treat the printed recommendation or initial selected size as a fixed
runtime count. Require at least one active PC; the party may gain or lose members
during the campaign. If the artifact has no pregenerated PCs, do not attribute
invented characters to the module:
create player-confirmed or explicitly labeled regression PCs from the active
content catalog, and retain that provenance in `notes`. For a named module NPC or
monster, the module supplies identity, role, disposition, and scene-specific
possessions while an inspected rule source supplies the mechanical statblock.
Record both sources. Fixed treasure may be placed on the card during lobby setup;
dice-denominated treasure stays unresolved until the real roll occurs.
Statblock import accepts reviewed English 2014 SRD-style and 2024 SRD
5.2.1-style cards when the complete required core and executable numeric actions
are present. The parser, content kind, source, and campaign edition must match.
If the exact creature is absent, spell-only without numeric attack facts,
ambiguous, or otherwise unsupported, keep it unresolved instead of substituting
a similar creature. Bounded layout OCR recovery remains 2014-only; use complete
indexed text or capable visual review for 2024.

Do not equate a missing module party-size range with four PCs. After complete
text search and visual review prove that the module is silent, a regression may
continue only after the SagaSmith Agent acting as DM records that negative
finding and an exact enabled-rule fallback. Missing or conflicting text/image
evidence still requires a capable external source review. Keep the reviewed
count and rule checksum on the
party manifest and mark it as an Agent-as-DM decision, not a module
recommendation. Build
the reviewed number of seats only after this gate; continue to prefer every
applicable module pregen within those seats.

Do not equate one structured sample-background artifact with a rule that every
PC must use the same unmodified background. The 2014 Core rule **Customizing a
Background** permits a reviewed custom name, any two nonduplicate skills, and a
total of two tool proficiencies or languages available from the enabled sample
backgrounds. Apply the enabled base background through
`character_content_apply`, include `custom_name`, `skills`, the required
language/tool choices, and the inventory ids for the retained background
equipment package, and preserve the base artifact and selected skills in
`background_grants.choices`. Never use an inactive setting supplement merely to
make background names more varied.

Complete starting equipment is part of the lobby quality gate. Reconcile every
independent class equipment choice, its selected pack, ammunition quantities,
the complete retained background package, and the background wallet grant.
Record each item against the catalog item that defines it or, when a pack/package
has no item artifact, against the exact class/background artifact that grants
it. Also persist two personality traits, one ideal, one bond, and one flaw in
`notes.profile`. A valid AC and weapon card do not excuse missing packs, mundane
gear, or starting coin.

An important named NPC may have an authored identity but no combat statblock at
all. Create that identity only with
`character_create_from(mode="narrative_npc")`. Supply the active
module/scene/chunk/page/hash reference, an excerpt containing the exact actor
name, an authored role, and a concise summary. The service stores immutable
source evidence in notes and marks the card `narrative_only`, `source_bound`,
`combat_statblock=not_imported`, and `combat_eligible=false`. Re-read the card,
keep the current `play` exposure, add the actor to the playthrough manifest, and
checkpoint. The default sheet values on this identity card are schema sentinels,
not inferred module mechanics: never use the card in a check or combat, never
claim its AC/HP/abilities as authored, and never upgrade it into a combatant
without later importing an exact statblock.

Use this executable public request shape:

```json
{
  "mode": "narrative_npc",
  "payload": {
    "campaign_id": "<campaign id>",
    "name": "<exact printed name>",
    "role": "<authored narrative role>",
    "summary": "<concise source-grounded summary>",
    "source_ref": {"module_id": "...", "scene_id": "...", "chunk_id": "...", "page_start": 1, "page_end": 1, "heading_path": ["..."], "content_sha256": "..."},
    "source_excerpt": "<excerpt containing the exact name>"
  },
  "idempotency_key": "<stable occurrence key>"
}
```

Do not send separate `module_id`/`scene_id`, `occurrence_id`, caller-supplied
status `tags`, or an `expected_revision`; module identity belongs inside the
exact `source_ref`, and the top-level idempotency key is the stable occurrence
identity. For an anonymous counted instance, additionally send the exact
printed `source_identity`, a stable `instance_key`, and use the canonical name
`<source_identity> [<instance_key>]` unless a complete committed
`identity_agent_ruling` assigns a proper name.

When an active PC dies, disappears, or leaves, create any replacement through the
same public, source-audited character workflow. Reuse an applicable unused
pregenerated PC first; otherwise build a new legal actor from the active catalog.
The replacement needs a distinct actor id and must begin with empty independent
ActorKnowledge. Never clone the predecessor's card, identity, inventory, or
knowledge ledger. Keep the predecessor actor and its knowledge intact. After the
new card passes its full audit, return to `play` and use a source-cited joining
event at the current Scene Atlas location to grant only facts the living
participants explicitly tell the replacement.

Before creating any actor, read `character-schema-v2.md`. All PCs, NPCs, and
monsters require complete structured cards; NPCs and monsters require
`notes.profile.summary`. Do not persist an unconfirmed draft. After every creation
or advancement, call `character_query(view="get")`. Use returned `derived`
mechanics only for PCs and source-statblocked actors; a `narrative_only` actor is
not mechanically authoritative.

Recording a class, subclass, species, or subspecies name is not sufficient.
Before the first `play` phase and after every level-up, retrieve selection
artifacts from the campaign's effective Core and enabled Addon Packs and
reconcile every class/subclass feature whose `minimum_level` is met, plus every
species grant and required choice, through `character_content_apply`. Treat
`catalog_only` as a stop condition that needs reviewer/DM completion, never as an
applicable card. When importing a finished character whose printed scores and HP
already include species bonuses, pass `values_include_species_grants: true` while
applying the species so the catalog provenance and nonnumeric traits are retained
without double-counting numeric grants. When only the printed ability scores or
only HP already includes the grant, use the narrower
`ability_scores_include_species_grants` or
`hit_points_include_species_grants` flag and let the other value settle normally.

For a 2024 background, apply the background's ability increases rather than
putting them into species grants: choose [2,1] on two different listed
abilities or [1,1,1] on all three, never above 20. Supply its listed tool choice
and `equipment_package="A"` or `"B"`; the same public write materializes the
source package's exact item stacks and remaining GP (or 50 GP for B). Do not
pre-create approximate items and attach their ids; a missing inventory template
is a rule-pack defect. Acolyte and Sage also require the player's
Magic Initiate spellcasting ability, two source-list cantrip artifact ids, and
one source-list level-1 spell artifact id in `origin_feat_selection`; verify the
resulting feat, three spell cards, always-prepared level-1 spell, and free-cast
Long-Rest resource. Criminal and Soldier materialize their fixed Origin feat in
the same background write.

At advancement, do not apply a `* Subclass` marker feature; it is catalog-only,
and the selected subclass artifact is authoritative. Process every
`repeatable_selection_levels` entry separately. ASI and Epic Boon choose a feat
artifact with nested feat choices; Expertise selects only proficiencies already
owned and not already expert. For 2024 Warlocks, apply the level 1 invocation
grant before later grants. Each repeatable blast invocation identifies a
different known Warlock cantrip, while Lessons of the First Ones identifies a
different Origin feat and its required feat choices. Re-read the actor and
confirm the chosen invocation option cards—not merely the parent count—are
present with exact source refs.

Before combat, audit at least: class and subclass features, species/subspecies
features, background customization and characteristics, proficiencies and
expertise, complete class/background equipment and wallet, resources and
recovery periods, equipped weapons and ammunition, spellbook/known/prepared
spells, AC, HP, speed, senses, resistances, and every unresolved rule. Missing
feature or equipment cards are setup defects, not permissions for the DM agent
to improvise abilities during combat.

For an NPC or monster statblock with Multiattack, record its exact legal attack
compositions in one Action activity. Do not flatten it to a generic
`attacks_per_action` value: that would allow illegal substitutions. Each option
uses stable inventory weapon ids, an explicit `attack_mode`, and a count:

```json
{
  "id": "bandit-captain-multiattack",
  "name": "Multiattack",
  "source_key": "Bandit Captain",
  "activation": {"type": "action"},
  "choices": {
    "multiattack_options": [
      {
        "id": "melee",
        "attacks": [
          {"weapon_id": "scimitar", "attack_mode": "melee", "count": 2},
          {"weapon_id": "dagger", "attack_mode": "melee", "count": 1}
        ]
      },
      {
        "id": "ranged",
        "attacks": [
          {"weapon_id": "dagger", "attack_mode": "ranged", "count": 2}
        ]
      }
    ]
  }
}
```

After creation, verify `derived.multiattack_options` against the source and make
sure every referenced weapon id exists. Preserve the source document and rule
reference on the activity card just like any other imported mechanic.

An AC-changing defensive reaction must be compiled from exact evidence instead
of inferred from its name or description. Keep the imported activity's Reaction
activation and source text. On first use, the DM Agent persists a
`content_solution` whose plan has `trigger: "attack.after_hit"` and exactly one
contextual step:

```json
{
  "id": "defend",
  "op": "attack.ac_bonus",
  "args": {
    "bonus": 2,
    "attack_modes": ["melee"],
    "requires_visible_attacker": true,
    "requires_wielded_melee_weapon": true
  }
}
```

Put this step in the source-cited plan, not under `choices`. The compiled
solution records the plan/card fingerprints and Agent reason. Do not turn
reaction text into an always-on AC bonus. If any source prerequisite cannot be
represented as a verified field, leave the mechanic unresolved for the DM
instead of silently weakening it.

For a 2014 class-prepared caster, eligible level 1+ class spells use
`grant.method: "class_prepared"`, identify the recorded class with
`grant.source_key`, and may keep `access.known: false`. Put the complete daily
choice in `spellcasting.preparation.selected_spell_ids`; the selected spells become
derived prepared spells. A Wizard selection must additionally be in the spellbook.
Cantrips are known but never consume a prepared-list selection.

Advancement changes the live campaign instance and must use the audited lobby
workflow; never patch `progression.level`, HP, Hit Dice, slots, or preparation
limits by hand:

1. Read `campaign.settings.advancement.mode`. Campaign creation must explicitly
   select `milestone` or `xp`; configure a missing/changed mode only in `lobby`
   through `campaign_change(action="advancement_configure", payload={mode})`.
   In milestone mode, inspect the module trigger and retain its exact `source_ref`;
   do not also synthesize encounter XP. In XP mode, award the reviewed amount as
   soon as the encounter/objective is resolved with
   `campaign_change(action="experience_award", payload={awards:
   [{character_id, amount, expected_revision}], reason, source_ref})`, using the
   current campaign revision. The award atomically updates all named PCs, records
   branch-local evidence, and returns threshold status; it never auto-levels.
   Prepare a level-up event for the final `memory_change(action="commit")` that says why the
   level was earned.
2. End active combat, switch the campaign to `lobby`, read the actor's latest
   revision, verify that the milestone is earned or XP returns `eligible=true`,
   and call
   `character_state_change(action="level_advance", payload={class_name,
   hp_method, reason, source_ref})`. The current implementation advances an
   existing 2014 or 2024 single class by exactly one level. Multiclass
   advancement remains a stop condition, not permission to replace the sheet.
3. Use `hp_method="fixed"` for the class fixed value, or use `"rolled"` only with
   an explicit player/DM choice to roll; the engine performs and returns the Hit
   Die roll after all guards pass. Never provide a roll value. The transaction
   updates maximum HP, the Hit Die
   pool, spell-slot capacity, and preparation maximum. It does not heal existing
   damage. Newly gained slot capacity becomes available, but spent old slots do
   not refresh. Source-bound per-level modifiers such as Dwarven Toughness are
   resolved from already applied catalog provenance and included in the matching
   `hp_progression` entry. The same transaction materializes always-prepared
   subclass spells whose recorded minimum level has just become eligible; audit
   `advancement.subclass_spell_grants` and their exact subclass provenance.
4. Read `advancement.follow_up`. Apply every eligible base-class and already
   selected-subclass feature in the listed order through
   `character_content_apply`. If `subclass_options` is nonempty, obtain the
   player's choice, apply that subclass, query the content catalog again, and
   apply its newly eligible feature cards. A feature that names a shared resource
   must be applied after the feature that grants that resource. Satisfy every
   structured `selection_requirements.field` with the exact requested count:
   Bard Expertise chooses two existing skill proficiencies, while College of
   Lore Bonus Proficiencies chooses three currently untrained skills. Verify the
   resulting expertise/proficiency values; an empty feature choice is not a
   complete level-up. Cross-check every returned feature's `minimum_level`
   against its cited class or subclass text before applying it; an implausible
   early unlock is an import/compiler defect and a stop condition, not a bonus.
5. Resolve each reported cantrip, known-spell, or spellbook choice through the
   effective rule/content retrieval surface; apply only eligible artifact ids.
   A Wizard adds the reported spells with `method="spellbook"`. For a prepared
   caster, also apply each newly chosen class spell not yet present on the card
   with `method="class_prepared"`; these card-hydration selections do not consume
   a reported known/spellbook choice and must leave `access.prepared=false`.
   Do not change a prepared list during level advancement. Reconcile any
   revised complete list through the next completed Long Rest, excluding
   automatically always-prepared subclass spells from the caller-selected list
   and its maximum.
6. Re-read the actor and audit level, HP maximum/current HP, Hit Dice, spell slots,
   preparation, feature resources, subclass, spells, and `derived`. Level
   advancement raises maximum HP but does not heal current HP. A replacement
   built at level 1 and advanced to a higher source entry gate keeps its lawful
   current HP until a rest, spell, feature, potion, or other public healing path
   changes it; never patch it to the new maximum. Do not return to `play` while
   any required catalog item or player choice is missing.
7. After confirmation, use `memory_change(action="commit")` for the level-up event and
   post-level snapshot, then return to `play` and reopen the phase-appropriate
   exposure. During full campaign regression only, a contiguous group of party
   members advancing from the same source-cited scene or downtime boundary may
   defer each actor-local snapshot after the complete actor audit. Create and
   verify one aggregate party-advancement checkpoint immediately after the final
   actor, before entering another sourced scene; a standalone advancement still
   keeps its own checkpoint.

The trigger timing is part of correctness. If a module says the party reaches
level 2 after a tavern encounter, settle level 2 before entering the next
bathhouse/dungeon scene. If an audit discovers a late award, record it explicitly
as a retrospective correction; do not claim that the earlier scene used the
correct level or silently rewrite its rolls.
