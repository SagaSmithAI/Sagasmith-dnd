# Combat actions

Use the narrowest structured action: preflight and resolve attacks, movement,
spell casting, reactions, activities, common actions, checks, concentration,
or HP changes. Query current legal options and exact source cards first.

The engine always owns attack/save arithmetic, upcasting, damage, critical
rules, action/bonus/reaction economy, spell slots, preparation, concentration,
ongoing effects, and random receipts. In grid mode it also owns range, target,
cover, visibility, movement, and area geometry. In agent mode those spatial
questions require the facade's exact structured `spatial_facts`; never replace
them with an unstructured narration or coordinates.

Resolve missing spatial facts from the latest committed attack/movement
receipts and the scene evidence. Account for intervening movement before reusing
a prior adjacency or reach ruling. `continuity_context` retrieves evidence; it
does not make the ruling for you, and an empty search does not erase recorded
facts. If evidence is genuinely insufficient, resolve that uncertainty before
committing the dependent action.

Do not use `combat_end_turn`, combat restart, or an enemy's inaction to recover
from a tool/schema error or unfinished content compilation. Keep the current
turn while repairing the request; if implementation work is needed, preserve it
in a verified maintenance checkpoint. Ending a turn without acting must be an
actual tactical choice supported by the situation. A committed retreat movement
alone does not prove escape: resolve applicable leaving-reach reactions and the
opponent's source-supported pursuit or decision to remain before ending combat.

For unregistered module or homebrew mechanics, the Agent reads exact source,
compiles one bounded source-bound solution, and executes it through the current
choice/application transaction. Do not accumulate special-case engine branches
for individual monsters.
