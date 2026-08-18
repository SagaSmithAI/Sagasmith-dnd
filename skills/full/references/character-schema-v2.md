# D&D Character Schema v2

Full Runtime uses MCP tools. Use `character_create_from`, `character_query`,
`character_sheet_replace`, and the granular `inventory_change`,
`inventory_transfer`, `wallet_change`, `character_state_change`, and
`character_action` facades. Include `principal_id`, `expected_revision`, and `idempotency_key` on
retriable writes; `player_name` is descriptive and does not grant access.

Runtime mode stores every PC, NPC, and monster as a `Character` record with the
same validated documents. `character_type` is `pc`, `npc`, or `monster`; it does
not change the required sheet shape.

## Authority

- `sheet` is the authoritative mechanical state.
- `notes` is authoritative actor-authored narrative profile state: description,
  relationships, and goals. It never stores actor memory.
- Branch-local `ActorKnowledge` is authoritative for one actor's beliefs,
  observations, secrets, and misinformation.
- `derived` is returned by `character_query(view="get")`; it is calculated from the sheet and
  must never be edited directly.
- `campaign.state.party.inventory` is the authoritative shared wallet and stash.
- The campaign Rule Profile is authoritative for edition and locale. A
  campaign-bound sheet's `edition` is a service-owned projection; campaign
  settings must not copy either field.
- Every new sheet and notes document has `schema_version: 2`.

## Actor Cards

Every game entity that can take part in play is a complete `Character` record.
Do not keep a second, abbreviated combat card in campaign state or a prose note.

Content imported from an optional rule pack keeps only stable provenance and
execution references on the card: `pack_id`, `pack_version`, `rule_refs`, and
`mechanic_refs`. Runtime uses and actor-specific state remain on the character;
the executable mechanic definition remains in the MCP-owned immutable pack.

| Type | Required identity and narrative state | Mechanical expectations |
|---|---|---|
| PC | `player_name` when player-controlled; `notes.profile.summary` is required by this skill as the player-facing setting description | Full progression, abilities, skills, combat, traits, resources, spells, content, effects, and personal inventory. |
| NPC | `notes.profile.summary` is required; record lasting conversation outcomes in ActorKnowledge and objective outcomes in CampaignMemory | Populate every value that can affect a check, save, combat, spell, resource, item, or effect when an exact statblock exists. A source-bound `narrative_only` identity card may retain schema defaults, but those sentinels are never mechanically authoritative and the actor is ineligible for checks or combat. |
| Monster | `notes.profile.summary` is required and describes appearance/behavior | The same full sheet, including CR-relevant combat values, defenses, senses, movement, actions in `content.activities`, limited uses, spells, equipment, effects, and loot. |

The public character library may hold reusable PC, NPC, and monster templates.
Any type may instead be created directly as a campaign instance. For player
character creation, prefer `character_create_from(mode="build")`: it atomically creates the public
template and an independent instance in the selected campaign. All live actors in
the same encounter must be read with `character_query(view="get")` and must use the campaign's
edition and rules profile.
Defaults are only placeholders while information is unknown. Once a rule source or
published stat block supplies a value, write the structured value rather than
leaving an inferred default.

For an important named module NPC with no published statblock, use
`character_create_from(mode="narrative_npc")`. Its exact source evidence lives in
notes and the card carries `narrative_only`/`source_bound` tags plus an explicit
`combat_eligible=false` response. It may own relationships, goals, and
ActorKnowledge, but no caller may treat its placeholder AC, HP, abilities, speed,
or other derived mechanics as authored.
The required payload fields are exactly `campaign_id`, `name`, `role`, `summary`,
`source_ref`, and `source_excerpt`; creation also requires a top-level stable
`idempotency_key`. Optional anonymous-instance fields are `source_identity`,
`instance_key`, and `identity_agent_ruling`. Do not send `occurrence_id`, status
tags, or duplicate module/scene ids beside `source_ref`.
For repeated anonymous source identities, keep one card and knowledge scope per
instance. Supply the exact printed `source_identity` plus a stable
`instance_key`; the default display name is
`<source_identity> [<instance_key>]` and the card is tagged
`anonymous_source_instance`. A settled Agent DM naming decision may instead
bind a proper display name to exactly that source identity and instance key.
Such a card also carries `agent_named_source_instance`; its name is narrative
identity only and does not authorize any mechanical fields.

Use only the public MCP creation facade:

- `character_create_from(mode="direct")` creates a reviewed public template or
  campaign instance from a complete v2 sheet and notes payload.
- `character_query(view="library")` lists reusable PC/NPC/monster templates.
- `character_create_from(mode="template")` creates a fresh campaign instance
  from a selected template.
- `character_create_from(mode="build")` is the preferred PC flow: one
  transaction creates the public template and initial campaign instance.

Instances retain `template_id` for provenance but are independent copies. Gameplay
mutations and snapshots apply to the campaign instance only; they never alter or
restore the public library template.

## Sheet

`sheet` contains these required top-level blocks:

```json
{
  "schema_version": 2,
  "edition": "2014",
  "identity": {},
  "progression": {},
  "ability_generation": {},
  "abilities": {},
  "skills": {},
  "combat": {},
  "traits": {},
  "resources": {},
  "spellcasting": {},
  "content": {},
  "conditions": [],
  "effects": [],
  "adventure_state": {},
  "inventory": {}
}
```

`progression` records level, XP, classes, subclass, hit die, background, and
species, including `background_grants` for the background feature, starting item
IDs, language/tool grants, and selection choices. `identity` records gender, age,
height, weight, faith/deity, and visible features. Do not place an arbitrary
portrait URI in the mechanical sheet. An imported package actor may instead have
`notes.profile.portrait_ref`, containing the exact package asset key, SHA-256,
image media type, alt text, and package id/version/checksum. The MCP resolves only
its managed checksum-bound bytes; callers never supply a filesystem or network
path for runtime rendering.
`combat` records current/max/temp HP, AC, initiative, all movement modes, hit
dice, `hp_progression` gains by level (`fixed|rolled|manual`), death saves,
exhaustion, inspiration, and an explicit wounded flag.
`traits.senses` always includes darkvision, blindsight, tremorsense, truesight,
and a passive-perception modifier. `resources` is a named pool with `value`,
`max`, `recovers_on`, and `source_key`.

## Skill Table

`sheet.skills` always contains the complete table below. Each entry is
`{ "proficiency": "none|half|proficient|expertise", "bonus": <integer> }`.
Set the proficiency from class, background, species, feats, and expertise; use
`bonus` only for a persistent additional modifier. Do not omit untrained skills.

| Ability | Skills |
|---|---|
| Strength | Athletics |
| Dexterity | Acrobatics, Sleight of Hand, Stealth |
| Intelligence | Arcana, History, Investigation, Nature, Religion |
| Wisdom | Animal Handling, Insight, Medicine, Perception, Survival |
| Charisma | Deception, Intimidation, Performance, Persuasion |

## Ability Generation

`ability_generation` records the pre-species/pre-advancement assignment that
created the card. The default `method: "unrecorded"` is only for existing cards;
new 2014 car creation must use one of these code-validated methods:

| Method | Runtime enforcement |
|---|---|
| `manual` | Preserves all six explicitly entered scores (1-30) without claiming that the engine rolled them. This option must remain available to players. |
| `standard_array` | Uses exactly `15, 14, 13, 12, 10, 8` once each. |
| `point_buy` | Uses scores 8-15 and spends exactly 27 points using the 2014 cost table. |
| `roll_4d6_drop_lowest` | Requires six recorded 4d6 pools, each with its lowest die dropped, and assigns every resulting score once. |

For an existing campaign actor, use `character_ability_apply` with the current
character revision and an idempotency key. For `roll_4d6_drop_lowest`, first
call it without assignments so the server rolls and records all six pools, then
call it again with the player's complete assignment. Caller-supplied dice are
never accepted. `dnd_ability_roll` is available when a separate campaign-bound
server roll is required. For a new PC, first call
`character_create_from(mode="build")` with only `campaign_id`, `name`, and the
optional public `summary`, take `result.instance`, and then call
`character_ability_apply` on that campaign actor. Do not put caller-authored
`ruleset` or `rolls` fields into `ability_generation`; the service records them.

The recorded assignments are the creation baseline. Later species adjustments,
ASI/feat choices, magic, and other legal changes may make the current
`abilities.*.score` different; do not rewrite the creation record to hide them.

## Spells And Limited Features

`content.spells` records a spell's source, level, grant, and access flags:
`known`, `prepared`, `always_prepared`, `in_spellbook`, `ritual_available`, and
`at_will`. Its structured `definition` records school, casting time, range,
duration, concentration, V/S/M components, material cost/consumption, and concise
rule effect. `point_cost` supports spell-point variants. The authoritative daily choice is
`spellcasting.preparation.selected_spell_ids`.

- In 2024, bards, clerics, druids, paladins, rangers, sorcerers, warlocks, and
  wizards use a level 1+ prepared list with the class-table limit.
- In 2014, clerics, druids, paladins, and wizards use prepared lists; bards,
  rangers, sorcerers, and warlocks use `mode: "known"`.
- A `class_prepared` spell is an eligible class-list spell, not a known spell. It
  may keep `access.known: false`; when its id is selected, derived state marks it
  prepared. Its `grant.source_key` must name a class recorded on the card.
- Wizards use spellbook membership separately from daily preparation; a prepared
  Wizard spell must be in that character's spellbook.
- Always-prepared spells are returned as prepared without consuming a selection.
- Cantrips are known and never consume a level 1+ prepared-spell selection.
- On multiclass cards, each class-granted spell records its class in
  `grant.source_key`; preparation limits and eligible spell level use that
  class's level, not the combined multiclass slot table.
- `spellcasting.casting_economy` is `slots` or `spell_points`; spell-point
  casting requires the structured `spellcasting.spell_points` resource.
- `content.features`, `content.feats`, and `content.activities` use the same
  source/description/uses/choices shape plus `resource_key`, `activation`, and
  level `scaling` for limited class, racial, item, and feat capabilities. Record
  Action Surge, Rage, Channel Divinity, and comparable features here, not in prose.
- A statblock Multiattack is an Action activity whose
  `choices.multiattack_options` list contains stable option ids and exact
  `{weapon_id, attack_mode, count}` entries. Every weapon id must resolve to an
  inventory attack. Keep alternate melee/ranged compositions as separate options;
  never replace their constraints with a generic extra-attack count.
- A deterministic AC reaction uses
  `choices.reaction_defense = {kind: "armor_class_bonus", bonus,
  attack_modes, requires_visible_attacker, requires_wielded_melee_weapon}` on a
  Reaction activity. This is a conditional post-hit mechanic, never a permanent
  AC modifier. Unstructured reaction prose remains an Agent-performed DM ruling
  by default.
- `content.selections` records structural catalog choices that are represented
  elsewhere on the sheet, such as background and subclass. Each entry retains
  `artifact_id`, kind, name, exact pack id/version, rule/mechanic references,
  and the explicit selection payload. Artifact ids are unique in this list.

## Inventory And Wallet

`inventory.wallet` is `{ "cp": 0, "sp": 0, "ep": 0, "gp": 0, "pp": 0 }`.
Balances are non-negative integers. Gems, trade bars, keys, and unusual currency
are items, not wallet balances.

Each item has a stable `id`, `name`, `kind`, `quantity`, `weight_oz`, `price_cp`,
short `description`, `source_key`, container/equipment state, identification,
attunement, condition, uses, charges, and type-specific `mechanics`.

A found `kind: "spellbook"` item records `mechanics.edition`, resolved
`spell_ids`, preserved `unresolved_spell_names`, `owner_mark`,
`source_scene_id`, and `copyable`. `deciphered` is informational: 2014 copying
from another Wizard's notation performs deciphering inside the paid/timed copy
process. The item is the source artifact; finding it does not mutate any
character's `spellcasting.spellbook.spell_ids`.

For every item that exists in play, this skill requires a nonempty `name` and
short `description`; use `source_key` whenever it comes from a rule source. A
plain key, gem, trade bar, quest object, or monster drop is still an item with
an ID, quantity, ownership, and description, not prose hidden in an event.

Equipment slots are `armor`, `shield`, `main_hand`, `off_hand`, `head`, `neck`,
`cloak`, `gloves`, `boots`, `ring_1`, `ring_2`, `shoulders`, `back`, `chest`,
`wrists`, `waist`, and `legs`. The slot map and each item's
`equipped` / `equipped_slot` fields must agree. Use
`inventory_change(action="equip"|"unequip")`; never set those fields through an
inventory patch.

Armor and shields have strict mechanics:

```json
{
  "armor": {
    "base_ac": 14,
    "dexterity_mode": "max",
    "dexterity_max": 2,
    "magic_bonus": 0,
    "stealth_disadvantage": true
  },
  "shield": { "ac_bonus": 2, "magic_bonus": 0 },
  "magic_item": { "ac_bonus": 1 }
}
```

Armor uses `dexterity_mode: "none"`, `"full"`, or `"max"`; `dexterity_max` is
required only for `"max"`. Set `stealth_disadvantage` from the exact armor table;
when such armor is equipped the runtime automatically rolls Dexterity (Stealth)
checks with disadvantage in play and combat. Do not also pass a client-authored
disadvantage for the same armor source. Armor may only occupy `armor`, shields
only `shield`, and rings must be `magic_item` records in a ring slot.

`derived.armor_class` is calculated in this order: explicit `combat.ac.override`;
otherwise armor or `combat.ac.base`; then shield, equipped magic-item AC bonuses,
and supported active effects. `derived.armor_class_breakdown` explains every
applied source. A supported effect change uses
`{ "path": "derived.armor_class", "mode": "add|override", "value": <integer> }`.
Other effect changes remain in `derived.unresolved_rules` for Agent-performed
DM adjudication.

Actor effects remain in `sheet.effects`. Effects attached to a room, object,
scene, or the campaign instead live in `campaign.state.world_effects` and are
written through `campaign_change(effect_add/effect_remove)`. Their visibility is
`public`, `party`, or `dm`; their minute/hour/day/round/encounter duration is
advanced by the same atomic campaign time paths as actor effects.
`created_at_elapsed_ticks` binds creation to the one six-second tick stream;
there is no second minute-based creation clock.

Containers are ordinary `kind: "container"` items. Items reference their parent
with `container_id`; containers cannot form cycles. Container mechanics record
`capacity_oz`, `weightless_contents`, and `extra_dimensional`, and the runtime
rejects direct contents that exceed capacity. Inventory encumbrance records
`mode: "standard|variant"` and currency-weight handling. `derived.inventory`
returns carried weight, 5e load thresholds/state, and weapon attack cards.

Weapon mechanics are structured, not free prose: category, melee/ranged attack
type, selected attack ability, damage formula/type, versatile formula, properties,
normal/long and thrown ranges, proficiency, magic bonus, and an optional linked
ammunition item ID. `derived.inventory.weapon_attacks` is the authoritative card
view for attack bonus and damage expression.

## Effects And Narrative

Effects record source, optional `source_spell_id`, active state, `concentration`,
a declared duration period/remaining count, structured changes, and a short
description. Every elapsed-time mutation advances round durations by its exact
tick count and minute/hour/day durations at the boundaries it crosses. Hour/day
durations may carry the service-managed
`elapsed_minutes_remainder` needed to accumulate actual elapsed time across
smaller clock advances. It must be non-negative and smaller than the duration
unit; clients must not author, round, or patch it. The runtime permits at most one
active concentration effect. It lists
effect changes it cannot derive automatically in `derived.unresolved_rules`; the
The SagaSmith Agent acting as DM must read the rules before narrating their
result.
When the exact Invisibility spell effect ends—by attack, spell cast, duration,
failed/replaced concentration, or Incapacitation—the runtime also removes the
condition it granted. Incapacitated, Paralyzed, Petrified, Stunned,
Unconscious, and death end every active concentration effect.

`adventure_state` records actor-scoped reputation, contributions, blessings,
wards, legendary boons, and durable status tags. Do not hide these campaign-facing
states in `dm_notes`.

`notes.profile.summary` is the one-paragraph public description. NPCs and monsters
both require it; a monster summary is its concise appearance/behavior description.
Character-card notes do not contain actor memory. New dialogue outcomes go to
ActorKnowledge when they describe one actor's belief or knowledge, and to
CampaignMemory when they are objective world facts. Do not store every line of
dialogue as memory.
`notes.profile.backstory` holds the longer character history; it complements, but
does not replace, the compact public `summary` and `appearance`.

Use `character_sheet_replace` only for a reviewed complete draft or a
deliberate full-sheet change. Never hand-edit one inventory entry, wallet balance,
prepared spell or effect through a raw sheet replacement during play. Persist
accepted subjective entries with `actor_knowledge_change` or the
`actor_knowledge` member of `memory_change(action="commit")`.
