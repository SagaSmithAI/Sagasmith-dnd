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

Every agent-positioned movement must explicitly provide
`spatial_facts.opportunity_attack_actor_ids`: the eligible current combatant IDs,
or `[]` after determining none apply. Omission means unassessed, not safe passage.
The Runtime returns `pending_ruling` without spending movement when it is missing;
complete the facts and retry with the current revision. The engine still checks
reaction eligibility and owns the resulting reaction windows.

When threats exist, also supply `opportunity_attack_boundaries`: one entry for
each crossed weapon reach, with `actor_id`, `distance_ft` from this move's origin,
and the eligible `weapon_ids`. In 2024, include difficult terrain's extra cost
before that boundary as `difficult_terrain_extra_ft`. In 2014 the engine derives
this cost from the required reviewed space segments below. Derive these
facts from the scene and current weapon sources; missing timing is a bounded
ruling, never permission to place the mover at its final destination early.
The Runtime commits only the prefix, opens one reaction in path order and
automatically resumes the stored remainder after all nested choices settle.
Use its returned position/budget/continuation; do not resubmit the remaining
distance as a second move. Incapacitation, changed geometry or insufficient
remaining speed can cancel the continuation at the committed boundary.

For 2014 agent movement, `space_segments` describes the entire route in positive
five-foot lengths. Each segment contains `distance_ft`, current `occupant_ids`,
`passage_width_ft` (explicit `null` means open), and boolean `difficult_terrain`.
Do not supply computed squeezing or size-eligibility conclusions. The engine
reads current cards, checks hostile size differences and occupied endpoints,
charges occupied terrain once, and derives squeezing. Squeezing adds movement
cost, disadvantages attacks and Dexterity saves, and advantages incoming attacks.
At encounter start/join, `participant_config.passage_width_ft` records an agent
actor's initial passage. Grid mode derives footprints from the reviewed map;
cropped map edges alone do not establish a narrow passage. Use an explicit path
when the grid route cannot be inferred. Reaction pauses preserve the actual
prefix's footprint and costs; restart resumes the saved remainder.

Off-turn movement is not automatically forced movement. When the exact source
makes a creature use its movement, action or reaction, its reviewed semantic
plan uses `movement.move` with fixed `payment` (`movement`, `action`, `reaction`),
`distance_limit` (`speed`, `half_speed`, or a positive feet allowance), and
`voluntary` (default true). These fields belong to the source template, never
Agent-filled slots. The mover's payment is separate from the source actor's
activation; settle any source-specific optional player choice before executing
the selected response. Runtime validates availability, charges once, retains
the source/plan fingerprint and resumes through every hostile reach exit.
Action/reaction grants do not consume the mover's ordinary turn movement pool.
External push/pull and teleportation remain exempt; do not use those labels for
a creature's self-powered movement. A paused movement must finish or cancel
before later steps of the semantic plan can execute.

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

Generic Ready fixes its response when armed. Use the supported structured action
or movement payload; the later trigger confirmation only opens the choice to
release or ignore it. Release executes that saved response and pays the reaction
in one transaction. Do not substitute a new target, weapon, action or route.
Readied attacks still wait for genuine defense/damage choices, and readied
movement still pauses at every hostile reach exit. An ignored trigger rearms
the same response until the actor's next turn. If a response needs a source-specific
ruling, record `{action: ruling, response, source, question}`; its release preserves
the original pending request and spends nothing while the ruling is unresolved.
