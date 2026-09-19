# Character building

Inspect module pregenerated characters before creating replacements. Import a
reviewed preset when available; otherwise create a legal character using the
enabled core and confirmed expansion packs.

Preserve the source of ability scores and exercise manual entry, standard
array, and point buy where applicable. Validate race/species, background,
class, subclass, proficiencies, equipment, spellcasting model, known/prepared
spells, spellbook, and advancement state. Party composition should cover
distinct combat, exploration, healing, control, and social capabilities rather
than duplicate optimization.

`character_create_from(mode="build")` can create an unfinished character shell.
A successful creation receipt is not a completed build: check the returned
ability-generation record, applied class/species/background, HP, equipment and
spell requirements. A descriptive summary such as "human fighter" does not
apply those rules. Continue configuring the same returned character ID; do not
create another copy just because setup remains incomplete. Before returning to
play, finish the living replacement party's setup and record its entry and
transmitted knowledge. Sequence phase changes and phase-dependent calls; do not
run them in parallel or query combat-only tools while in the lobby.

After selecting a class or subclass, follow the current-level `follow_up`
returned by `character_content_apply`. Re-read it after a restart or later
selection with `character_query(view="advancement", payload={"character_id":
"...", "class_name": "...", "scope": "current_level"})`. A character
controller can inspect their own current-level work; planning the next level
still requires the DM. Omitting `scope` previews the next level and can include
features the character cannot select yet.

The `current_class_features` scope covers only class/subclass feature choices.
Its `complete` flag does not certify equipment, spells, or the whole character.
Apply the exact offered source and `grant_level`, then inspect the refreshed
list: a missed earlier repeatable grant is offered before a later grant. Keep
spell and equipment checks explicit before declaring character creation done.
For a class with a reviewed spellcasting profile, `spell_selection` separately
reports the total required and selected class-known spells and preparation
capacity. `missing_for_setup` concerns initial preparation; a capacity increase
on level-up does not change a 2014 prepared list or authorize a level-up
preparation event. Subclass always-prepared spells do not consume this capacity.

Base-class application accepts `skills`, `tools`, `skill_replacements`, and
`tool_replacements`. When the reviewed `class_definition` also publishes
`starting_equipment`, that choice is required: submit
`starting_equipment={"mode":"equipment","choices":{"group_id":["artifact_id"]}}`
with every offered group answered, or `starting_equipment={"mode":"gold"}`
when that exact source offers a gold alternative. Never submit item properties
or a pre-rolled gold amount. The server grants source-bound item instances or
rolls the campaign stream and persists wallet, source receipt and random state
together. Items are not automatically equipped or attuned.

When the source's gold alternative replaces background equipment, it excludes
both class and background starting awards. Selecting the background afterward
does not grant its equipment or coins; selecting gold after the background
reclaims only its recorded, unchanged, still-held starting award. A spent,
transferred or changed award blocks this conversion rather than deleting other
property. Do not manually edit award receipts or delete inventory to bypass it.
Use the normal equipment tools after the build choices are settled.

Finish initial equipment before switching back to play. If setup was left
unfinished, return to the lobby and continue the same character's legitimate
build choices; changing phase does not itself authorize an item award. Do not
rename starting equipment as `training`, `story_reward`, or `module_reward`
to satisfy a play-time grant validator. An equipment rules entry establishes
an item's mechanics, not an earned reward or purchase. Use an actual starting
award receipt or a supported purchase with funds and transaction evidence.
Inspect current inventory before retrying, and retain any unsupported award
as an audit issue instead of duplicating or silently rewriting it.

When a character reply is spilled to a file, distinguish the current
`result.sheet.inventory` from embedded historical `before` snapshots and the
derived inventory summary. Locate the actual JSON property line and read its
bounded section for item IDs and equipment slots. A broad substring search for
`inventory` can return historical data first; do not use that data as current
state or repeatedly reread the start of the file. After an equipment write,
use the new receipt's revision and inventory instead of an older spill.

Unsupported equipment/wealth fields are rejected, not silently applied or
discarded. If the activated source has no executable starting-equipment
contract, its class receipt does not grant equipment: retain that build
requirement as unresolved until separately settled. Inspect the exact class
source and its ruling policy: an explicitly permitted source-bound Agent
ruling can establish the printed choices and fixed quantities even when the
structured contract is absent. Record the source, choices, existing inventory,
and resulting public-operation receipts; use reviewed item mechanics and grant
only missing quantities. This does not retroactively turn an earlier training
grant into a valid starting-award receipt. Preserve that discrepancy and
correct duplicates through public operations. If the source or ruling policy
does not support settlement, keep the requirement unresolved. Never manufacture
a successful structured choice. Generic equipment support does not certify any
unrepaired source package or the complete character build.

A dead, missing, or departed character remains stored with independent
knowledge. A replacement follows normal creation and joining; only knowledge
reasonably transmitted in the fiction may be added to it.

For sharing or migration, use the unified content-actor workflow. PC, NPC,
and monster differ only by `actor_type`; never maintain a separate monster
registry or host-side constructor. Import always creates a fresh identity and
never transfers ActorKnowledge. Use bundled or shared `kind="preset"` cards before
building a replacement when an applicable reviewed preset exists.
