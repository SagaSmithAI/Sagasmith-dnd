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

A dead, missing, or departed character remains stored with independent
knowledge. A replacement follows normal creation and joining; only knowledge
reasonably transmitted in the fiction may be added to it.

For sharing or migration, use the unified content-actor workflow. PC, NPC,
and monster differ only by `actor_type`; never maintain a separate monster
registry or host-side constructor. Import always creates a fresh identity and
never transfers ActorKnowledge. Use bundled or shared `kind="preset"` cards before
building a replacement when an applicable reviewed preset exists.
