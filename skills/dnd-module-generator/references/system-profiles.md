# Current system profiles

Use the profile selected by the authoring campaign's exact system_id. These
fields describe Package decisions, not an alternate schema. The live native MCP
schema and system validator remain authoritative.

## D&D 5e: dnd5e

### Native authoring envelope

- module_draft takes campaign_id and action at top level and uses payload for
  action fields.
- content_pack takes action at top level and uses payload for campaign_id, kind,
  and action fields.
- Finalization identity is payload.pack_id.

### Identity and classification

- Pack id prefix: dnd5e.module.
- Supported classifications: adventure and campaign.
- compatibility.editions uses 2014, 2024, or both.
- Include module_pack_v2 in required_capabilities when the source requires the
  current module runtime.
- A campaign requires at least one reachable ending.

### Play profile

~~~json
{
  "party_size": {
    "minimum": null,
    "maximum": null,
    "source_refs": []
  },
  "starting_level": {
    "value": 1,
    "source_refs": ["<receipt>"]
  },
  "expected_end_level": {
    "value": 5,
    "source_refs": ["<receipt>"]
  },
  "advancement": {
    "modes": ["milestone"],
    "recommended": "milestone",
    "source_refs": ["<receipt>"]
  },
  "pregenerated_characters": {
    "available": false,
    "applicability": "Reviewed; none are included.",
    "source_refs": ["<receipt>"]
  }
}
~~~

Starting level, expected end level, advancement, and pregen review require real
source receipts before finalization. Party-size guidance is optional: null
bounds with no receipt are valid when the source makes no recommendation.

Use these catalog arrays:

~~~json
{
  "items": [],
  "encounters": [],
  "hazards": [],
  "handouts": [],
  "mechanics": []
}
~~~

Review exact edition mechanics, DCs, rewards, encounter actors, spells, and
statblocks. Do not use CoC investigator, SAN, or ruleset fields.

## Call of Cthulhu 7e: coc7e

### Native authoring envelope

- module_draft takes campaign_id and action at top level and uses data for action
  fields.
- content_pack takes campaign_id and action at top level and uses data for kind
  and action fields.
- Finalization identity is data.package_id.

### Identity and classification

- Pack id prefix: coc7e.module.
- Supported classifications: scenario, campaign, solo_adventure, handout_pack.
- compatibility.editions must include 7e.
- ruleset values are classic and pulp.
- scenario, campaign, and solo_adventure require at least one reachable ending.
- solo_adventure requires solo_play.supported equal to true.

### Play profile

Every section requires at least one real source receipt.

~~~json
{
  "investigator_count": {
    "minimum": 2,
    "maximum": 4,
    "source_refs": ["<receipt>"]
  },
  "ruleset": {
    "supported": ["classic"],
    "recommended": "classic",
    "source_refs": ["<receipt>"]
  },
  "era": {
    "value": "1920s",
    "source_refs": ["<receipt>"]
  },
  "estimated_sessions": {
    "minimum": 1,
    "maximum": 2,
    "source_refs": ["<receipt>"]
  },
  "pregenerated_characters": {
    "available": true,
    "applicability": "Use the four included investigators.",
    "source_refs": ["<receipt>"]
  },
  "solo_play": {
    "supported": false,
    "source_refs": ["<receipt>"]
  }
}
~~~

investigator_count and estimated_sessions require positive ordered integer
ranges. ruleset.recommended must be present in ruleset.supported. era.value must
be non-empty. The available and supported values are booleans.

Use exactly these catalog arrays:

~~~json
{
  "clues": [],
  "handouts": [],
  "encounters": [],
  "hazards": [],
  "tomes": [],
  "spells": [],
  "mechanics": []
}
~~~

Review the following source-bound surfaces:

- exact clue truth, availability, alternate discovery routes, and handout link;
- SAN trigger plus success/failure loss expressions;
- ordinary, hard, extreme, opposed, combined, pushed, and group-Luck intent;
- NPC/creature source characteristics, skills, attacks, armor, SAN loss, and
  special abilities without inventing missing values;
- chase participants, hazards, barriers, routes, and starting evidence;
- Mythos tome/spell facts and whether the text supplies executable mechanics;
- solo node identities and authored transitions;
- Classic/Pulp differences explicitly supported by the source.

Do not add D&D levels, advancement, party-majority checks, classes, AC, or
spell-slot assumptions.

## Unsupported systems

If system_id is neither dnd5e nor coc7e, stop before Package decisions. Report
that the active system needs its own deterministic compiler/validator and an
explicit profile update. Do not coerce it into either documented shape.
