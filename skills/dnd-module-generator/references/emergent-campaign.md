# Emergent campaign expansion

Use this mode when the table wants to begin with a compact setting and a few
playable scenes, then discover the campaign through play. It is not an excuse
to improvise untracked canon or silently mutate an installed Module.

The same shard workflow also supports a complete authored Module when players
choose a reasonable destination outside its Scene Atlas. In that case the
campaign becomes `authored_with_extensions`: the authored Module remains the
generation-0 root and every table-created location is an `emergent_episode`
descendant. The extension is campaign canon, not retroactive publisher canon.

## Content and runtime boundaries

- Build the beginning as one immutable `emergent_seed` Package.
- Build each reviewed expansion as a new immutable `emergent_episode` Package.
- Set both the Package content classification and runtime-manifest classification
  to the matching emergent value during Package review.
- Link every episode to its parent campaign line and prior content through the
  runtime manifest `lineage` fields.
- Keep possible futures, fronts, story threads, character-arc opportunities,
  clues, and foreshadowing in Package design data.
- Keep events that actually occurred, learned facts, actor knowledge, character
  changes, and scene progress in campaign runtime state.
- Do not retcon a prior shard. Publish a superseding episode or let the runtime
  record the contradiction and its consequence.

## Seed ledger

Before play, record only what is needed for an honest first session:

- a stable campaign-line id, premise, tone, safety constraints, and starting
  situation;
- a small set of locations, factions, actors, secrets, and at least one
  immediately playable scene;
- pressures or `fronts` with intentions and visible escalation signals;
- open `story_threads`, each with a question, stakes, and several possible
  developments rather than one prescribed answer;
- character-arc opportunities expressed as tensions, relationships, costs, or
  questions, never a required player decision or predetermined ending;
- redundant clue paths for any revelation required to keep play moving; and
- scene links that describe possible transitions without asserting that a
  transition has occurred.

The seed may be deliberately incomplete. Missing future regions, villains,
answers, and endings are valid when they are not necessary for the opening
session.

## Runtime manifest v2 template

Every field is exact; omit no top-level collection and add no ad-hoc fields.
Use ids that remain stable when later episodes refer back to this seed.

```markdown
<!-- sagasmith-runtime-manifest
{
  "schema_version": 2,
  "module_key": "ashen-road-seed",
  "classification": "emergent_seed",
  "lineage": {
    "root_module_key": "ashen-road-seed",
    "parent_module_key": "",
    "generation": 0
  },
  "entities": [],
  "secrets": [],
  "clues": [
    {
      "id": "clue:toll-mark",
      "label": "A burned toll mark",
      "trigger": "Inspect the abandoned milestone",
      "revelation": "Someone still collects the eastern road's old debt",
      "linked_thread_ids": ["thread:eastern-debt"],
      "fallback_scene_ids": ["scene:tollhouse"]
    }
  ],
  "plot_nodes": [],
  "foreshadowing": [],
  "branches": [],
  "fronts": [
    {
      "id": "front:road-collectors",
      "name": "The Road Collectors",
      "goal": "Restore control of the eastern crossing",
      "stakes": "Travelers become debt-bound",
      "grim_portents": ["Fresh marks appear on occupied homes"],
      "linked_thread_ids": ["thread:eastern-debt"]
    }
  ],
  "story_threads": [
    {
      "id": "thread:eastern-debt",
      "title": "Who owns the road?",
      "question": "Why has the old toll returned?",
      "linked_front_ids": ["front:road-collectors"],
      "linked_clue_ids": ["clue:toll-mark"]
    }
  ],
  "character_arcs": [
    {
      "id": "arc:envoy-belonging",
      "actor_id": "pc:envoy",
      "actor_kind": "pc",
      "opportunities": [
        {
          "id": "opportunity:claim-the-road",
          "prompt": "Someone asks what makes a place worth defending",
          "scene_ids": ["scene:crossroads"],
          "thread_ids": ["thread:eastern-debt"]
        }
      ],
      "planned_beats": [],
      "possible_endings": []
    }
  ],
  "scene_links": [
    {
      "id": "link:crossroads-tollhouse",
      "from_scene_id": "scene:crossroads",
      "to_scene_id": "scene:tollhouse",
      "kind": "choice",
      "trigger": "The party takes the eastern road"
    }
  ]
}
-->
```

An `emergent_episode` uses the seed's `root_module_key`, names its immediate
`parent_module_key`, increments `generation`, and includes at least one
`scene_link`. `authored_module` remains available for a bounded prewritten work.

## Expansion cadence

Generate an episode only at a safe authoring intermission: close private NPC
conversations, leave combat, and return to Lobby. First obtain a bounded
`campaign_expansion` proposal from the current campaign line. Its output is
review-only and must cite the runtime facts, events, actor knowledge, source
evidence, or director question that justify each proposed addition.

Review the proposal for:

1. continuity with established runtime truth and unresolved consequences;
2. fronts that react to player action without negating it;
3. threads that branch or converge while preserving meaningful alternatives;
4. PC arcs that offer pressure and opportunity without choosing for a player;
5. NPC arcs grounded in goals, relationships, knowledge, and recent events;
6. clues with multiple discovery routes and no single-check dead end;
7. a compact set of scenes likely to become playable next; and
8. secrets or future scenes that must remain outside player-facing context.

Turn the reviewed proposal into a normal Module draft, attach evidence, repair
it through the standard authoring workflow, finalize it, import it inactive,
then explicitly activate it. Extend the playthrough manifest with the new
Module id instead of replacing the campaign line. Create any new runtime actor
instances only after activation, then resume play.

## Extending a complete authored Module

When players name an off-Atlas destination, first determine whether it is
consistent with established geography, travel, factions, and source facts. A
missing entry in the Atlas is not evidence that the place cannot exist. Reject
or reframe only a real contradiction; do not use Atlas coverage as an invisible
wall.

At the next safe scene boundary:

1. request `campaign_expansion` with the proposed destination and the player
   reason for going there;
2. ground the proposal in the authored shard, current scene, travel facts,
   relevant threads, and established consequences;
3. author the smallest useful `emergent_episode`, with a scene link from an
   established or clearly described transition point;
4. root its runtime lineage at the relevant authored Module and set its parent
   to that Module or the latest related extension;
5. extend the playthrough manifest using `campaign_mode="authored_with_extensions"`;
6. activate the reviewed shard, create only genuinely new actor instances, and
   let Scene Atlas display the added module/chapter group.

Preserve the authored Module's intended stakes and known constraints, but do
not force the extension to reconnect to a predetermined plot node. New clues
may illuminate an authored mystery; they must not silently replace a published
answer. If the detour later reconnects, express that as another scene link or
episode rather than rewriting the root Module.

## Expansion discipline

- Generate the smallest useful horizon, normally one episode or a few nearby
  scenes. Avoid pre-authoring distant outcomes that current choices may erase.
- Prefer consequences, changed relationships, and faction motion over adding
  unrelated novelty.
- Reuse established actors when their goals plausibly pull them into the next
  situation; introduce a new actor only when the role is genuinely new.
- Let dormant fronts and threads remain dormant. Do not advance every clock on
  every intermission.
- Mark uncertain interpretations as unresolved. Director review, deterministic
  validation, finalization, and activation remain separate authority gates.
- A generator or subagent may propose content but may not roll dice, call state
  mutation tools, publish narration, or write campaign truth directly.
- Extend a campaign line only with a currently active finalized Module Pack.
  A retired Pack already cited by the line remains historical evidence and
  cannot be removed while the playthrough manifest still references it.

## Long-running continuity

At each intermission, compare the campaign's branch-local `front_progress`,
`thread_progress`, and `arc_progress` with the latest installed shard design.
Use stable ids across episodes so progress survives expansion. Close an item
only when established play supports closure; otherwise advance, branch, pause,
or transform it with an explicit runtime update. Every front, thread, and arc id
must come from an installed shard runtime manifest. Every progress advance must
cite branch-visible event, snapshot, Scene Atlas, memory-fact, or settled NPC
conversation evidence; invented ids and evidence from another branch are
rejected. Completed arc opportunities must also exist in that arc's design.

When the table eventually wants a bounded finale, add an episode that supplies
reachable ending scenes. Emergent play does not require choosing that ending in
advance.
