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

For unregistered module or homebrew mechanics, the Agent reads exact source,
compiles one bounded source-bound solution, and executes it through the current
choice/application transaction. Do not accumulate special-case engine branches
for individual monsters.
