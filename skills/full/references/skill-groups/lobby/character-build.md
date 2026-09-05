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

Base-class application currently accepts only `skills`, `tools`,
`skill_replacements`, and `tool_replacements`. Unsupported equipment or wealth
fields are rejected, not silently applied or discarded. A class application
receipt does not grant starting equipment. If the activated source has no
executable starting-equipment contract, retain that build requirement as
unresolved; do not manufacture a successful choice or duplicate inventory.

A dead, missing, or departed character remains stored with independent
knowledge. A replacement follows normal creation and joining; only knowledge
reasonably transmitted in the fiction may be added to it.

For sharing or migration, use the unified content-actor workflow. PC, NPC,
and monster differ only by `actor_type`; never maintain a separate monster
registry or host-side constructor. Import always creates a fresh identity and
never transfers ActorKnowledge. Use bundled or shared `kind="preset"` cards before
building a replacement when an applicable reviewed preset exists.
