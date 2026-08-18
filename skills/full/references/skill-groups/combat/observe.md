# Combat observation

Before choosing an action, query encounter revision, round, active combatant,
turn resources, pending windows, conditions, concentration, HP, available
actions, map, and audience-safe actor details.

Use rule and module search only for discovery, then expand exact evidence.
Player reads must not reveal hidden target mechanics, unrevealed
reinforcements, DM map layers, or private actor knowledge.

Observation does not mutate state or reserve an action. Re-query after another
combatant, reaction, choice, restore, join, or map change.

Use `combat_query(view="render")` only when a user asks for an image or a
meaningful spatial change merits one. Choose `party_public` for a shared player
channel and `caller` only for that same authorized private audience. Forward the
returned native image and `alt_text`; never rebuild a share image from a broader
status result. Image failure does not block observation or play.
