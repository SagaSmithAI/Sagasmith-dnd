# D&D Content Catalog Contract

The Content Catalog is the common character-option surface for bundled Core
rules and source-bound extension packs. It separates three facts that must not
be conflated:

1. **Catalogued**: a source-linked record is searchable and has a stable id.
2. **Available**: its core edition is locked by the campaign, or its extension
   pack is enabled on the current branch.
3. **Executable**: a reviewed rule mechanic covers the requested outcome.

For the bundled 2014 SRD, `dnd5e.content.srd2014@1.4.0` is installed during MCP
startup when the full D&D skill repository is configured. Its records retain a
`bundled:srd2014/...` reference to the original Markdown file. Optional books
must use the source-bound `rulebook_draft` editing/finalization loop; every
artifact supplies imported `source_chunk_ids`, which the MCP resolves to the
exact document chunk/page citations before the Pack can be finalized.

The bundled 2024 catalog is a separate
`dnd5e.content.srd2024@1.0.0` pack compiled only from SRD 5.2.1 Markdown. It
contains source-bound classes, subclasses, species, backgrounds, feats, spells,
equipment, magic items, monsters, and per-level features. Every record uses a
`bundled:srd2024/...` reference; a shared engine function never authorizes a
2024 card to borrow a 2014 artifact id, formula, recovery schedule, or citation.
The 2024 class cards materialize their source-defined shared and local resources
(including Rage, Bardic Inspiration, Wild Shape, Second Wind, Channel Divinity,
Focus Points, Lay On Hands, Favored Enemy, Sorcery Points, and limited-use class
features) with class-level or ability-modifier scaling. The Sorcerer Metamagic
feature exposes all ten SRD 5.2.1 options and its level 2/10/17 selection counts;
each option remains searchable as bounded source context. A structured counter
does not by itself claim that the feature's semantic effect is engine-settled:
cards without an exact mechanic retain `ruling_requirements` on the actor card
and default those source-bound decisions to the Agent.

The 2024 equipment catalog covers weapons, armor, tools and tool variants,
adventuring gear, ammunition, mounts, tack, and large vehicles. Mundane item
records carry actor-schema inventory templates (including stack quantity,
per-unit weight, price, and weapon/armor mechanics); background package writes
consume those templates instead of reconstructing items from names.

The pack also records advancement choices that are easy to lose when a table is
flattened into prose: repeated ASI/feat grants, Bard and Rogue Expertise,
Mystic Arcanum at levels 11/13/15/17, Wizard Spell Mastery and Signature
Spells, Circle of the Land spell sets, and every level-gated Eldritch
Invocation gain. Subclass-marker feature headings are `catalog_only`; choose
the actual subclass artifact instead of applying a second empty marker card.

## Agent procedure

1. Read `campaign_rules(action="get_profile")` and the current branch state.
2. Use `content_pack(action="list"|"get", kind="core_rules"|"addon")` to
   inspect only the effective Core and enabled Addon Pack versions, then select
   their exact catalog artifact id.
3. Present only returned options and their source references to the player. Read
   `selection_requirements` for spell eligibility, subclass class/level,
   species grants, class/subclass feature level, background choices, and feat
   prerequisites; do not infer these from names.
4. For a supported card target, call `character_content_apply` with the
   character's latest revision, an idempotency key, and every required
   `selection` value. A spell selection identifies its `source_class` and grant
   `method`; a subclass identifies its target class on multiclass cards; a
   2014 background supplies its required language choices. A 2024 background
   supplies `ability_score_increases`, any tool choice, the exact
   `equipment_package="A"|"B"`, and, for Magic Initiate,
   `origin_feat_selection` with two cantrip ids, one level-1 spell id, and its
   spellcasting ability. That one transaction applies the background increase,
   skills/tools, the source package's exact item quantities and wallet grant,
   Origin feat, feat spells, and free Long-Rest casting resource.
   A species supplies every listed language, skill, tool, ability, and cantrip
   choice. If an imported
   finished sheet already includes all numeric species bonuses, set
   `values_include_species_grants: true` explicitly so provenance and traits are
   linked without adding the bonuses twice. If only part is already included,
   use `ability_scores_include_species_grants` and
   `hit_points_include_species_grants` separately; for example, a printed Hill
   Dwarf card may already include ability increases but still be missing the
   per-level HP grant.
   A 2024 ASI or Epic Boon feature uses
   `feat_choice={artifact_id, selection}`. `Skilled` records three
   `{kind: skill|tool, name}` choices. Apply Eldritch Invocations at each listed
   `grant_level`; repeatable blast invocations also identify their known
   Warlock cantrip `target_artifact_id`, while Lessons of the First Ones targets
   an Origin feat and carries that feat's `option_selection`.
5. If the response is `pending_ruling`, distinguish a player-owned build choice
   from a DM adjudication. Obtain the former from the player; the SagaSmith Agent
   resolves the latter by default from the exact rule/source and current actor
   state. Do not bypass either result by editing raw sheets.

An imported extension is not automatically enabled, and import is not a
mechanics claim. The DM selects its exact Pack version per branch. Snapshots
then retain that version/checksum lock for replay and audit.

Each bundled catalog is built from leaf records, not index pages: individual
spells, twelve classes, twelve subclass sections, species cards,
source-linked class/subclass feature sections, backgrounds, feats, and
structured equipment rows. A bare base-class name or prose card remains
`catalog_only`; only a card with the reviewed `class_definition` needed for the
level-one materializer is `selection_ready`. Applying that class card still does
not apply its separate level-one feature and equipment selections. Complex
species such as Dragonborn also remain `catalog_only`
until every ancestry-dependent grant is structured. Spell catalog cards retain
class eligibility, but a character card records only the class actually chosen
in `grant.source_key`.

## Character completeness gate

Before play or combat, compare every recorded class level and subclass against
the catalog and apply every feature available at that level. Then compare the
selected species/subspecies against its structured grants. A card is incomplete
if it records only `progression.classes`, `progression.species`, or a subclass
name while the corresponding `content.features`, proficiencies, traits,
resources, spell grants, or required choices are absent.

The engine recognizes source-linked feature cards, not prose guesses. For 2014 or 2024
Rogue Sneak Attack, pass `use_sneak_attack: true` to the attack declaration;
the engine validates finesse/ranged weapon eligibility, advantage or an active
enemy adjacent to the target, disadvantage, once-per-turn use, and critical
dice. The base 2014 or 2024 Fighter Second Wind activation is a single
engine-owned `combat_use_activity` call:
it pays the bonus action and use, rolls `1d10 + fighter level`, applies healing,
and returns one Core-audited result. Do not roll or heal again. For spell healing, supply the source actor,
recorded spell id, and actual slot level separately; the engine adds a recorded
Disciple of Life modifier and preserves it in the healing receipt. Halfling Lucky
rerolls are automatic and appear in the roll's `rerolls` audit field.
By contrast, 2024 Heroic Inspiration is a player choice immediately after one
recorded roll: call `character_check(action="reroll")` with the original
resolution id, die index, and exact die value. The replacement result is
mandatory and the same Heroic Inspiration cannot be spent twice.
The canonical 2014 and 2024 Fighter Action Surge features are directly executable through
`combat_use_activity`: it consumes the card use and grants one current-turn
`extra_action`, with a Core receipt. An imported or similarly named non-Core card
does not inherit that behavior merely from its prose or display name.
The canonical 2014 and 2024 Rogue Cunning Action features use the same tool with
`declaration.action` set to Dash, Disengage, or Hide. Dash and Disengage settle
their deterministic tactical effect. Hide pays and records the bonus-action
declaration but still requires a DM ruling for eligibility and observation; do
not pay a second action or infer Hidden from the card text alone. The SagaSmith
Agent performs that ruling from the scene, positions, senses, and exact rule by
default.

The 2024 Cleric Channel Divinity card binds both `Divine Spark` and `Turn
Undead` to Core mechanics. Divine Spark settles its level-scaled roll, Wisdom
modifier, healing or Constitution save, success half damage, damage type,
target state, and Channel Divinity spend atomically. Turn Undead uses the 2024
Frightened/Incapacitated effect and a source-capability dependency; Sear Undead
adds one shared source-bound damage roll without ending that Turn effect.
Preserve Life uses the edition's exact text: only the 2014 card excludes Undead
and Constructs. Catalog presence still does not imply every later 2024 feature
is executable. In particular, Cunning Strike, Tactical Mind, and Tactical Shift
remain implementation gates until their dedicated transaction tests and Core
boundaries exist; never emulate them with raw dice or sheet patches.
